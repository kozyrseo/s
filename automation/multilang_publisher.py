"""
KOZYR — Мультиязычный публикатор.

Публикует статью на всех языках, для которых есть готовая версия в
_pending_{lang}/{slug}/. Проставляет корректные hreflang между версиями,
обновляет sitemap для всех, публикует одновременно.

Вызов:
    python multilang_publisher.py --slug foo-bar
    # ↑ найдёт все _pending*/foo-bar/, опубликует все языки

    python multilang_publisher.py --slug foo-bar --langs ru,uk
    # ↑ явное указание языков

Логика:
1. Собираем список всех pending-версий этой статьи по всем LANG_CONFIG.
2. Строим translation_map: {"ru": "foo-bar", "uk": "foo-bar"}. Обычно
   слуги одинаковые, но берём как есть на случай override.
3. Для каждой версии обновляем meta.json.translation_of, чтобы
   publish.publish_article (существующий) выставил hreflang корректно.
4. Вызываем publish_article(slug, lang) поочерёдно.
5. Если один язык упал (например Sheets недоступен) — остальные всё равно
   публикуются, ошибка логируется.
6. В конце шлём в TG «✅ Опубликовано: RU + UK» с ссылками.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lang_config import LANG_CONFIG, get_cfg, canonical_url_for


def discover_pending_versions(slug: str) -> dict[str, Path]:
    """
    Ищет все _pending_*/{slug}/ или _pending/{slug}/ во всех известных языках.
    Возвращает: {"ru": Path("_pending/foo"), "uk": Path("_pending_uk/foo"), ...}
    """
    found = {}
    for lang, cfg in LANG_CONFIG.items():
        candidate = cfg["pending_dir"] / slug
        if candidate.exists() and (candidate / "meta.json").exists():
            found[lang] = candidate
    return found


def crosslink_translations(pending_versions: dict[str, Path]) -> None:
    """
    Для каждой версии прописывает translation_of со ссылками на все
    остальные версии — это то, что publish.publish_article использует
    для генерации hreflang-блока и переключателя языков.

    Даже если translator.py уже это сделал (он проставляет пары), здесь
    мы гарантируем что ВСЕ версии знают обо ВСЕХ остальных — на случай
    если публикуется 3+ языка.
    """
    slug_map = {lang: pending_dir.name for lang, pending_dir in pending_versions.items()}

    for lang, pending_dir in pending_versions.items():
        meta_path = pending_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # Начинаем с уже прописанных связей (translator.py проставляет пары,
        # и там могут быть языки, которые УЖЕ опубликованы и отсутствуют в
        # текущем pending — их нельзя терять, иначе переключатель на такой
        # язык уйдёт на дефолтную главную).
        existing = meta.get("translation_of")
        translation_of = dict(existing) if isinstance(existing, dict) else {}

        # Дополняем/обновляем языками из текущей публикации.
        for other in slug_map:
            if other != lang:
                translation_of[other] = slug_map[other]

        meta["translation_of"] = translation_of
        # Явно проставляем lang в meta, чтобы publish.py не пришлось
        # догадываться (у него была legacy-логика auto-detect).
        meta["lang"] = lang

        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"🔗 Cross-linked {lang}: translation_of = {translation_of}")


def publish_all_languages(pending_versions: dict[str, Path]) -> dict[str, dict]:
    """
    Публикует каждый язык через существующий publish.publish_article.
    Возвращает {"ru": {"status": "ok", "url": "..."}, "uk": {...}}.
    """
    from publish import publish_article

    results = {}
    for lang, pending_dir in pending_versions.items():
        slug = pending_dir.name
        print(f"\n🚀 Публикую {lang}: {slug}")
        try:
            code = publish_article(slug, cli_lang=lang)
            if code != 0:
                results[lang] = {"status": "error", "code": code}
                continue
            url = canonical_url_for(lang, slug)
            results[lang] = {"status": "ok", "slug": slug, "url": url}
            print(f"✅ {lang}: {url}")
        except Exception as e:
            print(f"❌ Публикация {lang} упала: {type(e).__name__}: {e}")
            results[lang] = {"status": "error", "error": str(e)[:300]}
    return results


def send_multilang_published_notification(slug: str, results: dict[str, dict]) -> None:
    """Шлёт в TG одно сообщение с итогами публикации всех языков."""
    import os
    import urllib.request
    import urllib.error

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ℹ️  Telegram выключен, уведомление не шлём.")
        return

    lines = [f"🚀 *Статья опубликована* · `{escape_md(slug)}`"]
    lines.append("")
    for lang, res in results.items():
        lang_flag = {"ru": "🇷🇺", "uk": "🇺🇦"}.get(lang, "")
        if res.get("status") == "ok":
            lines.append(f"{lang_flag} *{lang.upper()}*: [{escape_md(res['url'])}]({res['url']})")
        else:
            err = res.get("error", res.get("code", "?"))
            lines.append(f"{lang_flag} *{lang.upper()}*: ❌ `{escape_md(str(err))}`")

    text = "\n".join(lines)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Уведомление отправлено (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️  Telegram вернул {e.code}: {body[:300]}")
    except Exception as e:
        print(f"⚠️  Не удалось отправить: {type(e).__name__}: {e}")


def escape_md(text) -> str:
    if not text:
        return ""
    s = str(text)
    for ch in ("\\", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s


# ==== Основной вызов ====

def publish_all(slug: str, langs_filter: list[str] | None = None) -> int:
    """Возвращает код выхода: 0 если все языки опубликовались, 1 если что-то упало."""
    print(f"=== Мультиязычная публикация: {slug} ===")
    versions = discover_pending_versions(slug)
    if not versions:
        print(f"❌ Не нашёл _pending*/{slug}/ ни для одного языка.", file=sys.stderr)
        return 1

    if langs_filter:
        versions = {k: v for k, v in versions.items() if k in langs_filter}
        if not versions:
            print(f"❌ После фильтра по langs={langs_filter} нет ничего к публикации.",
                  file=sys.stderr)
            return 1

    print(f"Найдено версий: {list(versions.keys())}")

    # Prepare: cross-link translations
    crosslink_translations(versions)

    # Publish each
    results = publish_all_languages(versions)

    # Notify
    send_multilang_published_notification(slug, results)

    # Return code
    all_ok = all(r.get("status") == "ok" for r in results.values())
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Multilang publish")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--langs", default="",
                        help="Опционально: только эти языки (comma-separated). "
                             "По умолчанию — все найденные pending-версии.")
    args = parser.parse_args()

    langs_filter = [x.strip() for x in args.langs.split(",") if x.strip()] or None
    return publish_all(args.slug, langs_filter=langs_filter)


if __name__ == "__main__":
    sys.exit(main())
