"""
KOZYR — Аналитика опубликованных статей.

Что делает:
1. Достаёт из Google Search Console показатели по каждой опубликованной
   статье (URL из taxonomy.json + prefix /ua/blog/).
2. Классифицирует статьи:
     - 🟢 winners — impressions > 500, CTR > 5%, средняя позиция ≤ 10
     - 🟡 needs_boost — impressions есть, но позиция 10-25 → можно оптимизировать
     - 🔴 flat — impressions < 50 за 60 дней → тема не зашла или ранние дни
     - ⚫ new — опубликовано < 14 дней назад (ещё не индексируется толком)
3. Для каждой статьи достаёт до 5 главных поисковых запросов
   (те, по которым уже приходит трафик) — это готовый материал для
   related-статей и оптимизации.
4. Публикует отчёт в Telegram (по кнопке /analytics или расписанию).
5. Готовит рекомендации в отдельный файл analytics/report.json —
   его читает keyword_researcher, чтобы не предлагать темы, дублирующие
   уже успешные статьи, но при этом брать «недо­крутые» запросы, где
   мы 10-20 в топе, и раскрывать их отдельными статьями.

Запуск:
  python analytics.py --lang ru
  python analytics.py --lang ru --format markdown       # печатает MD в stdout
  python analytics.py --lang ru --telegram              # шлёт в TG
  python analytics.py --lang ru --update-taxonomy       # проставляет метки в taxonomy

Требует:
  GOOGLE_SERVICE_ACCOUNT_JSON  — как везде
  GSC_SITE_URL                  — например sc-domain:kozyr.ua
  TELEGRAM_BOT_TOKEN / CHAT_ID  — если --telegram
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

try:
    from google.oauth2.service_account import Credentials
    HAS_CREDS = True
except ImportError:
    HAS_CREDS = False

try:
    from googleapiclient.discovery import build as gapi_build
    HAS_GSC = True
except ImportError:
    HAS_GSC = False

from lang_config import get_cfg, SITE_URL

# GA4-модуль (поведение + конверсии). Опциональный — если недоступен,
# отчёт всё равно строится на данных GSC (graceful degradation).
try:
    import ga4_analytics
    HAS_GA4_MODULE = True
except ImportError:
    HAS_GA4_MODULE = False


# ==== Конфигурация ====

# За какой период считаем трафик статьи
LOOKBACK_DAYS = 60

# Пороги классификации (подстроишь под свой сайт после первых прогонов)
WINNERS_MIN_IMPRESSIONS = 500
WINNERS_MIN_CTR = 0.05
WINNERS_MAX_POSITION = 10.0

NEEDS_BOOST_MIN_IMPRESSIONS = 100
NEEDS_BOOST_MIN_POSITION = 10.0
NEEDS_BOOST_MAX_POSITION = 25.0

FLAT_MAX_IMPRESSIONS = 50

NEW_ARTICLE_MAX_AGE_DAYS = 14

# Отчёт: сколько строк писать в TG
TG_TOP_LINES = 10


# ==== GSC ====

def get_gsc_service():
    if not HAS_GSC:
        return None
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/webmasters.readonly"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gapi_build("searchconsole", "v1", credentials=credentials)


def fetch_page_stats(service, site_url: str, page_url: str,
                     start_date=None, end_date=None) -> dict:
    """Достаёт агрегированные показатели по конкретному URL за период."""
    if end_date is None:
        end_date = datetime.now(timezone.utc).date()
    if start_date is None:
        start_date = end_date - timedelta(days=LOOKBACK_DAYS)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["page"],
        "dimensionFilterGroups": [{
            "filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]
        }],
        "rowLimit": 1,
        "type": "web",
    }
    try:
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    except Exception as e:
        # Часто это 403 (нет прав) или 404 (страница не в индексе). Мягко пропускаем.
        return {"error": str(e)[:200]}

    rows = resp.get("rows", [])
    if not rows:
        return {
            "impressions": 0, "clicks": 0, "ctr": 0.0, "position": None
        }
    r = rows[0]
    imp = r.get("impressions", 0)
    cl = r.get("clicks", 0)
    return {
        "impressions": imp,
        "clicks": cl,
        "ctr": round((cl / imp) if imp else 0, 4),
        "position": round(r.get("position", 0), 1),
    }


def fetch_top_queries_for_page(service, site_url: str, page_url: str,
                                 limit: int = 5,
                                 start_date=None, end_date=None) -> list[dict]:
    """Достаёт топ-N запросов, по которым эта страница показывается."""
    if end_date is None:
        end_date = datetime.now(timezone.utc).date()
    if start_date is None:
        start_date = end_date - timedelta(days=LOOKBACK_DAYS)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "dimensionFilterGroups": [{
            "filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]
        }],
        "rowLimit": limit,
        "type": "web",
    }
    try:
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    except Exception:
        return []
    rows = resp.get("rows", [])
    return [
        {
            "query": r["keys"][0],
            "impressions": r.get("impressions", 0),
            "clicks": r.get("clicks", 0),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


# ==== Классификация ====

def classify_article(stats: dict, published_at: datetime | None) -> str:
    """
    Возвращает одну из меток: 'new', 'winners', 'needs_boost', 'flat'.
    Порядок проверок важен — новые не должны попадать в 'flat'.
    """
    if published_at is not None:
        age_days = (datetime.now(timezone.utc) - published_at).days
        if age_days < NEW_ARTICLE_MAX_AGE_DAYS:
            return "new"

    imp = stats.get("impressions", 0)
    ctr = stats.get("ctr", 0.0)
    pos = stats.get("position") or 99.0

    if imp >= WINNERS_MIN_IMPRESSIONS and ctr >= WINNERS_MIN_CTR and pos <= WINNERS_MAX_POSITION:
        return "winners"
    if imp >= NEEDS_BOOST_MIN_IMPRESSIONS and NEEDS_BOOST_MIN_POSITION < pos <= NEEDS_BOOST_MAX_POSITION:
        return "needs_boost"
    if imp <= FLAT_MAX_IMPRESSIONS:
        return "flat"
    # Средняя категория (по метрикам ни туда, ни сюда)
    return "needs_boost"


# ==== Taxonomy helpers ====

def load_taxonomy(taxonomy_path: Path) -> dict:
    if not taxonomy_path.exists():
        return {"articles": {}}
    return json.loads(taxonomy_path.read_text(encoding="utf-8"))


def parse_published_date(article_entry: dict) -> datetime | None:
    """
    Пробуем достать дату публикации из taxonomy-записи.
    Разные проекты пишут по-разному: 'published_at', 'date_published',
    'generated_at' (у наших pending). Возвращаем UTC-aware datetime или None.
    """
    for field in ("published_at", "date_published", "generated_at"):
        val = article_entry.get(field)
        if not val:
            continue
        try:
            if val.endswith("Z"):
                val = val[:-1] + "+00:00"
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            continue
    return None


# ==== Основной сбор ====

def resolve_period(days=None, date_from=None, date_to=None, period=None):
    """
    Определяет период отчёта из разных способов задания. Приоритет:
      1. date_from + date_to  — точный диапазон (YYYY-MM-DD)
      2. period               — пресет: 'week'/'month'/'quarter'/'year'
      3. days                 — последние N дней
      4. дефолт LOOKBACK_DAYS
    Возвращает (start_date, end_date, label, n_days).
    """
    today = datetime.now(timezone.utc).date()

    # 1. Точный диапазон
    if date_from and date_to:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").date()
            end = datetime.strptime(date_to, "%Y-%m-%d").date()
            if start > end:
                start, end = end, start
            n = (end - start).days
            label = f"{start.isoformat()} — {end.isoformat()}"
            return start, end, label, n
        except ValueError:
            print(f"⚠️  Неверный формат дат ({date_from}..{date_to}), "
                  f"нужен YYYY-MM-DD. Использую последние {LOOKBACK_DAYS} дней.")

    # 2. Пресеты
    presets = {"week": 7, "month": 30, "quarter": 90, "year": 365}
    if period and period in presets:
        n = presets[period]
        start = today - timedelta(days=n)
        ru = {"week": "последняя неделя", "month": "последний месяц",
              "quarter": "последний квартал", "year": "последний год"}
        return start, today, ru[period], n

    # 3. N дней
    if days and days > 0:
        start = today - timedelta(days=days)
        return start, today, f"последние {days} дней", days

    # 4. Дефолт
    start = today - timedelta(days=LOOKBACK_DAYS)
    return start, today, f"последние {LOOKBACK_DAYS} дней", LOOKBACK_DAYS


def collect_analytics(lang: str = "ru", start_date=None, end_date=None,
                      period_label: str | None = None) -> dict:
    """
    Собирает per-article аналитику для всех статей в taxonomy.
    Возвращает большой словарь с распределением по категориям + сырыми данными.
    """
    site_url = os.environ.get("GSC_SITE_URL")
    if not site_url:
        raise RuntimeError(
            "GSC_SITE_URL не задан. Пример: sc-domain:kozyr.ua "
            "или https://kozyr.ua/. Настраивается в GitHub Secrets."
        )

    service = get_gsc_service()
    if service is None:
        raise RuntimeError(
            "google-api-python-client не установлен. "
            "Добавь в requirements.txt: google-api-python-client>=2.100.0"
        )

    cfg = get_cfg(lang)
    taxonomy = load_taxonomy(cfg["taxonomy"])
    articles = taxonomy.get("articles", {})
    if not articles:
        print("ℹ️  В taxonomy пусто, нечего анализировать")
        return {"lang": lang, "articles": [], "by_category": {}}

    # Период: если не передан — дефолтные последние LOOKBACK_DAYS дней.
    today = datetime.now(timezone.utc).date()
    if end_date is None:
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=LOOKBACK_DAYS)
    n_days = (end_date - start_date).days or LOOKBACK_DAYS
    if not period_label:
        period_label = f"{start_date.isoformat()} — {end_date.isoformat()}"

    print(f"📊 Собираю аналитику по {len(articles)} статьям "
          f"(период: {period_label}, {n_days} дней)")

    results = []
    for slug, entry in articles.items():
        page_url = f"{SITE_URL}{cfg['url_prefix']}/{slug}/"
        stats = fetch_page_stats(service, site_url, page_url,
                                 start_date=start_date, end_date=end_date)
        if stats.get("error"):
            print(f"  ⚠️  {slug}: {stats['error']}")
            continue
        published_at = parse_published_date(entry)
        category = classify_article(stats, published_at)
        top_queries = fetch_top_queries_for_page(
            service, site_url, page_url, limit=5,
            start_date=start_date, end_date=end_date)

        results.append({
            "slug": slug,
            "title": entry.get("title", slug),
            "url": page_url,
            "category": category,
            "stats": stats,
            "top_queries": top_queries,
            "published_at": published_at.isoformat() if published_at else None,
            "tags": entry.get("tags", []),
        })

    # Группировка по категориям
    by_category: dict[str, list[dict]] = {"winners": [], "needs_boost": [], "flat": [], "new": []}
    for r in results:
        by_category[r["category"]].append(r)

    # Сортируем «winners» по impressions по убыванию — самые сильные первыми
    by_category["winners"].sort(key=lambda x: -x["stats"].get("impressions", 0))
    # «needs_boost» — по impressions убыванию (потенциал больше там, где больше показов)
    by_category["needs_boost"].sort(key=lambda x: -x["stats"].get("impressions", 0))

    # ── Обогащаем данными GA4 (поведение + конверсии) ────────────────────────
    ga4: dict = {"available": False}
    if HAS_GA4_MODULE:
        try:
            ga4 = ga4_analytics.collect_ga4(days=n_days)
        except Exception as e:
            print(f"⚠️  GA4 сбор не удался (отчёт продолжит на GSC): {e}")
            ga4 = {"available": False}

    if ga4.get("available"):
        behavior = ga4.get("behavior_by_page", {})
        convs = ga4.get("conversions_by_page", {})
        # ключи GA4 — pagePath. Наши статьи — url_prefix/slug/.
        for r in results:
            path = f"{cfg['url_prefix']}/{r['slug']}/"
            # пробуем и с завершающим слешем, и без
            b = behavior.get(path) or behavior.get(path.rstrip("/")) or {}
            c = convs.get(path) or convs.get(path.rstrip("/")) or {}
            r["behavior"] = b
            r["conversions"] = c
            # производная: конверсия страницы = клики-к-партнёру / просмотры
            views = b.get("views", 0)
            clicks = c.get("total", 0)
            r["conv_rate"] = round(clicks / views, 4) if views else 0.0

    return {"lang": lang, "articles": results, "by_category": by_category,
            "ga4": ga4,
            "period_label": period_label,
            "period_days": n_days,
            "collected_at": datetime.now(timezone.utc).isoformat()}


# ==== Отчёты ====

def format_markdown_report(analytics: dict) -> str:
    lines = ["# Аналитика KOZYR", ""]
    lines.append(f"_Собрано: {analytics.get('collected_at', '?')}_")
    lines.append(f"_Период: {analytics.get('period_label', f'последние {LOOKBACK_DAYS} дней')}_")
    lines.append("")

    articles = analytics.get("articles", [])

    # Суммарные показатели
    sum_impr = sum(a.get("stats", {}).get("impressions", 0) for a in articles)
    sum_clk = sum(a.get("stats", {}).get("clicks", 0) for a in articles)
    ctr = (sum_clk / sum_impr * 100) if sum_impr else 0.0
    lines.append("## Сводка")
    lines.append(f"- 📝 Статей отслеживается: **{len(articles)}**")
    lines.append(f"- 🔍 Показы в поиске: **{sum_impr}**")
    lines.append(f"- 🖱 Клики: **{sum_clk}** (CTR {ctr:.1f}%)")
    lines.append("")

    # ── Все статьи одной таблицей, по убыванию показов ──
    def _impr(a): return a.get("stats", {}).get("impressions", 0)
    def _pos(a):
        p = a.get("stats", {}).get("position")
        return p if p is not None else 999
    ranked = sorted(articles, key=lambda a: (-_impr(a), _pos(a)))

    if ranked:
        lines.append("## Все статьи (по показам)")
        lines.append("")
        lines.append("| Статья | Показы | Клики | CTR | Позиция |")
        lines.append("|---|---:|---:|---:|---:|")
        for it in ranked:
            s = it["stats"]
            title_short = it["title"][:60]
            pos = s.get("position")
            pos_str = f"{pos:.1f}" if isinstance(pos, (int, float)) else "—"
            lines.append(
                f"| [{title_short}]({it['url']}) "
                f"| {s.get('impressions', 0)} "
                f"| {s.get('clicks', 0)} "
                f"| {s.get('ctr', 0) * 100:.1f}% "
                f"| {pos_str} |"
            )
        lines.append("")

    # ── Топ-запросы по каждой статье (материал для оптимизации) ──
    has_queries = any(a.get("top_queries") for a in ranked)
    if has_queries:
        lines.append("## Топ поисковых запросов по статьям")
        lines.append("")
        for it in ranked:
            if not it.get("top_queries"):
                continue
            lines.append(f"**{it['title'][:70]}**")
            for q in it["top_queries"]:
                lines.append(
                    f"- `{q['query']}` — {q['impressions']} показов, "
                    f"поз. {q.get('position', '—')}"
                )
            lines.append("")

    # ── Конверсия и поведение (GA4) ──────────────────────────────────────
    ga4 = analytics.get("ga4", {})
    articles = analytics.get("articles", [])
    if ga4.get("available"):
        t = ga4.get("totals", {})
        total_clicks = sum(a.get("conversions", {}).get("total", 0) for a in articles)
        total_views = t.get("views", 0)
        site_cr = (total_clicks / total_views) if total_views else 0.0
        lines.append("## 📈 Конверсия и поведение (GA4)")
        lines.append("")
        lines.append(f"- Пользователи за период: **{t.get('users', 0)}**")
        lines.append(f"- Просмотры: **{total_views}**")
        lines.append(f"- Переходы к партнёрам: **{total_clicks}**")
        lines.append(f"- Конверсия сайта: **{site_cr * 100:.2f}%**")
        lines.append("")

        # таблица по статьям: поведение + конверсия
        with_data = [a for a in articles if a.get("behavior") or a.get("conversions")]
        if with_data:
            with_data.sort(key=lambda a: -a.get("conversions", {}).get("total", 0))
            lines.append("### По статьям: просмотры → вовлечённость → переходы")
            lines.append("")
            lines.append("| Статья | Просм. | Ср. время | Отказы | Переходы | Конв. |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for a in with_data:
                b = a.get("behavior", {})
                c = a.get("conversions", {})
                lines.append(
                    f"| {a['title'][:50]} "
                    f"| {b.get('views', 0)} "
                    f"| {_fmt_time(b.get('avg_engagement_s', 0))} "
                    f"| {b.get('bounce_rate', 0) * 100:.0f}% "
                    f"| {c.get('total', 0)} "
                    f"| {a.get('conv_rate', 0) * 100:.1f}% |"
                )
            lines.append("")

        # разбивка кликов по блокам (что конвертит)
        source_totals: dict[str, int] = {}
        for a in articles:
            for src, n in a.get("conversions", {}).get("by_source", {}).items():
                source_totals[src] = source_totals.get(src, 0) + n
        if source_totals:
            lines.append("### 🎛 Что конвертит (клики по блокам)")
            lines.append("")
            lines.append("| Блок | Клики |")
            lines.append("|---|---:|")
            for src, n in sorted(source_totals.items(), key=lambda x: -x[1]):
                label = SOURCE_LABELS.get(src, src)
                lines.append(f"| {label} | {n} |")
            lines.append("")

        # источники трафика
        src = ga4.get("traffic_sources", {})
        if src:
            lines.append("### 🚦 Источники трафика")
            lines.append("")
            for ch, n in sorted(src.items(), key=lambda x: -x[1]):
                lines.append(f"- {ch}: **{n}**")
            lines.append("")
    else:
        lines.append("## 📈 Конверсия и поведение")
        lines.append("")
        lines.append(
            "_GA4 не подключён. Задай `GA4_PROPERTY_ID` и дай service "
            "account доступ Viewer в GA4 — появятся переходы к партнёрам, "
            "разбивка по блокам (виджет/панель/CTA), поведение и источники._")
        lines.append("")

    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    """Секунды → человекочитаемо (1м 23с)."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}с"
    return f"{s // 60}м {s % 60:02d}с"


def _pct(x: float) -> str:
    return f"{round(x * 100, 1)}%"


# Человекочитаемые названия источников кликов (link_source из analytics.js)
SOURCE_LABELS = {
    "side_widget": "виджет сбоку",
    "mobile_bar": "моб. панель",
    "final_cta": "финальный CTA",
    "partner_card": "карточка",
    "link": "ссылка в тексте",
}


def format_telegram_report(analytics: dict) -> str:
    """
    Подробная сводка для Telegram (лимит 4096, Markdown v1).
    Без категорий winners/flat — показываем ВСЕ статьи по реальным метрикам,
    отсортированные по показам. Секции:
      1. Итоги за период (трафик + конверсии)
      2. Все статьи: показы / клики / CTR / позиция (+ GA4 поведение)
      3. Топ поисковых запросов (по статьям)
      4. Топ по переходам к партнёрам
      5. Что конвертит: разбивка по блокам (виджет/панель/CTA)
      6. Читают, но не кликают
      7. Источники трафика
    GA4-секции тихо пропускаются, если GA4 пуст. SEO-часть есть всегда.
    """
    ga4 = analytics.get("ga4", {})
    ga4_on = ga4.get("available", False)
    articles = analytics.get("articles", [])

    L: list[str] = ["📊 *Аналитика KOZYR*",
                    f"_{analytics.get('period_label', f'последние {LOOKBACK_DAYS} дней')}_", ""]

    sum_impr = sum(a.get("stats", {}).get("impressions", 0) for a in articles)
    sum_clicks_seo = sum(a.get("stats", {}).get("clicks", 0) for a in articles)
    seo_ctr = (sum_clicks_seo / sum_impr) if sum_impr else 0.0

    # ── 1. ИТОГИ ЗА ПЕРИОД ──
    L.append("*⚡ Итоги за период*")
    if ga4_on:
        t = ga4.get("totals", {})
        total_clicks = sum(a.get("conversions", {}).get("total", 0) for a in articles)
        total_views = t.get("views", 0)
        site_cr = (total_clicks / total_views) if total_views else 0.0
        L += [
            f"👥 Пользователи: *{t.get('users', 0)}*  ·  🖥 Сессии: *{t.get('sessions', 0)}*",
            f"👁 Просмотры (GA4): *{total_views}*",
            f"🎯 Переходы к партнёрам: *{total_clicks}*  ·  📈 Конв.: *{_pct(site_cr)}*",
        ]
    L += [
        f"🔍 Показы в поиске: *{sum_impr}*  ·  🖱 Клики: *{sum_clicks_seo}*  ·  CTR *{_pct(seo_ctr)}*",
        f"📝 Статей отслеживается: *{len(articles)}*",
        "",
    ]
    if not ga4_on:
        L += ["_GA4 пуст (нет визитов или данные ещё идут). SEO ниже — из Search Console._", ""]

    # ── 2. ВСЕ СТАТЬИ ПО МЕТРИКАМ ──
    def _impr(a): return a.get("stats", {}).get("impressions", 0)
    def _pos(a):
        p = a.get("stats", {}).get("position")
        return p if p is not None else 999
    ranked = sorted(articles, key=lambda a: (-_impr(a), _pos(a)))

    if ranked:
        L.append("*📄 Статьи — показы · клики · CTR · позиция*")
        for a in ranked:
            s = a.get("stats", {})
            impr = s.get("impressions", 0)
            clk = s.get("clicks", 0)
            ctr = s.get("ctr", 0.0)
            pos = s.get("position")
            pos_str = f"{pos:.1f}" if isinstance(pos, (int, float)) else "—"
            title = escape_md(a["title"][:40])
            line = f"• {title}\n   {impr} 👁 · {clk} 🖱 · {_pct(ctr)} · поз. {pos_str}"
            if ga4_on:
                b = a.get("behavior", {})
                gv = b.get("views", 0)
                eng = b.get("avg_engagement_s", 0)
                conv = a.get("conversions", {}).get("total", 0)
                if gv or conv:
                    line += f"\n   GA4: {gv} просм · {eng}s вовлеч · {conv} перех."
            L.append(line)
        L.append("")

    # ── 3. ТОП ПОИСКОВЫХ ЗАПРОСОВ ──
    queries_block = []
    for a in ranked:
        tq = a.get("top_queries", [])
        if not tq:
            continue
        qparts = []
        for q in tq[:2]:
            qtext = q.get("query", "")
            qimpr = q.get("impressions", 0)
            if qtext:
                qparts.append(f"{escape_md(qtext[:30])} ({qimpr})")
        if qparts:
            queries_block.append(f"• {escape_md(a['title'][:32])}: " + ", ".join(qparts))
    if queries_block:
        L.append("*🔎 Топ запросов (показы)*")
        L += queries_block[:8]
        L.append("")

    # ── 4. ТОП ПО ПЕРЕХОДАМ ──
    if ga4_on:
        by_conv = sorted(
            [a for a in articles if a.get("conversions", {}).get("total", 0) > 0],
            key=lambda a: -a["conversions"]["total"])
        if by_conv:
            L.append("*🔥 Топ по переходам к партнёрам*")
            for a in by_conv[:6]:
                c = a["conversions"]["total"]
                cr = a.get("conv_rate", 0)
                L.append(f"• {escape_md(a['title'][:44])} — *{c}* ({_pct(cr)})")
            L.append("")

    # ── 5. ЧТО КОНВЕРТИТ ──
    if ga4_on:
        source_totals: dict[str, int] = {}
        for a in articles:
            for src, n in a.get("conversions", {}).get("by_source", {}).items():
                source_totals[src] = source_totals.get(src, 0) + n
        if source_totals:
            L.append("*🎛 Что конвертит (клики по блокам)*")
            for src, n in sorted(source_totals.items(), key=lambda x: -x[1]):
                label = SOURCE_LABELS.get(src, src)
                L.append(f"• {label}: *{n}*")
            L.append("")

    # ── 6. ЧИТАЮТ, НО НЕ КЛИКАЮТ ──
    if ga4_on:
        problem = []
        for a in articles:
            b = a.get("behavior", {})
            views = b.get("views", 0)
            cr = a.get("conv_rate", 0)
            if views >= 20 and cr < 0.01:
                problem.append((a, views, cr))
        problem.sort(key=lambda x: -x[1])
        if problem:
            L.append("*⚠️ Читают, но не кликают* (чинить виджет/CTA)")
            for a, views, cr in problem[:5]:
                L.append(f"• {escape_md(a['title'][:44])} — {views} просм., {_pct(cr)}")
            L.append("")

    # ── 7. ИСТОЧНИКИ ТРАФИКА ──
    if ga4_on:
        src = ga4.get("traffic_sources", {})
        if src:
            top = sorted(src.items(), key=lambda x: -x[1])[:6]
            L.append("*🚦 Источники трафика*")
            for k, v in top:
                L.append(f"• {escape_md(str(k))}: *{v}*")
            L.append("")

    L.append("Полный отчёт: `analytics/report.md`")
    L.append("Команды: /analytics · /suggested · /queue · /help")

    text = "\n".join(L)
    if len(text) > 4000:
        text = text[:3980] + "\n…(обрезано, полный — в report.md)"
    return text



def escape_md(text: str) -> str:
    if not text:
        return ""
    s = str(text)
    for ch in ("\\", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ℹ️  Telegram-креды не заданы, пропускаю отправку")
        return
    payload = {
        "chat_id": chat_id, "text": text[:4000],
        "parse_mode": "Markdown", "disable_web_page_preview": True,
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
            print(f"✅ TG-отчёт отправлен (status {resp.status})")
    except Exception as e:
        print(f"⚠️  Не удалось отправить в TG: {type(e).__name__}: {e}")


def update_taxonomy_categories(analytics: dict, lang: str) -> None:
    """Проставляет 'performance_category' в taxonomy.json — потом можно
    использовать в publish.py и в related-логике (не показывать flat-статьи
    в блоках 'похожее', продвигать winners как якори)."""
    cfg = get_cfg(lang)
    path = cfg["taxonomy"]
    if not path.exists():
        print(f"⚠️  taxonomy не найдена: {path}")
        return
    tax = json.loads(path.read_text(encoding="utf-8"))
    articles = tax.setdefault("articles", {})
    updated = 0
    for entry in analytics.get("articles", []):
        slug = entry["slug"]
        if slug in articles:
            articles[slug]["performance_category"] = entry["category"]
            articles[slug]["performance_updated"] = analytics.get("collected_at", "")
            updated += 1
    path.write_text(json.dumps(tax, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Обновил performance-метки в taxonomy: {updated} статей")


# ==== CLI ====

def main() -> None:
    parser = argparse.ArgumentParser(description="KOZYR analytics")
    parser.add_argument("--lang", default="ru", choices=["ru"])
    parser.add_argument("--format", choices=["json", "markdown", "telegram"],
                        default="markdown", help="Формат вывода в stdout")
    parser.add_argument("--telegram", action="store_true",
                        help="Отправить сводку в TG-чат оператора")
    parser.add_argument("--save-report", type=Path, default=Path("analytics/report.md"),
                        help="Куда сохранить полный markdown-отчёт")
    parser.add_argument("--save-json", type=Path, default=Path("analytics/report.json"),
                        help="Куда сохранить сырые данные (json)")
    parser.add_argument("--update-taxonomy", action="store_true",
                        help="Записать performance-метки в taxonomy.json")
    # ── Настройка периода отчёта ──
    parser.add_argument("--days", type=int, default=None,
                        help="Последние N дней (напр. --days 30). "
                             "По умолчанию 60.")
    parser.add_argument("--period", choices=["week", "month", "quarter", "year"],
                        default=None,
                        help="Пресет периода: week/month/quarter/year")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="Начало диапазона YYYY-MM-DD (с --to)")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="Конец диапазона YYYY-MM-DD (с --from)")
    args = parser.parse_args()

    # Определяем период из аргументов
    start_date, end_date, period_label, _n = resolve_period(
        days=args.days, date_from=args.date_from,
        date_to=args.date_to, period=args.period)

    try:
        analytics = collect_analytics(
            lang=args.lang, start_date=start_date, end_date=end_date,
            period_label=period_label)
    except Exception as e:
        print(f"❌ Сбор аналитики упал: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    # Сохраняем сырые данные
    args.save_json.parent.mkdir(parents=True, exist_ok=True)
    args.save_json.write_text(
        json.dumps(analytics, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md_report = format_markdown_report(analytics)
    args.save_report.parent.mkdir(parents=True, exist_ok=True)
    args.save_report.write_text(md_report, encoding="utf-8")
    print(f"✅ Отчёты сохранены: {args.save_json}, {args.save_report}")

    if args.update_taxonomy:
        update_taxonomy_categories(analytics, args.lang)

    if args.format == "json":
        print(json.dumps(analytics, indent=2, ensure_ascii=False))
    elif args.format == "telegram":
        print(format_telegram_report(analytics))
    else:
        print(md_report)

    if args.telegram:
        send_telegram(format_telegram_report(analytics))


if __name__ == "__main__":
    main()
