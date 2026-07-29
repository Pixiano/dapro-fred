# Core/ui/overlay_window.py
#
# Native Win32 layered-window overlay — no browser engine. pywebview's
# WebView2 backend could not achieve real per-pixel desktop transparency
# (DwmExtendFrameIntoClientArea only fakes a frosted-glass tint on modern
# Windows); UpdateLayeredWindow with a manually-rendered ARGB bitmap does
# — this is architecturally closer to how the NVIDIA overlay actually
# works, and gives a genuinely invisible background with just the orb
# floating on the desktop.
#
# Three independent top-level windows, each with one job:
#   - OrbWindow:   pure visual, always click-through, real per-pixel
#                  alpha via UpdateLayeredWindow. Never receives input.
#   - IconWindow:  small floating layered icon (also UpdateLayeredWindow,
#                  but WITHOUT WS_EX_TRANSPARENT, so it's clickable).
#   - InputWindow: small popup hosting a native EDIT control (needs real
#                  WM_PAINT/child-control support, which UpdateLayeredWindow
#                  precludes — uses SetLayeredWindowAttributes uniform
#                  alpha instead, a plain translucent pill).
# This avoids all the click-through-polling/hit-zone complexity the old
# single-window pywebview design needed — each window naturally does or
# doesn't receive input by construction.

import ctypes
from ctypes import wintypes
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

import win32api
import win32con
import win32gui

from ui.orb_render import OrbRenderer, CANVAS

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

ORB_CLASS = "FredOrbWindow"
ICON_CLASS = "FredIconWindow"
INPUT_CLASS = "FredInputWindow"

ICON_SIZE = 32
INPUT_W, INPUT_H = 160, 30
GAP = 8

TICK_MS = {"idle": 60, "listening": 33, "thinking": 45, "speaking": 33}


# =========================================================
# LAYERED-BITMAP BLIT (raw ctypes — pywin32 doesn't wrap
# CreateDIBSection/UpdateLayeredWindow)
# =========================================================

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def _premultiplied_bgra(img: Image.Image) -> np.ndarray:
    arr = np.array(img).astype(np.float32)
    alpha = arr[:, :, 3:4] / 255.0
    premult = arr.copy()
    premult[:, :, 0:3] = arr[:, :, 0:3] * alpha
    bgra = premult[:, :, [2, 1, 0, 3]].astype(np.uint8)
    return np.ascontiguousarray(bgra)


def update_layered_window(hwnd, pil_image: Image.Image, x: int, y: int) -> bool:
    """Blits an RGBA PIL image onto a WS_EX_LAYERED window with real
    per-pixel alpha — the actual mechanism behind the transparent orb."""
    width, height = pil_image.size
    bgra = _premultiplied_bgra(pil_image)

    screen_hdc = user32.GetDC(0)
    mem_hdc = gdi32.CreateCompatibleDC(screen_hdc)

    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    ppv_bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(mem_hdc, ctypes.byref(bmi), 0, ctypes.byref(ppv_bits), None, 0)
    if not hbmp:
        gdi32.DeleteDC(mem_hdc)
        user32.ReleaseDC(0, screen_hdc)
        return False

    ctypes.memmove(ppv_bits, bgra.tobytes(), bgra.nbytes)
    old_bmp = gdi32.SelectObject(mem_hdc, hbmp)

    ppt_dst = _POINT(int(x), int(y))
    size = _SIZE(width, height)
    ppt_src = _POINT(0, 0)
    blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    ok = user32.UpdateLayeredWindow(
        int(hwnd), screen_hdc,
        ctypes.byref(ppt_dst), ctypes.byref(size),
        mem_hdc, ctypes.byref(ppt_src),
        0, ctypes.byref(blend), ULW_ALPHA,
    )

    gdi32.SelectObject(mem_hdc, old_bmp)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem_hdc)
    user32.ReleaseDC(0, screen_hdc)
    return bool(ok)


def get_screen_size():
    return (
        win32api.GetSystemMetrics(win32con.SM_CXSCREEN),
        win32api.GetSystemMetrics(win32con.SM_CYSCREEN),
    )


def _register_class(class_name, wnd_proc_dict):
    hinst = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wnd_proc_dict
    wc.hInstance = hinst
    wc.lpszClassName = class_name
    wc.hbrBackground = 0
    try:
        return win32gui.RegisterClass(wc)
    except Exception:
        return class_name


def _make_topmost_popup(class_atom, title, ex_style, x, y, w, h):
    hinst = win32api.GetModuleHandle(None)
    hwnd = win32gui.CreateWindowEx(
        ex_style, class_atom, title,
        win32con.WS_POPUP, x, y, w, h,
        0, 0, hinst, None,
    )
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
    )
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    return hwnd


# =========================================================
# ICON WINDOW — small floating layered "expand" glyph
# =========================================================

def _render_icon(hover=False):
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg_a = 190 if hover else 140
    draw.ellipse([1, 1, ICON_SIZE - 1, ICON_SIZE - 1], fill=(18, 24, 32, bg_a))
    c = (130, 225, 255, 255) if hover else (200, 215, 225, 220)
    m = 10
    ln = 5
    draw.line([(m, m + ln), (m, m), (m + ln, m)], fill=c, width=2)
    draw.line([(ICON_SIZE - m, m + ln), (ICON_SIZE - m, m), (ICON_SIZE - m - ln, m)], fill=c, width=2)
    draw.line([(m, ICON_SIZE - m - ln), (m, ICON_SIZE - m), (m + ln, ICON_SIZE - m)], fill=c, width=2)
    draw.line([(ICON_SIZE - m, ICON_SIZE - m - ln), (ICON_SIZE - m, ICON_SIZE - m), (ICON_SIZE - m - ln, ICON_SIZE - m)], fill=c, width=2)
    return img


class IconWindow:
    def __init__(self, x, y, on_click=None):
        self.x, self.y = x, y
        self.on_click = on_click
        self.hwnd = None
        self._class_atom = _register_class(ICON_CLASS, {win32con.WM_LBUTTONUP: self._on_click_msg})

    def _on_click_msg(self, hwnd, msg, wparam, lparam):
        if self.on_click:
            self.on_click()
        return 0

    def create(self):
        ex_style = win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
        self.hwnd = _make_topmost_popup(self._class_atom, "FRED_ICON", ex_style, self.x, self.y, ICON_SIZE, ICON_SIZE)
        update_layered_window(self.hwnd, _render_icon(), self.x, self.y)

    def move(self, x, y):
        self.x, self.y = x, y
        win32gui.SetWindowPos(
            self.hwnd, None, x, y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )
        update_layered_window(self.hwnd, _render_icon(), self.x, self.y)

    def destroy(self):
        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass


# =========================================================
# INPUT WINDOW — small translucent pill hosting a native EDIT control
# =========================================================

class InputWindow:
    def __init__(self, x, y, on_submit=None):
        self.x, self.y = x, y
        self.on_submit = on_submit
        self.hwnd = None
        self.edit_hwnd = None
        self._edit_bg_brush = None
        self._orig_edit_proc = None
        self._class_atom = _register_class(
            INPUT_CLASS,
            {win32con.WM_CTLCOLOREDIT: self._on_ctlcolor},
        )

    def _on_ctlcolor(self, hwnd, msg, wparam, lparam):
        hdc = wparam
        win32gui.SetTextColor(hdc, win32api.RGB(224, 232, 240))
        win32gui.SetBkColor(hdc, win32api.RGB(16, 20, 26))
        if self._edit_bg_brush is None:
            self._edit_bg_brush = win32gui.CreateSolidBrush(win32api.RGB(16, 20, 26))
        return self._edit_bg_brush

    def _edit_subclass_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_CHAR and wparam == 13:  # Enter
            text = win32gui.GetWindowText(hwnd).strip()
            win32gui.SetWindowText(hwnd, "")
            if text and self.on_submit:
                self.on_submit(text)
            return 0
        return win32gui.CallWindowProc(self._orig_edit_proc, hwnd, msg, wparam, lparam)

    def create(self):
        ex_style = win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
        self.hwnd = _make_topmost_popup(self._class_atom, "FRED_INPUT", ex_style, self.x, self.y, INPUT_W, INPUT_H)
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 235, win32con.LWA_ALPHA)

        hinst = win32api.GetModuleHandle(None)
        self.edit_hwnd = win32gui.CreateWindowEx(
            0, "EDIT", "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.ES_AUTOHSCROLL,
            4, 4, INPUT_W - 8, INPUT_H - 8,
            self.hwnd, 0, hinst, None,
        )
        self._orig_edit_proc = win32gui.SetWindowLong(
            self.edit_hwnd, win32con.GWL_WNDPROC, self._edit_subclass_proc
        )

    def move(self, x, y):
        self.x, self.y = x, y
        win32gui.SetWindowPos(
            self.hwnd, None, x, y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )

    def destroy(self):
        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass


# =========================================================
# ORB WINDOW — pure visual, always click-through
# =========================================================

class OrbWindow:
    def __init__(self, x, y, size=CANVAS):
        self.x, self.y = x, y
        self.size = size
        self.hwnd = None
        self._class_atom = _register_class(ORB_CLASS, {})

    def create(self):
        ex_style = (
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        )
        self.hwnd = _make_topmost_popup(self._class_atom, "FRED_ORB", ex_style, self.x, self.y, self.size, self.size)

    def blit(self, pil_image):
        update_layered_window(self.hwnd, pil_image, self.x, self.y)

    def move(self, x, y):
        self.x, self.y = x, y
        win32gui.SetWindowPos(
            self.hwnd, None, x, y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )

    def destroy(self):
        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass


# =========================================================
# TOP-LEVEL CONTROLLER
# =========================================================

class OverlayWindow:
    """
    Owns the three native windows + the render loop. External interface
    mirrors what overlay_app.py already expects: create(), run(on_ready),
    move_to(x, y), resize(...), destroy(), plus state/transcript/audio
    setters used by OverlayBridge.
    """

    def __init__(self, bridge, width=CANVAS, height=CANVAS, x=100, y=100):
        # UpdateLayeredWindow always resizes the window to match the
        # bitmap it's given, and the render canvas is a fixed constant
        # (orb_render.CANVAS) — so the actual window/canvas size is not
        # independently configurable via width/height. Accepted anyway
        # for interface compatibility with overlay_app.py's settings-
        # driven construction; only x/y (position) actually varies.
        self.bridge = bridge
        self.width = CANVAS
        self.height = CANVAS
        self.x = x
        self.y = y

        self.renderer = OrbRenderer(base_radius=int(CANVAS * 0.185))
        self.state = "idle"
        self.mic_level = 0.0
        self.speak_pulse = 0.0
        self._running = False
        self._fullscreen = False

        self.orb = OrbWindow(x, y, size=CANVAS)
        self.icon = None
        self.input = None

    # ---- lifecycle ----

    def create(self):
        self.orb.create()
        self.icon = IconWindow(*self._icon_pos(), on_click=self._handle_icon_click)
        self.icon.create()
        self.input = InputWindow(*self._input_pos(), on_submit=self._handle_submit)
        self.input.create()
        self.bridge.bind_window(self)

    def run(self, on_ready=None):
        self._running = True
        threading.Thread(target=self._render_loop, daemon=True).start()
        if on_ready:
            on_ready()
        win32gui.PumpMessages()

    def destroy(self):
        self._running = False
        for w in (self.orb, self.icon, self.input):
            if w:
                w.destroy()

    # ---- layout ----

    def _icon_pos(self):
        return (self.x + self.width - ICON_SIZE, self.y + self.height + GAP)

    def _input_pos(self):
        return (self.x, self.y + self.height + GAP)

    def move_to(self, x: int, y: int):
        self.x, self.y = x, y
        self.orb.move(x, y)
        if self.icon:
            self.icon.move(*self._icon_pos())
        if self.input:
            self.input.move(*self._input_pos())

    def toggle_fullscreen(self):
        # UpdateLayeredWindow always resizes the window to match the
        # blitted bitmap, so a variable window size fights itself —
        # the canvas/window stays a fixed CANVASxCANVAS; "expand" is
        # purely a bigger blob radius within it (see OrbRenderer.set_expand).
        self._fullscreen = not self._fullscreen
        self.renderer.set_expand(self._fullscreen)

    # ---- render loop ----

    def _render_loop(self):
        while self._running:
            frame = self.renderer.get_frame(self.state, self.mic_level, self.speak_pulse)
            try:
                self.orb.blit(frame)
            except Exception as e:
                print(f"[OverlayWindow] blit failed: {e}")
            time.sleep(TICK_MS.get(self.state, 50) / 1000.0)

    # ---- state pushed from OverlayBridge ----

    def set_state(self, state):
        self.state = state

    def set_transcript(self, user_text="", reply_text=""):
        # No transcript surface in the native-window design yet — the
        # small chip layout has no room for it without crowding the
        # click-through-free orb. Left as a no-op hook for now.
        pass

    def set_audio_level(self, level):
        self.mic_level = max(0.0, min(1.0, level))

    def set_speaking_envelope(self, value):
        self.speak_pulse = max(0.0, min(1.0, value))

    def set_palette_hue(self, degrees: int):
        # overlay_settings.json stores palette_hue in degrees (0-360,
        # default 192 to match the renderer's own idle-state base hue of
        # 0.53 * 360 ≈ 192) — convert to the 0..1 offset OrbRenderer wants.
        offset = ((degrees - 192) % 360) / 360.0
        self.renderer.set_hue_offset(offset)

    # ---- callbacks from native windows ----

    def _handle_icon_click(self):
        self.toggle_fullscreen()
        if self.bridge.on_toggle_fullscreen_cb:
            self.bridge.on_toggle_fullscreen_cb()

    def _handle_submit(self, text):
        if self.bridge.on_submit:
            self.bridge.on_submit(text)
