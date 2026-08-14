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
  - GSC_SITE_URL                  — sc-domain:kozyr.club или https://kozyr.club/
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


def get_existing_topics_full(sheet) -> list[dict]:
    """Возвращает существующие темы как список dict с ключами topic,
    primary_keyword, secondary_keywords, target_page, intent — для проверки
    каннибализации (нужны все поля, а не только строка ключа)."""
    try:
        records = sheet.get_all_records()
    except Exception:
        return []
    out = []
    for row in records:
        # берём только активные темы (не rejected) — с rejected не конфликтуем
        status = str(row.get("status", "")).strip().lower()
        if status == "rejected":
            continue
        out.append({
            "topic": str(row.get("topic", "")),
            "primary_keyword": str(row.get("primary_keyword", "")),
            "secondary_keywords": str(row.get("secondary_keywords", "")),
            "target_page": str(row.get("target_page", "")),
            "intent": str(row.get("intent", "")),
        })
    return out


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

CLUSTER_SYSTEM_PROMPT = """Ты — ведущий SEO-стратег для сайта KOZYR — витрины
покерных рейкбек-сделок и обзоров покер-румов/клубов. Домен kozyr.club.
Аудитория: игроки в покер из Украины (укр + рус языки), ищущие где играть,
какой рейкбек, какие бонусы, как выводить деньги.

ТВОЯ ЗАДАЧА: на основе поисковых запросов (GSC) и web_search выдать 6-8 новых
тем для блога, которые вместе покрывают воронку трафика — от широких
информационных запросов (много трафика, верх воронки) до узких коммерческих
(меньше трафика, но горячая аудитория, близкая к выбору рума).

═══════════════════════════════════════════════════════════════════
ФАКТЫ О ПЛОЩАДКАХ (КРИТИЧЕСКИ ВАЖНО — не противоречь им!)
═══════════════════════════════════════════════════════════════════
Сайт уже описывает эти площадки строго определённым образом. Темы и ключи
НЕ ДОЛЖНЫ противоречить фактам ниже, иначе статьи создадут дезинформацию:

• PokerBet — покер-рум на гривны, лицензия Curaçao Gaming Authority (НЕ
  украинская лицензия). У PokerBet НЕТ программы рейкбека — есть только
  бонусы (Welcome до 40 000 ₴, промо). ЗАПРЕЩЕНО: темы вида «рейкбек в
  PokerBet», «PokerBet рейкбек 40%» — такого продукта не существует.
  Можно: «бонусы PokerBet», «PokerBet вывод денег», «PokerBet обзор».

• KlubOk (ClubGG) — приватный покерный клуб в приложении ClubGG. Здесь
  рейкбек ЕСТЬ и достигает 40-65% (выплачивает агент клуба, не сам ClubGG).
  Игра на виртуальную валюту, реальные деньги через агентов. Рейкбек-темы
  привязывай к КЛУБАМ/ClubGG/KlubOk, НЕ к PokerBet.

• Онлайн-покер в Украине легален. НЕ используй устаревшее: регулятор
  «КРАИЛ» упоминай только если тема прямо про регулирование, и то
  аккуратно (регуляторная среда менялась). НЕ строй темы вокруг
  «легальный/нелегальный», «запрет», паники — это отпугивает и устаревает.

═══════════════════════════════════════════════════════════════════
СТРАТЕГИЯ КЛЮЧЕЙ: ШИРОКИЕ + УЗКИЕ (обязательный баланс)
═══════════════════════════════════════════════════════════════════
Из 6-8 тем распредели примерно так:

▸ 2-3 ШИРОКИЕ информационные темы (верх воронки, много трафика):
  Обучающие запросы, которые ищут массово. Приводят новый трафик,
  строят авторитет, легко ранжируются длинным качественным контентом.
  primary_keyword — среднечастотный обучающий запрос БЕЗ бренда.
  Примеры: «как играть в покер онлайн», «что такое рейкбек в покере»,
  «правила техасского холдема», «покерные комбинации», «банкролл
  менеджмент», «как выводить деньги из покер-рума».
  intent: informational. target_page: обычно /ua/ или /ua/blog/.

▸ 3-4 УЗКИЕ коммерческие темы (низ воронки, горячий трафик):
  Запросы людей, готовых выбрать рум/клуб. Меньше трафика, но высокая
  конверсия в переход по партнёрской ссылке.
  primary_keyword — длинный хвост с гео/брендом/годом.
  Примеры: «покер на гривны украина 2026», «покерный клуб ClubGG
  украина», «PokerBet обзор бонусы», «где играть в покер новичку украина»,
  «лучшие покер-румы украина рейкбек».
  intent: commercial. target_page: /ua/rooms/pokerbet/, /ua/clubs/klubok/, /ua/.

▸ 1 ТРЕНДОВАЯ/сезонная тема (если web_search выявил актуальное):
  Свежий инфоповод, турнир, обновление румов. Ловит всплеск интереса.

═══════════════════════════════════════════════════════════════════
ПРАВИЛА КАЧЕСТВА КЛЮЧЕЙ
═══════════════════════════════════════════════════════════════════
1. Одна тема = одна статья 1800-2600 слов, один чёткий поисковый интент.
2. НЕ дублируй темы из EXISTING_TOPICS — раскрывай новые аспекты/запросы.
   НЕ КАННИБАЛИЗИРУЙ: две твои темы не должны бороться за один запрос.
   Каждая тема — свой уникальный primary_keyword и своя страница-цель.
   Если две темы близки (например «рейкбек в клубе» и «как получить
   рейкбек ClubGG») — оставь ОДНУ, более широкую/сильную. Разные темы =
   разные поисковые интенты, а не переформулировки одного запроса.
3. primary_keyword — реальная поисковая фраза, как её вводят в Google.
   Широкие: без бренда, обучающие. Узкие: гео + бренд/год + модификатор.
   Плохо (пусто): «покер». Плохо (мертво): «рейкбек в PokerBet».
   Хорошо (широкий): «что такое рейкбек в покере».
   Хорошо (узкий): «покерный клуб clubgg украина рейкбек».
4. secondary_keywords — ровно 3 связанных запроса (LSI/синонимы/вариации),
   которые статья тоже закроет. Смесь: 1 широкий + 1-2 с гео/брендом.
5. ГЕО: аудитория — Украина. В узких темах включай «Украина/Україна»,
   «на гривны», «ClubGG/PokerBet/KlubOk» где уместно. В широких —
   можно без гео (обучающие запросы ищут вне привязки к стране).
6. target_page: /ua/ (каталог), /ua/rooms/pokerbet/ (обзор PokerBet),
   /ua/clubs/klubok/ (обзор KlubOk), /ua/blog/ (общий блог).
7. intent: informational (разобраться) | commercial (выбрать/сравнить).
8. notes — 2-3 предложения оператору: угол статьи, на чём сделать акцент,
   каких формулировок ИЗБЕГАТЬ (напомни про факты выше, если тема рядом
   с PokerBet/рейкбеком/регулированием).
9. evidence — откуда взят угол (какой GSC-запрос/тренд web_search питает).

Отвечай СТРОГО JSON-массивом, без преамбулы, без code fences:

[
  {
    "topic": "Что такое рейкбек в покере и как его получать",
    "primary_keyword": "что такое рейкбек в покере",
    "secondary_keywords": "как работает рейкбек, рейкбек украина, рейкбек клуб clubgg",
    "intent": "informational",
    "target_page": "/ua/blog/",
    "notes": "Широкая обучающая тема (верх воронки). Объясни механику рейкбека простыми словами, приведи расчёт. Рейкбек-примеры привязывай к клубам ClubGG (40-65%), НЕ к PokerBet (у него рейкбека нет — только бонусы).",
    "evidence": "GSC: широкий обучающий запрос, высокие показы; строит трафик и авторитет"
  }
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

    user_msg = f"""GSC OPPORTUNITIES (запросы, по которым сайт kozyr.club уже показывается,
но позиция низкая — есть место для роста, если написать точечную статью):

{gsc_block}

---

EXISTING_TOPICS (уже есть в очереди или опубликованы — НЕ дублируй):

{existing_block}

---

Проверь через web_search (для актуальности и трендов):
1. Какие ОБУЧАЮЩИЕ покерные темы сейчас популярны (правила, стратегии,
   комбинации, банкролл, вывод денег) — это широкие трафиковые запросы.
2. Что игроки из Украины/СНГ спрашивают про покерные клубы ClubGG, рейкбек
   в клубах, игру на гривны, вывод средств — узкие коммерческие запросы.
3. Свежие тренды: новые форматы, обновления приложений, турниры, промо.

ПОМНИ факты о площадках из системного промпта: PokerBet = бонусы (НЕ рейкбек),
рейкбек = только клубы ClubGG/KlubOk. Не предлагай темы, противоречащие этому.

Затем выдай {MAX_NEW_TOPICS_PER_RUN} тем в JSON из системного промпта,
соблюдая баланс: 2-3 широкие информационные (трафик) + 3-4 узкие
коммерческие (конверсия) + опционально 1 трендовая.
Приоритет: если есть GSC-запросы с реальным спросом — питай темы ими.
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

def send_telegram_report(new_topics: list[dict], gsc_stats: dict,
                         cannibal_skipped: list[dict] | None = None) -> None:
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
    ]
    if cannibal_skipped:
        lines.append(
            f"🚫 Отсеяно {len(cannibal_skipped)} тем из-за каннибализации "
            f"(боролись бы за те же запросы)."
        )
    lines += [
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


# ═══════════════════════════════════════════════════════════════════
# Детектор каннибализации ключевых слов
# ═══════════════════════════════════════════════════════════════════
# Каннибализация = две статьи борются за один поисковый запрос. Google не
# знает какую показывать → обе проседают. Ловим ДО добавления новой темы.
# Ключевые сигналы: (1) пересечение слов в primary_keyword (главный), (2)
# общее сходство ключей, (3) совпадение target_page + intent. Различаем
# широкие обучающие и узкие коммерческие темы — они НЕ конфликтуют, даже
# если про один предмет (разный интент = разная страница выдачи).

_STOPWORDS = {
    "в", "на", "и", "с", "по", "для", "как", "что", "такое", "это", "за",
    "от", "до", "из", "о", "об", "у", "к", "the", "a", "an", "of", "to",
    "in", "on", "for", "how", "what", "is", "покер", "poker", "2025", "2026",
    "украина", "україна", "украины", "україни", "онлайн",
}

def _stem(w: str) -> str:
    """Грубый стемминг — отрезаем частые русские окончания, чтобы
    сопоставлять однокоренные (рейкбек/рейкбека, клуб/клубе/клубах)."""
    for suf in ("ами", "ями", "ах", "ях", "ов", "ев", "ом", "ем", "ей",
                "ой", "ую", "ю", "е", "а", "и", "ы", "у", "о"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[:-len(suf)]
    return w

def _keywords_set(topic: dict) -> set:
    """Значимые (стеммированные) слова из всех ключей темы."""
    text = " ".join([
        topic.get("primary_keyword", ""),
        topic.get("secondary_keywords", ""),
        topic.get("topic", ""),
    ]).lower()
    words = re.findall(r"[a-zа-яёіїєґ]+", text)
    return {_stem(w) for w in words if len(w) > 2 and w not in _STOPWORDS}

def _primary_set(topic: dict) -> set:
    """Только слова из primary_keyword — сильнейший сигнал конфликта."""
    words = re.findall(r"[a-zа-яёіїєґ]+", topic.get("primary_keyword", "").lower())
    return {_stem(w) for w in words if len(w) > 2 and w not in _STOPWORDS}

def _similarity(a: dict, b: dict) -> float:
    """Жаккар-сходство по всем ключам (0..1)."""
    sa, sb = _keywords_set(a), _keywords_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def _primary_overlap(a: dict, b: dict) -> float:
    """Доля пересечения ГЛАВНЫХ ключей (от меньшего, 0..1)."""
    pa, pb = _primary_set(a), _primary_set(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / min(len(pa), len(pb))

CANNIBAL_THRESHOLD = 0.45

def detect_cannibalization(new_topic: dict, existing_topics: list[dict]) -> dict | None:
    """
    Проверяет, не съедает ли new_topic какую-то из existing_topics.
    Возвращает dict с деталями конфликта или None если чисто.

    Конфликтом считаем, если выполнено ЛЮБОЕ:
      • общее сходство ключей >= 0.45
      • главные ключи (primary) пересекаются на >= 60%
      • среднее сходство (>=0.3) И та же страница-цель И тот же интент
      • сильное пересечение primary (>=0.5) И та же страница-цель
    Широкая инфо-тема и узкая коммерческая про один предмет НЕ конфликтуют
    (разный intent + разный target_page → разные места в выдаче).
    """
    best = None
    for ex in existing_topics:
        sim = _similarity(new_topic, ex)
        pov = _primary_overlap(new_topic, ex)
        same_target = (new_topic.get("target_page", "").strip() ==
                       ex.get("target_page", "").strip())
        same_intent = (new_topic.get("intent", "").strip() ==
                       ex.get("intent", "").strip())
        is_conflict = (sim >= CANNIBAL_THRESHOLD
                       or pov >= 0.6
                       or (sim >= 0.3 and same_target and same_intent)
                       or (pov >= 0.5 and same_target))
        score = max(sim, pov)
        if is_conflict and (best is None or score > best["_score"]):
            best = {
                "conflicts_with": ex.get("topic", ex.get("primary_keyword", "?")),
                "similarity": round(sim, 2),
                "primary_overlap": round(pov, 2),
                "same_target": same_target,
                "same_intent": same_intent,
                "target_page": new_topic.get("target_page", ""),
                "_score": score,
            }
    return best



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
    existing_full = get_existing_topics_full(sheet)  # для проверки каннибализации
    print(f"📚 В таблице сейчас {len(existing)} уникальных тем/ключей")

    # 3. Claude → новые темы
    topics = cluster_and_generate_topics(
        gsc_opportunities=gsc_opportunities,
        existing_queries=existing,
        do_web_search=not skip_web,
    )

    # Отфильтровываем: точные дубли + каннибализацию (темы, съедающие друг друга)
    fresh = []
    cannibal_skipped = []  # для отчёта
    # накопитель уже принятых тем этого прогона (чтобы новые темы не ели друг друга)
    accepted_full = list(existing_full)
    for t in topics:
        topic_norm = t.get("topic", "").strip().lower()
        pk_norm = t.get("primary_keyword", "").strip().lower()

        # (а) точный дубль
        if topic_norm in existing or pk_norm in existing:
            print(f"⏭️  Пропускаю дубль: {t.get('topic')!r}")
            continue

        # (б) каннибализация — тема борется за тот же запрос, что уже есть
        conflict = detect_cannibalization(t, accepted_full)
        if conflict:
            print(f"🚫 Каннибализация: {t.get('topic')!r} пересекается с "
                  f"{conflict['conflicts_with']!r} "
                  f"(сходство {conflict['similarity']}, "
                  f"та же цель: {conflict['same_target']}). Пропускаю.")
            cannibal_skipped.append({
                "topic": t.get("topic", ""),
                "conflicts_with": conflict["conflicts_with"],
                "similarity": conflict["similarity"],
            })
            continue

        fresh.append(t)
        # Добавляем в накопители, чтобы следующие темы прогона не задваивались/не ели
        existing.add(topic_norm)
        existing.add(pk_norm)
        accepted_full.append(t)

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
        send_telegram_report(fresh, stats, cannibal_skipped=cannibal_skipped)

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
