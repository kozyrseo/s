#!/usr/bin/env python3
"""
KOZYR — уведомление в Telegram, когда сборка страницы партнёра закончилась.
Даёт оператору то, чего раньше не было: КЛИКАБЕЛЬНУЮ ссылку на превью + итог
смоук-теста + кнопки действий. Публикация перестаёт быть «вслепую».

Вызывается из generate-partner-tpl.yml ПОСЛЕ генерации и смоук-теста.

env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   — секреты воркфлоу (как в parse-partner)
  PARTNER_ID                             — id партнёра
  PUBLISH                                — "true" | "false"
  SMOKE_OK                               — "1" (прошёл) | "0" (провалился)
  SMOKE_SUMMARY                          — короткая строка итога смоук-теста
  CHAT_ID (optional)                     — переопределить чат назначения

Кнопки (callback_data совпадают с worker_v2.js):
  превью: ppublish:{id} · pmore:{id} · pcancel
  прод:   pshow:{id}
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

BASE = "https://kozyr.club"


def _draft(pid: str) -> dict:
    p = os.path.join(os.path.dirname(__file__), "..", "_partner_drafts", f"{pid}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _prod_path(draft: dict, pid: str) -> str:
    cc = draft.get("country", "ua")
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    return f"/{cc}/{kind}/{pid}/"


def _send(token: str, chat_id: str, text: str, keyboard: list | None) -> bool:
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"notify: send failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    pid = os.environ.get("PARTNER_ID", "").strip()
    publish = os.environ.get("PUBLISH", "false").strip().lower() == "true"
    smoke_ok = os.environ.get("SMOKE_OK", "1").strip() == "1"
    smoke_summary = os.environ.get("SMOKE_SUMMARY", "").strip()

    if not (token and chat_id and pid):
        print("notify: нет TELEGRAM_BOT_TOKEN/CHAT_ID/PARTNER_ID — пропуск")
        return 0

    draft = _draft(pid)
    name = draft.get("name", pid)
    smoke_line = ("✅ смоук-тест пройден" if smoke_ok
                  else "⚠️ смоук-тест нашёл проблемы") + (f" · {smoke_summary}" if smoke_summary else "")

    if publish:
        prod = BASE + _prod_path(draft, pid)
        text = (f"🌐 *{md(name)}* опубликован\n\n"
                f"{smoke_line}\n\n"
                f"Прод: [{md(prod)}]({prod})\n"
                f"Добавлен в каталог, sitemap, llms и OG-обложку.")
        kb = [[{"text": "📋 Показать сводку", "callback_data": f"pshow:{pid}"}]]
        _send(token, chat_id, text, kb)
        return 0

    # Превью
    preview = f"{BASE}/_pending_partner/{pid}/index.html"
    preview_uk = f"{BASE}/_pending_partner/{pid}_uk/index.html"
    warn = "" if smoke_ok else "\n\n⚠️ _Смоук-тест нашёл проблемы — глянь перед публикацией._"
    text = (f"🖼 *Превью готово* · *{md(name)}*\n\n"
            f"{smoke_line}\n\n"
            f"👀 Открыть превью:\n"
            f"• [RU-версия]({preview})\n"
            f"• [UK-версия]({preview_uk})\n"
            f"{warn}\n\n"
            f"Проверь и выбери действие ниже:")
    kb = [
        [{"text": "🌐 Опубликовать в прод", "callback_data": f"ppublish:{pid}"}],
        [{"text": "✏️ Дополнить и пересобрать", "callback_data": f"pmore:{pid}"}],
        [{"text": "❌ Отмена", "callback_data": "pcancel"}],
    ]
    _send(token, chat_id, text, kb)
    return 0


def md(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace("_", "\\_")
            .replace("*", "\\*").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]"))


if __name__ == "__main__":
    raise SystemExit(main())
