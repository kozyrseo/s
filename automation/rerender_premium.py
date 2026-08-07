#!/usr/bin/env python3
"""
KOZYR — переоформить УЖЕ опубликованные статьи под новый премиум-шаблон.

Берёт готовый HTML старой статьи, вытаскивает контент (h1, лид, тело, FAQ,
дата, meta), и заново собирает страницу через новый premium-шаблон с
enhancer'ом. Тексты статей НЕ меняются — только оформление.

Запуск:  python3 automation/rerender_premium.py <slug> <lang>
"""
import re, sys, html as htmlmod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body_enhance
from lang_config import get_cfg

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "templates" / "article.html"


def extract(slug, lang):
    sub = "ua/blog" if lang == "ru" else "ua/uk/blog"
    # Читаем ИСХОДНУЮ (до премиума) статью из чистого архива, если есть,
    # иначе — из текущего дерева. Это исключает двойную обработку.
    pristine = Path("/home/claude/k2/s-main") / sub / slug / "index.html"
    p = pristine if pristine.exists() else (ROOT / sub / slug / "index.html")
    h = p.read_text(encoding="utf-8")

    def m1(pat, flags=0):
        m = re.search(pat, h, flags)
        return m.group(1) if m else ""

    title = htmlmod.unescape(m1(r'<title>(.*?)</title>', re.DOTALL))
    desc = ""
    md = re.search(r'<meta name="description" content="([^"]*)"', h)
    if md:
        desc = htmlmod.unescape(md.group(1))
    h1 = htmlmod.unescape(re.sub("<[^>]+>", "", m1(r"<h1>(.*?)</h1>", re.DOTALL))).strip()
    lede = htmlmod.unescape(re.sub("<[^>]+>", "", m1(r'<p class="lede">(.*?)</p>', re.DOTALL))).strip()
    date = m1(r'<time datetime="([^"]+)"') or "2026-07-27"
    date_disp = htmlmod.unescape(m1(r'<time datetime="[^"]+">(.*?)</time>', re.DOTALL)) or date
    # original reading time (e.g. "11 мин чтения") if present
    rt = m1(r'(\d+)\s*(?:мин|хв)\s*(?:чтения|читання)')
    # если порт-копию уже пересобрали (rt мог сброситься) — берём из исходного архива
    if not rt:
        pristine = Path("/home/claude/k2/s-main") / ("ua/blog" if lang == "ru" else "ua/uk/blog") / slug / "index.html"
        if pristine.exists():
            pm = re.search(r'(\d+)\s*(?:мин|хв)\s*(?:чтения|читання)', pristine.read_text(encoding="utf-8"))
            if pm:
                rt = pm.group(1)

    # body
    om = re.search(r'<div class="post-body"[^>]*>', h)
    s = om.end()
    fm = re.search(r'<(?:section class="faq-section"|aside class="author)', h[s:])
    e = s + fm.start() if fm else len(h)
    seg = h[s:e]
    ci = seg.rstrip().rfind("</div>")
    body = seg[:ci].strip() if ci != -1 else seg.strip()

    # takeaways (existing block, reuse its <li>s)
    tk = re.search(r'<(?:aside|div)[^>]*key-takeaways[^>]*>.*?</(?:aside|div)>', h, re.DOTALL)
    takeaways_items = []
    if tk:
        takeaways_items = re.findall(r"<li>(.*?)</li>", tk.group(0), re.DOTALL)

    # FAQ
    faq = []
    fs = re.search(r'<section class="faq-section".*?</section>', h, re.DOTALL)
    if fs:
        for q, a in re.findall(
            r"<summary>(.*?)</summary>.*?<(?:div class=\"faq-answer\"|p)>(.*?)</(?:div|p)>",
            fs.group(0), re.DOTALL,
        ):
            faq.append((htmlmod.unescape(re.sub("<[^>]+>", "", q)).strip(),
                        htmlmod.unescape(re.sub("<[^>]+>", "", a)).strip()))

    # hero + og image
    hero = (ROOT / sub / slug / "hero.webp")
    has_hero = hero.exists()
    # article tag from breadcrumb/eyebrow
    tag = htmlmod.unescape(m1(r'<div class="eyebrow">(.*?)</div>', re.DOTALL)).replace("Блог KOZYR", "").strip() or "Рейкбек"

    return dict(title=title, desc=desc, h1=h1, lede=lede, date=date,
                date_disp=date_disp, body=body, takeaways=takeaways_items,
                faq=faq, has_hero=has_hero, tag=tag, sub=sub, rt=rt)


def build_takeaways(items):
    if not items:
        return ""
    lis = "\n".join("    <li>%s</li>" % re.sub("<[^>]+>", "", it).strip() for it in items)
    return (
        '<aside class="key-takeaways reveal">\n'
        '  <div class="takeaways-label">Ключевые выводы</div>\n'
        "  <ul>\n" + lis + "\n  </ul>\n</aside>"
    )


def build_faq(faq, lang):
    if not faq:
        return ""
    label = "Частые вопросы" if lang == "ru" else "Часті питання"
    items = ""
    for q, a in faq:
        items += (
            "<details>\n<summary>%s</summary>\n<p>%s</p>\n</details>\n"
            % (htmlmod.escape(q), htmlmod.escape(a))
        )
    return ('<section class="faq-section reveal" aria-label="FAQ">\n'
            "<h2>%s</h2>\n%s</section>" % (label, items))


def main():
    slug, lang = sys.argv[1], sys.argv[2]
    cfg = get_cfg(lang)
    ui = cfg["ui"]
    d = extract(slug, lang)
    tpl = TEMPLATE.read_text(encoding="utf-8")

    # enhance body (tables, dropcap, partners marker if present)
    body_html = body_enhance.enhance_body_html(d["body"], lang)

    hero_media = ""
    if d["has_hero"]:
        alt = ("Иллюстрация к статье: " if lang == "ru" else "Ілюстрація до статті: ") + d["h1"]
        hero_media = ('<div class="post-hero__media"><img src="hero.webp" alt="%s" '
                      'width="1536" height="1024" loading="eager" fetchpriority="high"></div>'
                      % htmlmod.escape(alt))

    canonical = "%s%s/" % (cfg["blog_url"], slug)
    site = "https://kozyr.club"
    reps = {
        "{{META_TITLE}}": htmlmod.escape(d["title"]),
        "{{META_DESCRIPTION}}": htmlmod.escape(d["desc"]),
        "{{META_DESCRIPTION_JSON}}": d["desc"].replace('"', '\\"'),
        "{{CANONICAL_URL}}": site + canonical,
        "{{H1_TITLE}}": htmlmod.escape(d["h1"]),
        "{{H1_TITLE_JSON}}": d["h1"].replace('"', '\\"'),
        "{{LEDE}}": htmlmod.escape(d["lede"]),
        "{{DATE_PUBLISHED}}": d["date"],
        "{{DATE_MODIFIED}}": d["date"],
        "{{DATE_PUBLISHED_DISPLAY}}": d["date_disp"],
        "{{LAST_UPDATED_BLOCK}}": "",
        "{{READING_TIME}}": d["rt"] or str(max(5, len(re.sub("<[^>]+>", "", d["body"]).split()) // 180)),
        "{{HERO_MEDIA_BLOCK}}": hero_media,
        "{{ARTICLE_TAG}}": htmlmod.escape(d["tag"]),
        "{{UI_IN_THIS_ARTICLE}}": ui.get("in_this_article", "В этой статье"),
        "{{KEY_TAKEAWAYS_BLOCK}}": build_takeaways(d["takeaways"]),
        "{{ARTICLE_BODY_HTML}}": body_html,
        "{{FAQ_BLOCK}}": build_faq(d["faq"], lang),
        "{{FAQ_JSONLD}}": "",
        "{{RELATED_ARTICLES_HTML}}": "",
        "{{OG_IMAGE_URL}}": (site + canonical + "hero.webp") if d["has_hero"] else (site + "/og-image.png"),
        "{{OG_IMAGE_WIDTH}}": "1536" if d["has_hero"] else "1200",
        "{{OG_IMAGE_HEIGHT}}": "1024" if d["has_hero"] else "630",
        "{{OG_IMAGE_ALT}}": htmlmod.escape(d["h1"]),
        "{{CTA_KEYWORD}}": d["tag"].lower(),
    }
    # UI + config placeholders
    for k, v in ui.items():
        reps["{{UI_%s}}" % k.upper()] = str(v)
    reps["{{HTML_LANG}}"] = cfg["html_lang"]
    reps["{{HOME_URL}}"] = cfg["home_url"]
    reps["{{BLOG_URL}}"] = cfg["blog_url"]
    reps["{{SITE_ORIGIN}}"] = site

    out = tpl
    for k, v in reps.items():
        out = out.replace(k, v)
    # strip any remaining unknown placeholders to be safe
    out = re.sub(r"\{\{[A-Z_]+\}\}", "", out)

    dest = ROOT / d["sub"] / slug / "index.html"
    dest.write_text(out, encoding="utf-8")
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    print("✅ %s (%s) — premium re-render. leftover placeholders: %d" % (slug, lang, len(left)))


if __name__ == "__main__":
    main()
