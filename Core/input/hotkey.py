# Core/input/hotkey.py
#
# Left Ctrl + Left Alt hold-to-talk, via a low-level keyboard hook.
#
# Why WH_KEYBOARD_LL and not RegisterHotKey: RegisterHotKey only fires on
# key *down*, and press-and-hold is the entire interaction here. A
# low-level hook sees every keydown/keyup system-wide, so both edges are
# available.
#
# Two hard constraints this module is built around:
#
#   1. The callback must return almost immediately. Windows silently
#      unhooks a process whose hook callback exceeds LowLevelHooksTimeout
#      (300 ms by default) and reports no error when it does — the hotkey
#      simply stops working. So the callback only mutates a small set and
#      invokes callbacks that are contractually non-blocking; all real
#      work (audio capture, STT, LLM, TTS) belongs on another thread.
#
#   2. install() must run on a thread that pumps messages. Low-level
#      hooks are delivered through the installing thread's message queue,
#      so without a pump the callback never fires at all. The pill's
#      window thread already calls PumpMessages, which is where this
#      gets installed.
#
# Left-hand keys specifically: on international layouts AltGr is reported
# as LeftCtrl + *Right* Alt, so binding Left Alt keeps this chord from
# ever colliding with it.

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

_DOWN_MSGS = (WM_KEYDOWN, WM_SYSKEYDOWN)
_UP_MSGS = (WM_KEYUP, WM_SYSKEYUP)

VK_LCONTROL = 0xA2
VK_LMENU = 0xA4

CHORD = frozenset({VK_LCONTROL, VK_LMENU})


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


# lParam is declared as LPARAM (an integer) rather than as a struct
# pointer so it can be handed straight back to CallNextHookEx without a
# cast; the struct is read by casting inside the callback instead.
HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.CallNextHookEx.restype = ctypes.c_long

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class HoldHotkey:
    """
    Fires on_press when both chord keys are held, on_release when either
    is let go. Both callbacks must return promptly (see module docstring)
    — hand anything slow to a worker thread.

    Deliberately observe-only: the hook always calls through to
    CallNextHookEx rather than swallowing the chord. Suppression is a
    trap here — at Ctrl-down you cannot yet know whether Alt is coming,
    and swallowing a keyup whose keydown was delivered leaves the
    foreground app with a stuck modifier. Ctrl+Alt pressed and released
    is a common no-op chord (it is the AltGr shape), so passing it
    through is the safe default. If Alt's menu-bar activation ever does
    show through, that is the thing to revisit, not this default.
    """

    def __init__(self, on_press=None, on_release=None, chord=CHORD):
        self.on_press = on_press
        self.on_release = on_release
        self.chord = frozenset(chord)

        self._down = set()
        self._engaged = False
        self._hook = None

        # Hard reference to the trampoline. ctypes will otherwise garbage
        # collect the callback object while Windows still holds its
        # address, and the next keypress calls into freed memory.
        self._proc = HOOKPROC(self._callback)

    # =========================================================
    # HOOK CALLBACK — must stay fast
    # =========================================================

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:  # HC_ACTION
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode

            if vk in self.chord:
                if w_param in _DOWN_MSGS:
                    self._down.add(vk)
                    if not self._engaged and self.chord <= self._down:
                        self._engaged = True
                        self._fire(self.on_press)

                elif w_param in _UP_MSGS:
                    self._down.discard(vk)
                    if self._engaged:
                        self._engaged = False
                        self._fire(self.on_release)

        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    @staticmethod
    def _fire(callback):
        if callback is None:
            return
        try:
            callback()
        except Exception as e:
            # An exception must never escape into the hook chain — it
            # would propagate through ctypes into Windows' own dispatch.
            print(f"[hotkey] callback error: {e}")

    # =========================================================
    # LIFECYCLE
    # =========================================================

    def install(self):
        """Must be called on a message-pumping thread (see module docstring)."""
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            raise OSError(
                "SetWindowsHookExW(WH_KEYBOARD_LL) failed — "
                f"GetLastError={kernel32.GetLastError()}"
            )
        return self._hook

    def uninstall(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    @property
    def engaged(self) -> bool:
        return self._engaged
