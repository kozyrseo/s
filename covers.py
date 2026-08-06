"""
KOZYR — генератор SVG-обложек для блога.

Зачем: у статей нет фото-hero (OpenAI-генерация опциональна и может не
сработать). Чтобы блог не выглядел «черновиком» с голыми карточками, каждая
статья получает векторную обложку в фирменной палитре KOZYR. Обложки:

  • строятся из токенов бренда (electric-blue #2668FF, gold #9C6A18,
    ink #0A1128, светлый фон) — читаются как единая площадка с главной;
  • детерминированы по slug/теме — одна и та же статья всегда получает один
    и тот же мотив;
  • не требуют внешних картинок, работают offline и для автогенерации.

Три смысловых мотива + универсальный фолбэк:
  chips     — стопки фишек на фоне сукна        (рейкбек / деньги / возврат)
  network   — узлы-инвайты вокруг хоста          (приватные клубы / ClubGG)
  versus    — раскол кадра надвое                 (сравнение румов/клубов)
  generic   — масть + сетка                       (всё остальное / фолбэк)

API:
  cover_svg(motif, ratio="16x9") -> str   # готовый <svg> для вставки
  pick_motif(slug, title, keywords) -> str # эвристика выбора мотива
"""

from __future__ import annotations

# Фирменные цвета (совпадают с --токенами kozyr-blog.css)
BLUE = "#2668FF"
BLUE_DEEP = "#1E52D9"
INK = "#0A1128"
GOLD = "#9C6A18"
GOLD_LT = "#F0B44A"
SURF = "#FFFFFF"
SURF_ALT = "#F5F7FB"
LINE = "#E5E7EE"

# Пропорции: 16x9 для карточек и внутристатейной обложки,
# 40x15 — широкий баннер под hero, если понадобится.
_RATIOS = {"16x9": (1600, 900), "40x15": (1600, 600)}


def _defs() -> str:
    return f"""
  <defs>
    <linearGradient id="kzFelt" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{SURF_ALT}"/>
      <stop offset="1" stop-color="#EEF2FA"/>
    </linearGradient>
    <linearGradient id="kzBlue" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BLUE}"/>
      <stop offset="1" stop-color="{BLUE_DEEP}"/>
    </linearGradient>
    <linearGradient id="kzGold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{GOLD_LT}"/>
      <stop offset="1" stop-color="{GOLD}"/>
    </linearGradient>
  </defs>"""


def _watermark_suit(x: float, y: float, size: float, color: str, op: float) -> str:
    # Пиковая масть как тихий водяной знак (перекликается с kozyr-enhance).
    s = size
    return (
        f'<g transform="translate({x},{y})" opacity="{op}">'
        f'<path d="M0 {-s*0.5} C {s*0.55} {-s*0.05}, {s*0.55} {s*0.35}, {s*0.12} {s*0.35} '
        f'C {s*0.30} {s*0.35}, {s*0.30} {s*0.62}, 0 {s*0.62} '
        f'C {-s*0.30} {s*0.62}, {-s*0.30} {s*0.35}, {-s*0.12} {s*0.35} '
        f'C {-s*0.55} {s*0.35}, {-s*0.55} {-s*0.05}, 0 {-s*0.5} Z" fill="{color}"/>'
        f'</g>'
    )


def _chips(w: int, h: int) -> str:
    """Стопки фишек — мотив денег/рейкбека."""
    cx = w * 0.66
    base = h * 0.80
    stacks = [
        (cx - 210, 5, BLUE),
        (cx - 40, 8, GOLD),
        (cx + 130, 3, INK),
    ]
    chips = []
    for sx, n, col in stacks:
        for i in range(n):
            cy = base - i * 30
            fill = "url(#kzBlue)" if col == BLUE else ("url(#kzGold)" if col == GOLD else INK)
            chips.append(
                f'<ellipse cx="{sx}" cy="{cy}" rx="88" ry="30" fill="{fill}" '
                f'stroke="rgba(255,255,255,.55)" stroke-width="3"/>'
            )
            chips.append(
                f'<ellipse cx="{sx}" cy="{cy-6}" rx="88" ry="30" fill="none" '
                f'stroke="rgba(255,255,255,.35)" stroke-width="2"/>'
            )
    chips_svg = "\n    ".join(chips)
    return f"""
  <rect width="{w}" height="{h}" fill="url(#kzFelt)"/>
  {_watermark_suit(w*0.20, h*0.42, 460, BLUE, 0.05)}
  <g>
    {chips_svg}
  </g>"""


def _network(w: int, h: int) -> str:
    """Узлы-инвайты вокруг хоста — мотив приватных клубов."""
    cx, cy = w * 0.5, h * 0.5
    import math
    nodes = []
    edges = []
    r = min(w, h) * 0.34
    count = 6
    for i in range(count):
        a = (i / count) * 2 * math.pi - math.pi / 2
        nx, ny = cx + r * math.cos(a), cy + r * math.sin(a)
        edges.append(
            f'<line x1="{cx}" y1="{cy}" x2="{nx:.0f}" y2="{ny:.0f}" '
            f'stroke="{BLUE}" stroke-width="2.5" opacity="0.45"/>'
        )
        nodes.append(
            f'<circle cx="{nx:.0f}" cy="{ny:.0f}" r="30" fill="{SURF}" '
            f'stroke="{BLUE}" stroke-width="3"/>'
        )
    return f"""
  <rect width="{w}" height="{h}" fill="url(#kzFelt)"/>
  {_watermark_suit(w*0.82, h*0.30, 360, GOLD, 0.05)}
  <g>
    {"".join(edges)}
    {"".join(nodes)}
    <circle cx="{cx}" cy="{cy}" r="52" fill="url(#kzBlue)"/>
    <circle cx="{cx}" cy="{cy}" r="52" fill="none" stroke="{GOLD_LT}" stroke-width="3"/>
  </g>"""


def _versus(w: int, h: int) -> str:
    """Раскол кадра надвое — мотив сравнения."""
    mid = w * 0.5
    return f"""
  <rect x="0" y="0" width="{mid}" height="{h}" fill="url(#kzBlue)"/>
  <rect x="{mid}" y="0" width="{w-mid}" height="{h}" fill="{INK}"/>
  <polygon points="{mid-60},0 {mid+60},0 {mid+8},{h} {mid-108},{h}"
           fill="{SURF}"/>
  <polygon points="{mid-14},0 {mid+14},0 {mid+14},{h} {mid-14},{h}"
           fill="url(#kzGold)"/>
  {_watermark_suit(w*0.24, h*0.50, 300, "#FFFFFF", 0.10)}
  {_watermark_suit(w*0.76, h*0.50, 300, GOLD_LT, 0.12)}
  <circle cx="{mid}" cy="{h*0.5}" r="46" fill="{SURF}"/>
  <text x="{mid}" y="{h*0.5+11}" text-anchor="middle"
        font-family="Space Grotesk, sans-serif" font-weight="700"
        font-size="30" fill="{INK}">VS</text>"""


def _generic(w: int, h: int) -> str:
    """Универсальная обложка: масть + тонкая сетка."""
    grid = []
    step = 120
    for gx in range(0, w + 1, step):
        grid.append(f'<line x1="{gx}" y1="0" x2="{gx}" y2="{h}" stroke="{LINE}" stroke-width="1"/>')
    for gy in range(0, h + 1, step):
        grid.append(f'<line x1="0" y1="{gy}" x2="{w}" y2="{gy}" stroke="{LINE}" stroke-width="1"/>')
    return f"""
  <rect width="{w}" height="{h}" fill="{SURF_ALT}"/>
  <g opacity="0.6">{"".join(grid)}</g>
  {_watermark_suit(w*0.5, h*0.46, 420, BLUE, 0.10)}
  {_watermark_suit(w*0.5, h*0.46, 300, GOLD, 0.10)}"""


_MOTIFS = {
    "chips": _chips,
    "network": _network,
    "versus": _versus,
    "generic": _generic,
}


def cover_svg(motif: str, ratio: str = "16x9") -> str:
    """Вернуть готовый <svg> обложки для указанного мотива."""
    w, h = _RATIOS.get(ratio, _RATIOS["16x9"])
    builder = _MOTIFS.get(motif, _generic)
    inner = builder(w, h)
    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-hidden="true" preserveAspectRatio="xMidYMid slice">'
        f'{_defs()}{inner}</svg>'
    )


def pick_motif(slug: str = "", title: str = "", keywords: str = "") -> str:
    """Эвристика: подобрать мотив по slug/заголовку/ключам статьи."""
    hay = " ".join([slug or "", title or "", keywords or ""]).lower()
    versus_hits = ("sravnenie", "porivnyannya", "vs", "или ", "чи ", "против", "проти", "klubok")
    network_hits = ("club", "klub", "клуб", "clubgg", "privat", "приват", "инвайт", "інвайт", "host", "хост")
    chips_hits = ("reykbek", "rejkbek", "рейкбек", "rakeback", "vyvod", "виведення", "деньг", "грош", "bankroll", "банкрол")
    if any(k in hay for k in versus_hits):
        return "versus"
    if any(k in hay for k in network_hits):
        return "network"
    if any(k in hay for k in chips_hits):
        return "chips"
    return "generic"


if __name__ == "__main__":
    # Быстрый предпросмотр: пишет 4 SVG в текущую папку.
    import pathlib
    for m in _MOTIFS:
        pathlib.Path(f"cover-{m}.svg").write_text(cover_svg(m), encoding="utf-8")
    print("wrote cover-*.svg")
