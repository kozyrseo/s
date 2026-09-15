"""
reposters/blogger.py — публикация статей на Blogger (blogspot.com).

МУЛЬТИБЛОГ: одна площадка Blogger может постить в РАЗНЫЕ блоги (украинский,
польский...) — блог выбирается по языку статьи из repost_blogs.json. Это
позволяет держать блог-донор на каждый рынок (релевантность для SEO).

Авторизация: OAuth refresh_token (получен один раз через Playground). Из него
на каждый запуск добывается свежий access_token. Секреты в окружении:
  BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN

Реестр блогов (repost_blogs.json):
  { "ua": {"blog_id": "...", "blog_url": "...", "langs": ["ru","uk"]}, ... }
Для публикации нужен blog_id — его добываем по blog_url через API (или задаём
в конфиге напрямую).
"""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.parse
from pathlib import Path

from .base import Reposter, RepostResult

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_API = "https://www.googleapis.com/blogger/v3"
BLOGS_CONFIG = Path(__file__).resolve().parent.parent / "data" / "repost_blogs.json"


def _get_access_token() -> str:
    """Обменивает refresh_token на свежий access_token."""
    cid = os.environ.get("BLOGGER_CLIENT_ID")
    secret = os.environ.get("BLOGGER_CLIENT_SECRET")
    refresh = os.environ.get("BLOGGER_REFRESH_TOKEN")
    if not all([cid, secret, refresh]):
        raise RuntimeError("BLOGGER_CLIENT_ID/SECRET/REFRESH_TOKEN не заданы")

    data = urllib.parse.urlencode({
        "client_id": cid,
        "client_secret": secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(TOKEN_URL, data=data, timeout=20).read())
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(f"Не удалось получить access_token: {resp}")
    return token


def _load_blogs() -> dict:
    """Реестр блогов-доноров из repost_blogs.json."""
    if not BLOGS_CONFIG.exists():
        return {}
    try:
        data = json.loads(BLOGS_CONFIG.read_text(encoding="utf-8"))
        return data.get("blogs", {})
    except Exception:
        return {}


def _blog_for_lang(lang: str) -> dict | None:
    """Находит блог-донор, принимающий данный язык статьи.
    Возвращает конфиг блога {blog_id, blog_url, langs, ...} или None."""
    for market, cfg in _load_blogs().items():
        if lang in (cfg.get("langs") or []):
            return cfg
    return None


def _resolve_blog_id(blog_cfg: dict, access_token: str) -> str:
    """blog_id из конфига, или добываем по blog_url через API (и кешируем)."""
    if blog_cfg.get("blog_id"):
        return blog_cfg["blog_id"]
    url = blog_cfg.get("blog_url", "")
    if not url:
        raise RuntimeError("В конфиге блога нет ни blog_id, ни blog_url")
    if not url.startswith("http"):
        url = "https://" + url
    api = f"{BLOGGER_API}/blogs/byurl?url={urllib.parse.quote(url, safe='')}"
    req = urllib.request.Request(api, headers={"Authorization": f"Bearer {access_token}"})
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    bid = resp.get("id")
    if not bid:
        raise RuntimeError(f"Не удалось получить blog_id для {url}: {resp}")
    return bid


class BloggerReposter(Reposter):
    key = "blogger"
    name = "Blogger"

    def is_configured(self) -> bool:
        # Настроена, если есть OAuth-секреты И хотя бы один блог в реестре
        secrets_ok = all(os.environ.get(k) for k in
                         ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET",
                          "BLOGGER_REFRESH_TOKEN"))
        return secrets_ok and bool(_load_blogs())

    def publish(self, *, title: str, content_html: str, author_name: str,
                author_url: str, canonical_url: str, lang: str) -> RepostResult:
        try:
            # 1. Выбираем блог-донор по языку статьи
            blog_cfg = _blog_for_lang(lang)
            if not blog_cfg:
                return RepostResult(ok=False,
                                    error=f"нет блога-донора для языка {lang}")

            # 2. Свежий access_token из refresh_token
            token = _get_access_token()

            # 3. blog_id
            blog_id = _resolve_blog_id(blog_cfg, token)

            # 4. Публикуем пост (Blogger API posts.insert)
            body = {
                "kind": "blogger#post",
                "title": title,
                "content": content_html,  # Blogger принимает HTML напрямую
            }
            # метки, если заданы в конфиге блога
            if blog_cfg.get("labels"):
                body["labels"] = blog_cfg["labels"]

            api = f"{BLOGGER_API}/blogs/{blog_id}/posts/"
            req = urllib.request.Request(
                api, data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            post_url = resp.get("url", "")
            if post_url:
                return RepostResult(ok=True, url=post_url)
            return RepostResult(ok=False, error=f"пост создан, но нет url: {resp}")

        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:300]
            return RepostResult(ok=False, error=f"HTTP {e.code}: {detail}")
        except Exception as e:
            return RepostResult(ok=False, error=str(e))
