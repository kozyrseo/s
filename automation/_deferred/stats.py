"""
PokerNet statistics collection module.

Responsibilities:
  - Pull Search Console data via API (7-day window)
  - Cache stats to .sc_stats_cache.json (used by pinned message)
  - Format detailed reports for Telegram (top queries, top pages)
  - Format queue peek messages (next topics from Sheets)

Usage:
    # Refresh SC cache (called daily by cron, or on-demand by dashboard)
    python automation/stats.py --refresh-cache
    
    # Send detailed stats report to Telegram
    python automation/stats.py --send-detailed
    
    # Send queue peek (next topics) to Telegram
    python automation/stats.py --send-queue-peek

Env vars required:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    GOOGLE_SERVICE_ACCOUNT_JSON_SC — Service Account JSON with SC API access
    SC_PROPERTY_URL — Search Console property URL (e.g. "https://pokernetai.com/")
    
    For queue peek:
    GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEETS_ID — same as generate.py

Note: SC requires a SEPARATE service account because the existing one for
Sheets may not have SC access. Or use the same one and add it as user in
Search Console settings (recommended).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Optional imports
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

try:
    from googleapiclient.discovery import build
    HAS_GAPI = True
except ImportError:
    HAS_GAPI = False


SC_STATS_CACHE = Path(__file__).parent / ".sc_stats_cache.json"
SC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


# ==== Telegram ====

def tg_send(token: str, chat_id: str, text: str, parse_markdown: bool = True) -> None:
    """Send a Telegram message (MarkdownV1 unless plain requested)."""
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_markdown:
        payload["parse_mode"] = "Markdown"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                print(f"⚠️  Telegram sendMessage not ok: {data}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"⚠️  Telegram HTTP {e.code}: {body}", file=sys.stderr)


def escape_md(text: str) -> str:
    """Escape MarkdownV1 special chars."""
    if not text:
        return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")


# ==== Search Console ====

def get_sc_service():
    """Initialize Google Search Console API client.
    Returns None if not configured (missing creds or library)."""
    if not HAS_GAPI:
        print("⚠️  google-api-python-client not installed; SC unavailable", file=sys.stderr)
        return None
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_SC") \
              or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        print("⚠️  GOOGLE_SERVICE_ACCOUNT_JSON_SC not set", file=sys.stderr)
        return None
    try:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SC_SCOPES)
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"⚠️  SC service init failed: {e}", file=sys.stderr)
        return None


def fetch_sc_summary(service, property_url: str, days: int = 7) -> dict:
    """Fetch aggregate SC stats for the last N days.
    Returns: {impressions, clicks, ctr, position, period}"""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)
    request = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": [],  # no dims = total aggregate
        "rowLimit": 1,
    }
    resp = service.searchanalytics().query(
        siteUrl=property_url, body=request
    ).execute()
    rows = resp.get("rows", [])
    if not rows:
        return {
            "impressions": 0, "clicks": 0,
            "ctr_percent": 0.0, "avg_position": 0.0,
            "period_days": days,
        }
    row = rows[0]
    return {
        "impressions": int(row.get("impressions", 0)),
        "clicks": int(row.get("clicks", 0)),
        "ctr_percent": round(row.get("ctr", 0.0) * 100, 2),
        "avg_position": round(row.get("position", 0.0), 1),
        "period_days": days,
    }


def fetch_sc_top_queries(service, property_url: str, days: int = 7, limit: int = 10) -> list:
    """Top N queries by clicks in the last N days."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)
    request = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "rowLimit": limit,
        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
    }
    resp = service.searchanalytics().query(
        siteUrl=property_url, body=request
    ).execute()
    return [
        {
            "query": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(row.get("ctr", 0.0) * 100, 1),
            "position": round(row.get("position", 0.0), 1),
        }
        for row in resp.get("rows", [])
    ]


def fetch_sc_top_pages(service, property_url: str, days: int = 7, limit: int = 10) -> list:
    """Top N pages by clicks in the last N days."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)
    request = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["page"],
        "rowLimit": limit,
        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
    }
    resp = service.searchanalytics().query(
        siteUrl=property_url, body=request
    ).execute()
    return [
        {
            "page": row["keys"][0],
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(row.get("ctr", 0.0) * 100, 1),
            "position": round(row.get("position", 0.0), 1),
        }
        for row in resp.get("rows", [])
    ]


# ==== Queue (Google Sheets) ====

def get_sheet():
    if not HAS_GSPREAD:
        return None
    try:
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet_id = os.environ.get("GOOGLE_SHEETS_ID")
        if not creds_json or not sheet_id:
            return None
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(sheet_id).sheet1
    except Exception as e:
        print(f"⚠️  get_sheet failed: {e}", file=sys.stderr)
        return None


def peek_next_topics(limit_per_lang: int = 3) -> dict:
    """Peek at the first N queued topics for each language."""
    sheet = get_sheet()
    if not sheet:
        return {"en": [], "pt": [], "zh": []}
    records = sheet.get_all_records()
    result = {"en": [], "pt": [], "zh": []}
    for row in records:
        if str(row.get("status", "")).strip().lower() != "queued":
            continue
        lang = str(row.get("lang", "")).strip().lower() or "en"
        if lang not in result:
            continue
        if len(result[lang]) >= limit_per_lang:
            continue
        result[lang].append({
            "topic": str(row.get("topic", "")).strip(),
            "primary_keyword": str(row.get("primary_keyword", "")).strip(),
            "intent": str(row.get("intent", "")).strip(),
            "target_page": str(row.get("target_page", "")).strip(),
        })
        if all(len(result[l]) >= limit_per_lang for l in result):
            break
    return result


# ==== Cache management ====

def refresh_sc_cache() -> int:
    """Pull SC stats and cache to file. Returns exit code."""
    service = get_sc_service()
    if not service:
        print("ℹ️  SC service unavailable, skipping cache refresh")
        # Write empty cache so pinned knows we tried
        SC_STATS_CACHE.write_text(json.dumps({
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "error": "service_unavailable",
        }, indent=2))
        return 0
    
    property_url = os.environ.get("SC_PROPERTY_URL", "https://pokernetai.com/")
    
    try:
        summary = fetch_sc_summary(service, property_url, days=7)
    except Exception as e:
        print(f"❌ fetch_sc_summary failed: {e}", file=sys.stderr)
        return 1
    
    data = {
        **summary,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "property_url": property_url,
    }
    SC_STATS_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"✅ Cached SC stats: {summary['impressions']:,} impr, "
          f"{summary['clicks']} clicks, pos {summary['avg_position']:.1f}")
    return 0


# ==== Telegram message builders ====

def build_detailed_stats_message() -> str:
    """Build a detailed stats message: summary + top 10 queries + top 5 pages."""
    service = get_sc_service()
    property_url = os.environ.get("SC_PROPERTY_URL", "https://pokernetai.com/")
    
    if not service:
        return (
            "📊 *Подробная статистика*\n\n"
            "⚠️ Search Console API не настроен.\n"
            "Установите `GOOGLE_SERVICE_ACCOUNT_JSON_SC` и `SC_PROPERTY_URL`."
        )
    
    lines = [f"📊 *Статистика за 7 дней*", f"_{escape_md(property_url)}_", ""]
    
    # Summary
    try:
        summary = fetch_sc_summary(service, property_url, days=7)
        lines.append(
            f"👁 *{summary['impressions']:,}* показов · "
            f"🖱 *{summary['clicks']}* кликов · "
            f"📊 CTR *{summary['ctr_percent']:.1f}%* · "
            f"🎯 поз. *{summary['avg_position']:.1f}*"
        )
        lines.append("")
    except Exception as e:
        lines.append(f"⚠️ Summary error: {escape_md(str(e))[:200]}")
    
    # Top queries
    try:
        queries = fetch_sc_top_queries(service, property_url, days=7, limit=10)
        if queries:
            lines.append("🔍 *Топ-10 поисковых запросов:*")
            for i, q in enumerate(queries, 1):
                q_text = escape_md(q["query"])[:50]
                lines.append(
                    f"{i}. `{q_text}` — "
                    f"{q['clicks']}/{q['impressions']} · "
                    f"поз. {q['position']:.1f}"
                )
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ Queries error: {escape_md(str(e))[:200]}")
    
    # Top pages
    try:
        pages = fetch_sc_top_pages(service, property_url, days=7, limit=5)
        if pages:
            lines.append("📄 *Топ-5 страниц по кликам:*")
            for i, p in enumerate(pages, 1):
                # Trim https://pokernetai.com/ prefix for compactness
                page_path = p["page"].replace(property_url.rstrip("/"), "") or "/"
                page_path = escape_md(page_path)[:50]
                lines.append(
                    f"{i}. `{page_path}` — "
                    f"{p['clicks']}/{p['impressions']} · "
                    f"поз. {p['position']:.1f}"
                )
            lines.append("")
    except Exception as e:
        lines.append(f"⚠️ Pages error: {escape_md(str(e))[:200]}")
    
    lines.append("_Формат: clicks/impressions · средняя позиция_")
    return "\n".join(lines)


def build_queue_peek_message() -> str:
    """Build a queue peek message: next 3 topics per language."""
    topics = peek_next_topics(limit_per_lang=3)
    lines = ["👀 *Следующие темы в очереди*", ""]
    
    en_topics = topics.get("en", [])
    pt_topics = topics.get("pt", [])
    zh_topics = topics.get("zh", [])
    
    lines.append(f"🇬🇧 *EN ({len(en_topics)} ближайших):*")
    if en_topics:
        for i, t in enumerate(en_topics, 1):
            topic = escape_md(t["topic"][:80])
            kw = escape_md(t["primary_keyword"][:50])
            target = escape_md(t["target_page"][:30])
            lines.append(f"{i}. *{topic}*")
            lines.append(f"   🎯 `{kw}` → {target}")
    else:
        lines.append("_очередь пуста_")
    
    lines.append("")
    lines.append(f"🇧🇷 *PT ({len(pt_topics)} ближайших):*")
    if pt_topics:
        for i, t in enumerate(pt_topics, 1):
            topic = escape_md(t["topic"][:80])
            kw = escape_md(t["primary_keyword"][:50])
            target = escape_md(t["target_page"][:30])
            lines.append(f"{i}. *{topic}*")
            lines.append(f"   🎯 `{kw}` → {target}")
    else:
        lines.append("_очередь пуста_")
    
    lines.append("")
    lines.append(f"🇨🇳 *ZH ({len(zh_topics)} ближайших):*")
    if zh_topics:
        for i, t in enumerate(zh_topics, 1):
            topic = escape_md(t["topic"][:80])
            kw = escape_md(t["primary_keyword"][:50])
            target = escape_md(t["target_page"][:30])
            lines.append(f"{i}. *{topic}*")
            lines.append(f"   🎯 `{kw}` → {target}")
    else:
        lines.append("_очередь пуста_")
    
    return "\n".join(lines)


# ==== Commands ====

def cmd_refresh_cache() -> int:
    return refresh_sc_cache()


def cmd_send_detailed() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("❌ Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1
    msg = build_detailed_stats_message()
    tg_send(token, chat_id, msg)
    print("✅ Sent detailed stats")
    return 0


def cmd_send_queue_peek() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("❌ Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1
    msg = build_queue_peek_message()
    tg_send(token, chat_id, msg)
    print("✅ Sent queue peek")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PokerNet stats module")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Refresh .sc_stats_cache.json")
    parser.add_argument("--send-detailed", action="store_true",
                        help="Send detailed stats to Telegram")
    parser.add_argument("--send-queue-peek", action="store_true",
                        help="Send next-topics message to Telegram")
    args = parser.parse_args()
    
    if args.refresh_cache:
        return cmd_refresh_cache()
    if args.send_detailed:
        return cmd_send_detailed()
    if args.send_queue_peek:
        return cmd_send_queue_peek()
    
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
