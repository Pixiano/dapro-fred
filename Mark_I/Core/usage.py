"""Usage data: one central place that keeps what really happened, by component and by year / month / day, for later tuning and training.

Layout (root = Project_FRED/Data in live runs; git-ignored, it holds real voice and speech):
  <root>/<component>/<YYYY>/<MM>/<DD>.jsonl        every bus event of that component (component = the event's source: audio, clock, stt, engine, tts, ...)
  <root>/<component>/<YYYY>/<MM>/<DD>/<HHMMSS_mmm>.wav   audio clips, with a {"kind": "clip", ...} line in the day's jsonl
Clips: wakeword (the last ~2 s of mic on every wake and near-miss), clock (every ignored utterance, with the reason), stt (every final utterance with its text).

Sockets
  in : UsageLog(bus, mic=None).start()/stop()    subscribes to the bus: every event -> its component's day file (screen.change is skipped: 700 a minute, only a box)
       save_clip(component, audio, **meta)       one wav + one jsonl line; returns the path, or None past `usage_clip_cap` clips for that component and day
       use_repo_data()                           point the root at Project_FRED/Data (live.py does this; the default root is <data_dir>/usage so tests never touch the repo)
  out: files only. Nothing here raises into Mark I: a full disk or a locked file costs the data, never the run.
Self-check: python usage.py
"""
import json
import os
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

import config

REPO_DATA = Path(__file__).resolve().parents[2] / "Data"
SKIP = {("screen", "change")}
_root, _lock, _counts = None, threading.Lock(), {}


def use_repo_data():
    global _root
    _root = Path(os.environ.get("FRED_USAGE") or REPO_DATA)


def root() -> Path:
    return _root or Path(os.environ.get("FRED_USAGE") or config.data_dir() / "usage")


def _day(component: str, ts: float | None = None) -> Path:
    t = datetime.fromtimestamp(ts) if ts else datetime.now()
    return root() / component / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}"


def append(component: str, record: dict, ts: float | None = None):
    try:
        day = _day(component, ts)
        with _lock:
            day.parent.mkdir(parents=True, exist_ok=True)
            with open(day.with_suffix(".jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as ex:
        print(f"[usage] not saved: {type(ex).__name__}")


def save_clip(component: str, audio, **meta):
    try:
        x = np.asarray(audio)
        if x.dtype != np.int16:
            x = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
        if not len(x):
            return None
        now = datetime.now()
        day = _day(component)
        with _lock:
            day.mkdir(parents=True, exist_ok=True)
            n = _counts.get(day)
            if n is None:
                n = len(list(day.glob("*.wav")))
            if n >= config.get().usage_clip_cap:
                return None
            _counts[day] = n + 1
            f = day / f"{now:%H%M%S}_{now.microsecond // 1000:03d}.wav"
            with wave.open(str(f), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(config.get().sample_rate)
                w.writeframes(x.tobytes())
        append(component, {"kind": "clip", "ts": now.timestamp(), "file": f.name, "audio_s": round(len(x) / config.get().sample_rate, 2),
                           "peak": round(float(np.max(np.abs(x))) / 32767, 3), **meta})
        return f
    except Exception as ex:
        print(f"[usage] clip not saved: {type(ex).__name__}")
        return None


class UsageLog:
    def __init__(self, bus, mic=None):
        self.bus, self.mic, self._unsub = bus, mic, None

    def start(self):
        self._unsub = self.bus.subscribe(self._on)

    def stop(self):
        self._unsub and self._unsub()

    def _on(self, e):
        if (e.source, e.kind) in SKIP:
            return
        append(e.source, {"ts": e.ts, "kind": e.kind, **e.data}, e.ts)
        if e.source == "audio" and e.kind in ("wake", "wake_near") and self.mic is not None:
            save_clip("wakeword", self.mic.ring(config.get().usage_wake_clip_s), event=e.kind, **{k: v for k, v in e.data.items() if isinstance(v, (int, float))})


def _selfcheck():
    import tempfile
    import time
    import events as ev
    tmp = tempfile.TemporaryDirectory()
    config.set_data_dir(tmp.name)
    bus = ev.EventBus()

    class M:
        def ring(self, s):
            return np.full(int(16000 * s), 1000, np.int16)
    u = UsageLog(bus, M())
    u.start()
    bus.emit("audio", "wake", score=0.9)
    bus.emit("audio", "wake_near", score=0.3)
    bus.emit("screen", "change", score=1.0)
    bus.emit("clock", "state", state="IGNORE", reason="not_vatsal")
    t = datetime.now()
    d = Path(tmp.name) / "usage"
    assert (d / "audio" / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}.jsonl").exists() and (d / "clock" / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}.jsonl").exists()
    assert not (d / "screen").exists()                                            # the skipped event
    assert len(list((d / "wakeword").rglob("*.wav"))) == 2
    rows = [json.loads(l) for l in (d / "wakeword" / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["wake", "wake_near"] and rows[0]["score"] == 0.9 and rows[0]["audio_s"] == config.get().usage_wake_clip_s
    config.get().usage_clip_cap = 2
    assert save_clip("clock", np.ones(1600, np.int16)) and save_clip("clock", np.ones(1600, np.int16)) is not None and save_clip("clock", np.ones(1600, np.int16)) is None   # per component and day
    config.get().usage_clip_cap = 400
    u.stop()
    print("usage ok")


if __name__ == "__main__":
    _selfcheck()
