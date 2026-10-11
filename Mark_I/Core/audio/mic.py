"""The one owner of the microphone: a single input stream, 16 kHz mono int16, 80 ms chunks, fanned out to subscribers.

Wake word, VAD, STT capture and the clock all listen here; nothing else opens the mic, so they can no longer fight over the device
(the old design paused one stream to open another). Replay and tests push audio through the same fan-out with feed_array(), so every
subscriber is tested on recordings exactly as it runs live.

Sockets
  in : Mic(bus=None, device=None).start() / stop() / pause() / resume()    pause releases the device (e.g. for a call), resume reopens it
       feed_array(int16, ts=None)            replay: split into chunks and deliver synchronously (last partial chunk zero-padded)
  out: subscribe(fn(chunk: int16 ndarray [CHUNK], ts: float)) -> unsubscribe()
       Subscribers run in the audio callback thread: keep them fast (a few ms); one that raises is counted and skipped, never stops the stream.
       ring(seconds) -> the most recent raw audio (int16), for pre-roll / pre-trigger; level -> peak of the last chunk (0..1)
       events on the bus: audio.mic_state {state: started|stopped|paused|resumed|failed, device, error?}
Device: settings.mic_device (name substring, "" = system default). A failed open refreshes PortAudio's device list and retries ONCE (a USB/Bluetooth
change shifts indices; the old FRED sat deaf for an hour on a dead index); a second failure emits mic_state failed and leaves the Mic stopped.
Self-check: python audio/mic.py          (no hardware)        live level check: python audio/mic.py --live    (records 2 s, stores nothing)
"""
import collections
import sys
import threading
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev

CHUNK = 1280   # 80 ms at 16 kHz: openwakeword's frame size, a whole number of 20 ms VAD frames


class Mic:
    def __init__(self, bus: ev.EventBus | None = None, device: str | None = None):
        c = config.get()
        self.bus = bus or ev.bus
        self.device = c.mic_device if device is None else device
        self._subs: list = []
        self._lock = threading.Lock()
        self._ring: collections.deque = collections.deque(maxlen=max(1, int(c.mic_ring_s * c.sample_rate / CHUNK)))
        self._stream = None
        self._carry = np.zeros(0, dtype=np.int16)
        self.level = 0.0
        self.errors = 0
        self._status_ts = 0.0

    # --- fan-out -------------------------------------------------------
    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)

        def unsubscribe():
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)
        return unsubscribe

    def _deliver(self, chunk: np.ndarray, ts: float):
        self._ring.append(chunk)
        self.level = float(np.max(np.abs(chunk))) / 32768.0
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(chunk, ts)
            except Exception as ex:
                self.errors += 1
                if self.errors <= 20:   # a broken subscriber would flood the bus at 12.5 chunks/s
                    self.bus.emit("audio", "subscriber_error", subscriber=getattr(fn, "__qualname__", repr(fn)), error=repr(ex))

    def feed_array(self, samples: np.ndarray, ts: float | None = None):
        """Replay path. ts = time of the first sample (default now); chunk timestamps advance by 80 ms."""
        samples = np.asarray(samples, dtype=np.int16)
        t = time.time() if ts is None else ts
        step = CHUNK / config.get().sample_rate
        for i in range(0, len(samples), CHUNK):
            c = samples[i:i + CHUNK]
            if len(c) < CHUNK:
                c = np.concatenate([c, np.zeros(CHUNK - len(c), dtype=np.int16)])
            self._deliver(c.copy(), t)
            t += step

    def ring(self, seconds: float | None = None) -> np.ndarray:
        chunks = list(self._ring)
        if seconds is not None:
            chunks = chunks[-max(1, int(round(seconds * config.get().sample_rate / CHUNK))):]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)

    # --- device --------------------------------------------------------
    def _pick_device(self):
        if not self.device:
            return None
        import sounddevice as sd
        want = self.device.lower()
        hits = [i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and want in d["name"].lower()]
        for i in hits:                                   # WASAPI first: the MME entry (the first name match) resamples to 44.1 kHz and has its own gain path (audit F7, 2026-10-11)
            if sd.query_hostapis(sd.query_devices(i)["hostapi"])["name"] == "Windows WASAPI":
                return i
        if hits:
            return hits[0]
        raise RuntimeError(f"no input device matching {self.device!r}")

    def _callback(self, indata, frames, time_info, status):
        try:
            if status and time.time() - self._status_ts > 10:
                self._status_ts = time.time()
                self.bus.emit("audio", "mic_state", state="stream_status", status=str(status))      # input overflow = dropped audio, invisible before
            x = np.clip(indata[:, 0] * 32767.0, -32768, 32767).astype(np.int16)
            self._carry = np.concatenate([self._carry, x])
            while len(self._carry) >= CHUNK:
                self._deliver(self._carry[:CHUNK].copy(), time.time())
                self._carry = self._carry[CHUNK:]
        except Exception as ex:   # the callback must never raise into PortAudio
            self.errors += 1
            self.bus.emit("audio", "mic_state", state="callback_error", error=repr(ex))

    def _open(self):
        import sounddevice as sd
        extra = None
        try:   # WASAPI shared mode needs auto_convert to accept 16 kHz
            idx = self._pick_device()
            dev = sd.query_devices(idx if idx is not None else sd.default.device[0])
            if sd.query_hostapis(dev["hostapi"])["name"] == "Windows WASAPI":
                extra = sd.WasapiSettings(auto_convert=True)
        except Exception:
            idx = None
            if self.device:
                raise
        s = sd.InputStream(samplerate=config.get().sample_rate, channels=1, dtype="float32", blocksize=CHUNK, device=idx, callback=self._callback, extra_settings=extra)
        s.start()
        return s, (sd.query_devices(idx if idx is not None else sd.default.device[0])["name"])

    def start(self) -> bool:
        with self._lock:
            if self._stream is not None:
                return True
        last = None
        for attempt in (0, 1):
            try:
                stream, name = self._open()
                with self._lock:
                    self._stream = stream
                self.bus.emit("audio", "mic_state", state="started", device=name)
                return True
            except Exception as ex:
                last = ex
                if attempt == 0:
                    try:
                        import sounddevice as sd
                        sd._terminate()
                        sd._initialize()   # private API, but the only way PortAudio re-enumerates devices
                    except Exception:
                        pass
        self.bus.emit("audio", "mic_state", state="failed", device=self.device or "default", error=repr(last))
        return False

    def stop(self, state: str = "stopped"):
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as ex:
                self.bus.emit("audio", "mic_state", state="stop_error", error=repr(ex))
        self.bus.emit("audio", "mic_state", state=state)

    def pause(self):
        self.stop("paused")

    def resume(self) -> bool:
        ok = self.start()
        if ok:
            self.bus.emit("audio", "mic_state", state="resumed")
        return ok

    @property
    def running(self) -> bool:
        return self._stream is not None


def _selfcheck():
    b = ev.EventBus()
    m = Mic(b, device="")
    got, ts = [], []
    m.subscribe(lambda c, t: (got.append(c), ts.append(t)))
    m.subscribe(lambda c, t: 1 / 0)                       # a broken subscriber must not stop delivery
    x = (np.arange(3000) % 200).astype(np.int16) * 100
    m.feed_array(x, ts=100.0)
    assert len(got) == 3 and all(len(c) == CHUNK for c in got)       # 3000 samples -> 2 full chunks + 1 zero-padded
    assert np.array_equal(got[0], x[:CHUNK]) and not got[2][3000 - 2 * CHUNK:].any()
    assert abs(ts[1] - 100.08) < 1e-6 and abs(ts[2] - 100.16) < 1e-6  # 80 ms steps
    assert m.errors == 3
    assert len(m.ring()) == 3 * CHUNK and len(m.ring(0.08)) == CHUNK and np.array_equal(m.ring(0.08), got[2])
    assert 0 < m.level <= 1.0
    for _ in range(100):                                   # the ring is bounded (mic_ring_s)
        m.feed_array(x[:CHUNK])
    assert len(m.ring()) <= int(config.get().mic_ring_s * 16000 / CHUNK) * CHUNK
    un = m.subscribe(lambda c, t: got.append("late"))
    un()
    n = len(got)
    m.feed_array(x[:CHUNK])
    assert len(got) == n + 1                              # unsubscribed fn not called again (only the first subscriber)
    bad = Mic(b, device="no such microphone anywhere")
    states = []
    b.subscribe(lambda e: states.append(e.data.get("state")), kinds={"mic_state"})
    assert bad.start() is False and states[-1] == "failed" and not bad.running
    print("mic ok")


def _live():
    m = Mic(ev.EventBus())
    if not m.start():
        print("could not open the microphone")
        return 1
    peaks = []
    m.subscribe(lambda c, t: peaks.append(float(np.max(np.abs(c))) / 32768))
    time.sleep(2)
    m.stop()
    print(f"{len(peaks)} chunks in 2 s (expect ~25), peak {max(peaks, default=0):.3f}, mean peak {sum(peaks) / max(1, len(peaks)):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(_live() if "--live" in sys.argv else _selfcheck())
