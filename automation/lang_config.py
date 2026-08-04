"""
KOZYR — конфигурация языков для пайплайна автогенерации статей.

Адаптировано из PokerNet AI. Единственный источник правды для всех
языко- и брендо-зависимых путей, промптов и UI-строк, которые
используют generate.py и publish.py.

KOZYR пока одноязычный (ru). Telegram-канал заложен, но выключен
(см. TELEGRAM_ENABLED в config ниже / переменные окружения).

Как добавить язык (например uk — украинскую версию):
  1. Добавь ключ в LANG_CONFIG со всеми полями.
  2. Создай system_prompt.<lang>.md.
  3. Создай taxonomy.<lang>.json (можно стаб {"articles": {}}).
  4. Добавь язык в choices аргумента --lang.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict


class UIStrings(TypedDict):
    breadcrumb_home: str
    breadcrumb_blog: str
    article_eyebrow: str
    min_read: str
    by_author: str
    author_written_by: str
    author_role: str
    author_bio: str
    last_updated: str
    cta_heading_prefix: str
    cta_heading_suffix: str
    cta_paragraph: str
    cta_button: str
    related_eyebrow: str
    related_heading_prefix: str
    related_heading_em: str
    nav_ai_bot: str
    nav_how: str
    nav_features: str
    nav_compare: str
    nav_cases: str
    nav_reviews: str
    nav_pricing: str
    header_cta: str
    footer_tagline: str
    footer_product_h: str
    footer_company_h: str
    footer_link_ai_bot: str
    footer_link_nlh: str
    footer_link_plo: str
    footer_link_short_deck: str
    footer_link_compare: str
    footer_link_cases: str
    footer_link_reviews: str
    footer_link_pricing: str
    footer_copyright: str
    lang_switcher_label: str
    lang_switcher_aria: str
    lang_switcher_target_url: str
    fab_label: str
    fab_aria: str
    fab_toast: str
    fab_tg_msg: str
    skip_link: str
    burger_aria: str
    nav_aria: str


class LangCfg(TypedDict):
    pending_dir: Path
    blog_dir: Path
    blog_index: Path
    url_prefix: str
    canonical_base: str
    html_lang: str
    og_locale: str
    og_locale_alt: str
    hreflang_self: str
    hreflang_alt: str
    home_url: str
    blog_url: str
    system_prompt: Path
    taxonomy: Path
    article_section_map: dict[str, str]
    ui: UIStrings


# ВАЖНО: замени на реальный домен после покупки (см. DEPLOY.md шаг 5).
# Держим kozyr.club как плейсхолдер, совпадающий с остальным кодом сайта,
# чтобы единая замена sed сработала и здесь.
SITE_URL = "https://kozyr.club"

# Telegram-автопостинг статей в канал проекта. Заложен, но ВЫКЛЮЧЕН.
# Включишь позже: TELEGRAM_ENABLED=true в переменных окружения workflow.
TELEGRAM_ENABLED = True


LANG_CONFIG: dict[str, LangCfg] = {
    # Ключ "ru" исторический (первый язык проекта) — сейчас это
    # "украинская русскоязычная" версия. При переходе на мульти-страны
    # роль языка определяется в country_config.py, а этот ключ остаётся
    # для обратной совместимости с taxonomy.json и старыми статьями.
    "ru": {
        "pending_dir": Path("_pending"),
        "blog_dir": Path("ua/blog"),
        "blog_index": Path("ua/blog/index.html"),
        "url_prefix": "/ua/blog",
        "canonical_base": f"{SITE_URL}/ua/blog",
        "html_lang": "ru",
        "og_locale": "ru_RU",
        "og_locale_alt": "uk_UA",
        "hreflang_self": "ru-UA",
        "hreflang_alt": "uk-UA",
        "home_url": "/ua/",
        "blog_url": "/ua/blog/",
        "system_prompt": Path("automation/prompts/system_prompt.md"),
        "taxonomy": Path("automation/taxonomy.json"),
        # Секции сайта, на которые статьи ставят внутренние ссылки.
        # Ключ = путь целевой страницы, значение = раздел (article:section).
        "article_section_map": {
            "/ua/rooms/pokerbet/": "Обзоры румов",
            "/ua/clubs/klubok/": "Обзоры клубов",
            "/ua/": "Рейкбек и сделки",
        },
        "ui": {
            "breadcrumb_home": "Главная",
            "breadcrumb_blog": "Блог",
            "article_eyebrow": "Блог KOZYR",
            "min_read": "мин чтения",
            "by_author": "Автор: команда KOZYR",
            "author_written_by": "Автор",
            "author_role": "Аналитик рейкбека · KOZYR",
            "author_bio": "Разбираем условия покерных румов и клубов: рейкбек, лимиты, выводы, честность полей. Пишем на основе реальных данных партнёров, которые обновляются по мере поступления.",
            "last_updated": "Обновлено",
            "cta_heading_prefix": "Ищешь ",
            "cta_heading_suffix": " с максимальным рейкбеком?",
            "cta_paragraph": "Подбери сделку под свой формат и лимиты за 15 секунд — в каталоге KOZYR только проверенные румы и клубы.",
            "cta_button": "Открыть каталог",
            "related_eyebrow": "Читать дальше",
            "related_heading_prefix": "Похожие материалы про ",
            "related_heading_em": "рейкбек и румы",
            # Навигация KOZYR (переиспользуем поля nav_* под пункты меню сайта)
            "nav_ai_bot": "Каталог",
            "nav_how": "Как это работает",
            "nav_features": "Рейкбек",
            "nav_compare": "Сравнение",
            "nav_cases": "Румы",
            "nav_reviews": "Клубы",
            "nav_pricing": "FAQ",
            "header_cta": "Открыть каталог",
            "footer_tagline": "KOZYR — витрина рейкбек-сделок для покерных игроков. Каталог румов и клубов, честные условия, прямые партнёрские ссылки. Рейкбек начисляет и выплачивает сам рум/клуб.",
            "footer_product_h": "Разделы",
            "footer_company_h": "Проект",
            "footer_link_ai_bot": "Каталог сделок",
            "footer_link_nlh": "PokerBet",
            "footer_link_plo": "KlubOk",
            "footer_link_short_deck": "Сравнение",
            "footer_link_compare": "Сравнение",
            "footer_link_cases": "Блог",
            "footer_link_reviews": "FAQ",
            "footer_link_pricing": "Правовая информация",
            "footer_copyright": "© 2026 KOZYR · Витрина рейкбек-сделок",
            "lang_switcher_label": "UA",
            "lang_switcher_aria": "Перейти на украинскую версию",
            "lang_switcher_target_url": "/ua/uk/",
            # Плавающая Telegram-кнопка (FAB). Пока канал выключен — оставляем
            # строки, но кнопку можно скрыть в шаблоне до запуска канала.
            "fab_label": "Мы в Telegram",
            "fab_aria": "Открыть Telegram-канал KOZYR",
            "fab_toast": "Открываем Telegram...",
            "fab_tg_msg": "Привет! Пришёл из блога KOZYR — расскажите про актуальные рейкбек-сделки.",
            "skip_link": "Перейти к основному содержанию",
            "burger_aria": "Открыть меню",
            "nav_aria": "Основная навигация",
        },
    },
    # Украиноязычная версия для украинской страны (ua). Стать-и живут
    # в /ua/uk/blog/{slug}/, генерируются как перевод русской через
    # translator.py. Тот же slug, что у русской пары (translation_of).
    "uk": {
        "pending_dir": Path("_pending_uk"),
        "blog_dir": Path("ua/uk/blog"),
        "blog_index": Path("ua/uk/blog/index.html"),
        "url_prefix": "/ua/uk/blog",
        "canonical_base": f"{SITE_URL}/ua/uk/blog",
        "html_lang": "uk",
        "og_locale": "uk_UA",
        "og_locale_alt": "ru_RU",
        "hreflang_self": "uk-UA",
        "hreflang_alt": "ru-UA",
        "home_url": "/ua/uk/",
        "blog_url": "/ua/uk/blog/",
        # Промпт для перевода отдельный: translator.py прогоняет русскую
        # статью через Claude с этим system_prompt.
        "system_prompt": Path("automation/prompts/system_prompt.uk.md"),
        "taxonomy": Path("automation/taxonomy.uk.json"),
        "article_section_map": {
            "/ua/rooms/pokerbet/": "Огляди румів",
            "/ua/clubs/klubok/": "Огляди клубів",
            "/ua/": "Рейкбек та угоди",
        },
        "ui": {
            "breadcrumb_home": "Головна",
            "breadcrumb_blog": "Блог",
            "article_eyebrow": "Блог KOZYR",
            "min_read": "хв читання",
            "by_author": "Автор: команда KOZYR",
            "author_written_by": "Автор",
            "author_role": "Аналітик рейкбеку · KOZYR",
            "author_bio": "Розбираємо умови покерних румів та клубів: рейкбек, ліміти, виводи, чесність полів. Пишемо на основі реальних даних партнерів, які оновлюються по мірі надходження.",
            "last_updated": "Оновлено",
            "cta_heading_prefix": "Шукаєш ",
            "cta_heading_suffix": " з максимальним рейкбеком?",
            "cta_paragraph": "Обери угоду під свій формат і ліміти за 15 секунд — у каталозі KOZYR лише перевірені руми та клуби.",
            "cta_button": "Відкрити каталог",
            "related_eyebrow": "Читати далі",
            "related_heading_prefix": "Схожі матеріали про ",
            "related_heading_em": "рейкбек та руми",
            "nav_ai_bot": "Каталог",
            "nav_how": "Як це працює",
            "nav_features": "Рейкбек",
            "nav_compare": "Порівняння",
            "nav_cases": "Руми",
            "nav_reviews": "Клуби",
            "nav_pricing": "FAQ",
            "header_cta": "Відкрити каталог",
            "footer_tagline": "KOZYR — вітрина рейкбек-угод для покерних гравців. Каталог румів та клубів, чесні умови, прямі партнерські посилання. Рейкбек нараховує та виплачує сам рум/клуб.",
            "footer_product_h": "Розділи",
            "footer_company_h": "Проект",
            "footer_link_ai_bot": "Каталог угод",
            "footer_link_nlh": "PokerBet",
            "footer_link_plo": "KlubOk",
            "footer_link_short_deck": "Порівняння",
            "footer_link_compare": "Порівняння",
            "footer_link_cases": "Блог",
            "footer_link_reviews": "FAQ",
            "footer_link_pricing": "Правова інформація",
            "footer_copyright": "© 2026 KOZYR · Вітрина рейкбек-угод",
            # Свитчер на этой странице ведёт на русскую версию
            "lang_switcher_label": "RU",
            "lang_switcher_aria": "Перейти на російську версію",
            "lang_switcher_target_url": "/ua/",
            "fab_label": "Ми в Telegram",
            "fab_aria": "Відкрити Telegram-канал KOZYR",
            "fab_toast": "Відкриваємо Telegram...",
            "fab_tg_msg": "Привіт! Прийшов з блогу KOZYR — розкажіть про актуальні рейкбек-угоди.",
            "skip_link": "Перейти до основного змісту",
            "burger_aria": "Відкрити меню",
            "nav_aria": "Основна навігація",
        },
    },
}


def get_cfg(lang: str) -> LangCfg:
    if lang not in LANG_CONFIG:
        valid = ", ".join(sorted(LANG_CONFIG.keys()))
        raise ValueError(
            f"Неизвестный язык: {lang!r}. Доступны: {valid}. "
            f"Добавь язык в automation/lang_config.py LANG_CONFIG."
        )
    return LANG_CONFIG[lang]


def validate_cfg_files_exist(lang: str) -> None:
    cfg = get_cfg(lang)
    if not cfg["system_prompt"].exists():
        raise FileNotFoundError(
            f"System prompt для lang={lang!r} не найден: {cfg['system_prompt']}."
        )
    if not cfg["taxonomy"].exists():
        raise FileNotFoundError(
            f"Taxonomy для lang={lang!r} не найдена: {cfg['taxonomy']} "
            f'(можно создать стаб {{"articles": {{}}}} )'
        )


def _is_valid_slug(slug) -> bool:
    if not isinstance(slug, str):
        return False
    s = slug.strip()
    if not s:
        return False
    if not re.fullmatch(r"[a-z0-9-]+", s):
        return False
    if not any(c.isalpha() for c in s):
        return False
    return True


def canonical_url_for(lang: str, slug: str) -> str:
    if not _is_valid_slug(slug):
        raise ValueError(
            f"canonical_url_for({lang!r}, {slug!r}): slug должен быть "
            f"kebab-case строкой; получено {type(slug).__name__} {slug!r}."
        )
    cfg = get_cfg(lang)
    return f"{cfg['canonical_base']}/{slug}/"


def normalize_translation_map(*, lang, slug, translation_of):
    result = {lang: slug}
    if not translation_of:
        return result
    if isinstance(translation_of, dict):
        for k, v in translation_of.items():
            if k in LANG_CONFIG and v:
                result[k] = v
        result.setdefault(lang, slug)
        return result
    other = str(translation_of).strip()
    if not other:
        return result
    # Одноязычный режим: пары нет. Оставлено для будущей uk-версии.
    return result


def build_hreflang_block(*, lang, slug, translation_of, country_code=None):
    """
    Собирает <link rel="alternate" hreflang="..."> для всех переводов.

    country_code — если задан, x-default будет указывать на primary_language
    этой страны. Если не задан — пытается определить страну по языку через
    country_of_lang(). Если и это не сработало — x-default = текущий язык.
    """
    versions = normalize_translation_map(
        lang=lang, slug=slug, translation_of=translation_of
    )
    if len(versions) < 2:
        return ""
    lines = []
    ordered = [lang] + sorted(k for k in versions if k != lang)
    for code in ordered:
        cfg = get_cfg(code)
        url = canonical_url_for(code, versions[code])
        lines.append(
            f'<link rel="alternate" hreflang="{cfg["hreflang_self"]}" href="{url}">'
        )

    # x-default: primary язык страны, если он есть в переводах.
    # Fallback-цепочка: явный country_code → country_of_lang(lang) → "ru" → lang
    xdefault_lang = None
    try:
        from country_config import get_country, country_of_lang
        if country_code:
            xdefault_lang = get_country(country_code)["primary_language"]
        else:
            c = country_of_lang(lang)
            if c:
                xdefault_lang = get_country(c)["primary_language"]
    except Exception:
        pass
    if not xdefault_lang or xdefault_lang not in versions:
        xdefault_lang = "ru" if "ru" in versions else lang

    xdefault_url = canonical_url_for(xdefault_lang, versions[xdefault_lang])
    lines.append(f'<link rel="alternate" hreflang="x-default" href="{xdefault_url}">')
    return "\n".join(lines)
