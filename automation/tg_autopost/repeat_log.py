"""
Anti-repeat log.

Stores the last N posted angles + post types as a JSON list. On each generation,
we feed the recent angles to Claude with explicit "do not repeat" instruction.
After successful posting, we append the new angle and trim to N.

We also store dates so the post_type rotation can detect "last pitch was X days
ago" without scanning the whole channel.

Single-file, no DB. Lives in the repo and is committed back via the GitHub Action
after each successful post. This keeps the bot fully stateless on the runner side.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILENAME = "posted_log.json"
DEFAULT_LOOKBACK = 60


def _log_path(autopost_dir: Path) -> Path:
    return autopost_dir / LOG_FILENAME


def load_log(autopost_dir: Path) -> dict:
    path = _log_path(autopost_dir)
    if not path.exists():
        return {"entries": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Don't crash on corrupted log — start fresh, log a warning
        print(f"⚠️  Corrupted posted_log.json — starting fresh")
        return {"entries": []}


def save_log(autopost_dir: Path, log: dict, lookback: int = DEFAULT_LOOKBACK) -> None:
    """Trim to lookback and write."""
    log["entries"] = log.get("entries", [])[-lookback:]
    _log_path(autopost_dir).write_text(
        json.dumps(log, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_entry(autopost_dir: Path, topic_angle: str, post_type: str,
                 lookback: int = DEFAULT_LOOKBACK) -> None:
    log = load_log(autopost_dir)
    entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "post_type": post_type,
        "topic_angle": topic_angle,
        "hash": hashlib.sha1(
            topic_angle.lower().encode("utf-8")
        ).hexdigest()[:12],
    }
    log.setdefault("entries", []).append(entry)
    save_log(autopost_dir, log, lookback=lookback)


def recent_angles(autopost_dir: Path, n: int = 30) -> list[str]:
    """Return last n topic_angle strings, newest last."""
    log = load_log(autopost_dir)
    entries = log.get("entries", [])
    return [e.get("topic_angle", "") for e in entries[-n:] if e.get("topic_angle")]


def days_since_last_pitch(autopost_dir: Path) -> int | None:
    """How many days since post_type='pitch' was last used. None if never."""
    log = load_log(autopost_dir)
    entries = log.get("entries", [])
    for entry in reversed(entries):
        if entry.get("post_type") == "pitch":
            try:
                last_dt = datetime.fromisoformat(entry["date"])
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - last_dt
                return max(0, delta.days)
            except (KeyError, ValueError):
                continue
    return None


def last_used_footer(autopost_dir: Path) -> str | None:
    """Return the last footer text used, for rotation avoidance."""
    log = load_log(autopost_dir)
    entries = log.get("entries", [])
    for entry in reversed(entries):
        footer = entry.get("footer")
        if footer:
            return footer
    return None


def append_full_entry(autopost_dir: Path, *, topic_angle: str, post_type: str,
                      footer: str, message_id: int | None = None,
                      lookback: int = DEFAULT_LOOKBACK) -> None:
    """Richer append used after successful publish."""
    log = load_log(autopost_dir)
    entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "post_type": post_type,
        "topic_angle": topic_angle,
        "footer": footer,
        "message_id": message_id,
        "hash": hashlib.sha1(
            topic_angle.lower().encode("utf-8")
        ).hexdigest()[:12],
    }
    log.setdefault("entries", []).append(entry)
    save_log(autopost_dir, log, lookback=lookback)
