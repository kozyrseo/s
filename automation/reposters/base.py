"""
reposters/base.py — базовый интерфейс площадки для репоста статей.

Расширяемая система: каждая площадка (Telegraph, Blogger, Teletype...) —
это класс-наследник Reposter с единым методом publish(). Чтобы добавить
новую площадку, достаточно:
  1. Создать reposters/{name}.py с классом-наследником
  2. Зарегистрировать его в reposters/__init__.py (REPOSTERS)
Всё остальное (рерайт, очередь, workflow, кнопки бота) — общее.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RepostResult:
    """Результат публикации на площадке."""
    ok: bool
    url: str = ""          # URL опубликованной статьи на площадке
    error: str = ""        # текст ошибки, если ok=False


class Reposter:
    """Базовый класс площадки. Наследники реализуют publish()."""

    # Уникальный код площадки (для реестра, кнопок, логов). Переопределить.
    key: str = "base"
    # Человекочитаемое имя (для кнопок в боте). Переопределить.
    name: str = "Base"

    def publish(self, *, title: str, content_html: str, author_name: str,
                author_url: str, canonical_url: str, lang: str) -> RepostResult:
        """Публикует статью на площадке.

        Аргументы:
          title         — заголовок статьи (уже переписанный под площадку)
          content_html  — тело статьи в HTML (уже переписанное), со ссылкой
                          на оригинал внутри
          author_name   — имя автора (Никита Волошин)
          author_url    — ссылка на страницу автора на kozyr.club
          canonical_url — URL оригинальной статьи на kozyr.club (для ссылки/меты)
          lang          — язык версии (ru/uk)

        Возвращает RepostResult(ok, url, error).
        """
        raise NotImplementedError("Площадка должна реализовать publish()")

    def is_configured(self) -> bool:
        """Готова ли площадка к работе (есть токен/ключи в окружении).
        Бот показывает кнопку только для настроенных площадок."""
        return True
