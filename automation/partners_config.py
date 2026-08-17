"""
KOZYR — чтение партнёров из partners.js (единая точка правды).

partners.js — это JS-файл, который используется фронтендом (финдер на
главной, карточки в блоге). Здесь мы вытаскиваем из него список партнёров
в Python, чтобы автоматизация (keyword_researcher, generate) знала:
  • какие площадки МОЖНО продвигать (наши партнёры),
  • какие бренды считать ЧУЖИМИ (всё, чего нет в этом списке),
  • факты о каждой площадке (рейкбек / бонусы / валюта / target_page).

Зачем: раньше список партнёров был захардкожен прямо в тексте промпта.
Добавляешь партнёра в partners.js → приходилось руками править промпт.
Теперь один источник: обновил partners.js — вся автоматизация подхватила.

Мы НЕ исполняем JS. Файл маленький и стабильно отформатирован, поэтому
достаём поля регулярками. Если формат сильно поменяется — парсер вернёт
пустой список, и вызывающая сторона обязана иметь фолбэк.
"""

from __future__ import annotations

import re
from pathlib import Path

# partners.js лежит в корне репозитория (рядом с index.html).
_AUTOMATION_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _AUTOMATION_DIR.parent
_PARTNERS_JS = _REPO_ROOT / "partners.js"

# ─────────────────────────────────────────────────────────────────────────
# Известные ЧУЖИЕ бренды покерных румов/клубов и регуляторные термины.
# Используются как «стоп-лист»: если primary_keyword темы содержит любой из
# них И это НЕ наш партнёр — тема отбраковывается.
#
# Список намеренно широкий: цель — чтобы бот не писал статьи ПРО конкурентов
# (нет партнёрской ссылки → нет монетизации → помогаем чужим ранжироваться).
# Упоминание конкурента ВНУТРИ статьи про нашего партнёра (в роли сравнения)
# — это ок и стоп-листом не ловится, т.к. фильтр смотрит только primary_keyword.
# ─────────────────────────────────────────────────────────────────────────
FOREIGN_ROOM_BRANDS = {
    "ggpoker", "gg poker", "ggpokerok", "pokerok", "fish buffet", "fishbuffet",
    "pokermatch", "poker match",
    "pokerstars", "poker stars",
    "888poker", "888 poker",
    "partypoker", "party poker",
    "pppoker", "pppoker",
    "x-poker", "xpoker", "x poker",
    "wpt global", "wptglobal", "wpt",
    "americas cardroom", "acr",
    "winamax", "unibet", "redstar", "red star",
    "suprema", "upoker", "pokerbros", "poker bros",
    "coinpoker", "coin poker", "natural8", "natural 8",
    "tigergaming", "tiger gaming", "bkc", "betonline",
    "ivey poker", "pokerdom", "покердом",
}

# Регуляторные / юридические термины — тоже стоп-лист (устаревает, отпугивает).
REGULATORY_TERMS = {
    "playcity", "плейсити", "плэйсити",
    "краил", "krail", "крайл",
    "4116", "768-ix", "768-ІХ", "закон",
    "налог", "податок", "ндфл", "военный сбор", "військовий збір",
    "ggr", "лицензи", "ліцензі", "легальн", "легальный", "легальний",
    "нелегальн", "запрет", "заборон", "регулятор", "регулятор",
    "som", "мониторинг", "моніторинг",
    "бездеп",  # бездепозитные бонусы запрещены — тема мертва
}


def _extract_field(block: str, field: str) -> str | None:
    """Достать строковое поле  field: "value"  из блока партнёра."""
    m = re.search(rf'\b{re.escape(field)}\s*:\s*"([^"]*)"', block)
    return m.group(1) if m else None


def _extract_rake(block: str):
    """rake может быть числом (40) или строкой ("none")."""
    m = re.search(r'\brake\s*:\s*("?)([^,"\n]+)\1', block)
    if not m:
        return None
    val = m.group(2).strip()
    if val.isdigit():
        return int(val)
    return val  # "none" и т.п.


def load_partners() -> list[dict]:
    """
    Прочитать partners.js и вернуть список партнёров:
        [{id, name, rake, currency, url, access, license, note}, ...]

    При любой ошибке чтения/парсинга — вернуть [] (вызывающая сторона
    обязана иметь фолбэк, чтобы автоматизация не падала целиком).
    """
    try:
        content = _PARTNERS_JS.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # Берём срез между  var PARTNERS = [  и  ];  — чтобы не зацепить
    # хелперы ниже по файлу.
    start = content.find("var PARTNERS")
    if start == -1:
        return []
    arr_start = content.find("[", start)
    arr_end = content.find("];", arr_start)
    if arr_start == -1 or arr_end == -1:
        return []
    arr = content[arr_start:arr_end]

    partners = []
    # Границы блоков: ищем ВСЕ вхождения  id: "..."  (начало каждого партнёра),
    # затем блок i = от id[i] до id[i+1]. Важно матчить именно  id: "..."  как
    # поле объекта, а не подстроку "id" (она есть в "android", "logoImg" и т.п.).
    id_matches = list(re.finditer(r'\bid\s*:\s*"([^"]+)"', arr))
    for i, m in enumerate(id_matches):
        pid = m.group(1)
        block_start = m.start()
        block_end = id_matches[i + 1].start() if i + 1 < len(id_matches) else len(arr)
        block = arr[block_start:block_end]

        # note: берём top-level note. Режем блок до объекта "card: {"
        # (именно с двоеточием и скобкой — иначе поймаем payments:["card"]).
        m_card = re.search(r'\bcard\s*:\s*\{', block)
        block_top = block[:m_card.start()] if m_card else block

        partners.append({
            "id": pid,
            "name": _extract_field(block, "name") or pid,
            "rake": _extract_rake(block),
            "currency": _extract_field(block, "currency"),
            "url": _extract_field(block, "url"),
            "access": _extract_field(block, "access"),  # direct | club
            "license": _extract_field(block, "license"),
            "note": _extract_field(block_top, "note"),
        })

    return partners


def partner_brand_tokens(partners: list[dict]) -> set[str]:
    """
    Множество «наших» брендовых токенов в нижнем регистре (name + id +
    известные алиасы). Используется, чтобы отличить упоминание своего
    партнёра от чужого рума.
    """
    tokens = set()
    for p in partners:
        if p.get("id"):
            tokens.add(p["id"].lower())
        if p.get("name"):
            tokens.add(p["name"].lower())
    # Ручные алиасы для наших партнёров (как их пишут в поиске).
    aliases = {
        "pokerbet": {"pokerbet", "покербет", "покер бет"},
        "klubok": {"klubok", "клубок", "clubgg", "club gg", "клуб gg"},
    }
    for pid, al in aliases.items():
        if any(p.get("id") == pid for p in partners):
            tokens |= al
    return {t for t in tokens if t}


def is_foreign_or_regulatory(primary_keyword: str, partners: list[dict]) -> str | None:
    """
    Проверить primary_keyword темы на «чужой бренд» или «регуляторику».

    Возвращает:
      • строку-причину (для лога), если тему надо ОТБРАКОВАТЬ,
      • None, если тема чистая.

    Логика:
      1. Если в ключе есть регуляторный термин → отбраковка.
      2. Если в ключе есть чужой бренд И нет нашего партнёра рядом → отбраковка.
         (Наш бренд рядом = сравнительная статья, её оставляем.)
    """
    pk = (primary_keyword or "").lower()
    if not pk:
        return None

    # 1. Регуляторика
    for term in REGULATORY_TERMS:
        if term in pk:
            return f"регуляторный/юридический термин «{term}»"

    # 2. Чужой бренд без нашего партнёра рядом
    our = partner_brand_tokens(partners)
    has_ours = any(tok in pk for tok in our)
    for brand in FOREIGN_ROOM_BRANDS:
        if brand in pk and not has_ours:
            return f"чужой покер-бренд «{brand}» в главном ключе (нет партнёрской ссылки)"

    # 3. «Рейкбек» привязан к нашему партнёру, у которого рейкбека НЕТ.
    #    Напр. «рейкбек в PokerBet» — такого продукта не существует
    #    (PokerBet = только бонусы). Это создаёт дезинформацию.
    if re.search(r"рейкбек|рейкбэк|rakeback|кешбек|кэшбек|кешбэк", pk):
        no_rake_aliases = {
            "pokerbet": {"pokerbet", "покербет", "покер бет"},
            # если добавишь ещё партнёров без рейкбека — впиши их алиасы сюда
            # (или, лучше, читай rake из partners.js — см. ниже)
        }
        # соберём алиасы всех НАШИХ партнёров, у кого rake == none/0
        no_rake_tokens = set()
        for p in partners:
            rake = p.get("rake")
            is_no_rake = (rake is None) or (rake == "none") or (rake == 0)
            if is_no_rake:
                if p.get("id"):
                    no_rake_tokens.add(p["id"].lower())
                if p.get("name"):
                    no_rake_tokens.add(p["name"].lower())
                no_rake_tokens |= no_rake_aliases.get(p.get("id"), set())
        for tok in no_rake_tokens:
            if tok and tok in pk:
                return (f"«рейкбек» привязан к партнёру «{tok}» без рейкбека "
                        f"(у него только бонусы — продукта не существует)")

    return None


# Маркеры отрицания: если чужой бренд идёт ПОСЛЕ такого слова поблизости —
# это инструкция «НЕ упоминать конкурента», а не витрина конкурента.
# Напр. notes «НЕ згадувати GGPoker» — это ХОРОШО, отбраковывать не нужно.
_NEGATION_MARKERS = [
    "не ", "нэ ", "без ", "уникат", "уникай", "избега", "избегай",
    "не згад", "не упомин", "не назыв", "не назив", "не рекл",
    "avoid", "without", "not mention", "don't", "dont", "except",
    "крім наш", "кроме наш", "тільки наш", "только наш", "лише наш",
]


def _brand_is_negated(text_lower: str, brand_pos: int) -> bool:
    """
    Проверяет, стоит ли перед брендом (в окне ~60 символов слева) маркер
    отрицания. Если да — упоминание бренда «защитное» (не упоминать его),
    и отбраковывать тему НЕ нужно.
    """
    window_start = max(0, brand_pos - 60)
    left_context = text_lower[window_start:brand_pos]
    return any(marker in left_context for marker in _NEGATION_MARKERS)


def has_foreign_brand_strict(text: str) -> str | None:
    """
    СТРОГАЯ проверка на чужой бренд — для свободного текста (notes, topic,
    secondary_keywords). Любой чужой бренд считается проблемой ДАЖЕ рядом с
    нашим партнёром — НО с учётом отрицания.

    Почему строже: в notes/topic конкуренты часто перечисляются как ровня
    («Перечисли румы: PokerBet, GGPoker, PokerMatch») — это витрина
    конкурентов. НО если бренд идёт после «не упоминать / уникати / avoid» —
    это правильная инструкция автору, её отбраковывать нельзя.

    Возвращает причину-строку или None.
    """
    t = (text or "").lower()
    if not t:
        return None
    for brand in FOREIGN_ROOM_BRANDS:
        pos = t.find(brand)
        if pos == -1:
            continue
        # бренд найден — но не под отрицанием ли он?
        if _brand_is_negated(t, pos):
            continue
        return f"упоминание чужого бренда «{brand}»"
    return None


def screen_topic(topic: dict, partners: list[dict]) -> str | None:
    """
    Полная проверка ТЕМЫ (dict со всеми полями) на пригодность.
    Объединяет все правила:
      • primary_keyword / target_page — is_foreign_or_regulatory (мягко:
        сравнение с нашим партнёром допустимо);
      • notes / topic / secondary_keywords — строгий скан чужих брендов
        + регуляторика.

    Возвращает причину отбраковки (для лога) или None если тема чистая.
    Это ЕДИНАЯ точка проверки — используется и на входе (новые темы от
    Claude), и при чистке таблицы (старый мусор).
    """
    # 1. Ключевые поля — мягкий режим (сравнение с нашим партнёром допустимо),
    #    сюда же попадает проверка на «рейкбек у беспейкбекового партнёра».
    for field in ("primary_keyword", "target_page"):
        reason = is_foreign_or_regulatory(topic.get(field, ""), partners)
        if reason:
            return f"{reason} [поле {field}]"

    # 2. Регуляторика — в любом текстовом поле
    for field in ("primary_keyword", "secondary_keywords", "topic", "notes"):
        txt = (topic.get(field, "") or "").lower()
        for term in REGULATORY_TERMS:
            if term in txt:
                return f"регуляторный/юридический термин «{term}» [поле {field}]"

    # 3. Чужие бренды в свободном тексте — строгий режим
    for field in ("topic", "secondary_keywords", "notes"):
        reason = has_foreign_brand_strict(topic.get(field, ""))
        if reason:
            return f"{reason} [поле {field}]"

    return None


def build_partner_facts_block(partners: list[dict]) -> str:
    """
    Собрать человекочитаемый блок «ФАКТЫ О ПЛОЩАДКАХ» для системного промпта
    Claude — динамически из partners.js. Разделяет партнёров по типу
    монетизации (рейкбек vs бонусы), чтобы Claude не путал.
    """
    if not partners:
        return "(не удалось прочитать partners.js — используй общие факты о KOZYR)"

    rake_partners = []
    bonus_partners = []
    for p in partners:
        rake = p.get("rake")
        line = f"• {p['name']}"
        details = []
        if p.get("license"):
            details.append(p["license"])
        if p.get("currency"):
            details.append(f"валюта {p['currency']}")
        if p.get("url"):
            details.append(f"target_page {p['url']}")
        if isinstance(rake, int) and rake > 0:
            line += f" — рейкбек ДО {rake}%"
            if details:
                line += " (" + ", ".join(details) + ")"
            if p.get("note"):
                line += f". {p['note']}"
            rake_partners.append(line)
        else:
            # rake == "none" или отсутствует → монетизация через бонусы
            line += " — рейкбека НЕТ, только бонусы"
            if details:
                line += " (" + ", ".join(details) + ")"
            if p.get("note"):
                line += f". {p['note']}"
            bonus_partners.append(line)

    out = []
    out.append("НАШИ ПАРТНЁРЫ (единственные площадки, которые МОЖНО продвигать):")
    out.append("")
    if rake_partners:
        out.append("С РЕЙКБЕКОМ (рейкбек-темы привязывай СЮДА):")
        out.extend(rake_partners)
        out.append("")
    if bonus_partners:
        out.append("БЕЗ РЕЙКБЕКА, только бонусы (НЕ пиши про их рейкбек — его нет):")
        out.extend(bonus_partners)
    return "\n".join(out)


if __name__ == "__main__":
    ps = load_partners()
    print(f"Найдено партнёров: {len(ps)}")
    for p in ps:
        print(" ", p)
    print()
    print("Брендовые токены:", partner_brand_tokens(ps))
    print()
    print(build_partner_facts_block(ps))
    print()
    # быстрый тест фильтра
    tests = [
        "рейкбек в pokerbet украина 2026",
        "рейкбек ggpoker fish buffet",
        "покерный клуб clubgg украина рейкбек",
        "что такое рейкбек в покере",
        "легальный покер украина закон 2026",
        "налог на выигрыши в покере украина",
        "pokerbet vs ggpoker сравнение",  # наш бренд рядом → ок
    ]
    for t in tests:
        reason = is_foreign_or_regulatory(t, ps)
        verdict = f"❌ {reason}" if reason else "✅ ок"
        print(f"  {verdict}  ← {t!r}")
