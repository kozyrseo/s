"""
PokerNet AI — Telegram autopost main entry point.

Run modes:
  python autopost.py generate                  # generate today's post + image, send preview
  python autopost.py generate --no-preview     # generate only (workflow commits, then sends preview separately)
  python autopost.py send-preview --slug SLUG  # send preview for an already-generated slug
  python autopost.py generate-pinned           # generate the pinned post
  python autopost.py publish --slug SLUG       # publish a previously-generated post to channel
  python autopost.py force-pitch               # force today's post to be a pitch post

Workflow (bilingual: EN to channel, RU to your review):
  1. Pick post_type from schedule (override to 'pitch' if N days passed since last)
  2. Pull recent angles from posted_log.json → feed to Claude as anti-repeat
  3. Claude generates EN post body + image_prompt + topic_angle (JSON output)
  4. Second Claude call: faithful EN→RU translation of the body (preview only)
  5. gpt-image-1 generates the hero image (one image, language-agnostic)
  6. Save to _pending_tg/{slug}/ for staging — post.json holds both versions
  7. (workflow) commit + push the pending folder to main
  8. send-preview sends RU preview to review chat with inline buttons:
        ✅ Publish (EN to channel)   🔄 Regenerate   ❌ Reject

  When you tap Publish:
  9. Cloudflare Worker (pokernet-tg-autopost-webhook) catches the callback
  10. Worker dispatches tg-autopost-publish.yml workflow with the slug input
  11. publish.py sends the EN body + footer + image to the channel
  12. Updates posted_log.json, archives _pending_tg/{slug}/

The two-step generate / commit / send-preview flow is critical: it ensures
the slug folder is on main BEFORE the preview message reaches Telegram. Without
it, you can tap Publish before the workflow has pushed the folder, and the
publish workflow checkouts main and finds nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import telegram_client as tg
from image_gen import generate_hero_image
from repeat_log import (
    recent_angles,
    days_since_last_pitch,
    last_used_footer,
    append_full_entry,
)


# ==== Paths ====
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
PENDING_DIR = REPO_ROOT / "_pending_tg"
CONFIG_PATH = HERE / "config.json"
TOPIC_BANKS_PATH = HERE / "topic_banks.json"
POST_PROMPT_PATH = HERE / "prompts" / "post_system_prompt.md"
PINNED_PROMPT_PATH = HERE / "prompts" / "pinned_system_prompt.md"
TRANSLATE_PROMPT_PATH = HERE / "prompts" / "translate_system_prompt.md"


# ==== Config helpers ====

def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_topic_banks() -> dict:
    return json.loads(TOPIC_BANKS_PATH.read_text(encoding="utf-8"))


# ==== Post type selection ====

def pick_post_type(config: dict) -> str:
    """
    Determine today's post_type based on day-of-week schedule + pitch override.
    Sunday=0, Monday=1, ..., Saturday=6 (matches Python's weekday() shifted by +1
    for biblical Sunday-first; we use isoweekday() % 7 for Sunday=0).
    """
    schedule = config["post_schedule"]
    pitch_interval = schedule.get("pitch_post_every_n_days", 10)

    # Pitch override
    days_since = days_since_last_pitch(HERE)
    if days_since is None or days_since >= pitch_interval:
        # First-ever post is NOT a pitch — start with insight to build trust
        if days_since is not None:
            print(f"⏰ {days_since} days since last pitch — overriding to pitch post")
            return "pitch"

    # Day-of-week lookup
    today = datetime.now(timezone.utc)
    dow = today.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
    return schedule.get(str(dow), "insight")


def pick_footer(config: dict) -> str:
    """Pick a footer that wasn't last-used. Substitute {manager}."""
    variants = config["footer_variants"]
    manager = config["manager_username"]
    last = last_used_footer(HERE)
    candidates = [v for v in variants if v != last] or variants
    chosen = random.choice(candidates)
    return chosen.replace("{manager}", manager)


# ==== Slug generation ====

def make_slug(topic_angle: str, post_type: str) -> str:
    """Date-prefixed slug for the pending folder.

    Length budget: callback_data limit in Telegram Bot API is 64 bytes.
    Longest prefix used in this code is 'tgpub:regenerate:' (17 chars when
    keeping 'regenerate' for clarity, currently 'regen' = 12 chars), date
    prefix is 11 chars ('YYYY-MM-DD-'), so base is capped at 30 to keep
    'tgpub:<verb>:<slug>' under 64 bytes with safe headroom.

    rstrip('-') after the [:30] cut is critical: a slice can land mid-word
    on a dash (e.g. 'pppoker-vs-pokerbros-off-peak-' — ends on '-'), leaving
    a trailing dash that some tooling (shell scripts, URL routers, comparison
    checks against the on-disk folder name) handles oddly.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cleaned = re.sub(r"[^a-z0-9]+", "-", topic_angle.lower()).strip("-")
    base = cleaned[:30].rstrip("-") or post_type
    return f"{today}-{base}"


# ==== Claude generation ====

def generate_post(post_type: str, topic_banks: dict, recent: list[str],
                  article: dict | None = None) -> dict:
    """Call Claude, return parsed JSON with body/image_prompt/topic_angle.

    Has a single retry: if the first call returns un-repairable JSON, we
    re-prompt Claude with the broken output asking it to fix it. This handles
    edge cases (mainly unescaped double quotes inside string values) that our
    regex repair can't safely fix.
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. pip install openai")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    system_prompt = POST_PROMPT_PATH.read_text(encoding="utf-8")
    user_message = build_user_message(post_type, topic_banks, recent, article)

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    print(f"🤖 Generating post (type={post_type}) with OpenRouter...")
    response = client.chat.completions.create(
        model="anthropic/claude-opus-4.8",
        max_tokens=4000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    raw = (response.choices[0].message.content or "").strip()

    try:
        return parse_json_response(raw)
    except RuntimeError as e:
        # Retry: ask the model to repair its own broken JSON
        print(f"⚠️  Initial parse failed ({e}); asking model to repair...")
        repair_response = client.chat.completions.create(
            model="anthropic/claude-opus-4.8",
            max_tokens=4000,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a JSON repair tool. The user will paste a broken JSON "
                        "object. Return the SAME content as valid JSON. Replace any "
                        "unescaped double quotes inside string values with single quotes. "
                        "Replace any raw newlines inside string values with \\n. Do not "
                        "change the meaning of the content. Output JSON only — no "
                        "explanation, no code fences."
                    ),
                },
                {"role": "user", "content": f"Repair this JSON:\n\n{raw}"},
            ],
        )
        repaired_raw = (repair_response.choices[0].message.content or "").strip()
        return parse_json_response(repaired_raw)


def pick_longform_article(config: dict) -> dict | None:
    """
    Pick a random article from longform_link_pool for Sunday's longform_link post.
    Returns dict with url/title/summary, or None if pool is empty/missing.
    """
    import random
    pool = config.get("longform_link_pool", [])
    if not pool:
        return None
    return random.choice(pool)


def build_user_message(post_type: str, topic_banks: dict, recent: list[str],
                       article: dict | None = None) -> str:
    """Compose the per-call user message for Claude."""
    banks_excerpt = {
        "operator_pains": topic_banks["operator_pains"],
        "formats": topic_banks["formats"],
        "platforms": topic_banks["platforms"],
        "operator_metrics": topic_banks["operator_metrics"],
    }
    avoid_block = (
        "\n".join(f"- {a}" for a in recent) if recent else "(none yet — this is one of the first posts)"
    )

    # For longform_link posts, inject the actual blog article URL/title/summary
    # so Claude tease-writes about a REAL article instead of inventing one.
    article_block = ""
    if post_type == "longform_link":
        if article:
            article_block = f"""

ARTICLE_TO_TEASE (use this exact URL — DO NOT invent your own):
- URL: {article['url']}
- Title: {article['title']}
- Summary: {article['summary']}

Write a 180-220 word teaser that frames the operational problem the article addresses, then ends with the link in this format:
→ Read the full breakdown: {article['url']}
"""
        else:
            article_block = """

NOTE: post_type is longform_link but NO article is available in the pool.
You MUST switch to post_type "insight" — set "post_type": "insight" in your JSON output.
Write a normal insight post WITHOUT any link references. Do NOT invent a URL.
"""

    return f"""Generate today's Telegram post.

POST_TYPE: {post_type}

TOPIC_BANKS (combine and remix; you may invent adjacent angles):
{json.dumps(banks_excerpt, indent=2)}

RECENT_ANGLES_TO_AVOID (do not repeat semantically):
{avoid_block}{article_block}

Return JSON only, per the schema in the system prompt. No prose, no code fences."""


def parse_json_response(raw: str) -> dict:
    """Strip optional code fences, parse JSON. Raise with context if it fails.

    Strategy ladder (each step more tolerant than the last):
      1. Strip code fences, then strict json.loads
      2. Extract the largest {...} block from the text, try json.loads on it
      3. Apply common-LLM-mistake repairs (smart quotes, trailing commas,
         literal newlines inside strings) and try json.loads again
    Only raise after all three fail.
    """
    text = raw.strip()
    # Strip ```json ... ``` fences if Claude added them despite instructions
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Attempt 1: strict parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e1:
        # Attempt 2: extract first {...last } and try
        try:
            data = _try_extract_json_object(text)
        except (json.JSONDecodeError, ValueError):
            # Attempt 3: repair common LLM mistakes, then parse
            try:
                repaired = _repair_likely_json(text)
                data = json.loads(repaired)
                print(f"⚠️  JSON needed repair (orig error: {e1}); parsed after fixup")
            except json.JSONDecodeError as e3:
                print(f"❌ Claude returned invalid JSON. First 800 chars:\n{text[:800]}")
                raise RuntimeError(f"Invalid JSON from Claude after 3 attempts: {e3}")

    required = {"topic_angle", "post_type", "body", "image_prompt"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"Claude JSON missing fields: {missing}")
    return data


def _try_extract_json_object(text: str) -> dict:
    """Find the outermost {...} block and try to parse just that."""
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("No {...} block found")
    return json.loads(text[first:last + 1])


def _repair_likely_json(text: str) -> str:
    """Apply targeted fixes for common LLM JSON mistakes.

    These repairs are conservative — they only touch patterns we know are
    safe to rewrite. We do NOT do generic sed-style mutilation that could
    silently corrupt content.
    """
    # 0. Reduce to {...} block if there's prose around it
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        text = text[first:last + 1]

    # 1. Replace literal newlines and tabs that appear *inside* string values
    #    with their escaped forms. JSON forbids raw \n / \t inside "...".
    #    This regex walks the text in chunks: outside-strings stays intact,
    #    inside-strings gets newlines escaped.
    def _escape_inside_strings(s: str) -> str:
        out = []
        in_str = False
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\\" and in_str and i + 1 < len(s):
                # keep escape sequence as-is (\" \n \\ etc)
                out.append(s[i:i+2])
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
                out.append(ch)
                i += 1
                continue
            if in_str and ch == "\n":
                out.append("\\n")
            elif in_str and ch == "\r":
                out.append("\\r")
            elif in_str and ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
            i += 1
        return "".join(out)

    text = _escape_inside_strings(text)

    # 2. Drop trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 3. Replace curly/typographic quotes with straight quotes ONLY when they
    #    appear in positions that look like JSON delimiters (next to : or ,).
    #    Inside content we leave them alone — they're valid JSON characters.
    #    This is rarely needed but cheap to try.
    text = re.sub(r"([{,]\s*)[“”]", r'\1"', text)
    text = re.sub(r"[“”](\s*[:,}])", r'"\1', text)

    return text


# ==== Translation EN -> RU (for review preview only) ====

def translate_to_russian(english_body: str) -> str:
    """
    Faithful EN→RU translation of the post body for the reviewer's preview.
    Footer is NOT translated (stays English — it's what actually appears in the channel).

    Failure is non-fatal: if the translation call fails, we fall back to the
    English body in the preview. The reviewer can still decide based on EN.
    """
    if not english_body or not english_body.strip():
        return ""

    try:
        from openai import OpenAI
    except ImportError:
        print("⚠️  openai missing — skipping translation, preview will be EN")
        return english_body

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("⚠️  OPENROUTER_API_KEY missing — skipping translation")
        return english_body

    system_prompt = TRANSLATE_PROMPT_PATH.read_text(encoding="utf-8")
    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    try:
        print("🌐 Translating to Russian for preview...")
        response = client.chat.completions.create(
            model="anthropic/claude-opus-4.8",
            max_tokens=3000,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Translate the following English post body into Russian per the rules. "
                        "Output the translated text only, nothing else.\n\n"
                        "---\n"
                        f"{english_body}\n"
                        "---"
                    ),
                },
            ],
        )
        ru = (response.choices[0].message.content or "").strip()
        # Strip a leading "---" if Claude echoed it back
        ru = re.sub(r"^---\s*", "", ru)
        ru = re.sub(r"\s*---\s*$", "", ru)
        return ru.strip()
    except Exception as e:
        print(f"⚠️  Translation failed ({type(e).__name__}: {e}) — preview will be EN")
        return english_body


# ==== Pending folder ====

def save_pending(slug: str, post_data: dict, footer: str,
                 image_path: Path | None,
                 body_ru: str = "") -> Path:
    """
    Persist generated content to _pending_tg/{slug}/ for review/publish.

    post.json stores BOTH:
      - body_en (canonical, what gets posted to the channel)
      - body_ru (preview only, what you read to decide)
    """
    pending = PENDING_DIR / slug
    pending.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": slug,
        "topic_angle": post_data["topic_angle"],
        "post_type": post_data["post_type"],
        "body_en": post_data["body"],
        "body_ru": body_ru,
        "footer": footer,
        "image_prompt": post_data["image_prompt"],
        "image_filename": image_path.name if image_path else None,
        "internal_notes": post_data.get("internal_notes", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (pending / "post.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if image_path and image_path.parent != pending:
        # image_gen already saved into pending/, but defend against future refactors
        import shutil
        shutil.copy2(image_path, pending / image_path.name)
    print(f"✅ Saved pending: {pending}")
    return pending


# ==== Final composition ====

def compose_full_text(body: str, footer: str) -> str:
    """Body + blank line + footer. Both are plain text; footer carries the manager handle."""
    body = body.strip()
    return f"{body}\n\n{footer}"


# ==== Preview to review chat ====

def send_preview(slug: str, post_data: dict, footer: str,
                 image_path: Path | None, config: dict,
                 body_ru: str = "") -> None:
    """
    Send a bilingual preview of the composed post to the review chat.

    Layout:
        🔎 PREVIEW · post_type
        _topic_angle_
        words(EN): N
        ────────────

        [RUSSIAN BODY — what reviewer reads to decide]

        — — — EN (will be posted) — — —

        [ENGLISH BODY — exact text that hits the channel on approval]

        [footer line — English, same as channel]

    The footer is shown attached to the EN block (its actual position).
    The reviewer reads the RU first; the EN block lets them spot-check
    terminology before tapping Publish.

    If the combined preview exceeds Telegram's message limit (4096 chars),
    we split: RU goes with the photo, EN goes as a follow-up message with
    the buttons.
    """
    token = os.environ.get(config["telegram"]["bot_token_env"])
    review_chat = os.environ.get(config["telegram"]["review_chat_id_env"])
    if not token or not review_chat:
        print("⚠️  Telegram bot token or review chat id missing — preview skipped")
        return

    en_with_footer = compose_full_text(post_data["body"], footer)
    ru_block = body_ru.strip() if body_ru else "_(перевод недоступен — смотри EN ниже)_"

    header = (
        f"🔎 *PREVIEW · {tg.escape_md(post_data['post_type'])}*\n"
        f"_{tg.escape_md(post_data['topic_angle'])}_\n"
        f"words (EN): {len(post_data['body'].split())}\n"
        f"────────────\n\n"
    )

    ru_section = f"🇷🇺 *RU (для ревью)*\n\n{ru_block}\n\n"
    en_section = f"━━━━━━━━━━━━━━\n🇬🇧 *EN (будет опубликовано)*\n\n{en_with_footer}"

    full_preview = header + ru_section + en_section

    keyboard = [
        [
            {"text": "✅ Publish", "callback_data": f"tgpub:publish:{slug}"},
            {"text": "🔄 Regenerate", "callback_data": f"tgpub:regen:{slug}"},
        ],
        [
            {"text": "❌ Reject", "callback_data": f"tgpub:reject:{slug}"},
        ],
    ]

    try:
        if image_path and image_path.exists():
            # If the whole bilingual preview fits in a caption, ship as one photo+caption.
            if len(full_preview) <= tg.SAFE_CAPTION_LIMIT:
                tg.send_photo(token, review_chat, image_path, full_preview,
                              keyboard=keyboard)
            elif len(header + ru_section) <= tg.SAFE_CAPTION_LIMIT:
                # Photo + RU caption, then EN as separate message with buttons
                tg.send_photo(token, review_chat, image_path,
                              header + ru_section.rstrip(),
                              keyboard=None)
                tg.send_message(token, review_chat, en_section,
                                keyboard=keyboard, disable_preview=True)
            else:
                # Both blocks long — photo alone, then RU msg, then EN msg with buttons
                tg.send_photo(token, review_chat, image_path,
                              header.rstrip(), keyboard=None)
                tg.send_message(token, review_chat, ru_section,
                                disable_preview=True)
                tg.send_message(token, review_chat, en_section,
                                keyboard=keyboard, disable_preview=True)
        else:
            # No image — text-only, may need split
            if len(full_preview) <= tg.MESSAGE_LIMIT:
                tg.send_message(token, review_chat, full_preview,
                                keyboard=keyboard, disable_preview=True)
            else:
                tg.send_message(token, review_chat, header + ru_section,
                                disable_preview=True)
                tg.send_message(token, review_chat, en_section,
                                keyboard=keyboard, disable_preview=True)
        print("✅ Preview sent to review chat (RU + EN)")
    except tg.TelegramError as e:
        print(f"⚠️  Preview send failed: {e}")


# ==== Mode: generate ====

def cmd_generate(force_pitch: bool = False, no_preview: bool = False) -> int:
    """Generate today's post. With --no-preview, just write files and print
    the slug to stdout — the workflow then commits + pushes, then runs
    `send-preview --slug ...` so the preview reaches Telegram only after
    the slug folder is on main."""
    config = load_config()
    topic_banks = load_topic_banks()

    post_type = "pitch" if force_pitch else pick_post_type(config)
    print(f"📅 Today's post_type: {post_type}")

    recent = recent_angles(HERE, n=config["anti_repeat"]["lookback_n"])

    # For longform_link posts, pre-pick a real blog article from the pool
    # so Claude tease-writes about a REAL URL instead of inventing one.
    article = pick_longform_article(config) if post_type == "longform_link" else None
    if post_type == "longform_link" and article:
        print(f"📚 Teasing article: {article['url']}")
    elif post_type == "longform_link" and not article:
        print(f"⚠️  longform_link selected but pool empty — Claude will switch to insight")

    post_data = generate_post(post_type, topic_banks, recent, article)

    slug = make_slug(post_data["topic_angle"], post_data["post_type"])
    pending_dir = PENDING_DIR / slug
    pending_dir.mkdir(parents=True, exist_ok=True)

    # Russian translation for the reviewer's preview (NOT posted to channel).
    body_ru = translate_to_russian(post_data["body"])

    image_path = None
    if config["image_generation"]["enabled"]:
        image_path = generate_hero_image(post_data["image_prompt"], pending_dir)

    footer = pick_footer(config)
    save_pending(slug, post_data, footer, image_path, body_ru=body_ru)

    if not no_preview:
        send_preview(slug, post_data, footer, image_path, config, body_ru=body_ru)
        print(f"\n✅ Generated. Tap Publish in review chat to send to channel.")
    else:
        print(f"\n✅ Generated (no preview sent). Workflow should commit, then run send-preview.")

    print(f"   Slug: {slug}")
    # Plain-line slug at the very end of stdout — workflow grabs this with `tail -1`
    print(slug)
    return 0


# ==== Mode: send-preview ====

def cmd_send_preview(slug: str) -> int:
    """Send the review-chat preview for an already-generated slug. Used by the
    workflow AFTER the commit + push step, so the slug folder is on main by
    the time the buttons reach Telegram."""
    config = load_config()
    pending_dir = PENDING_DIR / slug
    post_json_path = pending_dir / "post.json"
    if not post_json_path.exists():
        print(f"❌ Pending post not found: {post_json_path}")
        return 1

    post = json.loads(post_json_path.read_text(encoding="utf-8"))
    # Reconstruct the shape that send_preview expects
    post_data = {
        "topic_angle": post["topic_angle"],
        "post_type": post["post_type"],
        "body": post.get("body_en") or post.get("body") or "",
    }
    footer = post["footer"]
    body_ru = post.get("body_ru", "")

    image_filename = post.get("image_filename")
    image_path = pending_dir / image_filename if image_filename else None

    send_preview(slug, post_data, footer, image_path, config, body_ru=body_ru)
    return 0


# ==== Mode: generate-pinned ====

def cmd_generate_pinned() -> int:
    config = load_config()

    try:
        from openai import OpenAI
    except ImportError:
        print("❌ openai package not installed")
        return 1
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ OPENROUTER_API_KEY not set")
        return 1

    system_prompt = PINNED_PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = "Generate the pinned post for the PokerNet AI Telegram channel. Return JSON per the schema."

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    print("🤖 Generating pinned post with OpenRouter...")
    response = client.chat.completions.create(
        model="anthropic/claude-opus-4.8",
        max_tokens=2000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    data = parse_json_response_loose((response.choices[0].message.content or "").strip())

    body_with_substitutions = (
        data["body"]
        .replace("{manager}", config["manager_username"])
        .replace("{site}", config["site_url"])
    )

    pending_dir = PENDING_DIR / "_pinned"
    pending_dir.mkdir(parents=True, exist_ok=True)

    image_path = None
    if config["image_generation"]["enabled"]:
        image_path = generate_hero_image(data["image_prompt"], pending_dir,
                                         filename="pinned.webp")

    payload = {
        "kind": "pinned",
        "body": body_with_substitutions,
        "image_prompt": data["image_prompt"],
        "image_filename": image_path.name if image_path else None,
        "internal_notes": data.get("internal_notes", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (pending_dir / "pinned.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✅ Saved pinned to: {pending_dir}")

    # Send preview to review chat (no Publish button — pinning is manual per your choice)
    token = os.environ.get(config["telegram"]["bot_token_env"])
    review_chat = os.environ.get(config["telegram"]["review_chat_id_env"])
    if token and review_chat:
        # Translate the EN pinned body to RU for the reviewer.
        body_ru = translate_to_russian(body_with_substitutions)

        header = "📌 *PINNED POST PREVIEW*\n_скопируй EN-версию вручную и закрепи в канале_\n────────────\n\n"
        ru_block = f"🇷🇺 *RU (для ревью)*\n\n{body_ru}\n\n" if body_ru else ""
        en_block = f"━━━━━━━━━━━━━━\n🇬🇧 *EN (вставь в канал)*\n\n{body_with_substitutions}"
        full = header + ru_block + en_block

        try:
            if image_path and image_path.exists():
                if len(full) <= tg.SAFE_CAPTION_LIMIT:
                    tg.send_photo(token, review_chat, image_path, full)
                elif len(header + ru_block) <= tg.SAFE_CAPTION_LIMIT:
                    tg.send_photo(token, review_chat, image_path,
                                  (header + ru_block).rstrip())
                    tg.send_message(token, review_chat, en_block,
                                    disable_preview=True)
                else:
                    tg.send_photo(token, review_chat, image_path, header.rstrip())
                    if ru_block:
                        tg.send_message(token, review_chat, ru_block,
                                        disable_preview=True)
                    tg.send_message(token, review_chat, en_block,
                                    disable_preview=True)
            else:
                if len(full) <= tg.MESSAGE_LIMIT:
                    tg.send_message(token, review_chat, full,
                                    disable_preview=True)
                else:
                    tg.send_message(token, review_chat, header + ru_block,
                                    disable_preview=True)
                    tg.send_message(token, review_chat, en_block,
                                    disable_preview=True)
            print("✅ Pinned preview sent (RU + EN)")
        except tg.TelegramError as e:
            print(f"⚠️  Preview send failed: {e}")

    return 0


def parse_json_response_loose(raw: str) -> dict:
    """Looser parser for the pinned (only requires body + image_prompt)."""
    text = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    data = json.loads(text)
    if "body" not in data or "image_prompt" not in data:
        raise RuntimeError("Pinned JSON missing body or image_prompt")
    return data


# ==== Mode: publish ====

def cmd_publish(slug: str) -> int:
    """Publish the pending post {slug} to the channel."""
    config = load_config()
    pending_dir = PENDING_DIR / slug
    post_json_path = pending_dir / "post.json"
    if not post_json_path.exists():
        print(f"❌ Pending post not found: {post_json_path}")
        return 1

    post = json.loads(post_json_path.read_text(encoding="utf-8"))
    # body_en is canonical; fall back to legacy "body" if a pre-bilingual
    # post.json is sitting in pending from before the upgrade.
    english_body = post.get("body_en") or post.get("body") or ""
    if not english_body:
        print(f"❌ post.json has no body_en or body field")
        return 1
    full_text = compose_full_text(english_body, post["footer"])

    token = os.environ.get(config["telegram"]["bot_token_env"])
    channel_id = os.environ.get(config["telegram"]["channel_id_env"])
    if not token or not channel_id:
        print("❌ Bot token or channel id missing in env")
        return 1

    image_filename = post.get("image_filename")
    image_path = pending_dir / image_filename if image_filename else None

    try:
        if image_path and image_path.exists():
            if len(full_text) <= tg.SAFE_CAPTION_LIMIT:
                resp = tg.send_photo(token, channel_id, image_path, full_text)
            else:
                resp = tg.send_photo_with_long_text(
                    token, channel_id, image_path, full_text
                )
        else:
            resp = tg.send_message(token, channel_id, full_text,
                                   disable_preview=True)
    except tg.TelegramError as e:
        print(f"❌ Publish failed: {e}")
        return 1

    message_id = resp.get("result", {}).get("message_id")
    print(f"✅ Published to channel (message_id={message_id})")

    # Log it
    append_full_entry(
        HERE,
        topic_angle=post["topic_angle"],
        post_type=post["post_type"],
        footer=post["footer"],
        message_id=message_id,
        lookback=config["anti_repeat"]["lookback_n"],
    )
    print("✅ posted_log.json updated")

    # Cleanup pending — but keep a copy in archive for debugging
    archive_dir = PENDING_DIR / "_archive"
    archive_dir.mkdir(exist_ok=True)
    import shutil
    shutil.move(str(pending_dir), str(archive_dir / slug))
    print(f"✅ Moved {slug} to archive")

    return 0


# ==== CLI ====

def main() -> int:
    parser = argparse.ArgumentParser(description="PokerNet AI TG autopost")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="Generate today's post + send preview")
    g.add_argument("--force-pitch", action="store_true",
                   help="Force the post to be a pitch (override schedule)")
    g.add_argument("--no-preview", action="store_true",
                   help="Generate only, do not send the review-chat preview "
                        "(workflow uses this so it can commit+push the slug "
                        "before the buttons hit Telegram)")

    sp = sub.add_parser("send-preview",
                        help="Send the preview to review chat for a slug "
                             "that has already been generated and committed.")
    sp.add_argument("--slug", required=True)

    sub.add_parser("generate-pinned",
                   help="Generate the pinned post (one-off; you paste manually)")

    p = sub.add_parser("publish", help="Publish a pending post to channel")
    p.add_argument("--slug", required=True)

    args = parser.parse_args()

    if args.cmd == "generate":
        return cmd_generate(force_pitch=args.force_pitch,
                            no_preview=args.no_preview)
    if args.cmd == "send-preview":
        return cmd_send_preview(args.slug)
    if args.cmd == "generate-pinned":
        return cmd_generate_pinned()
    if args.cmd == "publish":
        return cmd_publish(args.slug)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
