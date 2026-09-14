"""
country_prompt_builder.py — генерация system_prompt для новой страны +
детектор легальных рисков (мультигео, Этап 3).

Промпт генерации статей содержит секции ДВУХ типов:
  • УНИВЕРСАЛЬНЫЕ (стиль, структура, SEO, формат) — переносятся из эталонного
    промпта как есть: правила хорошей статьи не зависят от страны.
  • ЗАВИСЯЩИЕ ОТ СТРАНЫ (партнёры, легальный контекст, ответственная игра,
    гео-таргетинг) — генерируются заново под страну.

Партнёрская секция строится программно из партнёров страны (пути /xx/, валюта
рынка, только из markets). Легальный контекст адаптирует Claude, затем проходит
УСИЛЕННУЮ ПРОВЕРКУ:
  1. самоаудит (Claude проверяет свой легальный текст)
  2. детектор юр-рисков (ищет утверждения о законах/лицензиях/налогах)
  3. показ оператору с подсветкой рисков (в мастере)

⚠️ Claude не юрист. Адаптация законов — черновик, не юр-консультация.
Оператор ОБЯЗАН вычитать легальную секцию перед публикацией.
"""
from __future__ import annotations
import os
import re
import json
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

AUTOMATION_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = AUTOMATION_DIR / "prompts"
BASE_PROMPT = PROMPTS_DIR / "system_prompt.md"

MODEL = "anthropic/claude-opus-4.8"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 12000

# Секции промпта, ЗАВИСЯЩИЕ от страны (их генерируем заново).
# Остальные секции берём из эталона как есть.
COUNTRY_DEPENDENT_SECTIONS = {
    "ТЕКУЩИЕ ПАРТНЁРЫ",
    "ТОН В ОТНОШЕНИИ ПАРТНЁРОВ",
    "ОТВЕТСТВЕННАЯ ИГРА",
    "SEO — ГЕО-ТАРГЕТИНГ",
    "ЦЕЛЕВАЯ АУДИТОРИЯ",
}

# Юридически чувствительные термины — детектор рисков подсвечивает их наличие
# в легальной секции, чтобы оператор проверил КАЖДОЕ утверждение.
LEGAL_RISK_TERMS = [
    # регуляторы / лицензии
    "лиценз", "ліценз", "licen", "регулятор", "КРАИЛ", "КРАІЛ", "CGA",
    "Curaçao", "Кюрасао", "MGA", "Totalizator", "УКГЦ",
    # легальность
    "легальн", "легаль", "законн", "запрещ", "заборон", "разрешён", "дозвол",
    # налоги
    "налог", "податк", "tax", "удержан", "утриман",
    # юридические последствия
    "суд", "штраф", "ответственност", "відповідальн",
]


def _client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai SDK не установлен")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не задан")
    return OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)


def _call(system: str, user: str) -> str:
    resp = _client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


# ─────────────────────────────────────────────────────────────────────────
# Разбор эталонного промпта на секции
# ─────────────────────────────────────────────────────────────────────────
def _split_sections(text: str) -> list[tuple[str, str]]:
    """Разбивает промпт на секции по '## ЗАГОЛОВОК'.
    Возвращает [(заголовок, полный_блок_включая_заголовок), ...].
    Первый блок (до первого ##) идёт с заголовком ''.
    """
    parts = re.split(r"(?m)^(## .+)$", text)
    sections = []
    # parts[0] — преамбула до первого ##
    if parts[0].strip():
        sections.append(("", parts[0]))
    # дальше идут пары (заголовок, тело)
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append((header.replace("## ", "").strip(), parts[i] + body))
    return sections


def _is_country_dependent(header: str) -> bool:
    return any(key in header for key in COUNTRY_DEPENDENT_SECTIONS)


# ─────────────────────────────────────────────────────────────────────────
# Генерация партнёрской секции из данных страны
# ─────────────────────────────────────────────────────────────────────────
def build_partners_section(partners: list[dict], country_code: str) -> str:
    """Секция «ТЕКУЩИЕ ПАРТНЁРЫ» из партнёров страны.
    partners — список dict с id, name, type, url-путями, валютой рынка.
    Пути строятся под country_code (/pl/rooms/...).
    """
    lines = ["## ТЕКУЩИЕ ПАРТНЁРЫ (на которые можно ставить внутренние ссылки)", ""]
    if not partners:
        lines.append("_Партнёры для этой страны ещё не раскатаны._ Не выдумывай")
        lines.append("партнёров и не ставь внутренних ссылок на несуществующие страницы.")
        lines.append("")
        return "\n".join(lines)

    for p in partners:
        kind = "clubs" if p.get("type") == "club" else "rooms"
        path = f"/{country_code}/{kind}/{p['id']}/"
        cur = p.get("currency", "")
        desc = p.get("note") or p.get("description") or ""
        line = f"- **{p['name']}** (`{path}`)"
        if cur:
            line += f" — валюта {cur}"
        if desc:
            line += f". {desc}"
        lines.append(line)
    lines.append("")
    lines.append("Не выдумывай других партнёров. Ставь внутренние ссылки только")
    lines.append("на перечисленные выше страницы.")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Адаптация легального контекста через Claude
# ─────────────────────────────────────────────────────────────────────────
_LEGAL_ADAPT_SYSTEM = """Ты помогаешь адаптировать легальный контекст промпта
для генератора статей аффилиат-сайта покерной тематики под страну {country}.

Тебе дают исходную секцию (написана под Украину). Нужно адаптировать её под
{country}:
- УБЕРИ упоминания украинских законов/регуляторов (КРАИЛ и т.п.), если они
  неприменимы к {country}.
- Если НЕ уверен в конкретных законах {country} — используй НЕЙТРАЛЬНЫЕ,
  осторожные формулировки, НЕ выдумывай названия законов/регуляторов/налоговых
  правил. Лучше обтекаемо («местное законодательство»), чем неверный факт.
- Сохрани общий принцип: баланс честности и доверия, не запугивать игрока,
  ответственная игра 18+.
- НЕ добавляй юридических утверждений, за которые нельзя ручаться.

Верни ТОЛЬКО адаптированный markdown секции, без пояснений вокруг."""


def adapt_legal_section(section_text: str, country_name: str) -> str:
    """Адаптирует одну легальную/партнёрскую-тон секцию под страну через Claude."""
    system = _LEGAL_ADAPT_SYSTEM.format(country=country_name)
    return _call(system, f"Адаптируй эту секцию под {country_name}:\n\n{section_text}")


# ─────────────────────────────────────────────────────────────────────────
# УСИЛЕННАЯ ПРОВЕРКА легалки
# ─────────────────────────────────────────────────────────────────────────
_LEGAL_AUDIT_SYSTEM = """Ты юридический ревьюер текста для покерного аффилиат-сайта.
Проверь адаптированный под страну {country} легальный текст на РИСКИ:
- утверждения о законах/лицензиях/налогах {country}, которые могут быть НЕВЕРНЫ
- остатки чужого (украинского) правового контекста
- слишком уверенные юридические заявления

Верни СТРОГО JSON:
{{"risks": [{{"quote": "рискованная фраза", "why": "чем рискует"}}]}}
Если рисков нет — {{"risks": []}}."""


def legal_self_audit(text: str, country_name: str) -> list[dict]:
    """Claude проверяет легальный текст на юр-риски. Возвращает [{quote, why}]."""
    system = _LEGAL_AUDIT_SYSTEM.format(country=country_name)
    try:
        raw = _call(system, f"Проверь текст:\n\n{text}")
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in raw else raw
        first, last = raw.find("{"), raw.rfind("}")
        return json.loads(raw[first:last + 1]).get("risks", [])
    except Exception:
        return []


def detect_legal_risk_terms(text: str) -> list[dict]:
    """Детектор: находит юридически чувствительные термины в тексте.
    Возвращает [{term, context}] — для подсветки оператору.
    Ловит очевидное (осталось 'КРАИЛ' в польском тексте, утверждения о налогах).
    """
    found = []
    low = text.lower()
    seen_terms = set()
    for term in LEGAL_RISK_TERMS:
        idx = low.find(term.lower())
        if idx != -1 and term.lower() not in seen_terms:
            seen_terms.add(term.lower())
            # контекст ±50 символов
            start = max(0, idx - 50)
            end = min(len(text), idx + len(term) + 50)
            ctx = text[start:end].replace("\n", " ").strip()
            found.append({"term": term, "context": f"…{ctx}…"})
    return found


def review_legal_section(text: str, country_name: str) -> dict:
    """Полная проверка легальной секции. Для показа оператору в мастере:
    {
      "text":        адаптированный текст,
      "ai_risks":    [{quote, why}],       # самоаудит Claude
      "term_hits":   [{term, context}],    # детектор терминов
      "needs_review": bool,                # есть ли на что смотреть
    }
    """
    ai_risks = legal_self_audit(text, country_name)
    term_hits = detect_legal_risk_terms(text)
    return {
        "text": text,
        "ai_risks": ai_risks,
        "term_hits": term_hits,
        "needs_review": bool(ai_risks or term_hits),
    }


# ─────────────────────────────────────────────────────────────────────────
# Главная сборка промпта страны
# ─────────────────────────────────────────────────────────────────────────
def build_country_prompt(*, country_code: str, country_name: str,
                         partners: list[dict],
                         translate_to: str | None = None) -> dict:
    """Собирает system_prompt для страны.

    Возвращает:
    {
      "prompt":        полный текст промпта,
      "legal_review":  результат review_legal_section (для показа оператору),
      "partners_used": сколько партнёров вошло,
    }

    translate_to — код языка, на который перевести промпт (для primary-языка
    страны, если он не русский). Если None — оставляем на русском (Claude
    понимает инструкции на русском, пишет на нужном языке).
    """
    base = BASE_PROMPT.read_text(encoding="utf-8")
    sections = _split_sections(base)

    partners_section = build_partners_section(partners, country_code)

    # Собираем легальный текст для проверки (секции про тон/легалку)
    legal_chunks = []
    rebuilt = []
    for header, block in sections:
        if "ТЕКУЩИЕ ПАРТНЁРЫ" in header:
            rebuilt.append(partners_section)
        elif _is_country_dependent(header) and header:
            # адаптируем под страну
            adapted = adapt_legal_section(block, country_name)
            rebuilt.append(adapted)
            # легальные/тон секции — на проверку
            if any(k in header for k in ("ТОН", "ОТВЕТСТВЕННАЯ", "ПАРТНЁР")):
                legal_chunks.append(adapted)
        else:
            rebuilt.append(block)

    prompt = "".join(
        b if b.endswith("\n") else b + "\n" for b in rebuilt
    )

    # Усиленная проверка легалки (по объединённому легальному тексту)
    legal_review = review_legal_section("\n\n".join(legal_chunks), country_name)

    return {
        "prompt": prompt,
        "legal_review": legal_review,
        "partners_used": len(partners),
    }
