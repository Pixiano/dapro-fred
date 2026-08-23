# Core/scripts/enroll_headphones.py
#
# ONE-TIME setup for orchestrator/headphone_watch.py: captures a single
# reference photo of Vatsal wearing his headphones, saved to
# HEADPHONES_REFERENCE_PATH. Same "run by hand whenever it needs
# (re)doing" convention as scripts/enroll_face.py — not wired into
# FRED's voice/turn flow.
#
# Deliberately just one photo, no embeddings, no face detection: this
# isn't identity matching (that's presence.py's job), it's a single
# reference image the local vision model compares the live frame
# against — the same two-image-compare mechanic
# input/presence.py's own _vision_fallback_is_match already uses, just
# asking a different question ("wearing the same headphones?" instead
# of "same person?").
#
# NOT CI-safe: needs a real camera and Vatsal actually wearing the
# headphones when it fires. No automated test — the only honest check
# is running it by hand and looking at the saved photo.

import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import HEADPHONES_REFERENCE_PATH
from input.presence import resolve_camera_index

_COUNTDOWN_SECONDS = 5


def main():
    print(f"Put your headphones on. Capturing in {_COUNTDOWN_SECONDS}s...")
    time.sleep(_COUNTDOWN_SECONDS)

    index = resolve_camera_index()
    cap = cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            print(f"Could not open camera index {index}.")
            sys.exit(1)
        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok:
        print("Camera opened but couldn't read a frame.")
        sys.exit(1)

    HEADPHONES_REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(HEADPHONES_REFERENCE_PATH), frame)
    print(f"Saved reference photo to {HEADPHONES_REFERENCE_PATH}")


if __name__ == "__main__":
    main()
