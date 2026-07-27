"""
Generate a hero image for an article using OpenAI GPT Image (gpt-image-1)
and composite it with the PokerNet AI brand panel on the left.

Pipeline:
  1. Take the image_prompt supplied by Claude in article metadata
  2. Call OpenAI Images API at 1536x1024 (landscape, fits og:image 1.91:1)
  3. Crop the AI image to its right 60% (so the subject area survives)
  4. Build a brand panel (logo + wordmark) on the left 35% of canvas
  5. Soft-blend the seam between brand panel and AI image
  6. Convert to WebP, save next to body.md in the pending dir

Why two-panel composition for blog hero images (matches Telegram channel):
  Single visual identity across all PokerNet AI surfaces — channel posts
  and blog hero images use the same brand panel. Readers who arrive on the
  blog from the channel (or vice versa) see consistent brand presence,
  which matters for B2B trust building. The same logic that justifies a
  consistent header on a website justifies a consistent hero composition.

Brand asset path:
  Logo lives at `automation/brand/logo.png`. This module and the Telegram
  pipeline (`automation/tg_autopost/image_gen.py`) both read from there
  so the brand asset has a single source of truth.

Failure handling:
  Failure of either AI generation OR composition is non-fatal. If the AI
  call fails, we return None and the article ships without a hero. If
  composition fails (e.g. logo file missing), we save the raw AI PNG as
  fallback so the article still gets a hero image, just without the
  brand panel.
"""

from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path

# ==== Output ====
IMAGE_MODEL = "gpt-image-1"
IMAGE_SIZE = "1536x1024"
IMAGE_QUALITY = "medium"
WEBP_QUALITY = 82
HERO_FILENAME = "hero.webp"

# ==== Canvas / layout (matches tg_autopost/image_gen.py for visual consistency) ====
CANVAS_W, CANVAS_H = 1536, 1024
LEFT_PANEL_RATIO = 0.35
LEFT_W = int(CANVAS_W * LEFT_PANEL_RATIO)   # 537 px
RIGHT_W = CANVAS_W - LEFT_W                  # 999 px
VISIBLE_RIGHT_RATIO = 0.60                   # keep right 60% of AI image
SEAM_BLEND_WIDTH = 80                        # px of soft gradient at the seam
LOGO_HEIGHT_RATIO = 0.38

# ==== Brand palette ====
BRAND_BG = (26, 20, 16)          # near-black brown (logo background)
BRAND_GOLD = (210, 168, 64)
BRAND_TAGLINE = (180, 170, 150)

# ==== Brand asset path ====
# Single source of truth for the logo. Same file is read by tg_autopost.
HERE = Path(__file__).resolve().parent       # automation/
LOGO_PATH = HERE / "brand" / "logo.png"


# Style suffix appended to every blog hero prompt. Tuned for the brand
# composition: subject pushed to the right of frame so the leftmost 40%
# (which gets cropped and replaced with the brand panel) doesn't lose key
# detail.
STYLE_SUFFIX = (
    " Photorealistic editorial style, dark cinematic lighting. "
    "Deep navy and obsidian palette with subtle gold accents. "
    "Soft volumetric shadows, high detail, shallow depth of field where "
    "appropriate. "
    "COMPOSITION CRITICAL: place the main subject and the visual center of "
    "interest in the RIGHT HALF of the frame, around 65-75% horizontally "
    "from the left edge. The leftmost 40% of the frame will be cropped — "
    "it should be a continuation of the dark atmospheric background, "
    "without important detail. Do NOT push the subject all the way to the "
    "right edge — keep meaningful image content visible across the right "
    "half, not just the right corner. "
    "ABSOLUTELY NO TEXT of any kind anywhere in the image — no letters, no "
    "numbers, no signage, no UI labels. "
    "NO HUMAN FACES, no recognizable people, no photographs of identifiable "
    "individuals. Hands are acceptable if abstract. "
    "NO real-room or platform logos — no PPPoker, PokerBros, ClubGG, "
    "WSOP, PokerStars, GGPoker, or any other branded mark. "
    "NO copyrighted character art."
)


# ==== Brand panel composition ====

def _build_brand_panel(panel_w: int, panel_h: int):
    """Build the LEFT-side brand panel: dark background + logo + wordmark.

    Identical for every article — that's the point. Built fresh each call
    rather than cached to avoid Pillow Image-object reuse issues if we ever
    parallelise.
    """
    from PIL import Image, ImageDraw

    panel = Image.new("RGB", (panel_w, panel_h), BRAND_BG)
    draw = ImageDraw.Draw(panel)

    logo_resized = None
    logo_y = panel_h // 2

    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            target_h = int(panel_h * LOGO_HEIGHT_RATIO)
            scale = target_h / logo.height
            logo_resized = logo.resize(
                (int(logo.width * scale), int(logo.height * scale)),
                Image.LANCZOS,
            )
            logo_x = (panel_w - logo_resized.width) // 2
            logo_y = (panel_h - logo_resized.height) // 2 - 60
            panel.paste(logo_resized, (logo_x, logo_y), logo_resized)
        except Exception as e:
            print(f"⚠️  Logo load/paste failed: {e}")
    else:
        print(f"⚠️  Logo not found at {LOGO_PATH} — brand panel will be text-only")

    font_brand = _load_font([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ], size=44)
    font_tag = _load_font([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ], size=22)

    brand_text = "PokerNet AI"
    tag_text = "Managed AI bot infrastructure"

    bw = draw.textlength(brand_text, font=font_brand)
    tw = draw.textlength(tag_text, font=font_tag)

    if logo_resized is not None:
        wordmark_y = logo_y + logo_resized.height + 36
    else:
        wordmark_y = panel_h // 2

    draw.text(
        ((panel_w - bw) // 2, wordmark_y),
        brand_text,
        fill=BRAND_GOLD,
        font=font_brand,
    )
    draw.text(
        ((panel_w - tw) // 2, wordmark_y + 58),
        tag_text,
        fill=BRAND_TAGLINE,
        font=font_tag,
    )

    return panel


def _load_font(candidate_paths, size):
    """Try a list of font file paths, return the first one that loads.
    Falls back to PIL default font if none work."""
    from PIL import ImageFont
    for p in candidate_paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _compose_with_brand_panel(ai_image):
    """Take the raw AI image (1536×1024), crop its rightmost VISIBLE_RIGHT_RATIO
    portion, paste onto a canvas with the brand panel on the left, blend
    the seam with a soft horizontal gradient.
    """
    from PIL import Image

    if ai_image.width != CANVAS_W or ai_image.height != CANVAS_H:
        ai_image = ai_image.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BRAND_BG)
    brand_panel = _build_brand_panel(LEFT_W, CANVAS_H)
    canvas.paste(brand_panel, (0, 0))

    ai_keep_w = int(CANVAS_W * VISIBLE_RIGHT_RATIO)
    ai_crop_left = CANVAS_W - ai_keep_w
    ai_right = ai_image.crop((ai_crop_left, 0, CANVAS_W, CANVAS_H))
    ai_right = ai_right.resize((RIGHT_W, CANVAS_H), Image.LANCZOS)
    canvas.paste(ai_right, (LEFT_W, 0))

    canvas = _blend_seam(canvas)
    return canvas


def _blend_seam(canvas):
    """Soften the boundary between brand panel and AI image. Pure Pillow,
    no numpy — keeps the dependency surface to what's already in
    requirements.txt.
    """
    from PIL import Image

    seam_x = LEFT_W
    blend_w = SEAM_BLEND_WIDTH

    strip = Image.new("RGBA", (blend_w, CANVAS_H), BRAND_BG + (0,))
    strip_pixels = strip.load()
    for x in range(blend_w):
        alpha = int(255 * (1 - x / max(blend_w - 1, 1)))
        for y in range(CANVAS_H):
            strip_pixels[x, y] = BRAND_BG + (alpha,)

    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(strip, dest=(seam_x, 0))
    return canvas_rgba.convert("RGB")


# ==== Main entry point ====

def generate_hero_image(image_prompt: str, target_dir: Path) -> Path | None:
    """
    Generate a hero image and save as `hero.webp` in target_dir.
    Returns the saved path, or None on AI-call failure (article ships without hero).
    On composition failure, falls back to saving the raw AI PNG without brand panel.
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

    full_prompt = image_prompt.strip() + STYLE_SUFFIX

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

    # Composite with brand panel + convert to WebP
    try:
        with Image.open(BytesIO(png_bytes)) as ai_img:
            if ai_img.mode in ("RGBA", "LA", "P"):
                ai_img = ai_img.convert("RGB")
            final = _compose_with_brand_panel(ai_img)
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / HERO_FILENAME
            final.save(out_path, format="WEBP", quality=WEBP_QUALITY, method=6)
    except Exception as e:
        print(f"⚠️  Composition or WebP conversion failed: {type(e).__name__}: {e}")
        # Fallback: save raw PNG without brand panel so the article still gets a hero
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = target_dir / "hero.png"
            fallback_path.write_bytes(png_bytes)
            png_kb = len(png_bytes) / 1024
            print(f"⚠️  Saved fallback PNG (no brand panel): {fallback_path} ({png_kb:.0f} KB)")
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
    return f"Illustration for article: {h1_title}"
