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

from config.settings import (
    HEADPHONES_OFF_PATHS,
    HEADPHONES_ON_PATHS,
    HEADPHONES_TRAINING_OFF_DIR,
    HEADPHONES_TRAINING_ON_DIR,
)
from input.presence import resolve_camera_index

_TRAIN_DIRS = {"on": HEADPHONES_TRAINING_ON_DIR, "off": HEADPHONES_TRAINING_OFF_DIR}

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


def _next_training_path(state: str) -> Path:
    """First non-existent NNN.jpg in the training/{on,off} folder —
    lets --train-shot be called repeatedly (e.g. once per turn while
    narrating a 30-50 shot session) without tracking a counter across
    calls; each invocation is a fresh process anyway."""
    d = _TRAIN_DIRS[state]
    d.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in d.glob("*.jpg")}
    n = 1
    while f"{n:03d}" in existing:
        n += 1
    return d / f"{n:03d}.jpg"


def passive_capture(state: str, duration: float, interval: float = 1.0, progress_every: int = 50):
    """One shot every `interval` seconds for `duration` seconds, into
    data/headphones_training/{on,off} — Vatsal's own call 2026-08-23:
    natural, unposed variation (however he's actually sitting, however
    the headphones actually happen to sit that minute) beats another
    batch of consciously-posed shots. Driven by wall-clock time rather
    than a fixed target count (Vatsal's own call 2026-08-24: "as many
    as plausible", not a preset number) — interval=1.0 keeps
    consecutive frames different enough to be worth keeping (frames a
    fraction of a second apart are near-duplicates) while still landing
    in the hundreds over a 5-minute window, not the "1 million" a
    sub-second interval would produce.

    Prints progress every `progress_every` shots (a session that can
    now run into the hundreds needs some sign of life) plus a final
    summary — still no per-shot prints. A failed individual shot
    (camera hiccup mid-session) is skipped, not fatal; the run keeps
    going so one bad frame doesn't lose the rest of a window that can't
    easily be redone identically.
    """
    print(f"Passive capture starting: '{state}' shots every {interval:.1f}s for {duration:.0f}s "
          f"(progress every {progress_every}).")
    start = time.time()
    saved = 0
    while time.time() - start < duration:
        frame = _capture(resolve_camera_index())
        if frame is not None:
            path = _next_training_path(state)
            cv2.imwrite(str(path), frame)
            saved += 1
            if saved % progress_every == 0:
                print(f"{saved} '{state}' shots saved so far...")
        time.sleep(interval)
    print(f"Passive capture done: {saved} '{state}' shots saved.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", choices=sorted(_SLOT_PATHS), help="capture just one named shot")
    parser.add_argument(
        "--train-shot", choices=sorted(_TRAIN_DIRS),
        help="capture one photo into data/headphones_training/{on,off}, auto-numbered — "
             "for scripts/train_headphones_classifier.py's 30-50-photo pool, not the "
             "6-shot vision-LLM reference set --shot targets",
    )
    parser.add_argument(
        "--passive", choices=sorted(_TRAIN_DIRS),
        help="unattended: one shot every --interval seconds for --duration seconds while you "
             "just sit there naturally, instead of posing for each one",
    )
    parser.add_argument("--duration", type=float, default=300, help="passive-capture window, seconds")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between passive-capture shots")
    parser.add_argument("--delay", type=float, default=0, help="seconds to wait before this shot")
    args = parser.parse_args()

    if args.passive:
        passive_capture(args.passive, args.duration, args.interval)
        sys.exit(0)

    if args.train_shot:
        ok = capture_one(_next_training_path(args.train_shot), args.delay)
        sys.exit(0 if ok else 1)

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
