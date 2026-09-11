#!/usr/bin/env python3
"""
KOZYR — рендер страницы партнёра из шаблона (Стадия 2 шаблонной системы).

Собирает готовый HTML из:
  • partner_template.html.j2 — статичный скелет (CSS/JS/навигация/футер) +
    Jinja-слоты для данных и прозы;
  • анкеты _partner_drafts/{id}.json — структурные данные (имя, валюта, рейкбек,
    плюсы/минусы, FAQ, лимиты, логотип, реф-ссылка);
  • контента (проза + rich-блоки: about, депозиты, бонусы и т.д.) — на Стадии 3
    его пишет LLM компактным JSON (~11 КБ вместо 85 КБ полной страницы).

Данные (плюсы/минусы/FAQ) идут из анкеты циклами — LLM их не трогает, поэтому
они не теряются. Проза — из контента. Структура — из шаблона, её не сломать.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import re

AUTOMATION = Path(__file__).resolve().parent
REPO_ROOT  = AUTOMATION.parent
TEMPLATE   = AUTOMATION / "partner_template.html.j2"
DRAFTS     = REPO_ROOT / "_partner_drafts"

I18N = {
    "ru": {
            "skip": "Перейти к основному содержанию",
            "compliance_pre": "Используя сайт, ты принимаешь",
            "terms": "Пользовательское соглашение",
            "privacy_doc": "Политику конфиденциальности",
            "compliance_post": ". Только для лиц старше 21 года.",
            "nav_deals": "Сделки",
            "nav_rooms": "Румы",
            "nav_clubs": "Клубы",
            "nav_blog": "Блог",
            "cta_activate_arrow": "Активировать сделку →",
            "cta_activate": "Активировать сделку",
            "partners": "Партнёры",
            "bc_home": "Главная",
            "review_word": "обзор",
            "verdict_label": "Вердикт KOZYR",
            "fact_rakeback": "Рейкбек",
            "fact_license": "Лицензия",
            "fact_currency": "Валюта",
            "fact_kyc": "KYC",
            "fact_access": "Доступ",
            "fact_min_deposit": "Мин. депозит",
            "fact_min_withdraw": "Мин. вывод",
            "fact_formats": "Форматы",
            "fact_payouts": "Выплаты",
            "fact_payments": "Платёжки",
            "toc_title": "Содержание",
            "sec_about": "О руме",
            "sec_legality": "Лицензия и статус",
            "sec_software": "Софт и интерфейс",
            "sec_games": "Игры и лимиты",
            "sec_traffic": "Трафик и поля",
            "sec_deposits": "Депозиты и выплаты",
            "sec_bonus": "Бонусы и промо",
            "sec_rakeback": "Рейкбек",
            "sec_kyc": "Верификация",
            "sec_reviews": "Отзывы",
            "pros_title": "✓ Плюсы",
            "cons_title": "− Минусы",
            "reviews_intro": "Реальные отзывы игроков, которые прошли через KOZYR. Все «проверено» подтверждены скрином депозита или вывода — модерируются вручную.",
            "play": "Играть",
            "sticky_rb": "Рейкбек до",
            "related_title": "Смотри также",
            "rel_pokerbet": "PokerBet — рум на гривны",
            "rel_klubok": "KlubOk — клуб в ClubGG",
            "rel_calc": "Калькулятор рейкбека",
            "rel_faq": "FAQ по сделкам",
            "rel_terms": "Условия использования",
            "footer_tagline": "Прозрачные рейкбек-сделки для украинских покерных игроков. Румы и клубы с проверкой.",
            "foot_product": "Продукт",
            "foot_all_rooms": "Все румы",
            "foot_all_clubs": "Все клубы",
            "foot_calc": "Калькулятор",
            "foot_help": "Помощь",
            "foot_legal": "Юридическое",
            "foot_privacy": "Приватность",
            "foot_rg": "Ответственная игра",
            "foot_howwe": "Как мы зарабатываем",
            "foot_disclaimer": "Отказ от ответственности",
            "copyright": "© 2026 KOZYR · Играй ответственно · 21+",
            "h_proscons_1": "Плюсы и ",
            "h_proscons_2": "минусы",
            "h_reviews_1": "Что говорят",
            "h_reviews_2": "игроки",
            "h_faq_1": "Часто задаваемые",
            "h_faq_2": "вопросы",
            "faq_word": "FAQ",
            "aria_play_in": "Начать игру в",
            "aria_legal": "Правовое уведомление",
            "aria_mainnav": "Основная навигация",
            "aria_home": "KOZYR — на главную",
            "aria_lang": "Выбор языка",
            "aria_menu": "Меню",
            "aria_breadcrumbs": "Хлебные крошки",
            "aria_facts": "Быстрые факты",
            "aria_cta": "Быстрый CTA",
            "toc_heading": "Содержание обзора",
            "rating_of": "из",
            "rating_word": "Рейтинг",
            "reviews_word": "отзывов",
            "and_word": "и",
            "cta_goto": "Перейти в",
            "cta_note_pre": "Заходи в ",
            "cta_note_post": " по ссылке KOZYR"
    },
    "uk": {
            "skip": "Перейти до основного вмісту",
            "compliance_pre": "Користуючись сайтом, ти приймаєш",
            "terms": "Угоду користувача",
            "privacy_doc": "Політику конфіденційності",
            "compliance_post": ". Тільки для осіб старше 21 року.",
            "nav_deals": "Угоди",
            "nav_rooms": "Руми",
            "nav_clubs": "Клуби",
            "nav_blog": "Блог",
            "cta_activate_arrow": "Активувати угоду →",
            "cta_activate": "Активувати угоду",
            "partners": "Партнери",
            "bc_home": "Головна",
            "review_word": "огляд",
            "verdict_label": "Вердикт KOZYR",
            "fact_rakeback": "Рейкбек",
            "fact_license": "Ліцензія",
            "fact_currency": "Валюта",
            "fact_kyc": "KYC",
            "fact_access": "Доступ",
            "fact_min_deposit": "Мін. депозит",
            "fact_min_withdraw": "Мін. вивід",
            "fact_formats": "Формати",
            "fact_payouts": "Виплати",
            "fact_payments": "Платіжки",
            "toc_title": "Зміст",
            "sec_about": "Про рум",
            "sec_legality": "Ліцензія і статус",
            "sec_software": "Софт та інтерфейс",
            "sec_games": "Ігри та ліміти",
            "sec_traffic": "Трафік і поля",
            "sec_deposits": "Депозити та виплати",
            "sec_bonus": "Бонуси та промо",
            "sec_rakeback": "Рейкбек",
            "sec_kyc": "Верифікація",
            "sec_reviews": "Відгуки",
            "pros_title": "✓ Плюси",
            "cons_title": "− Мінуси",
            "reviews_intro": "Реальні відгуки гравців, які пройшли через KOZYR. Усі «перевірено» підтверджені скріном депозиту або виведення — модеруються вручну.",
            "play": "Грати",
            "sticky_rb": "Рейкбек до",
            "related_title": "Дивись також",
            "rel_pokerbet": "PokerBet — рум на гривні",
            "rel_klubok": "KlubOk — клуб у ClubGG",
            "rel_calc": "Калькулятор рейкбеку",
            "rel_faq": "FAQ по угодах",
            "rel_terms": "Умови використання",
            "footer_tagline": "Прозорі рейкбек-угоди для українських покерних гравців. Руми та клуби з перевіркою.",
            "foot_product": "Продукт",
            "foot_all_rooms": "Всі руми",
            "foot_all_clubs": "Всі клуби",
            "foot_calc": "Калькулятор",
            "foot_help": "Допомога",
            "foot_legal": "Юридичне",
            "foot_privacy": "Приватність",
            "foot_rg": "Відповідальна гра",
            "foot_howwe": "Як ми заробляємо",
            "foot_disclaimer": "Відмова від відповідальності",
            "copyright": "© 2026 KOZYR · Грай відповідально · 21+",
            "h_proscons_1": "Плюси та ",
            "h_proscons_2": "мінуси",
            "h_reviews_1": "Що кажуть",
            "h_reviews_2": "гравці",
            "h_faq_1": "Часті",
            "h_faq_2": "запитання",
            "faq_word": "FAQ",
            "aria_play_in": "Почати гру в",
            "aria_legal": "Правове повідомлення",
            "aria_mainnav": "Основна навігація",
            "aria_home": "KOZYR — на головну",
            "aria_lang": "Вибір мови",
            "aria_menu": "Меню",
            "aria_breadcrumbs": "Хлібні крихти",
            "aria_facts": "Швидкі факти",
            "aria_cta": "Швидкий CTA",
            "toc_heading": "Зміст огляду",
            "rating_of": "з",
            "rating_word": "Рейтинг",
            "reviews_word": "відгуків",
            "and_word": "та",
            "cta_goto": "Перейти до",
            "cta_note_pre": "Заходь у ",
            "cta_note_post": " за посиланням KOZYR"
    },
}




def _bump_js_versions(html):
    """Проставляет ?v=<hash> для kozyr-enhance.js/kozyr-reviews.js по хешу файла."""
    import hashlib
    for jsname in ("kozyr-enhance.js", "kozyr-reviews.js"):
        jsfile = REPO_ROOT / "assets" / jsname
        if jsfile.exists():
            ver = hashlib.md5(jsfile.read_bytes()).hexdigest()[:8]
            html = re.sub(r"(" + re.escape(jsname) + r"\?v=)[A-Za-z0-9._-]+",
                          lambda m, v=ver: m.group(1) + v, html)
    return html


def build_page(draft: dict, content: dict, lang: str = "ru", is_preview: bool = False) -> str:
    """Рендерит HTML страницы партнёра из шаблона + анкеты + контента.

    lang="ru" → страница в /{country}/{kind}/{id}/  (основная)
    lang="uk" → украинская версия в /{country}/uk/{kind}/{id}/
    """
    from jinja2 import Template
    tpl = TEMPLATE.read_text(encoding="utf-8")
    p = dict(draft)
    # Превью не должно попадать в индекс (дубль финальной страницы).
    p["robots"] = "noindex, nofollow" if is_preview else "index, follow, max-image-preview:large"
    p["kind"] = "clubs" if draft.get("type") == "club" else "rooms"
    p["ref_url_final"] = (draft.get("ref_url") or "").strip() or "https://t.me/kozyr_support"
    p.setdefault("pros", [])
    p.setdefault("cons", [])
    p.setdefault("faq", [])
    # Принимаемые страны для геоблока: 'all' = принимает весь мир (не блокировать),
    # иначе список кодов через запятую из анкеты. country = основное гео (путь).
    _ac = draft.get("acceptedCountries") or []
    if (not _ac) or ("all" in _ac) or ("*" in _ac):
        p["accept_attr"] = "all"
    else:
        p["accept_attr"] = ",".join(str(c).strip().lower() for c in _ac)
    # Страны-исключения: откуда партнёр НЕ принимает (даже при accept=all).
    _ex = draft.get("excludedCountries") or []
    p["exclude_attr"] = ",".join(str(c).strip().lower() for c in _ex)
    country = draft.get("country", "ua")
    base = f"https://kozyr.club/{country}"
    if lang == "uk":
        p["lang_tag"] = "uk-UA"
        p["og_locale"] = "uk_UA"
        p["canonical"] = f"{base}/uk/{p['kind']}/{draft['id']}/"
    else:
        p["lang_tag"] = "ru-UA"
        p["og_locale"] = "ru_UA"
        p["canonical"] = f"{base}/{p['kind']}/{draft['id']}/"
    html = Template(tpl).render(p=p, content=content, t=I18N.get(lang, I18N["ru"]))
    return _bump_js_versions(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="ID партнёра из _partner_drafts/")
    ap.add_argument("--content", required=True, help="Путь к JSON с контентом (проза)")
    ap.add_argument("--out", default="", help="Куда записать (по умолч. _pending_partner/{id}/index.html)")
    args = ap.parse_args()

    draft_file = DRAFTS / f"{args.id}.json"
    if not draft_file.exists():
        sys.exit(f"❌ Нет анкеты: {draft_file}")
    draft = json.loads(draft_file.read_text(encoding="utf-8"))
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))

    html = build_page(draft, content)

    out = Path(args.out) if args.out else (REPO_ROOT / "_pending_partner" / args.id / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ Страница собрана из шаблона: {out} ({len(html)} символов)")


if __name__ == "__main__":
    main()
