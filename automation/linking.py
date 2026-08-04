"""
Internal linking engine for KOZYR content.

Responsibilities:
- Load taxonomy (format pages + articles, each with tags)
- Score topical relevance between any two pages using Jaccard + recency
- Pick top-N related items for an article or format page
- Build natural-language list of available articles to feed into the
  generation prompt (so Claude doesn't invent slugs)
- Validate inline links in generated markdown

This module is imported by both generate.py and publish.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ==== Config ====

DEFAULT_TAXONOMY_PATH = Path(__file__).parent / "taxonomy.json"

# Scoring weights
TAG_SIMILARITY_WEIGHT = 0.7
RECENCY_WEIGHT = 0.3

# Recency: articles older than this lose their boost entirely
RECENCY_HALF_LIFE_DAYS = 120  # ~4 months


# ==== Loading ====

# Cache keyed by resolved path so EN and PT taxonomies don't collide.
# Stage 3 i18n: each language has its own taxonomy file, and publish.py /
# generate.py pass `taxonomy_path` to the loaders.
_taxonomy_cache: dict[str, dict] = {}


def load_taxonomy(taxonomy_path: Path | None = None) -> dict:
    """Load a taxonomy file once and cache. Raises if file missing or malformed.

    `taxonomy_path` defaults to the EN taxonomy for backward compatibility
    with code that pre-dates Stage 3 i18n (notably backfill_related.py and
    the linking.py self-test). New callers should pass an explicit path
    derived from lang_config.
    """
    path = taxonomy_path if taxonomy_path is not None else DEFAULT_TAXONOMY_PATH
    key = str(path.resolve())
    if key in _taxonomy_cache:
        return _taxonomy_cache[key]
    if not path.exists():
        raise RuntimeError(
            f"taxonomy file not found at {path}. "
            f"This file is required for related-link generation."
        )
    with path.open(encoding="utf-8") as f:
        _taxonomy_cache[key] = json.load(f)
    return _taxonomy_cache[key]


def reload_taxonomy(taxonomy_path: Path | None = None) -> dict:
    """Force reload — useful in tests and when running scripts back-to-back."""
    path = taxonomy_path if taxonomy_path is not None else DEFAULT_TAXONOMY_PATH
    key = str(path.resolve())
    _taxonomy_cache.pop(key, None)
    return load_taxonomy(path)


def get_article_tags(slug: str, taxonomy_path: Path | None = None) -> list[str]:
    """Return tags for a given article slug, or [] if unknown."""
    tax = load_taxonomy(taxonomy_path)
    entry = tax.get("articles", {}).get(slug)
    if not entry:
        return []
    return list(entry.get("tags", []))


def get_format_page_tags(url: str, taxonomy_path: Path | None = None) -> list[str]:
    """Return tags for a format page like /nlh-bots/, or [] if unknown."""
    tax = load_taxonomy(taxonomy_path)
    entry = tax.get("format_pages", {}).get(url)
    if not entry:
        return []
    return list(entry.get("tags", []))


def get_all_article_slugs(taxonomy_path: Path | None = None) -> list[str]:
    """All known article slugs from taxonomy."""
    tax = load_taxonomy(taxonomy_path)
    return list(tax.get("articles", {}).keys())


def get_format_pages(taxonomy_path: Path | None = None) -> dict[str, dict]:
    """Return {url: entry} for all format pages."""
    tax = load_taxonomy(taxonomy_path)
    return dict(tax.get("format_pages", {}))


# ==== Scoring ====

def jaccard_similarity(tags_a: list[str], tags_b: list[str]) -> float:
    """Standard Jaccard: |intersection| / |union|. 0..1."""
    set_a, set_b = set(tags_a), set(tags_b)
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def recency_boost(date_iso: str, today: datetime | None = None) -> float:
    """
    Exponential decay from publish date. Returns 0..1.
    Today = 1.0, RECENCY_HALF_LIFE_DAYS old = 0.5, much older = ~0.
    """
    if not date_iso:
        return 0.5  # neutral default for unknown dates
    try:
        if "T" in date_iso:
            dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.5
    if today is None:
        today = datetime.now(timezone.utc)
    age_days = max(0, (today - dt).days)
    # Half-life decay: 2 ** (-age / half_life)
    return 2 ** (-age_days / RECENCY_HALF_LIFE_DAYS)


def score_pair(self_tags: list[str], other_tags: list[str], other_date: str = "") -> float:
    """Combined score: tag similarity (weighted) + recency (weighted)."""
    sim = jaccard_similarity(self_tags, other_tags)
    rec = recency_boost(other_date)
    return TAG_SIMILARITY_WEIGHT * sim + RECENCY_WEIGHT * rec


# ==== Related selection ====

def pick_related_for_article(
    self_slug: str,
    self_tags: list[str],
    candidates: list[dict],
    n: int = 3,
) -> list[dict]:
    """
    Pick top-N related articles for `self_slug`.

    candidates: list of dicts with keys: slug, title, description, date, tags.
    Returns up to N items, sorted by score descending.

    Diversity rule: if 2 of the top-3 share the same primary format tag,
    we try to swap the 3rd for something with a different format. This
    keeps the Related block visually varied.
    """
    self_format = _primary_format(self_tags)
    scored = []
    for c in candidates:
        if c.get("slug") == self_slug:
            continue
        score = score_pair(self_tags, c.get("tags", []), c.get("date", ""))
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top N. Then apply diversity swap if the top-2 share a format
    # and the 3rd slot has a same-format candidate when a different-format
    # one exists with comparable score.
    if len(scored) <= n:
        return [c for _, c in scored]

    chosen = [c for _, c in scored[:n]]
    chosen_formats = [_primary_format(c.get("tags", [])) for c in chosen]
    same_format_count = sum(1 for f in chosen_formats if f == self_format and f)

    if same_format_count >= 2:
        # Look for a different-format candidate in the next 5 to swap into slot 2
        for score, cand in scored[n : n + 5]:
            cand_fmt = _primary_format(cand.get("tags", []))
            if cand_fmt and cand_fmt != self_format:
                # Replace the lowest-scoring same-format item in chosen
                for i, ch in enumerate(chosen):
                    if _primary_format(ch.get("tags", [])) == self_format:
                        chosen[i] = cand
                        break
                break

    return chosen


def pick_related_for_format_page(
    format_url: str,
    candidates: list[dict],
    n: int = 3,
    taxonomy_path: Path | None = None,
) -> list[dict]:
    """
    Pick top-N related articles for a format page (/nlh-bots/, /plo-bots/,
    /short-deck-bots/). Same scoring as articles but the 'self tags' are
    the format page's tags from taxonomy.
    """
    format_tags = get_format_page_tags(format_url, taxonomy_path=taxonomy_path)
    if not format_tags:
        return []
    scored = []
    for c in candidates:
        score = score_pair(format_tags, c.get("tags", []), c.get("date", ""))
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:n]]


def _primary_format(tags: list[str]) -> str:
    """Return the format tag (nlh/plo/short-deck) or '' if none/mixed."""
    for t in tags:
        if t.startswith("format:") and t != "format:mixed":
            return t.split(":", 1)[1]
    return ""


# ==== Prompt context ====

def build_existing_articles_context(
    taxonomy_path: Path | None = None,
    url_prefix: str = "/blog",
) -> str:
    """
    Build a markdown block listing all existing articles, to be embedded
    in the generation prompt. This stops Claude from inventing slugs.

    `url_prefix` controls the prefix shown in the prompt — "/blog" for EN
    runs, "/pt/blog" for PT runs. Format pages are taken as-is from the
    taxonomy file (PT taxonomy stores PT format URLs like /pt/nlh-bots/).

    Format:
        ## EXISTING ARTICLES (use these for inline links)

        - <url_prefix>/<slug>/ — <title> (tags: <tag1>, <tag2>)
        - ...

        ## FORMAT PAGES (always link to ONE of these matching target_page)

        - <format_url> — <title>
    """
    tax = load_taxonomy(taxonomy_path)
    articles = tax.get("articles", {})
    formats = tax.get("format_pages", {})

    lines = ["## EXISTING ARTICLES (use these slugs for inline links — do NOT invent URLs)", ""]
    # Sort articles alphabetically by slug for stable prompt
    for slug in sorted(articles.keys()):
        entry = articles[slug]
        title = entry.get("title", slug)
        tags = ", ".join(entry.get("tags", []))
        lines.append(f"- `{url_prefix}/{slug}/` — {title}  \n  tags: {tags}")
    lines.append("")
    lines.append("## FORMAT PAGES (link to the ONE matching target_page)")
    lines.append("")
    for url in sorted(formats.keys()):
        entry = formats[url]
        title = entry.get("title", url)
        lines.append(f"- `{url}` — {title}")
    return "\n".join(lines)


# ==== Inline link validation ====

LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def extract_internal_links(markdown: str) -> list[tuple[str, str]]:
    """Return [(anchor_text, url), ...] for all internal links in the markdown.
    Internal = starts with '/' (relative path)."""
    links = []
    for match in LINK_PATTERN.finditer(markdown):
        anchor, url = match.group(1), match.group(2)
        if url.startswith("/"):
            links.append((anchor, url))
    return links


def validate_inline_links(
    markdown: str,
    taxonomy_path: Path | None = None,
    url_prefix: str = "/blog",
) -> tuple[list[str], list[str]]:
    """
    Walk all internal links in the markdown.
    Returns (valid_urls, invalid_urls). Used by publish.py to surface
    broken links to the operator before publishing.

    `url_prefix` is the article URL prefix for this language ("/blog" for
    EN, "/pt/blog" for PT). Cross-language links (PT article → /blog/...)
    are flagged as invalid — Stage 3 convention is one language per article.
    """
    tax = load_taxonomy(taxonomy_path)
    valid_slugs = set(tax.get("articles", {}).keys())
    valid_format_pages = set(tax.get("format_pages", {}).keys())

    # Pattern matches both "<prefix>/<slug>" and "<prefix>/<slug>/"
    article_pattern = re.compile(rf"^{re.escape(url_prefix)}/([^/]+)/?$")

    valid, invalid = [], []
    for _, url in extract_internal_links(markdown):
        # Normalize: ensure trailing slash for comparison against format pages
        normalized = url if url.endswith("/") else url + "/"

        if normalized in valid_format_pages:
            valid.append(url)
            continue

        # Article URLs in this language: <url_prefix>/<slug>/
        article_match = article_pattern.match(url)
        if article_match:
            slug = article_match.group(1)
            if slug in valid_slugs:
                valid.append(url)
            else:
                invalid.append(url)
            continue

        # Anchor on home (#contact, #pricing, etc.) — always allow.
        # For PT, the home anchor is "/pt/" + "#..." but operators may also
        # use "/" + "#..." in source — both pass through.
        if url.startswith("/#") or url == "/" or url == "/pt/" or url.startswith("/pt/#"):
            valid.append(url)
            continue

        # Anything else (e.g. /privacy/, /terms/, /pt/privacy/) — allow but note.
        # We do NOT flag these as invalid because the page may exist in the
        # static site (not in taxonomy.json which only tracks blog content).
        valid.append(url)
    return valid, invalid


def strip_invalid_links(markdown: str, invalid_urls: list[str]) -> str:
    """Remove [anchor](url) wrappers for known-invalid urls, keeping anchor text."""
    if not invalid_urls:
        return markdown
    invalid_set = set(invalid_urls)

    def replace(match: re.Match) -> str:
        anchor, url = match.group(1), match.group(2)
        if url in invalid_set:
            return anchor  # strip the link, keep text
        return match.group(0)

    return LINK_PATTERN.sub(replace, markdown)


def count_internal_links(markdown: str) -> int:
    """Count how many internal links are in the markdown body."""
    return len(extract_internal_links(markdown))


# ==== Self-test ====

if __name__ == "__main__":
    # Smoke test when running directly.
    # Stage 3 i18n: explicitly load the EN taxonomy for the smoke test.
    # Run with PT taxonomy by setting PYTHONPATH and editing the path here.
    tax = load_taxonomy()  # default = EN
    print(f"Loaded {len(tax.get('articles', {}))} articles, {len(tax.get('format_pages', {}))} format pages")
    print()
    print("--- Existing articles context (would be sent to Claude) ---")
    print(build_existing_articles_context())
    print()
    print("--- Smoke test: pick related for off-peak-rake-growth ---")
    candidates = []
    for slug, entry in tax.get("articles", {}).items():
        candidates.append({
            "slug": slug,
            "title": entry.get("title", slug),
            "description": entry.get("summary_for_related", ""),
            "tags": entry.get("tags", []),
            "date": "2026-03-18",  # dummy date for smoke test
        })
    self_tags = get_article_tags("off-peak-rake-growth")
    related = pick_related_for_article("off-peak-rake-growth", self_tags, candidates)
    for r in related:
        print(f"  - {r['slug']} (tags: {r['tags']})")
