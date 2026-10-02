# Core/ui/overlay_bridge.py
#
# The seam between the voice loop / orchestrator glue code and the
# native overlay window. With the native-window rewrite there's no more
# JS/webview bridge — this just forwards state pushes directly to
# OverlayWindow's setter methods, and holds the two callbacks the
# native windows themselves trigger (icon click, text submit).

from typing import Callable, Literal, Optional

State = Literal["idle", "listening", "thinking", "speaking"]


class OverlayBridge:
    def __init__(self):
        self._window = None  # bound once OverlayWindow.create() runs

        # Wired up by overlay_app.py. Plain attributes rather than a
        # formal event system since each has exactly one listener.
        self.on_submit: Optional[Callable[[str], None]] = None
        self.on_toggle_fullscreen_cb: Optional[Callable[[], None]] = None

    def bind_window(self, window):
        self._window = window

    # =========================================================
    # PUSHED FROM THE VOICE LOOP / ORCHESTRATOR GLUE
    # =========================================================

    def set_state(self, state: State):
        if self._window:
            self._window.set_state(state)

    def set_transcript(self, user_text: str = "", reply_text: str = ""):
        if self._window:
            self._window.set_transcript(user_text, reply_text)

    def set_audio_level(self, level: float):
        if self._window:
            self._window.set_audio_level(level)

    def set_speaking_envelope(self, value: float):
        if self._window:
            self._window.set_speaking_envelope(value)
