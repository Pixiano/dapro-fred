# Core/ui/pill/input_popup.py
#
# Small always-on-top popup holding a real native EDIT control, spawned
# by clicking the pill's type button. Not layered/PIL-drawn like the
# pill itself — a bitmap can't take keyboard input, so this is a plain
# Win32 child-control window instead, using the same win32gui/win32con
# calls layered.py already depends on.
#
# Threading: the window is created by prepare() on the thread that pumps
# messages (the pill's UI thread). show()/destroy() may be called from any
# thread; off that thread they are posted to it (layered.UiCalls), because
# a window made on, or focused from, a thread with no pump never works.
#
# Screen: each show() finds the monitor holding the point it was given (the
# pill's), scales the box, its margins and its font by that monitor's DPI
# (popup_geometry) and clamps the box inside that monitor's work area, signed
# coordinates included. Focus: the box takes the keyboard when it appears, and
# layered.force_foreground gets it even when Windows would refuse; if every
# trick fails the box is still up and a click on it types.

import sys
import threading
from pathlib import Path

import win32api
import win32con
import win32gui

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ui.pill.layered import WM_UI_CALL, UiCalls, force_foreground, monitor_at, raise_to_top, register_class, scale_for_dpi

POPUP_CLASS = "FredTypePopup"
POPUP_W, POPUP_H = 320, 74
TALL_H = 170                                   # question mode (the ask queue): room for a wrapped question above the box
_MARGIN = 8
HINT = "Type to FRED. Enter sends, Esc closes."


def popup_geometry(scale, tall=False):
    """Everything the box needs at a DPI scale, in px: (w, h, label rect, edit rect, font height). 1.0 = the 1x design above."""
    k = lambda v: round(v * scale)
    w, h, m = k(POPUP_W), k(TALL_H if tall else POPUP_H), k(_MARGIN)
    return w, h, (m, k(6), w - m * 2, h - k(44) if tall else k(30)), (m, h - k(34), w - m * 2, k(24)), k(14)


def clamp_box(x, y, w, h, area):
    """Move a w x h box so it lies inside area = (left, top, right, bottom): a moved taskbar, another resolution or a tall box must not push it off screen."""
    left, top, right, bottom = area
    return min(max(x, left), max(left, right - w)), min(max(y, top), max(top, bottom - h))


def clip(text, n):
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


class TypeInputPopup:
    """One text-entry popup: a label (hint, or the ask-queue question) above an EDIT box.
    Created once, then shown/hidden — CreateWindowEx
    inside a hot click handler would be wasted work on every reopen."""

    def __init__(self, on_submit):
        self.on_submit = on_submit
        self.hwnd = None
        self.edit_hwnd = None
        self.label_hwnd = None
        self._font = None
        self._font_h = 0                       # height the current font was made for
        self._tid = None                       # the thread that owns the window
        self._ui = UiCalls()
        self._orig_edit_proc = None
        self._once = None                      # one-shot submit handler set by show(on_submit=...)
        self._class_atom = register_class(
            POPUP_CLASS,
            {
                win32con.WM_ACTIVATE: self._on_activate,
                win32con.WM_DESTROY: self._on_destroy,
                WM_UI_CALL: self._ui.run,
            },
        )

    def prepare(self):
        """Create the window (hidden). Call it on the pumping thread."""
        if not self.hwnd:
            self._create()

    def _create(self):
        self._tid = threading.get_ident()
        hinst = win32api.GetModuleHandle(None)
        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW,
            self._class_atom, "FRED",
            win32con.WS_POPUP | win32con.WS_BORDER,
            0, 0, POPUP_W, POPUP_H,
            0, 0, hinst, None,
        )
        self.label_hwnd = win32gui.CreateWindowEx(
            0, "STATIC", "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.SS_LEFT,
            _MARGIN, 6, POPUP_W - _MARGIN * 2, 30,
            self.hwnd, 0, hinst, None,
        )
        self.edit_hwnd = win32gui.CreateWindowEx(
            0, "EDIT", "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER
            | win32con.ES_AUTOHSCROLL,
            _MARGIN, 40, POPUP_W - _MARGIN * 2, 24,
            self.hwnd, 0, hinst, None,
        )
        # Controls default to the old bitmap system font; use the UI font.
        self._set_font(round(14))
        # Subclass the EDIT control so Enter/Escape are ours to catch —
        # a plain (non-dialog) single-line EDIT control neither submits
        # on Enter nor notifies its parent of Escape by default.
        self._orig_edit_proc = win32gui.SetWindowLong(
            self.edit_hwnd, win32con.GWL_WNDPROC, self._edit_proc
        )

    def _set_font(self, px):
        if self._font and px == self._font_h:
            return
        lf = win32gui.LOGFONT()
        lf.lfFaceName, lf.lfHeight = "Segoe UI", -px
        old, self._font, self._font_h = self._font, win32gui.CreateFontIndirect(lf), px
        for h in (self.label_hwnd, self.edit_hwnd):
            win32gui.SendMessage(h, win32con.WM_SETFONT, self._font, 1)
        old and win32gui.DeleteObject(old)

    def _edit_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_CHAR and wparam in (win32con.VK_RETURN, win32con.VK_ESCAPE):
            return 0                           # the keydown below handled it; the char message would only beep
        if msg == win32con.WM_KEYDOWN:
            if wparam == win32con.VK_RETURN:
                text = win32gui.GetWindowText(self.edit_hwnd).strip()
                self.hide()
                if text:
                    (self._once or self.on_submit)(text)
                return 0
            if wparam == win32con.VK_ESCAPE:
                self.hide()
                return 0
            if wparam == ord("A") and win32api.GetKeyState(win32con.VK_CONTROL) < 0:
                win32gui.SendMessage(self.edit_hwnd, win32con.EM_SETSEL, 0, -1)       # a bare EDIT has no Ctrl+A
                return 0
        return win32gui.CallWindowProc(self._orig_edit_proc, hwnd, msg, wparam, lparam)

    def _on_activate(self, hwnd, msg, wparam, lparam):
        # Clicking anywhere outside the popup deactivates it — that's
        # "click outside the box" without a global mouse hook.
        if (wparam & 0xFFFF) == win32con.WA_INACTIVE:
            self.hide()
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self.hwnd = None
        self.edit_hwnd = None
        self.label_hwnd = None
        if self._font:
            win32gui.DeleteObject(self._font)
            self._font = None
        return 0

    def show(self, *args, **kw):
        """Any thread. x, y (top-left of the one-line box, on the pill's monitor), reply_text="", tall=False, on_submit=None, activate=True.
        reply_text: the label (default: a one-line hint); tall: a question with a few lines of options (ask queue);
        on_submit: handles this one entry instead of the default; activate=False: appear without taking the keyboard
        (no caller uses it now: the ask queue takes focus too)."""
        if self.hwnd and threading.get_ident() != self._tid:
            self._ui.post(self.hwnd, lambda: self._show_now(*args, **kw))
        else:
            self._show_now(*args, **kw)

    def _show_now(self, x, y, reply_text="", tall=False, on_submit=None, activate=True):
        if not self.hwnd:
            self._create()
        self._once = on_submit
        mon = monitor_at(int(x) + 4, int(y) + 4)                                   # the pill's monitor: (x, y) is the box's top-left just above the pill
        scale = scale_for_dpi(mon.dpi)
        w, h, label, edit, font_px = popup_geometry(scale, tall)
        base_h = popup_geometry(scale)[1]
        self._set_font(font_px)
        win32gui.SetWindowText(self.label_hwnd, clip(reply_text, 240 if tall else 80) or HINT)
        win32gui.SetWindowText(self.edit_hwnd, "")
        win32gui.MoveWindow(self.label_hwnd, *label, True)
        win32gui.MoveWindow(self.edit_hwnd, *edit, True)
        bx, by = clamp_box(int(x), int(y) - (h - base_h), w, h, mon.work)
        win32gui.MoveWindow(self.hwnd, bx, by, w, h, True)
        if not activate:
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
            raise_to_top(self.hwnd)
            return
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        raise_to_top(self.hwnd)
        force_foreground(self.hwnd)                                                # tolerates refusal: the box is up, one click on it types
        try:
            win32gui.SetFocus(self.edit_hwnd)
        except Exception:
            pass

    def hide(self):
        if self.hwnd:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def destroy(self):
        """Any thread (a window dies only on its own thread, so off-thread this posts)."""
        if self.hwnd and threading.get_ident() != self._tid:
            self._ui.post(self.hwnd, self.destroy)
        elif self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None


def _selfcheck():
    import time
    assert clamp_box(-50, 1000, 320, 74, (0, 0, 1920, 1040)) == (0, 966)                  # off the left and below the work area
    assert clamp_box(1800, -20, 320, 74, (0, 0, 1920, 1040)) == (1600, 0)
    assert clamp_box(10, 10, 320, 74, (0, 0, 200, 50)) == (0, 0)                         # a work area smaller than the box
    assert clamp_box(-1900, 100, 320, 74, (-1920, 0, 0, 1040)) == (-1900, 100)           # a monitor left of the primary: negative coordinates stay put
    assert clamp_box(-100, 100, 320, 74, (-1920, 0, 0, 1040)) == (-320, 100)             # ... and are clamped against its own right edge, not 0
    assert clamp_box(500, -2000, 320, 74, (0, -1080, 1920, -40)) == (500, -1080)         # a monitor above the primary
    assert popup_geometry(1.0) == (320, 74, (8, 6, 304, 30), (8, 40, 304, 24), 14)         # 100%: the original layout
    assert popup_geometry(1.0, tall=True)[:2] == (320, 170) and popup_geometry(1.0, tall=True)[3] == (8, 136, 304, 24)
    w2, h2, lab2, ed2, f2 = popup_geometry(2.0)
    assert (w2, h2, f2) == (640, 148, 28) and lab2 == (16, 12, 608, 60) and ed2 == (16, 80, 608, 48)
    assert popup_geometry(1.5)[0] == 480 and popup_geometry(1.25)[4] == round(17.5)
    assert clip("short", 80) == "short" and len(clip("x " * 200, 80)) == 80 and clip("x " * 200, 80).endswith("…")
    # show() from another thread runs on the pumping thread (the window is never shown here)
    ran, ready, stop = [], threading.Event(), threading.Event()
    pop = TypeInputPopup(on_submit=lambda t: None)

    def pump():
        pop.prepare()
        pop._show_now = lambda *a, **k: ran.append((threading.get_ident(), a, k))
        ready.set()
        while not stop.is_set():
            win32gui.PumpWaitingMessages()
            time.sleep(0.01)
        pop.destroy()
        win32gui.PumpWaitingMessages()
    t = threading.Thread(target=pump)
    t.start()
    ready.wait(3)
    pop.show(1, 2, "q", tall=True, activate=False)
    t0 = time.time()
    while not ran and time.time() - t0 < 3:
        time.sleep(0.02)
    assert ran and ran[0][0] == t.ident and ran[0][2] == {"tall": True, "activate": False}, ran
    pop.destroy()                                                                        # posted too
    t0 = time.time()
    while pop.hwnd and time.time() - t0 < 3:
        time.sleep(0.02)
    assert pop.hwnd is None
    stop.set()
    t.join(3)
    print("input popup ok")


if __name__ == "__main__":
    _selfcheck()
