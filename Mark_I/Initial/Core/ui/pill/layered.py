# Core/ui/pill/layered.py
#
# Win32 layered-window primitives: blit an RGBA image onto a top-level
# window with genuine per-pixel alpha.
#
# UpdateLayeredWindow + a manually built ARGB DIB is the only way to get
# real desktop transparency here. The alternatives were tried and don't
# work: WebView2 can't do per-pixel alpha at all, and
# DwmExtendFrameIntoClientArea only fakes a frosted-glass tint on modern
# Windows. This is architecturally what a compositor-blended overlay
# (the NVIDIA Alt+R style) does, minus the GPU path — the one structural
# cost is that UpdateLayeredWindow requires a CPU-side bitmap, so pixels
# must round-trip through host memory.
#
# pywin32 wraps UpdateLayeredWindow but not CreateDIBSection or
# BITMAPINFO, hence the raw ctypes structures below.
#
# Screens. enable_dpi_awareness() (called once, first thing in engine/live.py
# main, before any window or screen capture exists) makes the process
# per-monitor-v2 DPI aware (fallbacks: shcore, then user32), so Windows never
# bitmap-scales the pill; every size is then scaled by the monitor's DPI
# (scale_for_dpi: 144 dpi = 1.5) and every position is in true pixels.
# current_monitor() picks the monitor each time the pill is shown: the one
# holding the foreground window, else the one under the cursor, else the
# primary (pick_monitor is the pure rule). Coordinates are signed: a monitor
# left of or above the primary has a negative origin.
#
# Over other windows: the pill is WS_EX_TOPMOST, shown without activating and
# re-asserted with SetWindowPos(HWND_TOPMOST, SWP_NOACTIVATE), so it sits above
# normal topmost windows and borderless-fullscreen apps. LIMIT: an
# exclusive-fullscreen DirectX/Vulkan game owns the display and no ordinary
# window can be drawn over it (hooks or injection are deliberately not used).
# Warnings do not depend on the pill: proactive/scamwatch.py also sends the
# Windows toast (ui/toast.py), the fallback in that case.
#
# Keyboard focus: force_foreground() asks politely, then falls back to
# AttachThreadInput and last to the ALT-key trick, because Windows refuses
# SetForegroundWindow to a process that has not just received input.

import collections
import ctypes
from ctypes import wintypes
from typing import NamedTuple

import numpy as np
from PIL import Image

import win32api
import win32con
import win32gui

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

# WM_NCHITTEST reply that makes a hit fall through to the window
# underneath — per-pixel click-through without a second window.
HTTRANSPARENT = -1
HTCLIENT = 1

WM_UI_CALL = win32con.WM_APP + 1


class BITMAPINFOHEADER(ctypes.Structure):
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


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def premultiplied_bgra(img: Image.Image) -> np.ndarray:
    """
    UpdateLayeredWindow with AC_SRC_ALPHA requires *premultiplied* BGRA.
    Skipping the premultiply produces bright fringing around every soft
    edge, which is exactly where a glowing pill shows it worst.
    """
    arr = np.asarray(img).astype(np.float32)
    alpha = arr[:, :, 3:4] / 255.0
    out = arr.copy()
    out[:, :, 0:3] = arr[:, :, 0:3] * alpha
    return np.ascontiguousarray(out[:, :, [2, 1, 0, 3]].astype(np.uint8))


def update_layered_window(hwnd, pil_image: Image.Image, x: int, y: int) -> bool:
    """Blit an RGBA PIL image to a WS_EX_LAYERED window at screen (x, y)."""
    width, height = pil_image.size
    bgra = premultiplied_bgra(pil_image)

    screen_hdc = user32.GetDC(0)
    mem_hdc = gdi32.CreateCompatibleDC(screen_hdc)
    if not mem_hdc:
        user32.ReleaseDC(0, screen_hdc)
        return False

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # negative => top-down rows
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        mem_hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0
    )
    if not hbmp:
        gdi32.DeleteDC(mem_hdc)
        user32.ReleaseDC(0, screen_hdc)
        return False

    ctypes.memmove(bits, bgra.tobytes(), bgra.nbytes)
    old = gdi32.SelectObject(mem_hdc, hbmp)

    dst = POINT(int(x), int(y))
    size = SIZE(width, height)
    src = POINT(0, 0)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    ok = user32.UpdateLayeredWindow(
        int(hwnd), screen_hdc,
        ctypes.byref(dst), ctypes.byref(size),
        mem_hdc, ctypes.byref(src),
        0, ctypes.byref(blend), ULW_ALPHA,
    )

    gdi32.SelectObject(mem_hdc, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem_hdc)
    user32.ReleaseDC(0, screen_hdc)
    return bool(ok)


class UiCalls:
    """Run a function on the thread that pumps a window's messages. Other threads must not call into the HWND (SetFocus fails from them, a window created on them has no pump), so they post here and the window's own wndproc (WM_UI_CALL -> run) drains the queue in order."""

    def __init__(self):
        self._q = collections.deque()

    def post(self, hwnd, fn):
        self._q.append(fn)
        try:
            win32gui.PostMessage(hwnd, WM_UI_CALL, 0, 0)
        except Exception:
            pass                                                     # the window is already gone: nothing to run it on

    def run(self, *_):
        while self._q:
            try:
                self._q.popleft()()
            except Exception as ex:
                print(f"[UiCalls] {type(ex).__name__}: {ex}")
        return 0


WM_DPICHANGED = 0x02E0


def enable_dpi_awareness():
    """Per-monitor-v2, else per-monitor (shcore), else system (user32). Call once, before any window or screen capture. Returns which one took ("" when none did, e.g. already set by the host)."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):               # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:                      # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except Exception:
        pass
    try:
        return "system" if user32.SetProcessDPIAware() else ""
    except Exception:
        return ""


def scale_for_dpi(dpi):
    """96 dpi = 1.0, 144 = 1.5, 192 = 2.0; never below 1 (a bad reading must not shrink the pill)."""
    return max(int(dpi or 96), 96) / 96.0


class Monitor(NamedTuple):
    rect: tuple                                  # (left, top, right, bottom), whole monitor, signed screen pixels
    work: tuple                                  # same, minus the taskbar
    dpi: int
    primary: bool


def pick_monitor(monitors, fg_rect=None, cursor=None):
    """The monitor with the largest overlap with the foreground window's rect; else the one holding the cursor; else the primary; else the first."""
    best, best_area = None, 0
    for m in monitors:
        if fg_rect:
            w = min(m.rect[2], fg_rect[2]) - max(m.rect[0], fg_rect[0])
            h = min(m.rect[3], fg_rect[3]) - max(m.rect[1], fg_rect[1])
            if w > 0 and h > 0 and w * h > best_area:
                best, best_area = m, w * h
    if best:
        return best
    for m in monitors:
        if cursor and m.rect[0] <= cursor[0] < m.rect[2] and m.rect[1] <= cursor[1] < m.rect[3]:
            return m
    return next((m for m in monitors if m.primary), monitors[0] if monitors else None)


def _monitor_dpi(hmon):
    try:
        x, y = ctypes.c_uint(), ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:      # MDT_EFFECTIVE_DPI
            return int(x.value)
    except Exception:
        pass
    return 96


def list_monitors():
    out = []
    try:
        for hmon, _hdc, _r in win32api.EnumDisplayMonitors(None, None):
            info = win32api.GetMonitorInfo(hmon)
            out.append(Monitor(tuple(info["Monitor"]), tuple(info["Work"]), _monitor_dpi(int(hmon)), bool(info["Flags"] & 1)))
    except Exception:
        pass
    return out


def current_monitor():
    """Where the pill goes right now. Never raises: on any failure it is the primary work area at 96 dpi."""
    try:
        mons = list_monitors()
        try:
            fg = win32gui.GetForegroundWindow()
            rect = win32gui.GetWindowRect(fg) if fg else None
        except Exception:
            rect = None
        if rect and rect[0] <= -30000:
            rect = None                                                              # a minimised window parks far off screen
        m = pick_monitor(mons, rect, win32gui.GetCursorPos())
        if m:
            return m
    except Exception:
        pass
    w, h = get_screen_size()
    return Monitor((0, 0, w, h), get_work_area(), 96, True)


def monitor_at(x, y):
    """The monitor holding screen point (x, y) (the primary if none does)."""
    return pick_monitor(list_monitors(), None, (x, y)) or current_monitor()


def force_foreground(hwnd):
    """Give hwnd the keyboard when Windows would refuse it. Returns True when it became the foreground window; failure is tolerated (the caller's window is still up and a click on it works)."""
    def got():
        return win32gui.GetForegroundWindow() == hwnd
    try:
        win32gui.SetForegroundWindow(hwnd)
        if got():
            return True
    except Exception:
        pass
    fg_tid, me = None, win32api.GetCurrentThreadId()
    try:
        fg = win32gui.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else None
        if fg_tid and fg_tid != me:
            user32.AttachThreadInput(me, fg_tid, True)                               # share input state with the foreground thread: the lock no longer applies
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(me, fg_tid, False)
            if got():
                return True
    except Exception:
        pass
    try:
        for up in (0, 2):                                                            # a synthetic ALT tap counts as input and lifts the lock (last resort: some apps flash their menu bar)
            win32api.keybd_event(win32con.VK_MENU, 0, up, 0)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return got()


def get_screen_size():
    return (
        win32api.GetSystemMetrics(win32con.SM_CXSCREEN),
        win32api.GetSystemMetrics(win32con.SM_CYSCREEN),
    )


def get_work_area():
    """
    Desktop area excluding the taskbar. Used so the pill sits above a
    visible taskbar but drops to the true screen edge when the taskbar
    is auto-hidden.
    """
    rect = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    w, h = get_screen_size()
    return 0, 0, w, h


def register_class(class_name, wnd_proc):
    hinst = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = hinst
    wc.lpszClassName = class_name
    wc.hbrBackground = 0
    try:
        return win32gui.RegisterClass(wc)
    except Exception:
        # Already registered (e.g. a relaunch in the same process) —
        # the name itself is a valid class reference.
        return class_name


def create_layered_popup(class_atom, title, x, y, w, h, click_through=False):
    """
    Borderless, always-on-top, taskbar-invisible layered window.

    WS_EX_TOOLWINDOW keeps it out of the taskbar and Alt+Tab;
    WS_EX_NOACTIVATE stops it stealing focus, which matters because the
    pill appears over whatever the user is actually working in.

    WS_EX_TOPMOST is set here, at creation, and not via
    SetWindowPos(HWND_TOPMOST) afterwards. Measured on this window type
    (layered + tool + noactivate), that SetWindowPos call returns success
    and yet never sets the style bit, so the window ends up merely normal
    z-order — visible in testing, but silently sinking behind whatever
    the user clicks next. Passing the flag to CreateWindowEx does stick.
    """
    ex_style = (
        win32con.WS_EX_LAYERED
        | win32con.WS_EX_TOOLWINDOW
        | win32con.WS_EX_NOACTIVATE
        | win32con.WS_EX_TOPMOST
    )
    if click_through:
        ex_style |= win32con.WS_EX_TRANSPARENT

    hinst = win32api.GetModuleHandle(None)
    return win32gui.CreateWindowEx(
        ex_style, class_atom, title,
        win32con.WS_POPUP, x, y, w, h,
        0, 0, hinst, None,
    )


def raise_to_top(hwnd):
    """
    Re-assert z-order among *other* topmost windows on each show and
    every config.pill_topmost_every_s while visible (never activates). The
    WS_EX_TOPMOST style (set at creation) is what keeps this above normal
    windows; this only decides ordering against other always-on-top
    windows, so a failure here is cosmetic rather than structural.
    """
    try:
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    except Exception:
        pass
