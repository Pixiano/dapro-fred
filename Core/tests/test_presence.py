# Core/tests/test_presence.py
#
# NOT CI-safe: needs a real camera at PRESENCE_CAMERA_INDEX and a real
# enrolled face (run scripts/enroll_face.py first). Guarded behind
# __main__ rather than plain test_*() functions — this needs input()
# for the "step away" pause, which would hang pytest collection if it
# ran as module-level code. Run by hand: python tests/test_presence.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from input import presence


def main():
    presence.poll_once()
    assert presence.is_present() is True, "sit in frame, run again"
    print("present: OK,", presence.last_seen())

    input("step away from the camera, then press enter")
    presence.poll_once()
    assert presence.is_present() is False, "still detected present"
    print("absent: OK")

    assert not any(Path("Core/data").glob("*presence*frame*")), \
        "a raw frame was persisted, should never happen"
    print("no persisted frames: OK")


if __name__ == "__main__":
    main()


# =========================================================
# ONGOING EMBEDDING ACCUMULATION -- pytest-style, no camera or model
# needed: _accumulate_embedding() is pure JSON read/append + a cap
# check, so unlike main() above this IS a real automated test.
# =========================================================

import json as _json

import numpy as _np


class _FakeFace:
    def __init__(self, value):
        self.normed_embedding = _np.array([value, value, value])


def test_accumulate_embedding_appends_and_stops_at_cap(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    monkeypatch.setattr("scripts.enroll_face.EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(presence, "PRESENCE_MAX_EMBEDDINGS", 2)

    presence._accumulate_embedding(_FakeFace(0.1))
    presence._accumulate_embedding(_FakeFace(0.2))
    presence._accumulate_embedding(_FakeFace(0.3))  # cap already hit, must not append

    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert saved["embeddings"] == [[0.1, 0.1, 0.1], [0.2, 0.2, 0.2]]


def test_accumulate_embedding_noop_when_already_at_cap(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    enrollment_path.write_text(
        _json.dumps({"embeddings": [[0.9, 0.9, 0.9]]}), encoding="utf-8"
    )
    monkeypatch.setattr("scripts.enroll_face.EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(presence, "PRESENCE_MAX_EMBEDDINGS", 1)

    presence._accumulate_embedding(_FakeFace(0.5))

    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert saved["embeddings"] == [[0.9, 0.9, 0.9]]  # unchanged
