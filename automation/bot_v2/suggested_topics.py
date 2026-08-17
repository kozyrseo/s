"""
KOZYR bot v2 — операции со списком suggested тем в Google Sheets.

Что делает:
- list_suggested()  → показывает N предложенных тем со всеми полями
- approve_topic()   → status=suggested → queued (генератор возьмёт)
- reject_topic()    → status=rejected
- edit_topic()      → правит поля темы прямо в таблице
- get_topic_by_row() → достаёт строку по номеру

Используется:
- generate.py при запуске через кнопку «⚡ Сгенерировать сейчас» (для выбранной темы)
- Cloudflare Worker при обработке команд /suggested, /approve, /reject
- CLI: python -m automation.bot_v2.suggested_topics list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import gspread
from google.oauth2.service_account import Credentials


# ==== Google Sheets ====

def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(sheet_id).sheet1


def _headers(sheet) -> list[str]:
    return sheet.row_values(1)


def _col_index(headers: list[str], name: str) -> int:
    """1-based номер колонки. -1 если нет."""
    if name not in headers:
        return -1
    return headers.index(name) + 1


# ==== Операции ====

def list_by_status(status: str = "suggested", lang: str | None = None,
                     country: str | None = None, limit: int = 20) -> list[dict]:
    """Возвращает строки с заданным status. Каждая строка — dict со всеми
    полями + служебное поле `_row` (номер в таблице, начиная с 2).

    Фильтры:
      lang    — legacy-фильтр (сработает если в строке есть колонка `lang`)
      country — v2 multilang: колонка `country` (ua/pl/kz)
    """
    sheet = get_sheet()
    records = sheet.get_all_records()
    out = []
    for idx, row in enumerate(records, start=2):
        if str(row.get("status", "")).strip().lower() != status:
            continue
        if lang and str(row.get("lang", "")).strip().lower() != lang:
            continue
        if country and str(row.get("country", "")).strip().lower() != country:
            continue
        row_copy = dict(row)
        row_copy["_row"] = idx
        out.append(row_copy)
        if len(out) >= limit:
            break
    return out


def get_topic_by_row(row_index: int) -> dict | None:
    """По номеру строки (2-based) возвращает все поля темы."""
    sheet = get_sheet()
    headers = _headers(sheet)
    if row_index < 2:
        return None
    try:
        values = sheet.row_values(row_index)
    except gspread.exceptions.APIError:
        return None
    if not values:
        return None
    topic = {}
    for i, header in enumerate(headers):
        topic[header] = values[i] if i < len(values) else ""
    topic["_row"] = row_index
    return topic


def update_status(row_index: int, new_status: str) -> bool:
    """Меняет статус в конкретной строке. Возвращает True если ок."""
    sheet = get_sheet()
    headers = _headers(sheet)
    col = _col_index(headers, "status")
    if col == -1:
        raise RuntimeError("В таблице нет колонки 'status'")
    sheet.update_cell(row_index, col, new_status)
    return True


def update_field(row_index: int, field: str, value: str) -> bool:
    """Правит одно поле темы в таблице."""
    sheet = get_sheet()
    headers = _headers(sheet)
    col = _col_index(headers, field)
    if col == -1:
        raise RuntimeError(f"В таблице нет колонки '{field}'. Есть: {headers}")
    sheet.update_cell(row_index, col, value)
    return True


def approve_topic(row_index: int) -> dict:
    """Suggested → queued. Возвращает обновлённую строку."""
    update_status(row_index, "queued")
    return get_topic_by_row(row_index) or {}


def reject_topic(row_index: int) -> dict:
    update_status(row_index, "rejected")
    return get_topic_by_row(row_index) or {}


def cleanup_suggested(dry_run: bool = True,
                      only_bot_topics: bool = True) -> dict:
    """
    Массовая чистка: проходит все suggested-темы и переводит в rejected те,
    что не проходят стоп-фильтр (чужие бренды / регуляторика / неверный
    рейкбек). Использует ЕДИНУЮ проверку screen_topic из partners_config —
    ту же, что фильтрует новые темы на входе.

    Параметры:
      dry_run        — если True, НЕ трогает таблицу, только возвращает
                       список того, что было бы отклонено (для превью).
      only_bot_topics — если True, чистит только темы, которые НЕ помечены
                       как ручные. То есть тема с source != 'keyword_research'
                       (и непустым source) считается заведённой оператором
                       и НЕ трогается. Темы с пустым source (legacy-мусор от
                       бота) — чистятся. Это защищает ручные правки оператора.

    Возвращает dict:
      {
        "checked": N,            # сколько suggested-тем проверено
        "rejected": [...],       # список {row, topic, reason}
        "kept": M,               # сколько осталось suggested
        "skipped_manual": K,     # сколько пропущено как ручные
        "dry_run": bool,
      }
    """
    # Импорт фильтра. Пробуем несколько путей, т.к. модуль может лежать
    # в automation/ (обычно) — а этот файл в automation/bot_v2/.
    screen_topic = None
    load_partners = None
    for import_path in ("partners_config", "automation.partners_config"):
        try:
            mod = __import__(import_path, fromlist=["screen_topic", "load_partners"])
            screen_topic = mod.screen_topic
            load_partners = mod.load_partners
            break
        except Exception:
            continue
    if screen_topic is None:
        # последний шанс — добавить родительскую папку в sys.path
        import os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        try:
            from partners_config import screen_topic, load_partners
        except Exception as e:
            raise RuntimeError(
                f"Не удалось импортировать partners_config для чистки: {e}"
            )

    partners = load_partners()
    if not partners:
        print("⚠️  partners.js не прочитан — чистка небезопасна, останавливаюсь")
        return {"checked": 0, "rejected": [], "kept": 0,
                "skipped_manual": 0, "dry_run": dry_run, "error": "no_partners"}

    sheet = get_sheet()
    records = sheet.get_all_records()
    headers = _headers(sheet)
    status_col = _col_index(headers, "status")
    if status_col == -1:
        raise RuntimeError("В таблице нет колонки 'status'")

    rejected = []
    kept = 0
    skipped_manual = 0
    checked = 0

    for idx, row in enumerate(records, start=2):
        if str(row.get("status", "")).strip().lower() != "suggested":
            continue

        # Защита ручных тем: если source задан и это НЕ keyword_research —
        # тема заведена/отредактирована оператором, не трогаем.
        src = str(row.get("source", "")).strip().lower()
        if only_bot_topics and src and src != "keyword_research":
            skipped_manual += 1
            continue

        checked += 1
        reason = screen_topic(row, partners)
        if reason:
            rejected.append({
                "row": idx,
                "topic": row.get("topic", ""),
                "reason": reason,
            })
        else:
            kept += 1

    # Применяем (если не dry-run). Идём СНИЗУ ВВЕРХ по номерам строк на
    # случай будущих операций удаления — для update_cell порядок не важен,
    # но так безопаснее и консистентнее.
    if not dry_run and rejected:
        for item in sorted(rejected, key=lambda x: x["row"], reverse=True):
            sheet.update_cell(item["row"], status_col, "rejected")

    return {
        "checked": checked,
        "rejected": rejected,
        "kept": kept,
        "skipped_manual": skipped_manual,
        "dry_run": dry_run,
    }


def approve_and_dump_topic_file(row_index: int, target_path: str) -> str:
    """
    Помечает status=processing и записывает JSON-файл темы для generate.py.
    Используется когда пользователь нажимает "⚡ Сгенерировать сейчас"
    в TG-боте — Worker дёргает Actions с этим topic_file.
    """
    topic = get_topic_by_row(row_index)
    if not topic:
        raise RuntimeError(f"Строка {row_index} не найдена")
    update_status(row_index, "processing")

    dump = {
        "topic": topic.get("topic", ""),
        "primary_keyword": topic.get("primary_keyword", ""),
        "secondary_keywords": topic.get("secondary_keywords", ""),
        "intent": topic.get("intent", "informational"),
        "target_page": topic.get("target_page", "/ua/"),
        "notes": topic.get("notes", ""),
        # v2 multilang: колонки country и langs — если есть в строке,
        # передаются в multilang_generator. Если нет — legacy generate.py.
        "country": str(topic.get("country", "")).strip().lower() or None,
        "langs": str(topic.get("langs", "")).strip(),
        # Legacy lang (для обратной совместимости с одноязычными темами)
        "lang": str(topic.get("lang", "")).strip().lower() or "ru",
        # Оставляем _row чтобы generate.py потом мог перевести status обратно
        "_source_row": row_index,
    }
    # Убираем None-значения, чтобы JSON был чистым
    dump = {k: v for k, v in dump.items() if v is not None}
    from pathlib import Path
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ==== CLI ====

def main() -> None:
    parser = argparse.ArgumentParser(description="KOZYR suggested topics manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Показать список тем со статусом")
    p_list.add_argument("--status", default="suggested",
                        choices=["suggested", "queued", "processing", "done", "rejected"])
    p_list.add_argument("--lang", default=None)
    p_list.add_argument("--limit", type=int, default=20)

    p_get = sub.add_parser("get", help="Показать строку целиком по номеру")
    p_get.add_argument("row", type=int)

    p_approve = sub.add_parser("approve", help="Suggested → queued")
    p_approve.add_argument("row", type=int)

    p_reject = sub.add_parser("reject", help="Пометить как rejected")
    p_reject.add_argument("row", type=int)

    p_edit = sub.add_parser("edit", help="Изменить одно поле")
    p_edit.add_argument("row", type=int)
    p_edit.add_argument("field")
    p_edit.add_argument("value")

    p_dump = sub.add_parser("dump", help="approve + сохранить JSON-тему для generate.py")
    p_dump.add_argument("row", type=int)
    p_dump.add_argument("target", help="Куда положить файл, e.g. automation/topics/foo.json")

    p_clean = sub.add_parser("cleanup",
        help="Массово отклонить suggested-темы, не проходящие стоп-фильтр")
    p_clean.add_argument("--apply", action="store_true",
        help="Реально применить (без флага — только показать, dry-run)")
    p_clean.add_argument("--include-manual", action="store_true",
        help="Чистить и ручные темы тоже (по умолчанию ручные не трогаются)")

    args = parser.parse_args()

    try:
        if args.cmd == "list":
            rows = list_by_status(status=args.status, lang=args.lang, limit=args.limit)
            for r in rows:
                print(f"[row {r['_row']}] {r.get('topic', '')}")
                print(f"     🎯 {r.get('primary_keyword', '')}")
                print(f"     status={r.get('status')} · lang={r.get('lang')} · "
                      f"intent={r.get('intent')} · target={r.get('target_page')}")
                if r.get("evidence"):
                    print(f"     💡 {r['evidence']}")
                print()
        elif args.cmd == "get":
            t = get_topic_by_row(args.row)
            if not t:
                print(f"Строка {args.row} не найдена")
                sys.exit(1)
            print(json.dumps(t, indent=2, ensure_ascii=False))
        elif args.cmd == "approve":
            t = approve_topic(args.row)
            print(f"✅ Строка {args.row} → queued. {t.get('topic')}")
        elif args.cmd == "reject":
            t = reject_topic(args.row)
            print(f"❌ Строка {args.row} → rejected. {t.get('topic')}")
        elif args.cmd == "edit":
            update_field(args.row, args.field, args.value)
            print(f"✏️  Строка {args.row}: {args.field} = {args.value!r}")
        elif args.cmd == "dump":
            path = approve_and_dump_topic_file(args.row, args.target)
            print(f"✅ Тема выгружена в {path} (row {args.row} → processing)")
        elif args.cmd == "cleanup":
            result = cleanup_suggested(
                dry_run=not args.apply,
                only_bot_topics=not args.include_manual,
            )
            mode = "DRY-RUN (ничего не изменено)" if result["dry_run"] else "ПРИМЕНЕНО"
            print(f"🧹 Чистка suggested — {mode}")
            print(f"   Проверено: {result['checked']}")
            print(f"   Пропущено ручных: {result['skipped_manual']}")
            print(f"   Останется чистых: {result['kept']}")
            print(f"   {'Будет отклонено' if result['dry_run'] else 'Отклонено'}: "
                  f"{len(result['rejected'])}")
            print()
            for item in result["rejected"]:
                print(f"   ❌ row {item['row']}: {item['topic'][:55]}")
                print(f"        → {item['reason']}")
            if result["dry_run"] and result["rejected"]:
                print()
                print("   Чтобы применить: добавь флаг --apply")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
