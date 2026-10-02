# Core/ui/overlay_settings.py
#
# Runtime-mutable overlay position/palette settings, persisted as JSON
# (unlike Core/config/settings.py, which is static Python config) so the
# tray menu can read/write it without touching source.

import json
import os

from config.settings import BASE_DIR
from ui.overlay_window import get_screen_size

SETTINGS_PATH = BASE_DIR / "config" / "overlay_settings.json"

DEFAULT_SETTINGS = {
    "preset": "bottom-right",
    "custom_position": None,  # {"x": int, "y": int}, only used when preset == "custom"
    "palette_hue": 192,
    # Matches orb_render.CANVAS — the actual render/window size is fixed
    # (UpdateLayeredWindow always resizes to match the bitmap), this is
    # only used for position-preset margin math against screen edges.
    "size": {"width": 260, "height": 260},
}


def load_overlay_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[overlay_settings] Failed to read {SETTINGS_PATH}: {e} — using defaults")
        return dict(DEFAULT_SETTINGS)

    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_overlay_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, SETTINGS_PATH)


def resolve_position(settings: dict, window_width: int, window_height: int):
    """
    Screen (x, y) top-left for the overlay window given its preset (or an
    explicit custom position), computed against the *current* screen
    size — so a preset survives a resolution/monitor change instead of
    baking in stale absolute coordinates.
    """
    preset = settings.get("preset", "bottom-right")

    if preset == "custom" and settings.get("custom_position"):
        pos = settings["custom_position"]
        return int(pos["x"]), int(pos["y"])

    screen_w, screen_h = get_screen_size()
    margin = 40

    presets = {
        "top-left": (margin, margin),
        "top-right": (screen_w - window_width - margin, margin),
        "bottom-left": (margin, screen_h - window_height - margin),
        "bottom-right": (
            screen_w - window_width - margin,
            screen_h - window_height - margin,
        ),
        "center": ((screen_w - window_width) // 2, (screen_h - window_height) // 2),
    }

    return presets.get(preset, presets["bottom-right"])
