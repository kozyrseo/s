#!/usr/bin/env python3
"""
KOZYR — смоук-тест сгенерированной страницы партнёра (RU + UK).

Ловит регрессии, которые раньше находились только глазами по скринам:
  • русский текст, просочившийся в украинскую версию;
  • сломанный/пустой JSON-LD;
  • незаполненные плейсхолдеры ({{...}}, None, TODO, «?»);
  • отсутствие OG-обложки (файла) или её ссылки;
  • пропавший hreflang / canonical / H1 / title.

Запуск:
    python automation/test_partner_page.py --id tonpoker            # прод-пути
    python automation/test_partner_page.py --id tonpoker --pending  # _pending_partner/
Код возврата 0 — все проверки прошли; 1 — есть провалы (годится для CI/бота).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARTNERS_JSON = REPO_ROOT / "partners.json"

# Слова/формы, которые в украинском тексте почти наверняка означают
# просочившийся русский (у них другое написание в UK). Осознанно узкий список,
# чтобы не ловить общие для языков слова.
RU_LEAK_WORDS = [
    r"\bчто\b", r"\bчтобы\b", r"\bесли\b", r"\bсейчас\b", r"\bочень\b",
    r"\bэто\b", r"\bэтот\b", r"\bденьги\b", r"\bвывод\b", r"\bбольше\b",
    r"\bигрок\b", r"\bбыстро\b", r"\bнесколько\b", r"\bтолько\b", r"\bвъзалежно\b",
    r"\bрублей\b", r"\bрубл", r"\bМосква\b", r"\bсегодня\b", r"\bвсегда\b",
    r"\bприложение\b", r"\bбезопасн", r"\bподдержк", r"\bвыплаты\b", r"\bвыигрыш",
]
PLACEHOLDER_PATTERNS = [
    r"\{\{.*?\}\}", r"\bTODO\b", r"\bFIXME\b", r"\blorem\b", r"\bipsum\b",
    # значения-заглушки: пустые/None/undefined как единственное содержимое ячейки
    r">\s*None\s*<", r">\s*null\s*<", r">\s*undefined\s*<",
    r"Демо-", r"placeholder",
]


class Result:
    def __init__(self):
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.checks = 0

    def check(self, cond, msg, warn=False):
        self.checks += 1
        if not cond:
            (self.warns if warn else self.fails).append(msg)
        return bool(cond)


def strip_scripts_styles(html: str) -> str:
    h = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    return h


def visible_text(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", strip_scripts_styles(html))
    return re.sub(r"\s+", " ", t)


def check_common(html: str, r: Result, lang: str, pid: str):
    # JSON-LD валиден
    blocks = re.findall(r'application/ld\+json">(.*?)</script>', html, re.S)
    r.check(len(blocks) >= 2, f"[{lang}] мало JSON-LD блоков ({len(blocks)})")
    valid = 0
    for b in blocks:
        try:
            json.loads(b); valid += 1
        except json.JSONDecodeError as e:
            r.fails.append(f"[{lang}] невалидный JSON-LD: {str(e)[:80]}")
    r.check(valid == len(blocks), f"[{lang}] есть битые JSON-LD")

    # canonical, hreflang, title, H1, viewport
    r.check('rel="canonical"' in html, f"[{lang}] нет canonical")
    r.check(html.count('hreflang=') >= 2, f"[{lang}] мало hreflang")
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    r.check(bool(m and m.group(1).strip()), f"[{lang}] пустой <title>")
    if m:
        r.check(len(m.group(1)) <= 75, f"[{lang}] title длинноват ({len(m.group(1))})", warn=True)
    r.check(len(re.findall(r"<h1[\s>]", html)) == 1, f"[{lang}] должен быть ровно один <h1>")
    md = re.search(r'name="description" content="([^"]*)"', html)
    r.check(bool(md and md.group(1).strip()), f"[{lang}] пустой meta description")

    # OG-картинка: ссылка + файл существует
    og = re.search(r'property="og:image" content="https://kozyr\.club(/[^"]+)"', html)
    r.check(bool(og), f"[{lang}] нет og:image")
    if og:
        r.check((REPO_ROOT / og.group(1).lstrip("/")).exists(),
                f"[{lang}] og:image файл не найден: {og.group(1)}")

    # Плейсхолдеры в видимом тексте
    body = strip_scripts_styles(html)
    for pat in PLACEHOLDER_PATTERNS:
        hit = re.search(pat, body, re.I)
        r.check(not hit, f"[{lang}] плейсхолдер/пустое значение: {hit.group(0)[:40] if hit else pat}")

    # Логотип-ссылка резолвится (если есть <img> логотипа)
    for src in set(re.findall(r'<img[^>]+src="(/[^"]+\.(?:webp|png|jpg|svg))"', html)):
        r.check((REPO_ROOT / src.lstrip("/")).exists(),
                f"[{lang}] битая картинка: {src}", warn=True)


def check_uk_specific(html: str, r: Result):
    r.check('lang="uk' in html, "[uk] <html lang> не uk")
    txt = visible_text(html)
    leaks = []
    for pat in RU_LEAK_WORDS:
        for mm in re.finditer(pat, txt, re.I):
            leaks.append(mm.group(0))
    uniq = sorted(set(w.lower() for w in leaks))
    r.check(not uniq, f"[uk] русский текст в украинской версии: {', '.join(uniq[:12])}")


def page_paths(pid: str, pending: bool) -> tuple[Path, Path]:
    if pending:
        return (REPO_ROOT / "_pending_partner" / pid / "index.html",
                REPO_ROOT / "_pending_partner" / (pid + "_uk") / "index.html")
    # прод-пути из partners.json (url), UK — со вставкой /uk/
    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    p = next((x for x in data.get("partners", []) if x.get("id") == pid), None)
    if not p:
        sys.exit(f"❌ Партнёр '{pid}' не найден в partners.json (для прод-путей). "
                 f"Возможно, ещё не опубликован — попробуй --pending.")
    parts = [s for s in p["url"].split("/") if s]
    ru = REPO_ROOT.joinpath(*parts, "index.html")
    uk = REPO_ROOT.joinpath(parts[0], "uk", *parts[1:], "index.html")
    return ru, uk


def main() -> int:
    ap = argparse.ArgumentParser(description="Смоук-тест страницы партнёра")
    ap.add_argument("--id", required=True)
    ap.add_argument("--pending", action="store_true", help="проверять _pending_partner/, а не прод")
    args = ap.parse_args()

    ru_path, uk_path = page_paths(args.id, args.pending)
    r = Result()

    r.check(ru_path.exists(), f"RU страница не найдена: {ru_path}")
    r.check(uk_path.exists(), f"UK страница не найдена: {uk_path}")

    if ru_path.exists():
        check_common(ru_path.read_text(encoding="utf-8"), r, "ru", args.id)
    if uk_path.exists():
        uk_html = uk_path.read_text(encoding="utf-8")
        check_common(uk_html, r, "uk", args.id)
        check_uk_specific(uk_html, r)

    print(f"Проверок: {r.checks} · провалов: {len(r.fails)} · предупреждений: {len(r.warns)}")
    for w in r.warns:
        print(f"  ⚠️  {w}")
    for f in r.fails:
        print(f"  ❌ {f}")
    if not r.fails:
        print("✅ Смоук-тест пройден.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
