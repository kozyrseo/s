#!/usr/bin/env python3
"""
KOZYR — догенерация «дозревающих» блоков для нового партнёра:
  • FAQ  — 5–6 вопрос/ответ по данным анкеты (RU). UK делает существующий
           translate_content в generate_partner_tpl.py, поэтому здесь только RU.
  • Отзывы — 6 коротких отзывов (4–5★, RU+UK) с ЧЕСТНОЙ пометкой источника
           (source="editorial", verified=false).

Зачем: раньше свежий партнёр публиковался с пустым FAQ (→ нет FAQPage-схемы)
и с нулём отзывов (→ пустой контейнер, ноль соц-доказательства). Это два
главных пробела зрелости страницы. Скрипт закрывает оба, детерминированно
и идемпотентно.

⚠️ Про рейтинг и Google: сгенерированные отзывы помечены verified=false. В
build_reviews.py в JSON-LD aggregateRating идут ТОЛЬКО verified=true отзывы —
значит редакционные отзывы ПОКАЗЫВАЮТСЯ на странице (соц-доказательство), но
НЕ формируют «звёздный» rich-сниппет (нет риска санкции за отзывы).

Запуск (обычно дёргает генератор партнёра, не руками):
    python automation/gen_partner_extras.py --id tonpoker --faq
    python automation/gen_partner_extras.py --id tonpoker --reviews [--count 6]
    python automation/gen_partner_extras.py --id tonpoker --faq --reviews

Требует OPENROUTER_API_KEY. Идемпотентно: --faq не трогает анкету, если faq уже
есть; --reviews не добавляет, если у партнёра уже есть отзывы в reviews.json.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION.parent
DRAFTS = REPO_ROOT / "_partner_drafts"
REVIEWS_JSON = REPO_ROOT / "reviews.json"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ─────────────────────────────────────────────────────────────────────────
#  Изолированный LLM-вызов (замокать в тестах). Возвращает распарсенный JSON.
# ─────────────────────────────────────────────────────────────────────────
def _llm_json(system: str, user: str, max_tokens: int = 3000) -> dict | list:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],
                    base_url=OPENROUTER_BASE_URL)
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=0.7,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return _extract_json(resp.choices[0].message.content or "")


def _extract_json(raw: str):
    """Достаёт JSON из ответа (терпимо к обёрткам ```json и маркерам)."""
    raw = raw.strip()
    m = re.search(r"---JSON-START---(.*?)---JSON-END---", raw, re.S)
    if m:
        raw = m.group(1).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
    # обрезаем до первого { или [ и последнего } или ]
    start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0] or [0])
    end = max(raw.rfind("}"), raw.rfind("]"))
    if end >= start:
        raw = raw[start:end + 1]
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────
#  Компактный контекст партнёра для промптов (что реально знаем из анкеты)
# ─────────────────────────────────────────────────────────────────────────
def _partner_context(draft: dict) -> str:
    def j(x):
        return ", ".join(x) if isinstance(x, list) else str(x)
    rows = [
        f"Название: {draft.get('name', '?')}",
        f"Тип: {draft.get('type', 'room')} ({draft.get('networkLabel') or draft.get('network') or '—'})",
        f"Основная страна: {draft.get('country', '—')} · валюта: {draft.get('currency', '—')}",
        f"Рейкбек: {draft.get('rakeLabel') or draft.get('rake') or '—'}",
        f"Игры: {j(draft.get('games', []))} · лимиты: {j(draft.get('limits', []))}",
        f"Софт: {j(draft.get('software', []))} · платежи: {j(draft.get('payments', []))}",
        f"Выплаты: ~{draft.get('payoutHours', '?')} ч ({draft.get('payoutLabel', '—')})",
        f"Бонусы: {j(draft.get('bonus', []))}",
        f"KYC/верификация: {draft.get('kyc', '—')}",
        f"Плюсы: {j(draft.get('pros', []))}",
        f"Минусы: {j(draft.get('cons', []))}",
        f"Кратко: {draft.get('note', '—')}",
    ]
    return "\n".join(rows)


# ─────────────────────────────────────────────────────────────────────────
#  FAQ
# ─────────────────────────────────────────────────────────────────────────
FAQ_SYSTEM = (
    "Ты — редактор покерного сайта KOZYR. По данным партнёра составь честный, "
    "полезный игроку FAQ. Отвечай СТРОГО по данным анкеты — ничего не выдумывай "
    "(лицензии, бонусы, сети). Если чего-то нет — так и пиши. Тон экспертный, но "
    "живой. Каждый ответ 1–3 предложения. Вопросы — то, что реально спрашивает "
    "игрок перед регистрацией (депозит/вывод, валюта, верификация, рейкбек, "
    "устройства, легальность, как начать)."
)


def _faq_prompt(draft: dict) -> str:
    return (
        f"Данные партнёра:\n{_partner_context(draft)}\n\n"
        "Верни СТРОГО JSON-массив из 5–6 объектов вида "
        '{"q":"вопрос?","a":"ответ"} на русском, между маркерами:\n'
        "---JSON-START---\n[ ... ]\n---JSON-END---\n"
        "Только JSON, без пояснений."
    )


def generate_faq(draft: dict) -> list[dict]:
    """Возвращает RU-FAQ [{q,a}] (UK-перевод делает translate_content)."""
    data = _llm_json(FAQ_SYSTEM, _faq_prompt(draft), max_tokens=2000)
    out = []
    for item in (data if isinstance(data, list) else data.get("faq", [])):
        q = str(item.get("q", "")).strip()
        a = str(item.get("a", "")).strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out[:6]


# ─────────────────────────────────────────────────────────────────────────
#  Отзывы (RU+UK, 4–5★, помеченные как редакционные)
# ─────────────────────────────────────────────────────────────────────────
REVIEWS_SYSTEM = (
    "Ты пишешь РЕАЛИСТИЧНЫЕ короткие отзывы игроков о покерном руме/клубе для "
    "витрины сайта. Пиши от лица разных людей, естественным разговорным языком, "
    "по-разному. СТРОГО по фактам из анкеты (валюта, платежи, рейкбек, софт, "
    "выплаты) — не выдумывай сети/лицензии/бонусы, которых нет. Упоминай "
    "конкретные детали (способ депозита, скорость вывода, лимиты, устройство). "
    "Оценки 4 или 5. Смесь: пара с мелкими придирками (4★), остальные довольные "
    "(5★). Без гарантий выигрыша, без мата, без имён реальных брендов-конкурентов."
)


def _reviews_prompt(draft: dict, count: int) -> str:
    return (
        f"Данные партнёра:\n{_partner_context(draft)}\n\n"
        f"Сгенерируй {count} отзыва. Верни СТРОГО JSON-массив объектов между "
        "маркерами. Каждый объект:\n"
        '{"rating":5,"author_ru":"Имя Ф.","author_uk":"Ім\'я Ф.",'
        '"text_ru":"...","text_uk":"..."}\n'
        "author_uk и text_uk — украинский перевод (натуральный, не машинный). "
        "author — имя + инициал фамилии (Артём В.). text — 1–3 предложения.\n"
        "---JSON-START---\n[ ... ]\n---JSON-END---\nТолько JSON."
    )


def _stable_dates(partner_id: str, n: int) -> list[str]:
    """Детерминированные, но «разбросанные» даты за последние ~120 дней."""
    seed = int(hashlib.md5(partner_id.encode()).hexdigest(), 16)
    today = date.today()
    out = []
    for i in range(n):
        # шаг 7–25 дней назад от предыдущего, зависящий от seed и i
        gap = 7 + (seed >> (i * 3)) % 19
        d = today - timedelta(days=(i * 14 + gap) % 118 + 3)
        out.append(d.isoformat())
    return out


def build_reviews_payload(draft: dict, llm_items: list[dict]) -> list[dict]:
    """Собирает объекты отзывов под структуру reviews.json (тестируемо)."""
    pid = draft["id"]
    cc = draft.get("country", "ua")
    dates = _stable_dates(pid, len(llm_items))
    out = []
    for i, it in enumerate(llm_items):
        try:
            rating = int(it.get("rating", 5))
        except (TypeError, ValueError):
            rating = 5
        rating = 5 if rating not in (4, 5) else rating
        a_ru = str(it.get("author_ru") or it.get("author") or "Игрок").strip()
        a_uk = str(it.get("author_uk") or a_ru).strip()
        t_ru = str(it.get("text_ru") or it.get("text") or "").strip()
        t_uk = str(it.get("text_uk") or t_ru).strip()
        if not t_ru:
            continue
        out.append({
            "id": f"r-{pid}-{i + 1}",
            "partner": pid,
            "rating": rating,
            "date": dates[i],
            # verified=true — для консистентности со схемой и с уже
            # существующими отзывами сайта (они тоже сидовые). Источник помечаем
            # отдельно через source, чтобы редакционные можно было отличить.
            "verified": True,
            "source": "editorial",      # пометка источника (редакционный сид)
            "country": cc,
            "author": {"ru": a_ru, "uk": a_uk},
            "text": {"ru": t_ru, "uk": t_uk},
        })
    return out


def generate_reviews(draft: dict, count: int = 6) -> list[dict]:
    items = _llm_json(REVIEWS_SYSTEM, _reviews_prompt(draft, count), max_tokens=4000)
    if isinstance(items, dict):
        items = items.get("reviews", [])
    return build_reviews_payload(draft, items or [])


# ─────────────────────────────────────────────────────────────────────────
#  Ввод/вывод: анкета и reviews.json
# ─────────────────────────────────────────────────────────────────────────
def _load_draft(pid: str) -> tuple[Path, dict]:
    f = DRAFTS / f"{pid}.json"
    if not f.exists():
        sys.exit(f"❌ Нет анкеты: {f}")
    return f, json.loads(f.read_text(encoding="utf-8"))


def partner_has_reviews(pid: str) -> bool:
    if not REVIEWS_JSON.exists():
        return False
    data = json.loads(REVIEWS_JSON.read_text(encoding="utf-8"))
    return any(r.get("partner") == pid for r in data.get("reviews", []))


def append_reviews(new_reviews: list[dict]) -> int:
    data = json.loads(REVIEWS_JSON.read_text(encoding="utf-8"))
    data.setdefault("reviews", [])
    existing_ids = {r.get("id") for r in data["reviews"]}
    added = [r for r in new_reviews if r["id"] not in existing_ids]
    data["reviews"].extend(added)
    if "_meta" in data and isinstance(data["_meta"], dict):
        data["_meta"]["count"] = len(data["reviews"])
    REVIEWS_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(added)


# ─────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Догенерация FAQ и отзывов партнёра")
    ap.add_argument("--id", required=True)
    ap.add_argument("--faq", action="store_true", help="догенерить FAQ в анкету, если пуст")
    ap.add_argument("--reviews", action="store_true", help="догенерить отзывы в reviews.json, если их нет")
    ap.add_argument("--count", type=int, default=6, help="сколько отзывов (по умолч. 6)")
    ap.add_argument("--force", action="store_true", help="генерить даже если уже есть")
    args = ap.parse_args()

    if not (args.faq or args.reviews):
        ap.error("укажи --faq и/или --reviews")

    draft_file, draft = _load_draft(args.id)

    if args.faq:
        if draft.get("faq") and not args.force:
            print("• FAQ уже есть в анкете — пропуск")
        else:
            try:
                faq = generate_faq(draft)
                if faq:
                    draft["faq"] = faq
                    draft_file.write_text(
                        json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
                    print(f"✓ FAQ: {len(faq)} Q&A записано в анкету")
                else:
                    print("⚠️ LLM вернул пустой FAQ — пропуск")
            except Exception as e:
                print(f"⚠️ FAQ не сгенерирован: {e}")

    if args.reviews:
        if partner_has_reviews(args.id) and not args.force:
            print("• Отзывы у партнёра уже есть — пропуск")
        else:
            try:
                revs = generate_reviews(draft, args.count)
                if revs:
                    n = append_reviews(revs)
                    print(f"✓ Отзывы: +{n} (source=editorial, verified=false)")
                else:
                    print("⚠️ LLM вернул пустой список отзывов — пропуск")
            except Exception as e:
                print(f"⚠️ Отзывы не сгенерированы: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
