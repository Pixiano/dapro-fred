"""Presence: is someone at the desk? One camera snapshot every `presence_poll_s`, a face counted, a debounced present/absent state.

Sockets
  in : Presence(bus, camera, detector=None).poll() -> {"faces": int} | None  (None = camera fault, state unchanged);  start() / stop() run poll() on a fixed schedule
  out: bus event presence.state {present, faces, since_s} only when the state CHANGES, after debounce: present as soon as a face is seen (`presence_present_polls`, 1),
       absent once no face has been seen for `presence_absent_s` (45 s; noticed at the first poll after that, so up to one poll period later), so looking away or a missed detection does not flip it.
       presence.poll {faces} on every successful poll (log noise stays low: the EventLog keeps it as a number only).  is_present() -> bool | None (None before the first poll).
  A camera fault (senses/camera.py raises CameraFault, which also emits camera.fault) changes nothing by itself: the state is held, and the 45 s clock keeps running, so the first fault-free poll with no face after 45 s of no faces says absent.
  Detector: `detector(rgb) -> int` faces. Default is the insightface SCRFD detection model only (buffalo_l's det_10g, ~17 MB) on CPU: Vatsal approved CPU for this
  small detector 2026-10-06 (onnxruntime here has no CUDA provider). No recognition, no embeddings: this module never identifies anyone and stores nothing; frames
  are dropped after counting. Identity matching is a separate, later socket.
  Because it holds no GPU, it does not go through gpu.run, which removes the old "CUDA operation not permitted when stream is capturing" collisions by construction.
Self-check: python senses/presence.py (fake camera + detector); `--live` takes one real snapshot and prints the face count only; `--watch` prints the count every ~1 s for 40 s (try turning away, looking down).
"""
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from senses.camera import Camera, CameraFault


def _insightface_detector():
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"], providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return lambda rgb: len(app.get(rgb[:, :, ::-1]))          # insightface wants BGR


class Presence:
    def __init__(self, bus: ev.EventBus | None = None, camera: Camera | None = None, detector=None):
        self.bus = bus or ev.bus
        self.camera = camera or Camera(self.bus)
        self._detector, self._lazy = detector, detector is None
        self._present: bool | None = None
        self._seen = 0
        self._clock = time.monotonic          # injectable for tests
        self._last_face = self._clock()
        self._since = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def is_present(self) -> bool | None:
        return self._present

    def poll(self):
        try:
            rgb = self.camera.snap()
        except CameraFault:
            return None
        if self._lazy and self._detector is None:
            self._detector = _insightface_detector()
        n = int(self._detector(rgb))
        del rgb
        self.bus.emit("presence", "poll", faces=n)
        c = config.get()
        now = self._clock()
        self._seen = self._seen + 1 if n else 0
        if n:
            self._last_face = now
        new = self._present
        if n and self._seen >= c.presence_present_polls:
            new = True
        elif not n and now - self._last_face >= c.presence_absent_s:
            new = False
        if new != self._present:
            self.bus.emit("presence", "state", present=new, faces=n, since_s=int(now - self._since) if self._present is not None else 0)
            self._present, self._since = new, now
        return {"faces": n}

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="presence")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(10)

    def _loop(self):
        nxt = time.monotonic()
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as ex:                       # detector crash must not kill the loop
                self.bus.emit("presence", "error", error=repr(ex))
            nxt += config.get().presence_poll_s
            self._stop.wait(max(0.0, nxt - time.monotonic()))


def _selfcheck():
    import numpy as np
    config.get().presence_absent_s, config.get().presence_present_polls = 45.0, 1
    clock = [0.0]
    bus = ev.EventBus()
    states = []
    bus.subscribe(lambda e: states.append(e.data["present"]), kinds={"state"}, sources={"presence"})
    frame = np.full((8, 8, 3), 100, dtype=np.uint8)
    faces = iter([0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 2, 0])
    fault = {"on": False}

    class Cam:
        def snap(self):
            if fault["on"]:
                raise CameraFault("black frame after one retry")
            return frame

    p = Presence(bus, Cam(), lambda rgb: next(faces))
    p._clock = lambda: clock[0]
    p._last_face = 0.0
    _poll = p.poll
    p.poll = lambda: (clock.__setitem__(0, clock[0] + 10.0), _poll())[1]     # polls every 10 s
    assert p.is_present() is None
    p.poll(); p.poll()
    assert states == [] and p.is_present() is None             # 20 s of empty polls at startup: not enough to claim absence
    p.poll()
    assert states == [True]                                    # first face -> present immediately
    p.poll(); p.poll()
    assert states == [True] and p.is_present() is True         # two misses (20 s): still present
    p.poll()
    assert p.is_present() is True                              # a face resets the 45 s clock
    p.poll(); p.poll()
    fault["on"] = True
    assert p.poll() is None and p.is_present() is True         # camera fault: state held (clock still runs: 30 s without a face)
    fault["on"] = False
    p.poll()
    assert states == [True] and p.is_present() is True         # 40 s since the last face: under 45 s
    p.poll()
    assert states == [True, False] and p.is_present() is False  # 50 s -> absent, noticed at the first poll past 45 s
    p.poll()
    assert states == [True, False, True]                       # returns on the first face (two faces count as present too)
    config.reload()
    print("presence ok")


def watch(seconds=40):
    """Hand test: faces per second (numbers only), for turning away / looking down in front of the camera."""
    cam, det = Camera(), _insightface_detector()
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        t = time.monotonic()
        try:
            print(f"{seconds - (t_end - t):5.1f}s faces={det(cam.snap())}", flush=True)
        except CameraFault as ex:
            print("fault", ex, flush=True)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    elif "--live" in sys.argv:
        t = time.time()
        r = Presence().poll()
        print("live poll:", r, f"{time.time() - t:.1f}s incl. model load")
    else:
        _selfcheck()
