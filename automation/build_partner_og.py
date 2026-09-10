#!/usr/bin/env python3
"""
KOZYR — генератор OG-обложек (og:image) для страниц партнёров.

Зачем: шаблон партнёра ссылается на https://kozyr.club/og-{kind}-{id}.jpg
(2400×1260), но сам файл раньше приходилось делать вручную — и для новых
партнёров он часто отсутствовал, ломая превью при шеринге в соцсети/Telegram.
Этот скрипт детерминированно рендерит фирменную растровую обложку из данных
partners.json (имя, тип, валюта, рейкбек, мин. депозит, выплаты, Kozyr Score,
лого-картинка) — офлайн, без внешних сервисов.

Запуск:
    python automation/build_partner_og.py --id tonpoker
    python automation/build_partner_og.py --all          # все партнёры
    python automation/build_partner_og.py --id tonpoker --force   # перезаписать

Пишет: og-{rooms|clubs}-{id}.jpg в корень репозитория (2400×1260, RGB, JPEG).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
PARTNERS_JSON = REPO_ROOT / "partners.json"

W, H = 2400, 1260  # ровно как объявлено в og:image:width/height

# ── Палитра KOZYR ──
INK        = (10, 17, 40)      # #0A1128
INK_DEEP   = (0, 7, 20)        # #000714
WHITE      = (255, 255, 255)
LIGHT_BG   = (250, 250, 252)   # #FAFAFC
ACCENT     = (38, 104, 255)    # #2668FF
GOLD       = (240, 180, 74)    # #F0B44A
MUTED_L    = (92, 101, 128)    # #5C6580 (на светлом)
MUTED_D    = (150, 168, 205)   # приглушённый на тёмном


def _hex(h: str, default=(38, 104, 255)) -> tuple[int, int, int]:
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


# DejaVu почти всегда есть в системе; кириллица поддерживается.
BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
REG  = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
MONO = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgrad(size, top, bottom):
    w, h = size
    base = Image.new("RGB", (1, h))
    for y in range(h):
        base.putpixel((0, y), _lerp(top, bottom, y / max(h - 1, 1)))
    return base.resize((w, h))


def _rounded(draw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def _text_w(draw, txt, font):
    return draw.textbbox((0, 0), txt, font=font)[2]


def rows_map(p: dict) -> dict:
    out = {}
    for r in (p.get("card", {}).get("rows") or []):
        if isinstance(r, list) and len(r) >= 2 and r[0] and r[1] not in ("", "rake"):
            out[str(r[0])] = str(r[1])
    return out


def rake_chip(p: dict) -> str | None:
    r = p.get("rake")
    if r in (None, "", "none", 0, "0"):
        return None
    lbl = (p.get("rakeLabel") or "").strip()
    if lbl:
        return f"Рейкбек {lbl}"
    return f"Рейкбек до {r}%"


def build_chips(p: dict) -> list[str]:
    rm = rows_map(p)
    chips: list[str] = []
    if p.get("currency"):
        chips.append(f"Валюта {p['currency']}")
    rc = rake_chip(p)
    if rc:
        chips.append(rc)
    elif rm.get("Welcome-бонус"):
        chips.append(f"Бонус {rm['Welcome-бонус']}")
    if rm.get("Мин. депозит"):
        chips.append(f"Мин. депозит {rm['Мин. депозит']}")
    if p.get("payoutLabel"):
        chips.append(f"Выплаты {p['payoutLabel']}")
    return chips[:4]


def draw_og(p: dict) -> Image.Image:
    dark = bool(p.get("card", {}).get("dark"))
    acc_from = _hex(p.get("logo", {}).get("from"), ACCENT)
    acc_to = _hex(p.get("logo", {}).get("to"), _lerp(ACCENT, INK, 0.3))

    # Фон
    if dark:
        img = _vgrad((W, H), INK_DEEP, _lerp(INK_DEEP, acc_to, 0.35)).convert("RGB")
        fg, muted, card_bg, line = WHITE, MUTED_D, (255, 255, 255, 18), (255, 255, 255)
    else:
        img = _vgrad((W, H), WHITE, LIGHT_BG).convert("RGB")
        fg, muted, card_bg, line = INK, MUTED_L, (10, 17, 40, 8), INK

    draw = ImageDraw.Draw(img, "RGBA")

    # Мягкое акцентное свечение в правом верхнем углу
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 900, -500, W + 400, 600], fill=(*acc_from, 70 if dark else 45))
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    M = 140  # поля

    # ── Верх: вордмарк KOZYR + бейдж-плашка «Обзор 2026» ──
    f_brand = _font(BOLD, 62)
    draw.text((M, 96), "KOZYR", font=f_brand, fill=fg)
    # маленький акцентный квадрат перед именем бренда? Оставим лаконично.
    kind = (p.get("card", {}).get("kind") or p.get("networkLabel") or "").strip()
    badge = "Обзор 2026"
    f_badge = _font(BOLD, 40)
    bw = _text_w(draw, badge, f_badge)
    pad = 34
    bx2 = W - M
    bx1 = bx2 - bw - pad * 2
    _rounded(draw, [bx1, 96, bx2, 96 + 74], 37,
             fill=(*acc_from, 235))
    draw.text((bx1 + pad, 96 + 15), badge, font=f_badge, fill=WHITE)

    # ── Центр: лого-картинка + имя партнёра + тип ──
    top = 300
    logo_size = 240
    logo_x, logo_y = M, top
    name_x = M
    logo_img_rel = (p.get("card", {}).get("logoImg") or "").lstrip("/")
    logo_path = REPO_ROOT / logo_img_rel if logo_img_rel else None
    if logo_path and logo_path.exists():
        try:
            lg = Image.open(logo_path).convert("RGBA")
            lg.thumbnail((logo_size, logo_size), Image.LANCZOS)
            # плашка-подложка под лого (чтобы читалось и на тёмном, и на светлом)
            plate = [logo_x, logo_y, logo_x + logo_size, logo_y + logo_size]
            _rounded(draw, plate, 40, fill=(255, 255, 255, 255),
                     outline=(*acc_from, 255), width=3)
            off = ((logo_size - lg.width) // 2, (logo_size - lg.height) // 2)
            img.paste(lg, (logo_x + off[0], logo_y + off[1]), lg)
            draw = ImageDraw.Draw(img, "RGBA")
            name_x = logo_x + logo_size + 60
        except Exception:
            name_x = M

    # Имя (крупно, при необходимости уменьшаем под ширину)
    name = p.get("name", "")
    avail = (W - M) - name_x
    size = 168
    while size > 90:
        f_name = _font(BOLD, size)
        if _text_w(draw, name, f_name) <= avail:
            break
        size -= 6
    f_name = _font(BOLD, size)
    # вертикально центрируем имя относительно лого-плашки
    name_h = draw.textbbox((0, 0), name, font=f_name)[3]
    name_y = logo_y + (logo_size - name_h) // 2 - 10
    draw.text((name_x, name_y), name, font=f_name, fill=fg)
    if kind:
        f_kind = _font(REG, 48)
        draw.text((name_x + 4, name_y + name_h + 22), kind, font=f_kind, fill=muted)

    # ── Ряд фактов-чипов ──
    chips = build_chips(p)
    f_chip = _font(BOLD, 46)
    cy = 690
    cx = M
    ch_h = 96
    gap = 28
    for c in chips:
        cw = _text_w(draw, c, f_chip) + 64
        if cx + cw > W - M:
            cx = M
            cy += ch_h + gap
        _rounded(draw, [cx, cy, cx + cw, cy + ch_h], 24,
                 fill=card_bg, outline=(*acc_from, 120), width=2)
        draw.text((cx + 32, cy + 22), c, font=f_chip, fill=fg)
        cx += cw + gap

    # ── Kozyr Score (крупный блок справа снизу) ──
    score = p.get("score")
    if score is not None:
        f_score = _font(MONO, 150)
        f_slash = _font(BOLD, 60)
        f_lbl = _font(REG, 40)
        s_txt = f"{score}"
        sw = _text_w(draw, s_txt, f_score)
        sx2 = W - M
        sy = H - 360
        draw.text((sx2 - sw - 150, sy), s_txt, font=f_score, fill=(*acc_from,) if not dark else GOLD)
        draw.text((sx2 - 130, sy + 78), "/10", font=f_slash, fill=muted)
        draw.text((sx2 - _text_w(draw, "Kozyr Score", f_lbl), sy - 56),
                  "Kozyr Score", font=f_lbl, fill=muted)

    # ── Низ: домен + разделительная линия ──
    draw.line([(M, H - 150), (W - M, H - 150)], fill=(*line, 60), width=2)
    f_dom = _font(BOLD, 48)
    draw.text((M, H - 118), "kozyr.club", font=f_dom, fill=fg)
    f_tag = _font(REG, 44)
    tag = "Честные обзоры покер-румов и клубов"
    draw.text((W - M - _text_w(draw, tag, f_tag), H - 116), tag, font=f_tag, fill=muted)

    return img


def out_name(p: dict) -> str:
    kind = "clubs" if (p.get("access") == "club" or p.get("type") == "club") else "rooms"
    return f"og-{kind}-{p['id']}.jpg"


def main() -> int:
    ap = argparse.ArgumentParser(description="Генератор OG-обложек партнёров")
    ap.add_argument("--id", help="id партнёра из partners.json")
    ap.add_argument("--all", action="store_true", help="сгенерировать для всех партнёров")
    ap.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    args = ap.parse_args()

    data = json.loads(PARTNERS_JSON.read_text(encoding="utf-8"))
    partners = data.get("partners", [])
    if args.all:
        targets = partners
    elif args.id:
        targets = [p for p in partners if p.get("id") == args.id]
        if not targets:
            raise SystemExit(f"❌ Партнёр '{args.id}' не найден в partners.json")
    else:
        raise SystemExit("Укажи --id <id> или --all")

    for p in targets:
        dest = REPO_ROOT / out_name(p)
        if dest.exists() and not args.force:
            print(f"• {dest.name} уже есть (—force чтобы перезаписать) — пропуск")
            continue
        img = draw_og(p)
        img.save(dest, "JPEG", quality=88, optimize=True, progressive=True)
        print(f"✓ {dest.name}  ({img.width}×{img.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
