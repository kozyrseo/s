"""
KOZYR — Перевод сгенерированных статей на другие языки страны.

Как работает:
1. Читает готовую русскую статью из _pending/{slug}/ (article.json — весь JSON,
   который вернул Claude в generate_article: title, meta, h1, tags, sections,
   faq, russian_preview и т.д.).
2. Отправляет Claude с промптом «переведи, сохрани структуру, natural».
3. Получает JSON той же структуры на целевом языке.
4. Пересчитывает slug (если нужен свой url_slug — обычно тот же чтобы hreflang
   работал корректно).
5. Сохраняет в _pending_{target_lang}/{slug}/ те же 3 файла:
   - meta.json (со ссылкой translation_of на первичный slug)
   - body.md   (переведённый markdown-body)
   - preview.md (превью для GitHub-ссылки в TG)
6. Возвращает пути к обеим версиям — оркестратор их использует
   в едином TG-превью.

Промпт перевода:
- Строгое сохранение структуры H2/H3/списков/таблиц/faq
- Естественный язык, не машинный подстрочник
- Спец-термины покера — используем принятые в целевом языке
- Внутренние ссылки (/ua/rooms/pokerbet/) остаются как есть — они и так
  русско-универсальные пути. Слуги статей — тоже те же.
- Числа, даты, названия румов/клубов — не переводим
- Модальные слова, идиомы — адаптируем, не калькируем

Использование:
    from translator import translate_article
    translated = translate_article(
        source_pending_dir=Path("_pending/kak-vybrat-rum"),
        source_lang="ru",
        target_lang="uk",
    )
    # translated = {"slug": "...", "target_dir": Path("...")}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from lang_config import get_cfg


MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 16000

# Инструкции для переводчика — общие для всех целевых языков.
# Специфика конкретного языка (диалект, тональность, локальные термины)
# добавляется через ADDITIONAL_INSTRUCTIONS_BY_LANG ниже.

TRANSLATOR_SYSTEM_PROMPT = """Ты — переводчик статей блога KOZYR (kozyr.ua),
специализирующегося на рейкбек-сделках, покерных румах и клубах для игроков
СНГ и Украины.

Твоя задача — перевести JSON-статью с {source_lang} на {target_lang}.

ЖЁСТКИЕ ТРЕБОВАНИЯ:
1. Сохрани JSON-структуру 1-в-1. Все ключи остаются на английском (title,
   meta_title, h1_title, sections, faq, etc). Переводится ТОЛЬКО значение.
2. Массивы sections/faq/tags: длина не меняется. Порядок не меняется.
3. Внутри sections[i] сохраняй heading, subheadings, list_items, table_rows,
   paragraphs — переводя каждое поле по отдельности.
4. Внутренние URL (`/ua/`, `/ua/rooms/pokerbet/` и т.д.) НЕ переводить.
5. Названия брендов (PokerBet, KlubOk, PokerStars, GGPoker, ClubGG) — как есть.
6. Числа (цены, проценты, суммы), даты, суммы валют — как есть.
7. Английские термины (rakeback, MTT, cash game, EV) — если в целевом языке
   принят русский/украинский аналог, используй его; иначе оставляй.
8. `russian_preview` — переведи title_ru → title (на целевом языке),
   summary_ru → summary (тоже переведи), h2_translations — оставь
   структуру, но каждое ru → переведи на целевой.
9. `translation_of` — не задавай, оркестратор поставит сам.

ТОНАЛЬНОСТЬ:
- Естественный текст на целевом языке, не машинный.
- Если исходник обращается на «ты» — сохрани обращение (в украинском тоже «ти»).
- Идиомы адаптируй: «два по цене одного» → «два за ціну одного», не «два в ціні одного».
- Спец-термины покера пиши как принято в целевом языке (в украинском покерном
   комьюнити — «рейкбек», «раздача», «блайнд», «фолд», часто транслитерация).

ФОРМАТ ОТВЕТА:
Только валидный JSON. Без code fences, без преамбулы, без комментариев.
Тот же shape что на входе, все значения переведены."""


# Дополнительные инструкции по конкретной паре языков (можно расширять)
ADDITIONAL_INSTRUCTIONS_BY_LANG_PAIR = {
    ("ru", "uk"): """
СПЕЦИФИКА ru→uk:
- Обращение — на «ти» (украинское тыканье естественно).
- Кальки избегай: «рейтинг ромов» → «рейтинг румів», «клуб» → «клуб».
- «Покерный рум» → «покерний рум» (не «зал»).
- «Вывод денег» → «виведення коштів» / «вивід коштів».
- «Гривна» → «гривня» / «грн».
- «Заключение» / «Итог» → «Висновок» / «Підсумок».
- Названия юридических документов адаптируй (Terms → Умови користування).
- Не путай: «долі» (доли, части) vs «долі» (судьбы) — контекст.
""",
}


def load_article_json(pending_dir: Path) -> dict:
    """Читает автосохранённый article.json из _pending/{slug}/.
    Если файла нет — восстанавливает из meta.json + body.md (fallback)."""
    article_path = pending_dir / "article.json"
    if article_path.exists():
        return json.loads(article_path.read_text(encoding="utf-8"))
    # Fallback для старых pending, где article.json не сохранялся отдельно.
    meta = json.loads((pending_dir / "meta.json").read_text(encoding="utf-8"))
    body_md = (pending_dir / "body.md").read_text(encoding="utf-8")
    # Мы не можем полностью восстановить структуру sections из body.md
    # без парсинга, поэтому просим Claude перевести body.md как единый markdown.
    return {
        "_fallback_mode": True,
        "title": meta.get("h1_title", ""),
        "meta_title": meta.get("meta_title", ""),
        "meta_description": meta.get("meta_description", ""),
        "h1_title": meta.get("h1_title", ""),
        "tags": meta.get("tags", []),
        "image_prompt": meta.get("image_prompt", ""),
        "russian_preview": meta.get("russian_preview", {}),
        "markdown_body": body_md,
    }


def translate_article_json(article: dict, source_lang: str, target_lang: str) -> dict:
    """Отправляет Claude, получает переведённый JSON, парсит и возвращает."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = TRANSLATOR_SYSTEM_PROMPT.format(
        source_lang=_lang_name(source_lang),
        target_lang=_lang_name(target_lang),
    )
    extra = ADDITIONAL_INSTRUCTIONS_BY_LANG_PAIR.get((source_lang, target_lang), "")
    if extra:
        system_prompt += "\n" + extra

    # article.json может быть большой (2000+ слов). Отправляем как есть.
    article_for_translation = {k: v for k, v in article.items()
                                if not k.startswith("_")}
    user_msg = "Переведи это JSON:\n\n" + json.dumps(
        article_for_translation, ensure_ascii=False, indent=2
    )

    print(f"🌐 Перевод {source_lang} → {target_lang} (Claude {MODEL})...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
    raw = "\n".join(text_parts).strip()

    # Снимаем возможные code fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    # Ищем JSON-объект
    first = raw.find("{")
    last = raw.rfind("}")
    if first == -1 or last == -1:
        raise ValueError(
            f"Claude не вернул JSON. Первые 500 символов ответа:\n{raw[:500]}"
        )
    try:
        translated = json.loads(raw[first:last + 1])
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Невалидный JSON от Claude: {e}\n"
            f"Первые 500 символов:\n{raw[first:first + 500]}"
        )

    # Sanity check: базовые ключи должны быть на месте.
    # ВАЖНО: поля "title" в структуре из generate.py НЕТ (есть h1_title).
    # Раньше валидатор требовал "title" и падал на каждом переводе.
    for required in ("h1_title", "meta_title", "meta_description"):
        if required not in translated or not translated[required]:
            raise ValueError(f"В переводе отсутствует поле {required!r}")

    return translated


def _lang_name(code: str) -> str:
    """Отображаемое имя языка для промпта."""
    return {
        "ru": "русского",
        "uk": "украинский",
        "pl": "польский",
        "kk": "казахский",
        "en": "английский",
    }.get(code, code)


def render_body_markdown(article: dict) -> str:
    """
    Собирает body.md из переведённой article.json. Логика упрощена,
    т.к. структура sections/faq та же что и у оригинала. При fallback-режиме
    (markdown_body уже готов) — возвращает как есть.
    """
    if article.get("markdown_body"):
        return article["markdown_body"]

    lines = []
    sections = article.get("sections", [])
    for s in sections:
        heading = s.get("heading", "").strip()
        if heading:
            lines.append(f"## {heading}\n")

        # Sub-sections, list_items, paragraphs — общий подход как в generate.py
        for para in s.get("paragraphs", []):
            if para:
                lines.append(para.strip() + "\n")

        for sub in s.get("subheadings", []):
            sub_heading = sub.get("heading", "").strip()
            if sub_heading:
                lines.append(f"### {sub_heading}\n")
            for para in sub.get("paragraphs", []):
                if para:
                    lines.append(para.strip() + "\n")
            for item in sub.get("list_items", []):
                lines.append(f"- {item}")
            if sub.get("list_items"):
                lines.append("")

        for item in s.get("list_items", []):
            lines.append(f"- {item}")
        if s.get("list_items"):
            lines.append("")

    # FAQ в конце — если есть
    faq = article.get("faq", [])
    if faq:
        lines.append("## FAQ\n")
        for q in faq:
            q_text = q.get("question", "").strip()
            a_text = q.get("answer", "").strip()
            if q_text:
                lines.append(f"**{q_text}**\n")
            if a_text:
                lines.append(a_text + "\n")

    return "\n".join(lines).strip() + "\n"


def render_preview_markdown(article: dict) -> str:
    """
    Мини-превью для GitHub-ссылки в TG (короткая версия статьи).
    """
    lines = [f"# {article.get('h1_title', article.get('title', ''))}", ""]
    if article.get("meta_description"):
        lines.append(f"> {article['meta_description']}")
        lines.append("")
    sections = article.get("sections", [])
    for s in sections[:5]:
        heading = s.get("heading", "").strip()
        if heading:
            lines.append(f"## {heading}")
        paras = s.get("paragraphs", [])
        if paras:
            lines.append(paras[0][:400] + ("..." if len(paras[0]) > 400 else ""))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# ==== Основная функция ====

def translate_article(
    source_pending_dir: Path,
    source_lang: str,
    target_lang: str,
    target_pending_dir: Path | None = None,
    slug: str | None = None,
) -> dict:
    """
    Переводит статью из source_pending_dir → создаёт target_pending_dir/{slug}/.

    Аргументы:
      source_pending_dir: путь к папке исходной статьи (например _pending/foo).
      source_lang, target_lang: коды языков.
      target_pending_dir: КУДА положить перевод. По умолчанию — pending_dir
        целевого lang_config (например _pending_uk/foo).
      slug: обычно равен source slug (для корректного hreflang).
        По умолчанию берётся из имени source_pending_dir.

    Возвращает:
      {
        "target_dir": Path,
        "slug": str,
        "article": dict,     # переведённый article.json
        "source_slug": str,
      }

    Файлы, создаваемые в target_dir:
      - meta.json (с translation_of = {source_lang: source_slug})
      - body.md
      - preview.md
      - article.json (полная версия — понадобится если будут переводить дальше)
      - hero.webp — НЕ копируем, будет использована та же из source (см. publish.py)
    """
    if not source_pending_dir.exists():
        raise FileNotFoundError(f"Нет исходной папки: {source_pending_dir}")

    source_slug = slug or source_pending_dir.name
    target_slug = source_slug  # общий slug — чтобы hreflang работал

    # Куда сохранить
    target_lang_cfg = get_cfg(target_lang)
    if target_pending_dir is None:
        target_pending_dir = target_lang_cfg["pending_dir"] / target_slug
    target_pending_dir.mkdir(parents=True, exist_ok=True)

    # 1. Читаем оригинал
    source_article = load_article_json(source_pending_dir)
    source_meta = json.loads(
        (source_pending_dir / "meta.json").read_text(encoding="utf-8")
    )

    # 2. Переводим через Claude
    translated = translate_article_json(source_article, source_lang, target_lang)

    # 3. Формируем meta.json для целевого языка
    now = datetime.now(timezone.utc).isoformat()
    target_meta = {
        "slug": target_slug,
        "url_slug": target_slug,
        "lang": target_lang,
        "meta_title": translated.get("meta_title", ""),
        "meta_description": translated.get("meta_description", ""),
        "h1_title": translated.get("h1_title", ""),
        "tags": translated.get("tags", source_meta.get("tags", [])),
        "image_prompt": source_meta.get("image_prompt", ""),  # тот же промпт
        "has_hero_image": source_meta.get("has_hero_image", False),
        "word_count": _word_count(translated),
        "russian_preview": translated.get("russian_preview", {}),
        # Ссылка на оригинал — используется для hreflang и переключателя
        "translation_of": {source_lang: source_slug},
        # Сохраняем topic для audit
        "topic_row_data": source_meta.get("topic_row_data", {}),
        "source_row": source_meta.get("source_row"),
        "generated_at": now,
        "translated_from": source_lang,
        "translator_model": MODEL,
    }

    # 4. Пишем файлы
    (target_pending_dir / "meta.json").write_text(
        json.dumps(target_meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target_pending_dir / "article.json").write_text(
        json.dumps(translated, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target_pending_dir / "body.md").write_text(
        render_body_markdown(translated), encoding="utf-8"
    )
    (target_pending_dir / "preview.md").write_text(
        render_preview_markdown(translated), encoding="utf-8"
    )

    # 5. Обновляем translation_of у ИСТОЧНИКА, чтобы он тоже знал про перевод
    source_translation_of = source_meta.get("translation_of") or {}
    if not isinstance(source_translation_of, dict):
        source_translation_of = {}
    source_translation_of[target_lang] = target_slug
    source_meta["translation_of"] = source_translation_of
    (source_pending_dir / "meta.json").write_text(
        json.dumps(source_meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"✅ Перевод {source_lang}→{target_lang} готов: {target_pending_dir}")
    return {
        "target_dir": target_pending_dir,
        "slug": target_slug,
        "article": translated,
        "source_slug": source_slug,
        "meta": target_meta,
    }


def _word_count(article: dict) -> int:
    """Считает слова в переведённой статье (для quality-отчёта в TG)."""
    parts = [article.get("h1_title", ""), article.get("meta_description", "")]
    for s in article.get("sections", []):
        parts.append(s.get("heading", ""))
        parts.extend(s.get("paragraphs", []))
        for sub in s.get("subheadings", []):
            parts.append(sub.get("heading", ""))
            parts.extend(sub.get("paragraphs", []))
            parts.extend(sub.get("list_items", []))
        parts.extend(s.get("list_items", []))
    for q in article.get("faq", []):
        parts.append(q.get("question", ""))
        parts.append(q.get("answer", ""))
    return sum(len(str(p).split()) for p in parts if p)


# ==== CLI ====

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перевод статьи из _pending/ на другой язык"
    )
    parser.add_argument("--source-slug", required=True,
                        help="Слуг папки в исходной _pending/ (например 'foo-bar')")
    parser.add_argument("--source-lang", default="ru",
                        help="Код исходного языка. По умолчанию 'ru'")
    parser.add_argument("--target-lang", required=True,
                        help="Код целевого языка (например 'uk')")
    args = parser.parse_args()

    source_cfg = get_cfg(args.source_lang)
    source_dir = source_cfg["pending_dir"] / args.source_slug

    try:
        result = translate_article(
            source_pending_dir=source_dir,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
        )
        print(f"\n📊 Перевод: {result['target_dir']}")
        print(f"   Слов: {_word_count(result['article'])}")
        return 0
    except Exception as e:
        print(f"\n❌ Перевод упал: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
