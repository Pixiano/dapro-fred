"""Hold-to-talk chord (default Left Ctrl + Left Alt) via a low-level keyboard hook, reported on the bus.

Why a hook and not RegisterHotKey: hold-to-talk needs both the key-down and the key-up edge. Hard constraints (kept from the old, measured design):
the hook callback must return in well under 300 ms or Windows silently unhooks the process, so it only flips state and emits an event (emit is synchronous:
subscribers of audio.hotkey must be fast, hand real work to another thread); hooks are delivered through the installing thread's message queue, so
start() runs its own pump thread. Left-hand keys only: AltGr reports as LeftCtrl + Right Alt, so Left Alt never collides with it.
Observe-only: the hook always passes keys on (swallowing a key-up whose key-down went through leaves a stuck modifier in the foreground app).

Sockets
  in : Hotkey(bus, chord=CHORD).start() / stop()     start installs the hook on a dedicated thread with a message pump; returns False (and emits audio.hotkey_error) on failure
  out: bus events  audio.hotkey {down: True}   both chord keys held        audio.hotkey {down: False}   either released after engaging
       engaged property. Windows only (ctypes/user32).
Self-check: python audio/hotkey.py (drives the callback with synthetic key records; the real hook needs a human at the keyboard: python audio/hotkey.py --live)
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import events as ev

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
WH_KEYBOARD_LL = 13
_DOWN, _UP = (0x0100, 0x0104), (0x0101, 0x0105)
VK_LCONTROL, VK_LMENU = 0xA2, 0xA4
CHORD = frozenset({VK_LCONTROL, VK_LMENU})


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class Hotkey:
    def __init__(self, bus: ev.EventBus | None = None, chord=CHORD):
        self.bus, self.chord = bus or ev.bus, frozenset(chord)
        self._down: set = set()
        self._engaged = False
        self._hook = None
        self._thread = None
        self._tid = 0
        self._proc = HOOKPROC(self._callback)   # hard reference: ctypes would collect the trampoline while Windows still holds its address

    @property
    def engaged(self) -> bool:
        return self._engaged

    def _key(self, vk: int, w_param: int):
        if vk not in self.chord:
            return
        if w_param in _DOWN:
            self._down.add(vk)
            if not self._engaged and self.chord <= self._down:
                self._engaged = True
                self._emit(True)
        elif w_param in _UP:
            self._down.discard(vk)
            if self._engaged:
                self._engaged = False
                self._emit(False)

    def _emit(self, down: bool):
        try:
            self.bus.emit("audio", "hotkey", down=down)
        except Exception:
            pass   # never let an exception escape into the hook chain

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:
            self._key(ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode, w_param)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        ready, ok = threading.Event(), []

        def pump():
            self._tid = kernel32.GetCurrentThreadId()
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            ok.append(bool(self._hook))
            if not self._hook:
                self.bus.emit("audio", "hotkey_error", error=f"SetWindowsHookExW failed, GetLastError={kernel32.GetLastError()}")
            ready.set()
            msg = wintypes.MSG()
            while self._hook and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

        self._thread = threading.Thread(target=pump, name="hotkey-pump", daemon=True)
        self._thread.start()
        ready.wait(5)
        return bool(ok and ok[0])

    def stop(self):
        if self._thread and self._thread.is_alive():
            hook, self._hook = self._hook, None
            if hook:
                user32.UnhookWindowsHookEx(hook)
            user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT ends the pump
            self._thread.join(2)
        self._down.clear()
        self._engaged = False


def _selfcheck():
    b, got = ev.EventBus(), []
    b.subscribe(lambda e: got.append(e.data["down"]), kinds={"hotkey"})
    h = Hotkey(b)
    D, U = 0x0100, 0x0101
    h._key(VK_LCONTROL, D)
    assert got == [] and not h.engaged                      # one key is not the chord
    h._key(0x41, D)                                         # an unrelated key changes nothing
    h._key(VK_LMENU, D)
    assert got == [True] and h.engaged
    h._key(VK_LMENU, D)                                     # key auto-repeat does not re-fire
    assert got == [True]
    h._key(VK_LMENU, U)
    assert got == [True, False] and not h.engaged
    h._key(VK_LCONTROL, U)
    assert got == [True, False]                             # releasing the second key after disengage is silent
    h._key(VK_LCONTROL, 0x0104), h._key(VK_LMENU, 0x0104)   # SYSKEYDOWN counts as down (Alt chords arrive as SYS messages)
    assert got[-1] is True
    # the real trampoline path: a struct pointer in lParam is read and the call is passed on
    h2, got2 = Hotkey(b), []
    b.subscribe(lambda e: got2.append(e.data["down"]), kinds={"hotkey"})
    for vk in (VK_LCONTROL, VK_LMENU):
        rec = KBDLLHOOKSTRUCT(vkCode=vk)
        h2._proc(0, D, ctypes.addressof(rec))
    assert got2 == [True]
    assert h2._proc(-1, D, 0) == 0                          # n_code < 0: pass through untouched
    assert h2.start() and h2.stop() is None and not h2.engaged   # the pump thread installs and removes the hook cleanly
    print("hotkey ok")


def _live():
    b = ev.EventBus()
    b.subscribe(lambda e: print("chord", "DOWN" if e.data["down"] else "UP"), kinds={"hotkey"})
    h = Hotkey(b)
    print("hook installed:", h.start(), "- hold Left Ctrl + Left Alt (10 s)")
    time.sleep(10)
    h.stop()


if __name__ == "__main__":
    _live() if "--live" in sys.argv else _selfcheck()
