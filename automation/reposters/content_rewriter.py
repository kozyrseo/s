"""
reposters/content_rewriter.py — рерайт статьи под внешнюю площадку.

Берёт ГОТОВУЮ статью (HTML с сайта), извлекает основной текст, и через Claude
делает КОРОТКИЙ уникальный рерайт под площадку (не копипаст — Google не увидит
дубль). Дёшево: рерайт короче полной генерации.

Результат — HTML-фрагмент со ссылкой на оригинал внутри («читать полностью на
KOZYR»). Разные анкоры каждый раз (Claude варьирует). Язык рерайта = язык
исходной статьи (ru/uk).
"""
from __future__ import annotations
import os
import re
import json

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 4000  # рерайт короче генерации — экономия

_LANG_NAME = {"ru": "русском", "uk": "украинском"}


def extract_article_text(html: str) -> tuple[str, str]:
    """Из HTML статьи извлекает (заголовок, основной_текст).
    Убирает навигацию, карточки партнёров, скрипты — только тело статьи.
    """
    # заголовок
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    title = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    # тело статьи: пытаемся взять <article>, иначе <main>
    body_m = re.search(r"<article[^>]*>(.*?)</article>", html, re.S) or \
             re.search(r"<main[^>]*>(.*?)</main>", html, re.S)
    body = body_m.group(1) if body_m else html

    # убираем скрипты, стили, партнёрские карточки/виджеты, формы
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r"<[^>]*data-partner[^>]*>.*?</[^>]+>", "", body, flags=re.S)
    body = re.sub(r"<aside.*?</aside>", "", body, flags=re.S)
    body = re.sub(r"<nav.*?</nav>", "", body, flags=re.S)

    # чистый текст (для передачи Claude)
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return title, text


_REWRITE_SYSTEM = """Ты редактор, который делает УНИКАЛЬНУЮ короткую версию
статьи для публикации на внешней площадке (тизер-репост со ссылкой на оригинал).

ЗАДАЧА: на основе исходной статьи напиши НОВЫЙ короткий текст на {lang_name}
языке — НЕ копию, а самостоятельный пересказ ключевой мысли (250-450 слов).

ТРЕБОВАНИЯ:
- Уникальный текст: другой заголовок, другая структура, свои формулировки.
  Это НЕ рерайт предложений, а новый текст на ту же тему.
- Естественная ссылка на полную статью: вставь ОДНУ ссылку на оригинал с
  органичным анкором (не «читать тут», а по смыслу: «полный разбор рейкбека»,
  «как выбрать рум — в статье KOZYR» и т.п.). Анкор каждый раз разный.
- Тон живой, экспертный, без воды. Польза для читателя.
- НЕ выдумывай фактов, которых нет в оригинале.
- Формат ответа — СТРОГО JSON:
  {{"title": "заголовок для площадки", "html": "<p>...</p><h3>...</h3>..."}}
  В html — простые теги: p, h3, h4, ul/li, b, i, a. Ссылка на оригинал — тег a
  с href на переданный URL. Без markdown, без текста вокруг JSON."""


def rewrite_for_platform(article_html: str, canonical_url: str,
                         lang: str, platform_name: str) -> dict:
    """Рерайт статьи под площадку. Возвращает {title, html}.

    article_html  — HTML исходной статьи с сайта
    canonical_url — URL оригинала (для ссылки внутри)
    lang          — ru/uk (язык рерайта = язык статьи)
    platform_name — имя площадки (для контекста, напр. Telegraph)
    """
    if OpenAI is None:
        raise RuntimeError("openai SDK не установлен")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не задан")

    title, text = extract_article_text(article_html)
    lang_name = _LANG_NAME.get(lang, lang)
    system = _REWRITE_SYSTEM.format(lang_name=lang_name)
    user = (
        f"Площадка: {platform_name}\n"
        f"Ссылка на оригинал (вставь в текст): {canonical_url}\n"
        f"Исходный заголовок: {title}\n\n"
        f"Исходный текст статьи:\n{text[:6000]}"
    )

    client = OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)
    resp = client.chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in raw else raw
    first, last = raw.find("{"), raw.rfind("}")
    result = json.loads(raw[first:last + 1])

    # Гарантия: ссылка на оригинал есть в html (если Claude забыл — добавим)
    if canonical_url not in result.get("html", ""):
        anchor = "Полная статья на KOZYR" if lang == "ru" else "Повна стаття на KOZYR"
        result["html"] = result.get("html", "") + \
            f'<p><a href="{canonical_url}">{anchor}</a></p>'

    return result
