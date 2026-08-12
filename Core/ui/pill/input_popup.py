# Core/ui/pill/input_popup.py
#
# Small always-on-top popup holding a real native EDIT control, spawned
# by clicking the pill's type button. Not layered/PIL-drawn like the
# pill itself — a bitmap can't take keyboard input, so this is a plain
# Win32 child-control window instead, using the same win32gui/win32con
# calls layered.py already depends on.

import win32api
import win32con
import win32gui

from ui.pill.layered import register_class

POPUP_CLASS = "FredTypePopup"
POPUP_W, POPUP_H = 320, 74
_MARGIN = 8


class TypeInputPopup:
    """One text-entry popup: a label (last exchange) above an EDIT box.
    Created once, then shown/hidden — same lazy-create-on-first-show
    pattern as nothing else here needs, kept because CreateWindowEx
    inside a hot click handler would be wasted work on every reopen."""

    def __init__(self, on_submit):
        self.on_submit = on_submit
        self.hwnd = None
        self.edit_hwnd = None
        self.label_hwnd = None
        self._orig_edit_proc = None
        self._class_atom = register_class(
            POPUP_CLASS,
            {
                win32con.WM_ACTIVATE: self._on_activate,
                win32con.WM_DESTROY: self._on_destroy,
            },
        )

    def _create(self):
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
        # Subclass the EDIT control so Enter/Escape are ours to catch —
        # a plain (non-dialog) single-line EDIT control neither submits
        # on Enter nor notifies its parent of Escape by default.
        self._orig_edit_proc = win32gui.SetWindowLong(
            self.edit_hwnd, win32con.GWL_WNDPROC, self._edit_proc
        )

    def _edit_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_KEYDOWN:
            if wparam == win32con.VK_RETURN:
                text = win32gui.GetWindowText(self.edit_hwnd).strip()
                self.hide()
                if text:
                    self.on_submit(text)
                return 0
            if wparam == win32con.VK_ESCAPE:
                self.hide()
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
        return 0

    def show(self, x, y, reply_text=""):
        if not self.hwnd:
            self._create()
        win32gui.SetWindowText(self.label_hwnd, reply_text[:120])
        win32gui.SetWindowText(self.edit_hwnd, "")
        win32gui.MoveWindow(self.hwnd, int(x), int(y), POPUP_W, POPUP_H, True)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.SetFocus(self.edit_hwnd)

    def hide(self):
        if self.hwnd:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def destroy(self):
        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None
