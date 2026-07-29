# Core/ui/pill/render.py
#
# Composes one frame of the popup: the black capsule, its two buttons,
# whichever indicator is active, and the transcript line.
#
# Layout note — the transcript sits *above* the capsule, not below it as
# originally sketched. That's forced by geometry, not preference: the
# capsule lives at the bottom edge of the screen, so there is no room
# beneath it. Both live on one canvas (one window, one blit) rather than
# in two windows, which keeps them from ever drifting apart.

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- capsule geometry (matches the Typeless reference's proportions:
#     a wide, short capsule with circular buttons inset at each end) ---
PILL_W, PILL_H = 208, 46
BTN_D = 32
BTN_INSET = 7

# Room around the capsule for its drop shadow and the ribbon's bloom.
PAD = 20

# Transcript band above the capsule. Wider than the capsule so a spoken
# sentence has somewhere to go without wrapping to three lines.
TEXT_H = 44
TEXT_GAP = 12
CANVAS_W = 620
CANVAS_H = TEXT_H + TEXT_GAP + PILL_H + PAD * 2

PILL_X = (CANVAS_W - PILL_W) // 2
PILL_Y = PAD + TEXT_H + TEXT_GAP

PILL_BG = (11, 11, 13, 244)
BTN_CANCEL_BG = (58, 58, 61, 255)
BTN_ACCEPT_BG = (247, 247, 249, 255)
GLYPH_LIGHT = (255, 255, 255, 240)
GLYPH_DARK = (16, 16, 18, 255)
TEXT_COLOR = (238, 240, 244, 255)
TEXT_SHADOW = (0, 0, 0, 190)

# Per-state accent, used only as a faint rim + outer glow on the capsule.
# The indicator animation is the primary state signal; this exists so a
# transition is unmistakable even at a glance.
STATE_ACCENT = {
    "idle":      (120, 130, 145),
    "listening": (90, 200, 255),
    "thinking":  (170, 130, 255),
    "speaking":  (255, 180, 90),
    "working":   (90, 230, 170),
}

STATE_ACCENT_STRENGTH = {
    "idle": 0.20,
    "listening": 0.85,
    "thinking": 0.85,
    "speaking": 0.85,
    "working": 0.85,
}


def _load_font(size=15):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


_FONT = _load_font(15)


def button_centres():
    """Screen-independent button centres within the canvas, shared with
    the window's hit testing so the clickable area always matches what
    was actually drawn."""
    cy = PILL_Y + PILL_H / 2.0
    left = (PILL_X + BTN_INSET + BTN_D / 2.0, cy)
    right = (PILL_X + PILL_W - BTN_INSET - BTN_D / 2.0, cy)
    return left, right


def indicator_box():
    """(x, y, w, h) of the region between the two buttons."""
    (lx, _), (rx, _) = button_centres()
    x0 = lx + BTN_D / 2.0 + 8
    x1 = rx - BTN_D / 2.0 - 8
    return int(x0), int(PILL_Y + 6), int(x1 - x0), int(PILL_H - 12)


def _draw_glyph_x(draw, cx, cy, r):
    d = r * 0.42
    for a, b in (((-d, -d), (d, d)), ((-d, d), (d, -d))):
        draw.line(
            [(cx + a[0], cy + a[1]), (cx + b[0], cy + b[1])],
            fill=GLYPH_LIGHT, width=2,
        )


def _draw_glyph_check(draw, cx, cy, r):
    draw.line(
        [
            (cx - r * 0.42, cy + r * 0.02),
            (cx - r * 0.10, cy + r * 0.34),
            (cx + r * 0.46, cy - r * 0.34),
        ],
        fill=GLYPH_DARK, width=3, joint="curve",
    )


def _truncate(draw, text, font, max_w):
    if draw.textlength(text, font=font) <= max_w:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ellipsis, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis


def render_pill(
    state: str,
    phase: float,
    level: float,
    indicator,
    transcript: str = "",
    show_buttons: bool = True,
) -> Image.Image:
    """One full RGBA frame, canvas-sized, ready for UpdateLayeredWindow."""
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

    accent = STATE_ACCENT.get(state, STATE_ACCENT["idle"])
    strength = STATE_ACCENT_STRENGTH.get(state, 0.2)

    pill_box = [PILL_X, PILL_Y, PILL_X + PILL_W, PILL_Y + PILL_H]
    radius = PILL_H / 2.0

    # --- shadow + accent glow, drawn on their own layer and blurred so
    #     the capsule appears to sit above the desktop rather than on it
    glow_layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.rounded_rectangle(
        [pill_box[0] - 3, pill_box[1] - 2, pill_box[2] + 3, pill_box[3] + 4],
        radius=radius + 3,
        fill=(0, 0, 0, 150),
    )
    gd.rounded_rectangle(
        pill_box, radius=radius,
        outline=accent + (int(150 * strength),), width=3,
    )
    canvas = Image.alpha_composite(
        canvas, glow_layer.filter(ImageFilter.GaussianBlur(7))
    )

    draw = ImageDraw.Draw(canvas)

    # --- capsule body
    draw.rounded_rectangle(pill_box, radius=radius, fill=PILL_BG)
    draw.rounded_rectangle(
        pill_box, radius=radius,
        outline=accent + (int(90 * strength),), width=1,
    )

    # --- indicator
    ix, iy, iw, ih = indicator_box()
    if iw > 4 and ih > 4:
        vis = indicator.render(iw, ih, state, phase, level)
        canvas.alpha_composite(vis, (ix, iy))

    # --- buttons
    if show_buttons:
        (lcx, lcy), (rcx, rcy) = button_centres()
        r = BTN_D / 2.0
        draw.ellipse([lcx - r, lcy - r, lcx + r, lcy + r], fill=BTN_CANCEL_BG)
        _draw_glyph_x(draw, lcx, lcy, r)
        draw.ellipse([rcx - r, rcy - r, rcx + r, rcy + r], fill=BTN_ACCEPT_BG)
        _draw_glyph_check(draw, rcx, rcy, r)

    # --- transcript
    if transcript:
        text = _truncate(draw, transcript, _FONT, CANVAS_W - 40)
        w = draw.textlength(text, font=_FONT)
        tx = (CANVAS_W - w) / 2.0
        ty = PAD + (TEXT_H - 18) / 2.0
        # Shadow first: the text floats over arbitrary desktop content,
        # so it needs its own contrast rather than relying on a backdrop.
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (0, 2)):
            draw.text((tx + dx, ty + dy), text, font=_FONT, fill=TEXT_SHADOW)
        draw.text((tx, ty), text, font=_FONT, fill=TEXT_COLOR)

    return canvas


def canvas_origin_for_bottom_centre(work_right, work_bottom, work_left=0, margin=18):
    """
    Screen position that puts the *capsule* — not the canvas — at
    bottom-centre with `margin` of clearance, since the canvas carries
    empty transcript space above it that must not push it upward.
    """
    x = work_left + ((work_right - work_left) - CANVAS_W) // 2
    y = work_bottom - margin - (PAD + PILL_H) - (TEXT_H + TEXT_GAP)
    return int(x), int(y)
