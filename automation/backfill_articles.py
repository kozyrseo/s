#!/usr/bin/env python3
"""
KOZYR — бэкфилл существующих статей под №3 (видимая дата «Обновлено») и
№5 (автор-энтити).

Для КАЖДОЙ уже опубликованной статьи (ua/blog/*/ и ua/uk/blog/*/):
  1. Чинит author в JSON-LD (json-round-trip): name «KOZYR» → реальное имя
     автора, @id/url → страница автора, добавляет image и корректный jobTitle.
  2. Делает имя автора в байлайне и в блоке автора ССЫЛКОЙ на страницу автора.
  3. Видимую дату публикации меняет на дату модификации с меткой
     «Обновлено/Оновлено» (freshness-сигнал для читателей и ИИ). datePublished
     остаётся в schema и og:article:published_time.

Идемпотентно (повторный запуск ничего не меняет) + режим --check для CI.

Запуск:
  python automation/backfill_articles.py            # применить
  python automation/backfill_articles.py --check     # exit 1 если есть что менять
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://kozyr.club"
PHOTO_ABS = f"{SITE}/ua/blog/authors/nikita.webp"
NON_ARTICLE = {"authors", "logos", "tags"}

LANGS = {
    "ru": {
        "dir": ROOT / "ua" / "blog",
        "name": "Никита Волошин",
        "role": "Рейкбек-аналитик",
        "author_url": f"{SITE}/ua/blog/authors/nikita/",
        "updated": "Обновлено",
        "months": ["января", "февраля", "марта", "апреля", "мая", "июня",
                   "июля", "августа", "сентября", "октября", "ноября", "декабря"],
    },
    "uk": {
        "dir": ROOT / "ua" / "uk" / "blog",
        "name": "Микита Волошин",
        "role": "Рейкбек-аналітик",
        "author_url": f"{SITE}/ua/uk/blog/authors/nikita/",
        "updated": "Оновлено",
        "months": ["січня", "лютого", "березня", "квітня", "травня", "червня",
                   "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"],
    },
}


def fmt_date(iso: str, months: list[str]) -> str:
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {months[int(m) - 1]} {y}"
    except Exception:
        return iso


def fix_author_schema(html: str, cfg: dict) -> str:
    """json-round-trip: правит объект author в JSON-LD, где он есть."""
    def repl(m: re.Match) -> str:
        raw = m.group(1)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return m.group(0)
        if not (isinstance(obj, dict) and isinstance(obj.get("author"), dict)):
            return m.group(0)
        au = obj["author"]
        au["@type"] = "Person"
        au["@id"] = cfg["author_url"] + "#person"
        au["name"] = cfg["name"]
        au["jobTitle"] = cfg["role"]
        au["url"] = cfg["author_url"]
        au["image"] = PHOTO_ABS
        au.setdefault("knowsAbout",
                      ["Покерный рейкбек", "Обзоры румов", "Обзоры клубов", "Лимиты и выводы"])
        au["worksFor"] = {"@type": "Organization", "name": "KOZYR", "url": f"{SITE}/"}
        obj["author"] = au
        new = json.dumps(obj, ensure_ascii=False, indent=2)
        return m.group(0).replace(raw, "\n" + new + "\n")

    return re.sub(r'<script type="application/ld\+json">(.*?)</script>',
                  repl, html, flags=re.S)


def link_byline(html: str, cfg: dict) -> str:
    """Имя автора в байлайне и блоке -> ссылка с ПРАВИЛЬНЫМ для языка написанием.
    Устойчиво к обоим написаниям (часть старых UK-страниц содержит русское
    «Никита» в байлайне при украинском «Микита» в схеме) и чинит alt фото."""
    url = cfg["author_url"]
    correct = cfg["name"]
    link = f'<a href="{url}" rel="author">{correct}</a>'
    all_names = ["Никита Волошин", "Микита Волошин"]
    for nm in all_names:
        n = re.escape(nm)
        # 1) alt фото автора -> имя языка
        html = re.sub(
            r'(<img src="/ua/blog/authors/nikita\.webp" alt=")' + n + r'(")',
            lambda m: m.group(1) + correct + m.group(2), html)
        # 2) байлайн: <span>NAME</span> -> ссылка
        html = re.sub(
            r'(<span class="post-hero__author">.*?loading="lazy">)<span>' + n + r'</span>(</span>)',
            lambda m: m.group(1) + link + m.group(2), html, flags=re.S)
        # 3) байлайн уже ссылка (любое написание) -> нормализуем имя/href
        html = re.sub(
            r'(<span class="post-hero__author">.*?loading="lazy">)<a\b[^>]*>' + n + r'</a>(</span>)',
            lambda m: m.group(1) + link + m.group(2), html, flags=re.S)
        # 4) premium name: обычный текст или уже ссылка -> ссылка
        html = re.sub(
            r'(<div class="author-premium__name">)(?:<a\b[^>]*>)?' + n + r'(?:</a>)?(</div>)',
            lambda m: m.group(1) + link + m.group(2), html)
    return html


def get_schema_dates(html: str) -> tuple[str, str]:
    """Возвращает (datePublished, dateModified) из JSON-LD."""
    pub = re.search(r'"datePublished":\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})"', html)
    mod = re.search(r'"dateModified":\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})"', html)
    return (pub.group(1) if pub else "", mod.group(1) if mod else "")


def set_visible_updated(html: str, cfg: dict) -> str:
    """
    Видимая дата: показываем dateModified с меткой «Обновлено».
    Меняем именно байлайновый <time> (после post-hero__author). Идемпотентно:
    если метка уже есть — не трогаем.
    """
    pub, mod = get_schema_dates(html)
    if not mod:
        return html
    label = cfg["updated"]
    disp = fmt_date(mod, cfg["months"])
    new_time = f'<time datetime="{mod}">{label} {disp}</time>'

    # Если уже есть <time> с меткой — идемпотентный выход.
    if re.search(r'<time datetime="[^"]*">' + re.escape(label) + r'\s', html):
        # но убедимся, что дата актуальна (dateModified мог обновиться)
        html2 = re.sub(r'<time datetime="[^"]*">' + re.escape(label) + r'[^<]*</time>',
                       new_time, html, count=1)
        return html2

    # Иначе — заменяем байлайновый <time> (тот, что показывает дату публикации).
    if pub:
        pat = re.compile(r'<time datetime="' + re.escape(pub) + r'">[^<]*</time>')
        if pat.search(html):
            return pat.sub(new_time, html, count=1)
    # Фолбэк: первый <time> в post-hero__meta
    return re.sub(r'(<div class="post-hero__meta">.*?)<time datetime="[^"]*">[^<]*</time>',
                  lambda m: m.group(1) + new_time, html, count=1, flags=re.S)


def process(html: str, cfg: dict) -> str:
    out = fix_author_schema(html, cfg)
    out = link_byline(out, cfg)
    out = set_visible_updated(out, cfg)
    return out


def iter_articles():
    for lang, cfg in LANGS.items():
        d = cfg["dir"]
        if not d.exists():
            continue
        for child in sorted(d.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name in NON_ARTICLE:
                continue
            idx = child / "index.html"
            if idx.exists():
                yield lang, cfg, idx


def main() -> int:
    ap = argparse.ArgumentParser(description="Бэкфилл статей: автор + дата")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    drift = False
    changed = 0
    for lang, cfg, idx in iter_articles():
        cur = idx.read_text(encoding="utf-8")
        new = process(cur, cfg)
        if new != cur:
            if args.check:
                drift = True
                print(f"⚠️  нужно обновить: {idx.relative_to(ROOT)}")
            else:
                idx.write_text(new, encoding="utf-8")
                changed += 1
                print(f"✓ {idx.relative_to(ROOT)}")

    if args.check:
        if drift:
            return 1
        print("✓ Все статьи актуальны (автор + дата).")
        return 0
    print(f"Готово. Обновлено статей: {changed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
