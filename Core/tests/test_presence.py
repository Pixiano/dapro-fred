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


def _write_entries(path, entries):
    path.write_text(_json.dumps({"embeddings": entries}), encoding="utf-8")


def _kinds(path):
    return [e["kind"] for e in _json.loads(path.read_text(encoding="utf-8"))["embeddings"]]


def test_accumulate_embedding_writes_dynamic_tier_only(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    _write_entries(enrollment_path, [])
    monkeypatch.setattr("scripts.enroll_face.EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(presence, "PRESENCE_DYNAMIC_EMBEDDINGS_CAP", 5)

    presence._accumulate_embedding(_FakeFace(0.1))
    presence._accumulate_embedding(_FakeFace(0.2))

    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))["embeddings"]
    assert [e["embedding"] for e in saved] == [[0.1, 0.1, 0.1], [0.2, 0.2, 0.2]]
    assert all(e["kind"] == "dynamic" for e in saved)


def test_accumulate_embedding_fifo_evicts_oldest_dynamic_only(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    _write_entries(enrollment_path, [
        {"embedding": [9.0, 9.0, 9.0], "kind": "base", "ts": None},
        {"embedding": [8.0, 8.0, 8.0], "kind": "hard", "ts": None},
        {"embedding": [0.1, 0.1, 0.1], "kind": "dynamic", "ts": None},
        {"embedding": [0.2, 0.2, 0.2], "kind": "dynamic", "ts": None},
    ])
    monkeypatch.setattr("scripts.enroll_face.EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(presence, "PRESENCE_DYNAMIC_EMBEDDINGS_CAP", 2)

    presence._accumulate_embedding(_FakeFace(0.3))  # cap already at 2 dynamic -> evict oldest (0.1)

    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))["embeddings"]
    # base/hard entries untouched, oldest dynamic (0.1) evicted, new one appended
    assert {"embedding": [9.0, 9.0, 9.0], "kind": "base", "ts": None} in saved
    assert {"embedding": [8.0, 8.0, 8.0], "kind": "hard", "ts": None} in saved
    dynamic = [e for e in saved if e["kind"] == "dynamic"]
    assert [e["embedding"] for e in dynamic] == [[0.2, 0.2, 0.2], [0.3, 0.3, 0.3]]


# =========================================================
# FLAT-FORMAT MIGRATION -- old face_enrollment.json was {"embeddings":
# [[...], [...]]}, no per-entry tagging. First load must migrate it to
# the tagged {"embedding", "kind", "ts"} format without dropping
# anything, splitting legacy entries by position: the first
# PRESENCE_BASE_EMBEDDINGS_TARGET are "base", the rest "dynamic"
# (Vatsal's 2026-08-22 call — nothing existing gets silently dropped).
# =========================================================

from scripts import enroll_face as _enroll_face


def test_migration_from_old_flat_format_splits_by_position(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    old_flat = [[float(i)] * 3 for i in range(5)]
    monkeypatch.setattr(_enroll_face, "EMBEDDINGS_PATH", enrollment_path)
    monkeypatch.setattr(_enroll_face, "PRESENCE_BASE_EMBEDDINGS_TARGET", 3)
    _write_entries(enrollment_path, old_flat)

    migrated = _enroll_face._load_existing_embeddings()

    assert [e["embedding"] for e in migrated] == old_flat  # nothing dropped
    assert [e["kind"] for e in migrated] == ["base", "base", "base", "dynamic", "dynamic"]

    # Write-back happened: re-reading the file directly shows tagged dicts now.
    on_disk = _json.loads(enrollment_path.read_text(encoding="utf-8"))["embeddings"]
    assert all(isinstance(e, dict) for e in on_disk)


def test_migration_is_a_noop_on_already_tagged_format(tmp_path, monkeypatch):
    enrollment_path = tmp_path / "face_enrollment.json"
    tagged = [{"embedding": [1.0, 1.0, 1.0], "kind": "hard", "ts": "2026-08-22T00:00:00"}]
    monkeypatch.setattr(_enroll_face, "EMBEDDINGS_PATH", enrollment_path)
    _write_entries(enrollment_path, tagged)

    migrated = _enroll_face._load_existing_embeddings()

    assert migrated == tagged


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
    monkeypatch.setattr(presence, "PRESENCE_DYNAMIC_EMBEDDINGS_CAP", 50)
    monkeypatch.setattr(presence, "PRESENCE_PRESENT_DEBOUNCE", 2)
    monkeypatch.setattr(presence, "_present_streak", 0)
    monkeypatch.setattr(presence, "_state_cache", {"present": False, "last_seen": None, "last_checked": None})
    monkeypatch.setattr(presence, "_save_state", lambda state: None)
    monkeypatch.setattr(presence.presence_log, "log_poll", lambda present, matched_tier=None: None)

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
        presence, "_frame_matches_enrollment", lambda frame: (True, face, "dynamic")
    )

    presence.poll_once()  # 1st consecutive match: below debounce, no accumulate
    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert saved["embeddings"] == []

    presence.poll_once()  # 2nd consecutive match: clears debounce, accumulates
    saved = _json.loads(enrollment_path.read_text(encoding="utf-8"))
    assert [e["embedding"] for e in saved["embeddings"]] == [[0.7, 0.7, 0.7]]
    assert saved["embeddings"][0]["kind"] == "dynamic"


# =========================================================
# FAMILY CLASSIFICATION -- last_classification() accessor. Mock a
# multi-face/single-face frame and assert family matching never affects
# is_present()'s own Vatsal-only result (_frame_matches_enrollment's
# return value here is that same Vatsal-only present bool).
# =========================================================

def test_family_classification_does_not_affect_vatsal_match(monkeypatch):
    stranger_face = _FakeFace(-0.5)  # matches neither Vatsal nor any family member

    monkeypatch.setattr(
        presence, "_get_analyzer",
        lambda: _types.SimpleNamespace(get=lambda frame: [stranger_face]),
    )
    monkeypatch.setattr(
        presence, "_get_enrollment_embeddings",
        lambda: {"base": [], "hard": [], "dynamic": []},
    )
    monkeypatch.setattr(presence, "_get_family_embeddings", lambda: {})
    monkeypatch.setattr(presence, "_state_cache", {"present": False, "last_seen": None, "last_checked": None})
    # Every non-HIGH match now goes to the vision fallback (2026-08-23,
    # see _frame_matches_enrollment's own comment) — stub it rather than
    # exercising the real HTTP call against a fake "frame" string.
    monkeypatch.setattr(presence, "_vision_fallback_is_match", lambda frame: False)

    present, matched_face, matched_tier = presence._frame_matches_enrollment("frame")

    assert present is False  # Vatsal-only matching untouched, no match at all
    classification = presence.last_classification()
    assert classification["known_people"] == []
    assert classification["unrecognized"] is True


def test_family_classification_recognizes_family_member(monkeypatch):
    mom_face = _FakeFace(0.9)

    monkeypatch.setattr(
        presence, "_get_analyzer",
        lambda: _types.SimpleNamespace(get=lambda frame: [mom_face]),
    )
    monkeypatch.setattr(
        presence, "_get_enrollment_embeddings",
        lambda: {"base": [], "hard": [], "dynamic": []},
    )
    monkeypatch.setattr(presence, "_get_family_embeddings", lambda: {"Mom": [_np.array([0.9, 0.9, 0.9])]})
    monkeypatch.setattr(presence, "_state_cache", {"present": False, "last_seen": None, "last_checked": None})
    # Same reason as the stranger-classification test above.
    monkeypatch.setattr(presence, "_vision_fallback_is_match", lambda frame: False)

    present, _, _ = presence._frame_matches_enrollment("frame")

    assert present is False  # this face is Mom, not Vatsal -> is_present() stays False
    classification = presence.last_classification()
    assert classification["known_people"] == ["Mom"]
    assert classification["unrecognized"] is False


def test_family_classification_excludes_vatsals_own_face(monkeypatch):
    """A frame containing ONLY Vatsal (who is never in family_enrollment.json)
    must not report as unrecognized -- his own face is excluded from
    family classification before the known/unknown check runs."""
    vatsal_face = _FakeFace(0.9)

    monkeypatch.setattr(
        presence, "_get_analyzer",
        lambda: _types.SimpleNamespace(get=lambda frame: [vatsal_face]),
    )
    monkeypatch.setattr(
        presence, "_get_enrollment_embeddings",
        lambda: {"base": [_np.array([0.9, 0.9, 0.9])], "hard": [], "dynamic": []},
    )
    monkeypatch.setattr(presence, "_get_family_embeddings", lambda: {})
    monkeypatch.setattr(presence, "_state_cache", {"present": False, "last_seen": None, "last_checked": None})

    present, _, _ = presence._frame_matches_enrollment("frame")

    assert present is True  # real Vatsal match
    classification = presence.last_classification()
    assert classification["known_people"] == []
    assert classification["unrecognized"] is False  # excluded, not a stranger
