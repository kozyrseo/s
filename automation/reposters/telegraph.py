"""
reposters/telegraph.py — публикация статей на Telegraph (telegra.ph).

Telegraph API проверен вживую: createAccount выдаёт access_token без
регистрации, createPage публикует статью. Токен создаётся один раз и хранится
в секрете TELEGRAPH_TOKEN (если не задан — создаётся на лету, но лучше
持ent-токен, чтобы все статьи были под одним аккаунтом "Никита Волошин").

Формат контента Telegraph — массив нод (не сырой HTML). Конвертируем HTML
статьи в ноды через html_to_nodes().
"""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.parse
from html.parser import HTMLParser

from .base import Reposter, RepostResult

TELEGRAPH_API = "https://api.telegra.ph"
# Теги, которые Telegraph поддерживает в контенте
_ALLOWED_TAGS = {"a", "b", "strong", "i", "em", "u", "s", "p", "blockquote",
                 "h3", "h4", "ul", "ol", "li", "hr", "br", "figure", "figcaption"}
# Маппинг неподдерживаемых → поддерживаемые
_TAG_MAP = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4", "strong": "b", "em": "i"}


class _HTMLToNodes(HTMLParser):
    """Конвертер HTML → Telegraph-ноды (массив вложенных объектов)."""

    def __init__(self):
        super().__init__()
        self.root = []
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = _TAG_MAP.get(tag, tag)
        if tag not in _ALLOWED_TAGS:
            return
        node = {"tag": tag}
        adict = dict(attrs)
        if tag == "a" and adict.get("href"):
            node["attrs"] = {"href": adict["href"]}
        node["children"] = []
        self.stack[-1].append(node)
        if tag not in ("br", "hr"):
            self.stack.append(node["children"])

    def handle_endtag(self, tag):
        tag = _TAG_MAP.get(tag, tag)
        if tag not in _ALLOWED_TAGS or tag in ("br", "hr"):
            return
        if len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.stack[-1].append(data)


def html_to_nodes(html: str) -> list:
    """HTML → массив Telegraph-нод."""
    parser = _HTMLToNodes()
    parser.feed(html)
    # Telegraph требует непустой контент; если пусто — оборачиваем в параграф
    return parser.root or [{"tag": "p", "children": [html]}]


class TelegraphReposter(Reposter):
    key = "telegraph"
    name = "Telegraph"

    def _token(self, author_name: str) -> str:
        """Токен Telegraph: из секрета или создаётся на лету."""
        token = os.environ.get("TELEGRAPH_TOKEN")
        if token:
            return token
        # создаём аккаунт на лету (fallback)
        data = urllib.parse.urlencode({
            "short_name": "KOZYR",
            "author_name": author_name,
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            f"{TELEGRAPH_API}/createAccount", data=data, timeout=15).read())
        if resp.get("ok"):
            return resp["result"]["access_token"]
        raise RuntimeError(f"Telegraph createAccount failed: {resp}")

    def is_configured(self) -> bool:
        # Telegraph работает всегда (токен создаётся на лету при отсутствии секрета)
        return True

    def publish(self, *, title: str, content_html: str, author_name: str,
                author_url: str, canonical_url: str, lang: str) -> RepostResult:
        try:
            token = self._token(author_name)
            nodes = html_to_nodes(content_html)
            data = urllib.parse.urlencode({
                "access_token": token,
                "title": title[:256],
                "author_name": author_name[:128],
                "author_url": author_url[:512],
                "content": json.dumps(nodes, ensure_ascii=False),
                "return_content": "false",
            }).encode()
            resp = json.loads(urllib.request.urlopen(
                f"{TELEGRAPH_API}/createPage", data=data, timeout=20).read())
            if resp.get("ok"):
                return RepostResult(ok=True, url=resp["result"]["url"])
            return RepostResult(ok=False, error=str(resp))
        except Exception as e:
            return RepostResult(ok=False, error=str(e))
