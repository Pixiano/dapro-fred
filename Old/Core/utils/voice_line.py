# Core/utils/voice_line.py
#
# Publishes FRED's UI state to ~/voice-line/, the plain-file bus the HUD
# reads (hud/server.py). Two files, both tiny:
#
#   state      one word: idle | listening | thinking | speaking | alert
#   waveform   FRED's current voice amplitude, 0..1
#
# One-way and fire-and-forget by design. Nothing here is allowed to
# fail a turn: every write is wrapped, and if the bus directory can't be
# created the whole publisher quietly disables itself. FRED must work
# identically whether or not the HUD is running, and the HUD must be
# able to appear or vanish mid-turn without FRED noticing.
#
# Three things this has to get right, all of them consequences of how
# the reader works:
#
#   HEARTBEAT — the HUD ignores a state file older than 5s and eases back
#   to idle, so that a crashed producer decays to calm instead of
#   freezing. That means a state which is merely UNCHANGED still has to
#   be re-stamped: a 20-second "thinking" turn writes the word once and
#   would otherwise go stale at the 5s mark and show idle while FRED is
#   still working. A background thread re-writes the current state well
#   inside that window.
#
#   WAVEFORM ONLY WHILE SPEAKING — the reader's stomp-tolerance rule
#   treats any fresh, non-silent waveform as proof FRED is talking, and
#   lets it override the state file. The pill feeds set_level() from the
#   microphone during listening too; publishing that would make the HUD
#   read "speaking" while the USER is the one talking. So level updates
#   are dropped unless the current state is actually speaking.
#
#   THROTTLING — set_level() is driven by the audio callback, far faster
#   than the HUD's ~1.2s poll. Writes are rate-limited to something that
#   still lands comfortably inside the 0.4s freshness window.

import json
import threading
import time
from pathlib import Path

BUS_DIR = Path.home() / "voice-line"

# Must stay well under the reader's 0.4s stomp-freshness window.
WAVEFORM_MIN_INTERVAL = 0.1
# Must stay well under the reader's 5s staleness cutoff. Also bounds how
# late an alert hold can release (it can only end on a tick), which is why
# this is 1s rather than the ~4s the staleness rule alone would allow.
HEARTBEAT_SECONDS = 1.0

VALID = ("idle", "listening", "thinking", "speaking", "alert")

# The pill has a "working" state the HUD has no equivalent for. Both mean
# "busy, maximum motion", so it rides along with thinking rather than
# being dropped as an unrecognised word.
ALIASES = {"working": "thinking"}


class VoiceLineBus:
    """
    Fire-and-forget publisher. Safe to call from any thread; safe to call
    when nothing is reading.
    """

    def __init__(self, bus_dir=None, systems=None):
        """
        `systems` is an optional callable returning a dict of subsystem
        name -> bool/loaded state. It exists because the HUD runs in its
        own process and has no way to see whether Whisper or Kokoro are
        currently resident; only FRED knows that. Published on the same
        heartbeat as the state file rather than on change, since model
        loads happen off in ModelLifecycle with nothing to hook.
        """
        self.dir = Path(bus_dir) if bus_dir else BUS_DIR
        self._lock = threading.Lock()
        self._state = "idle"
        self._last_wave = 0.0
        self._alert_until = 0.0
        self._running = False
        self._thread = None
        self._systems = systems

        self.enabled = self._ensure_dir()
        if self.enabled:
            self._write("state", "idle")
            self._publish_systems()
            self._start_heartbeat()

    # =========================================================
    # PUBLIC
    # =========================================================

    def set_state(self, state):
        state = ALIASES.get(state, state)
        if not self.enabled or state not in VALID:
            return
        with self._lock:
            # An alert holds the HUD for a moment even as FRED moves on;
            # see alert() for why.
            if state != "alert" and time.monotonic() < self._alert_until:
                self._state = state          # remember, publish later
                return
            if state == self._state:
                return
            self._state = state
        self._write("state", state)

    def set_level(self, level):
        """Voice amplitude, 0..1. Ignored unless FRED is speaking."""
        if not self.enabled:
            return
        with self._lock:
            if self._state != "speaking":
                return
            now = time.monotonic()
            if now - self._last_wave < WAVEFORM_MIN_INTERVAL:
                return
            self._last_wave = now
        try:
            value = min(1.0, max(0.0, float(level)))
        except (TypeError, ValueError):
            return
        self._write("waveform", f"{value:.4f}")

    def alert(self, hold=2.5):
        """
        Flash the HUD red.

        The hold exists because the HUD only samples the bus about once
        a second: FRED's own error path sets alert and returns to idle in
        a few milliseconds, so without pinning it the flash would fall
        between two polls and never be seen at all.
        """
        if not self.enabled:
            return
        with self._lock:
            self._alert_until = time.monotonic() + hold
            self._state = "alert"
        self._write("state", "alert")

    def close(self):
        """Leave the bus reading idle rather than whatever was last live."""
        self._running = False
        if not self.enabled:
            return
        with self._lock:
            self._alert_until = 0.0
            self._state = "idle"
        self._write("state", "idle")

    # =========================================================
    # INTERNAL
    # =========================================================

    def _ensure_dir(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            print(f"[voice_line] disabled — cannot use {self.dir}: {e}")
            return False

    def _write(self, name, text):
        # Deliberately a plain write, not write-temp-then-replace. These
        # files are a handful of bytes, the reader already tolerates a
        # short or garbled read (it validates the word and ignores
        # unparseable floats), and os.replace against a reader that has
        # the file open can raise on Windows. A torn read costs one frame
        # of idle; a raised exception during speech would cost more.
        try:
            (self.dir / name).write_text(text, encoding="utf-8")
        except OSError:
            pass

    def _start_heartbeat(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._heartbeat, name="voice-line", daemon=True
        )
        self._thread.start()

    def _heartbeat(self):
        while self._running:
            time.sleep(HEARTBEAT_SECONDS)
            if not self._running:
                return
            with self._lock:
                state = self._state
                # An expired alert hold releases here, so the state FRED
                # moved on to during the flash is what gets re-stamped.
                releasing = bool(
                    self._alert_until and time.monotonic() >= self._alert_until
                )
                if releasing:
                    self._alert_until = 0.0
            # Idle normally needs no heartbeat — it is also what the reader
            # falls back to once the file goes stale. The exception is the
            # tick that releases an alert: if FRED went back to idle during
            # the flash, skipping the write here would strand "alert" in the
            # file until it aged out, holding the HUD red for seconds after
            # everything was fine again.
            if state != "idle" or releasing:
                self._write("state", state)
            self._publish_systems()

    def _publish_systems(self):
        """Never let a bad provider kill the heartbeat — the state file
        matters more than the systems panel."""
        if self._systems is None:
            return
        try:
            payload = json.dumps(self._systems())
        except Exception:
            return
        self._write("systems", payload)
