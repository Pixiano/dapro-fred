# Core/ui/tray.py
#
# System tray icon — the only way to reposition the overlay (never
# drag) and the standard way to quit it, since the overlay window
# itself is click-through everywhere except its two small hit zones.

import threading
import tkinter as tk
from typing import Callable

import pystray
from PIL import Image, ImageDraw

POSITION_PRESETS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"]


class TrayIcon:
    def __init__(
        self,
        get_settings: Callable[[], dict],
        apply_settings: Callable[[dict], None],
        on_quit: Callable[[], None],
    ):
        self._get_settings = get_settings
        self._apply_settings = apply_settings
        self._on_quit = on_quit
        self.icon = None
        self._thread = None

    # =========================================================
    # ICON IMAGE
    # =========================================================

    def _create_icon_image(self) -> Image.Image:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(60, 190, 230, 255))
        draw.text((24, 18), "F", fill=(8, 10, 14, 255))
        return img

    # =========================================================
    # MENU
    # =========================================================

    def _build_menu(self) -> pystray.Menu:
        position_items = [
            pystray.MenuItem(
                preset.replace("-", " ").title(),
                self._make_position_handler(preset),
            )
            for preset in POSITION_PRESETS
        ]
        return pystray.Menu(
            pystray.MenuItem("Position", pystray.Menu(*position_items)),
            pystray.MenuItem("Open Settings...", lambda icon, item: self._open_settings_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit FRED", lambda icon, item: self._on_quit()),
        )

    def _make_position_handler(self, preset: str):
        def _handler(icon, item):
            self._apply_settings({"preset": preset, "custom_position": None})

        return _handler

    def start(self):
        self.icon = pystray.Icon("FRED", self._create_icon_image(), "FRED", self._build_menu())
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self.icon:
            self.icon.stop()

    # =========================================================
    # SETTINGS WINDOW — small stdlib Tk window, deliberately not
    # another webview instance (rare/on-demand, no need for the
    # WebView2 overhead or the overlay's own Win32 styling here).
    # =========================================================

    def _open_settings_window(self):
        threading.Thread(target=self._run_settings_window, daemon=True).start()

    def _run_settings_window(self):
        current = self._get_settings()

        root = tk.Tk()
        root.title("FRED Overlay Settings")
        root.geometry("280x200")
        root.resizable(False, False)

        tk.Label(root, text="Palette hue (0-360)").pack(pady=(14, 2))
        hue_var = tk.IntVar(value=current.get("palette_hue", 192))
        tk.Scale(root, from_=0, to=360, orient="horizontal", variable=hue_var, length=220).pack()

        tk.Label(root, text="Custom position (leave blank to use presets)").pack(pady=(14, 2))

        frame = tk.Frame(root)
        frame.pack()
        tk.Label(frame, text="X:").grid(row=0, column=0, padx=4)
        x_var = tk.StringVar()
        tk.Entry(frame, textvariable=x_var, width=8).grid(row=0, column=1)
        tk.Label(frame, text="Y:").grid(row=0, column=2, padx=4)
        y_var = tk.StringVar()
        tk.Entry(frame, textvariable=y_var, width=8).grid(row=0, column=3)

        custom = current.get("custom_position")
        if custom:
            x_var.set(str(custom.get("x", "")))
            y_var.set(str(custom.get("y", "")))

        def on_save():
            update = {"palette_hue": hue_var.get()}
            x_text, y_text = x_var.get().strip(), y_var.get().strip()
            if x_text and y_text:
                try:
                    update["preset"] = "custom"
                    update["custom_position"] = {"x": int(x_text), "y": int(y_text)}
                except ValueError:
                    pass
            self._apply_settings(update)
            root.destroy()

        tk.Button(root, text="Save", command=on_save).pack(pady=16)

        root.mainloop()
