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
import sys
from pathlib import Path

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
        logo["text"] = (p.get("name", "?")[:2]).upper()

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


if __name__ == "__main__":
    main()
