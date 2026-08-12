# Core/ui/pill/window.py
#
# The popup's single native window plus its render loop.
#
# One window, not three. The atticked orb overlay needed separate windows
# because a click-through window (WS_EX_TRANSPARENT) can never receive
# input, so anything clickable had to live in its own HWND. WM_NCHITTEST
# removes that constraint: the window handles hit testing itself and
# answers HTTRANSPARENT for every pixel outside the two buttons, so a
# click on the capsule body or the transcript falls straight through to
# whatever is underneath while the buttons stay live.

import threading
import time

import win32con
import win32gui

from ui.pill.layered import (
    HTCLIENT,
    HTTRANSPARENT,
    create_layered_popup,
    get_work_area,
    raise_to_top,
    register_class,
    update_layered_window,
)
from ui.pill import render as R

PILL_CLASS = "FredPillWindow"

# Frame intervals per state. listening/speaking track live audio so they
# need to be smooth; the rest can idle slower. Nothing renders at all
# while hidden, which is the real reason this is cheap at rest.
TICK_MS = {
    "idle": 80,
    "listening": 33,
    "thinking": 40,
    "speaking": 33,
    "working": 50,
}


def _signed_lo_hi(lparam):
    """Split an lParam into signed x, y — screen coords can be negative
    on a multi-monitor setup, and the unsigned read silently breaks there."""
    x = lparam & 0xFFFF
    y = (lparam >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    return x, y


class PillWindow:
    """
    Bottom-centre popup. Hidden until show() — the pill is transient by
    design, appearing on the hotkey and going away once FRED has finished
    speaking, so there's no persistent element sitting on the desktop.
    """

    def __init__(self, indicator, on_cancel=None, on_accept=None, on_type=None, margin=18):
        self.indicator = indicator
        self.on_cancel = on_cancel
        self.on_accept = on_accept
        self.on_type = on_type
        self.margin = margin

        self.hwnd = None
        self.state = "idle"
        self.level = 0.0
        self.visible = False

        self._transcript = ""
        self._transcript_until = 0.0
        self._phase = 0.0
        self._last_frame = time.time()
        self._running = False
        self._lock = threading.Lock()

        self.x, self.y = 0, 0
        self._class_atom = register_class(
            PILL_CLASS,
            {
                win32con.WM_NCHITTEST: self._on_nchittest,
                win32con.WM_LBUTTONUP: self._on_lbuttonup,
                win32con.WM_DESTROY: self._on_destroy,
            },
        )

    # =========================================================
    # WINDOW MESSAGES
    # =========================================================

    def _on_nchittest(self, hwnd, msg, wparam, lparam):
        sx, sy = _signed_lo_hi(lparam)
        cx, cy = sx - self.x, sy - self.y  # -> canvas coords
        return HTCLIENT if self._hit_button(cx, cy) else HTTRANSPARENT

    def _hit_button(self, cx, cy):
        (lx, ly), (rx, ry), (tx, ty) = R.button_centres()
        r = R.BTN_D / 2.0
        for bx, by, which in ((lx, ly, "cancel"), (rx, ry, "accept"), (tx, ty, "type")):
            if (cx - bx) ** 2 + (cy - by) ** 2 <= r * r:
                return which
        return None

    def _on_lbuttonup(self, hwnd, msg, wparam, lparam):
        cx, cy = _signed_lo_hi(lparam)  # client coords == canvas coords here
        which = self._hit_button(cx, cy)
        if which == "cancel" and self.on_cancel:
            self.on_cancel()
        elif which == "accept" and self.on_accept:
            self.on_accept()
        elif which == "type" and self.on_type:
            self.on_type()
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self._running = False
        win32gui.PostQuitMessage(0)
        return 0

    # =========================================================
    # LIFECYCLE
    # =========================================================

    def create(self):
        left, _top, right, bottom = get_work_area()
        self.x, self.y = R.canvas_origin_for_bottom_centre(
            right, bottom, work_left=left, margin=self.margin
        )
        self.hwnd = create_layered_popup(
            self._class_atom, "FRED_PILL",
            self.x, self.y, R.CANVAS_W, R.CANVAS_H,
            click_through=False,  # we hit-test ourselves via WM_NCHITTEST
        )
        # Draw one frame before it is ever shown, so it can't flash as an
        # uninitialised black rectangle on first appearance.
        self._blit()

    def run(self, on_ready=None):
        """Blocks: pumps this thread's message queue. The hotkey hook is
        installed by the caller on this same thread, since low-level hooks
        are delivered through the installing thread's queue."""
        self._running = True
        threading.Thread(target=self._render_loop, daemon=True).start()
        if on_ready:
            on_ready()
        win32gui.PumpMessages()

    def destroy(self):
        self._running = False
        if self.hwnd:
            try:
                win32gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None

    # =========================================================
    # VISIBILITY
    # =========================================================

    def show(self):
        if self.visible or not self.hwnd:
            return
        self.visible = True
        self._last_frame = time.time()
        self._blit()
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        raise_to_top(self.hwnd)

    def hide(self):
        if not self.visible or not self.hwnd:
            return
        self.visible = False
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def reposition(self):
        """Recompute placement against the current work area — picks up a
        resolution change or the taskbar being shown/hidden."""
        left, _top, right, bottom = get_work_area()
        self.x, self.y = R.canvas_origin_for_bottom_centre(
            right, bottom, work_left=left, margin=self.margin
        )
        self._blit()

    # =========================================================
    # STATE PUSHED FROM THE CONTROLLER
    # =========================================================

    def set_state(self, state):
        self.state = state

    def set_level(self, level):
        self.level = max(0.0, min(1.0, float(level)))

    def set_transcript(self, text, ttl=2.5):
        with self._lock:
            self._transcript = text or ""
            self._transcript_until = time.time() + ttl if text else 0.0

    def clear_transcript(self):
        with self._lock:
            self._transcript = ""
            self._transcript_until = 0.0

    def set_indicator(self, indicator):
        self.indicator = indicator

    # =========================================================
    # RENDER LOOP
    # =========================================================

    def _current_transcript(self):
        with self._lock:
            if self._transcript and time.time() > self._transcript_until:
                self._transcript = ""
                self._transcript_until = 0.0
            return self._transcript

    def _blit(self):
        if not self.hwnd:
            return
        frame = R.render_pill(
            self.state,
            self._phase,
            self.level,
            self.indicator,
            transcript=self._current_transcript(),
        )
        update_layered_window(self.hwnd, frame, self.x, self.y)

    def _render_loop(self):
        while self._running:
            if not self.visible:
                # Nothing to draw and no phase to advance while hidden.
                time.sleep(0.05)
                self._last_frame = time.time()
                continue

            now = time.time()
            # Clamped so a stall (debugger pause, heavy LLM load) can't
            # make the animation jump a long way in a single frame.
            dt = max(0.0, min(now - self._last_frame, 0.25))
            self._last_frame = now
            self._phase += dt

            try:
                self._blit()
            except Exception as e:
                print(f"[PillWindow] blit failed: {e}")

            time.sleep(TICK_MS.get(self.state, 50) / 1000.0)
