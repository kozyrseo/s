"""
Generate a hero image for a KOZYR blog article using OpenAI GPT Image
(gpt-image-1), then stamp a small KOZYR "K" watermark in the corner.

Pipeline:
  1. Take the image_prompt supplied by Claude in article metadata
  2. Pick a palette (alternates: brand-blue vs natural-casino) so the blog
     doesn't look monotonous across many articles
  3. Call OpenAI Images API at 1536x1024 (landscape, fits og:image 1.91:1)
  4. Stamp a small semi-transparent KOZYR "K" mark in the bottom-right corner
  5. Convert to WebP, save next to body.md in the pending dir

Design decisions (KOZYR, differs from the old two-panel PokerNet layout):
  - NO left brand panel. The photo uses the full frame — cleaner, more
    editorial, lets the poker imagery breathe.
  - A small "K" watermark (bottom-right) gives quiet brand presence without
    eating a third of the canvas.
  - Palette alternates between the site's electric blue and natural casino
    tones (green felt, wood, warm light) so hero images feel varied.
  - Poker style (live vs online) is left to the per-article image_prompt
    that Claude writes; the style suffix keeps both looking photographic
    and on-brand.

Brand asset path:
  Watermark lives at `automation/brand/watermark-K-white.png` (white K on
  transparent) and `automation/brand/watermark-K-blue.png` (blue K). The
  white one is used on darker/natural photos, the blue one on light photos.

Failure handling:
  Failure of AI generation OR watermarking is non-fatal. If the AI call
  fails, we return None and the article ships without a hero. If the
  watermark step fails, we save the plain photo so the article still gets
  a hero image, just without the mark.
"""

from __future__ import annotations

import base64
import hashlib
import os
from io import BytesIO
from pathlib import Path

# ==== Output ====
IMAGE_MODEL = "gpt-image-1"
IMAGE_SIZE = "1536x1024"
IMAGE_QUALITY = "medium"
WEBP_QUALITY = 82
HERO_FILENAME = "hero.webp"

# ==== Canvas ====
CANVAS_W, CANVAS_H = 1536, 1024

# ==== Watermark ====
# Small "K" mark stamped bottom-right. Height as a fraction of canvas height.
WATERMARK_HEIGHT_RATIO = 0.11       # ~113 px tall on a 1024-tall canvas
WATERMARK_MARGIN = 44               # px from the right & bottom edges
WATERMARK_OPACITY = 0.82            # 0..1, slight transparency so it sits in

HERE = Path(__file__).resolve().parent          # automation/
WATERMARK_WHITE = HERE / "brand" / "watermark-K-white.png"
WATERMARK_BLUE = HERE / "brand" / "watermark-K-blue.png"


# ==== Palettes ====
# Two visual directions. We alternate deterministically per-article (hash of
# the prompt) so the same article always gets the same palette, but across
# many articles they interleave.
PALETTE_BLUE = (
    "Editorial photographic style with a modern electric-blue accent grade. "
    "Clean, crisp lighting; deep blue (#2668FF) present in the lighting, "
    "reflections or backdrop; balanced exposure, premium fintech-meets-poker "
    "aesthetic. High detail, shallow depth of field where appropriate. "
)
PALETTE_NATURAL = (
    "Photorealistic style with natural casino colours: green baize felt, warm "
    "wood tones, brass and gold chip accents, soft warm ambient light like a "
    "real card room. Rich but true-to-life colour, gentle volumetric shadows, "
    "shallow depth of field, high detail. "
)

# Shared constraints appended to every prompt regardless of palette.
STYLE_COMMON = (
    "Full-frame composition, the subject can sit anywhere in the frame — this "
    "image is used edge-to-edge with no cropping. "
    "ABSOLUTELY NO TEXT of any kind anywhere in the image — no letters, no "
    "numbers, no signage, no UI labels, no watermarks. "
    "NO HUMAN FACES, no recognizable people, no photographs of identifiable "
    "individuals. Hands are acceptable. "
    "NO real-room or platform logos — no PPPoker, PokerBros, ClubGG, WSOP, "
    "PokerStars, GGPoker, or any other branded mark. "
    "NO copyrighted character art."
)


def _pick_palette(seed_text: str) -> tuple[str, Path]:
    """Alternate palette deterministically from the prompt text.

    Returns (style_prefix, watermark_path). White watermark for the natural
    (darker) palette, blue watermark for the blue (lighter) palette.
    """
    h = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16)
    if h % 2 == 0:
        return PALETTE_BLUE, WATERMARK_BLUE
    return PALETTE_NATURAL, WATERMARK_WHITE


def _build_full_prompt(image_prompt: str) -> tuple[str, Path]:
    palette_prefix, watermark_path = _pick_palette(image_prompt.strip())
    full = f"{image_prompt.strip()} {palette_prefix}{STYLE_COMMON}"
    return full, watermark_path


# ==== Watermark stamping ====

def _stamp_watermark(photo, watermark_path: Path):
    """Paste a small semi-transparent K mark in the bottom-right corner.

    photo: a PIL RGB image at CANVAS_W x CANVAS_H.
    watermark_path: PNG with alpha (white or blue K on transparent).
    Falls back silently (returns photo unchanged) if the mark can't load.
    """
    from PIL import Image

    if not watermark_path.exists():
        print(f"⚠️  Watermark not found at {watermark_path} — shipping photo without mark")
        return photo

    try:
        mark = Image.open(watermark_path).convert("RGBA")
    except Exception as e:
        print(f"⚠️  Watermark load failed: {e} — shipping photo without mark")
        return photo

    # Scale mark to target height
    target_h = int(CANVAS_H * WATERMARK_HEIGHT_RATIO)
    scale = target_h / mark.height
    mark = mark.resize(
        (max(1, int(mark.width * scale)), target_h),
        Image.LANCZOS,
    )

    # Apply global opacity to the mark's alpha channel
    if WATERMARK_OPACITY < 1.0:
        alpha = mark.split()[3].point(lambda a: int(a * WATERMARK_OPACITY))
        mark.putalpha(alpha)

    # Position bottom-right with margin
    x = CANVAS_W - mark.width - WATERMARK_MARGIN
    y = CANVAS_H - mark.height - WATERMARK_MARGIN

    base = photo.convert("RGBA")
    base.alpha_composite(mark, dest=(x, y))
    return base.convert("RGB")


# ==== Main entry point ====

def generate_hero_image(image_prompt: str, target_dir: Path) -> Path | None:
    """
    Generate a hero image and save as `hero.webp` in target_dir.
    Returns the saved path, or None on AI-call failure (article ships without hero).
    On watermark failure, falls back to saving the plain photo.
    """
    if not image_prompt or not image_prompt.strip():
        print("ℹ️  No image_prompt in metadata — skipping hero image generation")
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("⚠️  OPENAI_API_KEY not set — skipping hero image generation")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("⚠️  `openai` package not installed — skipping hero image generation")
        return None

    try:
        from PIL import Image
    except ImportError:
        print("⚠️  `Pillow` package not installed — skipping hero image generation")
        return None

    full_prompt, watermark_path = _build_full_prompt(image_prompt)

    try:
        client = OpenAI(api_key=api_key)
        print(f"🎨 Generating hero image ({IMAGE_MODEL}, {IMAGE_SIZE}, {IMAGE_QUALITY})")
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=full_prompt,
            size=IMAGE_SIZE,
            quality=IMAGE_QUALITY,
            n=1,
        )
    except Exception as e:
        print(f"⚠️  OpenAI image generation failed: {type(e).__name__}: {e}")
        return None

    try:
        b64 = response.data[0].b64_json
        if not b64:
            print("⚠️  OpenAI returned no image data")
            return None
        png_bytes = base64.b64decode(b64)
    except (AttributeError, IndexError) as e:
        print(f"⚠️  Unexpected OpenAI response shape: {e}")
        return None

    # Stamp watermark + convert to WebP
    try:
        with Image.open(BytesIO(png_bytes)) as photo:
            if photo.mode in ("RGBA", "LA", "P"):
                photo = photo.convert("RGB")
            if photo.width != CANVAS_W or photo.height != CANVAS_H:
                photo = photo.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
            final = _stamp_watermark(photo, watermark_path)
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / HERO_FILENAME
            final.save(out_path, format="WEBP", quality=WEBP_QUALITY, method=6)
    except Exception as e:
        print(f"⚠️  Watermark or WebP conversion failed: {type(e).__name__}: {e}")
        # Fallback: save raw PNG (no watermark) so the article still gets a hero
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = target_dir / "hero.png"
            fallback_path.write_bytes(png_bytes)
            png_kb = len(png_bytes) / 1024
            print(f"⚠️  Saved fallback PNG (no watermark): {fallback_path} ({png_kb:.0f} KB)")
            return fallback_path
        except Exception as e2:
            print(f"⚠️  Fallback PNG save also failed: {e2}")
            return None

    size_kb = out_path.stat().st_size / 1024
    png_kb = len(png_bytes) / 1024
    print(f"✅ Hero image saved: {out_path} ({size_kb:.0f} KB WebP, was {png_kb:.0f} KB raw PNG)")
    return out_path


def hero_alt_text(h1_title: str) -> str:
    """Reasonable default alt text for a hero image. Used in HTML and og:image:alt."""
    return f"Иллюстрация к статье: {h1_title}"
