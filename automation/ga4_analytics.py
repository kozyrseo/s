"""
KOZYR — Сбор данных из Google Analytics 4 (Data API).

Дополняет analytics.py (который берёт SEO из Search Console) данными о
ПОВЕДЕНИИ и КОНВЕРСИИ — то, что GSC не показывает:

  ПОВЕДЕНИЕ (на статью):
    - просмотры (screenPageViews)
    - активные пользователи (activeUsers)
    - среднее время вовлечения (userEngagementDuration / activeUsers)
    - показатель вовлечённости (engagementRate)
    - показатель отказов (bounceRate)

  КОНВЕРСИЯ (события, которые мы шлём из analytics.js):
    - partner_page_click — переход на страницу партнёра из статьи
    - affiliate_click    — внешний переход к партнёру (outbound)
    - в разрезе link_source: side_widget / mobile_bar / final_cta /
      partner_card / link  → видно, ЧТО конвертит

Единый источник авторизации — тот же service account, что у GSC
(GOOGLE_SERVICE_ACCOUNT_JSON). Нужно только:
  1. Дать service account доступ Viewer в GA4 (Property Access Management)
  2. Задать GA4_PROPERTY_ID (число, напр. "480000000")

Если GA4_PROPERTY_ID не задан или библиотека не установлена — модуль
возвращает пустые данные и НЕ роняет основной отчёт (graceful degradation).

Запуск (для отладки):
  python ga4_analytics.py           # печатает собранное как JSON
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

# GA4 Data API — опциональная зависимость (не роняем, если нет)
try:
    from google.oauth2.service_account import Credentials
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest, Filter,
        FilterExpression, FilterExpressionList,
    )
    HAS_GA4 = True
except ImportError:
    HAS_GA4 = False


LOOKBACK_DAYS = 60
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

# События конверсии, которые шлёт наш analytics.js
CONVERSION_EVENTS = ["partner_page_click", "affiliate_click"]


def _get_ga4_client() -> Any | None:
    """Клиент GA4 Data API на том же service account, что и GSC."""
    if not HAS_GA4:
        return None
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        creds_info = json.loads(raw)
        creds = Credentials.from_service_account_info(
            creds_info, scopes=GA4_SCOPES)
        return BetaAnalyticsDataClient(credentials=creds)
    except Exception as e:
        print(f"⚠️  GA4: не удалось создать клиент: {e}")
        return None


def _property() -> str | None:
    pid = os.environ.get("GA4_PROPERTY_ID", "").strip()
    if not pid:
        return None
    # допускаем и "properties/123" и "123"
    return pid if pid.startswith("properties/") else f"properties/{pid}"


def is_available() -> bool:
    """GA4 готов к сбору? (есть библиотека, креды и property)."""
    return HAS_GA4 and bool(_property()) and _get_ga4_client() is not None


def _date_range(days: int = LOOKBACK_DAYS) -> "DateRange":
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return DateRange(start_date=start.isoformat(), end_date=end.isoformat())


def fetch_behavior_by_page(days: int = LOOKBACK_DAYS) -> dict[str, dict]:
    """
    Поведенческие метрики по каждой странице (path → метрики).
    Ключ — pagePath (напр. '/ua/blog/klubok-.../').
    """
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}

    req = RunReportRequest(
        property=prop,
        date_ranges=[_date_range(days)],
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="activeUsers"),
            Metric(name="userEngagementDuration"),
            Metric(name="engagementRate"),
            Metric(name="bounceRate"),
        ],
        limit=1000,
    )
    try:
        resp = client.run_report(req)
    except Exception as e:
        print(f"⚠️  GA4 behavior: {e}")
        return {}

    out: dict[str, dict] = {}
    for row in resp.rows:
        path = row.dimension_values[0].value
        vals = [m.value for m in row.metric_values]
        views = int(float(vals[0] or 0))
        users = int(float(vals[1] or 0))
        eng_dur = float(vals[2] or 0)
        eng_rate = float(vals[3] or 0)
        bounce = float(vals[4] or 0)
        out[path] = {
            "views": views,
            "users": users,
            # среднее время вовлечения на пользователя, сек
            "avg_engagement_s": round(eng_dur / users, 1) if users else 0.0,
            "engagement_rate": round(eng_rate, 4),
            "bounce_rate": round(bounce, 4),
        }
    return out


def fetch_conversions_by_page(days: int = LOOKBACK_DAYS) -> dict[str, dict]:
    """
    Клики по партнёрским кнопкам в разрезе страницы И источника (link_source).
    Возвращает: path → {
        'total': N,
        'by_source': {'side_widget': N, 'mobile_bar': N, 'final_cta': N, ...},
        'by_event': {'partner_page_click': N, 'affiliate_click': N},
    }
    Требует, чтобы в GA4 был зарегистрирован кастомный параметр 'link_source'
    (custom dimension). Если его нет — by_source будет пустым, но total и
    by_event посчитаются.
    """
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}

    # Фильтр: только наши конверсионные события
    ev_filter = FilterExpression(
        filter=Filter(
            field_name="eventName",
            in_list_filter=Filter.InListFilter(values=CONVERSION_EVENTS),
        )
    )

    # Пытаемся с разрезом по link_source (custom dimension). Если API его не
    # знает — повторяем без него.
    dims_with_source = [
        Dimension(name="pagePath"),
        Dimension(name="eventName"),
        Dimension(name="customEvent:link_source"),
        Dimension(name="customEvent:link_label"),
    ]
    dims_plain = [
        Dimension(name="pagePath"),
        Dimension(name="eventName"),
    ]

    def _run(dims):
        req = RunReportRequest(
            property=prop,
            date_ranges=[_date_range(days)],
            dimensions=dims,
            metrics=[Metric(name="eventCount")],
            dimension_filter=ev_filter,
            limit=2000,
        )
        return client.run_report(req)

    have_source = True
    try:
        resp = _run(dims_with_source)
    except Exception:
        # custom dimension не зарегистрирован — берём без источника
        have_source = False
        try:
            resp = _run(dims_plain)
        except Exception as e:
            print(f"⚠️  GA4 conversions: {e}")
            return {}

    out: dict[str, dict] = {}
    for row in resp.rows:
        dv = [d.value for d in row.dimension_values]
        path = dv[0]
        event = dv[1]
        source = dv[2] if have_source and len(dv) > 2 else None
        label = dv[3] if have_source and len(dv) > 3 else None
        count = int(float(row.metric_values[0].value or 0))

        rec = out.setdefault(path, {
            "total": 0, "by_source": {}, "by_event": {}, "by_label": {}})
        rec["total"] += count
        rec["by_event"][event] = rec["by_event"].get(event, 0) + count
        if source:
            # '(not set)' → 'link' (клики без явного источника)
            src = source if source and source != "(not set)" else "link"
            rec["by_source"][src] = rec["by_source"].get(src, 0) + count
        if label and label != "(not set)":
            rec["by_label"][label] = rec["by_label"].get(label, 0) + count
    return out


def fetch_traffic_sources(days: int = LOOKBACK_DAYS) -> dict[str, int]:
    """Откуда приходит трафик: канал → пользователи (organic/direct/social...)."""
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}
    req = RunReportRequest(
        property=prop,
        date_ranges=[_date_range(days)],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="activeUsers")],
        limit=50,
    )
    try:
        resp = client.run_report(req)
    except Exception as e:
        print(f"⚠️  GA4 sources: {e}")
        return {}
    out: dict[str, int] = {}
    for row in resp.rows:
        ch = row.dimension_values[0].value or "(other)"
        out[ch] = int(float(row.metric_values[0].value or 0))
    return out


def fetch_totals(days: int = LOOKBACK_DAYS) -> dict:
    """Сводные цифры по сайту за период (для шапки отчёта)."""
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}
    req = RunReportRequest(
        property=prop,
        date_ranges=[_date_range(days)],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
        ],
    )
    try:
        resp = client.run_report(req)
    except Exception as e:
        print(f"⚠️  GA4 totals: {e}")
        return {}
    if not resp.rows:
        return {"users": 0, "views": 0, "sessions": 0}
    v = [m.value for m in resp.rows[0].metric_values]
    return {
        "users": int(float(v[0] or 0)),
        "views": int(float(v[1] or 0)),
        "sessions": int(float(v[2] or 0)),
    }


def fetch_by_country(days: int = LOOKBACK_DAYS) -> dict[str, int]:
    """Пользователи по странам (топ гео-источников трафика)."""
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}
    try:
        req = RunReportRequest(
            property=prop,
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="country")],
            metrics=[Metric(name="activeUsers")],
            limit=15,
        )
        resp = client.run_report(req)
        out = {}
        for row in resp.rows:
            country = row.dimension_values[0].value or "—"
            n = int(float(row.metric_values[0].value or 0))
            if n > 0:
                out[country] = n
        return out
    except Exception as e:
        print(f"⚠️  GA4 by_country: {e}")
        return {}


def fetch_by_device(days: int = LOOKBACK_DAYS) -> dict[str, int]:
    """Пользователи по типу устройства (desktop / mobile / tablet)."""
    client = _get_ga4_client()
    prop = _property()
    if not client or not prop:
        return {}
    try:
        req = RunReportRequest(
            property=prop,
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="deviceCategory")],
            metrics=[Metric(name="activeUsers")],
            limit=10,
        )
        resp = client.run_report(req)
        out = {}
        for row in resp.rows:
            dev = row.dimension_values[0].value or "—"
            n = int(float(row.metric_values[0].value or 0))
            if n > 0:
                out[dev] = n
        return out
    except Exception as e:
        print(f"⚠️  GA4 by_device: {e}")
        return {}


def collect_ga4(days: int = LOOKBACK_DAYS) -> dict:
    """
    Собирает всё из GA4 в один словарь. Безопасно: если GA4 недоступен —
    возвращает {'available': False} и НЕ роняет основной отчёт.
    """
    if not is_available():
        return {"available": False}
    print(f"📈 GA4: собираю поведение + конверсии (lookback {days} дней)")
    return {
        "available": True,
        "totals": fetch_totals(days),
        "behavior_by_page": fetch_behavior_by_page(days),
        "conversions_by_page": fetch_conversions_by_page(days),
        "traffic_sources": fetch_traffic_sources(days),
        "by_country": fetch_by_country(days),
        "by_device": fetch_by_device(days),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import sys
    data = collect_ga4()
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()
