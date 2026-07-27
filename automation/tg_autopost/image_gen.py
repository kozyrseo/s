"""
Image generation for tg_autopost. Uses gpt-image-1 with a hardened style suffix
specifically for the PokerNet AI channel:
  - dark editorial palette (navy / obsidian / gold)
  - abstract operational scenes preferred over generic "chips on felt"
  - hard ban on text, faces, real-room logos (PPPoker / PokerBros / ClubGG / etc)
  - composition keeps the subject inside the visible RIGHT slice after our crop

Brand template:
  Every final image is composed as a 1536×1024 canvas with:
    - LEFT 35% — fixed brand panel: dark background, gold spade logo, wordmark
    - RIGHT 65% — the AI-generated illustration cropped to that area
  The brand panel and the cropped AI image are joined with a short horizontal
  gradient transition so the seam isn't visually jarring.

PNG → WebP conversion via Pillow. Failure of either AI generation OR composition
is non-fatal — the post can still ship without a hero image, the bot will use
sendMessage in that case.

Tuning constants live at the top of this file. The defaults here are the result
of iterating on real generated images:
  - LOGO_HEIGHT_RATIO 0.38: 50% felt cramped (logo touched panel edges)
  - SEAM_BLEND_WIDTH 80: enough to soften the join, narrow enough to not eat
    meaningful image area
  - VISIBLE_RIGHT_RATIO 0.60: smaller-than-panel-ratio because we want some
    of the AI image's left side (where the model often puts secondary detail)
    visible, not just the rightmost slice
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

# ==== Canvas / layout ====
CANVAS_W, CANVAS_H = 1536, 1024
LEFT_PANEL_RATIO = 0.35                    # brand panel is 35% of width
LEFT_W = int(CANVAS_W * LEFT_PANEL_RATIO)  # 537 px
RIGHT_W = CANVAS_W - LEFT_W                # 999 px

# How much of the AI image we actually keep (from the right side).
# 0.60 = keep the right 60%, discard the left 40%. We want this < the
# brand-panel ratio's complement (0.65) so we shift the crop window slightly
# to the left, keeping more of the AI subject visible.
VISIBLE_RIGHT_RATIO = 0.60

# Soft seam blend in pixels — how wide the gradient transition between
# brand panel and AI image is.
SEAM_BLEND_WIDTH = 80

# Logo size as fraction of canvas height. 0.38 looks balanced; 0.50 was too
# big (touched panel edges), 0.30 felt small.
LOGO_HEIGHT_RATIO = 0.38

# ==== Brand palette ====
BRAND_BG = (26, 20, 16)          # near-black brown (logo background)
BRAND_GOLD = (210, 168, 64)
BRAND_TAGLINE = (180, 170, 150)

# ==== Brand asset paths ====
# Logo lives at automation/brand/logo.png — shared between this Telegram
# pipeline and the blog hero pipeline (automation/image_gen.py). One file,
# one source of truth, no copies to keep in sync.
HERE = Path(__file__).resolve().parent       # automation/tg_autopost/
LOGO_PATH = HERE.parent / "brand" / "logo.png"


# This suffix is appended to EVERY prompt. It encodes the channel's visual
# identity. Tuned for the brand template: subject is concentrated in the
# RIGHT-CENTER region (NOT the right edge, NOT the center of the full frame),
# because we crop the right 60% of the image for display.
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

def _build_brand_panel(panel_w: int, panel_h: int) -> "Image.Image":
    """Build the LEFT-side brand panel: dark background + logo + wordmark.

    Identical for every post — that's the whole point. Built fresh each
    call rather than cached because Pillow Image objects aren't trivially
    safe to share if we ever go concurrent.
    """
    from PIL import Image, ImageDraw

    panel = Image.new("RGB", (panel_w, panel_h), BRAND_BG)
    draw = ImageDraw.Draw(panel)

    # ---- Logo (centered, scaled per LOGO_HEIGHT_RATIO) ----
    logo_resized = None
    logo_y = panel_h // 2  # default fallback for wordmark positioning

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
            # Center vertically with slight upward bias to leave room for wordmark
            logo_y = (panel_h - logo_resized.height) // 2 - 60
            panel.paste(logo_resized, (logo_x, logo_y), logo_resized)
        except Exception as e:
            print(f"⚠️  Logo load/paste failed: {e}")
    else:
        print(f"⚠️  Logo not found at {LOGO_PATH} — brand panel will be text-only")

    # ---- Wordmark + tagline ----
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


def _load_font(candidate_paths: list[str], size: int):
    """Try a list of font file paths, return the first one that loads.
    Falls back to PIL default font if none work."""
    from PIL import ImageFont
    for p in candidate_paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _compose_with_brand_panel(ai_image: "Image.Image") -> "Image.Image":
    """Take the raw AI image (1536×1024), crop its rightmost VISIBLE_RIGHT_RATIO
    portion, paste onto a canvas with the brand panel on the left, and blend
    the seam with a short horizontal gradient.

    Returns the final 1536×1024 RGB image.
    """
    from PIL import Image

    # Defensive resize if the model returned a slightly different size
    if ai_image.width != CANVAS_W or ai_image.height != CANVAS_H:
        ai_image = ai_image.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)

    # 1. Brand panel on the left
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BRAND_BG)
    brand_panel = _build_brand_panel(LEFT_W, CANVAS_H)
    canvas.paste(brand_panel, (0, 0))

    # 2. Crop the AI image to keep VISIBLE_RIGHT_RATIO of its width.
    #    Then resize that cropped slice to fit RIGHT_W exactly.
    ai_keep_w = int(CANVAS_W * VISIBLE_RIGHT_RATIO)
    ai_crop_left = CANVAS_W - ai_keep_w
    ai_right = ai_image.crop((ai_crop_left, 0, CANVAS_W, CANVAS_H))
    ai_right = ai_right.resize((RIGHT_W, CANVAS_H), Image.LANCZOS)
    canvas.paste(ai_right, (LEFT_W, 0))

    # 3. Soft seam: blend the first SEAM_BLEND_WIDTH pixels of the right side
    #    against the brand background using a horizontal alpha ramp. This
    #    erases the visible vertical seam without losing meaningful AI content.
    canvas = _blend_seam(canvas)

    return canvas


def _blend_seam(canvas: "Image.Image") -> "Image.Image":
    """Add a soft horizontal gradient at the boundary between brand panel
    and AI image, so the join doesn't look like a hard cut."""
    from PIL import Image
    import numpy as np

    arr = np.array(canvas).astype(np.float32)

    seam_x = LEFT_W
    blend_w = SEAM_BLEND_WIDTH

    # For columns [seam_x, seam_x + blend_w), blend with BRAND_BG using a
    # 0..1 ramp so column seam_x = 100% brand bg, column seam_x+blend_w = 100% AI.
    # Linear ramp; could be smoothstep for a gentler curve but linear is fine.
    bg = np.array(BRAND_BG, dtype=np.float32)
    for offset in range(blend_w):
        col = seam_x + offset
        if col >= CANVAS_W:
            break
        # ramp 0 (full bg) → 1 (full AI)
        t = offset / blend_w
        arr[:, col, :] = arr[:, col, :] * t + bg * (1 - t)

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# ==== Main entry point ====

def generate_hero_image(image_prompt: str, target_dir: Path,
                        filename: str = HERO_FILENAME) -> Path | None:
    """
    Generate a channel hero image: AI-generate via gpt-image-1, then composite
    with the brand panel template, save to target_dir/filename as WebP.
    Returns the saved path, or None on any failure (caller proceeds without image).
    """
    if not image_prompt or not image_prompt.strip():
        print("ℹ️  No image_prompt provided — skipping image generation")
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("⚠️  OPENAI_API_KEY not set — skipping image generation")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("⚠️  openai package missing — skipping image generation")
        return None

    try:
        from PIL import Image
    except ImportError:
        print("⚠️  Pillow missing — skipping image generation")
        return None

    full_prompt = image_prompt.strip() + STYLE_SUFFIX

    try:
        client = OpenAI(api_key=api_key)
        print(f"🎨 Generating image ({IMAGE_MODEL}, {IMAGE_SIZE}, {IMAGE_QUALITY})")
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=full_prompt,
            size=IMAGE_SIZE,
            quality=IMAGE_QUALITY,
            n=1,
        )
    except Exception as e:
        print(f"⚠️  OpenAI image API failed: {type(e).__name__}: {e}")
        return None

    try:
        b64 = response.data[0].b64_json
        if not b64:
            print("⚠️  OpenAI returned no image data")
            return None
        png_bytes = base64.b64decode(b64)
    except (AttributeError, IndexError) as e:
        print(f"⚠️  Unexpected response shape from OpenAI: {e}")
        return None

    # Composite with brand panel + convert to WebP
    try:
        with Image.open(BytesIO(png_bytes)) as ai_img:
            if ai_img.mode in ("RGBA", "LA", "P"):
                ai_img = ai_img.convert("RGB")
            final = _compose_with_brand_panel(ai_img)
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / filename
            final.save(out_path, format="WEBP", quality=WEBP_QUALITY, method=6)
    except Exception as e:
        print(f"⚠️  Composition or WebP conversion failed: {type(e).__name__}: {e}")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fallback = target_dir / filename.replace(".webp", ".png")
            fallback.write_bytes(png_bytes)
            print(f"⚠️  Saved raw AI PNG (no brand panel): {fallback}")
            return fallback
        except Exception as e2:
            print(f"⚠️  PNG fallback also failed: {e2}")
            return None

    size_kb = out_path.stat().st_size / 1024
    png_kb = len(png_bytes) / 1024
    print(f"✅ Image saved: {out_path} ({size_kb:.0f} KB WebP, was {png_kb:.0f} KB raw PNG)")
    return out_path
