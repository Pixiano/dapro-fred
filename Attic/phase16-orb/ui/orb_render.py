# Core/ui/orb_render.py
#
# Numpy/PIL rendering engine for the FRED orb — real per-pixel RGBA,
# fed to UpdateLayeredWindow by overlay_window.py for genuine desktop
# transparency (no browser engine, no frosted-glass compromise).
#
# Architecture: a metaball field (several soft circles blended via an
# inverse-square falloff, thresholded with a smooth edge) gives the
# organic squishy "water droplet" merge/split look cheaply in numpy.
# idle/thinking are steady-state animations with no live audio input,
# so they're pre-rendered into a short looping frame cache and replayed
# — the "record once, only compute on transition" behavior. listening/
# speaking react to live audio (mic RMS / TTS word pulses) and are
# short-lived interactive bursts, so they render live instead — a
# reasonable trade since they're brief, not the steady idle state the
# efficiency budget cares most about.

import time

import numpy as np
from PIL import Image, ImageFilter

CANVAS = 260
CENTER = CANVAS / 2.0

_ys, _xs = np.mgrid[0:CANVAS, 0:CANVAS].astype(np.float32)
_dist_from_center = np.sqrt((_xs - CENTER) ** 2 + (_ys - CENTER) ** 2)

LOOP_LEN = 60
LOOP_DURATION_SEC = 6.0
CACHED_STATES = {"idle", "thinking"}

# Single hue family (blue-cyan) across all states — small, deliberate
# deltas differentiate states, never a full rainbow sweep. Retune the
# whole palette by adjusting the "hue" values below.
STATE_PARAMS = {
    "idle":      dict(hue=0.53, sat=0.72, blob_scale=1.00, speed=0.55, swirl=0.0),
    "listening": dict(hue=0.55, sat=0.85, blob_scale=1.08, speed=0.95, swirl=0.0),
    "thinking":  dict(hue=0.60, sat=0.90, blob_scale=0.95, speed=1.9,  swirl=0.55),
    "speaking":  dict(hue=0.52, sat=0.85, blob_scale=1.00, speed=1.3,  swirl=0.0),
}


def _hsv_to_rgb(h, s, v):
    h = np.mod(h, 1.0)
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i = i.astype(np.int32) % 6
    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [v, q, p, p, t, v])
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [t, v, v, q, p, p])
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [p, p, t, v, v, q])
    return r, g, b


def _metaball_field(t, radius_px):
    blobs = [
        (CENTER + 15 * np.sin(t * 0.9),         CENTER + 11 * np.cos(t * 0.67), radius_px * 0.62),
        (CENTER + 13 * np.cos(t * 1.3 + 1.4),   CENTER + 15 * np.sin(t * 1.05 + 2.1), radius_px * 0.52),
        (CENTER + 11 * np.sin(t * 0.63 + 3.2),  CENTER - 13 * np.cos(t * 0.8 + 0.6),  radius_px * 0.44),
    ]
    field = np.zeros((CANVAS, CANVAS), dtype=np.float32)
    for (bx, by, r) in blobs:
        d2 = (_xs - bx) ** 2 + (_ys - by) ** 2 + 1.0
        field += (r * r) / d2
    return field


def render_frame(state, phase, mic_level=0.0, speak_pulse=0.0, base_radius=48, hue_offset=0.0):
    """
    `phase` is a single continuously-accumulated value (see OrbRenderer)
    driving every time-dependent effect below — metaball position, hue
    wobble, swirl rotation. It must never jump or reset on its own: the
    caller is responsible for advancing it smoothly frame to frame, at a
    rate that may vary by state (see OrbRenderer.get_frame), so that
    switching states never snaps the blob to a different point in its
    motion cycle — only style (color/size/effects) changes abruptly at a
    state transition, not position.
    """
    params = dict(STATE_PARAMS.get(state, STATE_PARAMS["idle"]))
    params["hue"] = (params["hue"] + hue_offset) % 1.0

    amp_boost = 1.0 + mic_level * 0.35 + speak_pulse * 0.3
    radius = base_radius * params["blob_scale"] * amp_boost

    field = _metaball_field(phase, radius)

    # Smooth threshold around field == 1 gives the gooey soft edge —
    # this is what makes overlapping blobs merge into one continuous
    # shape instead of visibly separate circles.
    edge = 0.18
    alpha = np.clip((field - (1 - edge)) / (2 * edge), 0, 1)

    dist_norm = np.clip(_dist_from_center / (CANVAS / 2), 0, 1)
    hue = (params["hue"] + dist_norm * 0.04 + 0.015 * np.sin(phase * 0.25)) % 1.0
    sat = np.clip(params["sat"] + 0.1 * (1 - dist_norm), 0, 1)
    val = np.clip(0.55 + 0.5 * alpha, 0, 1)

    r, g, b = _hsv_to_rgb(hue, sat, val)

    # Specular highlight — glassy sheen, offset toward upper-left.
    hl_cx, hl_cy = CENTER - radius * 0.32, CENTER - radius * 0.38
    hl_d2 = (_xs - hl_cx) ** 2 + (_ys - hl_cy) ** 2
    hl = 0.55 * np.exp(-hl_d2 / (2 * (radius * 0.28) ** 2)) * alpha

    core_r = r * (1 - hl) + hl
    core_g = g * (1 - hl) + hl
    core_b = b * (1 - hl) + hl
    core_a = np.clip(alpha + hl * 0.25, 0, 1)

    if params["swirl"] > 0:
        angle = np.arctan2(_ys - CENTER, _xs - CENTER)
        swirl_wave = 0.5 + 0.5 * np.sin(angle * 3 + phase * 3.0)
        swirl_mask = params["swirl"] * swirl_wave * alpha * 0.35
        core_r = np.clip(core_r + swirl_mask, 0, 1)
        core_g = np.clip(core_g + swirl_mask, 0, 1)
        core_b = np.clip(core_b + swirl_mask, 0, 1)

    # Outer glow — blur the alpha mask and use it as a soft halo behind
    # the core, colored at lower saturation/value. Real per-pixel alpha
    # means this actually bleeds into the desktop instead of sitting on
    # a visible panel.
    alpha_u8 = (alpha * 255).astype(np.uint8)
    glow_img = Image.fromarray(alpha_u8).filter(ImageFilter.GaussianBlur(radius * 0.35))
    glow_alpha = (np.asarray(glow_img).astype(np.float32) / 255.0) * 0.45

    glow_r, glow_g, glow_b = _hsv_to_rgb(
        np.full_like(glow_alpha, params["hue"]),
        np.full_like(glow_alpha, params["sat"] * 0.8),
        np.full_like(glow_alpha, 0.6),
    )

    final_alpha = np.clip(core_a + glow_alpha * (1 - core_a), 0, 1)
    has_glow = glow_alpha > 0.01
    final_r = np.where(has_glow, core_r * core_a + glow_r * (1 - core_a), core_r)
    final_g = np.where(has_glow, core_g * core_a + glow_g * (1 - core_a), core_g)
    final_b = np.where(has_glow, core_b * core_a + glow_b * (1 - core_a), core_b)

    rgba = np.zeros((CANVAS, CANVAS, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.clip(final_r * 255, 0, 255).astype(np.uint8)
    rgba[:, :, 1] = np.clip(final_g * 255, 0, 255).astype(np.uint8)
    rgba[:, :, 2] = np.clip(final_b * 255, 0, 255).astype(np.uint8)
    rgba[:, :, 3] = np.clip(final_alpha * 255, 0, 255).astype(np.uint8)

    return Image.fromarray(rgba, "RGBA")


class OrbRenderer:
    """
    Owns the per-state frame cache AND the single continuous animation
    phase shared by every state. idle/thinking replay a cached loop;
    listening/speaking render live to stay audio-reactive — but both
    paths read position from the same accumulating `_phase` value, which
    only ever advances (at whatever rate the current state's `speed`
    dictates) and never resets or jumps on a state change. That's what
    makes a transition change color/size/effects abruptly while the
    blob's actual position keeps flowing continuously through it,
    instead of visibly snapping to a different point in its cycle.
    """

    def __init__(self, base_radius=48):
        self.base_radius = base_radius
        self.hue_offset = 0.0
        self.expand_scale = 1.0
        self._loop_cache = {state: [None] * LOOP_LEN for state in CACHED_STATES}
        self._phase = 0.0
        self._last_time = time.time()

    def set_hue_offset(self, hue_offset: float):
        """hue_offset is 0..1 (fraction of the color wheel), added to
        every state's base hue. Invalidates the cache since idle/thinking
        frames are pre-rendered and won't reflect the new hue otherwise."""
        self.hue_offset = hue_offset % 1.0
        self._invalidate_cache()

    def set_expand(self, expanded: bool):
        """'Fullscreen' toggle — the window itself stays a fixed size
        (UpdateLayeredWindow always resizes to match the bitmap anyway,
        so a variable-size window fights itself); expanding just means a
        bigger blob within the same canvas."""
        self.expand_scale = 1.35 if expanded else 1.0
        self._invalidate_cache()

    def _invalidate_cache(self):
        self._loop_cache = {state: [None] * LOOP_LEN for state in CACHED_STATES}

    def get_frame(self, state, mic_level=0.0, speak_pulse=0.0):
        now = time.time()
        dt = max(0.0, min(now - self._last_time, 0.25))  # clamp huge gaps (e.g. after a debugger pause)
        self._last_time = now

        speed = STATE_PARAMS.get(state, STATE_PARAMS["idle"])["speed"]
        self._phase += dt * speed

        radius = self.base_radius * self.expand_scale

        if state in CACHED_STATES:
            cache = self._loop_cache[state]
            # Which cached frame corresponds to the current phase, not a
            # free-running counter — this is what lets re-entering a
            # cached state resume mid-loop instead of restarting at 0.
            idx = int((self._phase % LOOP_DURATION_SEC) / LOOP_DURATION_SEC * LOOP_LEN) % LOOP_LEN
            if cache[idx] is None:
                synthetic_phase = (idx / LOOP_LEN) * LOOP_DURATION_SEC
                cache[idx] = render_frame(
                    state, synthetic_phase, 0.0, 0.0, radius, self.hue_offset
                )
            return cache[idx]

        return render_frame(state, self._phase, mic_level, speak_pulse, radius, self.hue_offset)
