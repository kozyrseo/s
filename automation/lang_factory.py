"""
lang_factory.py — ФАБРИКА конфигов языковых версий (Уровень 3 мультигео-модели).

Собирает полный конфиг для пары (страна, язык) из:
  • country_config.py  — данные страны (url_prefix, languages, primary, iso_country)
  • lang_texts.py      — UI-тексты и языковые строки по коду языка

Вместо ручного прописывания каждой (страна × язык) комбинации — все технические
поля (пути, hreflang, локали, canonical) ГЕНЕРИРУЮТСЯ по единым правилам.

Правила выведены из эталонной украинской конфигурации (ru + uk):
  • primary-язык    → пути без языкового сегмента: /ua/, /ua/blog
  • вторичный язык  → с сегментом: /ua/uk/, /ua/uk/blog
  • hreflang_self   = {lang}-{ISO}         (ru-UA)
  • og_locale       = {lang}_{ISO}         (ru_UA)
  • pending_dir     = _pending (primary) | _pending_{lang} (вторичный)
  • taxonomy        = taxonomy.json (primary) | taxonomy.{lang}.json (вторичный)
  • system_prompt   = system_prompt.md (primary) | system_prompt.{lang}.md (вторичный)

Добавить страну = запись в country_config + тексты новых языков в lang_texts.
Пути и коды соберутся сами. Русский переиспользуется между странами.
"""
from __future__ import annotations
from pathlib import Path

from country_config import get_country
from lang_texts import get_lang_texts

# Пути к корню — те же, что в lang_config.py (единый источник)
AUTOMATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = AUTOMATION_DIR.parent
SITE_URL = "https://kozyr.club"


def _lang_url_segment(country_code: str, lang: str) -> str:
    """Языковой сегмент в пути.
    Primary-язык страны → '' (пусто, живёт в корне страны).
    Вторичный язык      → '{lang}/' (подпапка).
    Для ua: ru → '', uk → 'uk/'.
    """
    cfg = get_country(country_code)
    if lang == cfg["primary_language"]:
        return ""
    return f"{lang}/"


def _other_langs(country_code: str, lang: str) -> list[str]:
    """Остальные языки страны (для og_locale_alt, hreflang_alt)."""
    cfg = get_country(country_code)
    return [l for l in cfg["languages"] if l != lang]


def make_lang_cfg(country_code: str, lang: str) -> dict:
    """Собирает полный конфиг языковой версии (страна, язык).

    Возвращает dict с теми же полями, что исторический LANG_CONFIG[lang],
    но вычисленными из страны — поэтому масштабируется на любую страну.
    """
    country = get_country(country_code)
    if lang not in country["languages"]:
        raise ValueError(
            f"Язык {lang!r} не входит в страну {country_code!r} "
            f"(её языки: {country['languages']}). Проверь country_config.py."
        )

    texts = get_lang_texts(lang)
    ui = dict(texts["ui"])  # копия, чтобы не мутировать исходник

    prefix = country["url_prefix"].rstrip("/")          # "/ua"
    seg = _lang_url_segment(country_code, lang)          # "" или "uk/"
    iso = country["iso_country"]                          # "UA"
    is_primary = (lang == country["primary_language"])

    # ── Базовые пути страницы (с языковым сегментом) ──
    # blog-путь: /ua/blog или /ua/uk/blog
    url_root = f"{prefix}/{seg}".rstrip("/")             # "/ua" или "/ua/uk"
    blog_url_prefix = f"{url_root}/blog"                  # "/ua/blog" или "/ua/uk/blog"

    # ── Директории на диске ──
    # primary: ua/blog ; secondary: ua/uk/blog
    country_dir_name = prefix.lstrip("/")                # "ua"
    # pending_dir: исторически Украина использует _pending (primary) и
    # _pending_uk (вторичный) — сохраняем это для обратной совместимости.
    # Новые страны получают _pending_{country} и _pending_{country}_{lang},
    # чтобы генерация разных стран не пересекалась в одной папке.
    is_default_country = (country_code == "ua")
    if is_primary:
        blog_dir = REPO_ROOT / country_dir_name / "blog"
        pending_dir = (REPO_ROOT / "_pending") if is_default_country \
            else (REPO_ROOT / f"_pending_{country_code}")
        taxonomy = (AUTOMATION_DIR / "taxonomy.json") if is_default_country \
            else (AUTOMATION_DIR / f"taxonomy.{country_code}.json")
        system_prompt = AUTOMATION_DIR / "prompts" / "system_prompt.md"
    else:
        blog_dir = REPO_ROOT / country_dir_name / lang / "blog"
        pending_dir = (REPO_ROOT / f"_pending_{lang}") if is_default_country \
            else (REPO_ROOT / f"_pending_{country_code}_{lang}")
        taxonomy = (AUTOMATION_DIR / f"taxonomy.{lang}.json") if is_default_country \
            else (AUTOMATION_DIR / f"taxonomy.{country_code}.{lang}.json")
        system_prompt = AUTOMATION_DIR / "prompts" / f"system_prompt.{lang}.md"

    # ── Локали / hreflang: {lang}-{ISO} и {lang}_{ISO} ──
    hreflang_self = f"{lang}-{iso}"                      # "ru-UA"
    og_locale = f"{lang}_{iso}"                          # "ru_UA"
    others = _other_langs(country_code, lang)
    # alt — первый «соседний» язык (в текущей 2-язычной модели он один)
    if others:
        alt = others[0]
        hreflang_alt = f"{alt}-{iso}"
        og_locale_alt = f"{alt}_{iso}"
    else:
        hreflang_alt = hreflang_self
        og_locale_alt = og_locale

    # ── article_section_map: пути секций каталога → переведённые названия ──
    # ВАЖНО: секции указывают на ОБЩИЕ страницы каталога (без языкового сегмента).
    # И ru, и uk используют одни пути /ua/rooms/, /ua/clubs/, /ua/ — меняются
    # только НАЗВАНИЯ (перевод). Названия берём из lang_texts.
    section_map = {
        f"{prefix}/rooms/pokerbet/": texts["section_room_reviews"],
        f"{prefix}/clubs/klubok/": texts["section_club_reviews"],
        f"{prefix}/": texts["section_rakeback_deals"],
    }

    cfg = {
        # директории
        "pending_dir": pending_dir,
        "blog_dir": blog_dir,
        "blog_index": blog_dir / "index.html",
        # url
        "url_prefix": blog_url_prefix,
        "canonical_base": f"{SITE_URL}{blog_url_prefix}",
        "home_url": f"{url_root}/".replace("//", "/") if url_root else "/",
        "blog_url": f"{blog_url_prefix}/",
        # коды языка
        "html_lang": lang,
        "og_locale": og_locale,
        "og_locale_alt": og_locale_alt,
        "hreflang_self": hreflang_self,
        "hreflang_alt": hreflang_alt,
        # языковые тексты (из lang_texts)
        "lang_name_native": texts["lang_name_native"],
        "lang_name_ru": texts["lang_name_ru"],
        "hero_alt_prefix": texts["hero_alt_prefix"],
        # файлы
        "system_prompt": system_prompt,
        "taxonomy": taxonomy,
        # секции
        "article_section_map": section_map,
        # ui-блок
        "ui": ui,
    }
    return cfg
