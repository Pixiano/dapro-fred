# Core/ui/pill_app.py
#
# GUI-mode controller: hold left Ctrl+Alt, speak, release, FRED answers.
# ALSO: say "Hey FRED" — see Core/input/wakeword.py, added 2026-08-09
# alongside hold-to-talk, not replacing it (hands-busy moments, and it
# just feels more like talking to an assistant than reaching for a
# keybind). The original reasoning below for why hold-to-talk replaced
# the old always-on loop still holds for THAT design — trained a real
# acoustic "Hey FRED" model this time instead of the old Vosk text-
# matcher, so the false-trigger/no-endpoint problems it names are
# addressed rather than reintroduced; see wakeword.py's own docstring.
#
# Press-to-talk is a better fit than a wake word on every axis that
# mattered: nothing listens at rest (so idle cost is the pill's render
# loop and nothing else), there are no false triggers, there's no
# "Yes?" round trip before you can speak, and key-release gives Whisper
# a precisely bounded utterance instead of a VAD silence guess.
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

import json
import queue
import threading
import time

from audio import phrase_cache
from audio.fillers import ALL_FILLERS, pick_filler
from audio.greetings import ALL_GREETINGS, pick_greeting
from config.settings import TTS_ENABLED, STT_ENABLED
from orchestrator import canned_replies
from orchestrator.orchestrator import TOOL_LABELS, FREDOrchestrator
from input.hotkey import HoldHotkey
from input.wakeword import WakewordListener, watch_for_silence
from state import lockdown_state
from tools import machine_tools
from ui.pill import render as R
from ui.pill.indicators import random_indicator
from ui.pill.input_popup import POPUP_H, POPUP_W, TypeInputPopup
from ui.pill.window import PillWindow
from utils import event_log
from utils.model_lifecycle import ModelLifecycle
from utils.voice_line import BUS_DIR

# How often to check for a typed HUD command, and how long a stale
# command.json (left over from a HUD that's since closed) stays eligible
# — well under the HUD's own 20s wait, so a dead consumer never leaves
# the browser hanging on a command nobody will ever answer.
HUD_COMMAND_POLL = 0.4
HUD_COMMAND_MAX_AGE = 15.0

# How long the transcript of what you said stays on screen.
TRANSCRIPT_TTL = 2.5

# Brief visible "done" beat after speaking, before the pill disappears —
# without it the popup vanishes the instant audio ends, which reads as a
# crash rather than a completion.
IDLE_LINGER = 0.7

# How long merged_source() waits for the first real generated piece
# before giving up and injecting a filler. The cloud cascade in
# llm_client.py (Groq, then Cerebras) answers with sub-second
# time-to-first-token when it's up; the filler's whole reason to exist
# is masking SLOW generation, so playing it unconditionally in front of
# a fast cloud reply was pure added latency with nothing to hide. Only
# the local-tier fallback (or a genuinely slow API response) is slow
# enough to ever miss this window, so the filler now effectively means
# "the fast path didn't come through" rather than firing every turn.
FILLER_GRACE_SECONDS = 1.2

# Every caption _on_tool_event can speak, spelled out here so it can be
# pre-cached alongside the filler pool at startup (see _warm_phrase_cache).
# TOOL_LABELS is the same fixed ~30-entry dict _on_tool_event reads from
# to build "label..." — kept in sync by construction, not by hand.
ALL_TOOL_CAPTIONS = tuple(f"{label}..." for label in TOOL_LABELS.values())

# How long after boot FRED greets. Long at log-on because it is starting
# alongside everything else Windows launches; near-immediate on a manual
# launch, where the greeting is the confirmation it came up at all.
GREETING_DELAY_STARTUP = 120.0
GREETING_DELAY_NOW = 6.0


_current_app = None  # set by PillApp.__init__ — see get_current_app()


def get_current_app():
    """The one running PillApp instance, or None before __init__ / after
    shutdown. machine_tools.restart_fred() uses this to tear the app
    down cleanly (HUD server, tray icon) before exiting the process,
    without machine_tools importing this module at load time — pill_app
    already imports machine_tools, so that import would be circular."""
    return _current_app


class PillApp:

    def __init__(self, greet_now: bool = False, session_announce: str = ""):
        global _current_app
        self.greet_now = greet_now
        self._session_announce = session_announce
        self.orchestrator = FREDOrchestrator()

        from audio.device_info import apply_saved_devices
        apply_saved_devices()

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
            on_type=self._on_type_button,
        )
        self.type_popup = TypeInputPopup(on_submit=self._on_type_submit)
        # What to show above the type box as "the last exchange" — reuses
        # the same text already produced at each reply site below rather
        # than tracking a separate history.
        self._last_exchange = ""
        self.hotkey = HoldHotkey(
            on_press=self._on_hold_start,
            on_release=self._on_hold_end,
        )
        self.wakeword = WakewordListener(on_wake=self._on_wake_detected)
        # Set fresh each time a wake-triggered turn starts recording;
        # .set() cancels an in-flight silence watch early (a hotkey
        # press/release interrupting or superseding it) — see
        # _on_hold_start/_on_hold_end and watch_for_silence's docstring.
        self._silence_watch_stop = threading.Event()
        # Whether the CURRENT turn was wake-triggered (vs. hotkey) — see
        # _on_hold_start (resets to False on every activation) and
        # _on_wake_detected (sets True right after). Read by
        # _save_wake_capture's two call sites (_turn_body,
        # _on_cancel_button) to know whether to log this turn at all.
        self._wake_triggered = False
        self._wake_trigger_audio = None

        from vision.watcher_manager import ScreenWatcherManager
        self.screen_watcher = ScreenWatcherManager()

        # Mirror the pill's state/level onto the ~/voice-line/ file bus so
        # the HUD (hud/) can render the same turn. Purely one-way: if the
        # bus can't be written the publisher disables itself and FRED
        # behaves exactly as before.
        from utils.voice_line import VoiceLineBus
        self.voice_line = VoiceLineBus(systems=self._subsystem_status)
        self._mirror_window_to_bus()

        from utils.hud_manager import HudManager
        self.hud = HudManager()
        self._hud_cmd_seen = None  # last command.json id already answered

        # Show in the pill what FRED is doing when a tool fires, so an
        # action isn't audio-only (Phase 16's "visual confirmation").
        self.orchestrator.on_tool_event = self._on_tool_event
        self.orchestrator.on_ambiguous_choice = self._on_ambiguous_choice

        # Proactive speech (reminders, timers) goes through Kokoro rather
        # than the SAPI fallback, so an interruption sounds like the same
        # assistant you were just talking to.
        if self.tts:
            from utils import notifier
            notifier.set_voice(self._speak_proactive)

        # Only now — with set_voice already wired up if there's a voice
        # to wire — does the scheduler start processing jobs. Starting it
        # any earlier (e.g. inside FREDOrchestrator.__init__, which runs
        # as the very first line of this method) risked a persisted,
        # overdue reminder firing within moments of construction, while
        # notifier._voice was still None — speaking through the robotic
        # SAPI fallback instead of Kokoro even though this is GUI mode.
        self.orchestrator.scheduler.start()

        self._cancel = threading.Event()
        self._turn_lock = threading.Lock()
        self._recording = False
        self._turn_thread = None
        self._running = True

        # Rapid repeat presses used to genuinely queue: _turn_lock only
        # ever guaranteed two turns couldn't run AT THE SAME TIME, not
        # that a backlog of them wouldn't each still run in full, one
        # after another, long after the user had moved on — because
        # llama.cpp's generate call has no cooperative cancel point, an
        # interrupted turn only actually stops at its next checked
        # boundary, so several presses in quick succession could each
        # spawn a thread that patiently waits its turn on the lock and
        # then executes anyway. This counter is how a stale one gets
        # discarded instead: each press claims the next number, and a
        # turn checks — only once it's actually about to run, immediately
        # after acquiring the lock — whether it's still the latest
        # number issued. If not, something newer superseded it while it
        # was waiting, and it exits without transcribing, generating, or
        # speaking a word.
        self._turn_seq = 0

        # Set for the duration of one turn's TTS stream, so _on_tool_event
        # (called from the orchestrator, on the background generation
        # thread) can speak its caption through the SAME stream instead of
        # opening a new one. None outside of an active turn.
        self._active_queue = None
        self._active_prefix = None

        # Idle reclaim. `busy` covers both an in-flight turn and an
        # active recording, so nothing can be unloaded out from under a
        # request. Kokoro's slot here is RAM reclaim, not VRAM — see
        # KOKORO_UNLOAD_AFTER_WHISPER_SECONDS in settings.py.
        self.lifecycle = ModelLifecycle(
            llm=self.orchestrator.llm,
            stt=self.stt,
            tts=self.tts,
            busy=lambda: self._recording or self._turn_lock.locked(),
        )

        self.tray = None
        _current_app = self

    def _subsystem_status(self):
        """
        Which of FRED's models are currently resident, for the HUD's
        systems panel. Read through getattr because the bus is
        constructed before ModelLifecycle is — its heartbeat can fire
        during the rest of __init__, and a half-built app should report
        "not loaded" rather than raise.
        """
        lifecycle = getattr(self, "lifecycle", None)
        if lifecycle is None:
            return {"llm": False, "whisper": False, "kokoro": False, "muted": False, "locked": False}
        loaded = lambda m: bool(m is not None and m.is_loaded())
        try:
            muted = machine_tools.is_muted()
        except Exception:
            muted = False
        try:
            locked = lockdown_state.is_locked()
        except Exception:
            locked = False
        return {
            "llm": loaded(lifecycle.llm),
            "whisper": loaded(lifecycle.stt),
            "kokoro": loaded(lifecycle.tts),
            "muted": muted,
            "locked": locked,
        }

    def _mirror_window_to_bus(self):
        """
        Wrap the pill window's set_state/set_level so every UI update also
        lands on the voice-line bus.

        Wrapping the two setters once beats adding a publish call at each
        of the ~8 places that change pill state: a call site added later
        can't silently forget to publish, and nothing existing had to be
        touched to start mirroring. Both wrappers stay fire-and-forget —
        a publisher failure must never take the pill's own update with
        it, since the pill is the thing the user is actually looking at.
        """
        window = self.window
        set_state, set_level = window.set_state, window.set_level

        def state_and_publish(state, *args, **kwargs):
            try:
                self.voice_line.set_state(state)
            except Exception as e:
                print(f"[voice_line] state publish failed: {e}")
            return set_state(state, *args, **kwargs)

        def level_and_publish(level, *args, **kwargs):
            try:
                self.voice_line.set_level(level)
            except Exception as e:
                print(f"[voice_line] level publish failed: {e}")
            return set_level(level, *args, **kwargs)

        window.set_state = state_and_publish
        window.set_level = level_and_publish

    # =========================================================
    # LIFECYCLE
    # =========================================================

    def run(self):
        self.window.create()
        self.window.run(on_ready=self._on_ready)

    def _on_ready(self):
        self.hotkey.install()
        if self.stt:
            self.wakeword.resume()
        self._start_tray()
        self.lifecycle.start()
        self.screen_watcher.start()
        self.hud.start_server()
        self._start_phone_api()
        threading.Thread(target=self._hud_command_loop, daemon=True).start()
        self._schedule_greeting()
        if self.tts:
            threading.Thread(target=self._warm_phrase_cache, daemon=True).start()
        print(
            "[PillApp] Ready — hold LEFT Ctrl+Alt or say 'Hey FRED' to talk. "
            "Quit from the tray icon."
        )

    def _schedule_greeting(self):
        """
        Speak once, unprompted, shortly after coming up.

        The delay is the whole design here. At log-on FRED is competing
        with everything else Windows is starting — other startup chimes,
        a cold Kokoro, a disk still thrashing — so greeting immediately
        means talking into a mess nobody is listening to yet. Ten minutes
        in, the machine is settled and the user is actually at it.

        A manual launch is the opposite: you just double-clicked, so the
        greeting IS the confirmation FRED came up, and waiting ten
        minutes for it would be absurd. fred_popup passes --greet-now for
        that case; the default stays the log-on behaviour so the existing
        Startup shortcut needs no changes to get it.
        """
        if not self.tts:
            return
        delay = GREETING_DELAY_NOW if self.greet_now else GREETING_DELAY_STARTUP

        def run():
            time.sleep(delay)
            if not self._running:
                return
            # Skipped rather than queued if the user got in first — a
            # greeting arriving after a real exchange has begun is worse
            # than no greeting at all.
            if self._recording or self._turn_lock.locked():
                print("[PillApp] greeting skipped — already in conversation")
                return
            greeting = pick_greeting()
            try:
                if lockdown_state.is_locked():
                    greeting += " All systems nominal, sir. Lockdown mode on."
            except Exception:
                pass
            if self._session_announce:
                greeting += " " + self._session_announce
            self._speak_proactive(greeting)

        threading.Thread(target=run, daemon=True).start()
        print(f"[PillApp] greeting in {delay:.0f}s")

    def _warm_phrase_cache(self):
        """
        Pre-synthesises any filler/tool-caption phrase that isn't already
        cached (see audio/phrase_cache.py), so ordinary turns hit the
        cache instead of running Kokoro fresh. Backgrounded so a cold
        cache (first run, or after a voice/speed change) can't delay
        "Ready" — a turn that happens to need an uncached phrase before
        this finishes just synthesises it the normal way, same as today.

        Unloads Kokoro again afterwards: this thread is the one place
        that forces a load purely to build the cache, and once it's
        built, holding the model resident buys nothing further for a
        session that only ever speaks cached phrases.
        """
        hit, miss = phrase_cache.warm(
            self.tts, ALL_FILLERS + ALL_TOOL_CAPTIONS + ALL_GREETINGS
        )
        if miss:
            print(f"[PillApp] phrase cache: {hit} already cached, {miss} generated")
        if self.tts.unload():
            print("[PillApp] Kokoro unloaded after cache warm-up")

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
                    # Default item, so a plain left-click on the icon
                    # opens the HUD — the tray is the only way to summon
                    # it, and burying that behind a right-click menu
                    # would make the HUD feel hidden rather than on call.
                    pystray.MenuItem("Show HUD", lambda: self.hud.show(),
                                     default=True),
                    # Same function the "restart yourself" voice/HUD
                    # command calls (machine_tools.restart_fred) — one
                    # implementation, two triggers.
                    pystray.MenuItem("Restart FRED", lambda: machine_tools.restart_fred()),
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
        self.screen_watcher.stop()
        self.wakeword.pause()
        self._silence_watch_stop.set()
        self.hud.stop()
        # Leave the bus reading idle. Without this the HUD would sit on
        # FRED's last live state for the full staleness window after a
        # clean quit, which looks like a hang rather than a shutdown.
        try:
            self.voice_line.close()
        except Exception:
            pass
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
        self.type_popup.destroy()
        self.window.destroy()

    # =========================================================
    # HOTKEY EDGES — must return fast (see module docstring)
    # =========================================================

    def _on_hold_start(self):
        # First thing, before anything else — see watcher_manager.py's
        # touch() docstring on why this has to be fast and unconditional.
        # A real conversation turn is about to start and the screen
        # watcher (if it happens to be mid-analysis right now) must never
        # be competing for the GPU when that happens.
        self.screen_watcher.touch()

        # A hotkey press (or a wake-word detection, which also routes
        # through here — see _on_wake_detected) is "the user manually did
        # something," same signal that ends sleep mode as presence
        # returning. Cheap no-op when not currently sleeping.
        from orchestrator import sleep_mode
        sleep_mode.wake("hotkey")

        # Default for every activation; _on_wake_detected overrides this
        # to True right after calling this method, so a hotkey press
        # (which calls this directly) is never mistaken for a wake one.
        self._wake_triggered = False

        # Same reasoning: a real turn is starting (whether from the
        # hotkey or from _on_wake_detected calling this directly), so
        # the wake listener must release the mic device before STT's own
        # stream opens on it — see wakeword.py's module docstring.
        # Cancel any earlier wake-triggered silence watch too (a hotkey
        # press mid-wake-turn is an ordinary interrupt, same as the
        # cancel below) and hand this turn a fresh Event to watch.
        self.wakeword.pause()
        self._silence_watch_stop.set()
        self._silence_watch_stop = threading.Event()

        if not self.stt:
            return

        # Pressing while FRED is mid-answer is an interrupt: silence it
        # and start a fresh utterance. This is safe precisely because the
        # mic is closed during playback — the keypress is unambiguous, so
        # no echo cancellation is needed to tell FRED's voice from yours.
        self._cancel.set()

        # New indicator per activation, so both styles get seen in real
        # use rather than being compared from screenshots.
        self.window.set_indicator(random_indicator())

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
        # Re-stamp the idle clock from release, not press — the 5-minute
        # "hasn't touched the hotkey" window should count from when he
        # stopped talking to FRED, not when he started.
        self.screen_watcher.touch()

        # A real key release always ends whatever silence watch might
        # still be running from a wake-triggered turn this superseded.
        self._silence_watch_stop.set()

        if not self._recording:
            return
        self._recording = False
        self.window.set_level(0.0)
        self.window.set_state("thinking")

        # Claim this turn's number BEFORE the thread starts, on this
        # (the hotkey) thread — see _turn_seq's docstring in __init__.
        # Claiming it here rather than inside _run_turn closes a race
        # where two presses land close enough together that both threads
        # could otherwise read the counter before either increments it.
        self._turn_seq += 1
        my_seq = self._turn_seq

        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(my_seq,), daemon=True
        )
        self._turn_thread.start()

    def _on_wake_detected(self):
        """
        "Hey FRED" fired — runs on its own thread (see wakeword.py's
        _fire_wake), not the audio callback, so it's free to do real
        work. Starts a turn exactly like a hotkey press does, then
        watches for the silence a keyup would normally signal instead.
        """
        self._on_hold_start()
        self._wake_triggered = True
        # Snapshotted now, right after the fire — wakeword.py overwrites
        # last_trigger_audio on its NEXT fire, which _on_hold_start's own
        # pause() above makes impossible before this turn ends, but a
        # local copy is cheap and makes that invariant not load-bearing.
        self._wake_trigger_audio = self.wakeword.last_trigger_audio
        if not self.stt:
            return
        stop_flag = self._silence_watch_stop  # the fresh one _on_hold_start just made
        threading.Thread(
            target=watch_for_silence,
            args=(self.stt, self._on_hold_end, stop_flag),
            daemon=True,
        ).start()

    # =========================================================
    # ONE TURN
    # =========================================================

    def _run_turn(self, my_seq: int, text: str = None):
        # Serialised: a new activation interrupts the old turn (via
        # _cancel) and then waits here for its lock turn, so two turns
        # can never both be driving the pill at once.
        with self._turn_lock:
            # Checked the instant this turn actually gets to run, not
            # when it was queued — my_seq being stale here means at
            # least one newer press happened while this one was waiting
            # on the lock. That newer press is either running now or
            # about to be; this one is discarded outright rather than
            # transcribing, generating, or speaking anything for a
            # request the user has already moved past. No UI/state
            # touch either — whatever superseded this already owns the
            # pill's current state.
            if my_seq != self._turn_seq:
                return

            self._cancel.clear()
            try:
                self._turn_body(text)
            except Exception as e:
                print(f"[PillApp] turn failed: {e}")
                event_log.log_error("turn", e)
                # The HUD's only red state. Raised here rather than
                # inside the bus wrapper because a failed turn is the one
                # thing FRED knows is wrong but the pill itself shows no
                # differently — it just drops back to idle. The flash is
                # held briefly (see VoiceLineBus.alert) so the HUD's ~1s
                # poll can't miss it between the failure and the idle
                # that _to_idle_and_hide is about to publish.
                try:
                    self.voice_line.alert()
                except Exception:
                    pass
                self._to_idle_and_hide()

    def _save_wake_capture(self, cancelled: bool, transcript: str):
        """Logs one entry to the live wake-word dataset (see
        wakeword_capture.py) — only ever called when self._wake_triggered.
        A logging side-effect, never allowed to affect the actual turn;
        wakeword_capture.save() already guards itself, this is belt and
        suspenders on top, same reasoning as wakeword_log's own layered
        guards."""
        try:
            from input import wakeword_capture
            wakeword_capture.save(
                trigger_audio=self._wake_trigger_audio,
                followup_audio=self.stt.last_audio if self.stt else None,
                cancelled=cancelled,
                transcript=transcript or "",
                wake_score=self.wakeword.last_fired_score,
                wake_gain=self.wakeword.last_fired_gain,
            )
        except Exception as e:
            print(f"[PillApp] wake capture failed: {e}")

    def _turn_body(self, text: str = None):
        # Typed submissions (the type button) already have their text and
        # skip STT entirely; a mic turn (the only other caller) passes
        # nothing and transcribes here exactly as before.
        typed = text is not None
        if not typed:
            text = self.stt.stop_and_transcribe()
            if self._wake_triggered:
                self._save_wake_capture(cancelled=False, transcript=text)
                self._wake_triggered = False

        if not text:
            self._to_idle_and_hide()
            return

        print(f"You: {text}")
        event_log.log("user_speech", text=text, source="typed" if typed else "voice")

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
                self._last_exchange = f"{text} → {reply}"
            except Exception as e:
                print(f"[PillApp] {e}")
                event_log.log_error("process", e)
            self._to_idle_and_hide()
            return

        # Real generation starts right now, on a background thread, so it
        # overlaps with the filler phrase below instead of waiting for it
        # to finish first. Buffered through a queue rather than handed
        # straight to Kokoro because the filler and the real reply are two
        # separate self.tts.speak() calls sharing one producer.
        collected = []
        gen_queue = queue.Queue()

        def produce():
            try:
                for piece in self.orchestrator.process_stream(text):
                    # This early `return` is doing more than skipping
                    # remaining output — abandoning this generator
                    # without exhausting it is what actually stops
                    # llama.cpp's token loop mid-generation. Confirmed
                    # (llm_client.generate_stream's docstring): GPU load
                    # drops from 74% to idle within 0.2-0.4s of this
                    # return executing. Do not "simplify" this into
                    # draining the rest of the loop and discarding
                    # pieces afterward — that would silently reintroduce
                    # the exact background-generation cost this avoids.
                    if self._cancel.is_set():
                        return
                    collected.append(piece)
                    gen_queue.put(piece)
            except Exception as e:
                print(f"[PillApp] generation failed: {e}")
                event_log.log_error("generation", e)
                gen_queue.put("Sorry, something went wrong.")
            finally:
                # Always reached — cancel, exception, or normal finish —
                # so the consumer below can never block forever on a
                # producer that already died.
                gen_queue.put(None)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()

        def queued_source():
            while True:
                item = gen_queue.get()
                if item is None or self._cancel.is_set():
                    return
                yield item

        def on_first_audio():
            self.window.set_state("speaking")

        # Filler and any tool-event captions (see _on_tool_event) are
        # injected as their own sentences ahead of the real reply, all
        # spoken from ONE continuous output stream rather than a separate
        # self.tts.speak() call each. Two calls used to mean two
        # sd.OutputStreams, and TTS_PREROLL_SEC's Bluetooth wake-up ramp
        # (see settings.py) re-triggers on every new stream — so the real
        # reply's opening ~1s played through a second, unnecessary ramp
        # right where it mattered. One stream, one ramp, at the very start
        # of the turn instead of at the start of the actual answer.
        #
        # `prefix_texts` collects exactly what got queued this way, so it
        # can be stripped back off `spoken` below — none of it belongs in
        # conversation history, same as filler alone before this change.
        prefix_texts = []
        self._active_queue = gen_queue
        self._active_prefix = prefix_texts

        _NOTHING_YET = object()  # sentinel: distinguishes "timed out" from a real None

        def merged_source():
            # The filler exists to hide the model's reasoning latency
            # (see audio/fillers.py) — a canned reply
            # never touches the model at all, so playing ~1s of filler in
            # front of "thank you" would only add delay that has nothing
            # to hide.
            if canned_replies.is_canned(text):
                yield from queued_source()
                return

            # Wait briefly for real content before committing to a filler
            # at all — see FILLER_GRACE_SECONDS. Only if nothing shows up
            # in time (the fast cloud path didn't come through) does the
            # filler get spoken; a fast reply is yielded immediately with
            # no filler in front of it.
            try:
                first_item = gen_queue.get(timeout=FILLER_GRACE_SECONDS)
            except queue.Empty:
                first_item = _NOTHING_YET

            if first_item is _NOTHING_YET:
                filler = pick_filler(text)
                prefix_texts.append(filler)
                event_log.log("fred_speech", text=filler, spoken=True, filler=True)
                yield filler
                yield from queued_source()
                return

            if first_item is None or self._cancel.is_set():
                return
            yield first_item
            yield from queued_source()

        # If the real reply isn't ready the moment the filler (and any
        # tool captions) end, this just blocks here with no audio — the
        # pill stays exactly as it is until there's something real to
        # speak.
        try:
            spoken = self.tts.speak(
                merged_source(),
                on_level=self.window.set_level,
                on_first_audio=on_first_audio,
                cancel=self._cancel,
            )
        finally:
            self._active_queue = None
            self._active_prefix = None
            # llama.cpp is NOT thread-safe, and _turn_lock alone does not
            # protect it: the lock is released when _turn_body returns,
            # but `produce` runs on its own thread and can still be inside
            # create_chat_completion at that moment. Interrupting a reply
            # then immediately speaking again therefore started a second
            # generation on the same model while the first was still
            # decoding — two concurrent llama_decode calls, which aborts
            # the whole process with no catchable Python error.
            #
            # That is the "crashes if you hit the hotkey more than twice"
            # report: two turns 5s apart, each logging only its filler and
            # no reply, then `Fatal Python error: Aborted` inside
            # llama_cpp/_internals.py decode().
            #
            # Joining here keeps the generation inside the lock's lifetime,
            # so the next turn cannot start until this one has genuinely
            # left llama.cpp. A cancelled turn still waits for the C call
            # to return — it cannot be interrupted — which costs a moment
            # of latency on a fast re-press and is strictly better than
            # killing the process. The timeout is a backstop against a
            # wedged generation deadlocking the app forever.
            producer.join(timeout=120)
            if producer.is_alive():
                print("[PillApp] generation thread still running after 120s")
                event_log.log("error", source="turn", message="producer join timed out")

        reply = "".join(collected).strip()
        self._last_exchange = f"{text} → {reply}" if reply else text

        # `spoken` is everything that came out of the speaker this turn,
        # in order: filler, then any tool captions, then the real reply —
        # all from the single merged stream above. Strip the injected
        # prefix back off before comparing to `reply`, which never
        # included it, so a normal filler-then-answer turn doesn't read
        # as "interrupted" just because `spoken` started with "On it.".
        prefix_joined = " ".join(prefix_texts)
        heard_reply = (
            spoken[len(prefix_joined):].strip()
            if prefix_joined and spoken.startswith(prefix_joined)
            else spoken
        )

        # bool(spoken) rather than bool(heard_reply): if the turn was cut
        # off during the filler/captions, before a single character of
        # the real reply played, heard_reply is "" — falsy — which would
        # read as "not interrupted" under the same test that correctly
        # catches a cut mid-reply. spoken (the un-stripped original) is
        # only empty when literally nothing was heard at all, including
        # the filler, which is the one case with truly nothing to report.
        interrupted = bool(spoken) and heard_reply != reply

        print(f"F.R.E.D.: {reply}")
        event_log.log("fred_speech", text=reply, spoken=True, interrupted=interrupted)

        # Only what was actually heard belongs in history. Recording the
        # full reply after an interrupt would leave FRED believing it said
        # things the user never heard, and follow-ups go incoherent.
        if interrupted:
            print(f"[PillApp] interrupted — spoke {len(heard_reply)}/{len(reply)} chars")
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
        # from under itself — same reasoning covers resuming the wake
        # listener: a new turn already in progress must keep it paused.
        if not self._recording:
            self.window.hide()
            self.window.clear_transcript()
            if self.stt:
                self.wakeword.resume()

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
        """
        A tool is about to run — flash what it is on the pill AND speak
        it, so the cue exists whether or not you're looking at the
        screen. Runs on the orchestrator's background generation thread,
        mid-turn, which is why it reaches the live stream through
        self._active_queue/_active_prefix rather than a direct call —
        _turn_body owns the actual self.tts.speak() call.

        Silently a no-op outside of an active turn (_active_queue is
        None between turns), which matters because on_tool_event is a
        single instance-wide hook — nothing else scopes it to "only while
        a turn is in flight".
        """
        caption = f"{label}..."
        self.window.set_transcript(caption, ttl=2.0)
        event_log.log("tool_event", label=label)

        queue_ = self._active_queue
        prefix = self._active_prefix
        if queue_ is not None and prefix is not None:
            prefix.append(caption)
            queue_.put(caption)

    def _on_ambiguous_choice(self, top: str, alt: str):
        """
        The router found a genuine near-tie between two tools. FRED
        already picked `top` and is acting on it — this is purely
        informational, shown on the pill and NOT spoken (unlike
        _on_tool_event above), so a correct-but-uncertain turn doesn't
        cost extra speech time on every near-tie. A longer TTL than the
        tool-event caption since there are two things to actually read.
        """
        self.window.set_transcript(f"Went with: {top} (also close: {alt})", ttl=3.5)
        event_log.log("ambiguous_choice", top=top, alt=alt)

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
                # Paused for the same reason a real turn already pauses it
                # (see _on_hold_start) — the mic stays live otherwise, and
                # speaker-to-mic bleed of FRED's OWN voice can false-fire
                # the wake word and get captured as if it were a real
                # follow-up. Confirmed live: two captures in the wake-word
                # training set are FRED's own startup greeting, picked up
                # and transcribed as user speech. Every proactive
                # utterance (greeting, reminders, timers, proactive
                # checks) routes through this one function, so pausing
                # here covers all of them at once.
                self.wakeword.pause()
                try:
                    spoken = self.tts.speak(message, on_level=self.window.set_level)
                finally:
                    self.wakeword.resume()
                print(f"[PillApp] proactive speech done ({len(spoken)}/{len(message)} chars spoken)")
            except Exception as e:
                print(f"[PillApp] proactive speech failed: {e}")
                event_log.log_error("proactive_speech", e)
            finally:
                self._turn_lock.release()
                self._to_idle_and_hide()

        threading.Thread(target=run, daemon=True).start()

    def _start_phone_api(self):
        """
        Bring up the LAN endpoint the phone posts commands to.

        Its own process, like the HUD server, for the same reason: a
        crash there must not take FRED down with it. Both ends of the
        command bus (_hud_command_loop below) are already running by the
        time anything can arrive, so no ordering care is needed here.
        """
        import os
        import socket
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "web" / "phone_api.py"
        if not script.is_file():
            print(f"[phone_api] not found at {script}")
            return

        # Adopt an already-running endpoint instead of fighting for the
        # port — same rule as HudManager.start_server, and it matters more
        # here: HTTPServer sets SO_REUSEADDR, which on Windows lets a
        # second process bind a port that is already bound instead of
        # failing cleanly. Two live servers then split incoming requests
        # nondeterministically, and only one of them is on FRED's bus.
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", 8779)) == 0:
                print("[phone_api] already up on :8779 - reusing it")
                return

        try:
            self._phone_api = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt" else 0,
            )
            print(f"[phone_api] started (pid {self._phone_api.pid})")
        except OSError as e:
            # A busy port (a previous FRED that didn't exit cleanly) lands
            # here. The phone loses its front door; the voice path is
            # untouched, so this is a warning, not a failure.
            print(f"[phone_api] failed to start: {e}")

    def _hud_command_loop(self):
        """
        Answers text typed into the HUD's console (hud/index.html's
        #cmd). hud/server.py's submit_command() is the only thing that
        writes command.json and the only thing that reads
        command_reply.json — this is that channel's other end.

        Polling a plain file rather than anything fancier because this
        is one text box, used occasionally, not a stream: the whole
        point of the file bus (see voice_line.py) is that neither side
        has to be running for the other to work.
        """
        cmd_path = BUS_DIR / "command.json"
        reply_path = BUS_DIR / "command_reply.json"

        while self._running:
            time.sleep(HUD_COMMAND_POLL)
            try:
                stat = cmd_path.stat()
                data = json.loads(cmd_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

            req_id = data.get("id")
            text = str(data.get("text", "")).strip()
            if not req_id or not text or req_id == self._hud_cmd_seen:
                continue
            self._hud_cmd_seen = req_id

            # A command left over from before FRED (re)started, or from a
            # browser tab that's since given up waiting — answering it
            # now would just write a reply nobody reads.
            if time.time() - stat.st_mtime > HUD_COMMAND_MAX_AGE:
                continue

            def write_reply(reply_text, _req_id=req_id):
                # Fires as soon as the text exists — the browser gets its
                # answer the moment generation finishes, not after FRED
                # has also finished reading it aloud. See
                # _answer_hud_command's on_reply note for why speaking
                # still happens after this, inside the same lock.
                try:
                    reply_path.write_text(
                        json.dumps({"id": _req_id, "text": reply_text}),
                        encoding="utf-8",
                    )
                except OSError:
                    pass

            self._answer_hud_command(text, on_reply=write_reply)

    def _answer_hud_command(self, text: str, on_reply) -> None:
        """
        Runs typed text through the same orchestrator a spoken turn
        uses, then speaks the reply same as a mic turn would — a typed
        question still gets FRED's voice, not just text back. Held
        under _turn_lock like a mic turn, and unlike the mic path this
        call is synchronous (no background producer thread), so the
        lock's lifetime already covers both the llama.cpp call and the
        speech: the two can never overlap in the model, same guarantee
        _run_turn relies on.

        `on_reply(text)` fires the instant the reply text exists, before
        speaking starts — the HTTP request on the other end (see
        hud/server.py's submit_command) is waiting on that text, not on
        FRED finishing a possibly-long read-aloud. Speaking still runs
        inside this same _turn_lock hold afterward, so a second command
        (typed or spoken) still can't start until this one has actually
        finished talking — same serialisation a mic turn gets, just with
        the network response no longer sitting behind it.

        set_state/set_level go through self.window rather than
        self.voice_line directly — window.py is wrapped to mirror both
        onto the bus (see _mirror_window_to_bus), so the HUD's arc
        reactor animates through this exactly like a mic turn, with no
        second thing to keep in sync. window.show() is deliberately
        never called: a HUD-typed turn has no reason to pop the pill
        onto the desktop too.
        """
        print(f"[hud console] {text}")
        event_log.log("user_speech", text=text, source="hud")

        with self._turn_lock:
            self.window.set_state("thinking")
            try:
                reply = self.orchestrator.process(text)
            except Exception as e:
                print(f"[PillApp] hud command failed: {e}")
                event_log.log_error("hud_command", e)
                self.window.set_state("idle")
                on_reply("Sorry, something went wrong.")
                return

            event_log.log("fred_speech", text=reply, spoken=bool(self.tts), source="hud")
            self._last_exchange = f"{text} → {reply}"
            on_reply(reply)

            if self.tts and reply:
                self.window.set_state("speaking")
                try:
                    self.tts.speak(reply, on_level=self.window.set_level)
                except Exception as e:
                    print(f"[PillApp] hud command speech failed: {e}")
                    event_log.log_error("hud_command_speech", e)

            self.lifecycle.touch()
            self.window.set_level(0.0)
            self.window.set_state("idle")

    def _on_cancel_button(self):
        self._cancel.set()
        self._recording = False
        if self.stt:
            # cancel_recording() now returns whatever was captured (see
            # stt_whisper.py) — this is the exit path a wake-triggered
            # turn most often actually takes in practice (confirmed
            # live 2026-08-13: "I have mostly cancelled the thread
            # using the FRED button"), and that audio used to just be
            # thrown away here before it could ever be logged.
            self.stt.cancel_recording()
            if self._wake_triggered:
                self._save_wake_capture(cancelled=True, transcript="")
                self._wake_triggered = False
        self.window.clear_transcript()
        self.window.set_state("idle")
        self.window.hide()

    def _on_accept_button(self):
        # Mid-answer: stop talking but keep the exchange.
        self._cancel.set()

    def _on_type_button(self):
        """Type button on the pill — pop the EDIT-control box just above
        the capsule, pre-filled with nothing, showing the last exchange
        for context."""
        gap = 10
        x = self.window.x + (R.CANVAS_W - POPUP_W) // 2
        y = max(0, self.window.y - POPUP_H - gap)
        self.type_popup.show(x, y, reply_text=self._last_exchange)

    def _on_type_submit(self, text):
        """Enter in the type box — runs the exact same turn pipeline a
        mic release does (_run_turn -> _turn_body), just pre-loaded with
        typed text instead of a transcription, so filler/streaming/TTS/
        locking all behave identically to a spoken turn."""
        self._cancel.set()  # interrupt any turn still in flight, same as a fresh hotkey press
        self.window.clear_transcript()
        self.window.set_state("thinking")
        self.window.show()

        self._turn_seq += 1
        my_seq = self._turn_seq
        self._turn_thread = threading.Thread(
            target=self._run_turn, args=(my_seq, text), daemon=True
        )
        self._turn_thread.start()


def main(greet_now: bool = False, session_announce: str = ""):
    app = PillApp(greet_now=greet_now, session_announce=session_announce)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
