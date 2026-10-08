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
#
# DPI: the constants below are the 1x design. render_pill(scale=...) draws
# the whole frame at scale (1.5 = a 150% display) rather than enlarging a 1x
# bitmap, so lines and text stay sharp; button_centres/indicator_box/canvas_size
# take the same scale, and the window's hit testing uses them, so what is
# clickable is exactly what was drawn.

import functools

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- capsule geometry (matches the Typeless reference's proportions:
#     a wide, short capsule with circular buttons inset at each end) ---
PILL_H = 46
BTN_D = 32
BTN_INSET = 7
BTN_GAP = 8  # spacing between adjacent buttons (matches indicator_box's gap)

# Base two-button width, widened by one more button + its gap to fit the
# type-a-message button next to accept, on the same right-hand end.
PILL_W = 208 + BTN_D + BTN_GAP

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
PLATE_BG = (11, 11, 13, 228)              # backing plate behind the caption: white text alone vanishes on a bright desktop
WARN_BG = (110, 16, 18, 238)
WARN_RIM = (255, 120, 120, 235)
LINE_H = 19

# Per-state accent, used only as a faint rim + outer glow on the capsule.
# The indicator animation is the primary state signal; this exists so a
# transition is unmistakable even at a glance.
STATE_ACCENT = {
    "idle":      (120, 130, 145),
    "listening": (90, 200, 255),
    "thinking":  (170, 130, 255),
    "speaking":  (255, 180, 90),
    "working":   (90, 230, 170),
    "alert":     (255, 90, 90),           # a proactive warning (scam watch) on an otherwise idle pill
}

STATE_ACCENT_STRENGTH = {
    "idle": 0.20,
    "listening": 0.85,
    "thinking": 0.85,
    "speaking": 0.85,
    "working": 0.85,
    "alert": 1.0,
}


def _load_font(size=15):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


@functools.lru_cache(maxsize=8)
def _font(scale=1.0):
    return _load_font(max(1, round(15 * scale)))


def _px(v, scale):
    """A 1x design length at this DPI scale, at least 1 px so hairlines survive."""
    return max(1, round(v * scale))


def canvas_size(scale=1.0):
    return round(CANVAS_W * scale), round(CANVAS_H * scale)


def button_centres(scale=1.0):
    """Button centres within the canvas at `scale` (1.0 = the 1x design, 2.0 = a 200% display), shared with
    the window's hit testing so the clickable area always matches what
    was actually drawn.

    Three buttons, left to right: cancel, accept, type. `type` takes the
    pill's far-right slot (where accept used to sit before the pill was
    widened) and accept shifts one button-width + gap to the left of it,
    so the cancel/indicator/accept layout other code already expects is
    unchanged — only the new button is appended past the old right edge.
    """
    cy = PILL_Y + PILL_H / 2.0
    left = (PILL_X + BTN_INSET + BTN_D / 2.0, cy)
    type_btn = (PILL_X + PILL_W - BTN_INSET - BTN_D / 2.0, cy)
    right = (type_btn[0] - BTN_D - BTN_GAP, cy)
    return tuple((x * scale, y * scale) for x, y in (left, right, type_btn))


def indicator_box(scale=1.0):
    """(x, y, w, h) of the region between the cancel and accept buttons
    (not the type button, which lives further right of accept)."""
    (lx, _), (rx, _), _ = button_centres()
    x0 = lx + BTN_D / 2.0 + 8
    x1 = rx - BTN_D / 2.0 - 8
    return round(x0 * scale), round((PILL_Y + 6) * scale), round((x1 - x0) * scale), round((PILL_H - 12) * scale)


def _draw_glyph_x(draw, cx, cy, r, width=2):
    d = r * 0.42
    for a, b in (((-d, -d), (d, d)), ((-d, d), (d, -d))):
        draw.line(
            [(cx + a[0], cy + a[1]), (cx + b[0], cy + b[1])],
            fill=GLYPH_LIGHT, width=width,
        )


def _draw_glyph_check(draw, cx, cy, r, width=3):
    draw.line(
        [
            (cx - r * 0.42, cy + r * 0.02),
            (cx - r * 0.10, cy + r * 0.34),
            (cx + r * 0.46, cy - r * 0.34),
        ],
        fill=GLYPH_DARK, width=width, joint="curve",
    )


def _draw_glyph_type(draw, cx, cy, r, width=2):
    """Text-cursor (I-beam) glyph for the type-a-message button — same
    line-drawing approach as the X and check glyphs above, not a
    separate rendering technique."""
    h = r * 0.5
    w = r * 0.22
    draw.line([(cx, cy - h), (cx, cy + h)], fill=GLYPH_LIGHT, width=width)
    draw.line([(cx - w, cy - h), (cx + w, cy - h)], fill=GLYPH_LIGHT, width=width)
    draw.line([(cx - w, cy + h), (cx + w, cy + h)], fill=GLYPH_LIGHT, width=width)


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


def _wrap(draw, text, font, max_w, max_lines=2):
    """Greedy word wrap into at most max_lines; what does not fit ends the last line with an ellipsis."""
    words, lines, cur = text.split(), [], ""
    for i, wd in enumerate(words):
        t = f"{cur} {wd}".strip()
        if not cur or draw.textlength(t, font=font) <= max_w:
            cur = t
        elif len(lines) == max_lines - 1:
            return lines + [_truncate(draw, " ".join([cur] + words[i:]), font, max_w)]
        else:
            lines.append(_truncate(draw, cur, font, max_w))
            cur = wd
    return lines + [_truncate(draw, cur, font, max_w)] if cur else lines


def render_pill(
    state: str,
    phase: float,
    level: float,
    indicator,
    transcript: str = "",
    show_buttons: bool = True,
    warn: bool = False,
    hint: str = "",
    scale: float = 1.0,
) -> Image.Image:
    """One full RGBA frame, canvas_size(scale), ready for UpdateLayeredWindow. Everything is drawn at `scale` (not enlarged afterwards), so it is as crisp at 200% as at 100%."""
    k = scale
    cw, ch = canvas_size(k)
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    accent = STATE_ACCENT.get(state, STATE_ACCENT["idle"])
    strength = STATE_ACCENT_STRENGTH.get(state, 0.2)

    pill_box = [PILL_X * k, PILL_Y * k, (PILL_X + PILL_W) * k, (PILL_Y + PILL_H) * k]
    radius = PILL_H * k / 2.0

    # --- shadow + accent glow, drawn on their own layer and blurred so
    #     the capsule appears to sit above the desktop rather than on it
    glow_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.rounded_rectangle(
        [pill_box[0] - 3 * k, pill_box[1] - 2 * k, pill_box[2] + 3 * k, pill_box[3] + 4 * k],
        radius=radius + 3 * k,
        fill=(0, 0, 0, 150),
    )
    gd.rounded_rectangle(
        pill_box, radius=radius,
        outline=accent + (int(150 * strength),), width=_px(3, k),
    )
    canvas = Image.alpha_composite(
        canvas, glow_layer.filter(ImageFilter.GaussianBlur(7 * k))
    )

    draw = ImageDraw.Draw(canvas)

    # --- capsule body
    draw.rounded_rectangle(pill_box, radius=radius, fill=PILL_BG)
    draw.rounded_rectangle(
        pill_box, radius=radius,
        outline=accent + (int(90 * strength),), width=_px(1, k),
    )

    # --- indicator
    ix, iy, iw, ih = indicator_box(k)
    if iw > 4 and ih > 4:
        vis = indicator.render(iw, ih, state, phase, level, scale=k)
        canvas.alpha_composite(vis, (ix, iy))

    # --- buttons
    if show_buttons:
        (lcx, lcy), (rcx, rcy), (tcx, tcy) = button_centres(k)
        r = BTN_D * k / 2.0
        draw.ellipse([lcx - r, lcy - r, lcx + r, lcy + r], fill=BTN_CANCEL_BG)
        _draw_glyph_x(draw, lcx, lcy, r, _px(2, k))
        draw.ellipse([rcx - r, rcy - r, rcx + r, rcy + r], fill=BTN_ACCEPT_BG)
        _draw_glyph_check(draw, rcx, rcy, r, _px(3, k))
        draw.ellipse([tcx - r, tcy - r, tcx + r, tcy + r], fill=BTN_CANCEL_BG)
        _draw_glyph_type(draw, tcx, tcy, r, _px(2, k))

    # --- transcript (or, when there is none, the hint for the button under the mouse)
    # Collapse whitespace: draw.textlength() raises ValueError on an embedded newline (seen live with a multiline proactive message), and _wrap does the line breaking.
    transcript = " ".join((transcript or hint).split())
    if transcript:
        font, line_h = _font(k), LINE_H * k
        lines = _wrap(draw, transcript, font, (CANVAS_W - 80) * k)
        widths = [draw.textlength(ln, font=font) for ln in lines]
        badge = (26 if warn else 0) * k                          # room for the "!" badge
        top = (PAD + (TEXT_H - len(lines) * LINE_H) / 2.0) * k
        half = max(widths) / 2.0 + badge / 2.0
        draw.rounded_rectangle(
            [cw / 2.0 - half - 14 * k, top - 5 * k, cw / 2.0 + half + 14 * k, top + len(lines) * line_h + 4 * k],
            radius=12 * k, fill=WARN_BG if warn else PLATE_BG, outline=WARN_RIM if warn else None, width=_px(1, k),
        )
        if warn:
            bx, by = cw / 2.0 - half - 14 * k + 22 * k, top + len(lines) * line_h / 2.0
            draw.ellipse([bx - 9 * k, by - 9 * k, bx + 9 * k, by + 9 * k], fill=WARN_RIM)
            draw.text((bx, by), "!", font=font, fill=GLYPH_DARK, anchor="mm")
        for i, (ln, w) in enumerate(zip(lines, widths)):
            tx = (cw + badge) / 2.0 - w / 2.0
            draw.text((tx, top + i * line_h + k), ln, font=font, fill=TEXT_SHADOW)
            draw.text((tx, top + i * line_h), ln, font=font, fill=TEXT_COLOR)

    return canvas


def canvas_origin_for_bottom_centre(work_right, work_bottom, work_left=0, margin=18, scale=1.0):
    """
    Screen position that puts the *capsule* — not the canvas — at
    bottom-centre with `margin` (1x px) of clearance, since the canvas carries
    empty transcript space above it that must not push it upward. Coordinates
    are signed: work_left may be negative (a monitor left of the primary).
    """
    cw, _ = canvas_size(scale)
    x = work_left + ((work_right - work_left) - cw) // 2
    y = work_bottom - round(margin * scale) - round((PAD + PILL_H + TEXT_H + TEXT_GAP) * scale)
    return int(x), int(y)
