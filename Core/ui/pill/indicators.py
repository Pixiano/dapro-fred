# Core/ui/pill/indicators.py
#
# The two interchangeable visualisations that live in the middle of the
# pill. Both implement the same interface, and pill_app picks one at
# random per activation so they can be judged side by side in real use
# rather than in the abstract:
#
#   BarsIndicator   — the Typeless reference: white vertical bars,
#                     collapsing to a row of dots at rest.
#   RibbonIndicator — the iOS 27 Siri reference: a folded ribbon of
#                     spectrum light, cyan through white to amber/red.
#
# Note the ribbon is deliberately a full spectrum. That contradicts the
# earlier "one colour set, not a rainbow" instruction, but it's what the
# supplied Siri reference actually looks like, so the reference wins.
# BarsIndicator is the monochrome option if that call turns out wrong.
#
# States both must handle: idle, listening, thinking, speaking, working.
# `level` is real audio amplitude (mic RMS while listening, TTS PCM while
# speaking); thinking/working have no audio and animate off `phase` alone.

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

WHITE = (255, 255, 255)


class Indicator:
    name = "base"

    def render(self, width: int, height: int, state: str, phase: float, level: float) -> Image.Image:
        raise NotImplementedError


# =========================================================
# BARS — the Typeless look
# =========================================================

class BarsIndicator:
    """
    A fixed row of bars. At rest they shrink to their minimum and read as
    a dotted line, which is exactly how the reference behaves; as level
    rises they grow. A short rolling history makes the bars scroll like a
    waveform rather than pumping in unison.
    """

    name = "bars"

    def __init__(self, bar_count=11, bar_w=2, gap=4):
        self.bar_count = bar_count
        self.bar_w = bar_w
        self.gap = gap
        self._history = np.zeros(bar_count, dtype=np.float32)

    def render(self, width, height, state, phase, level):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        n = self.bar_count
        amps = self._amplitudes(state, phase, level, n)

        span = n * self.bar_w + (n - 1) * self.gap
        x0 = (width - span) / 2.0
        cy = height / 2.0
        max_h = height * 0.62
        min_h = float(self.bar_w)  # collapses to a dot

        for i, a in enumerate(amps):
            h = min_h + (max_h - min_h) * float(np.clip(a, 0.0, 1.0))
            x = x0 + i * (self.bar_w + self.gap)
            # Rounded caps: a dot at rest, a capsule when tall.
            draw.rounded_rectangle(
                [x, cy - h / 2.0, x + self.bar_w, cy + h / 2.0],
                radius=self.bar_w / 2.0,
                fill=WHITE + (235,),
            )
        return img

    def _amplitudes(self, state, phase, level, n):
        idx = np.arange(n, dtype=np.float32)

        if state == "listening" or state == "speaking":
            # Shift history left and push the newest sample in, so energy
            # visibly travels across the bars.
            self._history[:-1] = self._history[1:]
            jitter = 0.75 + 0.25 * np.sin(phase * 11.0 + n)
            self._history[-1] = float(np.clip(level, 0.0, 1.0)) * jitter
            # Taper the ends so the row reads as a shape, not a bar chart.
            taper = 0.45 + 0.55 * np.sin(np.pi * (idx + 0.5) / n)
            return self._history * taper

        if state == "thinking":
            # A single bump travelling left to right on repeat.
            pos = (phase * 1.6) % 1.0 * (n - 1)
            return np.exp(-((idx - pos) ** 2) / 2.2) * 0.9

        if state == "working":
            # Two slower counter-travelling bumps — visibly distinct from
            # "thinking" so a long task doesn't look like a hung prompt.
            a = (phase * 0.7) % 1.0 * (n - 1)
            b = (n - 1) - a
            return np.clip(
                np.exp(-((idx - a) ** 2) / 3.0) + np.exp(-((idx - b) ** 2) / 3.0),
                0, 1,
            ) * 0.75

        # idle — a very shallow breath so it isn't visually dead
        self._history[:] = 0.0
        return np.full(n, 0.06, dtype=np.float32) * (
            0.7 + 0.3 * np.sin(phase * 1.2 + idx * 0.4)
        )


# =========================================================
# RIBBON — the iOS 27 Siri look
# =========================================================

class RibbonIndicator:
    """
    Layered translucent ribbons of spectrum light.

    The reference reads as a folded silk sheet seen edge-on: several
    bands sharing a broad path but with different frequencies and phase
    offsets, so they cross and overlap and produce brighter seams where
    they stack. Hue sweeps along x — cyan at the left, desaturating
    through white in the middle, into amber and deep red at the right.
    Additive accumulation plus a blur pass gives the bloom.

    Rendered in numpy because it's a per-pixel field, but the region is
    only ~130x44, so the whole thing is a few thousand pixels per frame.
    """

    name = "ribbon"

    # (frequency, amplitude scale, phase offset, speed, thickness scale)
    LAYERS = [
        (1.0, 1.00, 0.0, 1.00, 1.00),
        (1.6, 0.70, 2.1, 1.35, 0.72),
        (2.3, 0.45, 4.2, 0.80, 0.55),
    ]

    def __init__(self):
        self._grid_cache = {}

    def _grid(self, width, height):
        key = (width, height)
        if key not in self._grid_cache:
            ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
            self._grid_cache[key] = (xs, ys, xs / max(width - 1, 1))
        return self._grid_cache[key]

    def render(self, width, height, state, phase, level):
        xs, ys, u = self._grid(width, height)

        drive, speed, thickness = self._state_shape(state, level)

        cy = height / 2.0
        # Vertical headroom for the wave, held off the top/bottom edges
        # so the bloom pass below doesn't get clipped.
        amp_room = height * 0.20 * drive
        base_thick = height * 0.13 * thickness

        acc_r = np.zeros((height, width), dtype=np.float32)
        acc_g = np.zeros_like(acc_r)
        acc_b = np.zeros_like(acc_r)
        acc_a = np.zeros_like(acc_r)

        t = phase * speed

        for freq, amp_scale, offset, layer_speed, thick_scale in self.LAYERS:
            wave = np.sin(u * np.pi * 2.0 * freq + offset + t * layer_speed)
            centre = cy + wave * amp_room * amp_scale

            # Thickness breathes along the ribbon so it looks like a
            # sheet turning in space rather than a constant-width stroke.
            turn = 0.55 + 0.45 * np.sin(u * np.pi * 2.0 * freq * 0.7 + offset - t * 0.6)
            thick = np.maximum(base_thick * thick_scale * turn, 0.8)

            band = np.exp(-(((ys - centre) / thick) ** 2))

            r, g, b = self._spectrum(u)
            weight = band * amp_scale
            acc_r += r * weight
            acc_g += g * weight
            acc_b += b * weight
            acc_a += weight

        # Fade both ends so the ribbon tapers into nothing instead of
        # being cut off by the pill's inner edge.
        taper = np.clip(np.sin(np.pi * np.clip(u, 0.0, 1.0)) * 1.35, 0.0, 1.0)
        acc_a *= taper

        alpha = np.clip(acc_a, 0.0, 1.0)
        # Normalise colour by accumulated weight so overlapping layers
        # brighten toward white rather than simply saturating one channel.
        norm = np.maximum(acc_a, 1e-4)
        rgb = np.stack(
            [
                np.clip(acc_r / norm * (0.75 + 0.45 * alpha), 0, 1),
                np.clip(acc_g / norm * (0.75 + 0.45 * alpha), 0, 1),
                np.clip(acc_b / norm * (0.75 + 0.45 * alpha), 0, 1),
            ],
            axis=-1,
        )

        out = np.zeros((height, width, 4), dtype=np.uint8)
        out[:, :, :3] = (rgb * 255).astype(np.uint8)
        out[:, :, 3] = (alpha * 255).astype(np.uint8)
        img = Image.fromarray(out, "RGBA")

        # Bloom. Blurring the composite (not just the alpha) is what
        # makes the colours bleed into each other the way the reference
        # does, instead of looking like three separate coloured lines.
        glow = img.filter(ImageFilter.GaussianBlur(max(height * 0.055, 0.8)))
        return Image.alpha_composite(glow, img)

    @staticmethod
    def _spectrum(u):
        """
        Cyan -> white -> amber -> deep red across the ribbon, matching the
        reference's left-to-right sweep. Built as explicit RGB stops
        rather than an HSV hue ramp because the desaturated white section
        in the middle is the characteristic part, and a pure hue sweep
        can't produce it.
        """
        stops = np.array(
            [
                [0.15, 0.72, 1.00],   # cyan-blue
                [0.55, 0.90, 1.00],   # pale blue
                [1.00, 1.00, 0.98],   # white core
                [1.00, 0.78, 0.32],   # amber
                [1.00, 0.36, 0.16],   # orange-red
                [0.85, 0.13, 0.30],   # deep red-magenta
            ],
            dtype=np.float32,
        )
        n = len(stops) - 1
        pos = np.clip(u, 0.0, 1.0) * n
        i = np.clip(np.floor(pos).astype(np.int32), 0, n - 1)
        f = (pos - i)[..., None]
        c = stops[i] * (1.0 - f) + stops[i + 1] * f
        return c[..., 0], c[..., 1], c[..., 2]

    @staticmethod
    def _state_shape(state, level):
        """(wave drive, animation speed, thickness) per state."""
        lv = float(np.clip(level, 0.0, 1.0))
        if state == "listening":
            return 0.45 + 1.15 * lv, 2.2, 0.8 + 0.7 * lv
        if state == "speaking":
            return 0.5 + 1.0 * lv, 1.8, 0.85 + 0.6 * lv
        if state == "thinking":
            return 1.0, 3.4, 0.75
        if state == "working":
            return 0.8, 1.2, 1.0
        return 0.3, 0.7, 0.7  # idle — slow shallow drift


ALL_INDICATORS = (BarsIndicator, RibbonIndicator)


def random_indicator(rng=None):
    """
    Pick one style for this activation. Randomised per invocation on
    purpose: seeing both in ordinary use is the fastest way to find out
    which one actually holds up.
    """
    import random

    rng = rng or random
    return rng.choice(ALL_INDICATORS)()
