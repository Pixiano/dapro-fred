# Core/tests/test_look_through_camera.py
#
# look_through_camera() must use the desk webcam (input/presence.py's
# PRESENCE_CAMERA_INDEX), not the paired phone's camera over ADB — fixed
# 2026-08-21 after Vatsal reported "it looks through the camera connected
# to the cable instead of the webcam." Regression check: phone_tools'
# capture_camera_photo must never be called from this path.

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2

from tools import vision_tools


def test_look_through_camera_uses_webcam_not_phone(monkeypatch):
    calls = {"phone": 0, "video_capture_index": None, "describe_prompt": None}

    def _boom():
        calls["phone"] += 1
        raise AssertionError("must not touch the phone camera")

    monkeypatch.setattr("tools.phone_tools.capture_camera_photo", _boom)

    class _FakeCap:
        def isOpened(self):
            return True

        def read(self):
            return True, "fake-frame"

        def release(self):
            pass

    def _fake_video_capture(index):
        calls["video_capture_index"] = index
        return _FakeCap()

    class _FakeBuf:
        def tobytes(self):
            return b"fake-jpg-bytes"

    monkeypatch.setattr(cv2, "VideoCapture", _fake_video_capture)
    monkeypatch.setattr(cv2, "imencode", lambda ext, frame: (True, _FakeBuf()))

    def _fake_describe_image(data_uri, prompt, max_tokens=300):
        calls["describe_prompt"] = prompt
        assert data_uri.startswith("data:image/jpeg;base64,")
        return "a desk with a keyboard"

    fake_app = types.SimpleNamespace(
        orchestrator=types.SimpleNamespace(
            llm=types.SimpleNamespace(describe_image=_fake_describe_image)
        )
    )
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    from config.settings import PRESENCE_CAMERA_INDEX

    result = vision_tools.look_through_camera("what's on the desk?")

    assert result == "a desk with a keyboard"
    assert calls["phone"] == 0
    assert calls["video_capture_index"] == PRESENCE_CAMERA_INDEX
    assert "what's on the desk?" in calls["describe_prompt"]


def test_take_phone_photo_uses_phone_not_webcam(monkeypatch, tmp_path):
    calls = {"webcam": 0, "phone": 0, "describe_prompt": None}

    def _boom(*a, **k):
        calls["webcam"] += 1
        raise AssertionError("must not touch the webcam")

    monkeypatch.setattr(cv2, "VideoCapture", _boom)

    fake_photo = tmp_path / "fake_phone_capture.png"
    fake_photo.write_bytes(b"fake-png-bytes")

    def _fake_capture_camera_photo():
        calls["phone"] += 1
        return str(fake_photo)

    monkeypatch.setattr(
        "tools.phone_tools.capture_camera_photo", _fake_capture_camera_photo
    )

    def _fake_describe_image(data_uri, prompt, max_tokens=300):
        calls["describe_prompt"] = prompt
        assert data_uri.startswith("data:image/png;base64,")
        return "a hallway"

    fake_app = types.SimpleNamespace(
        orchestrator=types.SimpleNamespace(
            llm=types.SimpleNamespace(describe_image=_fake_describe_image)
        )
    )
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    result = vision_tools.take_phone_photo("what's down the hall?")

    assert result == "a hallway"
    assert calls["phone"] == 1
    assert calls["webcam"] == 0
    assert "what's down the hall?" in calls["describe_prompt"]


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-q"]))
