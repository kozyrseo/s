"""
rollout_partner_pages.py — генерация HTML-страниц партнёров под новую страну
(мультигео, Вариант Б: переиспользуем существующий партнёрский генератор).

При раскатке партнёра на страну newcountry создаёт ДАННЫЕ (byMarket в
partners.json). Этот скрипт превращает данные в живые HTML-страницы
`/{country}/rooms/{id}/index.html`, используя уже готовый render_partner.build_page
(тот же дизайн/шаблон, что у Украины) — без дублирования логики.

Для каждого партнёра страны:
  1. Собирает draft из partners.json (плоские поля) + byMarket[country]
     (валюта, карточка, note этой страны) + country/iso/primary_language
  2. Генерит контент страницы (generate_partner_tpl.generate_content) или
     переиспользует, переводит на язык страны
  3. Рендерит HTML через render_partner.build_page → пишет в прод-путь страны

Запуск: python rollout_partner_pages.py --code pl
Читает задание .bot_state/country_jobs/{code}.json (список партнёров).
"""
from __future__ import annotations
import os
import sys
import json
import argparse
from pathlib import Path

AUTOMATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION_DIR.parent
sys.path.insert(0, str(AUTOMATION_DIR))

PARTNERS_JSON = REPO_ROOT / "partners.json"


def _country_meta(code: str) -> dict:
    """Паспорт страны из countries.json (iso, primary_language)."""
    from country_config import get_country
    return get_country(code)


def build_draft_for_market(partner: dict, code: str, cmeta: dict) -> dict:
    """Собирает draft партнёра под конкретную страну из partners.json + byMarket.

    Плоские поля партнёра — общие (id, name, type, лимиты, софт, лого...).
    byMarket[code] — рыночные (валюта, карточка, note, url).
    Плюс country/iso/primary_language — для правильных путей и локали.
    """
    bm = (partner.get("byMarket") or {}).get(code, {})
    draft = dict(partner)  # копия плоских полей
    # рыночные поля переопределяют
    draft["country"] = code
    draft["countries"] = [code]
    draft["iso_country"] = cmeta.get("iso_country", code.upper())
    draft["primary_language"] = cmeta.get("primary_language", "ru")
    if bm.get("currency"):
        draft["currency"] = bm["currency"]
    if bm.get("card"):
        draft["card"] = bm["card"]
    if bm.get("note"):
        draft["note"] = bm["note"]
    # ref_url — общий (одна партнёрская ссылка на все страны, как решено)
    return draft


def generate_partner_page(partner: dict, code: str, cmeta: dict) -> list[str]:
    """Генерит HTML-страницу(ы) партнёра под страну. Возвращает список путей.

    Использует существующие generate_partner_tpl.generate_content +
    render_partner.build_page — тот же механизм, что для Украины.
    """
    import generate_partner_tpl as gpt
    import render_partner as rp

    draft = build_draft_for_market(partner, code, cmeta)
    pid = draft["id"]
    kind = "clubs" if draft.get("type") == "club" else "rooms"
    primary = cmeta.get("primary_language", "ru")
    langs = cmeta.get("languages", [primary])

    created = []

    # Контент страницы: генерим через Claude (как для Украины) на primary-языке
    try:
        content = gpt.generate_content(draft)
    except Exception as e:
        print(f"  ⚠️ {pid}: generate_content упал ({e}) — пропуск")
        return created

    # Основная версия (primary-язык страны) → /{code}/{kind}/{id}/
    html = rp.build_page(draft, content, lang="ru", is_preview=False)
    # ВАЖНО: build_page строит путь из draft['country'] и lang; для основной
    # версии lang="ru" даёт /{country}/{kind}/{id}/ (сегмента языка нет).
    out = REPO_ROOT / code / kind / pid / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    created.append(str(out.relative_to(REPO_ROOT)))

    # Вторичные языки страны (если есть) → /{code}/{lang}/{kind}/{id}/
    for lang in langs[1:]:
        try:
            content_tr = gpt.translate_content(content, draft)
        except Exception:
            content_tr = content
        html_l = rp.build_page(draft, content_tr, lang="uk", is_preview=False)
        # build_page для lang="uk" даёт /{country}/uk/... — но нам нужен
        # произвольный вторичный язык. Пишем в правильный путь вручную.
        out_l = REPO_ROOT / code / lang / kind / pid / "index.html"
        out_l.parent.mkdir(parents=True, exist_ok=True)
        out_l.write_text(html_l, encoding="utf-8")
        created.append(str(out_l.relative_to(REPO_ROOT)))

    return created


def main() -> int:
    ap = argparse.ArgumentParser(description="Генерация страниц партнёров под страну")
    ap.add_argument("--code", required=True, help="Код страны")
    ap.add_argument("--job", default="", help="Путь к country_jobs/{code}.json")
    args = ap.parse_args()

    code = args.code.strip().lower()
    job_path = Path(args.job) if args.job else (
        REPO_ROOT / ".bot_state" / "country_jobs" / f"{code}.json")

    # список партнёров: из задания или все, у кого есть byMarket[code]
    partner_ids = []
    if job_path.exists():
        job = json.loads(job_path.read_text(encoding="utf-8"))
        partner_ids = job.get("partners", [])
    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    pmap = {p["id"]: p for p in data.get("partners", [])}
    if not partner_ids:
        # фолбэк: все партнёры с byMarket[code]
        partner_ids = [p["id"] for p in data.get("partners", [])
                       if (p.get("byMarket") or {}).get(code)]

    if not partner_ids:
        print(f"Нет партнёров для страны {code} — нечего генерить.")
        return 0

    cmeta = _country_meta(code)
    total = 0
    for pid in partner_ids:
        p = pmap.get(pid)
        if not p:
            print(f"  ⚠️ {pid} не найден в partners.json")
            continue
        pages = generate_partner_page(p, code, cmeta)
        for pg in pages:
            print(f"  ✅ создана страница: {pg}")
        total += len(pages)

    print(f"Готово: {total} страниц партнёров для {code}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
