# PokerNet AI — Telegram autopost

Daily 1-post-per-day generator for the PokerNet AI Telegram channel. Generates
operator-focused content with soft sales mechanics, sends a **bilingual preview
(RU for review, EN for the channel)** to your review chat with inline buttons,
posts the English version to the channel on approval.

## Bilingual flow (RU review → EN channel)

```
Claude generates EN post (operator voice, full vocabulary, soft-sell)
         ↓
Claude translates EN → RU (faithful, preview only)
         ↓
Preview lands in your review chat:
   📷 image
   🇷🇺 RU body (you read this to decide)
   ━━━━━━━━━
   🇬🇧 EN body + footer (exact channel post, for spot-checking terms)
   [✅ Publish] [🔄 Regenerate] [❌ Reject]
         ↓
Tap Publish  → English version + image hits the channel
              → posted_log.json updated, slug archived
```

You read 90% RU (fast), spot-check 10% EN for terminology, decide. The Russian
version is **never posted** — it exists only to speed up your review.

## What gets posted

- **Mon-Sat:** rotating mix of insight / pain-point / platform-comparison /
  case-study / checklist / news-take posts. ~80% editorial, ~15% bridge to
  solution category, ~5% soft brand mention. Always with a manager-handle
  footer line (rotated across 6 variants).
- **Sun:** longform_link teaser pointing to your existing blog.
- **Every ~10 days:** explicit pitch post (overrides the day's scheduled type).

## First-time setup

### 1. Set the manager handle

Open `automation/tg_autopost/config.json` and replace `@manager_username` with
the actual handle (e.g. `@pokernet_sales`).

### 2. Create the Telegram bot

1. Talk to `@BotFather` → `/newbot` → save the bot token.
2. Create your channel (or use an existing one). Add the bot as **admin**
   with "Post messages" permission. **This is the most common first-run
   failure** — without admin rights the bot can't post.
3. Get the channel's numeric ID. Easiest path:
   - Forward any message from the channel to `@userinfobot`, it will reply
     with the chat ID (e.g. `-1001234567890`).
4. Get your personal review chat ID. Send `/start` to your bot from your
   personal account, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — find `chat.id`.

### 3. Add GitHub secrets

In your repo settings → Secrets and variables → Actions, add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `OPENAI_API_KEY` | OpenAI key with image gen access |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_CHAT_ID` | Your personal chat ID (where previews go) |
| `TELEGRAM_CHANNEL_ID` | Channel chat ID (where approved posts go) |

### 4. Generate the pinned post

```bash
cd automation/tg_autopost
python autopost.py generate-pinned
```

This drops a preview in your review chat. Copy the body, paste it into your
channel as a new post, then long-press → Pin. Image is in
`_pending_tg/_pinned/pinned.webp`.

### 5. Smoke test the daily flow

Manually trigger the generation workflow from GitHub Actions UI
(`TG Autopost - Daily Generate` → Run workflow). Within ~30 seconds you
should get a preview in your review chat with three buttons.

Tap **✅ Publish** — within ~5 minutes (callback handler poll interval) the
post will appear in the channel.

## Daily flow after setup

```
09:00 UTC       cron: TG Autopost - Daily Generate
                  → Claude generates post + image
                  → preview to your review chat with buttons
                  → commits _pending_tg/{slug}/ to repo

You see preview in TG, decide:
  ✅ Publish      → callback handler picks up within 5 min
                  → autopost.py publish --slug ... runs
                  → post + image goes to channel
                  → posted_log.json updated, slug archived

  🔄 Regenerate   → callback handler picks up
                  → rejects current slug, runs generate again
                  → new preview in review chat

  ❌ Reject       → silently drops the slug, no post that day
```

## Local testing

```bash
cd automation/tg_autopost
pip install -r ../requirements.txt
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export TELEGRAM_CHANNEL_ID=...

# Generate today's post (sends preview to TELEGRAM_CHAT_ID)
python autopost.py generate

# Force a pitch post (override schedule)
python autopost.py generate --force-pitch

# Generate the pinned post (one-off)
python autopost.py generate-pinned

# Manually publish a specific pending slug
python autopost.py publish --slug 2026-05-05-some-slug
```

## File layout

```
automation/tg_autopost/
├── autopost.py              # main entry point
├── callback_handler.py      # poller for inline button taps
├── telegram_client.py       # raw urllib Telegram API wrapper
├── image_gen.py             # gpt-image-1 → WebP, poker style guard
├── repeat_log.py            # anti-repeat angle storage
├── config.json              # manager handle, channel ids, footers, schedule, languages
├── topic_banks.json         # operator pains / formats / platforms pools
├── posted_log.json          # auto-managed: last 60 angles + dates (gitted)
├── .last_update_id          # auto-managed: callback offset (gitted)
└── prompts/
    ├── post_system_prompt.md       # daily post writer instructions (EN)
    ├── pinned_system_prompt.md     # pinned post writer instructions (EN)
    └── translate_system_prompt.md  # EN → RU faithful translator (preview only)

_pending_tg/                          # repo root, gitted, auto-managed
├── 2026-05-05-some-slug/             # generated, awaiting your decision
│   ├── post.json
│   └── hero.webp
├── _pinned/                          # pinned post staging
├── _archive/                         # successfully published, kept for debugging
└── _rejected/                        # rejected/regenerated, kept for debugging
```

## Tweaking content style

The single highest-leverage file is
`prompts/post_system_prompt.md`. If you want posts to feel different — more
technical, less promotional, different tone — edit that file. Everything else
flows from it.

To change footer rotation: edit `config.json` → `footer_variants`.

To change post-type schedule: edit `config.json` → `post_schedule` (Sun=0
through Sat=6).

To change pitch frequency: edit `config.json` →
`post_schedule.pitch_post_every_n_days` (default 10).

## Adding more channel languages later

The current setup is **EN channel + RU review** (what you specified). To add
e.g. a Spanish channel, the path is:

1. Duplicate `automation/tg_autopost/` → `automation/tg_autopost_es/`
2. In the new copy's `config.json`, set `languages.channel_lang: "es"` and
   point `TELEGRAM_CHANNEL_ID` to the Spanish channel
3. Translate the **voice section** of `post_system_prompt.md` to Spanish,
   plus translate the `footer_variants` in `config.json`. Structural rules
   (length, 80/15/5 split, anti-repeat) stay identical.
4. Add a parallel GitHub Actions workflow for that language

Images are language-agnostic by design (zero text rendered on image). Same
`gpt-image-1` call. You can even share images across channels by hashing the
prompt and reusing — out of scope for this MVP but trivial to add.

**Why not just translate the EN post into the new language at runtime?** You
*could*, but the soft-sell mechanics and operator vocabulary calibrate
differently per language. A direct translation of EN content into Portuguese
will read as "translated" — non-native cadence, off-tone idioms — which kills
trust with native operator audiences. Generating natively in each language is
an extra ~$0.04/lang/day, which is nothing.

## Cost ballpark

- Claude Sonnet 4.5 (post generation): ~$0.04/post
- Claude Sonnet 4.5 (RU translation): ~$0.02/post (shorter context, output-only)
- gpt-image-1 medium quality: ~$0.04/image
- **Total: ~$0.10/day, ~$3/month**

The translation pass is intentionally a separate API call (not bundled with
generation). This costs ~$0.02 more per post but produces measurably better
EN posts: when Claude is asked to write EN+RU in one go it tends to flatten
both versions toward a generic shared register. Two passes lets the EN
post fully use operator vocabulary without compromise, and the RU translation
faithfully preserves all the technical terms.

## Troubleshooting

**"Bot can't initiate conversation with a user"** → Send `/start` to your bot
first from your personal account.

**"Chat not found"** → Wrong `TELEGRAM_CHAT_ID`. Use the numeric ID, not
`@username`.

**"Forbidden: bot is not a member of the channel"** → Add the bot as channel
admin with "Post messages" permission.

**Caption too long error** → The fallback `send_photo_with_long_text` should
handle this. If it still fires, your post body went over ~3000 chars (rare —
the prompt caps at 380 words).

**Buttons don't respond** → Check the callback handler workflow ran.
GitHub Actions free tier sometimes throttles `*/5` cron. Manually run
`TG Autopost - Callback Handler` to test.

**posted_log.json conflicts on push** → Two workflows tried to commit at
the same time. Re-run the second one — `concurrency` block on the callback
handler should prevent it but the daily-generate doesn't have it. If this
becomes frequent, add the same concurrency block to the generate workflow.
