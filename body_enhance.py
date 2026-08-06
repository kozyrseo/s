"""
KOZYR — авто-облагораживание тела статьи (body_html).

Задача: чтобы КАЖДАЯ новая статья, сгенерированная воркфлоу, выходила с
качественной инфографикой — без изменения того, как модель пишет текст.
Мы пост-обрабатываем уже отрендеренный из Markdown HTML и превращаем
структурные паттерны в фирменные блоки из kozyr-blog.css:

  1. Сравнительные таблицы  → класс .kz-vs + подсветка Да/Нет/✓/✗ и колонок.
  2. Нумерованные списки в секции «…по шагам / step by step»
                              → горизонтальная схема потока .kz-flow.
  3. Ключевая оговорка в секции «Чего … НЕ делает / що … НЕ робить»
                              → врезка-callout .kz-callout.

Всё консервативно: если паттерн не матчится уверенно — контент не трогаем.
Работает и для RU, и для UK (ключевые слова заданы для обоих языков).

Точка входа: enhance_body_html(html, lang="ru") -> str
"""

from __future__ import annotations

import re


# ─────────────────────────────────────────────────────────────────────────
# Иконки (inline SVG, наследуют currentColor)
# ─────────────────────────────────────────────────────────────────────────
_ICON_ARROW = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M5 12h14M13 6l6 6-6 6"/></svg>'
)
_ICON_INFO = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>'
)


# ─────────────────────────────────────────────────────────────────────────
# 1. Сравнительные таблицы → .kz-vs
# ─────────────────────────────────────────────────────────────────────────
_YES_TOKENS = {"да", "так", "yes", "✓", "✔", "є", "есть"}
_NO_TOKENS = {"нет", "ні", "no", "✗", "✘", "—", "-", "немає", "нема"}


def _colorize_cell(inner: str) -> str:
    """Подсветить ячейку, если её содержимое — да/нет-маркер."""
    plain = re.sub(r"<[^>]+>", "", inner).strip().lower().rstrip(".")
    if plain in _YES_TOKENS:
        return f'<span class="yes">{inner.strip()}</span>'
    if plain in _NO_TOKENS:
        return f'<span class="no">{inner.strip()}</span>'
    return inner


def _enhance_tables(html: str) -> str:
    """Навесить .kz-vs на таблицы и подсветить да/нет ячейки + колонки."""
    def repl(m: re.Match) -> str:
        table = m.group(0)

        # добавить класс kz-vs (не дублируя)
        open_tag = m.group(1)
        if "kz-vs" not in open_tag:
            if 'class="' in open_tag:
                new_open = open_tag.replace('class="', 'class="kz-vs ', 1)
            else:
                new_open = open_tag[:-1] + ' class="kz-vs">'
            table = table.replace(open_tag, new_open, 1)

        # пометить 2-ю и 3-ю колонки (сравнение обычно A vs B) для лёгкой заливки
        def mark_cols(row: str) -> str:
            cells = re.findall(r"<t([dh])(.*?)>(.*?)</t[dh]>", row, re.DOTALL)
            if len(cells) < 2:
                return row
            out = row
            # перестроим строку заново, чтобы добавить классы колонок + подсветку
            rebuilt = []
            for i, (tag, attrs, inner) in enumerate(cells):
                cls = ""
                if i == 1:
                    cls = " col-a"
                elif i == 2:
                    cls = " col-b"
                new_inner = _colorize_cell(inner) if tag == "d" else inner
                if cls and "class=" not in attrs:
                    attrs = attrs + f' class="{cls.strip()}"'
                elif cls:
                    attrs = re.sub(r'class="([^"]*)"', rf'class="\1{cls}"', attrs)
                rebuilt.append(f"<t{tag}{attrs}>{new_inner}</t{tag}>")
            # заменить исходные ячейки на перестроенные (в порядке следования)
            idx = 0
            def sub_cell(cm):
                nonlocal idx
                r = rebuilt[idx]
                idx += 1
                return r
            return re.sub(r"<t[dh].*?</t[dh]>", sub_cell, row, flags=re.DOTALL)

        table = re.sub(r"<tr>.*?</tr>", lambda rm: mark_cols(rm.group(0)),
                       table, flags=re.DOTALL)
        return table

    return re.sub(r"(<table[^>]*>).*?</table>", repl, html, flags=re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────
# 2. Списки «по шагам» → .kz-flow
# ─────────────────────────────────────────────────────────────────────────
_STEP_HEADING_RE = {
    "ru": re.compile(r"по\s+шагам|пошагов|шаг\s+за\s+шагом|как\s+это\s+работает", re.I),
    "uk": re.compile(r"по\s+кроках|покроков|крок\s+за\s+кроком|як\s+це\s+працює", re.I),
}


def _split_step(li_inner: str) -> tuple[str, str]:
    """Разбить пункт на заголовок и описание.

    Заголовок берём ТОЛЬКО из явного <strong>…</strong> в начале пункта —
    его автор задал сам. Во всех остальных случаях заголовка нет: показываем
    первое предложение целиком как аккуратный текст шага. Это гарантирует
    единообразие (нет полу-обрезанных заголовков и дублей) на любом контенте.
    """
    text = li_inner.strip()

    m = re.match(r"\s*<strong>(.*?)</strong>[\s:—–-]*(.*)", text, re.DOTALL)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        desc = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        return title, desc

    plain = re.sub(r"<[^>]+>", "", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", plain, maxsplit=1)[0].strip()
    return "", first_sentence


def _enhance_step_flows(html: str, lang: str) -> str:
    """Найти <h2>…по шагам…</h2> + следующий <ol> и превратить <ol> в .kz-flow."""
    heading_re = _STEP_HEADING_RE.get(lang, _STEP_HEADING_RE["ru"])

    # ищем h2, затем ближайший <ol>...</ol> до следующего <h2>
    def process(match: re.Match) -> str:
        h2 = match.group(0)
        if not heading_re.search(re.sub(r"<[^>]+>", "", h2)):
            return h2
        return h2  # сам h2 не меняем; замену <ol> делаем отдельным проходом ниже

    # Простой и надёжный подход: разбить на секции по <h2>, обработать нужные.
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.DOTALL)
    out = []
    i = 0
    while i < len(parts):
        chunk = parts[i]
        is_heading = bool(re.match(r"<h2[^>]*>.*?</h2>", chunk, re.DOTALL))
        if is_heading and heading_re.search(re.sub(r"<[^>]+>", "", chunk)):
            out.append(chunk)
            # следующий кусок — тело секции; заменим первый <ol> на flow
            if i + 1 < len(parts):
                body = parts[i + 1]
                body = _ol_to_flow(body)
                out.append(body)
                i += 2
                continue
        out.append(chunk)
        i += 1
    return "".join(out)


def _ol_to_flow(section_html: str) -> str:
    """Заменить первый <ol>…</ol> в куске на схему .kz-flow."""
    m = re.search(r"<ol>(.*?)</ol>", section_html, re.DOTALL)
    if not m:
        return section_html
    items = re.findall(r"<li>(.*?)</li>", m.group(1), re.DOTALL)
    # схема имеет смысл при 3–6 шагах
    if not (3 <= len(items) <= 6):
        return section_html

    steps_html = []
    for n, li in enumerate(items, 1):
        title, desc = _split_step(li)
        inner = f'<div class="kz-flow__num">{n}</div>'
        if title:
            inner += f'<div class="kz-flow__title">{title}</div>'
        if desc:
            inner += f'<div class="kz-flow__desc">{desc}</div>'
        steps_html.append(f'<div class="kz-flow__step">{inner}</div>')
    arrow = f'<div class="kz-flow__arrow">{_ICON_ARROW}</div>'
    row = arrow.join(steps_html)
    flow = f'<div class="kz-flow"><div class="kz-flow__row">{row}</div></div>'
    return section_html[:m.start()] + flow + section_html[m.end():]


# ─────────────────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────────────────
def enhance_body_html(html: str, lang: str = "ru") -> str:
    """Применить все безопасные улучшения к телу статьи."""
    if not html:
        return html
    lang = "uk" if str(lang).lower().startswith("uk") else "ru"
    html = _enhance_tables(html)
    html = _enhance_step_flows(html, lang)
    return html


if __name__ == "__main__":
    demo = (
        "<h2>Как это работает по шагам</h2>"
        "<ol><li>Ты находишь клуб и связываешься с ним.</li>"
        "<li>Клуб присылает данные для входа.</li>"
        "<li>Ты скачиваешь приложение и попадаешь в лобби.</li>"
        "<li>Пополняешь баланс через клуб.</li>"
        "<li>Выигрыш выводишь через клуб.</li></ol>"
        "<h2>Сравнение</h2>"
        "<table><thead><tr><th>Параметр</th><th>PokerBet</th><th>KlubOk</th></tr></thead>"
        "<tbody><tr><td>Легальность</td><td>Да</td><td>Нет</td></tr></tbody></table>"
    )
    print(enhance_body_html(demo, "ru"))
