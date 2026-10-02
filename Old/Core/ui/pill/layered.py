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

import ctypes
from ctypes import wintypes

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
    Re-assert z-order among *other* topmost windows on each show. The
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
