"""
KOZYR — конфигурация СТРАН и их языков.

Одна страна = один регион = набор языков, которые в нём используются.
При добавлении новой страны редактируется ТОЛЬКО этот файл + добавляется
запись в LANG_CONFIG в lang_config.py.

Дизайн:
- Тема в Google Sheets привязана к СТРАНЕ (колонка `country`).
- Все языки этой страны генерятся вместе (одна тема → N статей).
- Первый язык в `languages` = primary. С него начинается генерация,
  остальные — переводы через translator.py.
- URL первичного языка живёт в корне страны:      /ua/blog/{slug}/
- URL остальных языков — с language-суффиксом:    /ua/uk/blog/{slug}/
- Слуг общий для всех переводов одной темы.

Пример добавления Польши:
  1. В LANG_CONFIG добавить "pl" и "pl_uk" (если будет украинская польская версия).
  2. Здесь:
       "pl": {
           "name": "Польша",
           "flag": "🇵🇱",
           "languages": ["pl", "uk"],
           "primary_language": "pl",
           "url_prefix": "/pl",
       },
  3. Всё. Пайплайн подхватит автоматически.
"""

from __future__ import annotations

from typing import TypedDict


class CountryCfg(TypedDict):
    name: str                    # Отображаемое имя ("Украина")
    flag: str                    # Эмодзи-флаг ("🇺🇦")
    languages: list[str]         # Все языки страны, порядок ВАЖЕН: [0] = primary
    primary_language: str        # Дублируется отдельно для явности
    url_prefix: str              # Корневой путь страны ("/ua")


COUNTRY_CONFIG: dict[str, CountryCfg] = {
    "ua": {
        "name": "Украина",
        "flag": "🇺🇦",
        "languages": ["ru", "uk"],
        "primary_language": "ru",
        "url_prefix": "/ua",
    },
    # Заготовки на будущее — раскомментируй когда будешь запускать регион:
    #
    # "pl": {
    #     "name": "Польша",
    #     "flag": "🇵🇱",
    #     "languages": ["pl", "uk"],
    #     "primary_language": "pl",
    #     "url_prefix": "/pl",
    # },
    # "kz": {
    #     "name": "Казахстан",
    #     "flag": "🇰🇿",
    #     "languages": ["ru", "kk"],
    #     "primary_language": "ru",
    #     "url_prefix": "/kz",
    # },
}


def get_country(country_code: str) -> CountryCfg:
    """Возвращает конфиг страны. Кидает ValueError если страна не задана."""
    code = country_code.strip().lower()
    if code not in COUNTRY_CONFIG:
        valid = ", ".join(sorted(COUNTRY_CONFIG.keys()))
        raise ValueError(
            f"Неизвестная страна: {country_code!r}. "
            f"Доступны: {valid}. "
            f"Добавь в automation/country_config.py COUNTRY_CONFIG."
        )
    return COUNTRY_CONFIG[code]


def resolve_langs_for_country(country_code: str, override: str = "") -> list[str]:
    """
    Возвращает список языков для генерации.

    Логика:
      - Если override пустой → все языки страны (из COUNTRY_CONFIG.languages)
      - Если override "ru" или "uk" или "ru,uk" → эти языки, но только те что
        реально доступны в стране (лишние отбрасываются с предупреждением)
      - Порядок гарантируется: primary_language всегда первым

    Примеры для ua (languages=["ru","uk"], primary="ru"):
      resolve_langs_for_country("ua")            → ["ru", "uk"]
      resolve_langs_for_country("ua", "")        → ["ru", "uk"]
      resolve_langs_for_country("ua", "ru")      → ["ru"]
      resolve_langs_for_country("ua", "uk")      → ["uk"]
      resolve_langs_for_country("ua", "ru,uk")   → ["ru", "uk"]
      resolve_langs_for_country("ua", "uk,ru")   → ["ru", "uk"]  # primary впереди
      resolve_langs_for_country("ua", "en")      → ValueError    # не в стране
    """
    cfg = get_country(country_code)
    country_langs = cfg["languages"]
    primary = cfg["primary_language"]

    if not override.strip():
        return list(country_langs)

    requested = [x.strip().lower() for x in override.split(",") if x.strip()]
    if not requested:
        return list(country_langs)

    invalid = [x for x in requested if x not in country_langs]
    if invalid:
        raise ValueError(
            f"Языки {invalid} не поддерживаются в стране {country_code!r}. "
            f"Разрешены: {country_langs}. "
            f"Если правда нужны — сначала добавь их в COUNTRY_CONFIG."
        )

    # Сортируем: primary первым, остальное в порядке COUNTRY_CONFIG.languages
    ordered = []
    if primary in requested:
        ordered.append(primary)
    for lang in country_langs:
        if lang != primary and lang in requested and lang not in ordered:
            ordered.append(lang)
    return ordered


def country_of_lang(lang: str) -> str | None:
    """Обратный поиск: какой стране принадлежит язык (первое совпадение).
    Полезно для миграций/старого кода, где известен только lang."""
    for code, cfg in COUNTRY_CONFIG.items():
        if lang in cfg["languages"]:
            return code
    return None


def all_country_codes() -> list[str]:
    return sorted(COUNTRY_CONFIG.keys())


def all_lang_codes() -> list[str]:
    seen = set()
    result = []
    for cfg in COUNTRY_CONFIG.values():
        for lang in cfg["languages"]:
            if lang not in seen:
                seen.add(lang)
                result.append(lang)
    return result


def is_primary_language(country_code: str, lang: str) -> bool:
    """True если lang — первичный язык страны (например ru для ua)."""
    return get_country(country_code)["primary_language"] == lang


def build_lang_key(country_code: str, lang: str) -> str:
    """
    Собирает ключ языка для использования в LANG_CONFIG.

    Правило: если lang — первичный язык страны, ключ = country_code
    (для обратной совместимости: было lang="ru" при только-Украине).
    Иначе ключ = "{country_code}_{lang}" (например "ua_uk", "pl_uk").

    Причина: сейчас в проекте lang_config использует ключи типа "ru".
    С многостраностью естественно перейти на "ua"/"ua_uk"/"pl"/"pl_uk",
    но мы сохраняем "ru" для существующего кода (это по факту "украинская
    русскоязычная версия"). Новый украинский будет "ua_uk".
    """
    if is_primary_language(country_code, lang):
        return country_code  # "ua"  → украинская русскоязычная (устаревшее "ru")
    return f"{country_code}_{lang}"  # "ua_uk"


# ==== Утилиты для отчётов ====

def describe_country(country_code: str) -> str:
    """'🇺🇦 Украина (ru, uk)' — для сообщений в TG."""
    cfg = get_country(country_code)
    langs_str = ", ".join(cfg["languages"])
    return f"{cfg['flag']} {cfg['name']} ({langs_str})"
