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

# Every caption _on_tool_event can speak, spelled out here so it can be
# pre-cached alongside the filler pool at startup (see _warm_phrase_cache).
# TOOL_LABELS is the same fixed ~30-entry dict _on_tool_event reads from
# to build "label..." — kept in sync by construction, not by hand.
ALL_TOOL_CAPTIONS = tuple(f"{label}..." for label in TOOL_LABELS.values())

# How long after boot FRED greets. Long at log-on because it is starting
# alongside everything else Windows launches; near-immediate on a manual
# launch, where the greeting is the confirmation it came up at all.
GREETING_DELAY_STARTUP = 600.0
GREETING_DELAY_NOW = 6.0


class PillApp:

    def __init__(self, greet_now: bool = False):
        self.greet_now = greet_now
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
            return {"llm": False, "whisper": False, "kokoro": False}
        loaded = lambda m: bool(m is not None and m.is_loaded())
        return {
            "llm": loaded(lifecycle.llm),
            "whisper": loaded(lifecycle.stt),
            "kokoro": loaded(lifecycle.tts),
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
        self._start_tray()
        self.lifecycle.start()
        self.screen_watcher.start()
        self.hud.start_server()
        self._schedule_greeting()
        if self.tts:
            threading.Thread(target=self._warm_phrase_cache, daemon=True).start()
        print(
            "[PillApp] Ready — hold LEFT Ctrl+Alt to talk. "
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
            self._speak_proactive(pick_greeting())

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

    # =========================================================
    # ONE TURN
    # =========================================================

    def _run_turn(self, my_seq: int):
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
                self._turn_body()
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

        def merged_source():
            # The filler exists to hide the model's reasoning latency
            # (see audio/fillers.py) — a canned reply
            # never touches the model at all, so playing ~1s of filler in
            # front of "thank you" would only add delay that has nothing
            # to hide.
            if not canned_replies.is_canned(text):
                filler = pick_filler(text)
                prefix_texts.append(filler)
                event_log.log("fred_speech", text=filler, spoken=True, filler=True)
                yield filler
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


def main(greet_now: bool = False):
    app = PillApp(greet_now=greet_now)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
