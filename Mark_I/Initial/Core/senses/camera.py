"""Camera: one frame per request, device released immediately (snapshot, never a held feed).

Sockets
  in : Camera(bus, source=None).snap() -> HxWx3 uint8 RGB ndarray
  out: raises CameraFault when the device cannot open or the frame is dead after one retry. A dead frame is "black": 99th-percentile brightness under `camera_black_p99`
       or pixel std under `camera_flat_std` (a flat frame is the virtual "no signal" placeholder or a covered lens; a dim room still has noise and highlights).
       The retry re-opens the device after `camera_retry_s`. Events: camera.snap {ms, retried}, camera.fault {reason, retried}.
  Frames are never written to disk or put on the bus. `source` is anything with read_frame() -> BGR ndarray | None and close() (tests inject one).
Warm-up: webcams return dark or stale first frames, so `camera_warmup` frames are read and thrown away before the real one.
Camera index lives in settings (`camera_index`). Windows can renumber devices after a reboot or a new virtual camera; `python senses/camera.py --probe` prints
per-index shape / brightness / flatness (numbers only) so the right index can be confirmed without looking at the picture.
Self-check: python senses/camera.py (fake source: good, black-then-good, always-black, cannot-open); `--live` snaps the real camera once and prints numbers only.
"""
import sys
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev


class CameraFault(RuntimeError):
    pass


class CvSource:
    def __init__(self, index: int):
        import cv2
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap.release()
            raise CameraFault(f"camera {index} did not open")

    def read_frame(self):
        ok, f = self.cap.read()
        return f if ok else None

    def close(self):
        self.cap.release()


def is_dead(bgr: np.ndarray) -> bool:
    c = config.get()
    return float(np.percentile(bgr.max(axis=2), 99)) < c.camera_black_p99 or float(bgr.std()) < c.camera_flat_std


class Camera:
    def __init__(self, bus: ev.EventBus | None = None, source=None):
        self.bus = bus or ev.bus
        self._open = source if callable(source) else (lambda: source) if source else (lambda: CvSource(config.get().camera_index))

    def _grab(self):
        src = self._open()
        try:
            f = None
            for _ in range(config.get().camera_warmup):
                f = src.read_frame()
            return f
        finally:
            src.close()

    def snap(self) -> np.ndarray:
        t0, reason = time.monotonic(), ""
        for attempt in (0, 1):
            try:
                f = self._grab()
                reason = "no frame" if f is None else "black frame" if is_dead(f) else ""
            except CameraFault as ex:
                f, reason = None, str(ex)
            if not reason:
                self.bus.emit("camera", "snap", ms=int((time.monotonic() - t0) * 1000), retried=bool(attempt))
                return np.ascontiguousarray(f[:, :, ::-1])
            if attempt == 0:
                time.sleep(config.get().camera_retry_s)
        self.bus.emit("camera", "fault", reason=reason, retried=True)
        raise CameraFault(f"{reason} after one retry")


def probe(n: int = 4):
    import cv2
    for i in range(n):
        try:
            s = CvSource(i)
        except CameraFault:
            print(i, "closed")
            continue
        f = None
        for _ in range(config.get().camera_warmup):
            f = s.read_frame()
        s.close()
        if f is None:
            print(i, "no frame")
            continue
        print(i, f.shape[1], "x", f.shape[0], "mean", round(float(f.mean()), 1), "p99", int(np.percentile(f.max(axis=2), 99)), "std", round(float(f.std()), 1),
              "-> DEAD" if is_dead(f) else "-> ok")


def _selfcheck():
    config.get().camera_retry_s = 0.0
    bus = ev.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append((e.kind, dict(e.data))), sources={"camera"})
    rng = np.random.default_rng(1)
    good = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    black = np.zeros((48, 64, 3), dtype=np.uint8)
    flat = np.full((48, 64, 3), 9, dtype=np.uint8)              # the "no signal" placeholder: dim but flat
    dim = (rng.integers(0, 40, (48, 64, 3))).astype(np.uint8)
    dim[:6, :6] = 255                                           # a dark room with one lamp: noisy, has highlights -> alive

    class Src:
        def __init__(self, frames):
            self.frames, self.closed = list(frames), 0
        def read_frame(self):
            return self.frames.pop(0) if self.frames else None
        def close(self):
            self.closed += 1

    def cam(*attempts):
        srcs = [Src(a) for a in attempts]
        return Camera(bus, lambda: srcs.pop(0) if len(srcs) > 1 else srcs[0]), srcs

    w = config.get().camera_warmup
    c, s = cam([black] * (w - 1) + [good])                       # warm-up frames dark, real frame fine
    out = c.snap()
    assert out.shape == (48, 64, 3) and (out == good[:, :, ::-1]).all() and s[0].closed == 1 and seen[-1] == ("snap", {"ms": seen[-1][1]["ms"], "retried": False})
    c, s = cam([black] * w, [good] * w)                          # black, retry works
    c.snap()
    assert seen[-1][1]["retried"] is True and s[0].closed == 1
    assert Camera(bus, Src([dim] * w)).snap() is not None        # dim but alive is not a fault
    for bad in (black, flat):                                    # dead twice -> fault event + exception, never an image
        c, _ = cam([bad] * w, [bad] * w)
        try:
            c.snap()
            raise AssertionError("expected CameraFault")
        except CameraFault:
            pass
        assert seen[-1][0] == "fault" and seen[-1][1]["reason"] == "black frame"
    def cannot_open():
        raise CameraFault("camera 0 did not open")
    try:
        Camera(bus, cannot_open).snap()
        raise AssertionError("expected CameraFault")
    except CameraFault as ex:
        assert "did not open" in str(ex) and seen[-1][1]["reason"] == "camera 0 did not open"
    config.reload()
    print("camera ok")


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    elif "--live" in sys.argv:
        f = Camera().snap()
        print("live frame", f.shape, "mean", round(float(f.mean()), 1), "std", round(float(f.std()), 1))
    else:
        _selfcheck()
