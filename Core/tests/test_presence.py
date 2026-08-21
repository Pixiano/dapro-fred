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
import types as _types

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


# =========================================================
# PRESENT-DEBOUNCE GATES ACCUMULATION -- a confirmed match on a frame
# that hasn't cleared PRESENCE_PRESENT_DEBOUNCE consecutive present
# polls yet must not be written into the enrollment set, same
# false-positive window sleep_mode.py's own present-debounce closes for
# the greeting/sleep-exit path. Pure in-memory streak logic, no camera
# or model needed -- poll_once()'s camera/matching internals are
# monkeypatched out.
# =========================================================

def test_poll_once_does_not_accumulate_until_present_debounce_clears(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    enrollment_path.write_text(_json.dumps({"embeddings": []}), encoding="utf-8")
    monkeypatch.setattr("scripts.enroll_face.EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(presence, "PRESENCE_MAX_EMBEDDINGS", 50)
    monkeypatch.setattr(presence, "PRESENCE_PRESENT_DEBOUNCE", 2)
    monkeypatch.setattr(presence, "_present_streak", 0)
    monkeypatch.setattr(presence, "_state_cache", {"present": False, "last_seen": None, "last_checked": None})
    monkeypatch.setattr(presence, "_save_state", lambda state: None)
    monkeypatch.setattr(presence.presence_log, "log_poll", lambda present: None)

    face = _FakeFace(0.7)
    monkeypatch.setattr(
        "input.presence._get_analyzer",
        lambda: _types.SimpleNamespace(get=lambda frame: [face]),
    )

    class _FakeCap:
        def isOpened(self):
            return True

        def read(self):
            return True, "frame"

        def release(self):
            pass

    monkeypatch.setattr(presence.cv2, "VideoCapture", lambda index: _FakeCap())
    monkeypatch.setattr(
        presence, "_frame_matches_enrollment", lambda frame: (True, face)
    )

    presence.poll_once()  # 1st consecutive match: below debounce, no accumulate
    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert saved["embeddings"] == []

    presence.poll_once()  # 2nd consecutive match: clears debounce, accumulates
    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert saved["embeddings"] == [[0.7, 0.7, 0.7]]
