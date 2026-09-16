"""
newcountry_generator.py — генератор каркаса новой страны (мультигео, Этап 3).

Оркестратор: собирает воедино всё, что построено в Этапах 1-2. Вызывается
мастером `/newcountry` в боте (или из CLI для отладки).

ЧТО ДЕЛАЕТ (по шагам):
  1. Добавляет паспорт страны в data/countries.json (флаг, языки, домен, валюта)
  2. Переводит UI-тексты на язык(и) страны (через ui_translator, с самопроверкой)
     — если язык уже есть в lang_texts (напр. ru) — переиспользует, не переводит
  3. Дописывает переводы новых языков в lang_texts.py
  4. Создаёт структуру папок /xx/, /xx/{lang}/
  5. Генерирует хабы rooms/clubs (build-landings --country xx)
  6. Раскатывает ВЫБРАННЫХ партнёров (generate_partner_tpl, по одному)
  7. Обновляет llms.txt

БЕЗОПАСНОСТЬ: не трогает существующие страны. Всё пишется под новый код страны.
Результат кладётся так, чтобы оператор проверил перед публикацией.

ВАЖНО: переводы UI управляются мастером ДО вызова этого генератора — сюда
приходят уже утверждённые оператором тексты (translated_ui). Генератор их
только записывает, сам не переводит (перевод + кнопки — в мастере).
"""
from __future__ import annotations
import os
import json
from pathlib import Path

AUTOMATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION_DIR.parent
COUNTRIES_JSON = AUTOMATION_DIR / "data" / "countries.json"
LANG_TEXTS_PY = AUTOMATION_DIR / "lang_texts.py"
PROMPTS_DIR = AUTOMATION_DIR / "prompts"


# ─────────────────────────────────────────────────────────────────────────
# Шаг 1: паспорт страны → countries.json
# ─────────────────────────────────────────────────────────────────────────
def add_country_to_json(code: str, name: str, flag: str, languages: list[str],
                        iso_country: str, default_currency: str) -> dict:
    """Добавляет/обновляет страну в countries.json.
    languages[0] = primary. Возвращает записанный конфиг страны.
    """
    code = code.strip().lower()
    data = json.loads(COUNTRIES_JSON.read_text(encoding="utf-8"))
    data.setdefault("countries", {})

    cfg = {
        "name": name,
        "flag": flag,
        "languages": languages,
        "primary_language": languages[0],
        "url_prefix": f"/{code}",
        "iso_country": iso_country.upper(),
        "default_currency": default_currency.upper(),
    }
    data["countries"][code] = cfg
    COUNTRIES_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return cfg


# ─────────────────────────────────────────────────────────────────────────
# Шаг 3: дописать переводы нового языка в lang_texts.py
# ─────────────────────────────────────────────────────────────────────────
def add_lang_texts(lang: str, texts: dict) -> bool:
    """Дописывает запись языка в LANG_TEXTS (lang_texts.py).

    texts — полный словарь для языка: {lang_name_native, lang_name_ru,
    hero_alt_prefix, section_*, ui: {...}}. Если язык уже есть — не трогает
    (переиспользуется существующий, напр. ru между странами).

    Перезаписывает файл, регенерируя LANG_TEXTS программно (repr — точность).
    Возвращает True если добавлено, False если язык уже был.
    """
    import importlib
    import lang_texts as lt
    importlib.reload(lt)

    if lang in lt.LANG_TEXTS:
        return False  # язык уже есть — переиспользуем

    new_texts = dict(lt.LANG_TEXTS)
    new_texts[lang] = texts

    # Регенерируем файл (та же структура, что создавалась изначально)
    lines = [
        '"""',
        'lang_texts.py — UI-тексты и языковые строки ПО КОДУ ЯЗЫКА.',
        '',
        'Часть масштабируемой мультигео-модели (Уровень 2).',
        'Ключ — чистый код языка. Тексты НЕ зависят от страны.',
        'Добавляется генератором страны (newcountry_generator) при новом языке.',
        '"""',
        '',
        'LANG_TEXTS: dict[str, dict] = {',
    ]
    for lg, entry in new_texts.items():
        lines.append(f'    {lg!r}: {{')
        for k, v in entry.items():
            if k == 'ui':
                continue
            lines.append(f'        {k!r}: {v!r},')
        lines.append(f'        "ui": {{')
        for uk_, uv in entry.get('ui', {}).items():
            lines.append(f'            {uk_!r}: {uv!r},')
        lines.append(f'        }},')
        lines.append(f'    }},')
    lines.append('}')
    lines += ['', '',
        'def get_lang_texts(lang: str) -> dict:',
        '    """Тексты для языка. KeyError с понятной ошибкой, если языка нет."""',
        '    if lang not in LANG_TEXTS:',
        '        valid = ", ".join(sorted(LANG_TEXTS.keys()))',
        '        raise KeyError(',
        '            f"Нет UI-текстов для языка {lang!r}. Есть: {valid}. "',
        '            f"Добавь переводы в automation/lang_texts.py LANG_TEXTS."',
        '        )',
        '    return LANG_TEXTS[lang]',
        '']
    LANG_TEXTS_PY.write_text('\n'.join(lines), encoding='utf-8')
    return True


def build_lang_texts_entry(translated_ui: dict, lang: str,
                           lang_name_native: str, lang_name_ru: str,
                           hero_alt_prefix: str,
                           section_room: str, section_club: str,
                           section_deals: str) -> dict:
    """Собирает полную запись языка для lang_texts из переведённых кусков."""
    return {
        "lang_name_native": lang_name_native,
        "lang_name_ru": lang_name_ru,
        "hero_alt_prefix": hero_alt_prefix,
        "section_room_reviews": section_room,
        "section_club_reviews": section_club,
        "section_rakeback_deals": section_deals,
        "ui": translated_ui,
    }


# ─────────────────────────────────────────────────────────────────────────
# Шаг 4: структура папок
# ─────────────────────────────────────────────────────────────────────────
def create_country_dirs(code: str, languages: list[str]) -> list[str]:
    """Создаёт папки /xx/rooms, /xx/clubs, /xx/blog и языковые подпапки."""
    code = code.strip().lower()
    primary = languages[0]
    created = []
    base = REPO_ROOT / code
    for sub in ("rooms", "clubs", "blog"):
        d = base / sub
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d.relative_to(REPO_ROOT)))
    # вторичные языки
    for lang in languages[1:]:
        for sub in ("rooms", "clubs", "blog"):
            d = base / lang / sub
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d.relative_to(REPO_ROOT)))
    return created


# ─────────────────────────────────────────────────────────────────────────
# Оркестратор (вызывается мастером ПОСЛЕ утверждения переводов)
# ─────────────────────────────────────────────────────────────────────────
def generate_country(*, code: str, name: str, flag: str, languages: list[str],
                     iso_country: str, default_currency: str,
                     lang_texts_by_lang: dict,
                     partner_ids: list[str] | None = None) -> dict:
    """Полная генерация каркаса страны.

    Аргументы:
      code, name, flag, languages, iso_country, default_currency — паспорт
      lang_texts_by_lang: {lang: полная_запись_для_lang_texts} для НОВЫХ языков
        (языки, что уже есть — можно не передавать, переиспользуются)
      partner_ids: список id партнёров для раскатки (мастер их выбрал)

    Возвращает отчёт {steps: [...], country: cfg}.
    Порядок: сначала данные (JSON+тексты), потом папки. Генерацию хабов/
    партнёров/llms запускает мастер через workflow (они требуют окружения).
    """
    report = {"steps": [], "country": None, "code": code}

    # ВАЖЕН ПОРЯДОК: сначала тексты языков, ПОТОМ паспорт страны.
    # Причина: как только страна появляется в countries.json, следующий импорт
    # lang_config вызовет _build_lang_config(), который для КАЖДОГО языка страны
    # дёргает get_lang_texts(lang). Если текстов ещё нет — KeyError. Поэтому
    # тексты новых языков записываем ПЕРВЫМИ.

    # 1-2. тексты новых языков → lang_texts.py (ДО паспорта!)
    for lang, texts in (lang_texts_by_lang or {}).items():
        added = add_lang_texts(lang, texts)
        report["steps"].append(
            f"✅ Язык {lang}: тексты добавлены в lang_texts.py" if added
            else f"ℹ️ Язык {lang}: уже есть, переиспользован"
        )

    # 3. паспорт → JSON (теперь тексты уже на месте)
    cfg = add_country_to_json(code, name, flag, languages, iso_country, default_currency)
    report["country"] = cfg
    report["steps"].append(f"✅ Паспорт страны записан в countries.json: {code}")

    # 4. структура папок
    dirs = create_country_dirs(code, languages)
    report["steps"].append(f"✅ Создано папок: {len(dirs)}")

    # 5-7 (хабы, партнёры, llms) — запускаются мастером через workflow,
    # т.к. требуют полного окружения (build-landings, generate_partner_tpl).
    # Здесь фиксируем план:
    report["next_workflow_steps"] = {
        "landings": f"python build-landings.py --country {code}",
        "partners": [f"generate_partner_tpl for {pid} (country={code})"
                     for pid in (partner_ids or [])],
        "llms": "python automation/build_llms.py",
    }
    report["steps"].append(
        f"⏭ Хабы/партнёры/llms — запустит workflow ({len(partner_ids or [])} партнёров)"
    )

    return report


# ═══════════════════════════════════════════════════════════════════════════
#  CLI-ОРКЕСТРАТОР (запускается workflow newcountry.yml)
# ═══════════════════════════════════════════════════════════════════════════
def _tg_send(text: str) -> None:
    """Отправка сообщения в Telegram (превью результата оператору)."""
    import os, urllib.request, urllib.parse, json as _json
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("NEWCOUNTRY_CHAT_ID")
    if not token or not chat:
        print("⚠️  TELEGRAM_BOT_TOKEN/CHAT_ID не заданы — превью не отправлено")
        print(text)
        return
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text[:4000],
        "parse_mode": "Markdown", "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
    except Exception as e:
        print(f"⚠️  Telegram send failed: {e}\n{text}")


def run_from_job(code: str) -> int:
    """Читает задание country_jobs/{code}.json и генерирует страну целиком.

    Порядок:
      1. Для каждого НОВОГО языка страны — перевод UI (с самопроверкой)
      2. Сборка записей lang_texts для новых языков
      3. Генерация страны (паспорт + тексты + папки) через generate_country
      4. Генерация промпта статей + проверка легалки
      5. Превью оператору в Telegram (с легальными рисками)

    Хабы/партнёры/llms под страну запускает bash-часть workflow ПОСЛЕ этого
    (build-landings --country, generate_partner_tpl, build_llms) — им нужен
    доступ к обновлённым конфигам, которые пишет этот шаг.
    """
    job_path = AUTOMATION_DIR / "data" / ".." / ".." / ".bot_state" / "country_jobs" / f"{code}.json"
    job_path = (REPO_ROOT / ".bot_state" / "country_jobs" / f"{code}.json")
    if not job_path.exists():
        print(f"❌ Задание не найдено: {job_path}")
        return 1
    job = json.loads(job_path.read_text(encoding="utf-8"))

    code = job["code"]
    name = job["name"]
    flag = job.get("flag", "🏴")
    iso = job.get("iso_country", code.upper())
    languages = job["languages"]
    partner_ids = job.get("partners", [])
    # МУЛЬТИГЕО: валюта теперь ПО КАЖДОМУ партнёру (мастер шлёт partner_currencies).
    # Старый формат (одна currency на страну) поддерживаем для совместимости.
    partner_currencies = job.get("partner_currencies", {})
    # Дефолтная валюта страны: явная currency, иначе первая из партнёрских,
    # иначе валюта по коду страны (NC-таблица), иначе USD.
    _NC_CUR = {"pl": "PLN", "kz": "KZT", "by": "BYN", "de": "EUR", "cs": "CZK",
               "ro": "RON", "es": "EUR", "tr": "TRY", "ge": "GEL", "az": "AZN"}
    currency = (job.get("currency")
                or (list(partner_currencies.values())[0] if partner_currencies else None)
                or _NC_CUR.get(code, "USD"))

    # Чат для превью (из задания)
    if job.get("chat_id"):
        os.environ.setdefault("NEWCOUNTRY_CHAT_ID", str(job["chat_id"]))

    print(f"=== Генерация страны: {flag} {name} ({code}) ===")
    print(f"Языки: {languages}, валюта: {currency}, партнёров: {len(partner_ids)}")

    # 1-2. Перевод UI для новых языков
    import importlib
    import lang_texts as lt
    importlib.reload(lt)
    from ui_translator import translate_ui_with_checks
    from lang_texts import get_lang_texts

    base_ru = get_lang_texts("ru")  # эталон для перевода
    lang_texts_by_lang = {}
    ui_flags_report = []  # сомнительные фразы для превью

    for lang in languages:
        if lang in lt.LANG_TEXTS:
            print(f"  Язык {lang}: уже есть, переиспользую")
            continue
        print(f"  Язык {lang}: перевод UI ({len(base_ru['ui'])} фраз)...")
        try:
            result = translate_ui_with_checks(base_ru["ui"], lang)
            translated_ui = result["translated"]
            if result["flagged"]:
                ui_flags_report.append((lang, result["flagged"]))
            # секции и языковые поля — тоже переводим (короткие)
            extra = translate_ui_with_checks({
                "lang_name_native": base_ru["lang_name_native"],
                "lang_name_ru": base_ru["lang_name_ru"],
                "hero_alt_prefix": base_ru["hero_alt_prefix"],
                "section_room_reviews": base_ru["section_room_reviews"],
                "section_club_reviews": base_ru["section_club_reviews"],
                "section_rakeback_deals": base_ru["section_rakeback_deals"],
            }, lang)["translated"]
            lang_texts_by_lang[lang] = build_lang_texts_entry(
                translated_ui, lang,
                lang_name_native=extra.get("lang_name_native", lang),
                lang_name_ru=extra.get("lang_name_ru", lang),
                hero_alt_prefix=extra.get("hero_alt_prefix", "Иллюстрация:"),
                section_room=extra.get("section_room_reviews", "Room reviews"),
                section_club=extra.get("section_club_reviews", "Club reviews"),
                section_deals=extra.get("section_rakeback_deals", "Rakeback deals"),
            )
        except Exception as e:
            print(f"  ⚠️ Перевод {lang} упал: {e}")
            _tg_send(f"❌ Страна {flag} {name}: ошибка перевода UI ({lang}): {e}")
            return 1

    # 3. Генерация страны (паспорт + тексты + папки)
    report = generate_country(
        code=code, name=name, flag=flag, languages=languages,
        iso_country=iso, default_currency=currency,
        lang_texts_by_lang=lang_texts_by_lang,
        partner_ids=partner_ids,
    )

    # 3b. МУЛЬТИГЕО: раскатка выбранных партнёров — создаём byMarket[code]
    #     с валютой каждого партнёра + адаптированной карточкой + генерим отзывы.
    primary_lang = languages[0]
    review_flags_report = []  # для превью: сгенерированные отзывы
    if partner_ids:
        from partner_markets import rollout_partner_to_market
        from ui_translator import translate_ui_with_checks
        import json as _pj
        pdata = _pj.loads((REPO_ROOT / "partners.json").read_text(encoding="utf-8"))
        pmap = {p["id"]: p for p in pdata.get("partners", [])}

        for pid in partner_ids:
            p = pmap.get(pid)
            if not p:
                report["steps"].append(f"⚠️ Партнёр {pid} не найден — пропущен")
                continue
            pcur = partner_currencies.get(pid, currency)  # валюта этого партнёра
            # Адаптируем строки карточки под страну: Claude переведёт метки/суммы.
            # Берём украинскую карточку как образец, просим адаптировать валюту/суммы.
            base_rows = (p.get("card") or {}).get("rows", [])
            try:
                # переводим значения строк карточки на язык страны + валюту
                labels = {f"row_{i}": (r[1] if len(r) > 1 else "")
                          for i, r in enumerate(base_rows)}
                tr = translate_ui_with_checks(labels, primary_lang)["translated"]
                new_rows = []
                for i, r in enumerate(base_rows):
                    val = tr.get(f"row_{i}", r[1] if len(r) > 1 else "")
                    # валюту в строке «Валюта» подменяем на валюту партнёра
                    if len(r) > 0 and ("Валют" in str(r[0]) or "valut" in str(r[0]).lower()):
                        val = pcur
                    new_rows.append([r[0], val, r[2] if len(r) > 2 else False])
            except Exception as e:
                new_rows = base_rows  # фолбэк — оставляем как есть
                report["steps"].append(f"⚠️ Карточка {pid}: перевод не удался ({e})")

            ok = rollout_partner_to_market(pid, code,
                                           currency=pcur, card_rows=new_rows,
                                           note=(p.get("note") or ""))
            report["steps"].append(
                f"✅ Партнёр {pid} раскатан на {code} ({pcur})" if ok
                else f"⚠️ Партнёр {pid}: раскатка не удалась")

            # Отзывы под гео (3-5 на партнёра)
            try:
                from review_generator import generate_reviews, add_reviews_to_json, back_translate_reviews
                revs = generate_reviews(p.get("name", pid), p.get("type", "room"),
                                        name, primary_lang, count=4)
                if revs:
                    backs = back_translate_reviews(revs, primary_lang)
                    # verified_ratio по умолчанию 0.0: синтетические отзывы не
                    # помечаются "проверено" (см. review_generator.add_reviews_to_json)
                    add_reviews_to_json(pid, code, primary_lang, revs)
                    review_flags_report.append((pid, revs, backs))
                    report["steps"].append(f"✅ Отзывы {pid}: {len(revs)} шт (на проверку)")
            except Exception as e:
                report["steps"].append(f"⚠️ Отзывы {pid} не созданы: {e}")

    # 4. Промпт статей + проверка легалки
    legal_report = None
    try:
        from country_prompt_builder import build_country_prompt
        import json as _json
        # партнёры для промпта (те, что выбраны, с валютой рынка)
        pj = _json.loads((REPO_ROOT / "partners.json").read_text(encoding="utf-8"))
        chosen = [p for p in pj.get("partners", []) if p["id"] in partner_ids]
        for p in chosen:
            p["currency"] = partner_currencies.get(p["id"], currency)  # валюта партнёра
        prompt_result = build_country_prompt(
            country_code=code, country_name=name, partners=chosen)
        # сохраняем промпт как system_prompt.{primary}.md
        primary = languages[0]
        prompt_file = PROMPTS_DIR / f"system_prompt.{primary}.md"
        prompt_file.write_text(prompt_result["prompt"], encoding="utf-8")
        report["steps"].append(f"✅ Промпт статей: system_prompt.{primary}.md")
        legal_report = prompt_result["legal_review"]
    except Exception as e:
        print(f"  ⚠️ Генерация промпта упала: {e}")
        report["steps"].append(f"⚠️ Промпт статей не создан: {e}")

    # 5. Превью оператору
    L = [f"🌍 *Страна создана: {flag} {name}* (`{code}`)", ""]
    L += report["steps"]
    L.append("")
    # UI-флаги
    total_flags = sum(len(f) for _, f in ui_flags_report)
    if total_flags:
        L.append(f"⚠️ *Перевод UI: {total_flags} фраз требуют внимания:*")
        for lang, flags in ui_flags_report:
            for f in flags[:5]:
                L.append(f"  • [{lang}] «{f['ru']}» → «{f['translated']}»")
                L.append(f"    обратно: «{f['back']}» ({f['reason']})")
        L.append("")
    else:
        L.append("✅ Перевод UI: замечаний нет")
    # Легальные риски
    if legal_report and legal_report.get("needs_review"):
        L.append("")
        L.append("⚠️ *ЛЕГАЛКА — проверь эти места (юридически важно):*")
        for r in legal_report.get("ai_risks", [])[:5]:
            L.append(f"  • {r.get('quote','')[:80]}")
            L.append(f"    _{r.get('why','')[:80]}_")
        terms = legal_report.get("term_hits", [])
        if terms:
            L.append(f"  🔍 Юр-термины найдены: {', '.join(t['term'] for t in terms[:8])}")
        L.append("  ⚠️ _Обязательно вычитай легальную секцию перед публикацией._")
    else:
        L.append("✅ Легалка: явных рисков не найдено (но вычитать стоит)")
    L.append("")
    L.append(f"🧪 *ТЕСТ-РЕЖИМ:* всё в ветке `test-country-{code}`, НЕ на живом сайте.")
    L.append(f"_Смотри страницы в preview ветки. Украина и сайт не тронуты._")
    L.append(f"_Понравится — смержишь ветку в main (опубликуешь). Нет — удалишь ветку._")

    _tg_send("\n".join(L))
    print("\n".join(report["steps"]))
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Генератор страны (мультигео)")
    ap.add_argument("--code", required=True, help="Код страны из задания")
    args = ap.parse_args()
    return run_from_job(args.code)


if __name__ == "__main__":
    raise SystemExit(main())
