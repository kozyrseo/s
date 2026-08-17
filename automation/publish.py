"""
Publish a pending article to the live site.

Reads _pending/{slug}/ (body.md, meta.json), renders HTML from template,
saves blog/{slug}/index.html, updates sitemap.xml and blog/index.html,
removes _pending/{slug}/, updates Google Sheets, pings IndexNow.

Usage:
    python automation/publish.py --slug bot-detection-pppoker-anonymous-clubs
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import gspread
import markdown as md_lib
from google.oauth2.service_account import Credentials

from lang_config import (
    SITE_URL,
    TELEGRAM_ENABLED,
    get_cfg,
    validate_cfg_files_exist,
    canonical_url_for,
    build_hreflang_block,
)
from linking import (
    load_taxonomy,
    get_article_tags,
    pick_related_for_article,
    pick_related_for_format_page,
    validate_inline_links,
    strip_invalid_links,
    count_internal_links,
)

from body_enhance import enhance_body_html


# ==== Configuration ====
# Шаблон рядом с этим файлом → устойчиво к рабочей директории запуска
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "article.html"
SITEMAP_PATH = Path("sitemap.xml")
INDEXNOW_KEY_FILE_PATTERN = "*.indexnow.txt"  # the key file lives in repo root

# Stage 3 i18n: PENDING_DIR, BLOG_DIR, BLOG_INDEX_PATH, TAXONOMY_PATH used to
# be module-level constants. They are now per-language and resolved from
# lang_config at runtime via the --lang CLI flag.


# ==== Taxonomy management ====

def upsert_taxonomy_article(
    slug: str,
    title: str,
    description: str,
    tags: list[str],
    taxonomy_path: Path,
) -> None:
    """Add or update an article entry in the given taxonomy file. Idempotent.

    Why this matters:
      The Related-link engine reads the taxonomy. New articles need to be
      registered so they appear in scoring on the *next* article's publish.
      We do this in publish.py rather than generate.py because the slug can
      change between generation and publication (operator may rename), and
      we want only published articles in the registry.

    Stage 3 i18n: `taxonomy_path` is per-language. EN articles update
    automation/taxonomy.json; PT articles update automation/taxonomy.pt.json.

    Sensible fallbacks:
      - description: if missing, derive a 1-line summary from the meta_description
      - tags: if empty, leave empty list (downgrades to recency-based scoring)
    """
    if not taxonomy_path.exists():
        print(f"⚠️  {taxonomy_path} not found — skipping taxonomy update for '{slug}'")
        return

    try:
        with taxonomy_path.open(encoding="utf-8") as f:
            tax = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Could not load {taxonomy_path}: {e}. Skipping update for '{slug}'.")
        return

    articles = tax.setdefault("articles", {})

    # Build the entry. If the slug already exists, only update fields we know
    # about — preserve any operator edits (e.g. hand-curated summary_for_related).
    existing = articles.get(slug, {})
    summary = existing.get("summary_for_related") or _truncate_for_summary(description)

    entry = {
        "title": existing.get("title") or title,
        "description": description or existing.get("description", ""),
        "tags": tags or existing.get("tags", []),
        "summary_for_related": summary,
    }
    articles[slug] = entry

    try:
        taxonomy_path.write_text(
            json.dumps(tax, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        action = "Updated" if slug in articles and existing else "Added"
        print(f"✅ {action} taxonomy entry for '{slug}' in {taxonomy_path.name} (tags: {tags or 'none'})")
    except OSError as e:
        print(f"⚠️  Failed to write {taxonomy_path}: {e}")


def _truncate_for_summary(text: str, max_words: int = 22) -> str:
    """Trim a meta_description down to a tighter Related-card summary."""
    if not text:
        return ""
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",.;:") + "."


# ==== Markdown → HTML ====

def render_markdown(md_text: str) -> str:
    """Convert Markdown to HTML with extensions for tables, fenced code, etc."""
    html = md_lib.markdown(
        md_text,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html5",
    )
    return html


# ==== Reading time ====

def calculate_reading_time(word_count: int) -> int:
    """Average adult reading speed: 200 words per minute. Round up to nearest minute."""
    if not word_count or word_count < 1:
        return 1
    return max(1, math.ceil(word_count / 200))


# ==== Table of Contents ====

def extract_h2_headings(md_text: str) -> list[tuple[str, str]]:
    """Return list of (anchor_id, heading_text) for all H2 sections."""
    h2_pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    headings = []
    seen_ids = set()
    for match in h2_pattern.finditer(md_text):
        text = match.group(1).strip()
        anchor = slugify_anchor(text)
        # Avoid duplicate anchors
        original = anchor
        counter = 2
        while anchor in seen_ids:
            anchor = f"{original}-{counter}"
            counter += 1
        seen_ids.add(anchor)
        headings.append((anchor, text))
    return headings


def slugify_anchor(text: str) -> str:
    """Convert heading text to URL-safe anchor id."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text


def add_h2_anchor_ids(html: str, headings: list[tuple[str, str]]) -> str:
    """Add id="..." to <h2> tags in rendered HTML in order."""
    if not headings:
        return html
    # python-markdown renders ## as <h2>text</h2>, no id by default with our extensions.
    # We replace each <h2>...</h2> with <h2 id="...">...</h2> in order of appearance.
    result_parts = []
    last_pos = 0
    h2_iter = iter(headings)
    for match in re.finditer(r"<h2>(.*?)</h2>", html, flags=re.DOTALL):
        try:
            anchor, _ = next(h2_iter)
        except StopIteration:
            break
        result_parts.append(html[last_pos:match.start()])
        result_parts.append(f'<h2 id="{anchor}">{match.group(1)}</h2>')
        last_pos = match.end()
    result_parts.append(html[last_pos:])
    return "".join(result_parts)


def render_toc(headings: list[tuple[str, str]]) -> str:
    """Render Table of Contents block. Empty string if too few headings."""
    if len(headings) < 3:
        return ""
    items = "\n".join(
        f'<li><a href="#{anchor}">{escape_html(text)}</a></li>'
        for anchor, text in headings
    )
    return f'''<aside class="toc" aria-label="Table of contents">
<div class="toc-label">In this article</div>
<ol>
{items}
</ol>
</aside>'''


# ==== Key Takeaways ====

def render_key_takeaways(meta: dict, md_text: str) -> str:
    """
    Generate Key Takeaways block from FAQ first answers or first H2 section content.
    For now: extract from FAQ entries — first sentence of each answer makes a good takeaway.
    Pulls 3-4 items.
    """
    faq = meta.get("faq", [])
    takeaways = []
    for item in faq[:4]:
        answer = item.get("answer", "").strip()
        if not answer:
            continue
        # Take the first sentence as the takeaway. But many FAQ answers open
        # with a bare "Да."/"Нет."/"Ні." — a standalone yes/no is useless as a
        # key takeaway (bug: rendered as `<li>Нет.</li>`). In that case pull in
        # the following sentence(s) so the takeaway actually carries meaning,
        # and drop the item entirely if nothing substantive follows.
        sentences = re.split(r"(?<=[.!?])\s+", answer)
        SHORT_STARTERS = {
            "да", "нет", "ні", "нет.", "да.", "ні.",
            "так", "так.", "ниже", "ниже.",
        }
        parts = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            parts.append(s)
            joined = " ".join(parts)
            # Keep extending while what we have is just a bare yes/no (<= 3
            # words or a known short starter) — we want a meaningful clause.
            bare = joined.rstrip(".!?").strip().lower()
            if bare in SHORT_STARTERS or len(joined.split()) <= 3:
                continue
            break
        takeaway = " ".join(parts).strip()
        # Still nothing substantive (answer was only a yes/no)? skip it.
        if not takeaway or takeaway.rstrip(".!?").strip().lower() in SHORT_STARTERS:
            continue
        if len(takeaway) > 200:
            takeaway = takeaway[:197] + "..."
        takeaways.append(takeaway)

    if len(takeaways) < 3:
        # Fallback: skip the block if we don't have enough material
        return ""

    items = "\n".join(f"<li>{escape_html(t)}</li>" for t in takeaways)
    return f'''<aside class="key-takeaways" aria-label="Key takeaways">
<div class="takeaways-label">Key takeaways</div>
<ul>
{items}
</ul>
</aside>'''


# ==== Lede extraction ====

def extract_lede(md_text: str) -> tuple[str, str]:
    """Extract first paragraph as lede, return (lede_text_plain, remaining_md_with_lede_removed)."""
    text = md_text.lstrip()
    # First paragraph is everything up to the first blank line OR first H2
    # Find first H2 position
    h2_match = re.search(r"^##\s", text, re.MULTILINE)
    boundary = h2_match.start() if h2_match else len(text)

    intro_block = text[:boundary].strip()
    rest = text[boundary:]

    # Within the intro block, the lede is the FIRST paragraph (before first blank line)
    paragraphs = re.split(r"\n\s*\n", intro_block, maxsplit=1)
    if len(paragraphs) == 1:
        lede = paragraphs[0]
        intro_remainder = ""
    else:
        lede = paragraphs[0]
        intro_remainder = paragraphs[1]

    # Strip markdown emphasis from lede for the <p class="lede"> display
    lede_plain = re.sub(r"\*\*(.+?)\*\*", r"\1", lede)
    lede_plain = re.sub(r"\*(.+?)\*", r"\1", lede_plain)
    lede_plain = re.sub(r"\s+", " ", lede_plain).strip()

    # Remaining markdown: keep intro_remainder + rest (so the body keeps non-lede intro + sections)
    if intro_remainder:
        remaining_md = intro_remainder + "\n\n" + rest
    else:
        remaining_md = rest

    return lede_plain, remaining_md.strip()


# ==== FAQ rendering ====

# Локализованные заголовки FAQ-секции по языку статьи
FAQ_HEADINGS = {
    "ru": "Частые вопросы",
    "uk": "Часті запитання",
    "en": "Frequently asked questions",
    "pl": "Najczęściej zadawane pytania",
    "kz": "Жиі қойылатын сұрақтар",
}


def render_faq_html(faq: list[dict], lang: str = "ru") -> str:
    """Render visible FAQ section with <details>/<summary> blocks."""
    if not faq:
        return ""
    # Язык может прийти как 'ua' (RU-контент на украинском домене) — маппим на 'ru'
    heading = FAQ_HEADINGS.get(lang, FAQ_HEADINGS.get("ru"))
    items = []
    for entry in faq:
        q = escape_html(entry.get("question", "").strip())
        a = entry.get("answer", "").strip()
        # Allow inline markdown in answer (basic): convert to HTML via markdown lib
        a_html = md_lib.markdown(a, extensions=["extra"])
        # Strip outer <p> if single paragraph for cleaner styling
        a_html_inner = re.sub(r"^<p>(.*?)</p>\s*$", r"\1", a_html, flags=re.DOTALL)
        items.append(f'''<details class="faq-item">
<summary>{q}</summary>
<div class="faq-answer">{a_html_inner}</div>
</details>''')

    items_html = "\n".join(items)
    return f'''<section class="faq-section" aria-label="{heading}">
<h2>{heading}</h2>
{items_html}
</section>'''


def render_faq_jsonld(faq: list[dict]) -> str:
    """Render FAQPage JSON-LD mainEntity items."""
    if not faq:
        return ""
    parts = []
    for entry in faq:
        q = entry.get("question", "").strip()
        a = entry.get("answer", "").strip()
        # Build via json.dumps for proper escaping
        item = {
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": a,
            }
        }
        parts.append(json.dumps(item, ensure_ascii=False))
    return ",".join(parts)


# ==== HTML escaping ====

def escape_html(text: str) -> str:
    """Minimal HTML escape for text in attribute/content."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def escape_json_string(text: str) -> str:
    """Escape text to be safely embedded inside a JSON string literal."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


# ==== Date helpers ====

_MONTHS = {
    "ru": ["", "января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"],
    "uk": ["", "січня", "лютого", "березня", "квітня", "травня", "червня",
           "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"],
}


def format_date_human(iso_date: str, lang: str = "ru") -> str:
    """Convert YYYY-MM-DD to a localized 'D month YYYY' string."""
    try:
        if "T" in iso_date:
            dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(iso_date, "%Y-%m-%d")
        months = _MONTHS.get("uk" if str(lang).startswith("uk") else "ru", _MONTHS["ru"])
        return f"{dt.day} {months[dt.month]} {dt.year}"
    except (ValueError, AttributeError, IndexError):
        return iso_date


def date_to_iso_date(iso_datetime: str) -> str:
    """Extract YYYY-MM-DD from ISO datetime."""
    if not iso_datetime:
        return datetime.now(timezone.utc).date().isoformat()
    if "T" in iso_datetime:
        return iso_datetime.split("T")[0]
    return iso_datetime


# ==== Related articles ====

def get_existing_blog_posts(blog_dir: Path, taxonomy_path: Path) -> list[dict]:
    """Scan a blog directory for existing posts and return {slug, title, date, description, tags}.

    Stage 3 i18n: `blog_dir` and `taxonomy_path` are per-language. EN scans
    `blog/`, PT scans `pt/blog/`, each with its own taxonomy. Tags come from
    the language-matched taxonomy — articles missing from taxonomy get []
    (still appear, but with weak relatedness scores).
    """
    tax = load_taxonomy(taxonomy_path)
    tax_articles = tax.get("articles", {})

    posts = []
    if not blog_dir.exists():
        return posts
    for child in blog_dir.iterdir():
        if not child.is_dir():
            continue
        idx = child / "index.html"
        if not idx.exists():
            continue
        html = idx.read_text(encoding="utf-8", errors="ignore")
        # Extract title from <h1>
        title_match = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
        title = (title_match.group(1).strip() if title_match else child.name).split("|")[0].strip()

        # Description: prefer taxonomy summary (curated), fall back to JSON-LD
        slug = child.name
        tax_entry = tax_articles.get(slug, {})
        description = tax_entry.get("summary_for_related", "")

        if not description:
            ldjson_desc_match = re.search(
                r'"@type":\s*"BlogPosting".*?"description":\s*"((?:[^"\\]|\\.)*)"',
                html, re.DOTALL,
            )
            if ldjson_desc_match:
                description = ldjson_desc_match.group(1).encode("utf-8").decode("unicode_escape")
            else:
                # Fallback: take meta description, but strip after first orphan quote
                meta_desc_match = re.search(r'<meta name="description" content="([^"]*)"', html)
                description = meta_desc_match.group(1) if meta_desc_match else ""

        date_match = re.search(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})', html)
        date = date_match.group(1) if date_match else ""

        # Tags from taxonomy — empty list if untracked
        tags = list(tax_entry.get("tags", []))

        # hero-фото для премиум-карточки related + короткий тег
        has_hero = (child / "hero.webp").exists()
        _topic = next((t.split(":", 1)[1] for t in tags if t.startswith("topic:")), "")
        _tag_labels = {"rakeback": "Рейкбек", "clubs": "Клубы", "club-review": "Клубы",
                       "rooms": "Румы", "room-review": "Обзор", "comparison": "Сравнение",
                       "bankroll": "Банкролл", "strategy": "Стратегия", "beginners": "Новичкам",
                       "payments": "Платежи", "legal": "Легальность"}
        tag_label = _tag_labels.get(_topic, "Блог")

        posts.append({
            "slug": slug,
            "title": title,
            "description": description,
            "date": date,
            "tags": tags,
            "has_hero": has_hero,
            "tag_label": tag_label,
        })
    return posts


def render_related(
    current_slug: str,
    all_posts: list[dict],
    lang_cfg,
    current_tags: list[str] | None = None,
) -> str:
    """Render up to 3 related-article cards using topic-aware scoring.

    Scoring lives in linking.py: combines tag-overlap (Jaccard) and recency,
    with a diversity rule that avoids 3-of-the-same-format clusters.

    If we don't have current article tags (e.g. taxonomy missing), the function
    falls back to recency sort — same behavior as the old implementation.

    Stage 3 i18n: `lang_cfg` controls the URL prefix used in card hrefs and
    the localized fallback service-overview cards.
    """
    # Determine self tags. Argument takes priority; fall back to taxonomy.
    if current_tags is None:
        current_tags = get_article_tags(current_slug, taxonomy_path=lang_cfg["taxonomy"])

    if current_tags:
        chosen = pick_related_for_article(
            self_slug=current_slug,
            self_tags=current_tags,
            candidates=all_posts,
            n=3,
        )
    else:
        # Legacy fallback: recency sort. Logged so the operator notices.
        print(f"ℹ️  No tags for slug '{current_slug}' — falling back to recency-based related")
        others = [p for p in all_posts if p["slug"] != current_slug]
        others.sort(key=lambda p: p["date"], reverse=True)
        chosen = others[:3]

    url_prefix = lang_cfg["url_prefix"]
    read_word = "Читать" if not url_prefix.strip("/").endswith("uk/blog") and "/uk/" not in url_prefix else "Читати"
    if "/uk/" in url_prefix:
        read_word = "Читати"
    cards = []
    for p in chosen:
        cover = ""
        if p.get("has_hero"):
            cover = (f'<img src="{url_prefix}/{p["slug"]}/hero.webp" alt="" loading="lazy" '
                     f'style="width:100%;height:100%;object-fit:cover;display:block">')
        tag_label = escape_html(p.get("tag_label", "Блог"))
        cards.append(
            f'''<a href="{url_prefix}/{p["slug"]}/" class="post-card">'''
            f'''<div class="post-card__cover"><span class="post-card__tag">{tag_label}</span>{cover}</div>'''
            f'''<div class="post-card__body">'''
            f'''<h3 class="post-card__title">{escape_html(p["title"])}</h3>'''
            f'''<p class="post-card__excerpt">{escape_html(p["description"])}</p>'''
            f'''<span class="post-card__more">{read_word} →</span>'''
            f'''</div></a>'''
        )

    # If we have fewer than 3, fill with format-page cards. Diversity-aware: prefer
    # format pages whose tag is NOT the same as the article's primary format
    # (so an NLH article doesn't get a 3rd "see also: NLH page" filler).
    self_format = ""
    for t in (current_tags or []):
        if t.startswith("format:") and t != "format:mixed":
            self_format = t.split(":", 1)[1]
            break

    fallbacks_ordered = _format_page_fallback_cards(lang_cfg)
    # Move same-format fallback to the end of the list so other formats get filled first
    if self_format:
        fallbacks_ordered.sort(key=lambda fb: fb[0] == self_format)

    for _, fb_html in fallbacks_ordered:
        if len(cards) >= 3:
            break
        cards.append(fb_html)

    return "\n      ".join(cards)


def _format_page_fallback_cards(lang_cfg) -> list[tuple[str, str]]:
    """Localized fallback cards pointing to the three format-page hubs.

    These appear under Related when fewer than 3 article cards were chosen.
    The (format_key, html) tuples are returned so render_related can re-order
    them to keep the same-format card last.
    """
    if lang_cfg["html_lang"] == "pt-BR":
        return [
            ("nlh",
             '<article class="card"><h3><a href="/pt/nlh-bots/">Visão geral dos bots NLH</a></h3>'
             '<p>Infraestrutura de IA para No-Limit Hold\'em — limites 1/2 a 10/20+, suporte off-peak, integração com cronograma.</p></article>'),
            ("plo",
             '<article class="card"><h3><a href="/pt/plo-bots/">Visão geral dos bots PLO</a></h3>'
             '<p>Infraestrutura de IA para Pot-Limit Omaha — variantes de 4, 5 e 6 cartas com calibração ciente de variância.</p></article>'),
            ("short-deck",
             '<article class="card"><h3><a href="/pt/short-deck-bots/">Visão geral dos bots Short Deck</a></h3>'
             '<p>Infraestrutura de IA para Short Deck — rankings de mãos modificados, estruturas de ante, alinhamento com mercado asiático.</p></article>'),
        ]
    # EN default
    return [
        ("nlh",
         '<article class="card"><h3><a href="/nlh-bots/">NLH Bots service overview</a></h3>'
         '<p>AI infrastructure for No-Limit Hold\'em — limits 1/2 through 10/20+, off-peak support, schedule integration.</p></article>'),
        ("plo",
         '<article class="card"><h3><a href="/plo-bots/">PLO Bots service overview</a></h3>'
         '<p>AI infrastructure for Pot-Limit Omaha — 4-card, 5-card, 6-card variants with variance-aware calibration.</p></article>'),
        ("short-deck",
         '<article class="card"><h3><a href="/short-deck-bots/">Short Deck Bots service overview</a></h3>'
         '<p>AI infrastructure for Short Deck — modified hand rankings, ante structures, Asian-market schedule alignment.</p></article>'),
    ]


# ==== Format-page Related block updates ====

# A small marker comment is wrapped around the Related block in each format
# page so we can find and replace it programmatically. Pages without the
# marker won't be touched (safe for hand-edited or legacy versions).
RELATED_BLOCK_START = "<!-- AUTO_RELATED_BLOCK_START -->"
RELATED_BLOCK_END = "<!-- AUTO_RELATED_BLOCK_END -->"


def render_format_page_related_block(format_url: str, all_posts: list[dict], lang_cfg) -> str:
    """Render the Related-reading block for a format page.
    Returns the inner HTML of the section (between START/END markers).

    Stage 3 i18n: `lang_cfg` controls article URL prefix and the section's
    eyebrow + heading copy.
    """
    chosen = pick_related_for_format_page(
        format_url, all_posts, n=3, taxonomy_path=lang_cfg["taxonomy"]
    )

    # Map format url to a topical eyebrow and heading for the section.
    # PT format pages live at /pt/<slug>/, so we key the config by the bare
    # format slug rather than the full URL to keep one config table.
    format_slug = format_url.strip("/").split("/")[-1]  # "nlh-bots" etc.

    if lang_cfg["html_lang"] == "pt-BR":
        config = {
            "nlh-bots": ("Leitura relacionada", "Mais sobre <em>operações de clube NLH</em>"),
            "plo-bots": ("Leitura relacionada", "Mais sobre <em>PLO e operações de clube</em>"),
            "short-deck-bots": ("Leitura relacionada", "Mais sobre <em>Short Deck e operações de clube</em>"),
        }
        empty_msg = "Mais artigos em breve."
    else:
        config = {
            "nlh-bots": ("Related reading", "More on <em>NLH club operations</em>"),
            "plo-bots": ("Related reading", "More on <em>PLO and club operations</em>"),
            "short-deck-bots": ("Related reading", "More on <em>Short Deck and club operations</em>"),
        }
        empty_msg = "More articles coming soon."

    eyebrow, heading = config.get(format_slug, ("Related reading", "Related articles"))

    if not chosen:
        # Empty render: leave a placeholder so the section still validates HTML
        return (
            f'<section>\n  <div class="container">\n'
            f'    <div class="section-head">\n'
            f'      <div class="eyebrow">{eyebrow}</div>\n'
            f'      <h2>{heading}</h2>\n'
            f'    </div>\n'
            f'    <p style="color:var(--fg-mute)">{empty_msg}</p>\n'
            f'  </div>\n</section>'
        )

    url_prefix = lang_cfg["url_prefix"]
    cards = []
    for p in chosen:
        cards.append(
            f'      <article class="card"><h3><a href="{url_prefix}/{p["slug"]}/">'
            f'{escape_html(p["title"])}</a></h3>'
            f'<p>{escape_html(p["description"])}</p></article>'
        )
    cards_html = "\n".join(cards)

    return (
        f'<section>\n  <div class="container">\n'
        f'    <div class="section-head">\n'
        f'      <div class="eyebrow">{eyebrow}</div>\n'
        f'      <h2>{heading}</h2>\n'
        f'    </div>\n'
        f'    <div class="grid-3">\n{cards_html}\n    </div>\n'
        f'  </div>\n</section>'
    )


def update_format_page_related_blocks(all_posts: list[dict], lang_cfg) -> None:
    """Update the AUTO_RELATED_BLOCK in each format page.

    EN run patches /nlh-bots/, /plo-bots/, /short-deck-bots/.
    PT run patches /pt/nlh-bots/, /pt/plo-bots/, /pt/short-deck-bots/.

    The block is identified by START/END marker comments. Pages without those
    markers are skipped — this is a no-op on legacy pages until they're rewritten.
    """
    # Format-page URLs are listed in this language's taxonomy; we use them
    # both to drive the related selection AND to derive the on-disk path.
    format_urls = list(lang_cfg["article_section_map"].keys())

    page_paths: list[tuple[str, Path]] = []
    is_pt = lang_cfg["html_lang"] == "pt-BR"
    for url in format_urls:
        # url is like "/nlh-bots/" — disk path differs per language.
        format_slug = url.strip("/").split("/")[-1]  # "nlh-bots"
        if is_pt:
            page_path = Path("pt") / format_slug / "index.html"
            taxonomy_url = f"/pt/{format_slug}/"
        else:
            page_path = Path(format_slug) / "index.html"
            taxonomy_url = f"/{format_slug}/"
        page_paths.append((taxonomy_url, page_path))

    for url, page_path in page_paths:
        if not page_path.exists():
            print(f"ℹ️  Format page not found, skipping: {page_path}")
            continue
        content = page_path.read_text(encoding="utf-8")
        if RELATED_BLOCK_START not in content or RELATED_BLOCK_END not in content:
            print(f"ℹ️  No AUTO_RELATED_BLOCK markers in {page_path}, skipping (legacy page)")
            continue

        # Build the new block
        block_inner = render_format_page_related_block(url, all_posts, lang_cfg)

        # Replace the content between markers
        pattern = re.compile(
            re.escape(RELATED_BLOCK_START) + r".*?" + re.escape(RELATED_BLOCK_END),
            re.DOTALL,
        )
        new_block = f"{RELATED_BLOCK_START}\n{block_inner}\n{RELATED_BLOCK_END}"
        new_content = pattern.sub(new_block, content)

        if new_content != content:
            page_path.write_text(new_content, encoding="utf-8")
            print(f"✅ Updated Related block in {page_path}")
        else:
            print(f"ℹ️  Related block in {page_path} already up to date")


# ==== Sitemap update ====

def update_sitemap(
    slug: str,
    today_iso: str,
    lang_cfg,
    translation_of: str | None = None,
    image_url: str | None = None,
    image_title: str | None = None,
) -> None:
    """Add a new blog post URL to sitemap.xml.

    Stage 3 i18n behavior:
      - The new <url> entry uses lang_cfg["canonical_base"] (e.g. /pt/blog/<slug>/).
      - If `translation_of` is given (i.e. the article has a paired translation
        in the OTHER language), we emit hreflang annotations on the new entry
        AND patch the existing paired entry to reference the new language.
        This keeps the sitemap symmetric: each side of the pair links to the other.
      - If `translation_of` is None (original article with no pair), we emit
        a plain <url> block with no hreflang. EN-only or PT-only articles
        coexist with paired articles in the same sitemap.
      - The blog-index <loc> for THIS language gets its lastmod bumped so
        crawlers see the index changed.
    """
    if not SITEMAP_PATH.exists():
        print(f"⚠️  sitemap.xml not found at {SITEMAP_PATH}, skipping sitemap update")
        return

    content = SITEMAP_PATH.read_text(encoding="utf-8")
    new_url = canonical_url_for_slug(lang_cfg, slug)

    if translation_of:
        # translation_of may be a dict {lang: slug} (multilang pairing, current)
        # or a bare slug string (legacy). Resolve the paired language + slug either
        # way — the old code hardcoded "ru" and passed the dict straight into the
        # URL, producing a literal "{'uk': '...'}" in the sitemap.
        if isinstance(translation_of, dict):
            other_lang, other_slug = next(iter(translation_of.items()))
        else:
            other_lang, other_slug = "ru", translation_of
        try:
            other_cfg = get_cfg(other_lang)
            paired_url = canonical_url_for_slug(other_cfg, other_slug)
        except (ValueError, KeyError):
            other_cfg = None
            paired_url = None
    else:
        paired_url = None
        other_cfg = None

    # Build hreflang annotations for our own <url> block (only if paired).
    # x-default points at the Russian (primary) version consistently on both sides.
    self_hreflang_lines = ""
    if paired_url and other_cfg:
        x_default_url = new_url if lang_cfg["html_lang"] == "ru" else paired_url
        self_hreflang_lines = (
            f'    <xhtml:link rel="alternate" hreflang="{lang_cfg["hreflang_self"]}" href="{new_url}"/>\n'
            f'    <xhtml:link rel="alternate" hreflang="{other_cfg["hreflang_self"]}" href="{paired_url}"/>\n'
            f'    <xhtml:link rel="alternate" hreflang="x-default" href="{x_default_url}"/>\n'
        )

    # If our URL is already in the sitemap, just bump its lastmod.
    # Otherwise insert a new <url> block before </urlset>.
    if new_url in content:
        pattern = re.compile(
            r"(<url>\s*<loc>" + re.escape(new_url) + r"</loc>\s*<lastmod>)\d{4}-\d{2}-\d{2}(</lastmod>)",
            re.DOTALL,
        )
        content = pattern.sub(rf"\g<1>{today_iso}\g<2>", content)
    else:
        # Per-page image entry (article hero as JPEG) for the image sitemap.
        image_block = ""
        if image_url:
            _title = escape_html(image_title or "")
            image_block = (
                f"    <image:image>\n"
                f"      <image:loc>{image_url}</image:loc>\n"
                + (f"      <image:title>{_title}</image:title>\n" if _title else "")
                + f"    </image:image>\n"
            )
        new_entry = (
            f"  <url>\n"
            f"    <loc>{new_url}</loc>\n"
            f"    <lastmod>{today_iso}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.7</priority>\n"
            f"{self_hreflang_lines}"
            f"{image_block}"
            f"  </url>\n"
            f"</urlset>"
        )
        content = content.replace("</urlset>", new_entry)

    # If we have a translation pair, patch the OTHER language's existing entry
    # to also include hreflang back to us. This keeps the sitemap symmetric.
    if paired_url and other_cfg:
        # Find the paired entry. If it's missing — log a warning (Stage 4 will
        # add it when the EN counterpart is published). If it's present but has
        # no hreflang yet, inject the three <xhtml:link> lines after <priority>.
        paired_block_re = re.compile(
            r"(<url>\s*<loc>" + re.escape(paired_url) + r"</loc>.*?</url>)",
            re.DOTALL,
        )
        m = paired_block_re.search(content)
        if not m:
            print(
                f"ℹ️  Paired URL {paired_url} not in sitemap yet — "
                f"hreflang on this side only. Re-publish the pair to add it."
            )
        else:
            paired_block = m.group(1)
            if "<xhtml:link" not in paired_block:
                # Inject hreflang lines before </url>
                paired_hreflang = (
                    f'    <xhtml:link rel="alternate" hreflang="{other_cfg["hreflang_self"]}"        href="{paired_url}"/>\n'
                    f'    <xhtml:link rel="alternate" hreflang="{lang_cfg["hreflang_self"]}"     href="{new_url}"/>\n'
                    f'    <xhtml:link rel="alternate" hreflang="x-default" href="{(paired_url if other_cfg["html_lang"] != "pt-BR" else new_url)}"/>\n'
                )
                patched = paired_block.replace("</url>", paired_hreflang + "  </url>")
                content = content.replace(paired_block, patched)
                print(f"✅ Added hreflang to paired entry: {paired_url}")

    # Bump lastmod on the blog-INDEX entry for THIS language.
    blog_index_url = f"{SITE_URL}{lang_cfg['blog_url']}"
    blog_index_pattern = re.compile(
        r"(<url>\s*<loc>" + re.escape(blog_index_url) + r"</loc>\s*<lastmod>)\d{4}-\d{2}-\d{2}(</lastmod>)",
        re.DOTALL,
    )
    content = blog_index_pattern.sub(rf"\g<1>{today_iso}\g<2>", content)

    SITEMAP_PATH.write_text(content, encoding="utf-8")
    print(f"✅ Updated sitemap.xml ({lang_cfg['html_lang']}: {slug})")


def canonical_url_for_slug(cfg, slug: str) -> str:
    """Convenience wrapper — same as lang_config.canonical_url_for but takes
    a resolved cfg dict instead of a lang code string."""
    return f"{cfg['canonical_base']}/{slug}/"


# ==== Blog index update ====

def update_blog_index(
    slug: str,
    h1_title: str,
    meta_description: str,
    date_iso: str,
    reading_time: int,
    lang_cfg,
    article_tag: str = "Блог",
    article_has_hero: bool = False,
) -> None:
    """Add a card for the new article into the blog index for this language.

    Stage 3 i18n: writes to lang_cfg["blog_index"] (blog/index.html for EN,
    pt/blog/index.html for PT). The card href uses lang_cfg's url_prefix.

    Two index-page styles are supported because EN and PT shipped with
    different markup:

      EN: <div style="display:grid;gap:20px;margin-top:40px"> with
          <a class="card" ...> cards (inline-styled).

      PT: <div class="grid-3"> with <article class="card"> cards using
          shared CSS classes.

    The function detects which style the target file uses and emits the
    matching card markup. If neither marker is found, it skips with a
    warning instead of silently doing nothing — that warning is what
    saved us discovering the PT bug.

    PT note: until Stage 4, pt/blog/index.html ships with <meta robots="noindex"> —
    cards added here will still render but won't be crawled. That's fine; the
    cards collect properly so when Stage 4 lifts the noindex they're already in
    place.
    """
    blog_index_path = lang_cfg["blog_index"]
    if not blog_index_path.exists():
        print(f"⚠️  {blog_index_path} not found, skipping blog index update")
        return

    content = blog_index_path.read_text(encoding="utf-8")
    new_card_url = f"{lang_cfg['url_prefix']}/{slug}/"

    # Skip if already present (idempotent re-runs are safe)
    if f'href="{new_card_url}"' in content:
        print(f"ℹ️  Blog index already has card for {slug}, skipping")
        return

    # Keep the CollectionPage JSON-LD "hasPart" list in sync with the cards:
    # add the newest article so structured data doesn't drift behind the page.
    article_url_full = canonical_url_for_slug(lang_cfg, slug)
    if '"hasPart"' in content and article_url_full not in content:
        new_part = (
            '    {"@type": "BlogPosting", "headline": '
            + json.dumps(h1_title, ensure_ascii=False)
            + f', "url": "{article_url_full}", "datePublished": "{date_iso}"}},\n'
        )
        content = re.sub(
            r'"hasPart"\s*:\s*\[\s*\n',
            lambda m: m.group(0) + new_part,
            content, count=1,
        )

    # Localized "min read" suffix for the card's date row
    min_read_label = lang_cfg["ui"]["min_read"]
    date_human = format_date_human(date_iso, lang_cfg.get("hreflang_self","ru")[:2])

    # Style A: EN — inline-styled grid with <a class="card">
    grid_marker_inline = '<div style="display:grid;gap:20px;margin-top:40px">'

    # Style B: PT — class-based grid with <article class="card">
    grid_marker_classed = '<div class="grid-3">'

    if grid_marker_inline in content:
        # EN-style card
        new_card = f'''      <a href="{new_card_url}" class="card" style="display:block;padding:30px 28px;text-decoration:none">
        <div style="font-size:12px;color:var(--gold);letter-spacing:0.15em;text-transform:uppercase;margin-bottom:10px">{date_human} · {reading_time} {min_read_label}</div>
        <h3 style="font-size:24px;margin-bottom:10px">{escape_html(h1_title)}</h3>
        <p style="color:var(--fg-mute);font-size:15px">{escape_html(meta_description)}</p>
      </a>
'''
        content = content.replace(grid_marker_inline, grid_marker_inline + "\n" + new_card, 1)
        blog_index_path.write_text(content, encoding="utf-8")
        print(f"✅ Updated {blog_index_path} with new card (inline-grid style)")
        return

    if grid_marker_classed in content:
        # PT-style card: <article class="card"> with shared CSS.
        # We use "Operações de Clube" / "Operations" as a generic eyebrow because
        # blog_index doesn't have access to the article's taxonomy section here.
        # If we want per-article eyebrows later, plumb topic_section through
        # from publish_article() into this function.
        eyebrow_label = lang_cfg["ui"].get("blog_card_default_eyebrow", {"pt-BR": "Operações de Clube", "zh-Hans": "俱乐部运营"}.get(lang_cfg["hreflang_self"], "Club Operations"))
        new_card = f'''      <article class="card">
        <div class="eyebrow" style="margin-bottom:8px">{escape_html(eyebrow_label)}</div>
        <h3><a href="{new_card_url}">{escape_html(h1_title)}</a></h3>
        <p>{escape_html(meta_description)}</p>
        <p style="color:var(--fg-mute);font-size:14px;margin-top:12px">{reading_time} {min_read_label} · {date_human}</p>
      </article>
'''
        # Insert right after the first grid-3 opening (which is the articles grid;
        # subsequent grid-3 blocks are for product pages and shouldn't be touched).
        content = content.replace(grid_marker_classed, grid_marker_classed + "\n" + new_card, 1)
        blog_index_path.write_text(content, encoding="utf-8")
        print(f"✅ Updated {blog_index_path} with new card (class-based grid style)")
        return

    # Style C: премиум-редизайн — <div class="post-grid"> с <a class="post-card">
    grid_marker_post = '<div class="post-grid">'
    if grid_marker_post in content:
        # Обложка: реальное hero-фото, если есть; иначе — SVG-мотив covers.py
        hero_rel = f"{lang_cfg['url_prefix']}/{slug}/hero.webp"
        hero_file = (SITEMAP_PATH.parent / lang_cfg['url_prefix'].strip('/') / slug / "hero.webp")
        if article_has_hero:
            cover_svg = (f'<img src="{hero_rel}" alt="" width="1600" height="900" '
                         f'loading="lazy" style="width:100%;height:100%;object-fit:cover;display:block">')
        else:
            try:
                import covers as _covers
                motif = _covers.pick_motif(slug, h1_title)
                cover_svg = _covers.cover_svg(motif)
            except Exception:
                cover_svg = ""
        # короткий тег — передан из publish_article
        card_tag = article_tag or "Блог"
        arrow = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')
        read_word = min_read_label
        more_word = {"ru": "Читать", "uk": "Читати"}.get(lang_cfg.get("hreflang_self", "")[:2], "Читать")
        new_card = (
            f'      <a href="{new_card_url}" class="post-card">\n'
            f'        <div class="post-card__cover">\n'
            f'          <span class="post-card__tag">{escape_html(card_tag)}</span>\n'
            f'          {cover_svg}\n'
            f'        </div>\n'
            f'        <div class="post-card__body">\n'
            f'          <div class="post-card__meta"><time datetime="{date_iso}">{date_human}</time>'
            f'<span class="dot"></span><span>{reading_time} {read_word}</span></div>\n'
            f'          <h2 class="post-card__title">{escape_html(h1_title)}</h2>\n'
            f'          <p class="post-card__excerpt">{escape_html(meta_description)}</p>\n'
            f'          <span class="post-card__more">{more_word} {arrow}</span>\n'
            f'        </div>\n'
            f'      </a>\n'
        )
        content = content.replace(grid_marker_post, grid_marker_post + "\n" + new_card, 1)
        blog_index_path.write_text(content, encoding="utf-8")
        print(f"✅ Updated {blog_index_path} with new card (post-grid style)")
        return

    # Neither marker found — log loudly so the next publish doesn't silently fail
    print(f"⚠️  Could not find cards grid in {blog_index_path}")
    print(f"    Expected one of: '{grid_marker_inline}' or '{grid_marker_classed}'")
    print(f"    Card NOT added — fix the blog index template or this function.")


# ==== IndexNow ping ====

def find_indexnow_key() -> str | None:
    """Find the IndexNow key file in repo root. The filename (without .txt) is the key."""
    for f in Path(".").iterdir():
        if f.is_file() and f.name.endswith(".txt"):
            # IndexNow key files are typically 32+ char alphanumeric strings
            stem = f.stem
            if re.fullmatch(r"[a-zA-Z0-9]{8,128}", stem):
                # Verify file content matches filename (IndexNow protocol requirement)
                try:
                    content = f.read_text(encoding="utf-8").strip()
                    if content == stem:
                        return stem
                except Exception:
                    continue
    return None


def ping_indexnow(slug: str, lang_cfg) -> None:
    """Submit the new article URL to IndexNow.

    Stage 3 i18n: URL is built from lang_cfg["canonical_base"] so PT
    articles ping their /pt/blog/<slug>/ URL, not the EN equivalent.
    """
    key = find_indexnow_key()
    if not key:
        print("ℹ️  No IndexNow key file found in repo root — skipping IndexNow ping")
        return

    url = canonical_url_for_slug(lang_cfg, slug)
    # host must match SITE_URL's domain (kozyr.club), not a hardcoded value —
    # IndexNow rejects pings whose host doesn't own the submitted URLs.
    host = SITE_URL.split("://", 1)[-1].strip("/")
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{SITE_URL}/{key}.txt",
        "urlList": [url],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.indexnow.org/IndexNow",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ IndexNow ping sent (status {resp.status}) for {url}")
    except urllib.error.HTTPError as e:
        print(f"⚠️  IndexNow returned {e.code}: {e.reason}")
    except Exception as e:
        print(f"⚠️  IndexNow ping failed: {type(e).__name__}: {e}")


# ==== Google Sheets status update ====

def update_sheet_status(slug: str, published_url: str,
                        topic: str | None = None) -> None:
    """Mark the topic in Google Sheets as published, fill published_url and published_at.

    Matching strategy (in priority order):
      1. EXACT match on `topic` column if provided. This is the canonical
         match — `topic` is the verbatim title from the Sheets row,
         preserved through generation in meta.json::topic_row_data.topic.
         Any pending_review row whose topic equals the passed-in topic is
         the right one. No false positives possible.

      2. Slug-substring fallback (legacy behaviour). Only used when topic
         is None or no exact match was found. This is the fragile mode
         the old code used exclusively, and it silently failed whenever
         Claude generated a slug shorter than the verbatim topic
         (e.g. slug "pppoker-vs-pokerbros-club-owners-2026" doesn't match
         topic "PPPoker vs PokerBros: which app should club owners
         choose in 2026"). Kept as a last resort for any legacy pending
         items where meta.json didn't preserve topic_row_data.
    """
    try:
        creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        sheet_id = os.environ["GOOGLE_SHEETS_ID"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
        client = gspread.authorize(credentials)
        sheet = client.open_by_key(sheet_id).sheet1

        records = sheet.get_all_records()
        headers = sheet.row_values(1)

        target_row = None

        # ---- Match strategy 1: exact topic match ----
        if topic:
            topic_normalized = topic.strip()
            for idx, row in enumerate(records, start=2):
                status = str(row.get("status", "")).strip().lower()
                if status != "pending_review":
                    continue
                row_topic = str(row.get("topic", "")).strip()
                if row_topic == topic_normalized:
                    target_row = idx
                    print(f"✅ Matched Sheets row {target_row} by exact topic")
                    break

        # ---- Match strategy 2: legacy slug-substring fallback ----
        if target_row is None:
            for idx, row in enumerate(records, start=2):
                status = str(row.get("status", "")).strip().lower()
                if status != "pending_review":
                    continue
                row_topic = str(row.get("topic", "")).strip().lower()
                topic_slug = re.sub(r"[^a-z0-9]+", "-", row_topic).strip("-")
                if slug in topic_slug or topic_slug in slug:
                    target_row = idx
                    print(f"⚠️  Matched Sheets row {target_row} by slug-substring "
                          f"fallback (less reliable — check the row)")
                    break

        if target_row is None:
            print(f"⚠️  Could not find pending_review row matching slug '{slug}' "
                  f"or topic '{topic}' in Sheets — please update manually")
            return

        # Update status, published_url, published_at
        now_iso = datetime.now(timezone.utc).isoformat()

        def col_index(name: str) -> int | None:
            try:
                return headers.index(name) + 1
            except ValueError:
                return None

        status_col = col_index("status")
        url_col = col_index("published_url")
        date_col = col_index("published_at")

        if status_col:
            sheet.update_cell(target_row, status_col, "published")
        if url_col:
            sheet.update_cell(target_row, url_col, published_url)
        if date_col:
            sheet.update_cell(target_row, date_col, now_iso)
        print(f"✅ Sheets row {target_row} updated to published")
    except Exception as e:
        print(f"⚠️  Sheets update failed: {type(e).__name__}: {e}")


# ==== Telegram notification ====

def send_telegram_published(h1_title: str, published_url: str, lang: str = "ru") -> None:
    """Notify Telegram that the article was published.

    Stage 3 i18n: includes a flag emoji so the operator can tell EN vs PT
    publications apart at a glance in the Telegram queue.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ℹ️  Telegram credentials not set, skipping notification")
        return

    flag = {"ru": "🇺🇦"}.get(lang, "🇺🇦")
    text = f"✅ *Опубликовано* {flag}\n\n*{h1_title}*\n\n{published_url}"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Telegram notification sent (status {resp.status})")
    except Exception as e:
        print(f"⚠️  Telegram send failed: {type(e).__name__}: {e}")


# ==== Main publish logic ====

def _resolve_lang_from_meta(meta: dict, cli_lang: str | None) -> str:
    """Determine which language to publish under.

    Priority:
      1. --lang CLI flag (explicit override)
      2. meta.json `lang` field (set by Stage 3 generate.py)
      3. 'en' fallback (for pre-Stage-3 _pending/ folders that lack the field)

    The CLI flag wins over meta because the operator may want to manually
    re-route an article (rare but useful for testing). A mismatch logs a
    warning so accidents are visible.
    """
    meta_lang = meta.get("lang")
    if cli_lang:
        if meta_lang and meta_lang != cli_lang:
            print(
                f"⚠️  --lang={cli_lang} overrides meta.json lang={meta_lang}. "
                f"Verify this is intentional."
            )
        return cli_lang
    return meta_lang or "ru"


def publish_article(slug: str, cli_lang: str | None = None) -> int:
    # We don't yet know the language — we have to peek into meta.json first
    # to decide which pending dir to look in. Try EN first (default), then PT.
    # If --lang was passed explicitly, only look in that lang's pending dir.
    if cli_lang:
        candidate_langs = [cli_lang]
    else:
        candidate_langs = ["ru"]

    pending_dir = None
    meta = None
    body_md_path = None
    for lang_try in candidate_langs:
        cfg_try = get_cfg(lang_try)
        candidate = cfg_try["pending_dir"] / slug
        if candidate.exists():
            pending_dir = candidate
            body_md_path = pending_dir / "body.md"
            meta_path = pending_dir / "meta.json"
            if not body_md_path.exists() or not meta_path.exists():
                print(f"❌ Missing body.md or meta.json in {pending_dir}", file=sys.stderr)
                return 1
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            break

    if pending_dir is None or meta is None:
        searched = ", ".join(str(get_cfg(l)["pending_dir"] / slug) for l in candidate_langs)
        print(f"❌ Pending article not found in any pending dir. Searched: {searched}", file=sys.stderr)
        return 1

    # Resolve final language and load its config (prompt+taxonomy must exist)
    lang = _resolve_lang_from_meta(meta, cli_lang)
    try:
        # Only validate taxonomy file exists — system_prompt isn't needed at
        # publish time, but the centralized validator checks both. We catch
        # the system_prompt error specifically because it's harmless here.
        if not get_cfg(lang)["taxonomy"].exists():
            print(
                f"❌ Taxonomy for lang={lang!r} not found at {get_cfg(lang)['taxonomy']}.",
                file=sys.stderr,
            )
            return 1
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    lang_cfg = get_cfg(lang)
    print(f"ℹ️  Publishing as lang={lang!r} from {pending_dir}")

    md_text = body_md_path.read_text(encoding="utf-8")

    # Validate required fields. `faq` is NOT hard-required: an article without
    # FAQ still produces valid HTML (render_faq_html / render_faq_jsonld both
    # no-op on empty faq). Failing the whole run over a missing FAQ is too
    # brittle — it aborts the entire multilang publish (and blocks the git
    # commit) even after the other language already succeeded. So we require
    # only the truly essential fields and warn (not fail) when faq is absent.
    required = {"slug", "meta_title", "meta_description", "h1_title"}
    missing = required - meta.keys()
    if missing:
        print(f"❌ meta.json missing required fields: {missing}", file=sys.stderr)
        return 1
    if not meta.get("faq"):
        print("⚠️  meta.json has no 'faq' — publishing without FAQ block/FAQPage schema. "
              "Add faq to restore FAQ rich-snippets for this article.", file=sys.stderr)

    # ==== Slug split (July 2026): decouple pending-dir slug from URL slug ====
    #
    # `slug` (arg) — name of the _pending/ directory and Telegram callback_data
    #                token. Capped at 50 chars so Telegram inline buttons work.
    # `publish_slug` — what actually ends up in the URL and the blog/ directory.
    #                Uses meta["url_slug"] when generate.py wrote it (new posts),
    #                otherwise falls back to the pending-dir slug (old posts,
    #                pre-July-2026). This backward compat means old _pending/
    #                content keeps publishing correctly with no data migration.
    #
    # From this point on, use `publish_slug` for anything that touches the
    # published site (target_dir, canonical URL, related-articles lookup key
    # against blog/, hreflang alternates, sitemap entry). Continue to use
    # `slug` only for reading from `pending_dir` and cleaning it up at the end.
    publish_slug = meta.get("url_slug") or slug
    if publish_slug != slug:
        print(f"ℹ️  Publishing with url_slug={publish_slug!r} (pending-dir slug={slug!r})")

    # ==== Inline-link validation ====
    # Stage 3 i18n: validation uses this language's taxonomy and url_prefix.
    # Cross-language links (PT article → /blog/...) are flagged invalid.
    valid_links, invalid_links = validate_inline_links(
        md_text,
        taxonomy_path=lang_cfg["taxonomy"],
        url_prefix=lang_cfg["url_prefix"],
    )
    if invalid_links:
        print(f"⚠️  Found {len(invalid_links)} invalid internal link(s):")
        for url in invalid_links:
            print(f"     {url}  →  stripping <a> wrapper, keeping anchor text")
        md_text = strip_invalid_links(md_text, invalid_links)
        # Persist the cleaned markdown so the source matches what we publish
        body_md_path.write_text(md_text, encoding="utf-8")

    final_link_count = count_internal_links(md_text)
    print(f"ℹ️  Final internal-link count: {final_link_count}")
    if final_link_count < 3:
        print(f"⚠️  Article is publishing with {final_link_count} internal links "
              f"(target: 3-5). Consider adding more before promoting.")

    # Determine dates
    generated_at = meta.get("generated_at", "")
    date_published = date_to_iso_date(generated_at) if generated_at else datetime.now(timezone.utc).date().isoformat()
    today_iso = datetime.now(timezone.utc).date().isoformat()
    date_modified = today_iso

    # Lede
    lede_plain, body_md_without_lede = extract_lede(md_text)

    # Headings + TOC
    headings = extract_h2_headings(body_md_without_lede)
    toc_html = render_toc(headings)

    # Render markdown body to HTML
    body_html = render_markdown(body_md_without_lede)
    body_html = add_h2_anchor_ids(body_html, headings)
    # Auto-upgrade structural patterns (comparison tables, step lists) into
    # branded infographics so every published article looks polished.
    body_html = enhance_body_html(body_html, lang)

    # Reading time
    word_count = meta.get("word_count") or len(md_text.split())
    reading_time = calculate_reading_time(word_count)

    # FAQ
    faq = meta.get("faq", [])
    faq_html = render_faq_html(faq, lang)
    faq_jsonld = render_faq_jsonld(faq)

    # Key takeaways
    key_takeaways_html = render_key_takeaways(meta, md_text)

    # Last updated block (only show if different from publish date) — localized
    last_updated_block = ""
    if date_modified != date_published:
        last_updated_label = lang_cfg["ui"]["last_updated"]
        last_updated_block = (
            f'<span>·</span><span class="updated">'
            f'{last_updated_label} {format_date_human(date_modified, lang)}</span>'
        )

    # CTA keyword (extract from meta_title or topic_row_data primary_keyword)
    topic_data = meta.get("topic_row_data", {})
    primary_kw = topic_data.get("primary_keyword", "")
    cta_keyword = primary_kw if primary_kw else (
        {"ru": "рейкбек"}.get(lang, "рейкбек")
    )

    # Article section: localized via lang_cfg
    target_page = topic_data.get("target_page", "")
    default_section = {"ru": "Рейкбек и сделки"}.get(lang, "Рейкбек и сделки")
    article_section = lang_cfg["article_section_map"].get(target_page, default_section)

    # ── Определяем партнёра статьи по target_page (для виджета/CTA) ──────────
    # Единая точка правды — partners.js (id ↔ url). Здесь дублируем только
    # соответствие url→id, чтобы сгенерировать мета-теги. Если target_page —
    # страница конкретного партнёра, статья получит его боковой виджет,
    # мобильную панель и CTA на его страницу. Если target_page = каталог (/ua/)
    # или пусто (обзор/сравнение) — партнёра нет, виджет/панель не появятся
    # (это правильно: в нейтральном сравнении не пушим одного партнёра).
    # ── Партнёры статьи: масштабируемо, по тегам-платформам + network ────────
    # Единая точка правды о партнёрах — partners.js. Здесь дублируем МИНИМУМ
    # (id, network, url, есть ли рейкбек) только чтобы сгенерировать мета-теги
    # и CTA. Добавляешь партнёра → дописываешь одну строку сюда и объект в
    # partners.js. Логика ниже сама разложит его по статьям.
    _tags = meta.get("tags", []) or []  # теги статьи (platform:*, topic:*, ...)
    _PARTNERS_META = [
        # id,          network,     url,                      rake(%|None)
        ("pokerbet",   "pokerbet",  "/ua/rooms/pokerbet/",    None),
        ("klubok",     "clubgg",    "/ua/clubs/klubok/",      40),
    ]
    # платформенные теги статьи: и id-партнёров, и сети (platform:clubgg и т.п.)
    _plat_tags = [t.split(":", 1)[1] for t in (_tags or [])
                  if t.startswith("platform:")]
    _topic_tags = [t.split(":", 1)[1] for t in (_tags or [])
                   if t.startswith("topic:")]
    is_comparison = "comparison" in _topic_tags

    # Разрешаем партнёров: приоритет — точное совпадение id, иначе по сети.
    _by_id = [pm for pm in _PARTNERS_META if pm[0] in _plat_tags]
    _by_net = [pm for pm in _PARTNERS_META if pm[1] in _plat_tags]
    _resolved = _by_id if _by_id else _by_net

    # Строим мета-теги платформ (их читает partners.js для виджета/панели).
    # Всегда пишем kozyr:platforms из platform-тегов — JS сам решит по network.
    _platforms_attr = ",".join(_plat_tags)

    partner_id = ""
    partner_url = ""
    if is_comparison:
        # Сравнение — обе (все) карточки, панель на мобильном не показываем.
        partner_meta_block = '<meta name="kozyr:compare" content="all">'
        partner_widget_block = '<div class="toc-side__widget" data-partner-widget></div>'
    elif len(_resolved) == 1:
        # Ровно один партнёр (обзор партнёра ИЛИ общая статья про сеть с одним
        # партнёром в ней) → его карточка + персональный CTA + мобильная панель.
        partner_id, _net, partner_url, _rake = _resolved[0]
        _meta_lines = []
        if _platforms_attr:
            _meta_lines.append('<meta name="kozyr:platforms" content="%s">'
                               % escape_html(_platforms_attr))
        # для обратной совместимости и панели — явный partner+target
        _meta_lines.append('<meta name="kozyr:partner" content="%s">' % escape_html(partner_id))
        _meta_lines.append('<meta name="kozyr:target" content="%s">' % escape_html(partner_url))
        partner_meta_block = "\n".join(_meta_lines)
        partner_widget_block = '<div class="toc-side__widget" data-partner-widget></div>'
    elif len(_resolved) > 1:
        # Несколько партнёров по сети (общая статья про платформу) → все карточки,
        # но БЕЗ персонального CTA (не выделяем одного) и без мобильной панели.
        partner_meta_block = ('<meta name="kozyr:platforms" content="%s">'
                              % escape_html(_platforms_attr)) if _platforms_attr else ""
        partner_widget_block = '<div class="toc-side__widget" data-partner-widget></div>'
    else:
        # Ни партнёра, ни сети, ни сравнения — инфо-статья.
        partner_meta_block = ""
        partner_widget_block = ""

    # CTA: если есть ОДИН партнёр — кнопка на его страницу; иначе на каталог.
    cta_button_url = partner_url if partner_url else lang_cfg["home_url"]

    # ── Собираем финальный CTA-блок ─────────────────────────────────────────
    # Для партнёрской статьи — персональный призыв на страницу партнёра, без
    # поискового ключа. Для обзора/сравнения — generic на каталог (как раньше).
    _partner_names = {"pokerbet": "PokerBet", "klubok": "KlubOk"}
    _partner_rake = {"pokerbet": None, "klubok": 40}  # None = без рейкбека (бонусы)
    ui_cta = lang_cfg["ui"]
    if partner_id:
        _pname = _partner_names.get(partner_id, "")
        _prake = _partner_rake.get(partner_id)
        # Локализованные шаблоны персонального CTA
        if lang == "uk":
            if _prake:
                _cta_h = "Готовий почати грати в %s з рейкбеком до %d%%?" % (_pname, _prake)
                _cta_p = "Забирай актуальний Club ID і контакт агента, підключайся до додатку та грай на м'яких полях із розрахунками в гривні."
            else:
                _cta_h = "Готовий почати грати в %s?" % _pname
                _cta_p = "Реєструйся, забирай вітальний бонус і грай на гривні з ліцензованим покер-румом."
            _cta_btn = "Перейти на %s" % _pname
        else:
            if _prake:
                _cta_h = "Готов начать играть в %s с рейкбеком до %d%%?" % (_pname, _prake)
                _cta_p = "Забирай актуальный Club ID и контакт агента, подключайся к приложению и играй на мягких полях с расчётами в гривне."
            else:
                _cta_h = "Готов начать играть в %s?" % _pname
                _cta_p = "Регистрируйся, забирай приветственный бонус и играй на гривны с лицензированным покер-румом."
            _cta_btn = "Перейти на %s" % _pname
        final_cta_block = (
            '<div class="final-cta">\n'
            '        <h2>%s</h2>\n'
            '        <p>%s</p>\n'
            '        <a href="%s" rel="sponsored" class="btn btn-primary">%s <span>&rarr;</span></a>\n'
            '      </div>'
        ) % (escape_html(_cta_h), escape_html(_cta_p),
             escape_html(cta_button_url), escape_html(_cta_btn))
    else:
        # generic CTA на каталог (без поискового ключа — просто про подбор сделки)
        final_cta_block = (
            '<div class="final-cta">\n'
            '        <h2>%s%s</h2>\n'
            '        <p>%s</p>\n'
            '        <a href="%s" class="btn btn-primary">%s <span>&rarr;</span></a>\n'
            '      </div>'
        ) % (escape_html(ui_cta["cta_heading_prefix"].strip()),
             escape_html(ui_cta["cta_heading_suffix"].strip().lstrip()),
             escape_html(ui_cta["cta_paragraph"]),
             escape_html(lang_cfg["home_url"]),
             escape_html(ui_cta["cta_button"]))

    # Короткий тег для пилюли в hero (из topic-тега, иначе — раздел).
    # (_tags уже определён выше, в блоке партнёров)
    _topic = next((t.split(":", 1)[1] for t in _tags if t.startswith("topic:")), "")
    _tag_labels = {
        "rakeback": "Рейкбек", "clubs": "Клубы", "rooms": "Румы",
        "comparison": "Сравнение", "bankroll": "Банкролл", "strategy": "Стратегия",
        "payments": "Платежи", "legal": "Легальность",
    }
    article_tag = _tag_labels.get(_topic, article_section.split()[0] if article_section else "Блог")

    # Keywords for schema
    secondary_kw = topic_data.get("secondary_keywords", "")
    all_keywords = ", ".join(filter(None, [primary_kw, secondary_kw]))

    # Related articles (uses this language's blog dir + taxonomy).
    # `render_related` filters "the current article" out of the list by slug,
    # and the list comes from blog/ (published names) — so we must pass the
    # published slug, not the pending-dir one.
    current_tags = meta.get("tags", [])
    all_posts = get_existing_blog_posts(lang_cfg["blog_dir"], lang_cfg["taxonomy"])
    related_html = render_related(publish_slug, all_posts, lang_cfg, current_tags=current_tags)

    # Read template
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    # Substitute placeholders
    h1_title = meta["h1_title"]
    meta_title = meta["meta_title"]
    meta_description = meta["meta_description"]
    canonical_url = canonical_url_for_slug(lang_cfg, publish_slug)

    # ----- Hero image -----
    target_dir = lang_cfg["blog_dir"] / publish_slug
    target_dir.mkdir(parents=True, exist_ok=True)

    pending_hero = pending_dir / meta.get("hero_filename", "hero.webp")
    has_hero = bool(meta.get("has_hero_image")) and pending_hero.exists()

    if has_hero:
        published_hero = target_dir / pending_hero.name
        shutil.copy2(pending_hero, published_hero)
        # og:image / twitter:image must be a JPEG (social crawlers don't render WebP).
        # Prefer a hero.jpg written by image_gen; if it's absent (pre-seeded or
        # legacy hero, hero produced by another path), derive one from the hero
        # itself at publish time so OG ALWAYS points at a JPEG.
        published_jpg = target_dir / "hero.jpg"
        pending_hero_jpg = pending_dir / "hero.jpg"
        og_image_name = pending_hero.name  # fallback (only if conversion fails)
        if pending_hero_jpg.exists():
            shutil.copy2(pending_hero_jpg, published_jpg)
            og_image_name = "hero.jpg"
        else:
            try:
                from PIL import Image
                with Image.open(published_hero) as _im:
                    _im.convert("RGB").save(published_jpg, format="JPEG", quality=86, optimize=True)
                og_image_name = "hero.jpg"
                print(f"🖼️  Derived hero.jpg from {published_hero.name} for OG/social")
            except Exception as e:
                print(f"⚠️  Could not derive hero.jpg ({type(e).__name__}: {e}); OG uses {pending_hero.name}")
        og_image_url = f"{canonical_url}{og_image_name}"
        og_image_width = "1536"
        og_image_height = "1024"
        # Localized alt text
        alt_prefix = {"ru": "Иллюстрация к статье:"}.get(lang, "Иллюстрация к статье:")
        og_image_alt = f"{alt_prefix} {h1_title}"
        hero_media_block = (
            f'<div class="post-hero__media">'
            f'<img src="{pending_hero.name}" alt="{escape_html(og_image_alt)}" '
            f'width="1536" height="1024" loading="eager" fetchpriority="high">'
            f'</div>'
        )
        print(f"✅ Hero image copied: {published_hero}")
    else:
        og_image_url = f"{SITE_URL}/og-image.png"
        og_image_width = "1200"
        og_image_height = "630"
        og_image_alt = h1_title
        hero_media_block = ""
        print("ℹ️  No hero image — dark hero with suit pattern only")

    # ----- hreflang block (only if this article has a paired translation) -----
    # Uses publish_slug so alternates point at the actual on-site URL.
    # `translation_of` still refers to the paired article's pending-dir slug,
    # because that's how generate.py records the pairing; build_hreflang_block
    # resolves it against the other language's taxonomy at emit time.
    translation_of = meta.get("translation_of")
    hreflang_block = build_hreflang_block(
        lang=lang,
        slug=publish_slug,
        translation_of=translation_of,
    )

    # ----- Format-page URLs for footer (per language) -----
    # v2 multilang: prefix берётся из lang_cfg.home_url, который правильно
    # настроен для каждого языка ("/ua/" для ru, "/ua/uk/" для uk и т.д.).
    # Убираем trailing slash чтобы конкатенация была корректной.
    _lang_prefix = lang_cfg["home_url"].rstrip("/") or "/ua"
    # KOZYR: футерные ссылки ведут на реальные страницы сайта.
    # Имена плейсхолдеров исторические (NLH/PLO/SHORT_DECK), значения — KOZYR.
    # Для uk-версии пока показываем те же страницы (главная страна одна),
    # это нормально: свитчер и hreflang правильно расставят.
    _rooms_prefix = "/ua"  # каталог/rooms/clubs пока живёт только на /ua
    nlh_url = f"{_rooms_prefix}/rooms/pokerbet/"   # PokerBet
    plo_url = f"{_rooms_prefix}/clubs/klubok/"     # KlubOk
    short_deck_url = f"{_rooms_prefix}/#compare"   # Сравнение

    # ----- in-language full URLs for breadcrumb JSON-LD -----
    home_url_full = f"{SITE_URL}{lang_cfg['home_url']}"
    blog_url_full = f"{SITE_URL}{lang_cfg['blog_url']}"
    # /about/ for EN, /pt/about/ for PT — used in author Person schema
    about_url_full = f"{SITE_URL}{lang_cfg['home_url']}about/"

    # ----- in-language code for JSON-LD inLanguage (BCP-47) -----
    in_language = {"ru": "ru-UA"}.get(lang, "ru-UA")

    ui = lang_cfg["ui"]

    # v2 multilang: если у этой статьи есть перевод — переключатель ведёт
    # на конкретную статью-перевод, а не на дефолтную главную из UI.
    # Порядок: любой первый переведённый язык (обычно один — противоположный).
    lang_switch_url = ui["lang_switcher_target_url"]
    lang_switch_hreflang = lang_cfg["hreflang_alt"]
    if isinstance(translation_of, dict) and translation_of:
        # Берём первый доступный перевод (для стран с двумя языками — тот и есть)
        other_lang, other_slug = next(iter(translation_of.items()))
        try:
            other_cfg = get_cfg(other_lang)
            lang_switch_url = f"{other_cfg['url_prefix']}/{other_slug}/"
            lang_switch_hreflang = other_cfg["hreflang_self"]
        except (ValueError, KeyError):
            # если lang неизвестен — оставляем дефолт из UI
            pass

    # Значения для «текущего» языка (активная кнопка переключателя).
    _lang_self_label = {"ru": "RU", "uk": "UA"}.get(lang, "RU")
    _lang_switch_label = {"ru": "UA", "uk": "RU"}.get(lang, "UA")
    _lang_self_url = canonical_url  # сама себя
    _lang_self_code = "ru" if lang == "ru" else "uk"
    _lang_switch_code = "uk" if lang == "ru" else "ru"
    # Build the inline i18n block for kozyr-fab.js. PT pages declare
    # window.KozyrI18n with localized strings; EN pages emit nothing
    # (the FAB script's English defaults take over).
    #
    # We do this inline rather than via a separate JS file so the strings
    # land in the HTML <body> at parse time — no extra round-trip, no FOUC
    # where the FAB briefly flashes English on a PT page before localization.
    # Non-English pages (pt, zh) declare window.KozyrI18n with localized
    # FAB strings; English pages emit nothing (the FAB script's English
    # defaults take over).
    if lang in ("ru",) and TELEGRAM_ENABLED:
        kozyr_i18n_block = (
            '<script>window.KozyrI18n = {'
            f'"fabLabel":"{escape_json_string(ui["fab_label"])}",'
            f'"fabAria":"{escape_json_string(ui["fab_aria"])}",'
            f'"toastMsg":"{escape_json_string(ui["fab_toast"])}",'
            f'"tgMsg":"{escape_json_string(ui["fab_tg_msg"])}"'
            '};</script>'
        )
    else:
        kozyr_i18n_block = ""

    replacements = {
        # Core content
        "{{META_TITLE}}": escape_html(meta_title),
        "{{META_DESCRIPTION}}": escape_html(meta_description),
        "{{META_DESCRIPTION_JSON}}": escape_json_string(meta_description),
        "{{CANONICAL_URL}}": canonical_url,
        "{{PARTNER_META_BLOCK}}": partner_meta_block,
        "{{PARTNER_WIDGET_BLOCK}}": partner_widget_block,
        "{{FINAL_CTA_BLOCK}}": final_cta_block,
        "{{H1_TITLE}}": escape_html(h1_title),
        "{{H1_TITLE_JSON}}": escape_json_string(h1_title),
        "{{LEDE}}": escape_html(lede_plain),
        "{{DATE_PUBLISHED}}": date_published,
        "{{DATE_MODIFIED}}": date_modified,
        "{{DATE_PUBLISHED_DISPLAY}}": format_date_human(date_published, lang),
        "{{LAST_UPDATED_BLOCK}}": last_updated_block,
        "{{READING_TIME}}": str(reading_time),
        "{{WORD_COUNT}}": str(word_count),
        "{{ARTICLE_SECTION}}": article_section,
        "{{KEYWORDS_JSON}}": escape_json_string(all_keywords),
        "{{KEY_TAKEAWAYS_BLOCK}}": key_takeaways_html,
        "{{ARTICLE_BODY_HTML}}": body_html,
        "{{FAQ_BLOCK}}": faq_html,
        "{{FAQ_JSONLD}}": faq_jsonld,
        "{{CTA_KEYWORD}}": escape_html(cta_keyword),
        "{{RELATED_ARTICLES_HTML}}": related_html,
        "{{OG_IMAGE_URL}}": og_image_url,
        "{{OG_IMAGE_WIDTH}}": og_image_width,
        "{{OG_IMAGE_HEIGHT}}": og_image_height,
        "{{OG_IMAGE_ALT}}": escape_html(og_image_alt),
        "{{HERO_MEDIA_BLOCK}}": hero_media_block,
        "{{ARTICLE_TAG}}": escape_html(article_tag),
        "{{UI_IN_THIS_ARTICLE}}": ui.get("in_this_article", "В этой статье"),
        # ----- Stage 3 i18n placeholders -----
        "{{HTML_LANG}}": lang_cfg["html_lang"],
        "{{OG_LOCALE}}": lang_cfg["og_locale"],
        "{{OG_LOCALE_ALT}}": lang_cfg["og_locale_alt"],
        "{{HREFLANG_BLOCK}}": hreflang_block,
        "{{IN_LANGUAGE}}": in_language,
        "{{HOME_URL}}": lang_cfg["home_url"],
        "{{BLOG_URL}}": lang_cfg["blog_url"],
        "{{HOME_URL_FULL}}": home_url_full,
        "{{BLOG_URL_FULL}}": blog_url_full,
        "{{SITE_ORIGIN}}": SITE_URL,
        "{{ABOUT_URL}}": about_url_full,
        "{{NLH_URL}}": nlh_url,
        "{{PLO_URL}}": plo_url,
        "{{SHORT_DECK_URL}}": short_deck_url,
        # v2 multilang: подставляем реальный URL перевода если он есть,
        # иначе дефолт из UI (главная соседнего языка).
        "{{LANG_SWITCH_URL}}": lang_switch_url,
        "{{LANG_SWITCH_HREFLANG}}": lang_switch_hreflang,
        "{{LANG_SELF_URL}}": _lang_self_url,
        "{{HREFLANG_SELF}}": lang_cfg["hreflang_self"],
        "{{LANG_SELF}}": _lang_self_code,
        "{{LANG_SWITCH_LANG}}": _lang_switch_code,
        "{{UI_LANG_SELF_LABEL}}": _lang_self_label,
        # UI strings (localized chrome — header, footer, breadcrumb, CTA, related)
        "{{UI_BREADCRUMB_HOME}}": ui["breadcrumb_home"],
        "{{UI_BREADCRUMB_BLOG}}": ui["breadcrumb_blog"],
        "{{UI_ARTICLE_EYEBROW}}": ui["article_eyebrow"],
        "{{UI_MIN_READ}}": ui["min_read"],
        "{{UI_BY_AUTHOR}}": ui["by_author"],
        "{{UI_AUTHOR_WRITTEN_BY}}": ui["author_written_by"],
        "{{UI_AUTHOR_ROLE}}": ui["author_role"],
        "{{UI_AUTHOR_BIO}}": ui["author_bio"],
        "{{UI_AUTHOR_NAME}}": ui.get("author_name", "Никита Волошин"),
        "{{UI_CTA_HEADING_PREFIX}}": ui["cta_heading_prefix"],
        "{{UI_CTA_HEADING_SUFFIX}}": ui["cta_heading_suffix"],
        "{{UI_CTA_PARAGRAPH}}": ui["cta_paragraph"],
        "{{UI_CTA_BUTTON}}": ui["cta_button"],
        "{{UI_RELATED_EYEBROW}}": ui["related_eyebrow"],
        "{{UI_RELATED_HEADING_PREFIX}}": ui["related_heading_prefix"],
        "{{UI_RELATED_HEADING_EM}}": ui["related_heading_em"],
        "{{UI_NAV_AI_BOT}}": ui["nav_ai_bot"],
        "{{UI_NAV_HOW}}": ui["nav_how"],
        "{{UI_NAV_FEATURES}}": ui["nav_features"],
        "{{UI_NAV_COMPARE}}": ui["nav_compare"],
        "{{UI_NAV_CASES}}": ui["nav_cases"],
        "{{UI_NAV_REVIEWS}}": ui["nav_reviews"],
        "{{UI_NAV_PRICING}}": ui["nav_pricing"],
        "{{UI_HEADER_CTA}}": ui["header_cta"],
        "{{UI_LANG_SWITCH_LABEL}}": _lang_switch_label,
        "{{UI_LANG_SWITCH_ARIA}}": ui["lang_switcher_aria"],
        "{{UI_FOOTER_TAGLINE}}": ui["footer_tagline"],
        "{{UI_FOOTER_PRODUCT_H}}": ui["footer_product_h"],
        "{{UI_FOOTER_COMPANY_H}}": ui["footer_company_h"],
        "{{UI_FOOTER_LINK_AI_BOT}}": ui["footer_link_ai_bot"],
        "{{UI_FOOTER_LINK_NLH}}": ui["footer_link_nlh"],
        "{{UI_FOOTER_LINK_PLO}}": ui["footer_link_plo"],
        "{{UI_FOOTER_LINK_SHORT_DECK}}": ui["footer_link_short_deck"],
        "{{UI_FOOTER_LINK_COMPARE}}": ui["footer_link_compare"],
        "{{UI_FOOTER_LINK_CASES}}": ui["footer_link_cases"],
        "{{UI_FOOTER_LINK_REVIEWS}}": ui["footer_link_reviews"],
        "{{UI_FOOTER_LINK_PRICING}}": ui["footer_link_pricing"],
        "{{UI_FOOTER_COPYRIGHT}}": ui["footer_copyright"],
        # ----- Mobile UX (added 2026-07-17) -----
        "{{UI_SKIP_LINK}}": ui["skip_link"],
        "{{UI_BURGER_ARIA}}": ui["burger_aria"],
        "{{UI_NAV_ARIA}}": ui["nav_aria"],
        # ----- FAB i18n block (PT only; EN gets empty string) -----
        "{{KOZYR_I18N_BLOCK}}": kozyr_i18n_block,
    }

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    # Write blog/{slug}/index.html (or pt/blog/{slug}/index.html)
    target_file = target_dir / "index.html"
    target_file.write_text(rendered, encoding="utf-8")
    print(f"✅ Wrote {target_file}")

    # ==== Update language taxonomy with the newly published article ====
    # Taxonomy keys articles by the on-site directory name (see how
    # get_existing_blog_posts iterates blog_dir.iterdir() and uses child.name),
    # so we key by publish_slug — NOT the pending-dir slug.
    upsert_taxonomy_article(
        slug=publish_slug,
        title=h1_title,
        description=meta_description,
        tags=meta.get("tags", []),
        taxonomy_path=lang_cfg["taxonomy"],
    )

    # Update sitemap (with hreflang pair if translation_of is set) and blog index.
    # Both write URLs, so they need publish_slug.
    update_sitemap(
        publish_slug, today_iso, lang_cfg, translation_of=translation_of,
        image_url=(og_image_url if has_hero else None),
        image_title=(h1_title if has_hero else None),
    )
    update_blog_index(publish_slug, h1_title, meta_description, date_published, reading_time, lang_cfg, article_tag, has_hero)

    # Refresh Related blocks on this language's format pages
    refreshed_posts = get_existing_blog_posts(lang_cfg["blog_dir"], lang_cfg["taxonomy"])
    update_format_page_related_blocks(refreshed_posts, lang_cfg)

    # Remove _pending/{slug}/ (or _pending_pt/{slug}/)
    # Uses the pending-dir slug, not publish_slug: the pending dir was
    # created with the short name and that's what we need to remove.
    shutil.rmtree(pending_dir)
    print(f"✅ Removed {pending_dir}")

    # Update Sheets — pass the original topic from meta.json::topic_row_data
    # so update_sheet_status can match the row exactly. The pending dir was
    # just removed above, but we still have `meta` in memory from earlier.
    # Sheet "slug" column stores the on-site slug (that's what the operator
    # actually links to and searches for), so we send publish_slug.
    sheets_topic = meta.get("topic_row_data", {}).get("topic")
    update_sheet_status(publish_slug, canonical_url, topic=sheets_topic)

    # Bot v2: если тема пришла из строки Sheets через generate-from-row.yml,
    # проставляем status=done по номеру строки (более надёжно, чем поиск по topic).
    _mark_source_row_done(meta)

    # Note: IndexNow ping happens AFTER Netlify deploy in the workflow,
    # not here. We only update files here.

    print(f"\n✅ Article ready for commit: {canonical_url}")
    return 0


def _mark_source_row_done(meta: dict) -> None:
    """Bot v2: если в meta.json есть source_row (положено generate.py, когда
    тема пришла из строки Sheets через generate-from-row.yml), после
    успешной публикации проставляем этой строке status=done.
    Отдельная функция потому, что update_sheet_status ищет по slug/topic —
    в этом сценарии надёжнее прямо по row_index."""
    row = meta.get("source_row")
    if not row:
        return
    try:
        from bot_v2.suggested_topics import update_status
        update_status(int(row), "done")
        print(f"✅ Строка {row} в Google Sheets → status=done")
    except Exception as e:
        # Не валим публикацию, если Sheets недоступен — статья уже вышла.
        print(f"⚠️  Не удалось перевести строку {row} в done: {type(e).__name__}: {e}")


# ==== Post-deploy steps (called separately after Netlify deploy) ====

def run_post_deploy(slug: str, h1_title: str, lang: str = "ru") -> int:
    """Steps that run AFTER Netlify finishes deploying the new article.

    Stage 3 i18n: requires --lang to know which canonical URL to ping.
    Defaults to 'en' for backward compat with pre-Stage-3 workflow runs.
    """
    lang_cfg = get_cfg(lang)
    canonical_url = canonical_url_for_slug(lang_cfg, slug)
    ping_indexnow(slug, lang_cfg)
    send_telegram_published(h1_title, canonical_url, lang=lang)
    return 0


# ==== CLI ====

def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a pending article")
    parser.add_argument("--slug", required=True, help="Slug of the pending article")
    parser.add_argument(
        "--lang",
        choices=["ru"],
        default=None,
        help="Article language. If omitted, publish.py auto-detects from "
             "meta.json (Stage 3+ articles) or falls back to 'en' (legacy).",
    )
    parser.add_argument("--post-deploy", action="store_true",
                        help="Run only post-deploy steps (IndexNow + Telegram)")
    parser.add_argument("--title", default="",
                        help="H1 title (used in --post-deploy mode)")
    args = parser.parse_args()

    if args.post_deploy:
        return run_post_deploy(args.slug, args.title, lang=args.lang or "ru")
    return publish_article(args.slug, cli_lang=args.lang)


if __name__ == "__main__":
    sys.exit(main())
