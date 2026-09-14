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


_I18N_CACHE: dict = {}


def _fix_internal_links(content: dict, country: str):
    """Заменяет украинские внутренние ссылки /ua/ на путь текущей страны /{country}/
    во всех текстовых полях контента. Claude вставляет /ua/ по образцу — чиним.
    Для country=ua замена /ua/→/ua/ безопасна (ничего не меняет).
    """
    import re
    if country == "ua":
        return content  # для Украины ничего менять не нужно
    pat = re.compile(r"/ua/")
    repl = f"/{country}/"

    def _fix(v):
        if isinstance(v, str):
            return pat.sub(repl, v)
        if isinstance(v, list):
            return [_fix(x) for x in v]
        if isinstance(v, dict):
            return {k: _fix(x) for k, x in v.items()}
        return v

    return {k: _fix(v) for k, v in content.items()}


def _translate_i18n_to(lang: str) -> dict:
    """Переводит UI-подписи шаблона (I18N) на язык страны. Кэш по языку —
    83 подписи переводятся ОДИН раз на язык, не для каждого партнёра.

    Fallback: если перевод упал — вернёт русские подписи (страница не сломается,
    просто часть UI будет русской).
    """
    if lang in _I18N_CACHE:
        return _I18N_CACHE[lang]
    import os
    import json as _json
    from render_partner import I18N
    ru = I18N["ru"]
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return ru
    try:
        from openai import OpenAI
        from generate_partner_tpl import MODEL, MAX_TOKENS, OPENROUTER_BASE_URL
        _LANG = {"pl": "польский", "kk": "казахский", "de": "немецкий",
                 "cs": "чешский", "ro": "румынский", "es": "испанский",
                 "en": "английский", "tr": "турецкий"}
        lang_name = _LANG.get(lang, lang)
        system = (f"Переведи значения JSON (UI-подписи сайта) с русского на "
                  f"{lang_name}. Это кнопки, метки, короткие фразы интерфейса "
                  f"покерного аффилиат-сайта. Сохраняй смысл и краткость. "
                  f"Верни СТРОГО JSON с теми же ключами, без markdown.")
        client = OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)
        resp = client.chat.completions.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": _json.dumps(ru, ensure_ascii=False, indent=2)}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in raw else raw
        first, last = raw.find("{"), raw.rfind("}")
        tr = _json.loads(raw[first:last + 1])
        # дополняем недостающие ключи русскими (на случай пропусков)
        result = dict(ru)
        result.update({k: v for k, v in tr.items() if v})
        _I18N_CACHE[lang] = result
        return result
    except Exception as e:
        print(f"  ⚠️ перевод I18N на {lang} упал ({e}) — русские подписи")
        return ru


def _translate_content_to(content: dict, draft: dict, lang: str) -> dict:
    """Переводит контент страницы партнёра на произвольный язык (напр. pl).

    В отличие от generate_partner_tpl.translate_content (жёстко ru→uk),
    переводит на любой язык страны. Использует Claude через OpenRouter.
    Переводит и текстовые поля анкеты (pros/cons/faq), чтобы страница была
    полностью на языке страны. Fallback: если перевод упал — вернёт оригинал.
    """
    import os
    import json as _json
    try:
        from openai import OpenAI
    except ImportError:
        return content
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return content

    _LANG = {"pl": "польский", "kk": "казахский", "de": "немецкий",
             "cs": "чешский", "ro": "румынский", "es": "испанский",
             "en": "английский", "tr": "турецкий", "uk": "украинский"}
    lang_name = _LANG.get(lang, lang)

    bundle = dict(content)
    bundle["__pros"] = draft.get("pros", [])
    bundle["__cons"] = draft.get("cons", [])
    bundle["__faq"] = draft.get("faq", [])

    system = (f"Ты профессиональный переводчик. Переведи значения JSON с русского "
              f"на {lang_name}. Переводи ВСЕ значения (включая массивы "
              f"__pros/__cons/__faq). Сохраняй HTML-теги и разметку. Термины "
              f"(Rakeback, VIP, FAQ) — как принято в {lang_name}-сегменте. "
              f"Верни СТРОГО JSON с теми же ключами, без markdown.")
    user = "Переведи:\n```json\n" + _json.dumps(bundle, ensure_ascii=False, indent=2) + "\n```"
    try:
        from generate_partner_tpl import MODEL, MAX_TOKENS, OPENROUTER_BASE_URL, parse_content_json, apply_content_fixups, _clamp_meta_title, _clamp_meta_desc
        client = OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)
        resp = client.chat.completions.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        tr = parse_content_json(resp.choices[0].message.content or "")
        # обновляем pros/cons/faq в draft (для карточки страницы)
        if "__pros" in tr:
            draft["pros"] = tr.pop("__pros", draft.get("pros"))
        if "__cons" in tr:
            draft["cons"] = tr.pop("__cons", draft.get("cons"))
        if "__faq" in tr:
            draft["faq"] = tr.pop("__faq", draft.get("faq"))
        result = apply_content_fixups({k: v for k, v in tr.items() if not k.startswith("__")}, lang)
        result["meta_title"] = _clamp_meta_title(result.get("meta_title", ""))
        result["meta_description"] = _clamp_meta_desc(result.get("meta_description", ""))
        return result
    except Exception as e:
        print(f"  ⚠️ перевод контента на {lang} упал ({e}) — оставляю русский")
        return content


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

    # Контент страницы: генерим на русском (базовый промпт), затем переводим
    # на PRIMARY-язык страны через универсальный переводчик. Для Украины
    # (primary=ru) перевод не нужен. Для Польши (primary=pl) — переводим на pl.
    try:
        content = gpt.generate_content(draft)
    except Exception as e:
        print(f"  ⚠️ {pid}: generate_content упал ({e}) — пропуск")
        return created

    # МУЛЬТИГЕО: Claude при генерации контента вставляет внутренние ссылки с
    # украинскими путями (/ua/rooms/..., /ua/#faq) — он видит их в образце.
    # Заменяем /ua/ на путь текущей страны во ВСЕХ текстовых полях контента.
    # Для Украины (code=ua) замена /ua/→/ua/ ничего не меняет (безопасно).
    content = _fix_internal_links(content, code)

    # Перевод контента на primary-язык страны (если не русский)
    if primary != "ru":
        content = _translate_content_to(content, draft, primary)
        content = _fix_internal_links(content, code)  # ещё раз после перевода

    # Перевод UI-подписей шаблона (I18N: «Румы», «Сделки», «обзор»...) на язык
    # страны — иначе они останутся русскими. Один раз на страну (кэшируем).
    i18n_tr = _translate_i18n_to(primary) if primary != "ru" else None

    # Основная версия (primary-язык страны) → /{code}/{kind}/{id}/
    html = rp.build_page(draft, content, lang="ru", is_preview=False, i18n_override=i18n_tr)
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
