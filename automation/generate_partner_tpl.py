#!/usr/bin/env python3
"""
KOZYR — генератор страницы партнёра на ШАБЛОНЕ (Стадия 3).

Вместо переписывания всей 100-КБ страницы LLM пишет только КОНТЕНТ (проза +
rich-блоки) компактным JSON (~11 КБ). Страница собирается из шаблона через
render_partner.py. Это в разы дешевле по OpenRouter и не ломает вёрстку.

Поток:
  анкета _partner_drafts/{id}.json
    → LLM пишет content JSON (partner_content_prompt.md + пример content_example.json)
    → render_partner.build_page(анкета, контент) → HTML
    → _pending_partner/{id}/index.html (preview) или прод-путь (publish).
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT  = AUTOMATION.parent
DRAFTS     = REPO_ROOT / "_partner_drafts"
PENDING    = REPO_ROOT / "_pending_partner"
PROMPT     = AUTOMATION / "partner_content_prompt.md"
EXAMPLE    = AUTOMATION / "content_example.json"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 16000   # контента ~11 КБ (~3.5k токенов) — с запасом, один вызов

CONTENT_KEYS = [
    "meta_title", "meta_description", "og_title", "og_description",
    "schema_description", "hero_sub", "hero_badge", "hero_lead", "verdict",
    "sticky_note", "facts",
    "about", "legality", "software", "games", "traffic",
    "deposits", "bonus", "rakeback", "kyc", "cta",
]


def parse_content_json(raw: str) -> dict:
    t = (raw or "").strip()
    if "```" in t:
        m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', t, re.S)
        if m:
            t = m.group(1)
    try:
        d = json.loads(t)
    except Exception as e:
        raise RuntimeError(f"Ответ LLM — не валидный JSON: {e}\nНачало: {t[:200]}")
    missing = [k for k in CONTENT_KEYS if k not in d]
    if missing:
        raise RuntimeError(f"В контенте не хватает ключей: {missing}")
    return d


def generate_content(draft: dict) -> dict:
    """Вызывает LLM и возвращает content JSON."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
    system = PROMPT.read_text(encoding="utf-8")
    example = EXAMPLE.read_text(encoding="utf-8")
    user = (
        "ПРИМЕР структуры (партнёр TON Poker) — образец формата и разметки:\n"
        f"```json\n{example}\n```\n\n"
        "Теперь напиши content JSON для ЭТОГО партнёра. Контент оригинальный, "
        "на основе данных ниже; структуру и разметку бери из примера. "
        "Ответь ТОЛЬКО валидным JSON со всеми 16 ключами.\n\n"
        "ДАННЫЕ АНКЕТЫ:\n"
        f"```json\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n```"
    )
    print(f"  LLM: пишу контент ({MODEL}, max_tokens={MAX_TOKENS})…")
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    raw = resp.choices[0].message.content or ""
    content = apply_content_fixups(parse_content_json(raw), "ru")
    print(f"  ✓ контент получен ({len(json.dumps(content, ensure_ascii=False))} симв., все ключи на месте)")
    return content


def rewrite_links_ru_uk(text: str) -> str:
    """Переписывает внутренние ссылки /ua/... → /ua/uk/... (ассеты не трогает)."""
    if not isinstance(text, str):
        return text
    def repl(m):
        url = m.group(0)
        if url.startswith("/ua/uk/"):
            return url
        if re.search(r"\.(webp|jpg|jpeg|png|svg|gif|ico|css|js|pdf)($|[?#])", url):
            return url
        return "/ua/uk/" + url[len("/ua/"):]
    return re.sub(r'/ua/[^\s"\'<>)]+', repl, text)


TRANSLATE_SYSTEM = """Ти — перекладач покерного сайту. Переклади ЗНАЧЕННЯ цього JSON з
російської на українську мову.

ПРАВИЛА:
- Звертання — на «ти» (українське тикання природне).
- «Покерный рум» → «покерний рум» (не «зал»).
- «Вывод денег» → «виведення коштів».
- Уникай кальок з російської, пиши природною українською.
- ЗБЕРЕЖИ всі HTML-теги і структуру як є (<h2>, <p>, <ul>, <table>, <strong>, <em>, class-атрибути).
- Перекладай ТІЛЬКИ текст всередині тегів, самі теги не чіпай.
- Числа, назви (TON Poker, USDT, Cryptobot, мемкоїни) залишай як є.
- ОБОВ'ЯЗКОВО переклади ВСІ значення, включно з вкладеним об'єктом "facts"
  (rakeback, license, currency, kyc, access, formats, payouts, payments) та полем
  "sticky_note". Не залишай російських слів: "Крипта"→"Криптовалюта", "Нет"→"Ні",
  "Есть"→"Є", "Кэш"→"Кеш", "от мгновенно"→"від миттєво", "Крипто-рум"→"Крипто-рум",
  "Депозит от"→"Депозит від", "вход"→"вхід", "через"→"через".

Поверни ТІЛЬКИ валідний JSON з ТИМИ САМИМИ ключами. Без пояснень."""


# Детермінована зачистка контенту. Критично: аудиторія українська —
# НЕ можна московський час (МСК/Москва) і чутливі гео-терміни (СНГ/СНД).
# Застосовується і до RU-версії (сайт для укр. аудиторії), і до UK.
CONTENT_FIXUPS = {
    "ru": [
        (re.compile(r"\bКрипта\b"), "Криптовалюта"),
        (re.compile(r"\bкрипта\b"), "криптовалюта"),
        (re.compile(r"\s*по\s+МСК"), " по киевскому времени"),
        (re.compile(r"\s*за\s+МСК"), " по киевскому времени"),
        (re.compile(r"\(МСК\)"), "(киевское время)"),
        (re.compile(r"\bМСК\b"), "киевское время"),
        (re.compile(r"\s*\(на усмотрение аффилейта\)"), ""),
        (re.compile(r"на усмотрение аффилейта"), "индивидуально"),
        (re.compile(r"платит аффилейт по своему усмотрению"), "выплачивается по условиям партнёра"),
        (re.compile(r"\bаффилейт\w*"), "партнёр"),
        (re.compile(r"экс-?СН[ГД]"), "русскоязычных стран"),
        (re.compile(r"\bСН[ГД]\b"), "русскоязычных стран"),
        (re.compile(r"по\s+московскому\s+времени"), "по киевскому времени"),
        (re.compile(r"московск(ому|ое|ого)\s+врем"), "киевск\1 врем"),
        (re.compile(r"\bмск\b", re.I), "по киевскому времени"),
        (re.compile(r"постсоветск"), "русскоязычн"),
        (re.compile(r"на\s+рубл[а-я]*"), "на USDT"),
        (re.compile(r"в\s+рубл[а-я]*"), "в USDT"),
        (re.compile(r"рубл[а-я]*"), "USDT"),
        (re.compile(r"₽"), "USDT"),
    ],
    "uk": [
        (re.compile(r"\bКрипта\b"), "Криптовалюта"),
        (re.compile(r"\bкрипта\b"), "криптовалюта"),
        (re.compile(r"\s*за\s+МСК"), " за київським часом"),
        (re.compile(r"\s*по\s+МСК"), " за київським часом"),
        (re.compile(r"\(МСК\)"), "(київський час)"),
        (re.compile(r"\bМСК\b"), "київський час"),
        (re.compile(r"\s*\(на розсуд партнера\)"), ""),
        (re.compile(r"на розсуд аффілейта|на розсуд партнера"), "індивідуально"),
        (re.compile(r"\bаффілейт\w*|\bафілейт\w*"), "партнер"),
        (re.compile(r"Перейти в\b"), "Перейти до"),
        (re.compile(r"\s*\(на усмотрение аффилейта\)"), ""),
        (re.compile(r"на усмотрение аффилейта|на розсуд аффілейта"), "індивідуально"),
        (re.compile(r"\bаффилейт\w*"), "партнер"),
        (re.compile(r"екс-?СН[ГД]"), "російськомовних країн"),
        (re.compile(r"\bСН[ГД]\b"), "російськомовних країн"),
        (re.compile(r"по\s+московському\s+час[уі]"), "за київським часом"),
        (re.compile(r"московськ(ому|е|ого)\s+час"), "київськ\1 час"),
        (re.compile(r"\bмск\b", re.I), "за київським часом"),
        (re.compile(r"пострадянськ"), "російськомовн"),
        (re.compile(r"на\s+рубл[а-яі]*"), "на USDT"),
        (re.compile(r"в\s+рубл[а-яі]*"), "в USDT"),
        (re.compile(r"рубл[а-яі]*"), "USDT"),
        (re.compile(r"₽"), "USDT"),
    ],
}

def apply_content_fixups(val, lang):
    fx = CONTENT_FIXUPS.get(lang, [])
    if isinstance(val, str):
        for pat, rep in fx:
            val = pat.sub(rep, val)
        return val
    if isinstance(val, dict):
        return {k: apply_content_fixups(v, lang) for k, v in val.items()}
    if isinstance(val, list):
        return [apply_content_fixups(x, lang) for x in val]
    return val


def translate_content(content_ru: dict, draft: dict):
    """Переводит контент + переводимые поля анкеты (pros/cons/faq) ru→uk.

    Возвращает (content_uk, draft_uk): черновик с переведёнными pros/cons/faq,
    чтобы плюсы/минусы/FAQ на украинской странице тоже были украинскими.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
    # бандл: контент + переводимые поля анкеты (ключи с __ — не контент)
    bundle = dict(content_ru)
    bundle["__pros"] = draft.get("pros", [])
    bundle["__cons"] = draft.get("cons", [])
    bundle["__faq"] = draft.get("faq", [])
    user = "JSON для перекладу (переклади ВСІ значення, включно з масивами __pros/__cons/__faq):\n```json\n" + json.dumps(bundle, ensure_ascii=False, indent=2) + "\n```"
    print(f"  LLM: перевод контента + анкеты ru→uk ({MODEL})…")
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": TRANSLATE_SYSTEM},
                  {"role": "user", "content": user}],
    )
    uk = parse_content_json(resp.choices[0].message.content or "")
    for k, v in uk.items():
        uk[k] = rewrite_links_ru_uk(v)
    content_uk = apply_content_fixups({k: v for k, v in uk.items() if not k.startswith("__")}, "uk")
    draft_uk = dict(draft)
    draft_uk["pros"] = apply_content_fixups(uk.get("__pros", draft.get("pros", [])), "uk")
    draft_uk["cons"] = apply_content_fixups(uk.get("__cons", draft.get("cons", [])), "uk")
    draft_uk["faq"] = apply_content_fixups(uk.get("__faq", draft.get("faq", [])), "uk")
    print(f"  ✓ перевод получен ({len(json.dumps(uk, ensure_ascii=False))} симв.)")
    return content_uk, draft_uk


def partner_path(draft: dict, lang_prefix: str = "") -> str:
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    country = draft.get("country", "ua")
    lp = f"{lang_prefix}/" if lang_prefix else ""
    return f"{country}/{lp}{kind}/{draft['id']}"


PARTNERS_JSON = REPO_ROOT / "partners.json"


def _rake_label(draft):
    """Значение строки «Рейкбек» для карточки."""
    rl = (draft.get("rakeLabel") or "").strip()
    if rl:
        return rl
    rake = draft.get("rake")
    if rake not in (None, "none", "", 0):
        return f"{rake}%"
    return "—"


GAME_LABELS = {"cash": "Кэш", "mtt": "MTT", "spin": "Спины", "spins": "Спины",
               "sng": "SNG", "of": "OFC", "ofc": "OFC", "plo": "PLO", "nlh": "NLH",
               "zoom": "Zoom", "fast": "Fast", "hu": "HU"}


def _games_label(games):
    return ", ".join(GAME_LABELS.get(str(x).strip().lower(), str(x).strip().capitalize())
                     for x in (games or []))


def build_card_rows(draft):
    """Строки карточки каталога (до 5) из анкеты."""
    rows = [
        ["Рейкбек", _rake_label(draft), True],
        ["Валюта", str(draft.get("currency", "USDT")), False],
    ]
    if draft.get("minDeposit"):
        rows.append(["Мин. депозит", draft["minDeposit"], False])
    if draft.get("games"):
        rows.append(["Форматы", _games_label(draft["games"][:4]), False])
    if draft.get("payoutLabel"):
        rows.append(["Выплаты", draft["payoutLabel"], False])
    return rows[:5]


def build_partner_object(draft):
    """Объект партнёра для каталога partners.json."""
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
        "currency": draft.get("currency", "USDT"),
        "license": draft.get("license", ""),
        "url": "/" + partner_path(draft) + "/",
        "access": draft.get("access", "direct"),
        "network": draft.get("network", ""),
        "networkLabel": draft.get("network_label", draft.get("networkLabel", "")),
        "country": draft.get("country", "ua"),
        "countries": draft.get("countries", [draft.get("country", "ua")]),
        "acceptedCountries": draft.get("acceptedCountries", ["all"]),
        "limits": draft.get("limits", []),
        "games": draft.get("games", []),
        "software": draft.get("software", []),
        "payments": draft.get("payments", []),
        "bonus": draft.get("bonus", []),
        "payoutHours": int(draft.get("payoutHours", 24)),
        "payoutLabel": draft.get("payoutLabel", ""),
        "note": draft.get("note", ""),
        "logo": {
            "text": draft.get("name", "?")[:2].upper(),
            "from": draft.get("logo_from", "#14358F"),
            "to": draft.get("logo_to", "#2A6BFF"),
        },
        "card": {
            "logoImg": draft.get("logo_img", ""),
            "kind": draft.get("network_label", draft.get("networkLabel", "")),
            "dark": draft.get("dark_card", "false") == "true",
            "rows": build_card_rows(draft),
        },
    }


def upsert_partner_json(draft):
    """Добавляет ИЛИ ОБНОВЛЯЕТ партнёра в partners.json (по id)."""
    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    obj = build_partner_object(draft)
    parts = data.get("partners", [])
    for i, p in enumerate(parts):
        if p.get("id") == draft["id"]:
            parts[i] = obj
            print(f"  ✓ partners.json: обновлён '{draft['id']}'")
            break
    else:
        parts.append(obj)
        print(f"  ✓ partners.json: добавлен '{draft['id']}'")
    data["partners"] = parts
    PARTNERS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Пересобираем каталог partners.js из обновлённого partners.json
    _rebuild_partners_js()


def _rebuild_partners_js():
    """Пересобирает partners.js (каталог главной) через build_partners.py."""
    import subprocess, sys as _sys
    bp = PARTNERS_JSON.parent / "automation" / "build_partners.py"
    if not bp.exists():
        print("  ⚠️ build_partners.py не найден — partners.js не пересобран")
        return
    try:
        r = subprocess.run([_sys.executable, str(bp)], capture_output=True, text=True)
        if r.returncode == 0:
            print("  ✓ partners.js пересобран (каталог главной обновлён)")
        else:
            print(f"  ⚠️ build_partners.py вернул код {r.returncode}: {r.stderr[:200]}")
    except Exception as e:
        print(f"  ⚠️ не удалось пересобрать partners.js: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--publish", action="store_true", help="в прод (иначе _pending_partner)")
    args = ap.parse_args()

    draft_file = DRAFTS / f"{args.id}.json"
    if not draft_file.exists():
        sys.exit(f"❌ Нет анкеты: {draft_file}")
    draft = json.loads(draft_file.read_text(encoding="utf-8"))
    print(f"Партнёр: {draft.get('name')} ({draft.get('type')}, {args.id})")

    # ── Русская версия ──
    content_ru = generate_content(draft)
    from render_partner import build_page
    html_ru = build_page(draft, content_ru, lang="ru")

    if args.publish:
        out_ru = REPO_ROOT / partner_path(draft) / "index.html"
    else:
        out_ru = PENDING / args.id / "index.html"
    out_ru.parent.mkdir(parents=True, exist_ok=True)
    out_ru.write_text(html_ru, encoding="utf-8")
    print(f"✓ RU страница: {out_ru} ({len(html_ru)} символов)")

    # Каталог (главная): добавляем/обновляем партнёра при публикации в прод
    if args.publish:
        upsert_partner_json(draft)

    # ── Украинская версия (перевод контента → тот же шаблон) ──
    content_uk, draft_uk = translate_content(content_ru, draft)
    html_uk = build_page(draft_uk, content_uk, lang="uk")

    if args.publish:
        out_uk = REPO_ROOT / partner_path(draft, lang_prefix="uk") / "index.html"
    else:
        out_uk = PENDING / (args.id + "_uk") / "index.html"
    out_uk.parent.mkdir(parents=True, exist_ok=True)
    out_uk.write_text(html_uk, encoding="utf-8")
    print(f"✓ UK страница: {out_uk} ({len(html_uk)} символов)")


if __name__ == "__main__":
    main()
