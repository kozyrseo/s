"""
Generate article from next queued topic in Google Sheets.
 
Output format:
- JSON metadata block (short, easy to parse)
- Markdown article body between ---ARTICLE-MARKDOWN-START--- and ---ARTICLE-MARKDOWN-END--- markers
 
This separation prevents JSON parsing failures caused by long article text.
 
After saving the article, sends a Telegram preview message with inline action buttons.
"""
 
from __future__ import annotations
 
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
 
import gspread
from anthropic import Anthropic
from google.oauth2.service_account import Credentials
from slugify import slugify

from image_gen import generate_hero_image, hero_alt_text, HERO_FILENAME
from lang_config import get_cfg, validate_cfg_files_exist, TELEGRAM_ENABLED
from linking import (
    build_existing_articles_context,
    count_internal_links,
    extract_internal_links,
)
from quality_check import evaluate_article
 
 
# ==== Configuration ====
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 16000

# Note: PENDING_DIR and PROMPT_PATH used to be module-level constants.
# They are now derived from the --lang flag at runtime via lang_config.
# Functions that need them receive `lang_cfg` as an argument or read from
# meta passed in.
 
MARKDOWN_START = "---ARTICLE-MARKDOWN-START---"
MARKDOWN_END = "---ARTICLE-MARKDOWN-END---"
 
# GitHub repo for building links to body.md
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "seoptimz/z")
GITHUB_BRANCH = "main"
 
 
# ==== Google Sheets ====
 
def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(sheet_id).sheet1
 
 
def get_next_queued_topic(sheet, lang: str = "ru"):
    """Find the next queued topic for the given language.

    Stage 3 i18n: a `lang` column in the Sheet selects which queue this is.
    Behaviour:
      - Rows where lang == requested language are picked.
      - Rows with empty/missing `lang` cell are treated as 'en' (backward
        compat: existing rows queued before Stage 3 don't have this column,
        and they belong to the EN queue).
      - Rows with a non-matching lang are skipped.

    Note: `topic.get("translation_of")` from the same row is preserved
    through the pipeline so publish.py can emit hreflang-pair tags. The
    operator fills this column when planning a paired translation.
    """
    records = sheet.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("status", "")).strip().lower() != "queued":
            continue
        row_lang = str(row.get("lang", "")).strip().lower() or "ru"
        if row_lang != lang:
            continue
        return idx, row
    return None
 
 
def update_status(sheet, row_index: int, new_status: str) -> None:
    headers = sheet.row_values(1)
    status_col = headers.index("status") + 1
    sheet.update_cell(row_index, status_col, new_status)
 
 
# ==== Claude generation ====
 
def load_system_prompt(lang_cfg) -> str:
    return lang_cfg["system_prompt"].read_text(encoding="utf-8")
 
 
def build_user_message(topic: dict, lang_cfg) -> str:
    # Pass the language-specific taxonomy so existing-articles context is
    # drawn from the right pool (EN articles for EN runs, PT articles for PT).
    existing_articles_context = build_existing_articles_context(
        taxonomy_path=lang_cfg["taxonomy"],
        url_prefix=lang_cfg["url_prefix"],
    )
    return f"""Generate an article based on this topic.

**Topic:** {topic.get('topic', '')}
**Primary keyword:** {topic.get('primary_keyword', '')}
**Secondary keywords:** {topic.get('secondary_keywords', '')}
**Intent:** {topic.get('intent', 'informational')}
**Target service page:** {topic.get('target_page', '')}
**Special notes:** {topic.get('notes', '(none)')}

Use web_search to research current data and competitive content before writing.

---

{existing_articles_context}

---

INTERNAL LINKING REMINDER (the system prompt covers this in detail):
- Place 3 to 5 internal links inline in the body
- ONE link to the format page matching `target_page` above
- At least TWO links to articles from the EXISTING ARTICLES list above
- Use only slugs and URLs from the lists above — do not invent URLs
- Spread links across at least two H2 sections
- Add a `tags` field in the JSON metadata using the controlled vocabulary

---

OUTPUT FORMAT (must follow exactly):
1. Start your response with `{{` (a JSON object).
2. After the JSON closes with `}}`, on the next line write the literal marker: ---ARTICLE-MARKDOWN-START---
3. Write the article body as plain Markdown (no escaping needed, write naturally).
4. End with the literal marker: ---ARTICLE-MARKDOWN-END---
5. Nothing else after that marker.

NO preamble before the JSON. NO code fences. NO explanations."""
 
 
def parse_response(raw_text: str) -> tuple[dict, str]:
    """
    Parse Claude's response into (metadata_dict, markdown_body).
    Expects format: JSON object, then ---ARTICLE-MARKDOWN-START---, markdown, ---ARTICLE-MARKDOWN-END---.
    """
    text = raw_text.strip()
 
    # Remove possible code fences (just in case Claude wraps things)
    if "```json" in text and text.lstrip().startswith("```"):
        idx = text.find("```json")
        rest = text[idx + len("```json"):]
        end_fence = rest.find("```")
        if end_fence != -1:
            text = rest[:end_fence] + rest[end_fence + 3:]
    elif text.startswith("```"):
        text = text[3:].lstrip("\n")
        if "```" in text:
            text = text.replace("```", "", 1)
 
    if MARKDOWN_START not in text or MARKDOWN_END not in text:
        raise RuntimeError(
            f"Response missing markdown markers. "
            f"Has START: {MARKDOWN_START in text}, "
            f"Has END: {MARKDOWN_END in text}"
        )
 
    json_part, rest = text.split(MARKDOWN_START, 1)
    markdown_part, _ = rest.split(MARKDOWN_END, 1)
 
    json_part = json_part.strip()
    first_brace = json_part.find("{")
    last_brace = json_part.rfind("}")
    if first_brace == -1 or last_brace == -1:
        raise RuntimeError("No JSON object found in metadata section")
    json_str = json_part[first_brace : last_brace + 1]
 
    try:
        metadata = json.loads(json_str)
    except json.JSONDecodeError as e:
        print("--- JSON parse failed. Raw JSON candidate (first 3000 chars) ---")
        print(json_str[:3000])
        raise RuntimeError(f"Invalid JSON in metadata: {e}")
 
    markdown_body = markdown_part.strip()
    return metadata, markdown_body
 
 
def clean_citation_artifacts(text: str) -> str:
    """Remove citation markers and fix whitespace artifacts left behind."""
    # Remove citation/reference HTML tags
    text = re.sub(r"<cite[^>]*>(.*?)</cite>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"</?cite[^>]*>", "", text)
    text = re.sub(r"<sup[^>]*>.*?</sup>", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\d+\]", "", text)  # remove [1], [2] markers
 
    # Fix orphan whitespace/newlines before punctuation
    # (artifact of removed inline citations like "<cite>...</cite>, and...")
    text = re.sub(r"[ \t\n]+([,.;:!?])", r"\1", text)
 
    # Fix orphan newlines that broke sentences mid-paragraph.
    # Heuristic: a newline followed by lowercase letter is a continuation,
    # not a paragraph break — join it with a single space.
    text = re.sub(r"(\S)\n([a-z])", r"\1 \2", text)
 
    # Collapse triple+ newlines into double (paragraph separators)
    text = re.sub(r"\n{3,}", "\n\n", text)
 
    return text
 
 
def generate_article(topic: dict, lang_cfg) -> dict:
    """Send request to Claude and return dict with metadata + markdown_body."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system_prompt = load_system_prompt(lang_cfg)
    user_message = build_user_message(topic, lang_cfg)
 
    print(f"Generating article for topic: {topic.get('topic')!r}")
    print(f"Model: {MODEL}, max_tokens: {MAX_TOKENS}")
 
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": user_message}],
    )
 
    text_parts = []
    for block in response.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
    raw_text = "\n".join(text_parts).strip()
 
    try:
        metadata, markdown_body = parse_response(raw_text)
    except RuntimeError:
        print("--- Raw response (first 4000 chars) ---")
        print(raw_text[:4000])
        print("--- Raw response (last 2000 chars) ---")
        print(raw_text[-2000:])
        raise
 
    # Clean any citation artifacts
    markdown_body = clean_citation_artifacts(markdown_body)
 
    # Validate required metadata fields
    required = {"slug", "meta_title", "meta_description", "h1_title",
                "faq", "russian_preview"}
    missing = required - metadata.keys()
    if missing:
        raise RuntimeError(f"Metadata missing required fields: {missing}")

    # image_prompt is recommended but not strictly required — pipeline degrades
    # gracefully if Claude omits it (article ships without a unique hero image).
    if "image_prompt" not in metadata or not str(metadata.get("image_prompt", "")).strip():
        print("⚠️  Claude did not return image_prompt — article will use fallback og-image")
        metadata["image_prompt"] = ""

    # ==== Tags validation ====
    # Tags drive the related-link engine. If Claude omits them, surface a warning
    # but don't fail — the operator can fix in taxonomy.json before publish.
    tags = metadata.get("tags", [])
    if not isinstance(tags, list) or not tags:
        print("⚠️  Claude did not return 'tags' field — related-link scoring will be weak. "
              "Consider adding tags manually to taxonomy.json before publish.")
        metadata["tags"] = []
    else:
        # Quick sanity check: warn on unknown prefixes (typos) but keep them
        valid_prefixes = ("format:", "topic:", "platform:", "audience:")
        unknown = [t for t in tags if not any(t.startswith(p) for p in valid_prefixes)]
        if unknown:
            print(f"⚠️  Unknown tag prefixes (kept anyway): {unknown}")
        # Warn if no format tag — the related engine uses it for diversity
        has_format = any(t.startswith("format:") for t in tags)
        if not has_format:
            print("⚠️  No 'format:*' tag found — related-link diversity rule won't apply correctly")

    # ==== Inline-link validation ====
    # The system prompt requires 3-5 internal links. Surface a warning if
    # Claude under-delivered so the operator can decide to regenerate.
    link_count = count_internal_links(markdown_body)
    if link_count < 3:
        print(f"⚠️  Article has only {link_count} internal links (required: 3-5). "
              f"Consider regenerating or manually adding links before publish.")
    elif link_count > 6:
        print(f"⚠️  Article has {link_count} internal links — that's more than guidance suggests (3-5). "
              f"Review for over-linking before publish.")
    else:
        print(f"✅ Internal links: {link_count}")
        # Print the actual anchors so operator can eyeball them
        for anchor, url in extract_internal_links(markdown_body):
            print(f"     [{anchor}]({url})")

    metadata["markdown_body"] = markdown_body
    return metadata
 
 
# ==== File output ====
 
def slug_from_topic(topic: dict, claude_slug: str) -> str:
    """Short slug used as the _pending/ dirname and Telegram callback_data.

    Telegram inline-button callback_data has a hard 64-byte limit. The
    longest action prefix in our scheme is "regenerate:" (11 bytes), so
    the slug must fit in 53 bytes. We cap at 50 to leave a little headroom.

    IMPORTANT: this slug is NOT what ends up in the published URL. The
    URL uses `url_slug_from_topic()` below, which can be longer and cuts
    on word boundaries. That split exists because a 50-char hard cut in
    the middle of a word (e.g. "operator-p", "owner-guid") was hurting
    Google indexing — see the July 2026 GSC review.
    """
    candidate = claude_slug or topic.get("topic", "untitled")
    # word_boundary=True prevents mid-word truncation even at 50 chars,
    # so the _pending/ dirname and callback_data stay readable in logs
    # and Telegram messages.
    return slugify(candidate, max_length=50, word_boundary=True, save_order=True)


def url_slug_from_topic(topic: dict, claude_slug: str) -> str:
    """Longer slug used for the published URL — /blog/{url_slug}/.

    Rules:
      - Hard cap at 65 chars. Google's own guidance treats URL length as
        a minor ranking factor; 60-70 chars is the sweet spot where slugs
        stay descriptive without looking spammy.
      - word_boundary=True guarantees we never cut in the middle of a
        word. Losing a trailing word is fine; losing half a word ("guid",
        "operator-p") triggers a "low quality URL" signal.
      - save_order=True keeps the original word order, which matters for
        readable Portuguese/Chinese slugs.
    """
    candidate = claude_slug or topic.get("topic", "untitled")
    return slugify(candidate, max_length=65, word_boundary=True, save_order=True)
 
 
def _normalize_russian_preview(rp, article: dict) -> dict:
    """Coerce the russian_preview field into a stable dict shape.

    Why this exists:
      Claude (especially with weak/placeholder prompts) sometimes returns
      `russian_preview` as a plain string instead of the documented
      {title_ru, summary_ru, h2_translations} object. Downstream code
      calls .get() on it and AttributeErrors out, killing the whole run
      AFTER we've already paid for the API call. Normalizing here keeps
      the pipeline alive on a malformed response and logs a warning so we
      know the prompt needs tightening.

    Inputs we accept:
      - dict — pass through (also fills missing keys with sensible defaults)
      - str  — wrap as {title_ru: h1, summary_ru: <the str>, h2_translations: []}
      - None or other — empty defaults
    """
    if isinstance(rp, dict):
        return {
            "title_ru": rp.get("title_ru", article.get("h1_title", "")),
            "summary_ru": rp.get("summary_ru", ""),
            "h2_translations": rp.get("h2_translations", []) or [],
        }
    if isinstance(rp, str):
        print(
            "⚠️  russian_preview returned as string instead of dict. "
            "Wrapping it as summary_ru. Tighten the prompt to require "
            "the {title_ru, summary_ru, h2_translations} object structure."
        )
        return {
            "title_ru": article.get("h1_title", ""),
            "summary_ru": rp,
            "h2_translations": [],
        }
    if rp is not None:
        print(
            f"⚠️  russian_preview unexpected type {type(rp).__name__}, "
            f"falling back to empty preview."
        )
    return {
        "title_ru": article.get("h1_title", ""),
        "summary_ru": "",
        "h2_translations": [],
    }


def save_article(article: dict, topic: dict, lang_cfg, lang: str) -> tuple[Path, str]:
    """Save article files. Return (target_dir, final_slug)."""
    slug = slug_from_topic(topic, article.get("slug", ""))
    # Longer URL slug for the published site (see url_slug_from_topic docstring).
    # Kept separate from `slug` so Telegram callback_data stays under 64 bytes.
    url_slug = url_slug_from_topic(topic, article.get("slug", ""))
    target_dir = lang_cfg["pending_dir"] / slug
    target_dir.mkdir(parents=True, exist_ok=True)
 
    # Markdown body
    (target_dir / "body.md").write_text(article["markdown_body"], encoding="utf-8")
 
    # Russian preview (always Russian — it's the OPERATOR's preview, not the
    # reader's. Operator scans the TG queue in Russian regardless of which
    # language the article is in.)
    rp = _normalize_russian_preview(article.get("russian_preview"), article)
    h2_lines = "\n".join(
        f"- **{item.get('en', '')}** — {item.get('ru', '')}"
        for item in rp.get("h2_translations", [])
        if isinstance(item, dict)
    )
    preview_md = f"""# Превью статьи (на русском)
 
## Заголовок
**EN:** {article['h1_title']}
**RU:** {rp.get('title_ru', '')}
 
## Объём
{article.get('word_count', 'unknown')} слов
 
## Язык статьи
{lang} ({lang_cfg['html_lang']})
 
## Суть статьи
{rp.get('summary_ru', '')}
 
## Структура (H2-разделы)
{h2_lines}
 
## SEO
- **Meta title:** {article['meta_title']}
- **Meta description:** {article['meta_description']}
- **Primary keyword:** {topic.get('primary_keyword', '')}
- **Intent:** {topic.get('intent', '')}
- **Target page:** {topic.get('target_page', '')}
"""
    (target_dir / "preview.md").write_text(preview_md, encoding="utf-8")

    # Hero image (best-effort: if it fails, article still ships)
    image_prompt = article.get("image_prompt", "")
    hero_path = generate_hero_image(image_prompt, target_dir)
    has_hero = hero_path is not None

    # Metadata JSON
    # Stage 3 i18n: meta.json now carries `lang` and optional `translation_of`.
    # `lang` is required so publish.py knows which template config to render
    # the article with. `translation_of` is only set when the article has a
    # paired translation in the other language (used to emit hreflang tags).
    meta = {
        "slug": slug,
        # `url_slug` (added July 2026) is used by publish.py to build the
        # final URL and directory name under blog/. Falls back to `slug`
        # if the field is missing (older _pending/ dirs).
        "url_slug": url_slug,
        "lang": lang,
        "translation_of": article.get("translation_of") or topic.get("translation_of") or None,
        "meta_title": article["meta_title"],
        "meta_description": article["meta_description"],
        "h1_title": article["h1_title"],
        "word_count": article.get("word_count", 0),
        "faq": article["faq"],
        "tags": article.get("tags", []),
        "image_prompt": image_prompt,
        "has_hero_image": has_hero,
        "hero_filename": HERO_FILENAME if has_hero else "",
        "topic_row_data": topic,
        # Bot v2: если тема пришла из строки Sheets — сохраняем номер, чтобы
        # publish.py потом перевёл её в status=done автоматически.
        "source_row": topic.get("_source_row"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (target_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved article files to: {target_dir}")
    return target_dir, slug
 
 
# ==== Telegram preview with inline buttons ====
 
def build_telegram_preview(article: dict, topic: dict, slug: str, lang_cfg, lang: str,
                          quality_block: str = "") -> tuple[str, list]:
    """Build Markdown preview text + inline keyboard for Telegram."""
    rp = _normalize_russian_preview(article.get("russian_preview"), article)
    title_ru = rp.get("title_ru", article["h1_title"])
    summary_ru = rp.get("summary_ru", "")
    h2_translations = [it for it in rp.get("h2_translations", []) if isinstance(it, dict)]
 
    word_count = article.get("word_count", "?")
    primary_kw = topic.get("primary_keyword", "")
    target_page = topic.get("target_page", "")
    intent = topic.get("intent", "")
 
    # H2 list — limit to 6 entries to keep message under Telegram's 4096 char limit
    h2_lines = []
    for item in h2_translations[:6]:
        ru = item.get("ru", "")
        if ru:
            h2_lines.append(f"• {ru}")
    h2_block = "\n".join(h2_lines) if h2_lines else "_нет_"
 
    # Truncate summary if too long
    if len(summary_ru) > 700:
        summary_ru = summary_ru[:697] + "..."
 
    # Pending dir name varies by language: _pending or _pending_pt
    pending_name = lang_cfg["pending_dir"].name
    body_md_link = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{pending_name}/{slug}/body.md"
    preview_md_link = f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{pending_name}/{slug}/preview.md"

    # Language flag in the preview header — quick visual signal in TG queue
    lang_flag = {"ru": "🇺🇦"}.get(lang, "🇺🇦")

    quality_prefix = ""
    if quality_block:
        quality_prefix = f"\n{quality_block}\n"

    # Bot v2: если тема пришла из строки Google Sheets — показываем её номер.
    source_row = topic.get("_source_row")
    row_hint = f"\n📊 Источник: строка *{source_row}* в Sheets" if source_row else ""

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
 
    # Bot v2: расширенная клавиатура (3×2)
    #  ✅ Опубликовать · 📄 Полный текст
    #  🧾 Исходники   · ✏️ Правка
    #  🔄 Перегенерить · ❌ Отклонить
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
            {"text": "❌ Отклонить", "callback_data": f"reject:{slug}"},
        ],
    ]
    return text, keyboard
 
 
def escape_md(text: str) -> str:
    """Escape Telegram MarkdownV1 special chars in user content.

    MarkdownV1 (parse_mode=Markdown) treats these as formatting markers:
      _ italic   * bold   ` code   [ ] ( ) inline links

    Any unbalanced occurrence breaks the parser with 'can't parse entities'.
    Claude's russian_preview (title_ru, summary_ru, h2_translations) may
    contain any of these naturally — square brackets for examples, parens
    for clarifications, asterisks in numbered lists, etc. We escape ALL
    of them so the user content renders as literal text.

    URLs and our own intentional formatting (the surrounding f-string in
    build_telegram_preview) are NOT routed through this function, so they
    stay unescaped and parseable.
    """
    if not text:
        return ""
    s = str(text)
    # MarkdownV1: экранируем только реально ломающие маркеры форматирования.
    # Скобки ( ) [ ] в обычном тексте (вне ссылок) парсер не ломают, поэтому
    # их НЕ экранируем — иначе в превью появляются некрасивые \( \) \[ \].
    for ch in ("\\", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s
 
 
def send_telegram_preview(text: str, keyboard: list, photo_path: Path | None = None) -> None:
    """Send Telegram preview. With photo if provided (uses sendPhoto + caption),
    otherwise plain text (sendMessage). Photo caption is limited to 1024 chars
    by Telegram, so we truncate text in that mode."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ℹ️  Telegram credentials not set, skipping preview send")
        return

    if photo_path and photo_path.exists():
        _send_telegram_photo(token, chat_id, photo_path, text, keyboard)
    else:
        _send_telegram_message(token, chat_id, text, keyboard)


def _send_telegram_message(token: str, chat_id: str, text: str, keyboard: list,
                           parse_mode: str | None = "Markdown") -> None:
    """Send a Telegram text message. `parse_mode=None` sends plain text (useful
    as a fallback when Markdown parsing fails on user content)."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Telegram preview sent (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️  Telegram returned {e.code}: {body[:500]}")
        # If Markdown parsing failed, try once more without parse_mode so the
        # operator at least sees the raw text. Don't recurse infinitely — only
        # one retry, and only if we were using Markdown.
        if e.code == 400 and parse_mode and "parse entities" in body.lower():
            print("    → retrying as plain text (no parse_mode)")
            _send_telegram_message(token, chat_id, text, keyboard, parse_mode=None)
    except Exception as e:
        print(f"⚠️  Telegram preview send failed: {type(e).__name__}: {e}")


def _send_telegram_photo(token: str, chat_id: str, photo_path: Path,
                         caption: str, keyboard: list) -> None:
    """sendPhoto with multipart/form-data. Caption capped at 1024 chars by Telegram."""
    # Telegram caption limit is 1024 chars for sendPhoto
    if len(caption) > 1020:
        caption = caption[:1017] + "..."

    # Build multipart manually to avoid extra dependencies
    import mimetypes, secrets
    boundary = secrets.token_hex(16)
    crlf = "\r\n"

    fields = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "Markdown",
        "reply_markup": json.dumps({"inline_keyboard": keyboard}),
    }
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}{crlf}".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(crlf.encode())

    photo_bytes = photo_path.read_bytes()
    mime = mimetypes.guess_type(photo_path.name)[0] or "image/webp"
    body.extend(f"--{boundary}{crlf}".encode())
    body.extend(
        f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"{crlf}'.encode()
    )
    body.extend(f"Content-Type: {mime}{crlf}{crlf}".encode())
    body.extend(photo_bytes)
    body.extend(crlf.encode())
    body.extend(f"--{boundary}--{crlf}".encode())

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"✅ Telegram photo preview sent (status {resp.status})")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️  Telegram sendPhoto returned {e.code}: {err_body[:500]}")
        # Fall back to plain text so the operator still gets the preview
        print("    → falling back to text-only preview")
        _send_telegram_message(token, chat_id, caption, keyboard)
    except Exception as e:
        print(f"⚠️  Telegram sendPhoto failed: {type(e).__name__}: {e}")
        _send_telegram_message(token, chat_id, caption, keyboard)
 
 
# ==== Main ====

def has_article_generated_today(pending_dir: Path) -> bool:
    """Check if there's already a generated article for today in `pending_dir`.

    Why this matters:
      The workflow runs on cron (primary + backup slots same day). If both
      slots fire we don't want two articles per day. This guard makes
      generate.py idempotent across same-day invocations: first run generates,
      subsequent same-day runs are no-ops.

      Manual workflow_dispatch with --force overrides this guard, so the
      operator can still bang out an out-of-schedule article when needed.

    Stage 3 i18n: the guard is per-language. EN and PT have separate
    pending dirs, so EN cron and PT cron don't block each other on the
    same day. Each language can produce one article per day independently.

    Detection approach:
      We read each child folder's meta.json and compare `generated_at` (ISO
      UTC) to today's UTC date. meta.json is the canonical source of truth —
      it's written once by save_article() at generation time and never touched
      again.

      Previously this used filesystem mtime, which was unreliable under
      `actions/checkout@v4`: checkout writes all files with the runner's
      current timestamp, so every folder appeared "modified today" on every
      run. That caused silent no-ops — generator skipped with exit 0, no
      article was produced, and the Telegram preview was never sent because
      main() returned before reaching the preview block.

    Edge cases:
      - Folders without a readable meta.json are ignored (could be partial
        in-flight state or manually-created directories).
      - Malformed JSON or missing `generated_at` is ignored (same reasoning).
      - Date comparison is in UTC to match the cron timezone.
    """
    if not pending_dir.exists():
        return False
    today_utc = datetime.now(timezone.utc).date()
    for child in pending_dir.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            generated_at = meta.get("generated_at")
            if not generated_at:
                continue
            # ISO format with timezone, e.g. "2026-05-12T09:17:42.123456+00:00"
            gen_date = datetime.fromisoformat(generated_at).astimezone(timezone.utc).date()
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if gen_date == today_utc:
            print(f"ℹ️  Found today's article folder: {child.name}")
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate article from next queued topic")
    parser.add_argument(
        "--lang",
        choices=["ru"],
        default="ru",
        help="Article language. Defaults to 'en' for backward compatibility "
             "with pre-Stage-3 workflow runs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Generate even if there's already a folder created today in the "
             "pending dir. Used for out-of-schedule manual runs.",
    )
    parser.add_argument(
        "--topic-file",
        default=None,
        help="Путь к JSON-файлу с темой (локальный режим БЕЗ Google Sheets). "
             "Формат: {topic, primary_keyword, secondary_keywords, intent, "
             "target_page, notes}. Если задан — Sheets и Telegram не нужны.",
    )
    args = parser.parse_args()

    # Resolve language config and validate prompt+taxonomy exist BEFORE any
    # external API calls. Prompt missing is the most common Stage-3 footgun.
    try:
        validate_cfg_files_exist(args.lang)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    lang_cfg = get_cfg(args.lang)

    # Idempotency guard: skip if today already has an article in THIS lang's
    # pending dir (unless --force). EN and PT have independent guards.
    if not args.force and has_article_generated_today(lang_cfg["pending_dir"]):
        print(f"✅ An article in lang={args.lang!r} was already generated today — skipping. "
              "Use 'Run workflow' with force=true to override.")
        return 0

    # --- Локальный режим: тема из файла, без Google Sheets/Telegram ---
    if args.topic_file:
        topic = json.loads(Path(args.topic_file).read_text(encoding="utf-8"))
        sheet = None
        row_index = None
        print(f"Локальный режим. Тема: {topic.get('topic')!r}")
    else:
        sheet = get_sheet()
        next_topic = get_next_queued_topic(sheet, args.lang)
        if next_topic is None:
            print(f"No queued topics found for lang={args.lang!r}. Nothing to do.")
            return 0
        row_index, topic = next_topic
        print(f"Picked topic from row {row_index} (lang={args.lang}): {topic.get('topic')!r}")
        update_status(sheet, row_index, "generating")
 
    try:
        article = generate_article(topic, lang_cfg)
        target_dir, slug = save_article(article, topic, lang_cfg, args.lang)
        if sheet is not None:
            update_status(sheet, row_index, "pending_review")
        print(f"\n✅ Generation complete: {target_dir}")
 
        # Evaluate article quality before sending preview
        try:
            print("📊 Evaluating article quality...")
            quality = evaluate_article(article, topic, article["markdown_body"], lang=args.lang)
            print(f"✅ Quality score: {quality['total']}/100 — {quality['verdict']}")
            quality_block = quality["telegram_block"]
            # Save quality report to pending dir for later inspection
            quality_path = lang_cfg["pending_dir"] / slug / "quality.json"
            quality_path.write_text(
                json.dumps(quality, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"⚠️  Quality check failed (non-fatal): {e}")
            quality_block = ""
        
        # Send Telegram preview — только если канал включён.
        # KOZYR: пока TELEGRAM_ENABLED=False, поэтому этот блок пропускается.
        # Включишь позже — превью статей начнут приходить в бота с кнопками.
        if not TELEGRAM_ENABLED:
            print("ℹ️  Telegram выключен (TELEGRAM_ENABLED=False) — превью не отправляем.")
        else:
            try:
                text, keyboard = build_telegram_preview(
                    article, topic, slug, lang_cfg, args.lang,
                    quality_block=quality_block
                )
                hero_path = target_dir / HERO_FILENAME
                send_telegram_preview(
                    text,
                    keyboard,
                    photo_path=hero_path if hero_path.exists() else None,
                )
            except Exception as e:
                # Не валим весь job, если у Telegram проблемы — статья уже сохранена.
                print(f"⚠️  Telegram preview build/send failed: {type(e).__name__}: {e}")
 
        return 0
    except Exception as e:
        if sheet is not None:
            update_status(sheet, row_index, "queued")
        print(f"\n❌ Generation failed: {type(e).__name__}: {e}", file=sys.stderr)
        raise
 
 
if __name__ == "__main__":
    sys.exit(main())
