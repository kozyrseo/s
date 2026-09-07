#!/usr/bin/env python3
"""
KOZYR — рендер страницы партнёра из шаблона (Стадия 2 шаблонной системы).

Собирает готовый HTML из:
  • partner_template.html.j2 — статичный скелет (CSS/JS/навигация/футер) +
    Jinja-слоты для данных и прозы;
  • анкеты _partner_drafts/{id}.json — структурные данные (имя, валюта, рейкбек,
    плюсы/минусы, FAQ, лимиты, логотип, реф-ссылка);
  • контента (проза + rich-блоки: about, депозиты, бонусы и т.д.) — на Стадии 3
    его пишет LLM компактным JSON (~11 КБ вместо 85 КБ полной страницы).

Данные (плюсы/минусы/FAQ) идут из анкеты циклами — LLM их не трогает, поэтому
они не теряются. Проза — из контента. Структура — из шаблона, её не сломать.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT  = AUTOMATION.parent
TEMPLATE   = AUTOMATION / "partner_template.html.j2"
DRAFTS     = REPO_ROOT / "_partner_drafts"


def build_page(draft: dict, content: dict) -> str:
    """Рендерит HTML страницы партнёра из шаблона + анкеты + контента."""
    from jinja2 import Template
    tpl = TEMPLATE.read_text(encoding="utf-8")
    p = dict(draft)
    p["kind"] = "clubs" if draft.get("type") == "club" else "rooms"
    # реф-ссылка кнопки: из анкеты, иначе плейсхолдер саппорта
    p["ref_url_final"] = (draft.get("ref_url") or "").strip() or "https://t.me/kozyr_support"
    p.setdefault("pros", [])
    p.setdefault("cons", [])
    p.setdefault("faq", [])
    return Template(tpl).render(p=p, content=content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="ID партнёра из _partner_drafts/")
    ap.add_argument("--content", required=True, help="Путь к JSON с контентом (проза)")
    ap.add_argument("--out", default="", help="Куда записать (по умолч. _pending_partner/{id}/index.html)")
    args = ap.parse_args()

    draft_file = DRAFTS / f"{args.id}.json"
    if not draft_file.exists():
        sys.exit(f"❌ Нет анкеты: {draft_file}")
    draft = json.loads(draft_file.read_text(encoding="utf-8"))
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))

    html = build_page(draft, content)

    out = Path(args.out) if args.out else (REPO_ROOT / "_pending_partner" / args.id / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ Страница собрана из шаблона: {out} ({len(html)} символов)")


if __name__ == "__main__":
    main()
