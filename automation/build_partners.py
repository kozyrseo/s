#!/usr/bin/env python3
"""
KOZYR — сборщик partners.js из partners.json.

partners.json = единая точка правды (данные).
partners.js   = генерируется этим скриптом (НЕ редактировать вручную).

Ключевая функция: НОРМАЛИЗАЦИЯ данных для защиты вёрстки карточек:
  - обрезка длинного текста в rows (чтобы не вылезал за карточку)
  - фикс числа строк карточки (все партнёры = одинаковая высота)
  - проверка обязательного лого-картинки
  - дефолты для logo.from/to (чтобы не сломать CSS-градиент)

Запуск:
    python automation/build_partners.py
    python automation/build_partners.py --check   # только проверка, без записи

Использует:
    automation/partners.head.js  — шапка + IIFE (неизменяемая)
    automation/partners.tail.js  — код рендера (неизменяемый)
    partners.json                — данные
Пишет:
    partners.js
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path


def logo_initials(name: str) -> str:
    """Инициалы: «TON Poker»→«TP», «PokerBet»→«PB», «KlubOk»→«KO»."""
    name = (name or "").strip()
    if not name:
        return "?"
    spaced = re.sub(r"(?<=[a-zа-яёіїєґ])(?=[A-ZА-ЯЁІЇЄҐ])", " ", name)
    words = [w for w in re.split(r"[\s\-_]+", spaced) if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOMATION = Path(__file__).resolve().parent
PARTNERS_JSON = REPO_ROOT / "partners.json"
HEAD = AUTOMATION / "partners.head.js"
TAIL = AUTOMATION / "partners.tail.js"
OUTPUT = REPO_ROOT / "partners.js"

# ── ЛИМИТЫ ЗАЩИТЫ ВЁРСТКИ ──
MAX_ROW_VALUE_LEN = 42      # макс длина значения в строке карточки
MAX_NAME_LEN = 24           # макс длина имени партнёра
MAX_KIND_LEN = 34           # макс длина бейджа (kind)
CARD_ROWS_COUNT = 5         # ФИКСИРОВАННОЕ число строк карточки (все партнёры)
DEFAULT_LOGO_FROM = "#14358F"
DEFAULT_LOGO_TO = "#2A6BFF"


def truncate(text: str, limit: int) -> str:
    """Обрезает текст с многоточием, если длиннее лимита."""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_partner(p: dict, errors: list) -> dict:
    """
    Нормализует одного партнёра для безопасной вёрстки.
    Мутирует копию, возвращает её. Ошибки пишет в errors.
    """
    p = json.loads(json.dumps(p))  # deep copy
    pid = p.get("id", "?")

    # 1. Обязательные поля
    for field in ["id", "name", "type", "network", "score"]:
        if not p.get(field):
            errors.append(f"[{pid}] нет обязательного поля: {field}")

    # 2. Логотип: КАРТИНКА обязательна (решение №3)
    card = p.setdefault("card", {})
    if not card.get("logoImg"):
        errors.append(f"[{pid}] нет card.logoImg — логотип-картинка обязателен")

    # 3. Дефолты для градиента (fallback, чтобы CSS не сломался)
    logo = p.setdefault("logo", {})
    if not logo.get("from"):
        logo["from"] = DEFAULT_LOGO_FROM
    if not logo.get("to"):
        logo["to"] = DEFAULT_LOGO_TO
    if not logo.get("text"):
        logo["text"] = logo_initials(p.get("name", "?"))

    # 4. Обрезка длинных текстов (защита вёрстки)
    p["name"] = truncate(p.get("name", ""), MAX_NAME_LEN)
    if card.get("kind"):
        card["kind"] = truncate(card["kind"], MAX_KIND_LEN)

    # 5. Нормализация rows: обрезка значений + ФИКС числа строк
    rows = card.get("rows", [])
    normalized_rows = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 2:
            continue
        label = r[0]
        val = r[1]
        # "rake" — спец-значение, не трогаем (рендерится динамически)
        if val != "rake" and isinstance(val, str):
            val = truncate(val, MAX_ROW_VALUE_LEN)
        hi = r[2] if len(r) > 2 else False
        normalized_rows.append([label, val, hi])

    # ФИКС числа строк: дополняем пустыми или обрезаем до CARD_ROWS_COUNT
    # Пустые строки [.,.] с пустым значением отфильтруются в рендере,
    # но резервируют высоту — карточки получаются одинаковыми.
    while len(normalized_rows) < CARD_ROWS_COUNT:
        normalized_rows.append(["", "", False])
    normalized_rows = normalized_rows[:CARD_ROWS_COUNT]
    card["rows"] = normalized_rows

    return p


def build_partners_array(partners: list) -> str:
    """Формирует JS-код массива PARTNERS из списка объектов."""
    # Компактный, но читаемый JSON → JS (JSON — валидный JS-литерал)
    items = []
    for p in partners:
        obj = json.dumps(p, ensure_ascii=False, indent=6)
        # Сдвигаем отступ, чтобы вписать в структуру файла
        obj = "\n".join("  " + line for line in obj.split("\n"))
        items.append(obj)
    body = ",\n".join(items)
    return "  var PARTNERS = [\n" + body + "\n  ];\n"


def _esc(s) -> str:
    """HTML-escape (как esc() в index.html)."""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# Человекочитаемые подписи для платёжек и лимитов (для fallback-каталога).
_PAY_LABELS = {
    "card": "Карта", "bank": "Банк", "crypto": "Крипта",
    "p2p": "P2P", "ewallet": "Кошелёк", "agent": "Агент",
}


def build_static_catalog_html(partners: list, country: str = "ua") -> str:
    """HTML статического каталога (SEO + fallback без JS) из данных партнёров.

    Простой семантический блок: лого, название, рейкбек, валюта, заметка,
    ключевые факты (лимиты/платёжки/выплата), ссылка на обзор. НЕ копирует
    интерактивный финдер и НЕ содержит runtime-зависимостей (гео и т.п.).
    Фильтрует партнёров по стране (по полю countries), чтобы работать и для
    новых гео. Порядок — по убыванию рейкбека (как логичный дефолт).
    """
    # Фильтр по стране: партнёр показывается, если обслуживает эту страну
    # (или помечен 'all'/'*'), либо если поле не задано.
    def serves(p):
        cs = p.get("countries") or []
        if not cs:
            return True
        return country in cs or "all" in cs or "*" in cs

    items = [p for p in partners if serves(p)]

    # Сортировка: сначала с бо́льшим рейкбеком, потом остальные.
    def rake_key(p):
        r = p.get("rake")
        return r if isinstance(r, (int, float)) else -1
    items.sort(key=rake_key, reverse=True)

    cards = []
    for p in items:
        name = _esc(p.get("name", ""))
        url = _esc(p.get("url", "#"))
        cur = _esc(p.get("currency", ""))
        note = _esc(p.get("note", ""))

        # Логотип: картинка или градиентные инициалы
        logo = p.get("logo") or {}
        logo_img = (p.get("card") or {}).get("logoImg") or p.get("logoImg")
        if logo_img:
            logo_html = (f'<span class="kf-static-logo" style="padding:0;overflow:hidden">'
                         f'<img src="{_esc(logo_img)}" alt="" width="44" height="44" '
                         f'loading="lazy" style="width:100%;height:100%;object-fit:cover"></span>')
        else:
            frm = _esc(logo.get("from", "#2668FF"))
            to = _esc(logo.get("to", "#1E52D9"))
            txt = _esc(logo.get("text", (name[:2].upper() if name else "?")))
            logo_html = (f'<span class="kf-static-logo" '
                         f'style="background:linear-gradient(135deg,{frm},{to})">{txt}</span>')

        # Рейкбек
        rake = p.get("rake")
        if isinstance(rake, (int, float)) and rake > 0:
            rake_html = (f'<span class="kf-static-rake">'
                         f'<span class="kf-static-rake__num">до&nbsp;{int(rake)}</span>'
                         f'<span class="kf-static-rake__pct">%</span></span>')
        else:
            rake_html = ('<span class="kf-static-rake kf-static-rake--none">'
                         '<span class="kf-static-rake__num">Без рейкбека</span></span>')

        # Факты: лимиты (первые 3), платёжки, выплата
        facts = []
        limits = p.get("limits") or []
        if limits:
            facts.append(f'<span class="kf-static-fact">{_esc(" · ".join(limits[:3]))}</span>')
        pays = p.get("payments") or []
        if pays:
            pay_txt = ", ".join(_PAY_LABELS.get(x, x) for x in pays[:3])
            facts.append(f'<span class="kf-static-fact">{_esc(pay_txt)}</span>')
        payout = p.get("payoutLabel")
        if payout:
            facts.append(f'<span class="kf-static-fact">Вывод: {_esc(payout)}</span>')
        facts_html = "".join(facts)

        card = (
            '<article class="kf-static-item">'
            '<div class="kf-static-item__top">'
            f'{logo_html}'
            '<div>'
            f'<div class="kf-static-item__name">{name}</div>'
            f'<div class="kf-static-item__meta"><span class="kf-static-item__cur">{cur}</span></div>'
            '</div>'
            '</div>'
            f'<div>{rake_html}</div>'
            + (f'<p class="kf-static-item__note">{note}</p>' if note else '')
            + (f'<div class="kf-static-item__facts">{facts_html}</div>' if facts_html else '')
            + f'<a class="kf-static-item__go" href="{url}">Открыть обзор'
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
              '<path d="M7 17L17 7M17 7H8M17 7V16"/></svg></a>'
            '</article>'
        )
        cards.append(card)

    return "\n        ".join(cards)


def inject_static_catalog(html_path: Path, partners: list, country: str = "ua") -> bool:
    """Впечатывает статический каталог между маркерами в HTML-странице.

    Маркеры: <!-- KOZYR:STATIC_CATALOG:START --> ... :END -->.
    Возвращает True если файл изменён. Идемпотентно (можно гонять многократно).
    """
    if not html_path.exists():
        return False
    html = html_path.read_text(encoding="utf-8")
    start = "<!-- KOZYR:STATIC_CATALOG:START -->"
    end = "<!-- KOZYR:STATIC_CATALOG:END -->"
    if start not in html or end not in html:
        return False

    cards_html = build_static_catalog_html(partners, country)
    new_block = f"{start}\n        {cards_html}\n        {end}"

    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    new_html = pattern.sub(new_block, html)

    if new_html != html:
        html_path.write_text(new_html, encoding="utf-8")
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="только проверка")
    args = ap.parse_args()

    # Читаем данные
    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    partners = data["partners"]

    # Нормализуем + собираем ошибки
    errors = []
    normalized = [normalize_partner(p, errors) for p in partners]

    if errors:
        print("❌ Ошибки нормализации:")
        for e in errors:
            print(f"   {e}")
        if args.check:
            sys.exit(1)
        # При обычной сборке — критичные ошибки останавливают
        critical = [e for e in errors if "обязательного" in e or "logoImg" in e]
        if critical:
            print("\n🛑 Критичные ошибки — сборка остановлена.")
            sys.exit(1)
        print("\n⚠️ Некритичные — продолжаю сборку.")

    print(f"✓ Партнёров: {len(normalized)}")
    for p in normalized:
        rows_filled = sum(1 for r in p["card"]["rows"] if r[1])
        print(f"   • {p['id']}: {p['type']}/{p['network']}, "
              f"строк карточки {rows_filled}/{CARD_ROWS_COUNT}, лого {'✓' if p['card'].get('logoImg') else '✗'}")

    if args.check:
        print("\n✓ Проверка пройдена (запись не выполнялась).")
        return

    # Собираем partners.js: голова + массив + хвост
    head = HEAD.read_text(encoding="utf-8")
    tail = TAIL.read_text(encoding="utf-8")
    array_js = build_partners_array(normalized)

    output = head.rstrip() + "\n\n" + array_js + "\n" + tail.lstrip()
    OUTPUT.write_text(output, encoding="utf-8")

    print(f"\n✓ Собран {OUTPUT.relative_to(REPO_ROOT)} ({len(output.splitlines())} строк)")
    print("  Не редактируй partners.js вручную — правь partners.json + пересобирай.")

    # Статический каталог (SEO + fallback без JS) на главной UA.
    # Генерируется из тех же данных partners.json — одна точка правды.
    # Для новых гео сюда можно добавить их главные страницы с country=код.
    STATIC_PAGES = [
        (REPO_ROOT / "ua" / "index.html", "ua"),
        (REPO_ROOT / "ua" / "uk" / "index.html", "ua"),
    ]
    for page_path, country in STATIC_PAGES:
        try:
            changed = inject_static_catalog(page_path, normalized, country)
            if changed:
                print(f"✓ Статический каталог обновлён: {page_path.relative_to(REPO_ROOT)}")
            elif page_path.exists():
                print(f"  Статический каталог актуален: {page_path.relative_to(REPO_ROOT)}")
        except Exception as e:
            print(f"⚠️ Не удалось обновить каталог в {page_path.relative_to(REPO_ROOT)}: {e}")


if __name__ == "__main__":
    main()
