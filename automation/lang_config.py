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


def build_hreflang_block(*, lang, slug, translation_of):
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
    if "ru" in versions:
        xdefault_url = canonical_url_for("ru", versions["ru"])
    else:
        xdefault_url = canonical_url_for(lang, versions[lang])
    lines.append(f'<link rel="alternate" hreflang="x-default" href="{xdefault_url}">')
    return "\n".join(lines)
