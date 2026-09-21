"""Thumbnail generation.

Click-through rate multiplies every downstream revenue number, so the
thumbnail is worth real effort. The layout rules here are deliberately plain:
very few words, very large type, strong contrast, and a readable result at the
~210px width most viewers actually see.

Pillow is optional - without it the uploader simply lets YouTube pick a frame.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

log = logging.getLogger(__name__)

# Dark, saturated backgrounds read better against YouTube's light and dark UI
# than mid-tones do.
PALETTE = [
    ((11, 17, 38), (30, 58, 138)),
    ((26, 10, 36), (109, 40, 122)),
    ((10, 32, 28), (6, 95, 70)),
    ((38, 14, 10), (153, 44, 24)),
]


def _font(size: int):
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_thumbnail(
    text: str,
    out_path: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    accent_index: int = 0,
    kicker: str = "",
) -> Path | None:
    """Render a thumbnail. Returns ``None`` if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.info("Pillow not installed; skipping thumbnail generation")
        return None

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    top, bottom = PALETTE[accent_index % len(PALETTE)]
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)

    # Vertical gradient.
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=(
                int(top[0] + (bottom[0] - top[0]) * ratio),
                int(top[1] + (bottom[1] - top[1]) * ratio),
                int(top[2] + (bottom[2] - top[2]) * ratio),
            ),
        )

    # Accent bar gives the channel a consistent, recognisable frame.
    draw.rectangle([0, height - 18, width, height], fill=(250, 204, 21))

    # Four to six words maximum: more than that is unreadable at browse size.
    # Callers should pass a purpose-written phrase; capping a full title here
    # is a fallback that can cut mid-phrase.
    words = (text or "").split()
    headline = " ".join(words[:6]).upper()
    lines = textwrap.wrap(headline, width=15) or [""]
    lines = lines[:3]

    size = 120 if len(lines) <= 2 else 96
    font = _font(size)
    while size > 40:
        font = _font(size)
        widest = max(draw.textlength(line, font=font) for line in lines)
        if widest <= width * 0.88:
            break
        size -= 8

    line_height = size + 16
    total = line_height * len(lines)
    y = (height - total) // 2 + (20 if kicker else 0)

    for line in lines:
        w = draw.textlength(line, font=font)
        x = (width - w) / 2
        # Hard shadow keeps white type legible over any background.
        draw.text((x + 5, y + 5), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    if kicker:
        kfont = _font(44)
        ktext = kicker.upper()[:28]
        kw = draw.textlength(ktext, font=kfont)
        kx = (width - kw) / 2
        ky = (height - total) // 2 - 70
        draw.rectangle(
            [kx - 22, ky - 12, kx + kw + 22, ky + 58], fill=(250, 204, 21)
        )
        draw.text((kx, ky), ktext, font=kfont, fill=(0, 0, 0))

    img.save(out, "JPEG", quality=88)
    # YouTube rejects thumbnails over 2MB.
    if out.stat().st_size > 2_000_000:
        img.save(out, "JPEG", quality=70)
    return out
