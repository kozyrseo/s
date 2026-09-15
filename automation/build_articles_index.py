"""
build_articles_index.py — индекс опубликованных статей для команды /repost.

Собирает список статей с сайта (slug + заголовок + дата + языки) в
.bot_state/cache/articles_index.json. Бот читает этот индекс для быстрого показа
списка в /repost (не парся HTML каждой статьи на лету).

Запускается:
  • после публикации статьи (в конце multilang_publisher) — индекс свежий
  • или отдельно/по cron для актуализации

Читает ua/blog/*/index.html (ru) — берёт заголовок из <h1>, дату из meta.
Проверяет наличие uk-версии (ua/uk/blog/{slug}/).
"""
from __future__ import annotations
import re
import json
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = REPO_ROOT / "ua" / "blog"
UK_BLOG_DIR = REPO_ROOT / "ua" / "uk" / "blog"
INDEX_PATH = REPO_ROOT / ".bot_state" / "cache" / "articles_index.json"

# Папки-не-статьи в ua/blog
_SKIP = {"authors", "logos"}


def _title_from_html(html: str) -> str:
    """Заголовок статьи из <h1> (или <title> как фолбэк)."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    m = re.search(r"<title>([^<|]*)", html)
    return m.group(1).strip() if m else ""


def _date_from_html(html: str) -> str:
    """Дата публикации из datePublished (schema) или пусто."""
    m = re.search(r'"datePublished"\s*:\s*"([^"]*)"', html)
    return m.group(1)[:10] if m else ""


def build_index() -> dict:
    """Строит индекс статей. Возвращает {articles: [...], built_at}."""
    articles = []
    if not BLOG_DIR.exists():
        return {"articles": [], "built_at": datetime.now(timezone.utc).isoformat()}

    for d in sorted(BLOG_DIR.iterdir()):
        if not d.is_dir() or d.name in _SKIP:
            continue
        index_file = d / "index.html"
        if not index_file.exists():
            continue
        slug = d.name
        try:
            html = index_file.read_text(encoding="utf-8")
        except Exception:
            continue
        title = _title_from_html(html)
        date = _date_from_html(html)
        has_uk = (UK_BLOG_DIR / slug / "index.html").exists()
        articles.append({
            "slug": slug,
            "title": title or slug,
            "date": date,
            "langs": (["ru", "uk"] if has_uk else ["ru"]),
        })

    # сортируем по дате (свежие первыми); без даты — в конец
    articles.sort(key=lambda a: a.get("date") or "0000", reverse=True)

    return {
        "articles": articles,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "count": len(articles),
    }


def save_index() -> int:
    """Строит и сохраняет индекс. Возвращает число статей."""
    data = build_index()
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return data["count"]


if __name__ == "__main__":
    n = save_index()
    print(f"✅ Индекс статей построен: {n} статей → {INDEX_PATH.relative_to(REPO_ROOT)}")
