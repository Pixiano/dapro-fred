# Core/ui/pill_app.py
#
# GUI-mode controller: hold left Ctrl+Alt, speak, release, FRED answers.
#
# Replaces the atticked always-on wake-word loop. Press-to-talk is a
# better fit than a wake word on every axis that mattered: nothing
# listens at rest (so idle cost is the pill's render loop and nothing
# else), there are no false triggers, there's no "Yes?" round trip before
# you can speak, and key-release gives Whisper a precisely bounded
# utterance instead of a VAD silence guess.
#
# Threading contract:
#   - main thread          creates the window, installs the keyboard hook,
#                          pumps messages (all three must share a thread)
#   - render thread        owned by PillWindow
#   - one turn thread      per activation: STT -> orchestrator -> TTS
#
# The hotkey callbacks run inside the low-level keyboard hook and must
# return immediately (Windows unhooks a slow callback with no warning),
# so they only flip state and hand off to the turn thread.

import random
import threading
import time

from config.settings import TTS_ENABLED, STT_ENABLED
from orchestrator.orchestrator import FREDOrchestrator
from input.hotkey import HoldHotkey
from ui.pill.indicators import random_indicator
from ui.pill.window import PillWindow
from utils import event_log
from utils.model_lifecycle import ModelLifecycle

# How long the transcript of what you said stays on screen.
TRANSCRIPT_TTL = 2.5

# Brief visible "done" beat after speaking, before the pill disappears —
# without it the popup vanishes the instant audio ends, which reads as a
# crash rather than a completion.
IDLE_LINGER = 0.7


class PillApp:

    def __init__(self):
        self.orchestrator = FREDOrchestrator()

        self.stt = None
        self.tts = None
        if STT_ENABLED:
            from audio.stt_whisper import WhisperSTT
            self.stt = WhisperSTT()
        if TTS_ENABLED:
            from audio.tts_kokoro import KokoroTTS
            self.tts = KokoroTTS()

        self.window = PillWindow(
            random_indicator(),
            on_cancel=self._on_cancel_button,
            on_accept=self._on_accept_button,
        )
        self.hotkey = HoldHotkey(
            on_press=self._on_hold_start,
            on_release=self._on_hold_end,
        )

        # Show in the pill what FRED is doing when a tool fires, so an
        # action isn't audio-only (Phase 16's "visual confirmation").
        self.orchestrator.on_tool_event = self._on_tool_event

        # Proactive speech (reminders, timers) goes through Kokoro rather
        # than the SAPI fallback, so an interruption sounds like the same
        # assistant you were just talking to.
        if self.tts:
            from utils import notifier
            notifier.set_voice(self._speak_proactive)

        self._cancel = threading.Event()
        self._turn_lock = threading.Lock()
        self._recording = False
        self._turn_thread = None
        self._running = True

        # Idle VRAM reclaim. `busy` covers both an in-flight turn and an
        # active recording, so nothing can be unloaded out from under a
        # request.
        self.lifecycle = ModelLifecycle(
            llm=self.orchestrator.llm,
            stt=self.stt,
            busy=lambda: self._recording or self._turn_lock.locked(),
        )

        self.tray = None

    # =========================================================
    # LIFECYCLE
    # =========================================================

    def run(self):
        self.window.create()
        self.window.run(on_ready=self._on_ready)

    def _on_ready(self):
        self.hotkey.install()
        self._start_tray()
        self.lifecycle.start()
        print(
            "[PillApp] Ready — hold LEFT Ctrl+Alt to talk. "
            "Quit from the tray icon."
        )

    def _start_tray(self):
        """Only way to quit: the pill is transient and click-through, so
        there's no window for the user to close."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            icon_img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(icon_img)
            d.ellipse([6, 6, 58, 58], fill=(18, 22, 30, 255))
            d.ellipse([20, 26, 44, 38], fill=(90, 200, 255, 255))

            self.tray = pystray.Icon(
                "FRED", icon_img, "FRED — hold Left Ctrl+Alt",
                menu=pystray.Menu(
                    pystray.MenuItem("Quit FRED", lambda: self.shutdown()),
                ),
            )
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            print(f"[PillApp] tray unavailable ({e}) — Ctrl+C to quit")

    def shutdown(self):
        event_log.log("system", note="session end")
        self._running = False
        self._cancel.set()
        self.lifecycle.stop()
        try:
            self.hotkey.uninstall()
        except Exception:
            pass
        if self.stt:
            self.stt.cancel_recording()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        try:
            self.orchestrator.shutdown()
        except Exception:
            pass
        self.window.destroy()

    # =========================================================
    # HOTKEY EDGES — must return fast (see module docstring)
    # =========================================================

    def _on_hold_start(self):
        if not self.stt:
            return

        # Pressing while FRED is mid-answer is an interrupt: silence it
        # and start a fresh utterance. This is safe precisely because the
        # mic is closed during playback — the keypress is unambiguous, so
        # no echo cancellation is needed to tell FRED's voice from yours.
        self._cancel.set()

        # New indicator per activation, so both styles get seen in real
        # use rather than being compared from screenshots.
        self.window.set_indicator(random_indicator(random))

        # Start reloading anything the watchdog freed, now, so it happens
        # while the user speaks rather than after. Returns immediately.
        self.lifecycle.preload()

        self.window.clear_transcript()
        self.window.set_state("listening")
        self.window.set_level(0.0)
        self.window.show()

        self._recording = True
        threading.Thread(target=self._begin_recording, daemon=True).start()

    def _begin_recording(self):
        try:
            self.stt.start_recording()
            # Feed the listening waveform from live mic RMS.
            while self._recording and self._running:
                self.window.set_level(self.stt.level)
                time.sleep(0.03)
        except Exception as e:
            print(f"[PillApp] recording failed: {e}")
            event_log.log_error("recording", e)
            self._recording = False
            self._to_idle_and_hide()

    def _on_hold_end(self):
        if not self._recording:
            return
        self._recording = False
        self.window.set_level(0.0)
        self.window.set_state("thinking")
        self._turn_thread = threading.Thread(target=self._run_turn, daemon=True)
        self._turn_thread.start()

    # =========================================================
    # ONE TURN
    # =========================================================

    def _run_turn(self):
        # Serialised: a new activation interrupts the old turn (via
        # _cancel) and then waits here for it to actually finish, so two
        # turns can never both be driving the pill.
        with self._turn_lock:
            self._cancel.clear()
            try:
                self._turn_body()
            except Exception as e:
                print(f"[PillApp] turn failed: {e}")
                event_log.log_error("turn", e)
                self._to_idle_and_hide()

    def _turn_body(self):
        text = self.stt.stop_and_transcribe()

        if not text:
            self._to_idle_and_hide()
            return

        print(f"You: {text}")
        event_log.log("user_speech", text=text)

        # Shown immediately and left up for a couple of seconds while the
        # model works. Deliberately NOT a confirmation gate — inserting a
        # mandatory 2-3s pause before every query would cost more than
        # seeing the text is worth. Cancel with the X if it misheard.
        self.window.set_transcript(text, ttl=TRANSCRIPT_TTL)

        if self._cancel.is_set():
            self._to_idle_and_hide()
            return

        self.window.set_state("thinking")

        if not self.tts:
            try:
                reply = self.orchestrator.process(text)
                print(f"F.R.E.D.: {reply}")
                event_log.log("fred_speech", text=reply, spoken=False)
            except Exception as e:
                print(f"[PillApp] {e}")
                event_log.log_error("process", e)
            self._to_idle_and_hide()
            return

        # Stream the reply into the speaker. The generator is consumed by
        # Kokoro's producer thread, so synthesis of sentence one overlaps
        # generation of sentence two — the state flips to "speaking" on
        # the first audio callback rather than after the model finishes.
        collected = []

        def piece_source():
            try:
                for piece in self.orchestrator.process_stream(text):
                    if self._cancel.is_set():
                        return
                    collected.append(piece)
                    yield piece
            except Exception as e:
                print(f"[PillApp] generation failed: {e}")
                event_log.log_error("generation", e)
                yield "Sorry, something went wrong."

        def on_first_audio():
            self.window.set_state("speaking")

        spoken = self.tts.speak(
            piece_source(),
            on_level=self.window.set_level,
            on_first_audio=on_first_audio,
            cancel=self._cancel,
        )

        reply = "".join(collected).strip()
        print(f"F.R.E.D.: {reply}")
        event_log.log(
            "fred_speech", text=reply, spoken=True,
            interrupted=bool(spoken and spoken != reply),
        )

        # Only what was actually heard belongs in history. Recording the
        # full reply after an interrupt would leave FRED believing it said
        # things the user never heard, and follow-ups go incoherent.
        if spoken and spoken != reply:
            print(f"[PillApp] interrupted — spoke {len(spoken)}/{len(reply)} chars")
            # Weak negative signal on whatever tool this turn called, if
            # any — see orchestrator/tool_call_log.py. Cutting FRED off
            # doesn't always mean the tool was wrong, but it's evidence
            # worth keeping alongside the stronger error/success signals.
            from orchestrator import tool_call_log
            tool_call_log.log_turn_feedback(
                self.orchestrator.last_turn_id, interrupted=True
            )

        self._to_idle_and_hide()

    def _to_idle_and_hide(self):
        # Restart the idle clock from the end of the turn, not its start.
        self.lifecycle.touch()
        self.window.set_level(0.0)
        self.window.set_state("idle")
        time.sleep(IDLE_LINGER)
        # A new activation during the linger must not hide the pill out
        # from under itself.
        if not self._recording:
            self.window.hide()
            self.window.clear_transcript()

    # =========================================================
    # BUTTONS
    # =========================================================
    #
    # Both are near-equivalent right now, and that's expected: with
    # hold-to-talk, releasing the key already sends, so there is nothing
    # for a "confirm" button to confirm. They're retained as requested;
    # they become meaningfully different if a latch/toggle mode is added,
    # where an explicit send and an explicit discard are both needed.

    # =========================================================
    # HOOKS FROM THE ORCHESTRATOR / SCHEDULER
    # =========================================================

    def _on_tool_event(self, label: str):
        """A tool is about to run — flash what it is on the pill."""
        self.window.set_transcript(label + "...", ttl=2.0)
        event_log.log("tool_event", label=label)

    def _speak_proactive(self, message: str):
        """
        Speak a reminder or timer in FRED's own voice, showing the pill
        while it talks so an unprompted interruption has a face.

        Runs on the scheduler's thread. Skipped entirely if a turn is
        already in flight — talking over yourself is worse than a toast
        that waits, and the toast has already fired regardless.
        """
        if not self.tts:
            print("[PillApp] proactive speech skipped: no TTS configured")
            return
        if self._recording:
            print("[PillApp] proactive speech skipped: user is mid-recording")
            return

        def run():
            if not self._turn_lock.acquire(blocking=False):
                # Silent before this fix — a reminder firing during a
                # live conversation turn would vanish with no trace, which
                # is indistinguishable from a real bug when reported as
                # "it didn't speak". Logged now so the two are tellable
                # apart.
                print("[PillApp] proactive speech skipped: a turn is already running")
                return
            try:
                self.window.set_transcript(message, ttl=6.0)
                self.window.set_state("speaking")
                self.window.show()
                print(f"[PillApp] speaking proactively: {message!r}")
                event_log.log("fred_speech", text=message, spoken=True, proactive=True)
                spoken = self.tts.speak(message, on_level=self.window.set_level)
                print(f"[PillApp] proactive speech done ({len(spoken)}/{len(message)} chars spoken)")
            except Exception as e:
                print(f"[PillApp] proactive speech failed: {e}")
                event_log.log_error("proactive_speech", e)
            finally:
                self._turn_lock.release()
                self._to_idle_and_hide()

        threading.Thread(target=run, daemon=True).start()

    def _on_cancel_button(self):
        self._cancel.set()
        self._recording = False
        if self.stt:
            self.stt.cancel_recording()
        self.window.clear_transcript()
        self.window.set_state("idle")
        self.window.hide()

    def _on_accept_button(self):
        # Mid-answer: stop talking but keep the exchange.
        self._cancel.set()


def main():
    app = PillApp()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
