"""
repost_orchestrator.py — оркестратор репостов на внешние площадки.

Твоя модель:
  • Жмёшь «📡 Репост Telegraph» → UK-версия постится СРАЗУ (целевой язык)
  • RU-версия ставится в ОЧЕРЕДЬ (публикуется автоматически через 3-5 часов)
  • cron-workflow разбирает очередь раз в час → постит что пора

Расширяемо: работает с любой площадкой из реестра reposters (Telegraph сейчас,
Blogger потом). Ведёт лог (что куда запощено) и очередь (отложенные RU).

CLI:
  python repost_orchestrator.py post --slug SLUG --platform telegraph
      → UK сразу, RU в очередь
  python repost_orchestrator.py drain
      → разобрать очередь (для cron): опубликовать RU, у которых время пришло
"""
from __future__ import annotations
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reposters import get_reposter                      # noqa: E402
from reposters.content_rewriter import rewrite_for_platform  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BOT_STATE = REPO_ROOT / ".bot_state"
QUEUE_PATH = BOT_STATE / "repost_queue.json"
LOG_PATH = BOT_STATE / "repost_log.json"

SITE = "https://kozyr.club"
AUTHOR_NAME = "Никита Волошин"
AUTHOR_URL = f"{SITE}/ua/blog/authors/nikita/"

# Задержка второго языка (RU) — 3-5 часов. Берём середину: 4ч (можно из env).
SECOND_LANG_DELAY_HOURS = float(os.environ.get("REPOST_DELAY_HOURS", "4"))

# Приоритет: UK сразу (целевой рынок), RU отложенно.
IMMEDIATE_LANG = "uk"
DELAYED_LANG = "ru"


def _url_for(slug: str, lang: str) -> str:
    """URL статьи на сайте: ru → /ua/blog/, uk → /ua/uk/blog/."""
    if lang == "uk":
        return f"{SITE}/ua/uk/blog/{slug}/"
    return f"{SITE}/ua/blog/{slug}/"


def _html_path(slug: str, lang: str) -> Path:
    """Путь к готовому HTML статьи на диске."""
    if lang == "uk":
        return REPO_ROOT / "ua" / "uk" / "blog" / slug / "index.html"
    return REPO_ROOT / "ua" / "blog" / slug / "index.html"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _already_posted(slug: str, lang: str, platform: str) -> bool:
    """Проверка лога — не постили ли уже (защита от дублей)."""
    log = _load_json(LOG_PATH, [])
    return any(e.get("slug") == slug and e.get("lang") == lang
               and e.get("platform") == platform and e.get("ok") for e in log)


def _log_repost(slug: str, lang: str, platform: str, ok: bool,
                url: str = "", error: str = ""):
    log = _load_json(LOG_PATH, [])
    log.append({
        "slug": slug, "lang": lang, "platform": platform,
        "ok": ok, "url": url, "error": error,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    _save_json(LOG_PATH, log)


def post_one(slug: str, lang: str, platform: str) -> dict:
    """Публикует ОДНУ языковую версию статьи на площадку СЕЙЧАС.
    Возвращает {ok, url, error}.
    """
    if _already_posted(slug, lang, platform):
        return {"ok": True, "url": "", "error": "already posted (skip)"}

    reposter = get_reposter(platform)
    if not reposter:
        return {"ok": False, "error": f"платформа {platform} не найдена/не настроена"}

    html_path = _html_path(slug, lang)
    if not html_path.exists():
        return {"ok": False, "error": f"HTML не найден: {html_path}"}

    article_html = html_path.read_text(encoding="utf-8")
    canonical = _url_for(slug, lang)

    # рерайт под площадку (Claude)
    try:
        rewritten = rewrite_for_platform(
            article_html, canonical, lang, reposter.name)
    except Exception as e:
        _log_repost(slug, lang, platform, False, error=f"rewrite: {e}")
        return {"ok": False, "error": f"рерайт упал: {e}"}

    # публикация на площадке
    result = reposter.publish(
        title=rewritten["title"],
        content_html=rewritten["html"],
        author_name=AUTHOR_NAME,
        author_url=AUTHOR_URL,
        canonical_url=canonical,
        lang=lang,
    )
    _log_repost(slug, lang, platform, result.ok, result.url, result.error)
    return {"ok": result.ok, "url": result.url, "error": result.error}


def enqueue_delayed(slug: str, lang: str, platform: str, delay_hours: float):
    """Ставит языковую версию в очередь на отложенную публикацию."""
    queue = _load_json(QUEUE_PATH, [])
    due = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    queue.append({
        "slug": slug, "lang": lang, "platform": platform,
        "due_at": due.isoformat(),
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_json(QUEUE_PATH, queue)


def cmd_post(slug: str, platform: str) -> dict:
    """Полный цикл по клику: UK сразу + RU в очередь.
    Возвращает отчёт для превью в боте.
    """
    report = {"slug": slug, "platform": platform, "immediate": None, "queued": None}

    # 1. UK сразу
    imm = post_one(slug, IMMEDIATE_LANG, platform)
    report["immediate"] = {"lang": IMMEDIATE_LANG, **imm}

    # 2. RU в очередь (если UK успешно; иначе не ставим, чтобы разобраться)
    if imm.get("ok"):
        enqueue_delayed(slug, DELAYED_LANG, platform, SECOND_LANG_DELAY_HOURS)
        report["queued"] = {"lang": DELAYED_LANG, "delay_hours": SECOND_LANG_DELAY_HOURS}

    return report


def cmd_drain() -> list:
    """Разбирает очередь (для cron): постит версии, у которых время пришло.
    Возвращает список результатов.
    """
    queue = _load_json(QUEUE_PATH, [])
    if not queue:
        return []

    now = datetime.now(timezone.utc)
    remaining = []
    results = []

    for item in queue:
        try:
            due = datetime.fromisoformat(item["due_at"])
        except Exception:
            due = now  # некорректная дата — постим сразу
        if due <= now:
            r = post_one(item["slug"], item["lang"], item["platform"])
            results.append({**item, **r})
        else:
            remaining.append(item)  # ещё рано

    _save_json(QUEUE_PATH, remaining)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Репост статей на внешние площадки")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_post = sub.add_parser("post", help="UK сразу + RU в очередь")
    p_post.add_argument("--slug", required=True)
    p_post.add_argument("--platform", default="telegraph")

    sub.add_parser("drain", help="разобрать очередь (cron)")

    args = ap.parse_args()

    if args.cmd == "post":
        report = cmd_post(args.slug, args.platform)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # для Telegram-уведомления
        _notify_post(report)
        return 0 if (report.get("immediate") or {}).get("ok") else 1

    if args.cmd == "drain":
        results = cmd_drain()
        print(json.dumps(results, ensure_ascii=False, indent=2))
        for r in results:
            _notify_drain(r)
        return 0

    return 1


# ─── Telegram-уведомления оператору ───
def _tg(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(text)
        return
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text[:4000],
        "parse_mode": "Markdown", "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
    except Exception as e:
        print(f"tg send failed: {e}\n{text}")


def _notify_post(report: dict):
    slug = report["slug"]
    plat = report["platform"]
    imm = report.get("immediate") or {}
    L = [f"📡 *Репост на {plat.title()}*: `{slug}`", ""]
    if imm.get("ok"):
        L.append(f"✅ UK опубликована: {imm.get('url','')}")
    else:
        L.append(f"❌ UK не удалась: {imm.get('error','')}")
    q = report.get("queued")
    if q:
        L.append(f"⏳ RU-версия в очереди — выйдет через ~{q['delay_hours']}ч автоматически.")
    _tg("\n".join(L))


def _notify_drain(r: dict):
    if r.get("ok"):
        _tg(f"✅ Отложенный репост ({r['lang']}) опубликован: {r.get('url','')}\n`{r['slug']}`")
    else:
        _tg(f"❌ Отложенный репост ({r['lang']}) не удался: {r.get('error','')}\n`{r['slug']}`")


if __name__ == "__main__":
    raise SystemExit(main())
