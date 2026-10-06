"""Screen sense: capture on a fixed period, detect change with a tile-max difference, hand out a native-resolution crop of what changed. No model involved.

Most seconds nothing changes, so this decides when anything downstream (OCR, describer) is worth running; the crop, not a bigger model, is what made small text readable.
Frames live in memory only (the latest one); nothing is written to disk and events carry numbers, never pixels.

Detector: the frame is sampled every `screen_stride` pixels, grey-scaled (green channel), averaged into a rows x cols tile grid; a tile's similarity to the previous
frame is 1 - mean|diff| / 255; the frame score is the LOWEST tile similarity ("tile-max difference"). Changed when score < screen_threshold (0.990).
The first frame, and any frame whose size differs from the last (resolution or monitor switch), only set the baseline.

Fast path (lossless): the tile grid is quantised to bins of q = floor((1 - screen_threshold) * 255) levels (2 at 0.990) and hashed with sha256. Equal hashes mean every tile
moved by less than q levels, which is below the change threshold, so the similarity pass is skipped (`skips` counts them). Any real change flips at least one bin, so nothing is lost.
The same hash marks `screen.settled` events `duplicate=True` when the screen has returned to exactly the state of the previous settled event (a toast came and went, a menu
opened and closed): downstream OCR / description can skip those.

Sockets
  in : ScreenSense(bus, source=None).start() / stop()          a thread ticks every screen_interval_s on a fixed schedule (no drift; an overrun is counted, not queued)
       tick() -> Change | None                                  one capture+compare, synchronously (tests, replay, benchmarks)
       source = anything with grab() -> HxWx3|4 uint8 ndarray   default MssSource (primary monitor); ListSource replays frames
  out: bus event  screen.change {score, tiles, bbox: [x0,y0,x1,y1] in native px, w, h}      every tick that differs from the previous one (can be 5-10 per second on a busy screen)
                  screen.settled {bbox, changes, ms, duplicate}   `screen_settle_s` after the LAST change of a burst: the union of the burst's regions. THIS is what OCR and the describer
                                                       should follow, so a video or a scrolling page costs one pass at the end, not ten a second
       latest() -> last frame (ndarray) | None;  crop(bbox=None, pad=None) -> RGB ndarray at native resolution;  png(arr) -> bytes (for the vision slot)
       stats() -> {ticks, changes, overruns, grab_ms, detect_ms}  (running means; the resource numbers behind the interval choice, see tests/bench_screen.py)
Failure: a grab error emits screen.error (rate-limited to one per 30 s) and the loop keeps going.
Self-check: python senses/screen.py (synthetic frames, no display needed)
"""
import hashlib
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev


@dataclass
class Change:
    score: float
    tiles: int
    bbox: tuple
    w: int
    h: int


class MssSource:
    def __init__(self, monitor: int | None = None):
        import mss
        self._sct = mss.mss()
        self.monitor = config.get().screen_monitor if monitor is None else monitor

    def grab(self) -> np.ndarray:
        return np.asarray(self._sct.grab(self._sct.monitors[self.monitor]))   # BGRA, native resolution

    def close(self):
        self._sct.close()


class ListSource:
    """Replays frames in order; after the last one it keeps returning it (a screen that stopped changing)."""

    def __init__(self, frames):
        self.frames, self.i = list(frames), 0

    def grab(self) -> np.ndarray:
        f = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return f


class TileDetector:
    def __init__(self):
        self._prev: np.ndarray | None = None
        self._shape = None
        self.last_fp: bytes | None = None
        self.skips = 0

    def _tiles(self, frame: np.ndarray) -> np.ndarray:
        c = config.get()
        g = frame[::c.screen_stride, ::c.screen_stride, 1 if frame.shape[2] > 1 else 0]   # green channel, strided
        r, k = c.screen_grid_rows, c.screen_grid_cols
        h, w = g.shape[0] // r * r, g.shape[1] // k * k
        return g[:h, :w].reshape(r, h // r, k, w // k).mean(axis=(1, 3), dtype=np.float32)

    def reset(self):
        self._prev = self._shape = self.last_fp = None

    @staticmethod
    def _fp(tiles: np.ndarray) -> bytes:
        q = max(1, int((1.0 - config.get().screen_threshold) * 255))
        return hashlib.sha256((tiles // q).astype(np.int16).tobytes()).digest()

    def update(self, frame: np.ndarray) -> Change | None:
        c = config.get()
        t = self._tiles(frame)
        fp = self._fp(t)
        if self._prev is None or self._shape != frame.shape:
            self._prev, self._shape, self.last_fp = t, frame.shape, fp
            return None
        same, self.last_fp = fp == self.last_fp, fp
        if same:                                           # lossless fast path, see module docstring
            self._prev = t
            self.skips += 1
            return None
        sim = 1.0 - np.abs(t - self._prev) / 255.0
        self._prev = t
        bad = sim < c.screen_threshold
        n = int(bad.sum())
        if n < c.screen_min_tiles:
            return None
        rows, cols = np.where(bad)
        h, w = frame.shape[:2]
        th, tw = h / c.screen_grid_rows, w / c.screen_grid_cols
        bbox = (int(cols.min() * tw), int(rows.min() * th), min(w, int((cols.max() + 1) * tw)), min(h, int((rows.max() + 1) * th)))
        return Change(float(sim.min()), n, bbox, w, h)


class ScreenSense:
    def __init__(self, bus: ev.EventBus | None = None, source=None):
        self.bus = bus or ev.bus
        self._source = source
        self.det = TileDetector()
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._n = {"ticks": 0, "changes": 0, "overruns": 0}
        self._grab_s = self._detect_s = 0.0
        self._last_err = -1e9
        self._clock = time.monotonic          # injectable for tests
        self._settled_fp = None
        self._pend = None                     # [x0,y0,x1,y1, n_changes, t_first, t_last] of the burst in progress

    # --- one step ------------------------------------------------------
    def tick(self) -> Change | None:
        if self._source is None:
            self._source = MssSource()
        t0 = time.perf_counter()
        frame = self._source.grab()
        t1 = time.perf_counter()
        ch = self.det.update(frame)
        t2 = time.perf_counter()
        with self._lock:
            self._latest = frame
            self._n["ticks"] += 1
            self._grab_s += t1 - t0
            self._detect_s += t2 - t1
        now = self._clock()
        if ch:
            self._n["changes"] += 1
            self.bus.emit("screen", "change", score=round(ch.score, 4), tiles=ch.tiles, bbox=list(ch.bbox), w=ch.w, h=ch.h)
            b = ch.bbox
            p = self._pend
            self._pend = [b[0], b[1], b[2], b[3], 1, now, now] if p is None else [min(p[0], b[0]), min(p[1], b[1]), max(p[2], b[2]), max(p[3], b[3]), p[4] + 1, p[5], now]
        elif self._pend and now - self._pend[6] >= config.get().screen_settle_s:
            p, self._pend = self._pend, None
            dup = self.det.last_fp == self._settled_fp
            self._settled_fp = self.det.last_fp
            self.bus.emit("screen", "settled", bbox=p[:4], changes=p[4], ms=int((p[6] - p[5]) * 1000), duplicate=dup)
        return ch

    # --- loop ----------------------------------------------------------
    def _run(self):
        period = config.get().screen_interval_s
        nxt = time.monotonic()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as ex:
                if time.monotonic() - self._last_err > 30:
                    self._last_err = time.monotonic()
                    self.bus.emit("screen", "error", error=repr(ex))
            nxt += period
            wait = nxt - time.monotonic()
            if wait < 0:
                self._n["overruns"] += 1
                nxt = time.monotonic()
            else:
                self._stop.wait(wait)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screen-sense", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(5)

    # --- outputs -------------------------------------------------------
    def latest(self):
        with self._lock:
            return self._latest

    def crop(self, bbox=None, pad: int | None = None) -> np.ndarray | None:
        f = self.latest()
        if f is None:
            return None
        pad = config.get().crop_pad_px if pad is None else pad
        h, w = f.shape[:2]
        x0, y0, x1, y1 = bbox or (0, 0, w, h)
        x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
        c = f[y0:y1, x0:x1]
        return np.ascontiguousarray(c[:, :, 2::-1] if c.shape[2] == 4 else c[:, :, ::-1])   # BGR(A) -> RGB

    @staticmethod
    def png(rgb: np.ndarray) -> bytes:
        import cv2
        ok, buf = cv2.imencode(".png", rgb[:, :, ::-1])
        if not ok:
            raise ValueError("png encode failed")
        return buf.tobytes()

    def stats(self) -> dict:
        n = max(1, self._n["ticks"])
        return {**self._n, "skips": self.det.skips, "grab_ms": round(self._grab_s / n * 1000, 2), "detect_ms": round(self._detect_s / n * 1000, 2)}


def _frame(w=1280, h=720, fill=40):
    return np.full((h, w, 4), fill, dtype=np.uint8)


def _selfcheck():
    b, got = ev.EventBus(), []
    b.subscribe(got.append, sources={"screen"})
    a = _frame()
    same = a.copy()
    blink = a.copy()
    blink[300:316, 500:502, :3] = 255                       # a text cursor: 2 x 16 px
    toast = a.copy()
    toast[620:700, 900:1260, :3] = 200                      # a toast in the bottom right
    flash = toast.copy()
    flash[:, :, :3] += 1                                    # a 1-level global brightness shift: below the threshold
    s = ScreenSense(b, ListSource([a, same, blink, blink, toast, toast, flash]))
    assert s.tick() is None                                 # first frame: baseline
    assert s.tick() is None                                 # identical
    c = s.tick()                                            # cursor appears
    assert c and c.tiles >= 1 and c.bbox[0] <= 500 < c.bbox[2] and c.bbox[1] <= 300 < c.bbox[3] and c.score < 0.990
    assert s.tick() is None                                 # unchanged since the cursor frame
    c = s.tick()                                            # toast
    assert c and c.bbox[0] <= 900 and c.bbox[2] >= 1259 and c.bbox[1] <= 620 and c.bbox[3] >= 699 and c.tiles > 20
    assert s.tick() is None
    assert s.tick() is None                                 # tiny global shift is not a change
    assert [e.kind for e in got] == ["change", "change"] and got[1].data["bbox"] == list(c.bbox) and "frame" not in got[0].data
    crop = s.crop(c.bbox)
    assert crop.shape[2] == 3 and crop.shape[0] >= 80 and crop.shape[1] >= 360                      # native resolution: no scaling, pad only
    f = _frame(fill=0)
    f[0, 0] = (255, 0, 0, 255)                              # BGRA blue pixel -> RGB (0,0,255)
    s2 = ScreenSense(b, ListSource([f]))
    s2.tick()
    assert tuple(s2.crop((0, 0, 1, 1), pad=0)[0, 0]) == (0, 0, 255)
    assert s.png(crop)[:8] == b"\x89PNG\r\n\x1a\n"
    s3 = ScreenSense(b, ListSource([_frame(), _frame(640, 480, 90)]))                              # a resolution change is a new baseline, not a change
    s3.tick()
    assert s3.tick() is None
    st = s.stats()
    assert st["ticks"] == 7 and st["changes"] == 2 and st["grab_ms"] >= 0
    # settle: a burst of changes yields ONE screen.settled, settle_s after the last change, with the union region
    sb, settled = ev.EventBus(), []
    sb.subscribe(lambda e: settled.append(dict(e.data)), kinds={"settled"})
    clock = [0.0]
    c1, c2 = a.copy(), a.copy()
    c1[100:140, 100:300, :3] = 220
    c2[500:540, 800:1000, :3] = 220
    s6 = ScreenSense(sb, ListSource([a, c1, a, c2, c2, c2, c2, c2]))
    s6._clock = lambda: clock[0]
    config.get().screen_settle_s = 0.5
    for i in range(8):
        clock[0] = i * 0.2
        s6.tick()
    assert len(settled) == 1, settled                       # three changes (appear, vanish, other region) -> one settled event, after 0.5 s of quiet
    assert settled[0]["changes"] == 3 and settled[0]["bbox"][0] <= 100 and settled[0]["bbox"][2] >= 1000 and settled[0]["bbox"][1] <= 100 and settled[0]["bbox"][3] >= 540
    assert s6.det.skips >= 4 and settled[0]["duplicate"] is False                                   # identical frames take the hash fast path; the first settle is never a duplicate
    # a toast comes and goes: the second settled event is the SAME screen as the first -> duplicate
    settled.clear()
    t_ = a.copy()
    t_[600:680, 900:1260, :3] = 230
    clock[0] = 0.0
    s7 = ScreenSense(sb, ListSource([a, t_, a, a, a, a, t_, a, a, a, a, a, a]))
    s7._clock = lambda: clock[0]
    for i in range(13):
        clock[0] = i * 0.2
        s7.tick()
    assert [x["duplicate"] for x in settled] == [False, True], settled
    # a slow brightness ramp below the threshold never fires, with or without the fast path
    ramp = [a.copy() for _ in range(6)]
    for i, f in enumerate(ramp):
        f[:, :, :3] = 40 + i // 3                                                                    # +1 level over 6 frames
    s8 = ScreenSense(sb, ListSource(ramp))
    assert all(s8.tick() is None for _ in ramp)
    class Boom:
        def grab(self): raise OSError("display lost")
    errs = []
    b.subscribe(lambda e: errs.append(e), kinds={"error"}, sources={"screen"})
    config.get().screen_interval_s = 0.05
    s4 = ScreenSense(b, Boom())
    s4.start()
    time.sleep(0.4)
    s4.stop()
    assert len(errs) == 1                                  # rate-limited, loop kept going
    config.get().screen_interval_s = 0.05
    s5 = ScreenSense(b, ListSource([a, toast]))
    s5.start()
    time.sleep(0.5)
    s5.stop()
    assert s5.stats()["ticks"] >= 5 and s5.stats()["changes"] == 1 and s5.stats()["overruns"] == 0   # fixed schedule
    config.reload()
    print("screen ok")


if __name__ == "__main__":
    _selfcheck()
