#!/usr/bin/env python3
"""
KOZYR — серверный рендер отзывов + починка aggregateRating.

ЗАЧЕМ
-----
Отзывы игроков вставлялись только через kozyr-reviews.js (клиентский JS).
AI-краулеры (GPTBot, ClaudeBot, PerplexityBot, CCBot) JS не исполняют →
для них отзывы были невидимы, а единственный видимый рейтинг-сигнал —
JSON-LD aggregateRating — был битый: ratingCount=1, ratingValue=8.2 по
шкале 0–10 (это Kozyr Score, редакторская оценка), тогда как на странице
5 отзывов игроков по шкале 1–5. Google требует, чтобы aggregateRating
отражал реально показанные отзывы — иначе игнор сниппета или санкция.

ЧТО ДЕЛАЕТ
----------
Из reviews.json (единый источник правды) на КАЖДОЙ денежной странице:
  1. Пишет СТАТИЧНЫЙ HTML отзывов (та же разметка kz-rev-*, что рендерил
     JS, поэтому существующий CSS подхватывается) — краулеры видят отзывы.
  2. Переписывает JSON-LD aggregateRating под реальные отзывы:
     ratingValue = среднее по 1–5, bestRating=5, ratingCount=reviewCount=N,
     и добавляет массив отдельных Review-объектов. Битая оценка 8.2/10
     из aggregateRating убирается (Kozyr Score остаётся отдельно на странице).

kozyr-reviews.js остаётся подключённым как progressive enhancement — для
JS-пользователей он перерисует тот же контент; краулеры видят статику.

ИДЕМПОТЕНТНОСТЬ
---------------
Статика пишется между маркерами <!--RV-SUM:id--> / <!--RV-LIST:id-->,
JSON-LD переписывается через json-round-trip. Повторный запуск даёт
идентичный результат.

ЗАПУСК
------
  python automation/build_reviews.py            # записать в страницы
  python automation/build_reviews.py --check     # не писать; exit 1 если дрейф (CI)
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_JSON = ROOT / "reviews.json"
JS_FILE = ROOT / "assets" / "kozyr-reviews.js"
PARTNER_DISPLAY = {"pokerbet": "PokerBet", "klubok": "KlubOk"}

# (partner_id, lang, путь к странице)
PAGES = [
    ("pokerbet", "ru", ROOT / "ua" / "rooms" / "pokerbet" / "index.html"),
    ("pokerbet", "uk", ROOT / "ua" / "uk" / "rooms" / "pokerbet" / "index.html"),
    ("klubok", "ru", ROOT / "ua" / "clubs" / "klubok" / "index.html"),
    ("klubok", "uk", ROOT / "ua" / "uk" / "clubs" / "klubok" / "index.html"),
]

# Метки-локали (из I18N в kozyr-reviews.js — держим синхронно)
I18N = {
    "ru": {
        "of": "из", "basedOn": "на основе", "verified": "проверено",
        "verifyTip": "Скрин депозита или вывода подтверждён",
        "addReview": "Оставить отзыв",
        "plural2": ["отзыва", "отзывов", "отзывов"],
        "plural3": ["отзыв", "отзыва", "отзывов"],
        "months": ["января", "февраля", "марта", "апреля", "мая", "июня",
                   "июля", "августа", "сентября", "октября", "ноября", "декабря"],
        "ariaRating": "Рейтинг",
    },
    "uk": {
        "of": "з", "basedOn": "на основі", "verified": "перевірено",
        "verifyTip": "Скрін депозиту або виведення підтверджений",
        "addReview": "Залишити відгук",
        "plural2": ["відгуку", "відгуків", "відгуків"],
        "plural3": ["відгук", "відгуки", "відгуків"],
        "months": ["січня", "лютого", "березня", "квітня", "травня", "червня",
                   "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"],
        "ariaRating": "Рейтинг",
    },
}


# ── утилиты ────────────────────────────────────────────────────────────────
def esc(s: str) -> str:
    """Тот же escape, что в виджете: & < > \"."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def plural(n: int, forms: list[str]) -> str:
    """Славянская плюрализация (как в kozyr-reviews.js)."""
    n = abs(n) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return forms[2]
    if n1 == 1:
        return forms[0]
    if 2 <= n1 <= 4:
        return forms[1]
    return forms[2]


def format_date(iso: str, months: list[str]) -> str:
    """'2026-07-14' → '14 июля 2026' (RU) / '14 липня 2026' (UK)."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {months[int(m) - 1]} {y}"
    except Exception:
        return iso


def stars_html(rating: float, size: str, aria_label: str) -> str:
    full = int(rating)
    half = (rating - full) >= 0.5
    out = [f'<span class="kz-stars kz-stars--{size}" aria-label="{aria_label}">']
    for i in range(1, 6):
        state = "on" if i <= full else ("half" if (i == full + 1 and half) else "off")
        out.append(f'<span class="kz-star kz-star--{state}" aria-hidden="true">★</span>')
    out.append("</span>")
    return "".join(out)


def review_card_html(r: dict, lab: dict) -> str:
    badge = ""
    if r.get("verified"):
        badge = (f'<span class="kz-rev-verify" title="{lab["verifyTip"]}">'
                 '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" '
                 'stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/>'
                 f'</svg> {lab["verified"]}</span>')
    aria = f'{lab["ariaRating"]} {r["rating"]:.1f} {lab["of"]} 5'
    return (
        '<article class="kz-rev">'
        '<header class="kz-rev__head">'
        '<div class="kz-rev__who">'
        f'<span class="kz-rev__avatar" aria-hidden="true">{esc(r["author"][:1])}</span>'
        '<div>'
        f'<div class="kz-rev__name">{esc(r["author"])}{badge}</div>'
        f'<time class="kz-rev__date" datetime="{esc(r["date"])}">'
        f'{esc(format_date(r["date"], lab["months"]))}</time>'
        '</div>'
        '</div>'
        f'{stars_html(r["rating"], "sm", aria)}'
        '</header>'
        f'<p class="kz-rev__text">{esc(r["text"])}</p>'
        '</article>'
    )


def summary_html(reviews: list[dict], lab: dict) -> str:
    n = len(reviews)
    avg = sum(r["rating"] for r in reviews) / n
    aria = f'{lab["ariaRating"]} {avg:.1f} {lab["of"]} 5'
    return (
        '<span class="kz-rev-sum">'
        f'{stars_html(avg, "sm", aria)}'
        f'<span class="kz-rev-sum__num"><strong>{avg:.1f}</strong>&nbsp;{lab["of"]}&nbsp;5</span>'
        '<span class="kz-rev-sum__sep" aria-hidden="true">·</span>'
        f'<span class="kz-rev-sum__cnt">{n}&nbsp;{plural(n, lab["plural3"])}</span>'
        '</span>'
    )


def full_block_html(reviews: list[dict], lab: dict) -> str:
    n = len(reviews)
    avg = sum(r["rating"] for r in reviews) / n
    # Сортировка: новые сверху (как в виджете)
    ordered = sorted(reviews, key=lambda r: r["date"], reverse=True)
    aria = f'{lab["ariaRating"]} {avg:.1f} {lab["of"]} 5'
    cards = "".join(review_card_html(r, lab) for r in ordered)
    return (
        '<div class="kz-rev-block">'
        '<div class="kz-rev-topline">'
        '<div class="kz-rev-avg">'
        f'<div class="kz-rev-avg__num">{avg:.1f}<span class="kz-rev-avg__of">/5</span></div>'
        '<div class="kz-rev-avg__meta">'
        f'{stars_html(avg, "sm", aria)}'
        f'<span class="kz-rev-avg__cnt">{lab["basedOn"]} {n} {plural(n, lab["plural2"])}</span>'
        '</div>'
        '</div>'
        f'<a class="kz-rev-add" href="https://t.me/kozyr_support" target="_blank" '
        f'rel="noopener">{lab["addReview"]}</a>'
        '</div>'
        f'<div class="kz-rev-list">{cards}</div>'
        '</div>'
    )


# ── JSON-LD ────────────────────────────────────────────────────────────────
def build_schema(reviews: list[dict]) -> tuple[dict, list[dict]]:
    n = len(reviews)
    avg = round(sum(r["rating"] for r in reviews) / n, 1)
    agg = {
        "@type": "AggregateRating",
        "ratingValue": str(avg),
        "bestRating": "5",
        "worstRating": "1",
        "ratingCount": str(n),
        "reviewCount": str(n),
    }
    items = []
    for r in sorted(reviews, key=lambda x: x["date"], reverse=True):
        items.append({
            "@type": "Review",
            "author": {"@type": "Person", "name": r["author"]},
            "datePublished": r["date"],
            "reviewRating": {"@type": "Rating", "ratingValue": str(r["rating"]),
                             "bestRating": "5", "worstRating": "1"},
            "reviewBody": r["text"],
        })
    return agg, items


def rewrite_product_jsonld(page_html: str, agg: dict, review_items: list[dict]) -> str:
    """
    Находит JSON-LD со свойством aggregateRating (Product/Review), заменяет
    aggregateRating и review на корректные. json-round-trip — устойчиво к
    вложенности и идемпотентно.
    """
    def repl(m: re.Match) -> str:
        raw = m.group(1)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return m.group(0)  # не наш блок
        if not (isinstance(obj, dict) and "aggregateRating" in obj):
            return m.group(0)
        obj["aggregateRating"] = agg
        obj["review"] = review_items
        new = json.dumps(obj, ensure_ascii=False, indent=2)
        return m.group(0).replace(raw, "\n" + new + "\n")

    pattern = re.compile(
        r'<script type="application/ld\+json">(.*?)</script>', re.S)
    return pattern.sub(repl, page_html, count=0)


# ── инъекция статики между маркерами ───────────────────────────────────────
def inject_div(page_html: str, attr: str, partner: str, marker: str, inner: str) -> str:
    """
    Заменяет содержимое <div ... attr="partner" ...> на inner, обёрнутый в
    маркеры <!--marker:partner-->…<!--/marker:partner-->. Матч до маркера,
    а не до </div> — устойчиво к вложенным div внутри inner.
    """
    start = f"<!--{marker}:{partner}-->"
    end = f"<!--/{marker}:{partner}-->"
    wrapped = start + inner + end
    pat = re.compile(
        r'(<div[^>]*\b' + re.escape(attr) + r'="' + re.escape(partner) + r'"[^>]*>)'
        r'(?:' + re.escape(start) + r'.*?' + re.escape(end) + r')?'
        r'\s*(</div>)', re.S)
    if not pat.search(page_html):
        return page_html  # контейнера нет — молча пропускаем
    return pat.sub(lambda m: m.group(1) + wrapped + m.group(2), page_html, count=1)


# ── kozyr-reviews.js (клиентский рендер для JS-пользователей) ───────────────
def js_escape(s: str) -> str:
    """Экранирование для одинарных JS-строк: \\ и '."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def render_js_array(reviews: list[dict]) -> str:
    """Собирает `var REVIEWS = [...]` из reviews.json (тот же порядок/группировка)."""
    from collections import OrderedDict
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for r in reviews:
        groups.setdefault((r["partner"], r["lang"]), []).append(r)
    out = ["var REVIEWS = ["]
    for (partner, lang), items in groups.items():
        disp = PARTNER_DISPLAY.get(partner, partner)
        out.append(f"    /* ================ {disp} · {lang.upper()} ================ */")
        for r in items:
            verified = "true" if r.get("verified") else "false"
            out.append("    {")
            out.append(f"      id: '{r['id']}', partner: '{r['partner']}', lang: '{r['lang']}',")
            out.append(f"      author: '{js_escape(r['author'])}', rating: {r['rating']}, "
                       f"date: '{r['date']}', verified: {verified}, country: '{r.get('country', 'ua')}',")
            out.append(f"      text: '{js_escape(r['text'])}'")
            out.append("    },")
    out.append("  ];")
    return "\n".join(out)


def rewrite_js(js_text: str, reviews: list[dict]) -> str:
    """Заменяет тело массива REVIEWS. Идемпотентно (тот же вход → тот же выход)."""
    new_array = render_js_array(reviews)
    return re.sub(r'var REVIEWS = \[[\s\S]*?\n  \];', new_array, js_text, count=1)


def js_cache_version(js_text: str) -> str:
    """Cache-buster = 8 символов md5 содержимого JS.
    При любой правке отзывов версия меняется → Cloudflare/браузер тянут свежий
    файл; при неизменном содержимом версия та же → идемпотентно."""
    return hashlib.md5(js_text.encode("utf-8")).hexdigest()[:8]


def bump_cache_buster(page_html: str, version: str) -> str:
    """Проставляет ?v=<version> у ссылок на kozyr-reviews.js в странице."""
    return re.sub(r'(kozyr-reviews\.js\?v=)[A-Za-z0-9._-]+',
                  lambda m: m.group(1) + version, page_html)


def process_page(partner: str, lang: str, reviews: list[dict], page_html: str) -> str:
    lab = I18N[lang]
    part = [r for r in reviews if r["partner"] == partner and r["lang"] == lang]
    if not part:
        return page_html
    out = page_html
    out = inject_div(out, "data-reviews-summary", partner, "RV-SUM",
                     summary_html(part, lab))
    out = inject_div(out, "data-reviews", partner, "RV-LIST",
                     full_block_html(part, lab))
    agg, items = build_schema(part)
    out = rewrite_product_jsonld(out, agg, items)
    return out


# ── main ───────────────────────────────────────────────────────────────────
def load_reviews() -> list[dict]:
    try:
        data = json.loads(REVIEWS_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"❌ Не найден {REVIEWS_JSON}")
    except json.JSONDecodeError as e:
        sys.exit(f"❌ Битый JSON в {REVIEWS_JSON}: {e}")
    return data.get("reviews", [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Серверный рендер отзывов + aggregateRating")
    ap.add_argument("--check", action="store_true",
                    help="не писать; exit 1 если страница разошлась")
    args = ap.parse_args()

    reviews = load_reviews()
    drift = False
    changed = 0

    # Сначала считаем новый JS и его версию (хеш) — она пойдёт в cache-buster
    # ссылок на страницах, чтобы кеш гарантированно сбросился при правке отзывов.
    cur_js = new_js = None
    js_ver = None
    if JS_FILE.exists():
        cur_js = JS_FILE.read_text(encoding="utf-8")
        new_js = rewrite_js(cur_js, reviews)
        js_ver = js_cache_version(new_js)

    for partner, lang, path in PAGES:
        if not path.exists():
            print(f"⚠️  нет страницы: {path}")
            continue
        cur = path.read_text(encoding="utf-8")
        new = process_page(partner, lang, reviews, cur)
        if js_ver:
            new = bump_cache_buster(new, js_ver)
        if new != cur:
            if args.check:
                drift = True
                print(f"⚠️  {path.relative_to(ROOT)} — отзывы/aggregateRating/версия "
                      f"разошлись; запусти: python automation/build_reviews.py")
            else:
                path.write_text(new, encoding="utf-8")
                changed += 1
                print(f"✓ обновлено: {path.relative_to(ROOT)}")

    # kozyr-reviews.js — рендер для JS-пользователей: держим тексты из reviews.json
    if new_js is not None and new_js != cur_js:
        if args.check:
            drift = True
            print("⚠️  assets/kozyr-reviews.js разошёлся с reviews.json; "
                  "запусти: python automation/build_reviews.py")
        else:
            JS_FILE.write_text(new_js, encoding="utf-8")
            changed += 1
            print(f"✓ обновлено: assets/kozyr-reviews.js (v={js_ver})")

    if args.check:
        if drift:
            return 1
        print("✓ Отзывы, aggregateRating и cache-buster актуальны.")
        return 0

    print(f"Готово. Изменено файлов: {changed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
