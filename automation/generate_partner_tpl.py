#!/usr/bin/env python3
"""
KOZYR — генератор страницы партнёра на ШАБЛОНЕ (Стадия 3).

Вместо переписывания всей 100-КБ страницы LLM пишет только КОНТЕНТ (проза +
rich-блоки) компактным JSON (~11 КБ). Страница собирается из шаблона через
render_partner.py. Это в разы дешевле по OpenRouter и не ломает вёрстку.

Поток:
  анкета _partner_drafts/{id}.json
    → LLM пишет content JSON (partner_content_prompt.md + пример content_example.json)
    → render_partner.build_page(анкета, контент) → HTML
    → _pending_partner/{id}/index.html (preview) или прод-путь (publish).
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT  = AUTOMATION.parent
DRAFTS     = REPO_ROOT / "_partner_drafts"
PENDING    = REPO_ROOT / "_pending_partner"
PROMPT     = AUTOMATION / "partner_content_prompt.md"
EXAMPLE    = AUTOMATION / "content_example.json"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 16000   # контента ~11 КБ (~3.5k токенов) — с запасом, один вызов

CONTENT_KEYS = [
    "meta_title", "meta_description", "og_title", "og_description",
    "schema_description", "hero_sub",
    "about", "legality", "software", "games", "traffic",
    "deposits", "bonus", "rakeback", "kyc", "cta",
]


def parse_content_json(raw: str) -> dict:
    t = (raw or "").strip()
    if "```" in t:
        m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', t, re.S)
        if m:
            t = m.group(1)
    try:
        d = json.loads(t)
    except Exception as e:
        raise RuntimeError(f"Ответ LLM — не валидный JSON: {e}\nНачало: {t[:200]}")
    missing = [k for k in CONTENT_KEYS if k not in d]
    if missing:
        raise RuntimeError(f"В контенте не хватает ключей: {missing}")
    return d


def generate_content(draft: dict) -> dict:
    """Вызывает LLM и возвращает content JSON."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)
    system = PROMPT.read_text(encoding="utf-8")
    example = EXAMPLE.read_text(encoding="utf-8")
    user = (
        "ПРИМЕР структуры (партнёр TON Poker) — образец формата и разметки:\n"
        f"```json\n{example}\n```\n\n"
        "Теперь напиши content JSON для ЭТОГО партнёра. Контент оригинальный, "
        "на основе данных ниже; структуру и разметку бери из примера. "
        "Ответь ТОЛЬКО валидным JSON со всеми 16 ключами.\n\n"
        "ДАННЫЕ АНКЕТЫ:\n"
        f"```json\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n```"
    )
    print(f"  LLM: пишу контент ({MODEL}, max_tokens={MAX_TOKENS})…")
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    raw = resp.choices[0].message.content or ""
    content = parse_content_json(raw)
    print(f"  ✓ контент получен ({len(json.dumps(content, ensure_ascii=False))} симв., все ключи на месте)")
    return content


def partner_path(draft: dict, lang_prefix: str = "") -> str:
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    country = draft.get("country", "ua")
    lp = f"{lang_prefix}/" if lang_prefix else ""
    return f"{country}/{lp}{kind}/{draft['id']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--publish", action="store_true", help="в прод (иначе _pending_partner)")
    args = ap.parse_args()

    draft_file = DRAFTS / f"{args.id}.json"
    if not draft_file.exists():
        sys.exit(f"❌ Нет анкеты: {draft_file}")
    draft = json.loads(draft_file.read_text(encoding="utf-8"))
    print(f"Партнёр: {draft.get('name')} ({draft.get('type')}, {args.id})")

    content = generate_content(draft)

    from render_partner import build_page
    html = build_page(draft, content)

    if args.publish:
        out = REPO_ROOT / partner_path(draft) / "index.html"
    else:
        out = PENDING / args.id / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ Страница собрана (шаблон): {out} ({len(html)} символов)")


if __name__ == "__main__":
    main()
