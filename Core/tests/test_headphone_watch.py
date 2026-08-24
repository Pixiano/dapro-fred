# Core/tests/test_headphone_watch.py
#
# Pure logic test for headphone_watch.py's confirmation-prompt reply
# handling (handle_confirmation_reply) — no camera, no classifier, no
# real training photos. Everything else in this module needs a real
# camera/insightface/trained model to exercise meaningfully and is
# validated live, same convention as headphone_features.py's own
# __main__ self-check.

import numpy as np

from orchestrator import headphone_watch as hw


def _reset():
    hw._pending_confirmation = None


def test_no_op_when_nothing_pending():
    _reset()
    assert hw.handle_confirmation_reply("yes") is None


def test_yes_saves_frame_and_clears_pending(monkeypatch, tmp_path):
    _reset()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    hw._pending_confirmation = {"frame": frame, "label": True}

    saved_path = tmp_path / "001.jpg"
    monkeypatch.setattr(
        "scripts.enroll_headphones._next_training_path", lambda state: saved_path
    )

    reply = hw.handle_confirmation_reply("yes")
    assert reply == "Saved, thanks."
    assert saved_path.exists()
    assert hw._pending_confirmation is None


def test_no_discards_without_saving():
    _reset()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    hw._pending_confirmation = {"frame": frame, "label": False}

    reply = hw.handle_confirmation_reply("no")
    assert reply == "Noted, won't use that one."
    assert hw._pending_confirmation is None


def test_unclear_reply_clears_pending_and_falls_through():
    _reset()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    hw._pending_confirmation = {"frame": frame, "label": True}

    reply = hw.handle_confirmation_reply("what's the weather")
    assert reply is None
    assert hw._pending_confirmation is None  # doesn't stay open indefinitely
