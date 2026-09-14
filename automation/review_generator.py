"""
review_generator.py — генератор отзывов партнёра под конкретную страну
(мультигео, Этап 3, Часть 5).

При раскатке партнёра на новую страну создаёт 3-5 реалистичных отзывов с:
  • местными именами (польские: «Piotr K.», казахские и т.д.)
  • местными реалиями (BLIK/Przelewy24 вместо ПриватБанк/Моно; злоты вместо гривен)
  • текстом на языке страны
  • смешанным verified (часть true, часть false — как в жизни, реалистичнее)

Отзывы дописываются в reviews.json (единый источник). Оператор проверяет
их в превью (обратный перевод) и решает, публиковать ли — отзывы влияют на
доверие/конверсию, это его ответственность.

⚠️ Сгенерированные отзывы — синтетические. Оператор подтверждает публикацию.
"""
from __future__ import annotations
import os
import re
import json
import random
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEWS_JSON = REPO_ROOT / "reviews.json"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 6000

_LANG_NAMES = {
    "ru": "русском", "uk": "украинском", "pl": "польском", "kk": "казахском",
    "en": "английском", "de": "немецком", "cs": "чешском", "ro": "румынском",
}


def _client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai SDK не установлен")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не задан")
    return OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)


def _extract_json(raw: str):
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in raw else raw
    first, last = raw.find("["), raw.rfind("]")
    if first == -1 or last == -1:
        # может быть объект с полем reviews
        f2, l2 = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[f2:l2 + 1])
        return obj.get("reviews", [])
    return json.loads(raw[first:last + 1])


_GEN_SYSTEM = """Ты генерируешь реалистичные отзывы игроков о покерном руме/клубе
для страны, язык отзывов — {lang_name}.

ТРЕБОВАНИЯ К РЕАЛИСТИЧНОСТИ:
- Имена — типичные для этой страны (не русские/украинские, если страна другая).
- Реалии — местные: локальные платёжные методы, местная валюта, местные банки.
  НЕ упоминай украинские Приват24/Monobank/гривны, если страна не Украина.
- Тон — живой, разный: кто-то краткий, кто-то подробный. Не рекламный.
- Упоминай конкретику: скорость вывода, лимиты (NL10-NL50), софт, поля.
- Длина 1-3 предложения.

Верни СТРОГО JSON-массив объектов:
[{{"author": "Имя Ф.", "rating": 5, "text": "текст отзыва", "verified": true}}]
rating 4-5 (в основном), изредка 3. Без markdown, только JSON."""


def generate_reviews(partner_name: str, partner_type: str, country_name: str,
                     lang: str, count: int = 4) -> list[dict]:
    """Генерирует `count` отзывов о партнёре на языке `lang` под страну.
    Возвращает список сырых отзывов [{author, rating, text, verified}].
    """
    lang_name = _LANG_NAMES.get(lang, lang)
    system = _GEN_SYSTEM.format(lang_name=lang_name)
    kind = "покерном клубе" if partner_type == "club" else "покерном руме"
    user = (f"Сгенерируй {count} отзывов о {kind} «{partner_name}» "
            f"для игроков страны «{country_name}» на {lang_name} языке. "
            f"Имена и реалии — местные для «{country_name}».")
    resp = _client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    reviews = _extract_json(raw)
    return reviews[:count]


def back_translate_reviews(reviews: list[dict], lang: str) -> list[str]:
    """Обратный перевод текстов отзывов на русский (для проверки оператором)."""
    if not reviews:
        return []
    lang_name = _LANG_NAMES.get(lang, lang)
    texts = [r.get("text", "") for r in reviews]
    system = (f"Переведи отзывы с {lang_name} на русский буквально, для сверки "
              f"смысла. Верни СТРОГО JSON-массив строк (только переводы).")
    try:
        resp = _client().chat.completions.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps(texts, ensure_ascii=False)}],
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _extract_json(raw)
    except Exception:
        return []


def _next_review_id(data: dict) -> int:
    """Следующий числовой суффикс для id отзыва."""
    mx = 0
    for r in data.get("reviews", []):
        m = re.search(r"(\d+)", str(r.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def add_reviews_to_json(partner_id: str, country_code: str, lang: str,
                        reviews: list[dict], *,
                        verified_ratio: float = 0.6,
                        reviews_path: Path | None = None) -> int:
    """Дописывает сгенерированные отзывы в reviews.json.

    verified_ratio — доля отзывов с verified:true (0.6 = ~60%). Смешанно,
    как в жизни (оператор выбрал: некоторые да, некоторые нет).
    Дата — распределяется на последние ~90 дней для реалистичности.

    Возвращает количество добавленных.
    """
    from datetime import date, timedelta
    path = reviews_path or REVIEWS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("reviews", [])
    next_id = _next_review_id(data)

    n_verified = int(round(len(reviews) * verified_ratio))
    flags = [True] * n_verified + [False] * (len(reviews) - n_verified)
    random.shuffle(flags)

    added = 0
    for i, r in enumerate(reviews):
        d = date.today() - timedelta(days=random.randint(3, 90))
        entry = {
            "id": f"r-{next_id + i}",
            "partner": partner_id,
            "rating": int(r.get("rating", 5)),
            "date": d.isoformat(),
            "verified": flags[i] if i < len(flags) else False,
            "country": country_code,
            "author": {lang: r.get("author", "—")},
            "text": {lang: r.get("text", "")},
            "_generated": True,   # пометка: синтетический отзыв
        }
        data["reviews"].append(entry)
        added += 1

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
