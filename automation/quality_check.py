"""
Article quality evaluation.

Two-tier evaluation:
  1. Technical (Python, instant, free) — 13 metrics: title len, word count,
     link count, schema validity, keyword usage, table format integrity, etc.
  2. Content (Claude API, ~30 sec, ~$0.03) — 15 metrics: E-E-A-T, depth,
     unique angle, readability, intent fit, cliché avoidance, etc.

Returns a combined score normalized to 0-100 (technical and content
sub-scores are each reported as percent, then averaged into combined_percent).

Used by generate.py BEFORE sending Telegram preview.

Usage as module:
    from quality_check import evaluate_article
    result = evaluate_article(article_dict, topic_dict, markdown_body, lang="en")
    # result["total"]: int 0-100
    # result["verdict"]: "PUBLISH_READY" | "GOOD" | "NEEDS_REVISION" | "REJECT"
    # result["telegram_block"]: pre-formatted text for Telegram preview
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

# Anthropic is required for content evaluation. If unavailable, content
# check returns score 0 and the technical check stands alone.
try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# Threshold for verdict mapping. Tune these as needed.
VERDICT_THRESHOLDS = {
    "PUBLISH_READY": 90,   # 90+: ship it
    "GOOD": 75,            # 75-89: ship with light warnings
    "NEEDS_REVISION": 50,  # 50-74: ship but flag issues; operator decides
    "REJECT": 0,           # <50: auto-reject (not implemented yet — see flag)
}

CONTENT_EVAL_MODEL = "claude-sonnet-4-5"
CONTENT_EVAL_MAX_TOKENS = 4000


# ==== Technical evaluation (13 metrics, 54 points) ====

def detect_broken_tables(markdown_body: str) -> dict:
    r"""
    Detect markdown tables that have all rows concatenated on a single physical
    line. python-markdown's `extra` parser requires a newline between rows;
    without that, the table renders as a paragraph of pipe characters on the
    published page.

    Returns a dict:
      {
        "tables_found": int,        # total tables detected (broken + ok)
        "broken_count": int,        # how many broken tables
        "broken_snippets": list[str]  # first 80 chars of each broken table
      }

    Detection heuristic:
      A markdown table separator (``|---|``) is meant to occupy its OWN line.
      A well-formed separator line is short and contains only ``|``, ``-``,
      ``:``, and whitespace. If a line contains a separator token AND also
      contains cell content (anything other than the separator characters),
      the rows have been concatenated and the table is broken.

      Well-formed tables are counted by detecting blocks where a separator
      line is preceded by a header line that starts with ``|``.
    """
    if not markdown_body:
        return {"tables_found": 0, "broken_count": 0, "broken_snippets": []}

    lines = markdown_body.split("\n")
    broken_snippets: list[str] = []
    well_formed_count = 0

    # A "pure separator line" contains ONLY the characters: | - : whitespace.
    # If a line has the separator token `|---` AND any other character beyond
    # this set, then rows have been fused onto the separator line.
    separator_chars_only_re = re.compile(r"^[\s\-:|]+$")
    has_separator_token_re = re.compile(r"\|[\s]*-{3,}[\s]*\|")

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Line has a table separator token somewhere inside it.
        if not has_separator_token_re.search(stripped):
            continue

        # Case A: the line is ONLY the separator (chars limited to | - : space).
        # This is a well-formed table — the separator sits on its own line.
        if separator_chars_only_re.match(stripped):
            # Confirm the previous non-empty line is a header row.
            prev_idx = idx - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            if prev_idx >= 0 and lines[prev_idx].strip().startswith("|"):
                well_formed_count += 1
            continue

        # Case B: the line has a separator token AND other characters →
        # broken table (header/data rows concatenated with the separator).
        broken_snippets.append(stripped[:80])

    return {
        "tables_found": well_formed_count + len(broken_snippets),
        "broken_count": len(broken_snippets),
        "broken_snippets": broken_snippets,
    }


def evaluate_technical(article: dict, topic: dict, markdown_body: str, lang: str = "en") -> dict:
    """Score the article's technical SEO compliance. Max 54 points.

    Metric 13 (table_format) is a hard-fail catch: if a markdown table
    has all rows concatenated on a single line, it scores 0 here and the
    issue surfaces in the Telegram preview block, alerting the operator
    BEFORE the broken table reaches the public site.

    For CJK languages (zh) the title/description ideal lengths and the
    word-count metric are measured in characters, not space-delimited
    words — Chinese is information-dense and is not tokenised by spaces,
    so English byte/word targets would wrongly penalise correct articles.
    """
    scores = {}

    # CJK languages count characters, not space-delimited words, and use
    # shorter title/description targets (Google truncates CJK SERP titles
    # at ~30 full-width characters).
    is_cjk = lang in ("zh", "zh-Hans", "zh-Hant", "ja", "ko")

    title = str(article.get("h1_title", "") or article.get("title", ""))
    description = str(article.get("meta_description", ""))

    if is_cjk:
        # 1. Title length (5 pts: 14-30 全角 ideal)
        tlen = len(title)
        scores["title_length"] = {
            "max": 5,
            "score": 5 if 14 <= tlen <= 30 else 3 if 10 <= tlen <= 36 else 0,
            "detail": f"{tlen} chars (target CJK: 14-30)",
        }
        # 2. Description length (5 pts: 60-110 汉字 ideal)
        dlen = len(description)
        scores["desc_length"] = {
            "max": 5,
            "score": 5 if 60 <= dlen <= 110 else 3 if 45 <= dlen <= 130 else 0,
            "detail": f"{dlen} chars (target CJK: 60-110)",
        }
        # 3. Word count (5 pts: 1400-3000 汉字 ideal). Count CJK characters
        #    directly — markdown_body.split() returns almost nothing for
        #    Chinese because there are no spaces between words.
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", markdown_body))
        scores["word_count"] = {
            "max": 5,
            "score": 5 if 1400 <= cjk_chars <= 3000 else 3 if 1000 <= cjk_chars else 0,
            "detail": f"{cjk_chars} 汉字 (target CJK: 1400-3000)",
        }
    else:
        # 1. Title length (5 pts: 30-65 ideal)
        tlen = len(title)
        scores["title_length"] = {
            "max": 5,
            "score": 5 if 30 <= tlen <= 65 else 3 if 20 <= tlen <= 75 else 0,
            "detail": f"{tlen} chars (target: 30-65)",
        }
        # 2. Description length (5 pts: 130-160 ideal)
        dlen = len(description)
        scores["desc_length"] = {
            "max": 5,
            "score": 5 if 130 <= dlen <= 160 else 3 if 100 <= dlen <= 180 else 0,
            "detail": f"{dlen} chars (target: 130-160)",
        }
        # 3. Word count (5 pts: 1800-3500 ideal)
        word_count = len(markdown_body.split())
        scores["word_count"] = {
            "max": 5,
            "score": 5 if 1800 <= word_count <= 3500 else 3 if 1200 <= word_count else 0,
            "detail": f"{word_count} words (target: 1800-3500)",
        }
    
    # 4. H2 headings (5 pts: 7-12 ideal)
    h2_count = len(re.findall(r"^##\s", markdown_body, re.MULTILINE))
    scores["h2_count"] = {
        "max": 5,
        "score": 5 if 7 <= h2_count <= 12 else 3 if 4 <= h2_count else 0,
        "detail": f"{h2_count} H2 headings (target: 7-12)",
    }
    
    # 5. H3 headings (3 pts: ≥3 total)
    h3_count = len(re.findall(r"^###\s", markdown_body, re.MULTILINE))
    scores["h3_count"] = {
        "max": 3,
        "score": 3 if h3_count >= 3 else 1 if h3_count >= 1 else 0,
        "detail": f"{h3_count} H3 headings (target: ≥3)",
    }
    
    # 6. Internal links count (5 pts: 3-5 ideal)
    # Match markdown links to internal paths
    internal_links = re.findall(r'\]\((/[^)]+)\)', markdown_body)
    link_count = len(internal_links)
    scores["internal_links_count"] = {
        "max": 5,
        "score": 5 if 3 <= link_count <= 5 else 3 if 2 <= link_count <= 7 else 0,
        "detail": f"{link_count} internal links (target: 3-5)",
    }
    
    # 7. Internal links diversity (3 pts: links to multiple pages)
    unique_targets = set(internal_links)
    scores["internal_links_diversity"] = {
        "max": 3,
        "score": 3 if len(unique_targets) >= 3 else 1 if len(unique_targets) >= 2 else 0,
        "detail": f"{len(unique_targets)} unique link targets",
    }
    
    # 8. Target page link present (3 pts)
    target_page = str(topic.get("target_page", "")).strip()
    target_present = any(target_page in lnk for lnk in internal_links) if target_page else False
    scores["target_page_link"] = {
        "max": 3,
        "score": 3 if target_present else 0,
        "detail": f"Target {target_page} {'✓ found' if target_present else '✗ missing'}",
    }

    # 8b. External authoritative source link present (3 pts, E-E-A-T / GEO)
    # Внешняя ссылка на первоисточник — markdown-ссылка на http(s), не на свой домен.
    external_links = [
        u for u in re.findall(r'\]\((https?://[^)]+)\)', markdown_body)
        if "kozyr.club" not in u
    ]
    scores["external_source_link"] = {
        "max": 3,
        "score": 3 if len(external_links) >= 1 else 0,
        "detail": f"{len(external_links)} external source link(s) (target: ≥1)",
    }
    
    # 9. Primary keyword in title + H1 + first paragraph (4 pts)
    primary_kw = str(topic.get("primary_keyword", "")).strip().lower()
    if primary_kw:
        in_title = primary_kw in title.lower()
        first_para = markdown_body.split("\n\n")[0] if markdown_body else ""
        in_first_para = primary_kw in first_para.lower()
        h2_matches = re.findall(r"^##\s+(.+)$", markdown_body, re.MULTILINE)
        in_h2 = any(primary_kw in h.lower() for h in h2_matches)
        kw_score = (2 if in_title else 0) + (1 if in_first_para else 0) + (1 if in_h2 else 0)
        scores["primary_keyword"] = {
            "max": 4,
            "score": kw_score,
            "detail": f"title={in_title}, first_para={in_first_para}, h2={in_h2}",
        }
    else:
        scores["primary_keyword"] = {
            "max": 4, "score": 0,
            "detail": "no primary_keyword set in topic",
        }
    
    # 10. Secondary keywords coverage (4 pts: all mentioned)
    secondary_raw = str(topic.get("secondary_keywords", ""))
    secondaries = [s.strip().lower() for s in secondary_raw.split(",") if s.strip()]
    if secondaries:
        body_lower = markdown_body.lower()
        present = sum(1 for s in secondaries if s in body_lower)
        sec_score = round(4 * present / len(secondaries))
        scores["secondary_keywords"] = {
            "max": 4,
            "score": sec_score,
            "detail": f"{present}/{len(secondaries)} secondaries found",
        }
    else:
        scores["secondary_keywords"] = {
            "max": 4, "score": 4,  # full points if no secondaries to check
            "detail": "no secondaries set (skipped)",
        }
    
    # 11. FAQ presence (4 pts)
    faq_section = article.get("faq", []) or article.get("faq_items", [])
    faq_count = len(faq_section) if isinstance(faq_section, list) else 0
    scores["faq_count"] = {
        "max": 4,
        "score": 4 if faq_count >= 6 else 2 if faq_count >= 3 else 0,
        "detail": f"{faq_count} FAQ items (target: ≥6)",
    }
    
    # 12. No common clichés (4 pts)
    cliches = [
        r"in today'?s fast-?paced world",
        r"in conclusion[,:]",
        r"it'?s important to note",
        r"at the end of the day",
        r"in summary",
        r"as we all know",
    ]
    body_lower = markdown_body.lower()
    cliche_hits = [c for c in cliches if re.search(c, body_lower)]
    scores["no_cliches"] = {
        "max": 4,
        "score": 4 if not cliche_hits else max(0, 4 - len(cliche_hits)),
        "detail": f"{len(cliche_hits)} clichés found" if cliche_hits else "clean",
    }

    # 13. Table format integrity (4 pts) — catches the silent failure where
    # markdown tables get emitted with all rows on one line, which makes
    # python-markdown render them as broken paragraphs of pipe characters.
    # Zero score forces operator attention in the Telegram preview before
    # the article is published.
    table_check = detect_broken_tables(markdown_body)
    if table_check["broken_count"] > 0:
        first_snippet = table_check["broken_snippets"][0] if table_check["broken_snippets"] else ""
        # Keep under ~120 chars so it survives the Telegram-block truncation.
        detail = (
            f"BROKEN: {table_check['broken_count']} table(s) on 1 line "
            f"— will render as pipe-paragraph. Snippet: «{first_snippet[:40]}…»"
        )
        scores["table_format"] = {"max": 4, "score": 0, "detail": detail}
    elif table_check["tables_found"] == 0:
        # No tables at all — neutral (most topics need a table, but the
        # "comparison table required" rule is enforced by the content
        # rubric and the prompt, not here. We only penalize broken tables.)
        scores["table_format"] = {
            "max": 4, "score": 4,
            "detail": "no tables present",
        }
    else:
        scores["table_format"] = {
            "max": 4, "score": 4,
            "detail": f"{table_check['tables_found']} table(s), all well-formed",
        }

    total = sum(s["score"] for s in scores.values())
    max_total = sum(s["max"] for s in scores.values())
    
    return {
        "tier": "technical",
        "scores": scores,
        "total": total,
        "max": max_total,
        "percent": round(100 * total / max_total) if max_total else 0,
    }


# ==== Content evaluation (Claude, 15 metrics, 50 points) ====

CONTENT_RUBRIC = {
    "eeat": (4, "E-E-A-T: specific numbers, real cases, dates, expert depth"),
    "sourcing": (4, "Честность фактов: конкретные числа партнёров (рейкбек %, лимиты, сроки вывода) поданы как условия рума/клуба, а не выдуманы; неизвестное помечено 'уточняется'; НЕТ ложных формулировок, что KOZYR сам платит рейкбек/ведёт расчёты; KOZYR — витрина и ссылка; без фабрикации статистики"),
    "depth": (4, "Depth: explains WHY not just WHAT, beyond surface-level"),
    "concreteness": (4, "Concreteness: specifics over vague claims (numbers, platforms, dates)"),
    "unique_angle": (4, "Unique angle: differentiated from competitor articles on the same topic"),
    "intent_match": (4, "Search intent match: informational/commercial/transactional aligned with topic"),
    "structure": (3, "Logical structure: each H2 advances the argument, no repetition"),
    "readability": (3, "Readability: short paragraphs, active voice, scanability"),
    "primary_kw_natural": (3, "Primary keyword usage natural, not stuffed"),
    "secondary_coverage": (3, "Secondary keywords covered naturally and meaningfully"),
    "link_context": (3, "Links placed in meaningful context, informative anchor text"),
    "faq_relevance": (3, "FAQ answers real questions a target reader would ask"),
    "h2_clickability": (3, "H2 titles promise concrete value, not 'Introduction'/'Conclusion'"),
    "no_filler": (3, "No filler phrases like 'in conclusion', 'fast-paced world'"),
    "brand_voice": (3, "Тон KOZYR: экспертный, честный, помогает игроку выбрать; не рекламное втюхивание; уместное упоминание ответственной игры — это плюс, а не минус"),
    "conversion_bridge": (3, "Логичный переход к каталогу /ua/ или обзорам румов/клубов; естественный CTA с пользой для игрока"),
}


def build_content_eval_prompt(article: dict, topic: dict, markdown_body: str, lang: str) -> str:
    """Compose the Claude prompt for content quality evaluation."""
    rubric_lines = []
    for key, (max_pts, desc) in CONTENT_RUBRIC.items():
        rubric_lines.append(f"  - {key} (max {max_pts}): {desc}")
    rubric_text = "\n".join(rubric_lines)

    _faq = article.get("faq", []) or []
    if _faq:
        _faq_text = "\n".join(
            f"  Q: {e.get('question','')}\n  A: {e.get('answer','')}" for e in _faq
        )
    else:
        _faq_text = "(FAQ отсутствует)"

    return f"""Ты — старший SEO-редактор, оцениваешь статью для блога KOZYR — это витрина рейкбек-сделок для покерных ИГРОКОВ (B2C). KOZYR показывает каталог румов и клубов с условиями и ведёт по партнёрской ссылке; сам рум/клуб начисляет и выплачивает рейкбек. Аудитория — игроки СНГ/Украины, от новичков до регуляров. Оценивай именно с этой позиции: НЕ штрафуй за отсутствие B2B-угла для владельцев клубов, НЕ требуй формулировок вида «клубы, которыми мы управляем» — KOZYR ничем не управляет. Упоминание ответственной игры уместно и не является минусом.

ARTICLE CONTEXT:
- Language: {lang}
- Primary keyword: {topic.get('primary_keyword', '(none)')}
- Secondary keywords: {topic.get('secondary_keywords', '(none)')}
- Target intent: {topic.get('intent', 'informational')}
- Target service page (where reader should be guided): {topic.get('target_page', '(none)')}

ARTICLE TO EVALUATE (markdown body):
{markdown_body[:18000]}

FAQ (из JSON, рендерится отдельным блоком — оценивай faq_relevance по нему, НЕ ищи FAQ в теле статьи):
{_faq_text}

EVALUATE on these 16 dimensions. For each, give an integer score from 0 to the max points listed:

{rubric_text}

For each dimension, return:
- score (integer 0..max)
- evidence: короткая цитата/ссылка из статьи, обосновывающая балл НА РУССКОМ (макс 100 симв)
- fix: короткая конкретная рекомендация НА РУССКОМ (только если score < max; макс 100 симв)

Output strict JSON, no preamble, no code fences:

{{
  "scores": {{
    "eeat": {{"score": 3, "evidence": "...", "fix": "..."}},
    "depth": {{"score": 4, "evidence": "...", "fix": null}},
    ... (all 16 dimensions)
  }},
  "top_3_strengths": ["... (НА РУССКОМ)", "...", "..."],
  "top_3_issues": ["... (НА РУССКОМ)", "...", "..."],
  "verdict_explanation": "одно предложение НА РУССКОМ"
}}
"""


def evaluate_content(article: dict, topic: dict, markdown_body: str, lang: str) -> dict:
    """Score article content quality via Claude API. Max 50 points.
    Returns score 0 if API unavailable — caller should fall back gracefully."""
    if not HAS_ANTHROPIC:
        return {
            "tier": "content",
            "scores": {},
            "total": 0,
            "max": 50,
            "percent": 0,
            "error": "anthropic library not installed",
        }
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "tier": "content",
            "scores": {},
            "total": 0,
            "max": 50,
            "percent": 0,
            "error": "ANTHROPIC_API_KEY not set",
        }
    
    client = Anthropic(api_key=api_key)
    prompt = build_content_eval_prompt(article, topic, markdown_body, lang)
    
    try:
        response = client.messages.create(
            model=CONTENT_EVAL_MODEL,
            max_tokens=CONTENT_EVAL_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {
            "tier": "content", "scores": {}, "total": 0, "max": 50, "percent": 0,
            "error": f"JSON parse failed: {e}",
        }
    except Exception as e:
        return {
            "tier": "content", "scores": {}, "total": 0, "max": 50, "percent": 0,
            "error": f"API call failed: {e}",
        }
    
    # Combine into our format with max-points constraint
    scores = {}
    total = 0
    for key, (max_pts, desc) in CONTENT_RUBRIC.items():
        entry = data.get("scores", {}).get(key, {})
        score = max(0, min(int(entry.get("score", 0)), max_pts))
        scores[key] = {
            "max": max_pts,
            "score": score,
            "evidence": str(entry.get("evidence", ""))[:150],
            "fix": str(entry.get("fix", "") or "")[:150],
        }
        total += score
    
    max_total = sum(m for m, _ in CONTENT_RUBRIC.values())
    
    return {
        "tier": "content",
        "scores": scores,
        "total": total,
        "max": max_total,
        "percent": round(100 * total / max_total) if max_total else 0,
        "top_3_strengths": data.get("top_3_strengths", [])[:3],
        "top_3_issues": data.get("top_3_issues", [])[:3],
        "verdict_explanation": str(data.get("verdict_explanation", ""))[:300],
    }


# ==== Combined evaluation ====

def decide_verdict(total_percent: int) -> str:
    """Map total percent to a verdict label."""
    if total_percent >= VERDICT_THRESHOLDS["PUBLISH_READY"]:
        return "PUBLISH_READY"
    if total_percent >= VERDICT_THRESHOLDS["GOOD"]:
        return "GOOD"
    if total_percent >= VERDICT_THRESHOLDS["NEEDS_REVISION"]:
        return "NEEDS_REVISION"
    return "REJECT"


def evaluate_article(article: dict, topic: dict, markdown_body: str,
                     lang: str = "en", skip_content: bool = False) -> dict:
    """Top-level evaluation. Returns:
        {
          "technical": {...},
          "content": {...},
          "total": int 0-100,
          "verdict": str,
          "telegram_block": str (pre-formatted for inclusion in preview)
        }
    
    If skip_content=True, only technical evaluation runs (useful for testing
    or when ANTHROPIC_API_KEY is missing).
    """
    tech = evaluate_technical(article, topic, markdown_body, lang)
    
    if skip_content:
        content = {"tier": "content", "scores": {}, "total": 0, "max": 50, "percent": 0,
                   "error": "skipped"}
    else:
        content = evaluate_content(article, topic, markdown_body, lang)
    
    # Combined total (normalized to 0-100): tech and content sub-scores
    # are summed and divided by combined max. Adding/removing metrics in
    # either tier is safe — the percent stays in 0-100 range.
    combined_total = tech["total"] + content["total"]
    combined_max = tech["max"] + content["max"]
    combined_percent = round(100 * combined_total / combined_max) if combined_max else 0
    
    verdict = decide_verdict(combined_percent)
    
    return {
        "technical": tech,
        "content": content,
        "total": combined_percent,
        "verdict": verdict,
        "telegram_block": format_telegram_block(tech, content, combined_percent, verdict),
    }


# ==== Telegram formatting ====

VERDICT_ICONS = {
    "PUBLISH_READY": "🟢",
    "GOOD": "🟡",
    "NEEDS_REVISION": "🟠",
    "REJECT": "🔴",
}


def _escape_md(text: str) -> str:
    """Escape Telegram MarkdownV1 special chars in user-content strings.
    
    Telegram parse_mode=Markdown treats `_ * [ ] ` as formatting markers.
    Claude-generated strengths/issues often contain unescaped underscores
    (e.g. 'B2B angle' is fine, but 'pain_point' breaks parsing) or stray
    asterisks. An unclosed entity causes Telegram to reject the whole
    message with "Can't find end of the entity".
    
    We escape the three most common breakers: _ * `.
    Square brackets are left alone (rarely cause issues in plain prose).
    """
    if not text:
        return ""
    return str(text).replace("_", r"\_").replace("*", r"\*").replace("`", r"\`")


def format_telegram_block(tech: dict, content: dict,
                          total_percent: int, verdict: str) -> str:
    """Pre-format the score block for inclusion in Telegram preview.
    
    All user-content strings (strengths, issues, tech detail) are escaped
    via _escape_md to prevent Markdown parse failures in Telegram.
    """
    icon = VERDICT_ICONS.get(verdict, "⚪")
    tech_pct = tech.get("percent", 0)
    content_pct = content.get("percent", 0)
    
    lines = [
        f"🏆 *Качество: {total_percent}/100* {icon} {verdict}",
        f"   📊 Технически: {tech_pct}/100 · 📝 Контент: {content_pct}/100",
    ]
    
    if content.get("error"):
        lines.append(f"   ⚠️ Content check: {_escape_md(content['error'])}")
    
    # Top strengths
    strengths = content.get("top_3_strengths", [])
    if strengths:
        lines.append("")
        lines.append("✅ *Сильное:*")
        for s in strengths[:3]:
            # Truncate FIRST, then escape — escaping after truncation
            # avoids cutting a backslash-escape sequence in half.
            lines.append(f"   • {_escape_md(s[:120])}")
    
    # Top issues (combine content issues + technical fails)
    issues = list(content.get("top_3_issues", []))
    tech_fails = []
    critical_tech_fails = []  # Hard-fail metrics that MUST surface (e.g. broken tables)
    for key, s in tech.get("scores", {}).items():
        if s["score"] < s["max"]:
            entry = f"{key}: {s['detail']}"
            # table_format with score=0 means a broken table will reach prod —
            # always surface it first, regardless of how many other issues exist.
            if key == "table_format" and s["score"] == 0:
                critical_tech_fails.append(entry)
            else:
                tech_fails.append(entry)

    # Critical fails come first, then content issues, then remaining tech fails.
    all_issues = critical_tech_fails + issues + tech_fails
    if all_issues:
        lines.append("")
        lines.append("⚠️ *Что улучшить:*")
        for issue in all_issues[:5]:
            lines.append(f"   • {_escape_md(issue[:120])}")
    
    return "\n".join(lines)


# ==== CLI for testing ====

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python quality_check.py <article.json> <body.md> [lang]")
        sys.exit(1)
    article_path = sys.argv[1]
    body_path = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "en"
    
    with open(article_path) as f:
        article_data = json.load(f)
    with open(body_path) as f:
        body_md = f.read()
    
    # Try to find a topic dict in the article JSON
    topic = article_data.get("topic", {})
    if not topic:
        topic = {
            "primary_keyword": article_data.get("primary_keyword", ""),
            "secondary_keywords": article_data.get("secondary_keywords", ""),
            "intent": article_data.get("intent", "informational"),
            "target_page": article_data.get("target_page", ""),
        }
    
    skip_content = os.environ.get("SKIP_CONTENT_CHECK") == "1"
    result = evaluate_article(article_data, topic, body_md, lang=lang,
                              skip_content=skip_content)
    print(f"\n=== EVALUATION RESULT ===")
    print(f"Total: {result['total']}/100 — {result['verdict']}")
    print(f"\n--- Telegram block ---")
    print(result["telegram_block"])
    print(f"\n--- Full JSON ---")
    print(json.dumps({k: v for k, v in result.items() if k != "telegram_block"},
                     indent=2, ensure_ascii=False, default=str))
