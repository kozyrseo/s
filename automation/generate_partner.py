#!/usr/bin/env python3
"""
KOZYR — генератор страницы партнёра через Anthropic API.

Работает по той же схеме, что generate.py (статьи):
  Anthropic API + system_prompt (partner_prompt.md) → HTML страницы.

Вход:  _partner_drafts/{id}.json (собран Telegram-ботом из анкеты)
Выход: _pending_partner/{id}/index.html (RU) + партнёр в partners.json

Запуск:
    python automation/generate_partner.py --id royalclub
    python automation/generate_partner.py --id royalclub --publish  # сразу в прод

Требует: OPENROUTER_API_KEY в окружении (как generate.py).
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI

# ── Пути ──
AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION.parent
PROMPT_FILE = AUTOMATION / "prompts" / "partner_prompt.md"
NETWORKS_FILE = AUTOMATION / "networks.json"
PARTNERS_JSON = REPO_ROOT / "partners.json"
DRAFTS_DIR = REPO_ROOT / "_partner_drafts"
PENDING_DIR = REPO_ROOT / "_pending_partner"

# ── Модель (как в generate.py) ──
MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 32000  # страница большая (~2000 строк), нужен запас

# Маркеры HTML в ответе AI
HTML_START = "---PARTNER-HTML-START---"
HTML_END = "---PARTNER-HTML-END---"


def load_reference(partner_type: str) -> str:
    """Загружает эталонную страницу как образец структуры."""
    if partner_type == "club":
        ref = REPO_ROOT / "ua" / "clubs" / "klubok" / "index.html"
    else:
        ref = REPO_ROOT / "ua" / "rooms" / "pokerbet" / "index.html"
    return ref.read_text(encoding="utf-8")


def build_user_message(draft: dict, reference_html: str, networks: dict) -> str:
    """Формирует запрос к AI: данные партнёра + эталон + инструкция формата."""
    net = networks.get(draft.get("network", ""), {})
    draft_json = json.dumps(draft, ensure_ascii=False, indent=2)

    return f"""Создай страницу партнёра для KOZYR по данным ниже.

ДАННЫЕ ПАРТНЁРА (из анкеты):
```json
{draft_json}
```

СПРАВКА О СЕТИ/ПРИЛОЖЕНИИ:
{json.dumps(net, ensure_ascii=False, indent=2) if net else "(своя сеть партнёра)"}

ЭТАЛОН СТРУКТУРЫ (страница {"клуба" if draft.get("type") == "club" else "рума"} — копируй структуру, стиль, вёрстку, но пиши контент под нового партнёра):
```html
{reference_html}
```

ЗАДАЧА:
1. Создай ПОЛНУЮ HTML-страницу нового партнёра по образцу эталона.
2. Сохрани ВСЮ структуру (14 секций), стиль, вёрстку эталона.
3. Механические данные (score, лимиты, плюсы/минусы, FAQ) — строго из анкеты.
4. Текст секций (about, лицензия, бонусы и т.д.) — напиши уникально под партнёра.
5. Логотип KOZYR в header И footer — РЕАЛЬНЫЙ градиентный (как в эталоне), НЕ примитивный.
6. Замени все данные {draft.get("name")} везде: meta, title, canonical, og, H1, факты.
7. canonical и og:url → https://kozyr.club{partner_path_for(draft)}
8. Соблюдай ВСЕ правила из системного промпта (только этот партнёр, без конкурентов).

ФОРМАТ ОТВЕТА (строго):
{HTML_START}
<!DOCTYPE html>
... полный HTML страницы ...
{HTML_END}

Верни ТОЛЬКО HTML между маркерами, без комментариев до/после."""


def partner_path_for(draft: dict, lang_prefix: str = "") -> str:
    """URL-путь страницы партнёра."""
    country = draft.get("country", "ua")
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    if lang_prefix:
        return f"/{country}/{lang_prefix}/{kind}/{draft['id']}/"
    return f"/{country}/{kind}/{draft['id']}/"


def parse_html_response(raw_text: str) -> str:
    """Извлекает HTML между маркерами."""
    text = raw_text.strip()
    # Снимаем возможные code-fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    if HTML_START not in text or HTML_END not in text:
        raise RuntimeError(
            f"Ответ без HTML-маркеров. START: {HTML_START in text}, END: {HTML_END in text}"
        )
    _, rest = text.split(HTML_START, 1)
    html, _ = rest.split(HTML_END, 1)
    return html.strip()


def inject_cta_link(html: str, draft: dict) -> str:
    """Подставляет реф-ссылку партнёра в CTA-кнопки.

    LLM копирует из эталонной страницы плейсхолдер ссылки (t.me/kozyr_support),
    т.к. в промпте ссылки нет. Здесь мы надёжно, без участия модели, заменяем
    href у всех кнопок с атрибутом data-partner на draft['ref_url'].
    Ссылки без data-partner (напр. «Оставить отзыв») НЕ трогаем.
    Если ref_url в анкете нет — оставляем как есть.
    """
    ref = (draft.get("ref_url") or "").strip()
    if not ref:
        print("  ⚠️ ref_url в анкете нет — кнопка «Перейти» останется с плейсхолдером.")
        return html

    def _replace_href(m):
        tag = m.group(0)
        if 'href="' in tag:
            return re.sub(r'href="[^"]*"', f'href="{ref}"', tag, count=1)
        # href нет в теге — добавим
        return tag[:2] + f' href="{ref}"' + tag[2:]

    new_html, n = re.subn(r'<a\b[^>]*\bdata-partner\b[^>]*>', _replace_href, html)
    print(f"  ✓ Реф-ссылка подставлена в {n} CTA-кнопок: {ref[:50]}")
    return new_html


def generate_html_with_continuation(client, system_prompt, user_message, max_parts=5):
    """
    Генерирует HTML устойчиво к обрыву по лимиту токенов.

    Страница партнёра большая (~100 КБ / ~2000 строк), и один ответ модели
    может не влезть в лимит вывода MAX_TOKENS — тогда он обрывается, не поставив
    закрывающий маркер ---PARTNER-HTML-END---. Здесь мы это ловим: пока в
    накопленном ответе нет END-маркера и модель обрывается по длине, просим её
    ПРОДОЛЖИТЬ ровно с места обрыва и склеиваем куски (до max_parts частей).
    Каждый вызов остаётся в пределах известного рабочего лимита 32000 токенов.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    full = ""
    for part in range(1, max_parts + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=messages,
        )
        choice = resp.choices[0]
        chunk = choice.message.content or ""
        full += chunk
        print(f"  Часть {part}: +{len(chunk)} символов (всего {len(full)})")

        if HTML_END in full:
            break  # страница доведена до конца

        finish = getattr(choice, "finish_reason", None)
        # Если модель остановилась НЕ по длине (а END-маркера так и нет) —
        # продолжать бессмысленно, что-то другое пошло не так.
        if finish not in ("length", "max_tokens", None):
            print(f"  Модель остановилась по причине '{finish}', END-маркера нет — прекращаю.")
            break
        if part == max_parts:
            print("  Достигнут предел частей, END-маркера всё ещё нет.")
            break

        # Просим продолжить ровно с места обрыва, без повторов.
        messages.append({"role": "assistant", "content": chunk})
        messages.append({
            "role": "user",
            "content": (
                "Ответ оборвался по лимиту. Продолжи HTML РОВНО с того места, "
                "на котором остановился — ничего не повторяй и не начинай заново. "
                f"Доведи страницу до конца и обязательно закрой её маркером {HTML_END} "
                "на отдельной строке в самом конце."
            ),
        })
    return full.strip()


def validate_html(html: str, draft: dict, errors: list) -> None:
    """Базовые проверки сгенерированной страницы."""
    # Настоящий логотип (не примитивный)
    if 'd="M12 8h9v20' in html:
        errors.append("Примитивный логотип (M12 8h9v20) — нужен настоящий градиентный")
    # Логотип должен быть в header и footer (хотя бы 2 linearGradient)
    if html.count("linearGradient") < 2:
        errors.append("Логотип KOZYR должен быть в header И footer (мин. 2 градиента)")
    # Имя партнёра присутствует
    if draft.get("name", "") not in html:
        errors.append(f"Имя партнёра '{draft.get('name')}' не найдено на странице")
    # Нет эмодзи (грубая проверка на частые)
    for emoji in ["🎯", "🔥", "✅", "💰", "🎁"]:
        if emoji in html:
            errors.append(f"Найден эмодзи {emoji} — на странице эмодзи запрещены")
            break
    # Базовая целостность HTML
    if "<!DOCTYPE html>" not in html or "</html>" not in html:
        errors.append("HTML неполный (нет DOCTYPE или </html>)")


def add_to_partners_json(draft: dict) -> None:
    """Добавляет партнёра в partners.json (если ещё нет)."""
    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    existing_ids = {p["id"] for p in data["partners"]}
    if draft["id"] in existing_ids:
        print(f"  partners.json: '{draft['id']}' уже есть, пропускаю")
        return
    # Собираем объект партнёра из анкеты
    partner = build_partner_object(draft)
    data["partners"].append(partner)
    data["_meta"]["count"] = len(data["partners"])
    PARTNERS_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ partners.json: добавлен '{draft['id']}' (запусти build_partners.py)")


def logo_initials(name: str) -> str:
    """Инициалы для текстового лого: «TON Poker»→«TP», «PokerBet»→«PB»."""
    name = (name or "").strip()
    if not name:
        return "?"
    spaced = re.sub(r"(?<=[a-zа-яёіїєґ])(?=[A-ZА-ЯЁІЇЄҐ])", " ", name)
    words = [w for w in re.split(r"[\s\-_]+", spaced) if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1", "y", "да")


def build_partner_object(draft: dict) -> dict:
    """Преобразует анкету в объект partners.json."""
    rake = draft.get("rake", "none")
    if rake != "none":
        try:
            rake = int(rake)
        except (ValueError, TypeError):
            rake = "none"
    return {
        "id": draft["id"],
        "name": draft["name"],
        "type": draft.get("type", "room"),
        "score": float(draft.get("score", 0)),
        "rake": rake,
        **({"rakeText": draft["rakeText"]} if draft.get("rakeText") else {}),
        **({"rakeLabel": draft["rakeLabel"]} if draft.get("rakeLabel") else {}),
        "currency": draft.get("currency", "UAH"),
        "license": draft.get("license", ""),
        "url": partner_path_for(draft),
        "access": draft.get("access", "direct"),
        "network": draft.get("network", ""),
        "networkLabel": draft.get("networkLabel", ""),
        "country": draft.get("country", "ua"),
        "countries": draft.get("countries", ["ua"]),
        "acceptedCountries": draft.get("acceptedCountries", ["ua"]),
        "limits": draft.get("limits", []),
        "games": draft.get("games", []),
        "software": draft.get("software", []),
        "payments": draft.get("payments", []),
        "bonus": draft.get("bonus", []),
        "payoutHours": int(draft.get("payoutHours", 24)),
        "payoutLabel": draft.get("payoutLabel", ""),
        "note": draft.get("note", ""),
        "logo": {
            "text": logo_initials(draft.get("name", "?")),
            "from": draft.get("logo_from") or "#14358F",
            "to": draft.get("logo_to") or "#2A6BFF",
        },
        "card": {
            "logoImg": draft.get("logo_img", ""),
            "kind": f"{draft.get('networkLabel', '')}",
            "dark": as_bool(draft.get("dark_card", False)),
            "rows": build_card_rows(draft),
        },
    }


def build_card_rows(draft: dict) -> list:
    """Строки карточки (5 шт) из данных анкеты."""
    rows = [
        ["Рейкбек", "rake"],
        ["Валюта", f"{draft.get('currency', 'UAH')}", False],
    ]
    if draft.get("bonus"):
        rows.append(["Бонус", draft.get("rakeLabel", "—"), True])
    if draft.get("minDeposit"):
        rows.append(["Мин. депозит", draft["minDeposit"], False])
    if draft.get("games"):
        games_str = ", ".join(draft["games"][:4])
        rows.append(["Форматы", games_str, False])
    return rows[:5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="ID партнёра из _partner_drafts/")
    ap.add_argument("--publish", action="store_true", help="сразу в прод")
    args = ap.parse_args()

    draft_file = DRAFTS_DIR / f"{args.id}.json"
    if not draft_file.exists():
        sys.exit(f"❌ Нет файла анкеты: {draft_file}")

    draft = json.loads(draft_file.read_text(encoding="utf-8"))

    # ── Быстрый путь публикации: превью уже собрано и проверено оператором ──
    # Если оператор нажал «Опубликовать» после проверки превью, публикуем
    # РОВНО ту страницу, что он видел (_pending_partner/{id}/index.html),
    # а не генерируем заново (LLM недетерминирован → в прод ушёл бы другой текст,
    # плюс лишний вызов API). Заново генерим только если превью почему-то нет.
    preview_file = PENDING_DIR / draft["id"] / "index.html"
    if args.publish and preview_file.exists():
        html = preview_file.read_text(encoding="utf-8")
        out = REPO_ROOT / partner_path_for(draft).strip("/") / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"✓ Опубликовано из превью (без повторной генерации): {out.relative_to(REPO_ROOT)}")
        add_to_partners_json(draft)
        print()
        print("✓ Готово (перенос готового превью).")
        print("  Не забудь: python automation/build_partners.py (пересобрать partners.js)")
        return

    networks = json.loads(NETWORKS_FILE.read_text(encoding="utf-8"))["networks"]
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    reference = load_reference(draft.get("type", "room"))

    print(f"Партнёр: {draft['name']} ({draft.get('type')}, {draft.get('network')})")
    print(f"Модель: {MODEL}, max_tokens: {MAX_TOKENS}")

    # Вызов API (как generate.py)
    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=OPENROUTER_BASE_URL,
    )
    user_message = build_user_message(draft, reference, networks)

    raw = generate_html_with_continuation(client, system_prompt, user_message)
    html = parse_html_response(raw)
    html = inject_cta_link(html, draft)

    # Валидация
    errors = []
    validate_html(html, draft, errors)
    if errors:
        print("⚠️ Проблемы страницы:")
        for e in errors:
            print(f"   {e}")
        # Критичные (логотип, целостность) — останавливают
        critical = [e for e in errors if "логотип" in e.lower() or "неполн" in e.lower()]
        if critical:
            sys.exit("🛑 Критичные проблемы — не сохраняю.")

    # Сохранение
    if args.publish:
        out = REPO_ROOT / partner_path_for(draft).strip("/") / "index.html"
    else:
        out = PENDING_DIR / draft["id"] / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ Страница: {out.relative_to(REPO_ROOT)}")

    # Добавляем в partners.json
    add_to_partners_json(draft)

    print()
    print("✓ Готово." + ("" if args.publish
                          else " Проверь _pending_partner/, потом публикуй через бота."))
    print("  Не забудь: python automation/build_partners.py (пересобрать partners.js)")


if __name__ == "__main__":
    main()
