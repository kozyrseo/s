"""
Test script to verify all integrations are working:
1. Anthropic API
2. Google Sheets
3. Telegram bot
"""
import os
import json
import sys
import requests
from anthropic import Anthropic
import gspread
from google.oauth2.service_account import Credentials


def send_telegram_message(text: str) -> None:
    """Send message to Telegram chat."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    response.raise_for_status()


def test_anthropic() -> str:
    """Test Anthropic API by making a simple request."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=50,
        messages=[
            {"role": "user", "content": "Reply with exactly: OK"}
        ],
    )
    reply = message.content[0].text.strip()
    return f"Anthropic: ✅ Connected (reply: {reply!r})"


def test_google_sheets() -> str:
    """Test Google Sheets by reading the topics table."""
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    rows = worksheet.get_all_values()
    topic_count = len(rows) - 1  # minus header row
    first_topic = rows[1][1] if len(rows) > 1 else "(empty)"
    return (
        f"Google Sheets: ✅ Connected\n"
        f"  Rows in sheet: {len(rows)}\n"
        f"  Topics ready: {topic_count}\n"
        f"  First topic: {first_topic}"
    )


def main() -> int:
    results = []
    has_errors = False

    # Test 1: Anthropic
    try:
        results.append(test_anthropic())
    except Exception as e:
        results.append(f"Anthropic: ❌ FAILED — {type(e).__name__}: {e}")
        has_errors = True

    # Test 2: Google Sheets
    try:
        results.append(test_google_sheets())
    except Exception as e:
        results.append(f"Google Sheets: ❌ FAILED — {type(e).__name__}: {e}")
        has_errors = True

    # Build summary message
    header = "✅ All systems connected" if not has_errors else "⚠️ Some issues found"
    body = "\n\n".join(results)
    summary = f"*Connection test*\n\n{header}\n\n```\n{body}\n```"

    # Test 3: Telegram (if this fails, we won't get the message at all)
    try:
        send_telegram_message(summary)
        print("Telegram: ✅ Message sent")
    except Exception as e:
        print(f"Telegram: ❌ FAILED — {type(e).__name__}: {e}", file=sys.stderr)
        has_errors = True

    # Print to GitHub Actions log
    print(summary)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
