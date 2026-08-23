# Core/scripts/enroll_headphones.py
#
# ONE-TIME setup for orchestrator/headphone_watch.py: captures 4
# reference photos of Vatsal — two wearing the headphones (one with
# glasses, one without) and two not (same), saved to
# HEADPHONES_ON_PATHS / HEADPHONES_OFF_PATHS. Same "run by hand
# whenever it needs (re)doing" convention as scripts/enroll_face.py —
# not wired into FRED's voice/turn flow.
#
# The glasses variation is Vatsal's own call 2026-08-23: a glasses-day
# frame compared only against a glasses-less reference (or vice versa)
# is a needless extra source of mismatch, so one reference of each
# combination goes on disk. headphone_watch.py's live check only reads
# the first of each pair; the second is a spare, not currently read by
# anything.
#
# NOT CI-safe: needs a real camera and Vatsal actually present, midway
# switching the headphones on/off on cue. No automated test — the only
# honest check is running it by hand and looking at the saved photos.

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import HEADPHONES_OFF_PATHS, HEADPHONES_ON_PATHS
from input.presence import resolve_camera_index

# (path, seconds-of-countdown-before-this-shot, prompt).
_SHOTS = [
    (HEADPHONES_ON_PATHS[0], 10, "Wearing the headphones, shot 1."),
    (HEADPHONES_ON_PATHS[1], 10, "Wearing the headphones, shot 2 — swap glasses on/off from shot 1."),
    (HEADPHONES_OFF_PATHS[0], 10, "Take the headphones OFF now. Shot 3 (no headphones)."),
    (HEADPHONES_OFF_PATHS[1], 10, "No headphones, shot 4 — swap glasses on/off from shot 3."),
]


def _capture(index: int):
    cap = cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
    finally:
        cap.release()
    return frame if ok else None


_SLOT_PATHS = {
    "on1": HEADPHONES_ON_PATHS[0], "on2": HEADPHONES_ON_PATHS[1],
    "off1": HEADPHONES_OFF_PATHS[0], "off2": HEADPHONES_OFF_PATHS[1],
}


def capture_one(path: Path, delay: float = 0) -> bool:
    """One shot, no countdown narration of its own — the caller (a
    human typing commands in real time, or a driving script/agent) is
    responsible for telling the person when to change pose, and simply
    waits `delay` seconds before the shutter fires. Confirmed live
    2026-08-23: a script's own printed countdown isn't something anyone
    reliably watches mid-motion, so narration moved out of this file
    entirely — this is deliberately just the camera mechanics."""
    if delay:
        time.sleep(delay)
    frame = _capture(resolve_camera_index())
    if frame is None:
        print(f"Could not read a frame for {path.name} — camera open/read failed.")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), frame)
    print(f"saved {path.name}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", choices=sorted(_SLOT_PATHS), help="capture just one named shot")
    parser.add_argument("--delay", type=float, default=0, help="seconds to wait before this shot")
    args = parser.parse_args()

    if args.shot:
        ok = capture_one(_SLOT_PATHS[args.shot], args.delay)
        sys.exit(0 if ok else 1)

    # No --shot: the full unattended 4-shot sequence, prompts printed
    # only (no beeps — see capture_one's docstring for why that's not
    # relied on for timing anymore). Fine for a re-run where the person
    # already knows the sequence; first-time walkthroughs should be
    # driven shot-by-shot with --shot instead.
    for path, countdown, prompt in _SHOTS:
        print(f"{prompt} Capturing in {countdown}s...")
        if not capture_one(path, countdown):
            sys.exit(1)

    print("Done — 4 reference photos saved.")


if __name__ == "__main__":
    main()
