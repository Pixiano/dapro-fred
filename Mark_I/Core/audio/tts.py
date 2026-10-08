"""Voice out: Kokoro (CPU, ONNX) speaks a reply sentence by sentence while the next sentence is still being synthesised; cancel() silences it within one 43 ms audio block.

Sockets
  in : Speaker(bus=None, synth=None, stream=None).speak(text, cancel=None) -> str      blocks while speaking; returns the text actually SPOKEN (record that, not the full reply, when cancelled)
       text may be a string or an iterable of pieces (a streaming model): speech starts at the first complete sentence
       Speaker.warm(texts)   pre-synthesise short fixed phrases into an in-memory cache (FILLERS); a cache hit costs no synthesis
       Speaker.level         0..1 loudness of what is playing now (the pill animates from it)
       Speaker.cancel()      stop what is playing now (the barge-in handler; speak() also takes its own cancel Event)
       synth(text) -> (float32 samples, rate)   default: Kokoro read in place from the frozen Old/Core/models/kokoro; stream(rate) -> object with start/write/abort/stop/close   default: sounddevice
       output device: the Windows default, always (config has no device setting)
  out: bus events tts.start {} / tts.end {cancelled} (the turn controller uses them for barge-in and the follow-up window); config.tts_enabled False = nothing is spoken, the text is still returned
Bluetooth: the link ramps its volume at the start and buffers at the end, so `tts_preroll_s` of silence is written at once (after the first sentence has been synthesised, so every spoken reply starts about that long after the sentence is ready; it is skipped when audio ended less than `tts_preroll_gap_s` ago, and fillers/interim lines get no tail) and `tts_postroll_s` after the last word (measured live on Vatsal's headphones, 2026-08); set both to 0 on a wired output.
Self-check: python audio/tts.py (fake synth and stream; Kokoro itself is exercised by tests/live_tts.py)
"""
import queue
import random
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log

BLOCK = 1024
FILLERS = ("On it.", "One moment.", "Let me check.", "Just a second.", "Checking now.")     # 2-3 words, spoken only when a reply is slower than filler_after_s (engine/live.py)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_LINK = re.compile(r"\[([^\]]*)\]\(https?://[^\s)]+\)")
_URL = re.compile(r"https?://(?:www\.)?([a-z0-9-]+)\.\S+", re.I)                              # a bare URL is spoken as its site name, never letter by letter
_NOISE = re.compile(r"[*_`#>]+")
_BULLET = re.compile(r"^\s*[-•]\s*", re.M)
_TAG = re.compile(r"\[[^\]]*\]")                                                              # internal ids like [reminder_17_1]
_FRAC = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)")
_TIMES = re.compile(r"(?<!\d)(\d+)\s*\*\s*(\d+)(?!\d)")


def clean(text: str) -> str:
    text = _FRAC.sub(r"\1 over \2", text)
    text = _TIMES.sub(r"\1 times \2", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(r"\1", text)
    text = _TAG.sub("", text)
    text = _BULLET.sub("", text)
    return re.sub(r"[ \t]+", " ", _NOISE.sub("", text)).strip()


def split(text: str, first_max: int = 90, min_chunk: int = 45) -> list:
    """Sentences, front-loaded for latency: a short opening chunk (cut at a comma if the first sentence is long), short fragments ("Dr.") merged into the next."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    bounds = [0] + [m.end() for m in _SENTENCE.finditer(text)] + [len(text)]
    spans = []
    for a, b in zip(bounds, bounds[1:]):
        if not text[a:b].strip():
            continue
        if spans and spans[-1][1] - spans[-1][0] < min_chunk:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    out = [text[a:b].strip() for a, b in spans]
    if out and len(out[0]) > first_max:
        cut = out[0].rfind(", ", 0, first_max)
        if cut > min_chunk:
            out[0:1] = [out[0][:cut + 1], out[0][cut + 2:]]
    return out


class _Kokoro:
    def __init__(self):
        self._k, self._lock = None, threading.Lock()

    def __call__(self, text: str):
        with self._lock:
            if self._k is None:
                from kokoro_onnx import Kokoro
                c = config.get()
                self._k = Kokoro(c.kokoro_model, c.kokoro_voices)
        c = config.get()
        return self._k.create(text, voice=c.tts_voice, speed=c.tts_speed, lang="en-us")


def _device_stream(rate: int):
    import sounddevice as sd
    want, idx = config.get().speaker_device.lower(), None
    for i, d in enumerate(sd.query_devices()):
        if want and d["max_output_channels"] > 0 and want in d["name"].lower():
            idx = i
            break
    return sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=idx)       # idx None = the system default


class Speaker:
    def __init__(self, bus=None, synth=None, stream=None):
        self.bus, self._synth, self._stream = bus, synth or _Kokoro(), stream or _device_stream
        self._cache: dict = {}
        self._cancel = threading.Event()
        self._last_end = 0.0                                                # when audio last stopped: the Bluetooth lead-in is only needed after a gap
        self.level = 0.0                                                    # 0..1 loudness of the block being played
        self._lock = threading.Lock()                                       # one reply at a time

    def warm(self, texts=FILLERS):
        for t in texts:
            try:
                self._cache[t] = self._synth(clean(t))
            except Exception as ex:
                log.session("tts_warm_error", error=type(ex).__name__)

    def cancel(self):
        self._cancel.set()

    def _emit(self, kind, **d):
        self.bus and self.bus.emit("tts", kind, **d)

    def speak(self, text, cancel=None, quiet=False) -> str:
        """quiet=True (fillers, interim lines): no tail silence, and the tts events carry quiet=True so the pill and HUD do not treat it as the answer."""
        pieces = [text] if isinstance(text, str) else text
        if not config.get().tts_enabled:
            return "".join(p or "" for p in pieces).strip()
        with self._lock:
            self._cancel.clear()
            mine = cancel or threading.Event()
            stop = lambda: mine.is_set() or self._cancel.is_set()
            done = threading.Event()
            q: queue.Queue = queue.Queue(maxsize=1)                         # one sentence of look-ahead hides the synthesis time and keeps a cancel cheap

            def put(item):
                while not (stop() or done.is_set()):
                    try:
                        q.put(item, timeout=0.1)
                        return
                    except queue.Full:
                        pass

            def synth(chunk):
                words = clean(chunk)
                if not words:
                    return
                try:
                    put((chunk, *(self._cache.get(words) or self._synth(words))))
                except Exception as ex:
                    log.session("tts_error", error=type(ex).__name__)

            def producer():
                buf, first = "", True
                for p in pieces:
                    if stop():
                        break
                    buf = re.sub(r"[ \t]+", " ", buf + (p or "")).lstrip()
                    while not stop():
                        ready = split(buf, 90, 12) if first else split(buf, 10 ** 6)                  # the opener may be short: speech starts at once
                        if not (len(ready) > 1 or buf.rstrip().endswith((".", "!", "?"))):
                            break
                        synth(ready[0])
                        first = False
                        i = buf.find(ready[0])
                        buf = buf[i + len(ready[0]):] if i >= 0 else ""
                for chunk in ([] if stop() else split(buf)):
                    synth(chunk)
                put(None)

            threading.Thread(target=producer, daemon=True, name="tts-synth").start()
            c, spoken, stream, rate, natural = config.get(), [], None, None, False
            self._emit("start", quiet=quiet)
            try:
                while not stop():
                    try:
                        item = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is None:
                        natural = True
                        break
                    chunk, samples, rate = item
                    if stream is None:
                        stream = self._stream(rate)
                        stream.start()
                        c.tts_preroll_s > 0 and time.monotonic() - self._last_end > c.tts_preroll_gap_s and stream.write(np.zeros(int(rate * c.tts_preroll_s), np.float32))
                    for i in range(0, len(samples), BLOCK):
                        if stop():
                            break
                        block = np.ascontiguousarray(samples[i:i + BLOCK], dtype=np.float32)
                        stream.write(block)
                        self.level = min(1.0, float(np.sqrt(np.mean(block * block))) * 4)           # what is audible now, for the pill's animation
                    else:
                        spoken.append(chunk)
            finally:
                done.set()
                if stream is not None:
                    try:
                        if stop():
                            stream.abort()
                        else:
                            natural and not quiet and c.tts_postroll_s > 0 and stream.write(np.zeros(int(rate * c.tts_postroll_s), np.float32))
                            stream.stop()
                        stream.close()
                    except Exception as ex:
                        log.session("tts_error", error=type(ex).__name__)
                self.level = 0.0
                self._last_end = time.monotonic()
                self._emit("end", cancelled=not natural, quiet=quiet)
            return " ".join(spoken).strip()


def pick_filler() -> str:
    return random.choice(FILLERS)


def _selfcheck():
    import time
    import events as ev
    assert clean("See [the report](https://example.com/r) and https://www.example.org/x/y now, 3/4 of **it**, 2 * 3.\n- item [reminder_1_2]") == "See the report and example now, 3 over 4 of it, 2 times 3.\nitem"
    assert split("Hi there. Dr. Smith is here. It costs 3.5 dollars and that is all for today, thanks a lot.") == ["Hi there. Dr. Smith is here. It costs 3.5 dollars and that is all for today, thanks a lot."]          # fragments under 45 chars merge
    assert split("This sentence is comfortably longer than the minimum. Short two. And a third one that is also long enough to stand.") == ["This sentence is comfortably longer than the minimum.", "Short two. And a third one that is also long enough to stand."]
    long = "This first sentence is very long and keeps going on and on without any stop, so it needs to be cut at a comma somewhere sensible for speed. Then more."
    assert len(split(long)[0]) <= 90 and len(split(long)) == 3 and split("") == []

    class Stream:
        log = []

        def __init__(self, rate):
            self.rate, self.events = rate, []
            Stream.log.append(self)

        def start(self): self.events.append("start")
        def write(self, a): self.events.append(len(a)); time.sleep(0.002)
        def abort(self): self.events.append("abort")
        def stop(self): self.events.append("stop")
        def close(self): self.events.append("close")

    made = []
    synth = lambda t: (made.append(t), (np.ones(3000, np.float32) * 0.1, 24000))[1]
    bus = ev.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append((e.kind, e.data)), sources=("tts",))
    c = config.get()
    c.tts_preroll_s, c.tts_postroll_s = 0.5, 0.25
    sp = Speaker(bus, synth, Stream)
    out = sp.speak("First sentence is here. Second one follows now. Third and last.")
    assert out == "First sentence is here. Second one follows now. Third and last.", out
    ev_s = Stream.log[-1].events
    assert ev_s[:2] == ["start", 12000] and ev_s[-3:] == [6000, "stop", "close"], ev_s                       # preroll first, postroll after the last word
    assert seen[0] == ("start", {"quiet": False}) and seen[-1] == ("end", {"cancelled": False, "quiet": False})
    # a quiet line (filler / interim): events say so, no tail silence; and no lead-in right after other audio (the link is awake)
    n = len(seen)
    sp._last_end = time.monotonic()
    sp.speak("Quick one.", quiet=True)
    ev_q = Stream.log[-1].events
    assert ev_q[0] == "start" and ev_q[1] != 12000 and ev_q[-2:] == ["stop", "close"] and 6000 not in ev_q, ev_q
    assert seen[n] == ("start", {"quiet": True}) and seen[-1] == ("end", {"cancelled": False, "quiet": True})
    sp._last_end = 0.0
    # streaming input: speech starts at the first complete sentence, before the rest has arrived
    got = []

    def stream_pieces():
        yield "Hello there, how are"
        yield " you today? I am well"
        got.append(list(made))                                                      # the first sentence was already synthesised
        yield " thanks."
    made.clear()
    assert sp.speak(stream_pieces()).startswith("Hello there, how are you today?") and got[0] == ["Hello there, how are you today?"], (got, made)
    # cache: a warmed phrase is not synthesised again
    sp.warm(["On it."])
    made.clear()
    sp.speak("On it.")
    assert made == []
    # cancel mid-sentence: audio aborts, only whole spoken sentences are returned, the event says cancelled
    slow = lambda t: (np.zeros(240000, np.float32), 24000)
    sp2 = Speaker(bus, slow, Stream)
    ev_ = threading.Event()
    threading.Timer(0.15, ev_.set).start()
    t0 = time.time()
    r = sp2.speak("A very long first sentence that never really finishes in time. And another one.", cancel=ev_)
    assert r == "" and time.time() - t0 < 1.0 and Stream.log[-1].events[-2:] == ["abort", "close"] and seen[-1] == ("end", {"cancelled": True, "quiet": False}), (r, Stream.log[-1].events[-3:])
    c.tts_enabled = False                                                           # off: the text comes back, nothing plays
    n = len(Stream.log)
    assert sp.speak("quiet please") == "quiet please" and len(Stream.log) == n
    config.reload()
    print("tts ok")


if __name__ == "__main__":
    _selfcheck()
