"""
KOZYR bot v2 — патч для automation/generate.py

Что меняем:
  1. build_telegram_preview() — расширяем inline-клавиатуру:
       ✅ Опубликовать · 📄 Полный текст
       🧾 Исходники   · ✏️ Правка
       🔄 Перегенерить · ❌ Отклонить

  2. save_article() — если тема пришла с полем _source_row (из
     generate-from-row.yml), при удачной генерации переводим строку
     в таблице в status=done. Если тема из локального файла или
     Google Sheets flow — работает как раньше.

Как применить:
  cd automation
  python -m bot_v2.apply_patches           # автоматом заменит функции
  # или вручную: сравнить с текущим generate.py и вклеить нужные куски

Патч написан идемпотентно: повторный запуск ничего не сломает.
"""

# ---------------------------------------------------------------------------
# ЗАМЕНА для build_telegram_preview() — расширенная клавиатура + row_hint
# ---------------------------------------------------------------------------

BUILD_TELEGRAM_PREVIEW_NEW = '''
def build_telegram_preview(article: dict, topic: dict, slug: str, lang_cfg, lang: str,
                          quality_block: str = "") -> tuple[str, list]:
    """Build Markdown preview text + inline keyboard for Telegram (bot v2)."""
    rp = _normalize_russian_preview(article.get("russian_preview"), article)
    title_ru = rp.get("title_ru", article["h1_title"])
    summary_ru = rp.get("summary_ru", "")
    h2_translations = [it for it in rp.get("h2_translations", []) if isinstance(it, dict)]

    word_count = article.get("word_count", "?")
    primary_kw = topic.get("primary_keyword", "")
    target_page = topic.get("target_page", "")
    intent = topic.get("intent", "")

    h2_lines = []
    for item in h2_translations[:6]:
        ru = item.get("ru", "")
        if ru:
            h2_lines.append(f"• {ru}")
    h2_block = "\\n".join(h2_lines) if h2_lines else "_нет_"

    if len(summary_ru) > 700:
        summary_ru = summary_ru[:697] + "..."

    pending_name = lang_cfg["pending_dir"].name
    body_md_link = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{pending_name}/{slug}/body.md"
    preview_md_link = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{pending_name}/{slug}/preview.md"

    lang_flag = {"ru": "🇺🇦"}.get(lang, "🇺🇦")

    quality_prefix = ""
    if quality_block:
        quality_prefix = f"\\n{quality_block}\\n"

    # Bot v2: если тема пришла из строки таблицы, добавляем ссылку на источник.
    source_row = topic.get("_source_row")
    row_hint = f"\\n📊 Источник: строка *{source_row}* в Google Sheets" if source_row else ""

    text = f"""📝 *Новая статья на ревью* {lang_flag} `{lang}`
{quality_prefix}
*{escape_md(title_ru)}*
 
📊 {word_count} слов · {escape_md(intent)} · `{escape_md(target_page)}`
🎯 Primary: {escape_md(primary_kw)}{row_hint}
 
*О чём статья:*
{escape_md(summary_ru)}
 
*Структура:*
{escape_md(h2_block)}
 
🔗 [Полный текст]({body_md_link}) · [Превью]({preview_md_link})"""

    # ==== Bot v2: расширенная клавиатура ====
    # Первый ряд — публикация/просмотр
    # Второй ряд — исходники/правка (v2 features)
    # Третий ряд — регенерация/отклонение
    keyboard = [
        [
            {"text": "✅ Опубликовать", "callback_data": f"publish:{slug}"},
            {"text": "📄 Полный текст", "callback_data": f"fulltext:{slug}"},
        ],
        [
            {"text": "🧾 Исходники", "callback_data": f"sources:{slug}"},
            {"text": "✏️ Правка",   "callback_data": f"edit_menu:{slug}"},
        ],
        [
            {"text": "🔄 Перегенерить", "callback_data": f"regenerate:{slug}"},
            {"text": "❌ Отклонить",   "callback_data": f"reject:{slug}"},
        ],
    ]
    return text, keyboard
'''

# ---------------------------------------------------------------------------
# ДОБАВКА в save_article() — сохраняем _source_row в meta.json
# ---------------------------------------------------------------------------
# В блоке `meta = { ... }` в save_article нужно добавить строку:
#   "source_row": topic.get("_source_row"),
# после generated_at. Это нужно, чтобы potом можно было
# автоматически проставить status=done в таблице.


# ---------------------------------------------------------------------------
# ЗАМЕНА для publish.py: после успешной публикации если есть source_row
# — переводим строку в status=done.
# ---------------------------------------------------------------------------
PUBLISH_ROW_DONE_HOOK = '''
def _mark_source_row_done(meta: dict) -> None:
    """Bot v2: если статья пришла через generate-from-row.yml,
    в meta лежит source_row — после публикации переводим строку в done."""
    row = meta.get("source_row")
    if not row:
        return
    try:
        from bot_v2.suggested_topics import update_status
        update_status(int(row), "done")
        print(f"✅ Строка {row} в Sheets → done")
    except Exception as e:
        print(f"⚠️  Не удалось обновить строку {row} в Sheets: {e}")
'''
