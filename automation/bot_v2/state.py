"""
KOZYR bot v2 — управление состоянием сессий редактирования.

Файловый JSON-стор в .bot_state/ (в корне репозитория). Cloudflare Worker
читает и пишет через GitHub Contents API. Не идеально по производительности
(зато без внешних баз — вся автоматизация укладывается в GitHub + Cloudflare
Workers, как и было раньше на этом проекте).

Что хранит:
1. edit_sessions/{slug}.json  — активные правки статей (кто, что менял).
2. history/{slug}.json         — история действий по slug (для аудита).
3. ab_tests/{slug}.json        — активные A/B-тесты (варианты title/description).
4. cache/gsc_last_run.json     — когда последний раз читали GSC (rate-limit).

Формат edit_session:
{
  "slug": "kak-vybrat-pokernyj-rum-s-vysokim-rejkbekom",
  "field": "meta_title",           # какое поле сейчас правим
  "opened_at": "2026-08-04T...",
  "opened_by_chat_id": 123,
  "opened_by_message_id": 456,     # message_id превью, куда крепим правку
  "original_value": "...",
  "proposed_value": null           # ждём следующее сообщение пользователя
}

Формат history entry:
{
  "slug": "...",
  "action": "publish" | "regenerate" | "reject" | "edit" | "edit_applied" | "ab_test_started",
  "at": "2026-08-04T...",
  "by_chat_id": 123,
  "details": {...}
}
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_ROOT = Path(".bot_state")
EDIT_SESSIONS_DIR = STATE_ROOT / "edit_sessions"
HISTORY_DIR = STATE_ROOT / "history"
AB_TESTS_DIR = STATE_ROOT / "ab_tests"
CACHE_DIR = STATE_ROOT / "cache"

for d in (EDIT_SESSIONS_DIR, HISTORY_DIR, AB_TESTS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ==== Edit sessions ====

# Какие поля разрешено редактировать из TG. Всё остальное — только через git.
# Если поле не в списке — /edit slug field откажет.
EDITABLE_FIELDS = {
    "meta_title", "meta_description", "h1_title",
    "image_prompt", "notes", "target_page",
    # spec-поля для тем:
    "primary_keyword", "secondary_keywords",
}

# Сколько минут держим сессию открытой, потом сама протухает
SESSION_TTL_MIN = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_edit_session(slug: str, field: str, chat_id: int, message_id: int,
                       original_value: str) -> dict:
    """Открывает сессию редактирования. Автоматически закрывает
    любую уже открытую по тому же slug (последняя команда побеждает)."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(
            f"Поле {field!r} не разрешено к редактированию из TG. "
            f"Доступные: {sorted(EDITABLE_FIELDS)}"
        )
    session = {
        "slug": slug,
        "field": field,
        "opened_at": _now_iso(),
        "opened_by_chat_id": chat_id,
        "opened_by_message_id": message_id,
        "original_value": original_value,
        "proposed_value": None,
    }
    path = EDIT_SESSIONS_DIR / f"{slug}.json"
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    return session


def get_edit_session(slug: str) -> dict | None:
    path = EDIT_SESSIONS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    session = json.loads(path.read_text(encoding="utf-8"))
    # TTL
    try:
        opened = datetime.fromisoformat(session["opened_at"])
        age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
        if age_min > SESSION_TTL_MIN:
            path.unlink(missing_ok=True)
            return None
    except (ValueError, KeyError):
        pass
    return session


def find_edit_session_by_chat(chat_id: int) -> dict | None:
    """Находит открытую сессию редактирования по chat_id. Нужно чтобы понимать:
    когда оператор просто пишет в чат, это ответ на открытое /edit."""
    for p in EDIT_SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("opened_by_chat_id") == chat_id:
            # тот же TTL-чек
            try:
                opened = datetime.fromisoformat(data["opened_at"])
                age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
                if age_min > SESSION_TTL_MIN:
                    p.unlink(missing_ok=True)
                    continue
            except (ValueError, KeyError):
                pass
            return data
    return None


def close_edit_session(slug: str) -> None:
    path = EDIT_SESSIONS_DIR / f"{slug}.json"
    path.unlink(missing_ok=True)


def apply_edit(slug: str, proposed_value: str, meta_json_path: Path) -> dict:
    """
    Применяет правку к meta.json в _pending/{slug}/. Возвращает результат:
    {status: 'ok'|'error', field, old, new, ...}
    """
    session = get_edit_session(slug)
    if session is None:
        return {"status": "error", "message": "Сессия не найдена или устарела"}
    field = session["field"]
    if not meta_json_path.exists():
        return {"status": "error", "message": f"{meta_json_path} не найдена"}

    meta = json.loads(meta_json_path.read_text(encoding="utf-8"))
    old_value = meta.get(field, "")
    meta[field] = proposed_value
    meta_json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    close_edit_session(slug)
    add_history_entry(slug, "edit_applied", {
        "field": field, "old": old_value, "new": proposed_value
    })
    return {"status": "ok", "field": field, "old": old_value, "new": proposed_value}


# ==== History ====

def add_history_entry(slug: str, action: str, details: dict | None = None,
                       by_chat_id: int | None = None) -> None:
    """Добавляет запись в history/{slug}.json. Идемпотентно."""
    path = HISTORY_DIR / f"{slug}.json"
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            entries = []
    else:
        entries = []

    entries.append({
        "at": _now_iso(),
        "action": action,
        "by_chat_id": by_chat_id,
        "details": details or {},
    })
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def get_history(slug: str) -> list[dict]:
    path = HISTORY_DIR / f"{slug}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


# ==== A/B tests ====

# Простой формат: два варианта поля (обычно meta_title или meta_description).
# После публикации через 30 дней смотрим CTR в GSC, ставим победителя.

def start_ab_test(slug: str, field: str, variant_a: str, variant_b: str) -> dict:
    if field not in ("meta_title", "meta_description"):
        raise ValueError(f"A/B поддерживается только для meta_title/meta_description, не {field}")
    test = {
        "slug": slug, "field": field,
        "variant_a": variant_a, "variant_b": variant_b,
        "started_at": _now_iso(),
        "current": "a",   # начинаем с A, затем в определённые дни меняем
        "results": {},
    }
    path = AB_TESTS_DIR / f"{slug}.json"
    path.write_text(json.dumps(test, indent=2, ensure_ascii=False), encoding="utf-8")
    add_history_entry(slug, "ab_test_started", {
        "field": field, "variant_a": variant_a[:60], "variant_b": variant_b[:60]
    })
    return test


def get_ab_test(slug: str) -> dict | None:
    path = AB_TESTS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_active_ab_tests() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in AB_TESTS_DIR.glob("*.json")
    ]


# ==== Cache (простой rate-limit для дорогих API) ====

def cache_get(key: str, max_age_min: int = 60) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        wrapped = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    try:
        cached_at = datetime.fromisoformat(wrapped["cached_at"])
        age_min = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age_min > max_age_min:
            return None
    except (ValueError, KeyError):
        return None
    return wrapped.get("value")


def cache_set(key: str, value: Any) -> None:
    path = CACHE_DIR / f"{key}.json"
    wrapped = {"cached_at": _now_iso(), "value": value}
    path.write_text(json.dumps(wrapped, indent=2, ensure_ascii=False), encoding="utf-8")
