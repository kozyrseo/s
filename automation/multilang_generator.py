"""
KOZYR — Оркестратор мульти-языковой генерации.

Точка входа для генерации одной темы во ВСЕХ языках её страны.
Читает из Google Sheets (или из файла темы) поля country и langs,
координирует generate.py + translator.py + отправку единого TG-превью.

Пайплайн:
  1. Определить страну и языки:
       country = "ua"
       langs   = resolve_langs_for_country("ua", override=langs_from_sheet)
              = ["ru", "uk"]           # первый = primary
  2. Сгенерировать статью на primary_language через generate.py (как раньше)
  3. Для каждого не-primary языка — перевести через translator.py
  4. Собрать ЕДИНОЕ превью в TG:
     - Одна карточка с hero-картинкой
     - Обе версии заголовков/summary
     - Кнопки:  ✅ Опубликовать обе  ·  📄 Полный текст RU
               🧾 Исходники        ·  📄 Полный текст UK
               ✏️ Правка RU        ·  ✏️ Правка UK
               🔄 Перегенерить     ·  ❌ Отклонить

Запуск:
  python multilang_generator.py --country ua                      # из Sheets
  python multilang_generator.py --country ua --langs ru,uk        # override
  python multilang_generator.py --topic-file topic.json --country ua

Или (для перевода уже опубликованной статьи, если понадобится):
  python multilang_generator.py --translate-existing --source-slug foo \\
                                --source-lang ru --target-lang uk
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from lang_config import get_cfg, validate_cfg_files_exist
from country_config import (
    get_country,
    resolve_langs_for_country,
    describe_country,
    is_primary_language,
)


# Импортируем из существующих модулей — мы их не переписываем,
# просто дёргаем сверху.
sys.path.insert(0, str(Path(__file__).parent))


def escape_md(s: str) -> str:
    if not s:
        return ""
    out = str(s)
    for ch in ("\\", "_", "*", "`"):
        out = out.replace(ch, "\\" + ch)
    return out


def generate_primary_article(topic: dict, primary_lang: str,
                              lang_cfg, force: bool = False) -> tuple[Path, str, dict]:
    """
    Обёртка над generate.generate_article + save_article из существующего
    generate.py. Возвращает (target_dir, slug, article_dict).
    """
    # Ленивый импорт — иначе циклы при import из workflow'ов.
    from generate import (
        generate_article,
        save_article,
        has_article_generated_today,
        evaluate_article,
    )
    from generate import HERO_FILENAME  # хвостовые константы

    if not force and has_article_generated_today(lang_cfg["pending_dir"]):
        print(f"ℹ️  Сегодня уже есть pending для lang={primary_lang!r}. "
              f"Пропускаю (используй --force чтобы перезаписать).")
        return None, None, None

    print(f"📝 Генерирую primary статью (lang={primary_lang})...")
    article = generate_article(topic, lang_cfg)
    target_dir, slug = save_article(article, topic, lang_cfg, primary_lang)

    # Сохраняем ПОЛНЫЙ article.json — понадобится translator.py
    (target_dir / "article.json").write_text(
        json.dumps(article, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✅ Primary готова: {target_dir}")
    return target_dir, slug, article


def translate_to_all_langs(source_dir: Path, source_lang: str,
                            other_langs: list[str], slug: str) -> list[dict]:
    """
    Для каждого языка из other_langs делает translator.translate_article.
    Возвращает список результатов (по одному на язык).
    """
    from translator import translate_article

    results = []
    for target_lang in other_langs:
        print(f"\n🌐 Перевожу на {target_lang}...")
        try:
            result = translate_article(
                source_pending_dir=source_dir,
                source_lang=source_lang,
                target_lang=target_lang,
                slug=slug,
            )
            results.append(result)
        except Exception as e:
            # Не роняем весь пайплайн — primary уже готов, оператор
            # может допереводить позже через /translate slug lang.
            print(f"⚠️  Перевод {source_lang}→{target_lang} упал: "
                  f"{type(e).__name__}: {e}")
            results.append({"error": str(e), "target_lang": target_lang})
    return results


def copy_hero_to_translations(source_dir: Path, translations: list[dict]) -> None:
    """
    Первичная статья содержит hero.webp. Копируем её во все переводы,
    чтобы meta.json.has_hero_image был правдой и превью работали. Это НЕ
    дубликат генерации картинки (её мы делаем один раз) — просто копия
    файла, чтобы папка _pending_uk/{slug}/ содержала всё для рендера.
    """
    from generate import HERO_FILENAME
    from image_gen import HERO_OG_FILENAME

    hero_src = source_dir / HERO_FILENAME
    if not hero_src.exists():
        print(f"ℹ️  Hero-картинка отсутствует в {source_dir}, "
              f"переводы будут без картинки (нестрашно, publish.py обработает).")
        return

    hero_jpg_src = source_dir / HERO_OG_FILENAME
    for t in translations:
        if t.get("error"):
            continue
        target_dir = t.get("target_dir")
        if target_dir and Path(target_dir).exists():
            shutil.copy2(hero_src, Path(target_dir) / HERO_FILENAME)
            # copy the JPEG OG-copy too, so og:image works for translations
            if hero_jpg_src.exists():
                shutil.copy2(hero_jpg_src, Path(target_dir) / HERO_OG_FILENAME)
            print(f"🖼️  Hero скопирована в {target_dir}")


def build_multilang_telegram_preview(
    primary_article: dict,
    primary_slug: str,
    primary_lang: str,
    translations: list[dict],
    lang_cfgs: dict,
    country_code: str,
    quality_block: str = "",
) -> tuple[str, list[list[dict]]]:
    """
    Собирает единое превью для всех языков в одном сообщении + расширенную
    клавиатуру, где действия дифференцированы по языку.
    """
    from generate import GITHUB_REPO, GITHUB_BRANCH

    country_cfg = get_country(country_code)
    flag = country_cfg["flag"]
    country_name = country_cfg["name"]

    # Заголовки для каждой версии
    langs_info = []

    # Primary
    primary_rp = primary_article.get("russian_preview", {}) or {}
    langs_info.append({
        "lang": primary_lang,
        "title": primary_rp.get("title_ru") or primary_article.get("h1_title", ""),
        "summary": primary_rp.get("summary_ru", "")[:400],
        "word_count": primary_article.get("word_count", "?"),
        "pending_dir": lang_cfgs[primary_lang]["pending_dir"].name,
        "slug": primary_slug,
    })

    # Переводы
    for t in translations:
        if t.get("error"):
            langs_info.append({
                "lang": t.get("target_lang", "?"),
                "error": t["error"][:200],
            })
            continue
        art = t.get("article", {})
        rp = art.get("russian_preview", {}) or {}
        target_lang = None
        # определим lang по имени папки
        target_dir = Path(t["target_dir"])
        for lang, cfg in lang_cfgs.items():
            if cfg["pending_dir"].name == target_dir.parent.name:
                target_lang = lang
                break
        target_lang = target_lang or "?"
        langs_info.append({
            "lang": target_lang,
            "title": rp.get("title_ru") or art.get("h1_title", ""),
            "summary": rp.get("summary_ru", "")[:400],
            "word_count": t.get("meta", {}).get("word_count", "?"),
            "pending_dir": lang_cfgs[target_lang]["pending_dir"].name,
            "slug": t["slug"],
        })

    # Собираем текст
    lines = [f"📝 *Новая статья на ревью* {flag} `{country_name}`"]
    if quality_block:
        lines.append("")
        lines.append(quality_block)

    for info in langs_info:
        lines.append("")
        lang = info["lang"]
        lang_flag = _lang_flag(lang)
        lines.append(f"─── {lang_flag} *{lang.upper()}* ───")
        if info.get("error"):
            lines.append(f"❌ Ошибка перевода: `{escape_md(info['error'])}`")
            continue
        lines.append(f"*{escape_md(info['title'])}*")
        lines.append(f"📊 {info['word_count']} слов")
        if info.get("summary"):
            lines.append("")
            lines.append(escape_md(info["summary"]))

        # GitHub-ссылки на файлы
        pending_name = info["pending_dir"]
        slug = info["slug"]
        body_link = (f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/"
                     f"{pending_name}/{slug}/body.md")
        lines.append(f"🔗 [Полный текст]({body_link})")

    text = "\n".join(lines)

    # Клавиатура: главная кнопка + доп-кнопки под ней
    # (пожелание оператора: одна большая + точечные ниже)
    kb: list[list[dict]] = []

    # Ряд 1: ГЛАВНАЯ кнопка — публикация всех языков
    kb.append([
        {"text": "✅ Опубликовать все языки",
         "callback_data": f"publish_all:{primary_slug}"},
    ])

    # Ряд 2: точечная публикация каждого языка отдельно
    # (полезно если один перевод пока не готов или требует ручной правки)
    single_publish_row = []
    for info in langs_info:
        if info.get("error"):
            continue
        lang = info["lang"]
        lang_flag = _lang_flag(lang)
        single_publish_row.append({
            "text": f"🚀 Только {lang_flag} {lang.upper()}",
            "callback_data": f"publish_lang:{lang}:{primary_slug}",
        })
    if len(single_publish_row) > 1:
        # Показываем эту строку только если языков ≥ 2 (иначе бессмысленно)
        kb.append(single_publish_row)

    # Ряд 3: полный текст по каждому языку
    fulltext_row = []
    for info in langs_info:
        if info.get("error"):
            continue
        lang = info["lang"]
        lang_flag = _lang_flag(lang)
        fulltext_row.append({
            "text": f"📄 {lang_flag} Текст",
            "callback_data": f"fulltext_lang:{lang}:{primary_slug}",
        })
    if fulltext_row:
        kb.append(fulltext_row)

    # Ряд 4: правка по каждому языку
    edit_row = []
    for info in langs_info:
        if info.get("error"):
            continue
        lang = info["lang"]
        lang_flag = _lang_flag(lang)
        edit_row.append({
            "text": f"✏️ {lang_flag} Правка",
            "callback_data": f"edit_menu_lang:{lang}:{primary_slug}",
        })
    if edit_row:
        kb.append(edit_row)

    # Ряд 5: исходники и регенерация
    kb.append([
        {"text": "🧾 Исходники", "callback_data": f"sources:{primary_slug}"},
        {"text": "🔄 Перегенерить", "callback_data": f"regenerate:{primary_slug}"},
    ])

    # Ряд 6: отклонение
    kb.append([
        {"text": "❌ Отклонить всё", "callback_data": f"reject_all:{primary_slug}"},
    ])

    return text, kb


def _lang_flag(lang: str) -> str:
    return {"ru": "🇷🇺", "uk": "🇺🇦", "pl": "🇵🇱", "kk": "🇰🇿", "en": "🇬🇧"}.get(lang, "")


def send_multilang_preview(text: str, keyboard: list, primary_target_dir: Path) -> None:
    """Один вызов send_telegram_preview из generate.py — та же логика,
    просто клавиатура шире и текст длиннее."""
    from generate import send_telegram_preview, HERO_FILENAME, TELEGRAM_ENABLED

    if not TELEGRAM_ENABLED:
        print("ℹ️  Telegram выключен, превью не отправляем.")
        return

    hero = primary_target_dir / HERO_FILENAME
    send_telegram_preview(
        text=text,
        keyboard=keyboard,
        photo_path=hero if hero.exists() else None,
    )


# ==== Google Sheets — расширенное чтение с учётом country/langs ====

def get_next_multilang_topic(sheet, country_filter: str | None = None) -> tuple[int, dict] | None:
    """
    Расширенный аналог get_next_queued_topic из generate.py. Читает Sheets,
    находит первую строку со status=queued, учитывая колонки country и langs.

    Возвращает: (row_index, topic_dict) или None если очередь пуста.

    Формат topic_dict:
      {
        "country": "ua",
        "langs":   ["ru", "uk"],   # уже resolve'нутые
        "topic":   "...",
        "primary_keyword": "...",
        ...
      }
    """
    from gspread.exceptions import APIError

    try:
        records = sheet.get_all_records()
    except APIError as e:
        print(f"⚠️  Не удалось прочитать Sheets: {e}")
        return None

    for idx, row in enumerate(records, start=2):
        if str(row.get("status", "")).strip().lower() != "queued":
            continue

        country = str(row.get("country", "")).strip().lower()
        if not country:
            # Обратная совместимость: если country не задан, но задан lang=ru —
            # трактуем как country=ua (это старые темы до мультиязычности).
            legacy_lang = str(row.get("lang", "")).strip().lower()
            if legacy_lang == "ru":
                country = "ua"
            else:
                print(f"⏭️  Строка {idx}: нет country, пропускаю")
                continue

        if country_filter and country != country_filter.lower():
            continue

        try:
            langs_override = str(row.get("langs", "")).strip()
            langs = resolve_langs_for_country(country, override=langs_override)
        except ValueError as e:
            print(f"⏭️  Строка {idx}: {e}")
            continue

        topic = dict(row)
        topic["country"] = country
        topic["langs"] = langs
        topic["_source_row"] = idx
        return idx, topic

    return None


# ==== Основной пайплайн ====

def run_multilang_pipeline(topic: dict, force: bool = False) -> dict:
    """
    Полный пайплайн: генерация primary + переводы + превью в TG.
    Возвращает сводку.
    """
    country_code = topic["country"]
    langs = topic["langs"]
    if not langs:
        raise ValueError(f"topic не содержит языков (country={country_code})")

    primary_lang = langs[0]
    other_langs = langs[1:]

    print(f"\n=== Мультиязычная генерация: {describe_country(country_code)} ===")
    print(f"Primary: {primary_lang}, others: {other_langs or 'нет'}")
    print(f"Тема: {topic.get('topic', '')!r}\n")

    # 1. Валидируем конфиги для всех языков
    for lang in langs:
        validate_cfg_files_exist(lang)

    lang_cfgs = {lang: get_cfg(lang) for lang in langs}

    # 2. Генерим primary
    result = generate_primary_article(
        topic=topic,
        primary_lang=primary_lang,
        lang_cfg=lang_cfgs[primary_lang],
        force=force,
    )
    if result == (None, None, None):
        return {"status": "skipped", "reason": "already generated today"}
    primary_dir, primary_slug, primary_article = result

    # 3. Переводы
    translations = []
    if other_langs:
        translations = translate_to_all_langs(
            source_dir=primary_dir,
            source_lang=primary_lang,
            other_langs=other_langs,
            slug=primary_slug,
        )
        # 4. Копируем hero-картинку в переводы
        copy_hero_to_translations(primary_dir, translations)

    # 5. Quality-check (только для primary, переводы наследуют)
    quality_block = ""
    try:
        from generate import evaluate_article
        print("\n📊 Оцениваю качество primary...")
        quality = evaluate_article(
            primary_article, topic,
            primary_article.get("markdown_body", ""),
            lang=primary_lang,
        )
        print(f"✅ Quality: {quality['total']}/100 — {quality['verdict']}")
        quality_block = quality.get("telegram_block", "")
        (primary_dir / "quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"⚠️  Quality-check упал: {e}")

    # 6. Отправляем единое превью
    try:
        text, keyboard = build_multilang_telegram_preview(
            primary_article=primary_article,
            primary_slug=primary_slug,
            primary_lang=primary_lang,
            translations=translations,
            lang_cfgs=lang_cfgs,
            country_code=country_code,
            quality_block=quality_block,
        )
        send_multilang_preview(text, keyboard, primary_dir)
    except Exception as e:
        print(f"⚠️  Отправка превью упала: {type(e).__name__}: {e}")

    return {
        "status": "ok",
        "country": country_code,
        "primary_lang": primary_lang,
        "primary_slug": primary_slug,
        "translations": [
            {"lang": t.get("target_lang") or _detect_lang_from_dir(t),
             "slug": t.get("slug"),
             "error": t.get("error")}
            for t in translations
        ],
    }


def _detect_lang_from_dir(t: dict) -> str:
    target_dir = t.get("target_dir")
    if not target_dir:
        return "?"
    parent_name = Path(target_dir).parent.name  # _pending_uk
    if parent_name.startswith("_pending_"):
        return parent_name[len("_pending_"):]
    return "?"


# ==== CLI ====

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Мультиязычная генерация одной темы"
    )
    parser.add_argument("--country", default=None,
                        help="Фильтр по стране (ua/pl/kz…). Обязателен если не задан --topic-file.")
    parser.add_argument("--langs", default="",
                        help="Override языков через запятую (например 'ru,uk' или 'ru'). "
                             "Если пусто — берутся все языки страны.")
    parser.add_argument("--topic-file", default=None,
                        help="Локальный режим: путь к JSON-файлу с темой "
                             "(без обращения к Sheets). Обязательные поля: "
                             "topic, primary_keyword, country. Можно указать langs.")
    parser.add_argument("--force", action="store_true",
                        help="Генерировать даже если сегодня уже была статья")
    args = parser.parse_args()

    # Локальный режим — тема из файла
    if args.topic_file:
        topic = json.loads(Path(args.topic_file).read_text(encoding="utf-8"))
        country = args.country or topic.get("country")
        if not country:
            # Обратная совместимость: старые темы без country, но с lang=ru —
            # трактуем как Украину (тот же fallback, что в get_next_multilang_topic).
            legacy_lang = str(topic.get("lang", "")).strip().lower()
            if legacy_lang == "ru":
                country = "ua"
        if not country:
            print("❌ --topic-file без поля country и без --country "
                  "(и нет legacy lang=ru)", file=sys.stderr)
            return 1
        langs_override = args.langs or topic.get("langs", "")
        if isinstance(langs_override, list):
            langs_override = ",".join(langs_override)
        topic["country"] = country
        topic["langs"] = resolve_langs_for_country(country, override=langs_override)
        print(f"Локальный режим. Тема: {topic.get('topic')!r}")
        result = run_multilang_pipeline(topic, force=args.force)
        print(f"\n=== Готово ===\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0

    # Режим из Sheets
    from generate import get_sheet, update_status

    sheet = get_sheet()
    next_topic = get_next_multilang_topic(sheet, country_filter=args.country)
    if not next_topic:
        filter_msg = f" (country={args.country})" if args.country else ""
        print(f"📭 Очередь пуста{filter_msg}. Нечего делать.")
        return 0

    row_index, topic = next_topic
    if args.langs:
        topic["langs"] = resolve_langs_for_country(topic["country"], override=args.langs)
    print(f"Взял строку {row_index}: {topic.get('topic')!r}")

    update_status(sheet, row_index, "generating")
    try:
        result = run_multilang_pipeline(topic, force=args.force)
        update_status(sheet, row_index, "pending_review")
        print(f"\n=== Готово ===\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0
    except Exception as e:
        update_status(sheet, row_index, "queued")
        print(f"\n❌ Пайплайн упал, вернул строку в queued: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
