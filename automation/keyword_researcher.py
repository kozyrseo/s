"""
KOZYR — Модуль анализа ключевых запросов и автопополнения таблицы тем.

Что делает:
1. Читает Google Search Console → находит запросы с высокими impressions
   и низкими позициями (opportunities), т.е. по которым уже показываемся,
   но можно вырасти в топ.
2. Дополнительно (опционально) через web_search Claude собирает
   конкурентные запросы: смотрит топ‑выдачу конкурентов (PokerOff, PokerScout,
   PokerNews, покерные форумы), извлекает популярные темы.
3. Кластеризует запросы по темам через Claude (semantic clustering).
4. Дополняет Google Sheets новыми строками status=suggested (не queued —
   оператор смотрит и решает: queued/rejected/edit).
5. Отправляет отчёт в Telegram: сколько нашли новых запросов, ТОП‑10.

Запускается:
  - Локально:      python keyword_researcher.py --lang ru
  - GitHub Actions: cron раз в неделю (см. .github/workflows/research-keywords.yml)
  - По кнопке из TG-бота ("🔎 Найти новые темы")

Использует:
  - GOOGLE_SERVICE_ACCOUNT_JSON — тот же, что в generate.py
  - GOOGLE_SHEETS_ID              — та же таблица
  - GSC_SITE_URL                  — sc-domain:kozyr.ua или https://kozyr.ua/
  - ANTHROPIC_API_KEY             — для кластеризации + web_search
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gspread
from anthropic import Anthropic
from google.oauth2.service_account import Credentials

# Google Search Console — опционально: если модуль не установлен,
# GSC-часть пропускается и работает только режим web_search.
try:
    from googleapiclient.discovery import build as gapi_build
    HAS_GSC = True
except ImportError:
    HAS_GSC = False

from lang_config import get_cfg


# ==== Конфигурация ====

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8000

# GSC: сколько дней назад брать данные
GSC_LOOKBACK_DAYS = 90

# Порог: запрос попадает в кандидаты, если impressions >= N и позиция > M
GSC_MIN_IMPRESSIONS = 50
GSC_MIN_POSITION = 8.0   # если позиция ниже (число больше) — есть куда расти
GSC_MAX_POSITION = 30.0  # если ниже 30-й — уже без шансов, отбрасываем

# Сколько тем добавлять в таблицу за один прогон
MAX_NEW_TOPICS_PER_RUN = 8

# GitHub repo для ссылок в TG
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "kozyrseo/s")


# ==== Google Sheets ====

def get_sheet():
    """Тот же лист, что читает generate.py."""
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(sheet_id).sheet1


def get_existing_queries(sheet) -> set[str]:
    """Возвращает набор всех уже добавленных запросов, чтобы не дублировать."""
    try:
        records = sheet.get_all_records()
    except Exception as e:
        print(f"⚠️  Не удалось прочитать таблицу: {e}")
        return set()
    existing = set()
    for row in records:
        topic = str(row.get("topic", "")).strip().lower()
        pk = str(row.get("primary_keyword", "")).strip().lower()
        if topic:
            existing.add(topic)
        if pk:
            existing.add(pk)
    return existing


def append_topic_to_sheet(sheet, topic_data: dict, country: str = "ua",
                            langs: str = "") -> None:
    """Добавляет новую строку в таблицу со status=suggested.

    country: код страны из country_config (ua/pl/kz…).
    langs:   override языков через запятую. Пусто = все языки страны.
    lang:    legacy-поле (для обратной совместимости с одноязычными темами)."""
    headers = sheet.row_values(1)
    row: list[Any] = [""] * len(headers)

    def set_col(name: str, value: Any) -> None:
        if name in headers:
            row[headers.index(name)] = value

    set_col("status", "suggested")
    # v2 multilang: страна и языки
    set_col("country", country)
    set_col("langs", langs)
    # Legacy: заполняем lang первым языком страны для обратной совместимости
    try:
        from country_config import get_country
        primary_lang = get_country(country)["primary_language"]
        set_col("lang", primary_lang)
    except Exception:
        set_col("lang", "ru")

    set_col("topic", topic_data.get("topic", ""))
    set_col("primary_keyword", topic_data.get("primary_keyword", ""))
    set_col("secondary_keywords", topic_data.get("secondary_keywords", ""))
    set_col("intent", topic_data.get("intent", "informational"))
    set_col("target_page", topic_data.get("target_page", ""))
    set_col("notes", topic_data.get("notes", ""))
    # Служебные колонки — пригодятся оператору
    set_col("source", topic_data.get("source", "keyword_research"))
    set_col("evidence", topic_data.get("evidence", ""))

    sheet.append_row(row, value_input_option="USER_ENTERED")


# ==== Google Search Console ====

def get_gsc_service():
    """Инициализирует клиент Search Console API из service account."""
    if not HAS_GSC:
        return None
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/webmasters.readonly"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gapi_build("searchconsole", "v1", credentials=credentials)


def fetch_gsc_opportunities(site_url: str) -> list[dict]:
    """
    Достаёт из GSC запросы, по которым сайт уже показывается, но позиция
    достаточно низкая, чтобы был смысл писать про них статью и вырваться
    в топ-5.

    Стратегия:
      - impressions >= GSC_MIN_IMPRESSIONS  (есть спрос)
      - GSC_MIN_POSITION <= position <= GSC_MAX_POSITION  (место для роста)
      - CTR < 3%  (кликают редко — либо чужие домены забирают, либо мы не в топе)

    Возвращает список dict: {query, impressions, clicks, ctr, position}.
    """
    service = get_gsc_service()
    if service is None:
        print("ℹ️  google-api-python-client не установлен — пропускаю GSC")
        return []

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=GSC_LOOKBACK_DAYS)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "rowLimit": 500,
        "type": "web",
    }
    try:
        response = service.searchanalytics().query(
            siteUrl=site_url, body=body
        ).execute()
    except Exception as e:
        print(f"⚠️  GSC запрос упал: {type(e).__name__}: {e}")
        return []

    rows = response.get("rows", [])
    opportunities = []
    for row in rows:
        query = row["keys"][0]
        impressions = row.get("impressions", 0)
        clicks = row.get("clicks", 0)
        position = row.get("position", 99.0)
        ctr = (clicks / impressions) if impressions else 0.0

        if impressions < GSC_MIN_IMPRESSIONS:
            continue
        if not (GSC_MIN_POSITION <= position <= GSC_MAX_POSITION):
            continue
        if ctr > 0.05:
            # Уже неплохой CTR, статья не нужна
            continue

        opportunities.append({
            "query": query,
            "impressions": impressions,
            "clicks": clicks,
            "ctr": round(ctr, 4),
            "position": round(position, 1),
        })

    # Сортируем по impressions (сначала самое перспективное)
    opportunities.sort(key=lambda x: -x["impressions"])
    print(f"📊 GSC: нашёл {len(opportunities)} запросов-возможностей")
    return opportunities[:50]  # хватит 50 на кластеризацию


# ==== Кластеризация через Claude + Web Search ====

CLUSTER_SYSTEM_PROMPT = """Ты — SEO-стратег для сайта KOZYR (витрина рейкбек-сделок
в покере, домен kozyr.ua, аудитория — игроки СНГ/Украины).

Задача: получить список поисковых запросов и (опционально) свежий web_search
по покерной тематике и выдать 5-8 новых тем статей для блога.

Правила:
1. Одна тема = одна статья ~2000 слов, узкий SEO-фокус.
2. Не дублируй темы: смотри список EXISTING_TOPICS — новые темы должны
   раскрывать другой аспект.
3. primary_keyword — точная поисковая фраза (низкочастотник или средний),
   как её ищут в Google. Не «покер», а «рейкбек в pokerbet 2026».
4. target_page — куда вести читателя:
   - `/ua/`                     (каталог сделок)
   - `/ua/rooms/pokerbet/`      (обзор PokerBet)
   - `/ua/clubs/klubok/`        (обзор KlubOk)
5. intent:
   - `informational` — читатель хочет разобраться (что такое, как работает)
   - `commercial`    — читатель выбирает (сравнение, рейтинг, «где лучше»)
6. notes — 2-3 предложения оператору: что подчеркнуть, каких формулировок
   избегать, на какие цифры не ссылаться.
7. evidence — коротко: откуда угол темы (какие GSC-запросы её питают,
   какие тренды подсмотрены в web_search).

Отвечай строго JSON-массивом, без преамбулы, без code fences:

[
  {
    "topic": "...",
    "primary_keyword": "...",
    "secondary_keywords": "ключ1, ключ2, ключ3",
    "intent": "informational",
    "target_page": "/ua/",
    "notes": "...",
    "evidence": "GSC: 240 показов по 'рейкбек в pokerbet', позиция 12; тренд web: обсуждение на 2p2 в марте"
  },
  ...
]
"""


def cluster_and_generate_topics(
    gsc_opportunities: list[dict],
    existing_queries: set[str],
    do_web_search: bool = True,
) -> list[dict]:
    """
    Отправляет GSC-запросы в Claude, при желании подключает web_search,
    получает 5-8 готовых тем.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Формируем контекст: GSC-возможности + список уже существующих тем
    gsc_lines = []
    for opp in gsc_opportunities[:30]:  # ограничиваем размер промпта
        gsc_lines.append(
            f"- «{opp['query']}» — {opp['impressions']} показов, "
            f"позиция {opp['position']}, CTR {opp['ctr']}"
        )
    gsc_block = "\n".join(gsc_lines) if gsc_lines else "(GSC-данных нет — работай с web_search)"

    existing_block = "\n".join(f"- {q}" for q in sorted(existing_queries)[:40]) or "(пусто)"

    user_msg = f"""GSC OPPORTUNITIES (запросы, по которым сайт kozyr.ua уже показывается,
но позиция низкая — есть место для роста, если написать точечную статью):

{gsc_block}

---

EXISTING_TOPICS (уже есть в очереди или опубликованы — НЕ дублируй):

{existing_block}

---

Проверь через web_search:
1. Какие темы про рейкбек, PokerBet, приватные покерные клубы, ClubGG сейчас
   обсуждаются на 2p2, покерных форумах, Reddit r/poker, RakeTheRake, PokerNews
   за последние 3-6 месяцев.
2. Есть ли изменения в законодательстве Украины по онлайн-покеру.
3. Какие вопросы про рейкбек часто задают на украинских покерных форумах.

Затем выдай {MAX_NEW_TOPICS_PER_RUN} тем в JSON-формате из системного промпта.
Приоритет: темы, питаемые GSC-запросами (там уже есть спрос), с
дополнительной опорой на найденное в web_search.
"""

    tools = []
    if do_web_search:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    print(f"🤖 Кластеризация тем (Claude {MODEL}, web_search={'on' if do_web_search else 'off'})")
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=CLUSTER_SYSTEM_PROMPT,
        tools=tools,
        messages=[{"role": "user", "content": user_msg}],
    )

    text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
    raw = "\n".join(text_parts).strip()

    # Убираем возможные code fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    # Ищем JSON-массив
    first_bracket = raw.find("[")
    last_bracket = raw.rfind("]")
    if first_bracket == -1 or last_bracket == -1:
        print("⚠️  Claude не вернул JSON-массив. Первые 800 символов ответа:")
        print(raw[:800])
        return []

    try:
        topics = json.loads(raw[first_bracket:last_bracket + 1])
    except json.JSONDecodeError as e:
        print(f"⚠️  Невалидный JSON от Claude: {e}")
        print(raw[first_bracket:last_bracket + 1][:800])
        return []

    if not isinstance(topics, list):
        print(f"⚠️  Ожидался список, получено {type(topics).__name__}")
        return []

    # Простая валидация: минимально необходимые поля
    valid = []
    for t in topics:
        if not isinstance(t, dict):
            continue
        if not t.get("topic") or not t.get("primary_keyword"):
            continue
        # Помечаем источник — так оператор в таблице видит, что тема
        # предложена ботом, а не заведена вручную
        t.setdefault("source", "keyword_research")
        valid.append(t)

    print(f"✅ Claude предложил {len(valid)} тем")
    return valid


# ==== Telegram-отчёт ====

def send_telegram_report(new_topics: list[dict], gsc_stats: dict) -> None:
    """Присылает в чат оператора сводку по итогам keyword-research."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ℹ️  Telegram-креды не заданы, пропускаю отчёт")
        return

    lines = [
        "🔎 *Keyword research: новый прогон*",
        "",
        f"📊 GSC: обработано {gsc_stats.get('opportunities_found', 0)} запросов-возможностей "
        f"(impressions ≥ {GSC_MIN_IMPRESSIONS}, позиция {GSC_MIN_POSITION}-{GSC_MAX_POSITION}).",
        f"➕ Добавлено {len(new_topics)} новых тем в таблицу со `status=suggested`.",
        "",
        "*ТОП-5 тем на ревью:*",
    ]
    for i, t in enumerate(new_topics[:5], 1):
        title = t.get("topic", "")[:100]
        pk = t.get("primary_keyword", "")
        lines.append(f"{i}. *{escape_md(title)}*")
        lines.append(f"   🎯 `{escape_md(pk)}`")
    lines += [
        "",
        "Открой Google-таблицу и переведи нужные в `queued` — они пойдут в генерацию.",
        "",
        "Или из этого чата: /suggested — покажу список, кнопки:",
        "  ✅ В очередь → status=queued (генератор возьмёт)",
        "  ✏️ Правка → откроем редактор темы",
        "  ❌ Отклонить → status=rejected",
    ]
    text = "\n".join(lines)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Telegram-отчёт отправлен (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️  Telegram вернул {e.code}: {body[:400]}")
    except Exception as e:
        print(f"⚠️  Не удалось отправить в Telegram: {type(e).__name__}: {e}")


def escape_md(text: str) -> str:
    """MarkdownV1: экранируем ломающие маркеры."""
    if not text:
        return ""
    s = str(text)
    for ch in ("\\", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s


# ==== Основной пайплайн ====

def run(lang: str = "ru", skip_gsc: bool = False, skip_web: bool = False,
        dry_run: bool = False) -> dict:
    """
    Основной сценарий:
      1. Прочитать GSC (если доступен и не skip_gsc).
      2. Достать существующие темы из таблицы.
      3. Скормить всё Claude → получить N новых тем.
      4. Добавить их в таблицу (если не dry_run).
      5. Отправить отчёт в TG.

    Возвращает словарь со сводкой — удобно для GitHub Actions summary.
    """
    print(f"=== KOZYR keyword research (lang={lang}, dry_run={dry_run}) ===")

    # 1. GSC-возможности
    gsc_opportunities: list[dict] = []
    site_url = os.environ.get("GSC_SITE_URL")
    if skip_gsc:
        print("ℹ️  --skip-gsc: GSC не читаем")
    elif not site_url:
        print("ℹ️  GSC_SITE_URL не задан — GSC пропускаем")
    else:
        gsc_opportunities = fetch_gsc_opportunities(site_url)

    # 2. Существующие темы
    sheet = get_sheet()
    existing = get_existing_queries(sheet)
    print(f"📚 В таблице сейчас {len(existing)} уникальных тем/ключей")

    # 3. Claude → новые темы
    topics = cluster_and_generate_topics(
        gsc_opportunities=gsc_opportunities,
        existing_queries=existing,
        do_web_search=not skip_web,
    )

    # Отфильтровываем те, что уже есть
    fresh = []
    for t in topics:
        topic_norm = t.get("topic", "").strip().lower()
        pk_norm = t.get("primary_keyword", "").strip().lower()
        if topic_norm in existing or pk_norm in existing:
            print(f"⏭️  Пропускаю дубль: {t.get('topic')!r}")
            continue
        fresh.append(t)
        # Тут же добавляем в existing, чтобы не задваивались темы внутри одного прогона
        existing.add(topic_norm)
        existing.add(pk_norm)

    fresh = fresh[:MAX_NEW_TOPICS_PER_RUN]

    # 4. Пишем в таблицу
    # v2 multilang: определяем страну по lang (для обратной совместимости).
    # В будущем keyword_researcher можно расширить чтобы принимал --country
    # напрямую, но пока связка lang→country работает для проекта KOZYR.
    try:
        from country_config import country_of_lang
        country = country_of_lang(lang) or "ua"
    except Exception:
        country = "ua"

    if dry_run:
        print(f"🧪 DRY RUN: не пишу в таблицу. Получилось бы {len(fresh)} тем:")
        for t in fresh:
            print(f"   - {t.get('topic')}")
    else:
        for t in fresh:
            try:
                append_topic_to_sheet(sheet, t, country=country, langs="")
                print(f"➕ В таблицу: {t.get('topic')} (country={country})")
            except Exception as e:
                print(f"⚠️  Не удалось записать строку: {e}")

    # 5. Отчёт в TG
    stats = {
        "opportunities_found": len(gsc_opportunities),
        "existing_topics": len(existing),
        "new_topics_added": len(fresh),
    }
    if not dry_run:
        send_telegram_report(fresh, stats)

    return {"topics": fresh, "stats": stats}


# ==== CLI ====

def main() -> None:
    parser = argparse.ArgumentParser(
        description="KOZYR keyword research: собирает темы и пополняет Google Sheets"
    )
    parser.add_argument("--lang", default="ru", choices=["ru"],
                        help="Язык. Пока только ru (можно добавить в lang_config)")
    parser.add_argument("--skip-gsc", action="store_true",
                        help="Не читать Google Search Console")
    parser.add_argument("--skip-web", action="store_true",
                        help="Не подключать web_search Claude (быстрее и дешевле)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Не писать в таблицу, только показать что получилось бы")
    args = parser.parse_args()

    try:
        result = run(
            lang=args.lang,
            skip_gsc=args.skip_gsc,
            skip_web=args.skip_web,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"❌ Прогон упал: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n=== Готово ===")
    print(json.dumps(result["stats"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
