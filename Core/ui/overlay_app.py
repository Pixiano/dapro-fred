# Core/ui/overlay_app.py
#
# Top-level controller for GUI mode — wires the orchestrator, audio
# pipeline, native overlay window, and system tray together into a
# non-blocking, event-driven equivalent of Core/main.py's run_voice_loop.

import threading

from config.settings import STT_ENABLED, TTS_ENABLED, WAKE_WORD_ENABLED
from orchestrator.orchestrator import FREDOrchestrator
from audio.tts import TTSManager
from audio.mic_level import MicLevelMonitor
from ui.overlay_bridge import OverlayBridge
from ui.overlay_window import OverlayWindow
from ui.overlay_settings import (
    load_overlay_settings,
    save_overlay_settings,
    resolve_position,
)
from ui.tray import TrayIcon


class OverlayApp:

    def __init__(self):

        self.settings = load_overlay_settings()
        width = self.settings["size"]["width"]
        height = self.settings["size"]["height"]
        x, y = resolve_position(self.settings, width, height)

        self.orchestrator = FREDOrchestrator()
        self.tts = TTSManager()

        # STT/wake-word are optional per config — mirrors the same
        # STT_ENABLED/WAKE_WORD_ENABLED checks Core/main.py already uses.
        self.stt = None
        self.wake_word = None
        if STT_ENABLED:
            from audio.stt import STTManager
            self.stt = STTManager()
            if WAKE_WORD_ENABLED:
                from audio.wake_word import WakeWordListener
                self.wake_word = WakeWordListener(stt=self.stt)

        self.bridge = OverlayBridge()
        self.bridge.on_submit = self._on_submit_text
        # Fullscreen sizing is owned entirely by OverlayWindow now (the
        # icon click handler resizes itself) — no app-level bookkeeping
        # needed here.

        self.window = OverlayWindow(self.bridge, width=width, height=height, x=x, y=y)
        self.window.create()

        self.mic_monitor = MicLevelMonitor(on_level=self.bridge.set_audio_level)

        self.tray = TrayIcon(
            get_settings=lambda: self.settings,
            apply_settings=self._apply_settings,
            on_quit=self.shutdown,
        )

        self._running = True

    def run(self):
        """Blocks the main thread — the three native windows are created
        on this thread, and Win32 requires the same thread to pump their
        message queue (win32gui.PumpMessages, called inside window.run)."""
        self.window.run(on_ready=self._on_ready)

    def _on_ready(self):
        """Fires once the native windows exist and are ready to receive
        state pushes — starts everything that drives them."""
        self.tray.start()
        self.bridge.set_state("idle")

        if STT_ENABLED and TTS_ENABLED and self.stt:
            threading.Thread(target=self._voice_loop, daemon=True).start()

    # =========================================================
    # VOICE LOOP — event-driven analogue of Core/main.py::run_voice_loop
    # =========================================================

    def _voice_loop(self):
        while self._running:
            try:
                if self.wake_word:
                    self.bridge.set_state("idle")
                    self.wake_word.listen_for_wake_word()
                    self.bridge.set_state("listening")
                    self.mic_monitor.start()
                    self.tts.speak("Yes?")
                else:
                    self.bridge.set_state("listening")
                    self.mic_monitor.start()

                user_input = self.stt.listen_once()
                self.mic_monitor.stop()

                if not user_input:
                    self.bridge.set_state("idle")
                    continue

                self._handle_input(user_input)

            except Exception as e:
                print(f"[OverlayApp] Voice loop error: {e}")
                self.mic_monitor.stop()
                self.bridge.set_state("idle")

    def _on_submit_text(self, text: str):
        # Runs on the native message-loop thread (the EDIT control's
        # subclassed WM_CHAR handler) — never block that thread with the
        # LLM call, hand off immediately.
        threading.Thread(target=self._handle_input, args=(text,), daemon=True).start()

    def _handle_input(self, text: str):
        self.bridge.set_transcript(user_text=text)
        self.bridge.set_state("thinking")

        try:
            response = self.orchestrator.process(text)
        except Exception as e:
            response = f"Error: {e}"

        self.bridge.set_transcript(user_text=text, reply_text=response)
        self.bridge.set_state("speaking")

        def on_word():
            self.bridge.set_speaking_envelope(1.0)

        def on_end():
            self.bridge.set_speaking_envelope(0.0)
            self.bridge.set_state("idle")

        self.tts.speak(response, on_word=on_word, on_end=on_end)

    # =========================================================
    # SETTINGS / TRAY
    # =========================================================

    def _apply_settings(self, update: dict):
        self.settings.update(update)
        save_overlay_settings(self.settings)

        width = self.settings["size"]["width"]
        height = self.settings["size"]["height"]
        x, y = resolve_position(self.settings, width, height)
        self.window.move_to(x, y)

        if "palette_hue" in update:
            self.window.set_palette_hue(update["palette_hue"])

    # =========================================================
    # SHUTDOWN
    # =========================================================

    def shutdown(self):
        self._running = False
        self.mic_monitor.stop()
        self.tray.stop()
        try:
            self.orchestrator.shutdown()
        except Exception:
            pass
        self.window.destroy()


def main():
    app = OverlayApp()
    app.run()


if __name__ == "__main__":
    main()
