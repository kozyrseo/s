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

from google.oauth2.service_account import Credentials

try:
    from googleapiclient.discovery import build as gapi_build
    HAS_GSC = True
except ImportError:
    HAS_GSC = False

from lang_config import get_cfg, SITE_URL


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


def fetch_page_stats(service, site_url: str, page_url: str) -> dict:
    """Достаёт агрегированные показатели по конкретному URL за LOOKBACK_DAYS."""
    end_date = datetime.now(timezone.utc).date()
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
                                 limit: int = 5) -> list[dict]:
    """Достаёт топ-N запросов, по которым эта страница показывается."""
    end_date = datetime.now(timezone.utc).date()
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

def collect_analytics(lang: str = "ru") -> dict:
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

    print(f"📊 Собираю аналитику по {len(articles)} статьям (lookback {LOOKBACK_DAYS} дней)")

    results = []
    for slug, entry in articles.items():
        page_url = f"{SITE_URL}{cfg['url_prefix']}/{slug}/"
        stats = fetch_page_stats(service, site_url, page_url)
        if stats.get("error"):
            print(f"  ⚠️  {slug}: {stats['error']}")
            continue
        published_at = parse_published_date(entry)
        category = classify_article(stats, published_at)
        top_queries = fetch_top_queries_for_page(service, site_url, page_url, limit=5)

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

    return {"lang": lang, "articles": results, "by_category": by_category,
            "collected_at": datetime.now(timezone.utc).isoformat()}


# ==== Отчёты ====

def format_markdown_report(analytics: dict) -> str:
    lines = ["# Аналитика KOZYR", ""]
    lines.append(f"_Собрано: {analytics.get('collected_at', '?')}_")
    lines.append(f"_Период: последние {LOOKBACK_DAYS} дней_")
    lines.append("")

    cats = analytics.get("by_category", {})
    lines.append(f"## Сводка")
    lines.append(f"- 🟢 Winners: **{len(cats.get('winners', []))}**")
    lines.append(f"- 🟡 Needs boost: **{len(cats.get('needs_boost', []))}**")
    lines.append(f"- 🔴 Flat: **{len(cats.get('flat', []))}**")
    lines.append(f"- ⚫ New (< {NEW_ARTICLE_MAX_AGE_DAYS} дней): **{len(cats.get('new', []))}**")
    lines.append("")

    def render_group(title: str, items: list[dict], limit: int = 15) -> None:
        if not items:
            return
        lines.append(f"## {title} (топ {min(limit, len(items))})")
        lines.append("")
        lines.append("| Статья | Показы | Клики | CTR | Позиция |")
        lines.append("|---|---:|---:|---:|---:|")
        for it in items[:limit]:
            s = it["stats"]
            title_short = it["title"][:60]
            lines.append(
                f"| [{title_short}]({it['url']}) "
                f"| {s.get('impressions', 0)} "
                f"| {s.get('clicks', 0)} "
                f"| {s.get('ctr', 0) * 100:.1f}% "
                f"| {s.get('position', '—')} |"
            )
        lines.append("")

    render_group("🟢 Winners — работают, не трогаем", cats.get("winners", []))
    render_group("🟡 Needs boost — есть места 10-25, можно докрутить", cats.get("needs_boost", []))
    render_group("🔴 Flat — не пошли, рассмотреть переписывание", cats.get("flat", []))

    # Топ-запросы по needs_boost — это чистый материал для «дожимающих» статей
    if cats.get("needs_boost"):
        lines.append("## Топ-запросы для 'дожатия' (материал для новых статей)")
        lines.append("")
        for it in cats["needs_boost"][:5]:
            if not it["top_queries"]:
                continue
            lines.append(f"**{it['title'][:70]}**")
            for q in it["top_queries"]:
                lines.append(
                    f"- `{q['query']}` — {q['impressions']} показов, "
                    f"поз. {q['position']}"
                )
            lines.append("")

    return "\n".join(lines)


def format_telegram_report(analytics: dict) -> str:
    """Компактная сводка для TG (лимит 4096, режим Markdown v1)."""
    cats = analytics.get("by_category", {})
    lines = [
        "📊 *Аналитика KOZYR*",
        f"_последние {LOOKBACK_DAYS} дней_",
        "",
        f"🟢 Winners: *{len(cats.get('winners', []))}*",
        f"🟡 Needs boost: *{len(cats.get('needs_boost', []))}*",
        f"🔴 Flat: *{len(cats.get('flat', []))}*",
        f"⚫ New: *{len(cats.get('new', []))}*",
        "",
    ]

    if cats.get("winners"):
        lines.append("*🟢 Топ-5 победителей:*")
        for it in cats["winners"][:5]:
            s = it["stats"]
            lines.append(
                f"• {escape_md(it['title'][:55])} — {s.get('clicks', 0)}кл · "
                f"поз. {s.get('position', '—')}"
            )
        lines.append("")

    if cats.get("needs_boost"):
        lines.append("*🟡 Топ-5 для дожатия (высокий потенциал):*")
        for it in cats["needs_boost"][:5]:
            s = it["stats"]
            lines.append(
                f"• {escape_md(it['title'][:55])} — {s.get('impressions', 0)} показов · "
                f"поз. {s.get('position', '—')}"
            )
        lines.append("")

    lines.append("Полный отчёт: `analytics/report.md` в репозитории.")
    lines.append("Команды: /analytics · /suggested · /queue · /help")
    return "\n".join(lines)


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
    args = parser.parse_args()

    try:
        analytics = collect_analytics(lang=args.lang)
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
