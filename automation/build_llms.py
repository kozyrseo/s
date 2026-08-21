#!/usr/bin/env python3
"""
KOZYR — генератор llms.txt и llms-full.txt (единая точка правды для ИИ).

ЗАЧЕМ ЭТОТ СКРИПТ
-----------------
Файлы llms.txt / llms-full.txt читают языковые модели (ChatGPT, Perplexity,
Gemini, Claude, Google AI Overviews). Раньше они правились руками и
разошлись с реальностью: утверждали, что PokerBet имеет лицензию КРАИЛ и
удерживает налоги, тогда как partners.json, лендинги и системный промпт
говорят «лицензия Curaçao, НЕ КРАИЛ, рейкбека нет». Нейросети повторяли
эту дезинформацию — это регуляторный риск.

Решение: llms.txt/full ГЕНЕРИРУЮТСЯ из данных, а не пишутся вручную.
Все факты о лицензии/налогах/рейкбеке ВЫВОДЯТСЯ из partners.json через
явные маппинги ниже, поэтому ложное «КРАИЛ» технически невозможно
эмитировать — оно нигде не хранится как свободный текст.

ИСТОЧНИКИ ДАННЫХ (единая правда)
--------------------------------
  • partners.json                 — факты о партнёрах (лицензия, скор, рейк, форматы…)
  • automation/taxonomy.json      — чистые заголовки/описания RU-статей
  • automation/taxonomy.uk.json   — то же для UK-статей
  • ua/blog/*/ и ua/uk/blog/*/    — что РЕАЛЬНО опубликовано (сканируется на диске)

Заголовки статей берутся из таксономии (курируемые), при отсутствии —
парсятся из <title>/<meta description> самой страницы. Так список всегда
отражает то, что опубликовано, даже если таксономия отстала.

РЕДАКТУРНЫЙ ТЕКСТ (не факты) вынесен в блок EDITORIAL ниже: описание
модели, глоссарий понятий, «кому подходит». Факты (лицензия, скор, рейк%,
депозит, бонус, выплаты) — только из partners.json.

ЗАПУСК
------
  python automation/build_llms.py            # сгенерировать и записать файлы
  python automation/build_llms.py --check     # ничего не писать; выйти с кодом 1,
                                              # если файлы на диске разошлись с
                                              # тем, что должно генерироваться (для CI)
  python automation/build_llms.py --stdout     # напечатать llms.txt в stdout

Пути якорятся к корню репозитория (родитель папки automation/), поэтому
скрипт работает из любой рабочей директории.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ── Пути (якорь = корень репо) ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
PARTNERS_JSON = ROOT / "partners.json"
TAXONOMY_RU = ROOT / "automation" / "taxonomy.json"
TAXONOMY_UK = ROOT / "automation" / "taxonomy.uk.json"

BLOG_RU_DIR = ROOT / "ua" / "blog"
BLOG_UK_DIR = ROOT / "ua" / "uk" / "blog"

LLMS_TXT = ROOT / "llms.txt"
LLMS_FULL_TXT = ROOT / "llms-full.txt"

SITE = "https://kozyr.club"

# Папки внутри blog/, которые не являются статьями
NON_ARTICLE_DIRS = {"authors", "logos", "tags"}


# ── Редактурный текст (правь здесь; ФАКТЫ живут в partners.json) ────────────
EDITORIAL: dict[str, Any] = {
    # Одно-предложное резюме проекта (блок > в начале файла)
    "summary": (
        "KOZYR (kozyr.club) — витрина рейкбек-сделок в покере для игроков из "
        "Украины. Мы показываем условия проверенных покер-румов и клубов "
        "(рейкбек, лимиты, платёжки, правовой статус) и ведём по прямой "
        "партнёрской ссылке. KOZYR не является оператором азартных игр — "
        "рейкбек начисляет и выплачивает сам рум или клуб. Сайт доступен на "
        "двух языках: русском (/ua/) и украинском (/ua/uk/)."
    ),
    # Абзац про модель (идёт под резюме)
    "model": (
        "Модель работы: покер-рум платит партнёрскую комиссию за приведённого "
        "игрока. KOZYR получает эту комиссию и возвращает большую её часть "
        "игроку в виде рейкбека. Регистрация по реф-ссылке KOZYR = игрок "
        "получает рейкбек. KOZYR не ведёт расчётов, не принимает депозиты и "
        "не удерживает балансы. Только для лиц от 21 года. Мы поддерживаем "
        "принципы ответственной игры."
    ),
    # Глоссарий (только llms-full). Никаких утверждений о лицензиях здесь.
    "glossary": [
        ("Рейк", "комиссия, которую покер-рум забирает с каждой раздачи "
                 "(из банка в кеше или как часть бай-ина в турнирах)."),
        ("Рейкбек", "возврат части рейка игроку. Для открытых румов рабочий "
                    "диапазон 30–60%; выше 60% встречается редко. Высокий "
                    "процент не равен лучшим условиям, если выплаты медленные, "
                    "лимиты не те или поля переполнены регулярами."),
        ("Kozyr Score", "рейтинг партнёров от 0 до 10 по пяти параметрам: "
                        "рейкбек, качество трафика, скорость выплат, промо и "
                        "поддержка. Формируется по объективным критериям; "
                        "партнёр не может «купить» оценку."),
        ("Kozyr Match", "калькулятор ориентировочного возврата рейка на основе "
                        "лимита и часов игры в неделю. Даёт оценку, не гарантию."),
        ("Типы партнёров", "«Прямая ссылка» — классические румы, регистрация "
                           "по реф-ссылке, игра на их платформе. «Клубный "
                           "формат» — приватные приложения (ClubGG, PPPoker, "
                           "PokerBros), доступ по инвайту, расчёты через хоста."),
    ],
    # Расширенное описание партнёров (только llms-full), по id.
    # Здесь — БЕЗОПАСНАЯ описательная проза; факты подставляются из данных.
    "partner_extended": {
        "pokerbet": {
            "formats": "Hold'em, Omaha, Short Deck, MTT, fast-fold",
            "fits": (
                "Кому подходит: игрокам, которые хотят играть на гривны с "
                "гибкими платежами (карта, банк) и быстрыми выплатами. "
                "Рейкбека у PokerBet нет — площадка компенсирует это бонусами."
            ),
        },
        "klubok": {
            "formats": "Hold'em, PLO, AoF (All-in or Fold), Short Deck (6+), SNG, MTT",
            "fits": (
                "Кому подходит: игрокам, которые ищут высокий рейкбек и мягкие "
                "поля без верификации и готовы к клубной модели расчётов через "
                "хоста в Telegram. Номинал фишки привязан к гривне 1:1."
            ),
        },
    },
    # Внешние авторитетные ссылки-первоисточники (усиливают цитируемость для ИИ)
    "sources": [
        ("Закон Украины №768-IX об азартных играх",
         "https://zakon.rada.gov.ua/laws/show/768-20"),
        ("КРАИЛ — регулятор азартных игр Украины",
         "https://www.gc.gov.ua/"),
    ],
}


# ── Загрузка данных ────────────────────────────────────────────────────────
def load_json(path: Path) -> Any:
    """Читает JSON; при отсутствии/ошибке — понятное сообщение и выход."""
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"❌ Не найден файл данных: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"❌ Битый JSON в {path}: {e}")


def load_partners() -> list[dict]:
    data = load_json(PARTNERS_JSON)
    partners = data.get("partners", [])
    if not partners:
        sys.exit("❌ В partners.json нет партнёров.")
    return partners


# ── Вывод фактов из данных (safety-critical: лицензия/налоги/рейкбек) ───────
def rakeback_text(p: dict) -> str:
    """Рейкбек СТРОГО из поля rake. 'none'/0/пусто → 'нет'."""
    r = p.get("rake")
    if r in (None, "", "none", 0, "0"):
        return "нет (площадка даёт только бонусы, не рейкбек)"
    return f"{r}% еженедельно"


def legal_status(p: dict) -> str:
    """
    Правовой статус ВЫВОДИТСЯ из поля license. Ветка Curaçao жёстко
    отрицает КРАИЛ и авто-налог — поэтому ложное «легальный украинский рум
    с лицензией КРАИЛ» невозможно сгенерировать из данных.
    """
    lic = (p.get("license") or "").strip()
    low = lic.lower()
    if "cura" in low or "кюрасао" in low:
        return (
            "Лицензия Curaçao (Curaçao Gaming Authority). НЕ входит в "
            "украинский лицензионный режим КРАИЛ; оператор не удерживает "
            "налоги автоматически — налоговые обязательства на игроке."
        )
    if "офшор" in low or "offshore" in low:
        return (
            "Офшорная юрисдикция. Украинской лицензии КРАИЛ нет; формально не "
            "покрыто законом Украины №768-IX — налоговые обязательства и "
            "декларирование на игроке. Правовая серая зона."
        )
    # Неизвестный статус — отдаём как есть, но не выдумываем «легальность».
    return lic or "уточняется"


def card_rows_map(p: dict) -> dict[str, str]:
    """
    Собирает {метка: значение} из card.rows. Значение-плейсхолдер 'rake'
    заменяется на вывод rakeback_text(). Пропускает служебные флаги.
    """
    out: dict[str, str] = {}
    for row in (p.get("card", {}) or {}).get("rows", []):
        if not row or len(row) < 2:
            continue
        label = str(row[0]).strip()
        value = row[1]
        if value == "rake":
            value = rakeback_text(p)
        out[label] = str(value).strip()
    return out


def to_uk_url(ru_path: str) -> str:
    """
    UK-эквивалент RU-пути. RU-дерево — /ua/…, UK-дерево — /ua/uk/….
    Поле url в partners.json уже содержит /ua/, поэтому вставляем /uk
    ПОСЛЕ /ua, а не префиксуем (иначе получится /ua/uk/ua/…).
    """
    if ru_path.startswith("/ua/uk/"):
        return f"{SITE}{ru_path}"
    if ru_path.startswith("/ua/"):
        return f"{SITE}/ua/uk/{ru_path[len('/ua/'):]}"
    return f"{SITE}{ru_path}"


def partner_type_label(p: dict) -> str:
    """Человекочитаемый тип: 'покер-рум (Curaçao)' / 'приватный клуб (ClubGG)'."""
    net = p.get("networkLabel") or p.get("network") or ""
    if p.get("access") == "club" or p.get("type") == "club":
        base = "приватный клуб"
    else:
        base = "покер-рум"
    return f"{base} · {net}" if net else base


# ── Сборка одно-строчной сводки партнёра (для llms.txt) ────────────────────
def partner_oneliner(p: dict) -> str:
    rows = card_rows_map(p)
    parts: list[str] = []

    # Тип + валюта
    cur = p.get("currency", "")
    lead = partner_type_label(p)
    if cur:
        lead += f", валюта {cur}"
    parts.append(lead + ".")

    # Рейкбек (из данных)
    parts.append(f"Рейкбек: {rakeback_text(p)}.")

    # Бонус, депозит, выплаты, доступ, верификация — если есть в карточке
    if rows.get("Welcome-бонус"):
        parts.append(f"Welcome-бонус: {rows['Welcome-бонус']}.")
    if rows.get("Мин. депозит"):
        parts.append(f"Мин. депозит: {rows['Мин. депозит']}.")
    if p.get("payoutLabel"):
        parts.append(f"Выплаты: {p['payoutLabel']}.")
    if rows.get("Доступ"):
        parts.append(f"Доступ: {rows['Доступ']}.")
    if rows.get("Верификация"):
        parts.append(f"Верификация: {rows['Верификация']}.")

    # Правовой статус (из данных, с явным отрицанием КРАИЛ где надо)
    parts.append(legal_status(p))

    # Kozyr Score
    if p.get("score") is not None:
        parts.append(f"Kozyr Score {p['score']}.")

    return " ".join(parts)


# ── Список статей: живые папки на диске + обогащение из таксономии ──────────
def clean_page_title(raw: str) -> str:
    """Из <title> убирает SEO-хвост ' | KOZYR', ' — … | …' и т.п."""
    t = raw.strip()
    # Отрезаем всё после разделителя-пайпа (обычно ' | KOZYR')
    t = t.split("|")[0].strip()
    return t


def parse_page_meta(index_html: Path) -> tuple[str, str]:
    """Фолбэк: вытащить (title, description) из самой страницы."""
    try:
        html = index_html.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ("", "")
    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            title = clean_page_title(m.group(1))
    desc = ""
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.I)
    if m:
        desc = m.group(1).strip()
    return (title, desc)


def collect_articles(blog_dir: Path, taxonomy_path: Path, url_prefix: str) -> list[dict]:
    """
    Возвращает [{slug, title, description, url}], отсортированный по slug
    (детерминированно — чистые git-диффы). Источник «что опубликовано» —
    реальные папки на диске; заголовки берём из таксономии, иначе из страницы.
    """
    if not blog_dir.exists():
        return []
    tax = load_json(taxonomy_path) if taxonomy_path.exists() else {}
    tax_articles = tax.get("articles", {}) if isinstance(tax, dict) else {}

    items: list[dict] = []
    for child in sorted(blog_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name in NON_ARTICLE_DIRS:
            continue
        index_html = child / "index.html"
        if not index_html.exists():
            continue
        slug = child.name

        entry = tax_articles.get(slug, {})
        title = (entry.get("title") or "").strip()
        desc = (entry.get("summary_for_related") or entry.get("description") or "").strip()

        if not title or not desc:
            p_title, p_desc = parse_page_meta(index_html)
            title = title or p_title
            desc = desc or p_desc

        # Декодируем HTML-сущности (&#x27; → ', &amp; → & …): файл plain-text,
        # сущности из мета-тегов страниц в нём смотрятся мусором.
        title = html.unescape(title).strip()
        desc = html.unescape(desc).strip()

        if not title:
            continue  # без заголовка не выводим

        items.append({
            "slug": slug,
            "title": title,
            "description": desc,
            "url": f"{SITE}{url_prefix}{slug}/",
        })
    return items


# ── Рендер llms.txt (краткий) ──────────────────────────────────────────────
def render_llms_txt(partners: list[dict],
                    ru_articles: list[dict],
                    uk_articles: list[dict]) -> str:
    L: list[str] = []
    L.append("# KOZYR")
    L.append("")
    L.append(f"> {EDITORIAL['summary']}")
    L.append("")
    L.append(EDITORIAL["model"])
    L.append("")

    # Партнёры — с RU и UK ссылками (устраняет UK-only перекос)
    L.append("## Партнёры (румы и клубы)")
    L.append("")
    for p in partners:
        ru_url = f"{SITE}{p['url']}"
        uk_url = to_uk_url(p["url"])
        L.append(f"- [{p['name']}]({ru_url}): {partner_oneliner(p)} "
                 f"Укр. версия: {uk_url}")
    L.append("")

    # Блог RU
    if ru_articles:
        L.append("## Блог — на русском (/ua/)")
        L.append("")
        for a in ru_articles:
            desc = f": {a['description']}" if a["description"] else ""
            L.append(f"- [{a['title']}]({a['url']}){desc}")
        L.append("")

    # Блог UK
    if uk_articles:
        L.append("## Блог — украинский (/ua/uk/)")
        L.append("")
        for a in uk_articles:
            desc = f": {a['description']}" if a["description"] else ""
            L.append(f"- [{a['title']}]({a['url']}){desc}")
        L.append("")

    # Правовая информация — обе версии
    L.append("## Правовая информация")
    L.append("")
    L.append(f"- [Правовая информация — RU]({SITE}/ua/legal/): "
             "Пользовательское соглашение, Политика конфиденциальности, "
             "Ответственная игра, Отказ от ответственности.")
    L.append(f"- [Правова інформація — UK]({SITE}/ua/uk/legal/)")
    L.append("")

    # Optional
    L.append("## Optional")
    L.append("")
    L.append(f"- [Главная (русский)]({SITE}/ua/): Каталог сделок с фильтрами "
             "по рейкбеку, стране, лимитам, формату и платёжкам. Калькулятор "
             "Kozyr Match.")
    L.append(f"- [Головна (українською)]({SITE}/ua/uk/): Та же витрина на "
             "украинском.")
    L.append(f"- [Sitemap]({SITE}/sitemap.xml): Полная карта сайта со всеми "
             "языковыми версиями.")
    L.append("")

    return "\n".join(L).rstrip() + "\n"


# ── Рендер llms-full.txt (подробный) ───────────────────────────────────────
def render_llms_full(partners: list[dict],
                     ru_articles: list[dict],
                     uk_articles: list[dict]) -> str:
    today = date.today().isoformat()
    L: list[str] = []
    L.append("# KOZYR — полная справка для ИИ")
    L.append("")
    L.append(f"> {EDITORIAL['summary']} Этот файл содержит развёрнутую "
             "справочную информацию о проекте, партнёрах и ключевых понятиях "
             "для языковых моделей.")
    L.append("")
    L.append(f"_Сгенерировано автоматически из partners.json и taxonomy — "
             f"дата: {today}. Не редактировать вручную._")
    L.append("")

    L.append("## О проекте")
    L.append("")
    L.append(EDITORIAL["model"])
    L.append("")
    L.append("KOZYR НЕ является оператором азартных игр, покерным румом, "
             "финансовой организацией или платёжной системой. Рейкбек, "
             "депозиты и выплаты ведёт сам рум или клуб напрямую с игроком.")
    L.append("")
    L.append("География: сейчас основной фокус — Украина, расчёты в UAH, "
             "поддержка на русском и украинском. В планах — Польша и Германия.")
    L.append("")

    # Глоссарий
    L.append("## Ключевые понятия")
    L.append("")
    for term, definition in EDITORIAL["glossary"]:
        L.append(f"**{term}** — {definition}")
        L.append("")

    # Партнёры — подробно, факты из данных
    for p in partners:
        rows = card_rows_map(p)
        ext = EDITORIAL["partner_extended"].get(p["id"], {})
        L.append(f"## {p['name']} ({partner_type_label(p)})")
        L.append("")
        L.append(f"URL (RU): {SITE}{p['url']}")
        L.append(f"URL (UK): {to_uk_url(p['url'])}")
        if p.get("score") is not None:
            L.append(f"Kozyr Score: {p['score']}")
        L.append("")
        L.append(f"Рейкбек: {rakeback_text(p)}.")
        L.append(f"Правовой статус: {legal_status(p)}")
        formats = ext.get("formats") or rows.get("Форматы")
        if formats:
            L.append(f"Форматы: {formats}.")
        # Собираем остальные факты карточки, если есть
        fact_line = []
        if p.get("currency"):
            fact_line.append(f"валюта — {p['currency']}")
        if rows.get("Мин. депозит"):
            fact_line.append(f"мин. депозит {rows['Мин. депозит']}")
        if rows.get("Welcome-бонус"):
            fact_line.append(f"welcome-бонус {rows['Welcome-бонус']}")
        if p.get("payoutLabel"):
            fact_line.append(f"выплаты {p['payoutLabel']}")
        if rows.get("Доступ"):
            fact_line.append(f"доступ {rows['Доступ']}")
        if rows.get("Верификация"):
            fact_line.append(f"верификация {rows['Верификация']}")
        if fact_line:
            L.append("Условия: " + ", ".join(fact_line) + ".")
        if ext.get("fits"):
            L.append(ext["fits"])
        L.append("")

    # Краткое сравнение (факты из данных)
    if len(partners) >= 2:
        L.append("## Сравнение партнёров (кратко)")
        L.append("")
        for p in partners:
            L.append(f"- **{p['name']}**: рейкбек — {rakeback_text(p)}; "
                     f"{legal_status(p).split('.')[0]}.")
        L.append("")

    # Блог
    if ru_articles or uk_articles:
        L.append("## Материалы блога")
        L.append("")
        if ru_articles:
            L.append("Русскоязычные (/ua/blog/):")
            L.append("")
            for a in ru_articles:
                desc = f" — {a['description']}" if a["description"] else ""
                L.append(f"- {a['title']} ({a['url']}){desc}")
            L.append("")
        if uk_articles:
            L.append("Украиноязычные (/ua/uk/blog/):")
            L.append("")
            for a in uk_articles:
                desc = f" — {a['description']}" if a["description"] else ""
                L.append(f"- {a['title']} ({a['url']}){desc}")
            L.append("")

    # Первоисточники
    if EDITORIAL.get("sources"):
        L.append("## Первоисточники")
        L.append("")
        for name, url in EDITORIAL["sources"]:
            L.append(f"- {name}: {url}")
        L.append("")

    # Правовое
    L.append("## Правовая информация")
    L.append("")
    L.append(f"- Правовая информация (RU): {SITE}/ua/legal/")
    L.append(f"- Правова інформація (UK): {SITE}/ua/uk/legal/")
    L.append("")
    L.append("Ограничение: сайт только для лиц от 21 года. KOZYR поддерживает "
             "принципы ответственной игры и размещает информацию о рисках и "
             "самоограничении.")
    L.append("")

    return "\n".join(L).rstrip() + "\n"


# ── main ───────────────────────────────────────────────────────────────────
def build() -> tuple[str, str]:
    partners = load_partners()
    ru_articles = collect_articles(BLOG_RU_DIR, TAXONOMY_RU, "/ua/blog/")
    uk_articles = collect_articles(BLOG_UK_DIR, TAXONOMY_UK, "/ua/uk/blog/")
    return (
        render_llms_txt(partners, ru_articles, uk_articles),
        render_llms_full(partners, ru_articles, uk_articles),
    )


def write_files() -> tuple[Path, Path]:
    """
    Сгенерировать и записать оба файла. Возвращает пути.
    Предназначено для импорта из publish.py — вызывается в конце публикации,
    чтобы llms.txt/full пересобирались из partners.json в том же коммите и
    никогда не расходились. Ошибки НЕ подавляет — вызывающий сам решает,
    оборачивать ли в try/except (публикацию ронять не стоит).
    """
    llms_txt, llms_full = build()
    LLMS_TXT.write_text(llms_txt, encoding="utf-8")
    LLMS_FULL_TXT.write_text(llms_full, encoding="utf-8")
    return LLMS_TXT, LLMS_FULL_TXT


def main() -> int:
    ap = argparse.ArgumentParser(description="Генератор llms.txt / llms-full.txt")
    ap.add_argument("--check", action="store_true",
                    help="не писать; выйти 1 если файлы на диске разошлись")
    ap.add_argument("--stdout", action="store_true",
                    help="напечатать llms.txt в stdout и выйти")
    args = ap.parse_args()

    llms_txt, llms_full = build()

    if args.stdout:
        sys.stdout.write(llms_txt)
        return 0

    if args.check:
        drift = False
        for path, expected in ((LLMS_TXT, llms_txt), (LLMS_FULL_TXT, llms_full)):
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != expected:
                drift = True
                print(f"⚠️  {path.name} разошёлся с partners.json/taxonomy — "
                      f"запусти: python automation/build_llms.py")
        if drift:
            return 1
        print("✓ llms.txt и llms-full.txt актуальны.")
        return 0

    LLMS_TXT.write_text(llms_txt, encoding="utf-8")
    LLMS_FULL_TXT.write_text(llms_full, encoding="utf-8")
    print(f"✓ Записан {LLMS_TXT.relative_to(ROOT)} ({len(llms_txt)} байт)")
    print(f"✓ Записан {LLMS_FULL_TXT.relative_to(ROOT)} ({len(llms_full)} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
