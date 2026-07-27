"""
Telegram API client for tg_autopost.

Why a custom client instead of python-telegram-bot:
  - We make ~3 calls per post run; pulling in the full library is overkill.
  - GitHub Actions cold-start: smaller dependency tree = faster runs.
  - The blog pipeline already uses raw urllib for Telegram — we stay consistent.

What this handles:
  - sendMessage (long posts that don't fit a caption)
  - sendPhoto (photo + caption ≤1024 chars, the ideal channel post format)
  - sendMediaGroup (photo + long body — photo first, body as reply)
  - editMessageReplyMarkup (clearing buttons after action)
  - answerCallbackQuery (acknowledging button taps)

Telegram quirks worth knowing:
  - Caption limit on sendPhoto is 1024 chars INCLUDING markdown control chars.
    So a 1000-char post body + 50-char footer + bold markers can blow the limit.
    We auto-fall-back to "photo + reply" mode when over 1000 chars.
  - parse_mode='Markdown' (V1) is far more forgiving than MarkdownV2.
    V2 requires escaping: _*[]()~`>#+-=|{}.! — that's 17 characters that show
    up constantly in poker content (HU, +bb/100, 0.10/0.25, etc). We use V1.
  - Bot tokens that send to channels need the bot added as channel admin
    with "Post messages" permission. Common first-run failure.
"""

from __future__ import annotations

import json
import secrets
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


TELEGRAM_API = "https://api.telegram.org"
PHOTO_CAPTION_LIMIT = 1024
SAFE_CAPTION_LIMIT = 1000   # leave room for markdown chars
MESSAGE_LIMIT = 4096


class TelegramError(Exception):
    pass


def _post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise TelegramError(f"HTTP {e.code}: {body[:500]}") from e
    except Exception as e:
        raise TelegramError(f"{type(e).__name__}: {e}") from e


def send_message(token: str, chat_id: str, text: str,
                 keyboard: Optional[list] = None,
                 disable_preview: bool = True,
                 parse_mode: str = "Markdown") -> dict:
    """Send a plain-text message. Truncates at MESSAGE_LIMIT."""
    if len(text) > MESSAGE_LIMIT:
        text = text[:MESSAGE_LIMIT - 4] + "..."
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return _post_json(f"{TELEGRAM_API}/bot{token}/sendMessage", payload)


def send_photo(token: str, chat_id: str, photo_path: Path,
               caption: str, keyboard: Optional[list] = None,
               parse_mode: str = "Markdown") -> dict:
    """
    Send a photo with caption. If caption > SAFE_CAPTION_LIMIT, caller should
    use send_photo_with_long_text() instead.
    """
    if len(caption) > PHOTO_CAPTION_LIMIT:
        caption = caption[:PHOTO_CAPTION_LIMIT - 4] + "..."

    boundary = secrets.token_hex(16)
    crlf = "\r\n"

    fields = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if keyboard:
        fields["reply_markup"] = json.dumps({"inline_keyboard": keyboard})

    body_parts: list[bytes] = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}{crlf}".encode())
        body_parts.append(
            f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}'.encode()
        )
        body_parts.append(str(value).encode("utf-8"))
        body_parts.append(crlf.encode())

    # photo file part
    photo_bytes = photo_path.read_bytes()
    filename = photo_path.name
    mime = "image/webp" if filename.endswith(".webp") else "image/png"
    body_parts.append(f"--{boundary}{crlf}".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"{crlf}'.encode()
    )
    body_parts.append(f"Content-Type: {mime}{crlf}{crlf}".encode())
    body_parts.append(photo_bytes)
    body_parts.append(crlf.encode())
    body_parts.append(f"--{boundary}--{crlf}".encode())

    body = b"".join(body_parts)
    req = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode("utf-8", errors="ignore")
        raise TelegramError(f"HTTP {e.code}: {body_resp[:500]}") from e
    except Exception as e:
        raise TelegramError(f"{type(e).__name__}: {e}") from e


def send_photo_with_long_text(token: str, chat_id: str, photo_path: Path,
                              text: str, keyboard: Optional[list] = None,
                              parse_mode: str = "Markdown") -> dict:
    """
    For posts where body > SAFE_CAPTION_LIMIT: send the photo with no caption,
    then send the full text as a separate message. Buttons attach to the text
    message (where they're more visible anyway).

    Returns the second (text) message response — that's what gets pinned/edited.
    """
    # 1. Photo, no caption
    send_photo(token, chat_id, photo_path, caption="", parse_mode=parse_mode)
    # 2. Full text with optional keyboard
    return send_message(token, chat_id, text, keyboard=keyboard,
                        disable_preview=True, parse_mode=parse_mode)


def edit_reply_markup(token: str, chat_id: str, message_id: int,
                      keyboard: Optional[list] = None) -> dict:
    """Update or remove inline keyboard. Pass keyboard=None to remove."""
    payload: dict = {"chat_id": chat_id, "message_id": message_id}
    if keyboard is None:
        payload["reply_markup"] = {"inline_keyboard": []}
    else:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return _post_json(
        f"{TELEGRAM_API}/bot{token}/editMessageReplyMarkup", payload
    )


def answer_callback(token: str, callback_query_id: str,
                    text: str = "", show_alert: bool = False) -> dict:
    """Acknowledge a button tap so the loading spinner stops on the user's side."""
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }
    return _post_json(
        f"{TELEGRAM_API}/bot{token}/answerCallbackQuery", payload
    )


def get_updates(token: str, offset: int = 0, timeout: int = 25) -> dict:
    """Long-poll for updates. Used by the callback handler script."""
    payload = {"offset": offset, "timeout": timeout, "allowed_updates": ["callback_query"]}
    return _post_json(
        f"{TELEGRAM_API}/bot{token}/getUpdates", payload, timeout=timeout + 5
    )


# ==== Markdown V1 escaping helpers ====
# parse_mode=Markdown (V1) only requires escaping these inside content:
#   _ * ` [
# Far gentler than V2. We use this throughout.

_MD_V1_BREAKERS = ("_", "*", "`", "[")


def escape_md(text: str) -> str:
    """Escape Telegram MarkdownV1 breakers in user-supplied content."""
    if not text:
        return ""
    out = str(text)
    for ch in _MD_V1_BREAKERS:
        out = out.replace(ch, f"\\{ch}")
    return out


def bold(text: str) -> str:
    """Wrap text in MarkdownV1 bold, escaping inner content first."""
    return f"*{escape_md(text)}*"
