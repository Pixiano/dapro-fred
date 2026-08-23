# Core/scripts/enroll_headphones.py
#
# ONE-TIME setup for orchestrator/headphone_watch.py: captures 8
# reference photos of Vatsal — four wearing the headphones and four not
# — saved to HEADPHONES_ON_PATHS / HEADPHONES_OFF_PATHS. Same "run by
# hand whenever it needs (re)doing" convention as scripts/enroll_face.py
# — not wired into FRED's voice/turn flow.
#
# Raised from 2 shots per state to 4 (Vatsal's own call 2026-08-23),
# mainly for more glasses-combination coverage: a glasses-day frame
# compared only against a glasses-less reference (or vice versa) is a
# needless extra source of mismatch. headphone_watch.py's live check
# averages the histogram across every shot that exists per state, not
# just the first — more shots genuinely improve the comparison now.
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
    (HEADPHONES_ON_PATHS[2], 10, "Wearing the headphones, shot 3 — new angle/pose, glasses as you like."),
    (HEADPHONES_ON_PATHS[3], 10, "Wearing the headphones, shot 4 — swap glasses on/off from shot 3."),
    (HEADPHONES_OFF_PATHS[0], 10, "Take the headphones OFF now. Shot 5 (no headphones)."),
    (HEADPHONES_OFF_PATHS[1], 10, "No headphones, shot 6 — swap glasses on/off from shot 5."),
    (HEADPHONES_OFF_PATHS[2], 10, "No headphones, shot 7 — new angle/pose, glasses as you like."),
    (HEADPHONES_OFF_PATHS[3], 10, "No headphones, shot 8 — swap glasses on/off from shot 7."),
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
    "on3": HEADPHONES_ON_PATHS[2], "on4": HEADPHONES_ON_PATHS[3],
    "on5": HEADPHONES_ON_PATHS[4], "on6": HEADPHONES_ON_PATHS[5],
    "off1": HEADPHONES_OFF_PATHS[0], "off2": HEADPHONES_OFF_PATHS[1],
    "off3": HEADPHONES_OFF_PATHS[2], "off4": HEADPHONES_OFF_PATHS[3],
    "off5": HEADPHONES_OFF_PATHS[4], "off6": HEADPHONES_OFF_PATHS[5],
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

    print("Done — 8 reference photos saved.")


if __name__ == "__main__":
    main()
