"""
reposters/__init__.py — РЕЕСТР площадок для репоста.

Чтобы добавить новую площадку (Blogger, Teletype...):
  1. Создай reposters/{name}.py с классом-наследником Reposter
  2. Импортируй и добавь в REPOSTERS ниже (1 строка)
  3. Кнопка в боте появится автоматически

Бот и workflow работают с площадками через этот реестр — не хардкодят
конкретную площадку.
"""
from .base import Reposter, RepostResult
from .telegraph import TelegraphReposter
from .blogger import BloggerReposter

# ─── РЕЕСТР ПЛОЩАДОК ───
# key → класс. Раскомментируй/добавь строку, чтобы включить площадку.
REPOSTERS: dict[str, type[Reposter]] = {
    "telegraph": TelegraphReposter,
    "blogger": BloggerReposter,
    # "teletype": TeletypeReposter,
}


def get_reposter(key: str) -> Reposter | None:
    """Экземпляр площадки по ключу, или None если нет/не настроена."""
    cls = REPOSTERS.get(key)
    if not cls:
        return None
    inst = cls()
    return inst if inst.is_configured() else None


def active_reposters() -> list[Reposter]:
    """Все настроенные площадки (для кнопок в боте)."""
    result = []
    for cls in REPOSTERS.values():
        inst = cls()
        if inst.is_configured():
            result.append(inst)
    return result
