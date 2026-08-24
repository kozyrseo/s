#!/usr/bin/env python3
"""
KOZYR — парсер описания партнёра из свободного текста.

Оператор пишет в Telegram ОДИН текст с описанием партнёра (как угодно),
Claude API извлекает из него структурированные параметры → _partner_drafts/{id}.json.

Вход:  текст (через --text или файл --text-file)
Выход: _partner_drafts/{id}.json + краткая сводка "что понял / чего не хватает"

Запуск:
    python automation/parse_partner.py --text-file /tmp/partner_desc.txt
    python automation/parse_partner.py --text "RoyalClub — клуб в PPPoker..."

Требует: OPENROUTER_API_KEY.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

from openai import OpenAI

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION.parent
NETWORKS_FILE = AUTOMATION / "networks.json"
QUESTIONS_FILE = AUTOMATION / "partner_questions.json"
DRAFTS_DIR = REPO_ROOT / "_partner_drafts"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 4000


def build_parse_prompt(networks: dict, questions: dict) -> str:
    """Системный промпт: как извлечь параметры партнёра из текста."""
    net_list = "\n".join(
        f"  - {k}: {v['label']} ({v['type']})" for k, v in networks.items()
    )
    # Собираем список полей из анкеты для справки
    fields = []
    for section in questions["sections"]:
        for q in section["questions"]:
            fields.append(f"  - {q['key']}: {q['q'][:60]}")
    fields_str = "\n".join(fields)

    return f"""Ты — парсер данных о покерных партнёрах для сайта KOZYR.

Оператор описывает партнёра свободным текстом. Извлеки из него ВСЕ параметры
и верни строго JSON. Что не указано — ставь разумный дефолт или null.

ИЗВЕСТНЫЕ ПРИЛОЖЕНИЯ/СЕТИ:
{net_list}

ПОЛЯ ДЛЯ ИЗВЛЕЧЕНИЯ:
{fields_str}

ПРАВИЛА ИЗВЛЕЧЕНИЯ:
- id: сгенерируй из названия латиницей, строчными (RoyalClub → royalclub)
- type: "room" (покер-рум) или "club" (приватный клуб). Клуб в приложении = club.
- network: определи приложение из текста (PPPoker→pppoker, ClubGG→clubgg). Рум→своё имя.
- networkLabel: человекочитаемое (PPPoker, ClubGG)
- score: если не указан — поставь 7.5 (средний)
- rake: число процентов (35) или "none" если рейкбека нет
- rakeLabel: как показать ("до 35%", "нет — только бонусы")
- currency: UAH/USD/EUR (гривна→UAH)
- payoutHours: число часов (примерно, из "30-90 минут" → 1)
- списки (games, limits, software, payments, bonus):
  массивы строк. games: cash/mtt/spins/sng. software: ios/android/win/mac/web.
  payments: card/bank/crypto/ewallet.

СТРАНЫ — ВАЖНО, это ДВЕ РАЗНЫЕ вещи:
- country: ОСНОВНАЯ страна (ОДНА) — где клуб базируется, для кого в первую
  очередь. Определяет путь страницы (/ua/, /kz/). Извлеки из текста:
  "клуб для украинцев" / "работает в Украине" → "ua".
  Коды: Украина=ua, Казахстан=kz, Польша=pl, Россия=ru, Беларусь=by.
  ПОДСКАЗКИ: если валюта UAH → скорее всего ua; если упомянута ОДНА страна —
  она основная. Если основную определить НЕЛЬЗЯ — поставь null.
- countries: массив = [country] (та же основная, для фильтра каталога).
- acceptedCountries: МНОГО стран — откуда клуб ПРИНИМАЕТ игроков (для плашки
  "доступен в..."). Извлеки ВСЕ упомянутые: "принимает из Украины, Польши,
  Германии" → ["ua","pl","de"]. Если в тексте про приём ничего — оставь []
  (потом подставим основную страну).

- pros, cons: массивы строк (каждый плюс/минус отдельно)
- howJoin: массив шагов
- about: массив абзацев (2-3) — можешь развернуть кратко из описания
- note: краткое описание в 1 предложение для карточки
- faq: массив {{"q":"...","a":"..."}} — если есть вопросы, иначе []
- logo_from, logo_to: hex-цвета если указаны, иначе null (будет дефолт)
- dark_card: true если клуб/тёмная тема, иначе false
- country: основная страна (ua по умолчанию)

ФОРМАТ ОТВЕТА — строго JSON между маркерами:
---PARTNER-JSON-START---
{{
  "id": "...",
  "name": "...",
  ... все поля ...
  "_missing": ["список важных полей, которых НЕ было в тексте"]
}}
---PARTNER-JSON-END---

Поле _missing — перечисли что не удалось извлечь (для уточнения у оператора).
Верни ТОЛЬКО JSON между маркерами."""


def parse_json_response(raw: str) -> dict:
    """Извлекает JSON между маркерами."""
    text = raw.strip()
    START = "---PARTNER-JSON-START---"
    END = "---PARTNER-JSON-END---"
    if START in text and END in text:
        _, rest = text.split(START, 1)
        json_str, _ = rest.split(END, 1)
    else:
        # Фолбэк: ищем первый { до последнего }
        first = text.find("{")
        last = text.rfind("}")
        if first == -1 or last == -1:
            raise RuntimeError("JSON не найден в ответе")
        json_str = text[first : last + 1]
    return json.loads(json_str.strip())


def summarize(draft: dict) -> str:
    """Краткая сводка 'что понял' для показа оператору."""
    lines = []
    lines.append(f"🎯 {draft.get('name', '?')} · {draft.get('type', '?')} · {draft.get('networkLabel', draft.get('network', '?'))}")
    rake = draft.get("rakeLabel", "?")
    lines.append(f"💰 Рейкбек: {rake} · {draft.get('currency', '?')}")
    games = draft.get("games", [])
    limits = draft.get("limits", [])
    if games or limits:
        lines.append(f"🎮 {', '.join(limits[:3])} · {', '.join(games)}")
    pros = draft.get("pros", [])
    cons = draft.get("cons", [])
    lines.append(f"✅ Плюсы: {len(pros)} · Минусы: {len(cons)}")
    missing = draft.get("_missing", [])
    if missing:
        lines.append(f"⚠️ Не указано: {', '.join(missing[:6])}")
    return "\n".join(lines)


def send_telegram_summary(chat_id: str, draft: dict) -> None:
    """Отправляет сводку 'что понял' в Telegram с кнопками."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или chat_id — сводку в Telegram не шлю")
        return

    draft_id = draft.get("id", "?")
    # Собираем текст сводки (Markdown)
    L = ["📋 *Вот что я понял:*", ""]
    L.append(f"🎯 *{draft.get('name', '?')}* · {draft.get('type', '?')} · {draft.get('networkLabel', draft.get('network', '?'))}")

    # Основная страна
    country = draft.get("country")
    if country:
        L.append(f"📍 Основная страна: {country} · Валюта: {draft.get('currency', '?')}")
    else:
        L.append(f"📍 Основная страна: _не определена_ · Валюта: {draft.get('currency', '?')}")
    # Принимает игроков (если список шире основной)
    accepted = draft.get("acceptedCountries", [])
    if accepted and (len(accepted) > 1 or (country and accepted != [country])):
        L.append(f"🌐 Принимает из: {', '.join(accepted)}")

    L.append(f"💰 Рейкбек: {draft.get('rakeLabel', '?')}")
    games = draft.get("games", [])
    limits = draft.get("limits", [])
    if games or limits:
        L.append(f"🎮 {', '.join(limits[:4])} · {', '.join(games)}")
    sw = draft.get("software", [])
    if sw:
        L.append(f"📱 {', '.join(sw)}")
    L.append(f"✅ Плюсы: {len(draft.get('pros', []))} · ❌ Минусы: {len(draft.get('cons', []))}")
    missing = draft.get("_missing", [])
    if missing:
        L.append("")
        L.append(f"⚠️ _Не указано (будут дефолты): {', '.join(missing[:8])}_")
    L.append("")
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    path_country = country or "??"
    L.append(f"_Путь: /{path_country}/{kind}/{draft_id}/_")
    text = "\n".join(L)

    # Если основная страна не определена — сначала кнопки выбора страны.
    if draft.get("_country_unclear") or not country:
        keyboard = [
            [
                {"text": "🇺🇦 Украина", "callback_data": f"pcountry:{draft_id}:ua"},
                {"text": "🇰🇿 Казахстан", "callback_data": f"pcountry:{draft_id}:kz"},
            ],
            [
                {"text": "🇵🇱 Польша", "callback_data": f"pcountry:{draft_id}:pl"},
                {"text": "🇧🇾 Беларусь", "callback_data": f"pcountry:{draft_id}:by"},
            ],
            [{"text": "❌ Отмена", "callback_data": "pcancel"}],
        ]
        text += "\n\n❓ *Укажи основную страну* (где база клуба — определяет путь страницы):"
    else:
        keyboard = [
            [{"text": "✅ Создать страницу", "callback_data": f"pconfirm:{draft_id}"}],
            [{"text": "✏️ Дополнить текстом", "callback_data": f"pmore:{draft_id}"}],
            [{"text": "❌ Отмена", "callback_data": "pcancel"}],
        ]
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Сводка отправлена в Telegram (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️ Telegram {e.code}: {body[:300]}")
        # Ретрай без Markdown
        if e.code == 400 and "parse entities" in body.lower():
            payload.pop("parse_mode", None)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data, headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                print("✅ Отправлено plain text")
            except Exception as e2:
                print(f"⚠️ Повтор не удался: {e2}")
    except Exception as e:
        print(f"⚠️ Отправка не удалась: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="Текст описания партнёра")
    ap.add_argument("--text-file", help="Файл с текстом описания")
    ap.add_argument("--chat-id", help="Telegram chat_id для отправки сводки")
    ap.add_argument("--out-summary", help="Куда записать сводку (для бота)")
    args = ap.parse_args()

    if args.text_file:
        description = Path(args.text_file).read_text(encoding="utf-8")
    elif args.text:
        description = args.text
    else:
        sys.exit("❌ Нужен --text или --text-file")

    networks = json.loads(NETWORKS_FILE.read_text(encoding="utf-8"))["networks"]
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    system_prompt = build_parse_prompt(networks, questions)

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=OPENROUTER_BASE_URL,
    )
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Описание партнёра:\n\n{description}"},
        ],
    )
    raw = (response.choices[0].message.content or "").strip()
    draft = parse_json_response(raw)

    # Валидация id
    pid = draft.get("id", "")
    if not re.match(r"^[a-z0-9-]+$", pid):
        # Чиним id
        pid = re.sub(r"[^a-z0-9-]", "", (draft.get("name", "partner")).lower().replace(" ", ""))
        draft["id"] = pid or "partner"

    # ── Обработка стран ──
    # Основная страна: если Claude не смог определить (null) — помечаем флагом,
    # бот спросит кнопкой. Иначе используем.
    country = draft.get("country")
    if not country or country in ("null", "none", ""):
        draft["country"] = None
        draft["_country_unclear"] = True
    else:
        draft["_country_unclear"] = False
        # countries = [основная] если не задан
        if not draft.get("countries"):
            draft["countries"] = [country]

    # acceptedCountries: если пусто — дефолт = [основная страна] (когда она ясна)
    if not draft.get("acceptedCountries"):
        if draft.get("country"):
            draft["acceptedCountries"] = [draft["country"]]
        else:
            draft["acceptedCountries"] = []

    # Добавляем country в _missing, если неясна (для сводки)
    if draft.get("_country_unclear"):
        missing = draft.get("_missing", [])
        if "основная страна" not in missing:
            missing.insert(0, "основная страна")
        draft["_missing"] = missing

    # Сохраняем черновик
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{draft['id']}.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = summarize(draft)
    print(f"✓ Распарсено → {out.relative_to(REPO_ROOT)}")
    print()
    print(summary)

    if args.out_summary:
        Path(args.out_summary).write_text(summary, encoding="utf-8")

    # Отправляем сводку с кнопками в Telegram
    if args.chat_id:
        send_telegram_summary(args.chat_id, draft)


if __name__ == "__main__":
    main()
