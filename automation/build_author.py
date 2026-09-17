#!/usr/bin/env python3
"""
KOZYR — генератор страницы автора (E-E-A-T).

ЗАЧЕМ
-----
В статьях автор в schema назывался «KOZYR» (бренд), а в подписи — «Никита
Волошин», и вело это на главную. Для Google это рассогласование + «пустой»
автор без страницы. Здесь создаётся настоящая страница автора с разметкой
ProfilePage/Person, на которую теперь указывают @id/url в статьях и ссылка
в байлайне. Данные автора берутся из lang_config (единый источник).

Создаёт:
  /ua/blog/authors/nikita/index.html      (ru)
  /ua/uk/blog/authors/nikita/index.html   (uk)

Запуск:
  python automation/build_author.py            # записать страницы
  python automation/build_author.py --check     # exit 1 если дрейф (CI)
"""

from __future__ import annotations

import argparse
import html as html_mod
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lang_config import get_cfg, SITE_URL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PHOTO = "/ua/blog/authors/nikita.webp"

# Специализация автора (schema knowsAbout + видимые чипы), по языкам
KNOWS = {
    "ru": ["Покерный рейкбек", "Эффективный рейкбек", "Обзоры румов",
           "Обзоры клубов", "Приватные клубы ClubGG", "Мягкие поля и выбор стола",
           "Лимиты и выплаты", "Платёжки и криптовалюта", "Банкролл-менеджмент",
           "Правовой статус и налоги"],
    "uk": ["Покерний рейкбек", "Ефективний рейкбек", "Огляди румів",
           "Огляди клубів", "Приватні клуби ClubGG", "М'які поля та вибір столу",
           "Ліміти та виплати", "Платіжки та криптовалюта", "Банкрол-менеджмент",
           "Правовий статус і податки"],
}

# Локализованные подписи страницы
T = {
    "ru": {
        "title": "{name} — рейкбек-аналитик KOZYR",
        "desc": "{name} — автор блога KOZYR, рейкбек-аналитик. 10+ лет в онлайн-покере, разбирает условия румов и клубов по реальным данным.",
        "eyebrow": "Автор",
        "specializes": "Специализация",
        "articles_h": "Материалы автора",
        "articles_p": "Разборы условий румов и клубов — рейкбек, поля, выводы, без маркетингового тумана.",
        "to_blog": "Все статьи в блоге →",
        "to_catalog": "Каталог рейкбек-сделок →",
        "home": "На главную",
        "blog": "Блог",
        "disclaimer": "Материалы носят информационный характер. Только для лиц от 21 года. Играйте ответственно.",
    },
    "uk": {
        "title": "{name} — рейкбек-аналітик KOZYR",
        "desc": "{name} — автор блогу KOZYR, рейкбек-аналітик. 10+ років в онлайн-покері, розбирає умови румів і клубів за реальними даними.",
        "eyebrow": "Автор",
        "specializes": "Спеціалізація",
        "articles_h": "Матеріали автора",
        "articles_p": "Розбори умов румів і клубів — рейкбек, поля, виведення, без маркетингового туману.",
        "to_blog": "Усі статті в блозі →",
        "to_catalog": "Каталог рейкбек-угод →",
        "home": "На головну",
        "blog": "Блог",
        "disclaimer": "Матеріали мають інформаційний характер. Тільки для осіб від 21 року. Грайте відповідально.",
    },
}


def esc(s: str) -> str:
    return html_mod.escape(str(s), quote=True)


def author_url(lang: str) -> str:
    cfg = get_cfg(lang)
    return f"{SITE_URL}{cfg['blog_url']}authors/nikita/"


def render(lang: str) -> str:
    cfg = get_cfg(lang)
    ui = cfg["ui"]
    t = T[lang]
    name = ui.get("author_name", "Никита Волошин")
    role = ui.get("author_role", "Рейкбек-аналитик · KOZYR")
    bio = ui.get("author_bio", "")
    knows = KNOWS[lang]
    url = author_url(lang)
    url_ru = author_url("ru")
    url_uk = author_url("uk")
    blog_url = f"{SITE_URL}{cfg['blog_url']}"
    home_url = f"{SITE_URL}{cfg['home_url']}"
    html_lang = "uk" if lang == "uk" else "ru"

    title = t["title"].format(name=name)
    desc = t["desc"].format(name=name)
    chips = "".join(f'<li>{esc(k)}</li>' for k in knows)
    knows_json = ", ".join(f'"{esc(k)}"' for k in knows)

    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="theme-color" content="#000714">
<title>{esc(title)} | KOZYR</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<link rel="alternate" hreflang="ru-UA" href="{url_ru}">
<link rel="alternate" hreflang="uk-UA" href="{url_uk}">
<link rel="alternate" hreflang="x-default" href="{SITE_URL}/">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<meta property="og:type" content="profile">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{SITE_URL}{PHOTO}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "ProfilePage",
  "mainEntity": {{
    "@type": "Person",
    "@id": "{url}#person",
    "name": "{esc(name)}",
    "url": "{url}",
    "image": "{SITE_URL}{PHOTO}",
    "jobTitle": "{esc(role.split('·')[0].strip())}",
    "description": "{esc(bio)}",
    "knowsAbout": [{knows_json}],
    "worksFor": {{"@type": "Organization", "name": "KOZYR", "url": "{SITE_URL}/"}}
  }}
}}
</script>
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {{"@type": "ListItem", "position": 1, "name": "{esc(t['home'])}", "item": "{home_url}"}},
    {{"@type": "ListItem", "position": 2, "name": "{esc(t['blog'])}", "item": "{blog_url}"}},
    {{"@type": "ListItem", "position": 3, "name": "{esc(name)}", "item": "{url}"}}
  ]
}}
</script>
<style>
:root{{--bg:#000714;--card:rgba(255,255,255,.04);--bd:rgba(255,255,255,.10);--ink:#E8ECF7;--ink2:#A8B0C8;--accent:#2668FF;--gold:#E4B95B}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);font-family:'Inter',system-ui,sans-serif;line-height:1.6;min-height:100vh}}
a{{color:inherit}}
.wrap{{max-width:860px;margin:0 auto;padding:32px 20px 64px}}
nav.crumbs{{font-size:13px;color:var(--ink2);margin-bottom:24px}}
nav.crumbs a{{text-decoration:none;color:var(--ink2)}}
nav.crumbs a:hover{{color:var(--ink)}}
.eyebrow{{font-family:'Space Grotesk',sans-serif;font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:600}}
/* HERO — тёмный, с градиентом и золотым свечением (стиль about) */
.hero{{position:relative;overflow:hidden;border-radius:26px;padding:40px 40px 36px;margin:14px 0 26px;
  background:radial-gradient(130% 170% at 8% -10%, #17244E 0%, #0A1128 52%, #06060F 100%);
  box-shadow:0 26px 64px rgba(6,17,40,.38)}}
.hero::before{{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(55% 120% at 90% -10%, rgba(228,185,91,.20), transparent 62%)}}
.hero>*{{position:relative}}
.hero-top{{display:flex;gap:24px;align-items:center}}
.hero img{{width:110px;height:110px;border-radius:20px;object-fit:cover;
  border:2px solid rgba(255,255,255,.14);flex:0 0 auto;box-shadow:0 10px 28px rgba(0,0,0,.4)}}
h1{{font-family:'Space Grotesk',sans-serif;font-size:38px;line-height:1.1;letter-spacing:-0.02em;margin-bottom:8px;color:#fff}}
.role{{font-family:'JetBrains Mono',monospace;color:var(--gold);font-weight:600;font-size:13px;letter-spacing:.04em}}
.bio{{color:rgba(255,255,255,.82);font-size:16px;line-height:1.65;margin:22px 0 0;max-width:64ch}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:26px;margin:18px 0}}
.card h2{{font-family:'Space Grotesk',sans-serif;font-size:19px;margin-bottom:16px;color:#fff}}
.chips{{list-style:none;display:flex;flex-wrap:wrap;gap:9px}}
.chips li{{background:rgba(38,104,255,.12);border:1px solid rgba(38,104,255,.3);color:#cfe0ff;font-size:13px;padding:8px 14px;border-radius:999px}}
.links{{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}}
.links a{{text-decoration:none;color:#fff;background:var(--accent);padding:12px 20px;border-radius:12px;font-weight:600;font-size:14px;font-family:'Space Grotesk',sans-serif;transition:background .15s ease}}
.links a:hover{{background:#1E52D9}}
.links a.ghost{{background:rgba(255,255,255,.06);border:1px solid var(--bd);color:var(--ink)}}
.links a.ghost:hover{{background:rgba(255,255,255,.1)}}
.disc{{color:var(--ink2);font-size:12px;margin-top:30px;border-top:1px solid var(--bd);padding-top:16px}}
@media(max-width:560px){{.hero{{padding:28px 22px 24px}}.hero-top{{flex-direction:column;text-align:center;gap:16px}}h1{{font-size:28px}}.bio{{font-size:15px}}}}
</style>
<script src="/analytics.js" defer></script>
</head>
<body>
<main class="wrap">
  <nav class="crumbs" aria-label="breadcrumb">
    <a href="{home_url}">{esc(t['home'])}</a> · <a href="{blog_url}">{esc(t['blog'])}</a> · {esc(name)}
  </nav>
  <div class="hero">
    <span class="eyebrow">{esc(t['eyebrow'])}</span>
    <div class="hero-top" style="margin-top:14px">
      <img src="{PHOTO}" alt="{esc(name)}" width="110" height="110">
      <div>
        <h1>{esc(name)}</h1>
        <div class="role">{esc(role)}</div>
      </div>
    </div>
    <p class="bio">{esc(bio)}</p>
  </div>

  <div class="card">
    <h2>{esc(t['specializes'])}</h2>
    <ul class="chips">{chips}</ul>
  </div>

  <div class="card">
    <h2>{esc(t['articles_h'])}</h2>
    <p style="color:var(--ink2);font-size:15px;margin-bottom:14px">{esc(t['articles_p'])}</p>
    <div class="links">
      <a href="{blog_url}">{esc(t['to_blog'])}</a>
      <a class="ghost" href="{home_url}">{esc(t['to_catalog'])}</a>
    </div>
  </div>

  <p class="disc">{esc(t['disclaimer'])}</p>
</main>
</body>
</html>
"""


PAGES = {
    "ru": ROOT / "ua" / "blog" / "authors" / "nikita" / "index.html",
    "uk": ROOT / "ua" / "uk" / "blog" / "authors" / "nikita" / "index.html",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Генератор страницы автора")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    drift = False
    for lang, path in PAGES.items():
        content = render(lang)
        if args.check:
            cur = path.read_text(encoding="utf-8") if path.exists() else ""
            if cur != content:
                drift = True
                print(f"⚠️  {path.relative_to(ROOT)} разошлась; запусти build_author.py")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"✓ записано: {path.relative_to(ROOT)}")

    if args.check:
        if drift:
            return 1
        print("✓ Страницы автора актуальны.")
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
