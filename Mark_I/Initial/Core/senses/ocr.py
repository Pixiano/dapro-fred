"""Screen text (OCR) socket. OFF until Vatsal approves CPU use of Windows OCR (standing rule): `ocr_enabled` is False and read() returns None without running anything.

Sockets
  in : read(rgb: HxWx3 uint8 ndarray) -> str | None
  out: None = OCR is off (callers treat the screen text as unverified, see senses/describe.ground); a string = the recognised text of that crop.
       Enabling it before the engine exists raises OcrUnavailable, never a silent empty string (an empty string would read as "there is no text on screen").
The engine itself (Windows.Media.Ocr on the changed-region crop, ~50-150 ms CPU) is added when the approval lands; the interface and the off-switch are fixed here so
describe.py and the engine loop can be written against them now.
Self-check: python senses/ocr.py
"""
import sys
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


class OcrUnavailable(RuntimeError):
    pass


def read(rgb: np.ndarray) -> str | None:
    if not config.get().ocr_enabled:
        return None
    raise OcrUnavailable("ocr_enabled is on but no OCR engine is installed yet (Windows OCR awaits approval/implementation)")


def _selfcheck():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    assert read(img) is None                                 # off by default: nothing runs
    config.get().ocr_enabled = True
    try:
        read(img)
        raise AssertionError("expected OcrUnavailable")
    except OcrUnavailable:
        pass
    finally:
        config.reload()
    print("ocr ok")


if __name__ == "__main__":
    _selfcheck()
