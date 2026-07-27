"""
Backfill script: rebuild the "Related reading" block in existing blog posts
using the new tag-aware scoring engine.

This script is OPT-IN. It does not run automatically. The operator runs it
manually when they want to rebuild Related blocks across all existing
articles using the latest taxonomy.

What it changes (per article):
  - The HTML between the "Related" section markers (or, for legacy articles
    without markers, the existing Related <section> identified heuristically)

What it does NOT change:
  - Article title, lede, body text, FAQ, schema, dates
  - Sitemap, blog index, format pages (use publish.py for those)
  - Any article that doesn't have a recognizable Related section

Usage:
    python automation/backfill_related.py            # dry run, prints what would change
    python automation/backfill_related.py --apply    # actually write changes
    python automation/backfill_related.py --apply --slug off-peak-rake-growth   # one article
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from linking import (
    load_taxonomy,
    pick_related_for_article,
    get_article_tags,
)


BLOG_DIR = Path("ua/blog")


# Patterns to find the Related section in an existing article. We try the
# new marker-based pattern first, then fall back to a heuristic that catches
# the legacy "Related <em>...</em>" heading style.

NEW_MARKER_START = "<!-- AUTO_RELATED_BLOCK_START -->"
NEW_MARKER_END = "<!-- AUTO_RELATED_BLOCK_END -->"

# Legacy pattern: matches the section that starts with a "Related ..." heading
# (the existing articles use H2 with em like "Related <em>club operations</em> articles")
# and contains a grid-3 of cards. We capture from <section> to </section>.
LEGACY_RELATED_SECTION_PATTERN = re.compile(
    r'(<section>\s*<div class="container">\s*<div class="section-head">\s*'
    r'<div class="eyebrow">[^<]*?</div>\s*'
    r'<h2>Related[^<]*(?:<em>[^<]*</em>[^<]*)?</h2>\s*'
    r'</div>\s*<div class="grid-3">.*?</div>\s*</div>\s*</section>)',
    re.DOTALL | re.IGNORECASE,
)


def get_blog_post_metadata(slug: str, blog_dir: Path = BLOG_DIR) -> dict:
    """Read minimal metadata for a single blog post: slug, title, date, tags, description."""
    idx = blog_dir / slug / "index.html"
    if not idx.exists():
        return {}
    html = idx.read_text(encoding="utf-8", errors="ignore")

    title_match = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
    title = (title_match.group(1).strip() if title_match else slug).split("|")[0].strip()

    date_match = re.search(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})', html)
    date = date_match.group(1) if date_match else ""

    # Description: prefer taxonomy summary, else fall back to JSON-LD
    tax = load_taxonomy()
    tax_entry = tax.get("articles", {}).get(slug, {})
    description = tax_entry.get("summary_for_related", "")
    if not description:
        ldjson_desc_match = re.search(
            r'"@type":\s*"BlogPosting".*?"description":\s*"((?:[^"\\]|\\.)*)"',
            html, re.DOTALL,
        )
        if ldjson_desc_match:
            description = ldjson_desc_match.group(1).encode("utf-8").decode("unicode_escape")

    tags = list(tax_entry.get("tags", []))
    return {
        "slug": slug,
        "title": title,
        "date": date,
        "description": description,
        "tags": tags,
    }


def render_related_section(chosen: list[dict], heading_em: str = "Related <em>club operations</em>") -> str:
    """Render the full <section> block (legacy-compatible markup)."""
    cards = []
    for p in chosen:
        title = (p.get("title") or p.get("slug", "")).replace("&", "&amp;")
        desc = (p.get("description") or "").replace("&", "&amp;").replace("<", "&lt;")
        cards.append(
            f'      <article class="card"><h3><a href="/ua/blog/{p["slug"]}/">'
            f'{title}</a></h3><p>{desc}</p></article>'
        )
    cards_html = "\n".join(cards)

    return (
        f'<section>\n  <div class="container">\n'
        f'    <div class="section-head">\n'
        f'      <div class="eyebrow">Related reading</div>\n'
        f'      <h2>{heading_em} articles</h2>\n'
        f'    </div>\n'
        f'    <div class="grid-3">\n{cards_html}\n    </div>\n'
        f'  </div>\n</section>'
    )


def find_related_section(html: str) -> tuple[str, int, int] | None:
    """Locate the Related section in the article HTML.

    Returns (matched_html, start_pos, end_pos) or None if no Related section
    found. We try the new marker-based pattern first, then fall back to legacy.
    """
    if NEW_MARKER_START in html and NEW_MARKER_END in html:
        start = html.find(NEW_MARKER_START)
        end = html.find(NEW_MARKER_END) + len(NEW_MARKER_END)
        return html[start:end], start, end

    legacy_match = LEGACY_RELATED_SECTION_PATTERN.search(html)
    if legacy_match:
        return legacy_match.group(1), legacy_match.start(), legacy_match.end()

    return None


def process_article(slug: str, all_posts: list[dict], dry_run: bool = True) -> bool:
    """Rebuild the Related block in one article. Returns True if changes were applied (or would be in dry-run)."""
    idx = BLOG_DIR / slug / "index.html"
    if not idx.exists():
        print(f"⚠️  {idx} does not exist, skipping")
        return False

    html = idx.read_text(encoding="utf-8")
    location = find_related_section(html)
    if not location:
        print(f"ℹ️  {slug}: no Related section found (neither markers nor legacy heading), skipping")
        return False

    matched, start, end = location

    # Pick new related set
    self_tags = get_article_tags(slug)
    if not self_tags:
        print(f"⚠️  {slug}: no tags in taxonomy, skipping (would fall back to recency, "
              f"which is the same as the legacy block)")
        return False

    chosen = pick_related_for_article(
        self_slug=slug,
        self_tags=self_tags,
        candidates=all_posts,
        n=3,
    )
    if not chosen:
        print(f"⚠️  {slug}: scoring returned 0 candidates, skipping")
        return False

    new_block = render_related_section(chosen)
    new_html = html[:start] + new_block + html[end:]

    # Show diff
    chosen_slugs = [c["slug"] for c in chosen]
    print(f"\n📝 {slug}:")
    print(f"   Self tags: {self_tags}")
    print(f"   New related: {chosen_slugs}")
    if matched.startswith(NEW_MARKER_START):
        print(f"   (using marker-based replacement)")
    else:
        print(f"   (using legacy-section replacement)")

    if not dry_run:
        idx.write_text(new_html, encoding="utf-8")
        print(f"   ✅ Written")
    else:
        print(f"   (dry run — pass --apply to write)")

    return True


def collect_all_posts() -> list[dict]:
    """Build the candidate list once: every article in blog/ with metadata."""
    posts = []
    for child in sorted(BLOG_DIR.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "index.html").exists():
            continue
        meta = get_blog_post_metadata(child.name)
        if meta:
            posts.append(meta)
    return posts


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Related-reading blocks in existing articles")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes. Without this flag, runs in dry-run mode.")
    parser.add_argument("--slug", default="",
                        help="Process only this slug. Without it, processes all articles in blog/.")
    args = parser.parse_args()

    if not BLOG_DIR.exists():
        print(f"❌ Blog directory not found: {BLOG_DIR}", file=sys.stderr)
        return 1

    print(f"Loading taxonomy and scanning blog/...")
    all_posts = collect_all_posts()
    print(f"Found {len(all_posts)} articles in blog/")

    posts_with_tags = [p for p in all_posts if p["tags"]]
    print(f"  {len(posts_with_tags)} have taxonomy tags (eligible for scoring)")
    print(f"  {len(all_posts) - len(posts_with_tags)} are missing from taxonomy.json")
    print()

    if args.slug:
        targets = [args.slug]
    else:
        targets = [p["slug"] for p in all_posts]

    if args.apply:
        print(f"🔧 APPLY mode — changes WILL be written")
    else:
        print(f"👀 DRY RUN — no files will be modified. Pass --apply to write.")
    print(f"Processing {len(targets)} article(s)...")

    changed = 0
    for slug in targets:
        if process_article(slug, all_posts, dry_run=not args.apply):
            changed += 1

    print(f"\n{'=' * 60}")
    print(f"Summary: {changed}/{len(targets)} article(s) {'updated' if args.apply else 'would be updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
