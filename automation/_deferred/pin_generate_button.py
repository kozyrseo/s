"""
Setup / refresh script for the pinned dashboard message in the article-review chat.

The pinned message acts as a live dashboard with three blocks:
  - Queue status:    counts from Google Sheets + _pending dirs
  - SC stats (7d):   impressions, clicks, CTR, avg position (cached)
  - Schedule status: active / paused

And six action buttons:
  Row 1: [🇬🇧 Generate EN]  [🇧🇷 Generate PT]
  Row 2: [📊 Подробная статистика]  [👀 Следующие темы]
  Row 3: [⏸ Пауза / ▶️ Возобновить]  [🔄 Обновить]

Usage:
    # Initial setup (creates new pinned message)
    python automation/pin_generate_button.py --setup

    # Refresh existing pinned message (called by dashboard-action.yml workflow)
    python automation/pin_generate_button.py --refresh
    
    # Refresh and override message_id (in case the file got out of sync)
    python automation/pin_generate_button.py --refresh --message-id 12345

State file:
    automation/.pinned_message_state.json   stores message_id so refresh
    knows which message to edit. Committed to repo so workflows can read it.

Env vars:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — required
    GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEETS_ID — for queue counts
    SC_PROPERTY_URL — Search Console property (e.g. https://pokernetai.com/),
                      optional. If not set, SC block is omitted from pinned.
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

# Optional imports — pin works without them, just with less info
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# State file location (committed to repo)
STATE_FILE = Path(__file__).parent / ".pinned_message_state.json"

# Path relative to repo root for schedule pause flag
SCHEDULE_PAUSED_FLAG = Path(__file__).parent.parent / ".schedule_paused"

# Path to cached SC stats (refreshed by stats workflow)
SC_STATS_CACHE = Path(__file__).parent / ".sc_stats_cache.json"

# Path to pending dirs (relative to repo root)
PENDING_EN = Path(__file__).parent.parent / "_pending"
PENDING_PT = Path(__file__).parent.parent / "_pending_pt"
PENDING_ZH = Path(__file__).parent.parent / "_pending_zh"


def tg_api(token: str, method: str, payload: dict) -> dict:
    """POST to Telegram Bot API and return parsed JSON response."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} HTTP {e.code}: {body}") from e


# ==== Data collection ====

def get_queue_counts() -> dict:
    """Read Google Sheets and count queued/published per language.
    Returns dict with EN and PT counts. Returns empty dict on any error."""
    if not HAS_GSPREAD:
        return {}
    try:
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet_id = os.environ.get("GOOGLE_SHEETS_ID")
        if not creds_json or not sheet_id:
            return {}
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id).sheet1
        records = sheet.get_all_records()
        
        counts = {
            "en_queued": 0, "en_published": 0, "en_rejected": 0,
            "pt_queued": 0, "pt_published": 0, "pt_rejected": 0,
            "zh_queued": 0, "zh_published": 0, "zh_rejected": 0,
        }
        for row in records:
            status = str(row.get("status", "")).strip().lower()
            lang = str(row.get("lang", "")).strip().lower() or "en"
            key = f"{lang}_{status}"
            if key in counts:
                counts[key] += 1
        return counts
    except Exception as e:
        print(f"⚠️  get_queue_counts failed: {e}", file=sys.stderr)
        return {}


def count_pending_articles() -> dict:
    """Count articles waiting in _pending/, _pending_pt/ and _pending_zh/."""
    counts = {"en_pending": 0, "pt_pending": 0, "zh_pending": 0}
    if PENDING_EN.exists():
        counts["en_pending"] = sum(
            1 for d in PENDING_EN.iterdir()
            if d.is_dir() and (d / "body.md").exists()
        )
    if PENDING_PT.exists():
        counts["pt_pending"] = sum(
            1 for d in PENDING_PT.iterdir()
            if d.is_dir() and (d / "body.md").exists()
        )
    if PENDING_ZH.exists():
        counts["zh_pending"] = sum(
            1 for d in PENDING_ZH.iterdir()
            if d.is_dir() and (d / "body.md").exists()
        )
    return counts


def get_sc_stats_cached() -> dict | None:
    """Read cached Search Console stats. Returns None if file missing or
    older than 24 hours (stale)."""
    if not SC_STATS_CACHE.exists():
        return None
    try:
        with open(SC_STATS_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        cached_at_str = data.get("cached_at", "")
        cached_at = datetime.fromisoformat(cached_at_str)
        age = datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)
        if age.total_seconds() > 86400:  # 24h
            return None
        return data
    except Exception:
        return None


def is_schedule_paused() -> bool:
    return SCHEDULE_PAUSED_FLAG.exists()


def estimate_next_publish_eta(queued_count: int, lang: str) -> str:
    """Rough estimate of when the next article in this language will be
    generated, given the alternating day-of-year cron schedule."""
    if queued_count == 0:
        return "очередь пуста"
    # Cron alternates EN/PT by day-of-year parity, so each language runs
    # every other day on average. With one article per day total.
    days = queued_count * 2
    if days == 2:
        return "через 2 дня"
    elif days == 4:
        return "через 4 дня"
    elif days <= 14:
        return f"через {days} дней"
    else:
        return f"~{days // 7} недель"


# ==== Pinned message building ====

PINNED_HEADER = "🛠 *Управление статьями PokerNet*"


def build_pinned_text() -> str:
    """Compose the live pinned message text from current data."""
    lines = [PINNED_HEADER, ""]
    
    # Queue block
    counts = get_queue_counts()
    pending = count_pending_articles()
    
    if counts:
        en_q = counts.get("en_queued", 0)
        pt_q = counts.get("pt_queued", 0)
        zh_q = counts.get("zh_queued", 0)
        en_p = counts.get("en_published", 0)
        pt_p = counts.get("pt_published", 0)
        zh_p = counts.get("zh_published", 0)
        en_pending = pending["en_pending"]
        pt_pending = pending["pt_pending"]
        zh_pending = pending.get("zh_pending", 0)
        
        lines.append("📊 *Очередь:*")
        lines.append(
            f"🇬🇧 EN: {en_q} в queued · "
            f"⏳ {en_pending} на ревью · ✅ {en_p} published"
        )
        lines.append(
            f"🇧🇷 PT: {pt_q} в queued · "
            f"⏳ {pt_pending} на ревью · ✅ {pt_p} published"
        )
        lines.append(
            f"🇨🇳 ZH: {zh_q} в queued · "
            f"⏳ {zh_pending} на ревью · ✅ {zh_p} published"
        )
        if en_q > 0:
            lines.append(f"  EN следующая: {estimate_next_publish_eta(en_q, 'en')}")
        if pt_q > 0:
            lines.append(f"  PT следующая: {estimate_next_publish_eta(pt_q, 'pt')}")
        if zh_q > 0:
            lines.append(f"  ZH следующая: {estimate_next_publish_eta(zh_q, 'zh')}")
    else:
        lines.append("📊 *Очередь:* недоступно (нет доступа к Google Sheets)")
    
    lines.append("")
    
    # Search Console block (cached)
    sc = get_sc_stats_cached()
    if sc:
        lines.append("📈 *За 7 дней (Google Search Console):*")
        impressions = sc.get("impressions", 0)
        clicks = sc.get("clicks", 0)
        ctr = sc.get("ctr_percent", 0.0)
        position = sc.get("avg_position", 0.0)
        lines.append(
            f"👁 {impressions:,} показов · 🖱 {clicks} кликов · "
            f"📊 CTR {ctr:.1f}% · 🎯 поз. {position:.1f}"
        )
    else:
        lines.append("📈 *Search Console:* данные обновятся вечером")
    
    lines.append("")
    
    # Schedule status
    if is_schedule_paused():
        lines.append("⚙️ *Расписание:* ⏸ На паузе")
    else:
        lines.append("⚙️ *Расписание:* ▶️ Активно (ежедневно)")
    
    # Timestamp
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    lines.append("")
    lines.append(f"🕐 _Обновлено: {now_msk.strftime('%d %B, %H:%M')} МСК_")
    
    return "\n".join(lines)


def build_pinned_keyboard() -> list:
    """Compose the 6-button inline keyboard."""
    pause_button = (
        {"text": "▶️ Возобновить", "callback_data": "schedule:resume"}
        if is_schedule_paused()
        else {"text": "⏸ Пауза", "callback_data": "schedule:pause"}
    )
    return [
        # Row 1: generate
        [
            {"text": "🇬🇧 Generate EN", "callback_data": "force_generate:en"},
            {"text": "🇧🇷 Generate PT", "callback_data": "force_generate:pt"},
            {"text": "🇨🇳 Generate ZH", "callback_data": "force_generate:zh"},
        ],
        # Row 2: info
        [
            {"text": "📊 Подробная статистика", "callback_data": "stats:detailed"},
            {"text": "👀 Следующие темы", "callback_data": "queue:peek"},
        ],
        # Row 3: control
        [
            pause_button,
            {"text": "🔄 Обновить", "callback_data": "pinned:refresh"},
        ],
    ]


# ==== State management ====

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ==== Telegram operations ====

def unpin_all(token: str, chat_id: str) -> None:
    try:
        result = tg_api(token, "unpinAllChatMessages", {"chat_id": chat_id})
        if result.get("ok"):
            print("ℹ️  Unpinned all previously pinned messages")
    except Exception as e:
        print(f"⚠️  unpinAllChatMessages failed (non-fatal): {e}")


def send_pinned(token: str, chat_id: str, text: str, keyboard: list) -> int:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    result = tg_api(token, "sendMessage", payload)
    if not result.get("ok"):
        raise RuntimeError(f"sendMessage failed: {result}")
    return result["result"]["message_id"]


def edit_pinned(token: str, chat_id: str, message_id: int,
                text: str, keyboard: list) -> bool:
    """Edit an existing pinned message. Returns True on success."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }
    try:
        result = tg_api(token, "editMessageText", payload)
        return bool(result.get("ok"))
    except RuntimeError as e:
        # "message is not modified" — content unchanged, that's OK
        if "message is not modified" in str(e).lower():
            print("ℹ️  Pinned message content unchanged (skipped edit)")
            return True
        # Message gone (deleted from chat) — need to create new one
        if "message to edit not found" in str(e).lower():
            print("⚠️  Pinned message no longer exists — will create new one")
            return False
        raise


def pin_message(token: str, chat_id: str, message_id: int) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": True,
    }
    result = tg_api(token, "pinChatMessage", payload)
    if not result.get("ok"):
        raise RuntimeError(f"pinChatMessage failed: {result}")


# ==== Main flow ====

def cmd_setup(token: str, chat_id: str) -> int:
    """Create a fresh pinned message. Unpins all old ones first."""
    print(f"Setup mode: creating new pinned message in chat {chat_id}")
    unpin_all(token, chat_id)
    
    text = build_pinned_text()
    keyboard = build_pinned_keyboard()
    
    try:
        message_id = send_pinned(token, chat_id, text, keyboard)
        print(f"✅ Sent message_id={message_id}")
    except Exception as e:
        print(f"❌ Failed to send: {e}", file=sys.stderr)
        return 1
    
    try:
        pin_message(token, chat_id, message_id)
        print(f"✅ Pinned message_id={message_id}")
    except Exception as e:
        print(f"⚠️  Could not pin (sent but unpinned): {e}", file=sys.stderr)
    
    # Save state
    save_state({
        "message_id": message_id,
        "chat_id": chat_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"✅ Saved state to {STATE_FILE}")
    return 0


def cmd_refresh(token: str, chat_id: str, override_msg_id: int | None) -> int:
    """Refresh existing pinned message. Falls back to setup if state missing."""
    state = load_state()
    message_id = override_msg_id or state.get("message_id")
    
    if not message_id:
        print("ℹ️  No pinned message state found — falling back to setup mode")
        return cmd_setup(token, chat_id)
    
    print(f"Refresh mode: editing message_id={message_id}")
    
    text = build_pinned_text()
    keyboard = build_pinned_keyboard()
    
    success = edit_pinned(token, chat_id, message_id, text, keyboard)
    if not success:
        # Message was deleted — recreate
        print("Recreating pinned message...")
        return cmd_setup(token, chat_id)
    
    print(f"✅ Refreshed message_id={message_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pinned dashboard management")
    parser.add_argument("--setup", action="store_true",
                        help="Create a fresh pinned message (unpins old)")
    parser.add_argument("--refresh", action="store_true",
                        help="Edit existing pinned with fresh content")
    parser.add_argument("--message-id", type=int, default=None,
                        help="Override message_id (rarely needed)")
    args = parser.parse_args()
    
    if not (args.setup or args.refresh):
        # Default: refresh (matches what dashboard workflow needs)
        args.refresh = True
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("❌ Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1
    
    if args.setup:
        return cmd_setup(token, chat_id)
    else:
        return cmd_refresh(token, chat_id, args.message_id)


if __name__ == "__main__":
    sys.exit(main())
