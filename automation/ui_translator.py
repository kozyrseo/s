"""
ui_translator.py — перевод UI-интерфейса для новой страны, с самопроверкой.

Часть мультигео-генератора (Этап 3). Переводит 49 UI-фраз (кнопки, меню,
футер) на язык новой страны через Claude (OpenRouter), затем:
  1. САМОПРОВЕРКА: второй проход Claude проверяет свой перевод на ошибки
  2. ОБРАТНЫЙ ПЕРЕВОД: переводит результат обратно на русский для сверки смысла
  3. ПОМЕТКА: находит фразы, где обратный перевод разошёлся с оригиналом,
     помечает как «требует внимания»

Оператор в боте видит только помеченные фразы + обратный перевод, и кнопками
[Оставить]/[Переперевести] управляет — сам НЕ правит (язык может быть незнаком).
Переперевод делает бот (retranslate_one).

Использует тот же паттерн, что translator.py: OpenAI SDK + OpenRouter + Claude.
"""
from __future__ import annotations
import os
import json

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 8000

# Языковые имена для промптов
_LANG_NAMES = {
    "ru": "русский", "uk": "украинский", "pl": "польский",
    "kk": "казахский", "en": "английский", "de": "немецкий",
    "cs": "чешский", "ro": "румынский", "es": "испанский",
}


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get(code, code)


def _client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai SDK не установлен (pip install openai)")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не задан")
    return OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)


def _call_claude(system: str, user: str) -> str:
    """Один вызов Claude через OpenRouter. Возвращает текст ответа."""
    resp = _client().chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _extract_json(raw: str):
    """Достаёт JSON-объект из ответа (снимает code fences)."""
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    first, last = raw.find("{"), raw.rfind("}")
    if first == -1 or last == -1:
        raise ValueError(f"Claude не вернул JSON:\n{raw[:400]}")
    return json.loads(raw[first:last + 1])


# ─────────────────────────────────────────────────────────────────────────
# Шаг 1: перевод словаря UI-фраз
# ─────────────────────────────────────────────────────────────────────────
_TRANSLATE_SYSTEM = """Ты профессиональный переводчик интерфейсов сайтов.
Переводишь UI-строки (кнопки, пункты меню, подписи) с русского на {target}.

ПРАВИЛА:
- Перевод должен звучать естественно для носителя {target}, как в настоящих сайтах.
- Короткие фразы (кнопки) — короткие и в том же стиле.
- Устоявшиеся термины (Rakeback, VIP, FAQ, HUD) оставляй как принято в {target}-сегменте.
- НЕ добавляй пояснений, НЕ меняй смысл.
- Верни СТРОГО JSON: тот же набор ключей, значения — переводы. Без markdown, без текста вокруг."""


def translate_ui(texts_ru: dict, target_lang: str) -> dict:
    """Переводит словарь {ключ: русская_фраза} на target_lang.
    Возвращает {ключ: переведённая_фраза}.
    """
    system = _TRANSLATE_SYSTEM.format(target=_lang_name(target_lang))
    user = "Переведи значения этого JSON, ключи оставь как есть:\n\n" + \
        json.dumps(texts_ru, ensure_ascii=False, indent=2)
    return _extract_json(_call_claude(system, user))


# ─────────────────────────────────────────────────────────────────────────
# Шаг 2: самопроверка перевода
# ─────────────────────────────────────────────────────────────────────────
_REVIEW_SYSTEM = """Ты редактор-корректор переводов интерфейсов.
Тебе дают оригинал (русский) и перевод на {target}. Найди проблемы:
- смысловые ошибки (перевод значит не то)
- неестественные/машинные формулировки
- неверные термины

Верни СТРОГО JSON вида:
{{"issues": [{{"key": "ключ", "reason": "кратко в чём проблема"}}]}}
Если проблем нет — {{"issues": []}}. Только реальные проблемы, не придирки."""


def self_review(texts_ru: dict, translated: dict, target_lang: str) -> list[dict]:
    """Claude проверяет свой перевод. Возвращает список проблемных {key, reason}."""
    system = _REVIEW_SYSTEM.format(target=_lang_name(target_lang))
    pairs = {k: {"ru": texts_ru.get(k), target_lang: translated.get(k)}
             for k in texts_ru}
    user = "Проверь переводы:\n\n" + json.dumps(pairs, ensure_ascii=False, indent=2)
    try:
        result = _extract_json(_call_claude(system, user))
        return result.get("issues", [])
    except Exception:
        return []  # самопроверка не критична — если упала, просто нет авто-issues


# ─────────────────────────────────────────────────────────────────────────
# Шаг 3: обратный перевод на русский (для сверки смысла оператором)
# ─────────────────────────────────────────────────────────────────────────
_BACK_SYSTEM = """Ты переводчик. Переведи UI-строки с {source} обратно на русский —
буквально, чтобы можно было сверить смысл. Верни СТРОГО JSON: те же ключи,
значения — русский обратный перевод. Без markdown."""


def back_translate(translated: dict, source_lang: str) -> dict:
    """Переводит translated (на source_lang) обратно на русский."""
    system = _BACK_SYSTEM.format(source=_lang_name(source_lang))
    user = "Переведи обратно на русский:\n\n" + \
        json.dumps(translated, ensure_ascii=False, indent=2)
    try:
        return _extract_json(_call_claude(system, user))
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────
# Оркестратор: полный цикл перевод + проверка
# ─────────────────────────────────────────────────────────────────────────
def _similar(a: str, b: str) -> bool:
    """Грубая проверка: обратный перевод похож на оригинал по смыслу?
    Нормализуем регистр/пробелы/пунктуацию и сравниваем."""
    import re
    norm = lambda s: re.sub(r"[^\w]+", " ", (s or "").lower()).strip()
    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    # пересечение слов (для коротких фраз)
    wa, wb = set(na.split()), set(nb.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(len(wa), len(wb))
    return overlap >= 0.5


def translate_ui_with_checks(texts_ru: dict, target_lang: str) -> dict:
    """Полный цикл. Возвращает структуру для показа оператору в боте:
    {
      "translated":   {key: перевод},
      "back":         {key: обратный перевод на русский},
      "flagged":      [{key, ru, translated, back, reason}],  # требуют внимания
      "ok_count":     N,   # переведено без замечаний
    }
    flagged = объединение (самопроверка Claude) ∪ (обратный перевод разошёлся).
    """
    translated = translate_ui(texts_ru, target_lang)
    issues = self_review(texts_ru, translated, target_lang)
    back = back_translate(translated, target_lang)

    issue_keys = {i["key"]: i.get("reason", "") for i in issues if "key" in i}

    flagged = []
    for k, ru_text in texts_ru.items():
        back_text = back.get(k, "")
        reason = None
        if k in issue_keys:
            reason = issue_keys[k] or "самопроверка отметила"
        elif back_text and not _similar(ru_text, back_text):
            reason = "обратный перевод разошёлся по смыслу"
        if reason:
            flagged.append({
                "key": k,
                "ru": ru_text,
                "translated": translated.get(k, ""),
                "back": back_text,
                "reason": reason,
            })

    return {
        "translated": translated,
        "back": back,
        "flagged": flagged,
        "ok_count": len(texts_ru) - len(flagged),
    }


def retranslate_one(key: str, ru_text: str, target_lang: str,
                    previous: str = "") -> dict:
    """Переперевод одной фразы (кнопка [Переперевести] в боте).
    Возвращает {translated, back}. previous — прошлый вариант (чтобы дать другой).
    """
    system = _TRANSLATE_SYSTEM.format(target=_lang_name(target_lang))
    hint = ""
    if previous:
        hint = (f"\n\nПрошлый вариант был: {previous!r} — он показался неудачным. "
                f"Дай ДРУГОЙ, более естественный вариант.")
    user = (f"Переведи одну UI-фразу на {_lang_name(target_lang)}. "
            f"Верни СТРОГО JSON {{\"{key}\": \"перевод\"}}.{hint}\n\n"
            f"Фраза: {ru_text!r}")
    translated = _extract_json(_call_claude(system, user))
    new_text = translated.get(key, "")
    back = back_translate({key: new_text}, target_lang)
    return {"translated": new_text, "back": back.get(key, "")}
