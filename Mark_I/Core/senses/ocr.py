"""Screen text (OCR): Windows.Media.Ocr on the changed-region crop. CPU only, ~40-50 ms for a full dense 1280x720 frame. Approved by Vatsal 2026-10-06.

Sockets
  in : read(rgb: HxWx3 uint8 RGB ndarray) -> str | None
  out: the recognised text, lines joined with newlines; "" when the crop has no text (or is too small to read: under 40 px a side);
       None only when OCR is switched off (settings.ocr_enabled False): callers then treat screen text as unverified (senses/describe.ground).
       OcrUnavailable when it is on but cannot run (no engine for the user's languages, winrt not installed): never a silent "".
Language: the engine is built from the user's profile languages (English here; English is the default medium). The engine is created once and reused.
Runs on the calling thread (asyncio.run per call, ~ms of overhead); no GPU, so it does not go through gpu.run.
Dependencies (Core venv): winrt-runtime, winrt-Windows.Media.Ocr, .Graphics.Imaging, .Storage.Streams, .Globalization, .Foundation(.Collections).
Self-check: python senses/ocr.py (renders text with OpenCV and reads it back through the real engine; needs Windows)
"""
import asyncio
import sys
import threading
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

MIN_SIDE = 40   # the engine rejects tiny images


class OcrUnavailable(RuntimeError):
    pass


_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            try:
                from winrt.windows.media.ocr import OcrEngine
            except ImportError as ex:
                raise OcrUnavailable(f"winrt OCR packages not installed: {ex}")
            _engine = OcrEngine.try_create_from_user_profile_languages()
            if _engine is None:
                raise OcrUnavailable("no Windows OCR language pack for the user's profile languages")
        return _engine


async def _recognize(rgb: np.ndarray) -> str:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    h, w = rgb.shape[:2]
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0], bgra[:, :, 1], bgra[:, :, 2], bgra[:, :, 3] = rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0], 255
    wr = DataWriter()
    wr.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(wr.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = await _get_engine().recognize_async(bmp)
    return "\n".join(line.text for line in res.lines)


def read(rgb: np.ndarray) -> str | None:
    if not config.get().ocr_enabled:
        return None
    rgb = np.ascontiguousarray(rgb[:, :, :3])
    h, w = rgb.shape[:2]
    if min(h, w) < MIN_SIDE:
        return ""
    try:
        return asyncio.run(_recognize(rgb))
    except OcrUnavailable:
        raise
    except Exception as ex:
        raise OcrUnavailable(f"OCR failed: {ex!r}")


def _selfcheck():
    import cv2
    img = np.full((120, 560, 3), 255, dtype=np.uint8)
    cv2.putText(img, "Build failed: 3 tests", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, "Exit code 503 at 14:32", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
    t = read(img)
    assert t is not None and "Build failed" in t and "503" in t and "14:32" in t, t
    assert read(np.full((120, 560, 3), 255, dtype=np.uint8)) == ""             # a blank crop is "no text", not None and not an error
    assert read(np.zeros((20, 500, 3), dtype=np.uint8)) == ""                  # too small to read
    config.get().ocr_enabled = False
    try:
        assert read(img) is None                                               # off: nothing runs
    finally:
        config.reload()
    print("ocr ok")


if __name__ == "__main__":
    _selfcheck()
