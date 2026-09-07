#!/usr/bin/env python3
"""
KOZYR — авто-обработка логотипа партнёра.

Оператор кидает боту логотип файлом (PNG/JPG/WebP/SVG, хоть тяжёлый и с полями).
Воркер сохраняет сырой файл в _partner_logos_raw/{id}.{ext} и запускает этот
скрипт. Скрипт:
  • для растровых (PNG/JPG/WebP): обрезает однотонные поля, делает КВАДРАТ
    (фон = цвет полей, прозрачность сохраняется), ужимает до 512×512, webp;
  • для SVG: копирует как есть (вектор обрабатывать не нужно);
  • кладёт результат в ua/blog/logos/{id}.webp (или .svg);
  • прописывает путь в _partner_drafts/{id}.json → logo_img;
  • удаляет сырой файл.

Идемпотентно: повторный запуск на том же входе даёт тот же результат.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR   = REPO_ROOT / "_partner_logos_raw"
LOGO_DIR  = REPO_ROOT / "ua" / "blog" / "logos"
DRAFTS    = REPO_ROOT / "_partner_drafts"

SIZE = 512          # финальная сторона квадрата
PAD  = 0.12         # запас по краям (доля от стороны логотипа)
INK_TOLERANCE = 12  # порог «отличия от фона» при обрезке полей


def find_raw(pid: str) -> Path | None:
    """Находит сырой файл _partner_logos_raw/{id}.* (любое расширение)."""
    if not RAW_DIR.exists():
        return None
    for f in sorted(RAW_DIR.glob(f"{pid}.*")):
        return f
    return None


def update_draft_logo(pid: str, web_path: str) -> None:
    df = DRAFTS / f"{pid}.json"
    if not df.exists():
        print(f"  ⚠️ черновик {df.name} не найден — logo_img не проставлен (только файл создан)")
        return
    d = json.loads(df.read_text(encoding="utf-8"))
    d["logo_img"] = web_path
    if isinstance(d.get("_missing"), list):
        d["_missing"] = [m for m in d["_missing"] if "logo" not in m.lower()]
    df.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ draft.logo_img = {web_path}")


def process_raster(src: Path, dst: Path) -> None:
    from PIL import Image, ImageChops
    im = Image.open(src).convert("RGBA")
    w0, h0 = im.size

    # 1. Обрезка однотонных полей
    alpha = im.getchannel("A")
    has_transp = alpha.getextrema()[0] < 255
    if has_transp:
        # есть прозрачность — обрезаем по непрозрачной области, паддинг прозрачный
        bbox = alpha.getbbox()
        pad_color = (0, 0, 0, 0)
    else:
        # непрозрачный — обрезаем однотонную рамку по цвету угла (любой цвет)
        corner = im.getpixel((0, 0))
        bg = Image.new("RGBA", im.size, corner)
        diff = ImageChops.difference(im.convert("RGB"), bg.convert("RGB"))
        # усиливаем, чтобы поймать слабые отличия, и режем по порогу
        diff = diff.point(lambda p: 255 if p > INK_TOLERANCE else 0)
        bbox = diff.getbbox()
        pad_color = corner
    if bbox:
        im = im.crop(bbox)

    # 2. Квадрат с запасом по краям (фон = цвет полей)
    side = max(im.width, im.height)
    side_p = int(side * (1 + PAD * 2))
    sq = Image.new("RGBA", (side_p, side_p), pad_color)
    ox = (side_p - im.width) // 2
    oy = (side_p - im.height) // 2
    sq.alpha_composite(im, (ox, oy))

    # 3. Ужать до 512×512, сохранить webp
    final = sq.resize((SIZE, SIZE), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if has_transp:
        final.save(dst, "WEBP", quality=94, method=6)      # с альфой
    else:
        final.convert("RGB").save(dst, "WEBP", quality=94, method=6)
    print(f"  ✓ {w0}×{h0} → обрезка+квадрат → {SIZE}×{SIZE}, {dst.stat().st_size} байт")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="ID партнёра")
    args = ap.parse_args()
    pid = args.id

    raw = find_raw(pid)
    if not raw:
        sys.exit(f"❌ Нет сырого логотипа _partner_logos_raw/{pid}.* — нечего обрабатывать")
    ext = raw.suffix.lower().lstrip(".")
    print(f"Логотип: {raw.relative_to(REPO_ROOT)} ({ext})")

    if ext == "svg":
        # вектор — просто переносим как есть
        dst = LOGO_DIR / f"{pid}.svg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw.read_bytes())
        update_draft_logo(pid, f"/ua/blog/logos/{pid}.svg")
        print(f"  ✓ SVG перенесён без обработки: {dst.relative_to(REPO_ROOT)}")
    else:
        dst = LOGO_DIR / f"{pid}.webp"
        process_raster(raw, dst)
        update_draft_logo(pid, f"/ua/blog/logos/{pid}.webp")
        # чистим устаревшие форматы того же лого (png/jpg), если были
        for old in LOGO_DIR.glob(f"{pid}.*"):
            if old.suffix.lower() not in (".webp",):
                old.unlink()
                print(f"  ✓ удалён устаревший {old.name}")

    raw.unlink()
    print(f"  ✓ сырой файл удалён")
    print("Готово.")


if __name__ == "__main__":
    main()
