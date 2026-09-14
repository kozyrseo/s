"""
lang_texts.py — UI-тексты и языковые строки ПО КОДУ ЯЗЫКА.

Часть масштабируемой мультигео-модели (Уровень 2).
Ключ — чистый код языка (ru, uk, pl, kk...). Тексты НЕ зависят от страны:
русский текст одинаков для Украины и любой другой русскоязычной страны.

Добавить новый язык = добавить сюда запись с переведёнными строками.
Если язык уже есть (напр. ru) — он переиспользуется между странами.
"""

LANG_TEXTS: dict[str, dict] = {
    'ru': {
        'lang_name_native': 'русском',
        'lang_name_ru': 'русском',
        'hero_alt_prefix': 'Иллюстрация к статье:',
        'section_room_reviews': 'Обзоры румов',
        'section_club_reviews': 'Обзоры клубов',
        'section_rakeback_deals': 'Рейкбек и сделки',
        "ui": {
            'breadcrumb_home': 'Главная',
            'breadcrumb_blog': 'Блог',
            'article_eyebrow': 'Блог KOZYR',
            'min_read': 'мин чтения',
            'by_author': 'Автор: Никита Волошин',
            'author_name': 'Никита Волошин',
            'in_this_article': 'В этой статье',
            'author_written_by': 'Автор',
            'author_role': 'Рейкбек-аналитик · KOZYR',
            'author_bio': 'Никита в онлайн-покере больше 10 лет — прошёл путь от микролимитов NL2 до регуляра мидстейкса, поэтому оценивает румы и клубы по тому, что реально видит за столом, а не по рекламе. В KOZYR считает эффективный рейкбек, проверяет мягкость полей, сроки и надёжность выплат — по данным партнёров, которые сверяются с агентами и операторами и обновляются по мере изменений. Без маркетингового тумана и обещаний «занеси и выиграй».',
            'last_updated': 'Обновлено',
            'author_profile_link': 'Об авторе →',
            'cta_heading_prefix': 'Ищешь ',
            'cta_heading_suffix': ' с максимальным рейкбеком?',
            'cta_paragraph': 'Подбери сделку под свой формат и лимиты за 15 секунд — в каталоге KOZYR только проверенные румы и клубы.',
            'cta_button': 'Открыть каталог',
            'related_eyebrow': 'Читать дальше',
            'related_heading_prefix': 'Похожие материалы про ',
            'related_heading_em': 'рейкбек и румы',
            'nav_ai_bot': 'Каталог',
            'nav_how': 'Как это работает',
            'nav_features': 'Рейкбек',
            'nav_compare': 'Сравнение',
            'nav_cases': 'Румы',
            'nav_reviews': 'Клубы',
            'nav_pricing': 'FAQ',
            'header_cta': 'Открыть каталог',
            'footer_tagline': 'KOZYR — витрина рейкбек-сделок для покерных игроков. Каталог румов и клубов, честные условия, прямые партнёрские ссылки. Рейкбек начисляет и выплачивает сам рум/клуб.',
            'footer_product_h': 'Разделы',
            'footer_company_h': 'Проект',
            'footer_link_ai_bot': 'Каталог сделок',
            'footer_link_nlh': 'PokerBet',
            'footer_link_plo': 'KlubOk',
            'footer_link_short_deck': 'Сравнение',
            'footer_link_compare': 'Сравнение',
            'footer_link_cases': 'Блог',
            'footer_link_reviews': 'FAQ',
            'footer_link_pricing': 'Правовая информация',
            'footer_copyright': '© 2026 KOZYR · Витрина рейкбек-сделок · 21+ · Играй ответственно',
            'lang_switcher_label': 'UA',
            'lang_switcher_aria': 'Перейти на украинскую версию',
            'lang_switcher_target_url': '/ua/uk/',
            'fab_label': 'Мы в Telegram',
            'fab_aria': 'Открыть Telegram-канал KOZYR',
            'fab_toast': 'Открываем Telegram...',
            'fab_tg_msg': 'Привет! Пришёл из блога KOZYR — расскажите про актуальные рейкбек-сделки.',
            'skip_link': 'Перейти к основному содержанию',
            'burger_aria': 'Открыть меню',
            'nav_aria': 'Основная навигация',
        },
    },
    'uk': {
        'lang_name_native': 'українській',
        'lang_name_ru': 'украинском',
        'hero_alt_prefix': 'Ілюстрація до статті:',
        'section_room_reviews': 'Огляди румів',
        'section_club_reviews': 'Огляди клубів',
        'section_rakeback_deals': 'Рейкбек та угоди',
        "ui": {
            'breadcrumb_home': 'Головна',
            'breadcrumb_blog': 'Блог',
            'article_eyebrow': 'Блог KOZYR',
            'min_read': 'хв читання',
            'by_author': 'Автор: Микита Волошин',
            'author_name': 'Микита Волошин',
            'in_this_article': 'У цій статті',
            'author_written_by': 'Автор',
            'author_role': 'Рейкбек-аналітик · KOZYR',
            'author_bio': "Нікіта в онлайн-покері понад 10 років — пройшов шлях від мікролімітів NL2 до регуляра мідстейксу, тому оцінює руми та клуби за тим, що реально бачить за столом, а не за рекламою. У KOZYR рахує ефективний рейкбек, перевіряє м'якість полів, строки та надійність виплат — за даними партнерів, які звіряються з агентами й операторами та оновлюються по мірі змін. Без маркетингового туману й обіцянок «занеси і виграй».",
            'last_updated': 'Оновлено',
            'author_profile_link': 'Про автора →',
            'cta_heading_prefix': 'Шукаєш ',
            'cta_heading_suffix': ' з максимальним рейкбеком?',
            'cta_paragraph': 'Обери угоду під свій формат і ліміти за 15 секунд — у каталозі KOZYR лише перевірені руми та клуби.',
            'cta_button': 'Відкрити каталог',
            'related_eyebrow': 'Читати далі',
            'related_heading_prefix': 'Схожі матеріали про ',
            'related_heading_em': 'рейкбек та руми',
            'nav_ai_bot': 'Каталог',
            'nav_how': 'Як це працює',
            'nav_features': 'Рейкбек',
            'nav_compare': 'Порівняння',
            'nav_cases': 'Руми',
            'nav_reviews': 'Клуби',
            'nav_pricing': 'FAQ',
            'header_cta': 'Відкрити каталог',
            'footer_tagline': 'KOZYR — вітрина рейкбек-угод для покерних гравців. Каталог румів та клубів, чесні умови, прямі партнерські посилання. Рейкбек нараховує та виплачує сам рум/клуб.',
            'footer_product_h': 'Розділи',
            'footer_company_h': 'Проект',
            'footer_link_ai_bot': 'Каталог угод',
            'footer_link_nlh': 'PokerBet',
            'footer_link_plo': 'KlubOk',
            'footer_link_short_deck': 'Порівняння',
            'footer_link_compare': 'Порівняння',
            'footer_link_cases': 'Блог',
            'footer_link_reviews': 'FAQ',
            'footer_link_pricing': 'Правова інформація',
            'footer_copyright': '© 2026 KOZYR · Вітрина рейкбек-угод · 21+ · Грай відповідально',
            'lang_switcher_label': 'RU',
            'lang_switcher_aria': 'Перейти на російську версію',
            'lang_switcher_target_url': '/ua/',
            'fab_label': 'Ми в Telegram',
            'fab_aria': 'Відкрити Telegram-канал KOZYR',
            'fab_toast': 'Відкриваємо Telegram...',
            'fab_tg_msg': 'Привіт! Прийшов з блогу KOZYR — розкажіть про актуальні рейкбек-угоди.',
            'skip_link': 'Перейти до основного змісту',
            'burger_aria': 'Відкрити меню',
            'nav_aria': 'Основна навігація',
        },
    },
}


def get_lang_texts(lang: str) -> dict:
    """Тексты для языка. KeyError с понятной ошибкой, если языка нет."""
    if lang not in LANG_TEXTS:
        valid = ", ".join(sorted(LANG_TEXTS.keys()))
        raise KeyError(
            f"Нет UI-текстов для языка {lang!r}. Есть: {valid}. "
            f"Добавь переводы в automation/lang_texts.py LANG_TEXTS."
        )
    return LANG_TEXTS[lang]
