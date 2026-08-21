# Core/scripts/enroll_face.py
#
# ONE-TIME setup for presence detection (see input/presence.py). Run this
# by hand whenever enrollment needs to be (re)done — not wired into FRED's
# voice/turn flow, matches tools/haismart_setup.py's own "run manually"
# convention for this kind of setup script.
#
# NOT CI-safe: needs a real camera at PRESENCE_CAMERA_INDEX and a real
# face in front of it. There's no automated test for this file — the only
# honest check is running it by hand and looking at what it printed and
# saved.
#
# Fully automatic per Vatsal's call 2026-08-21: no per-shot keypress, just
# a countdown between PRESENCE_ENROLLMENT_SHOTS captures.
#
# Multi-person tolerance (Vatsal's call): a frame with 2+ faces is NOT
# rejected — the largest face by bounding-box area (closest to camera,
# presumed to be the person enrolling) is picked, and its bbox is printed
# so a human watching the terminal can tell if it grabbed the wrong
# person and just rerun the script. No interactive picker — deliberately
# simple. Only a frame with 0 faces is rejected.

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import (
    DATA_DIR,
    PRESENCE_CAMERA_INDEX,
    PRESENCE_ENROLLMENT_INTERVAL_SECONDS,
    PRESENCE_ENROLLMENT_SHOTS,
)

EMBEDDINGS_PATH = DATA_DIR / "face_enrollment.json"

# Deliberate, narrow exception to "never persist raw frames" (see
# presence.py's module docstring for the general rule): the ambiguous-
# match vision-model fallback needs an actual reference photo to compare
# against, not just a numeric embedding. This is the one frame kept.
REFERENCE_PHOTO_PATH = DATA_DIR / "face_reference.jpg"


def _largest_face(faces):
    def area(f):
        x1, y1, x2, y2 = f.bbox
        return (x2 - x1) * (y2 - y1)
    return max(faces, key=area)


def _load_existing_embeddings() -> list:
    try:
        data = json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))
        return data["embeddings"]
    except (OSError, ValueError, KeyError):
        return []


def _append_embeddings(new_embeddings: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    embeddings = _load_existing_embeddings() + new_embeddings
    EMBEDDINGS_PATH.write_text(json.dumps({"embeddings": embeddings}), encoding="utf-8")
    return embeddings


def seed_from_photo(analyzer, photo_path: Path):
    """Seed enrollment from an existing, already-confirmed-clear photo
    instead of a live capture session — Vatsal's call 2026-08-21: a real
    reference photo (used tonight in the face-comparison tests, both via
    LM Studio and this repo's own vision_server.py) is available, so a
    live 5-shot session isn't strictly required before presence.py has
    something real to match against.

    Separate, standalone path from main()'s live-capture flow — does not
    touch or complicate it. The seed embedding is appended to whatever's
    already in face_enrollment.json (or starts it), same format as a
    live-captured shot; a live enroll_face.py run afterward just adds
    more embeddings under different lighting/angles, both coexist.

    Also seeds face_reference.jpg if it doesn't exist yet, using this
    same "one deliberate reference photo persisted" exception documented
    above (only if the reference photo isn't already there from an
    earlier session — never overwrites an existing one).
    """
    frame = cv2.imread(str(photo_path))
    if frame is None:
        print(f"Could not read image at {photo_path}")
        sys.exit(1)

    faces = analyzer.get(frame)
    if not faces:
        print(f"No face detected in {photo_path} — nothing seeded.")
        sys.exit(1)

    face = _largest_face(faces) if len(faces) > 1 else faces[0]
    if len(faces) > 1:
        print(f"{len(faces)} faces detected, picked largest at bbox={face.bbox.tolist()}")

    embeddings = _append_embeddings([face.normed_embedding.tolist()])
    print(f"Seeded 1 embedding from {photo_path} ({len(embeddings)} total now in {EMBEDDINGS_PATH})")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not REFERENCE_PHOTO_PATH.exists():
        cv2.imwrite(str(REFERENCE_PHOTO_PATH), frame)
        print(f"Seeded reference photo to {REFERENCE_PHOTO_PATH}")
    else:
        print(f"Reference photo already exists at {REFERENCE_PHOTO_PATH}, left unchanged.")


def _run_live_capture(analyzer):
    cap = cv2.VideoCapture(PRESENCE_CAMERA_INDEX)
    if not cap.isOpened():
        print(f"Could not open camera index {PRESENCE_CAMERA_INDEX}.")
        sys.exit(1)

    embeddings = []
    reference_frame = None

    try:
        for shot_num in range(1, PRESENCE_ENROLLMENT_SHOTS + 1):
            if shot_num > 1:
                print(f"Next shot in {PRESENCE_ENROLLMENT_INTERVAL_SECONDS}s...")
                time.sleep(PRESENCE_ENROLLMENT_INTERVAL_SECONDS)

            ok, frame = cap.read()
            if not ok:
                print(f"Shot {shot_num}: camera read failed, skipping.")
                continue

            faces = analyzer.get(frame)
            if not faces:
                print(f"Shot {shot_num}: no face detected, skipping.")
                continue

            face = _largest_face(faces) if len(faces) > 1 else faces[0]
            if len(faces) > 1:
                print(f"Shot {shot_num}: {len(faces)} faces detected, "
                      f"picked largest at bbox={face.bbox.tolist()} "
                      f"(rerun if this was the wrong person)")
            else:
                print(f"Shot {shot_num}: 1 face detected, bbox={face.bbox.tolist()}")

            embeddings.append(face.normed_embedding.tolist())
            if reference_frame is None:
                reference_frame = frame
    finally:
        cap.release()

    if not embeddings:
        print("No usable shots captured — nothing saved. Rerun the script.")
        sys.exit(1)

    # Appended, not overwritten: a --seed run and a live run can both
    # contribute embeddings to the same face_enrollment.json.
    all_embeddings = _append_embeddings(embeddings)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Same "never overwrite an existing reference photo" rule as
    # seed_from_photo() — this was previously asymmetric (this path
    # always overwrote, seeding never did), so seeding then later
    # running a live capture would silently replace the seeded photo.
    if not REFERENCE_PHOTO_PATH.exists():
        cv2.imwrite(str(REFERENCE_PHOTO_PATH), reference_frame)
        print(f"Saved reference photo to {REFERENCE_PHOTO_PATH}")
    else:
        print(f"Reference photo already exists at {REFERENCE_PHOTO_PATH}, left unchanged.")

    print(f"Saved {len(embeddings)}/{PRESENCE_ENROLLMENT_SHOTS} new embeddings "
          f"({len(all_embeddings)} total) to {EMBEDDINGS_PATH}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", metavar="PHOTO_PATH", help=(
        "Seed enrollment from an existing photo instead of a live 5-shot "
        "capture session (e.g. an already-confirmed-clear reference "
        "photo). Standalone path, separate from the live capture flow "
        "below — can be run before or after it, both contribute to the "
        "same face_enrollment.json."
    ))
    args = parser.parse_args()

    from insightface.app import FaceAnalysis

    print("Loading buffalo_l (CPU)...")
    analyzer = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    analyzer.prepare(ctx_id=0)

    if args.seed:
        seed_from_photo(analyzer, Path(args.seed))
        return

    _run_live_capture(analyzer)


if __name__ == "__main__":
    main()
