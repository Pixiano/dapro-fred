import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.phone_tools as pt

_real_device_ready = pt._device_ready
_real_adb = pt._adb
_real_sleep = time.sleep

try:
    # capture_camera_photo(): not connected -> plain error, no adb calls made
    pt._device_ready = lambda: False
    result = pt.capture_camera_photo()
    assert "isn't connected" in result, result

    # capture_camera_photo(): screencap fails -> error, HOME still pressed to
    # leave the camera app (no orphaned foreground camera on failure)
    pt._device_ready = lambda: True
    calls = []

    def _fake_adb(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("shell", "screencap"):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    pt._adb = _fake_adb
    time.sleep = lambda *_: None
    result = pt.capture_camera_photo()
    assert "couldn't capture" in result.lower(), result
    assert ("shell", "input", "keyevent", "KEYCODE_HOME") in calls, calls

    # capture_camera_photo(): full success path -> real local path returned,
    # temp file cleaned up on the phone
    calls.clear()

    def _fake_adb_ok(*args, **kwargs):
        calls.append(args)
        if args[0] == "pull":
            Path(args[2]).write_bytes(b"fake-png-bytes")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    pt._adb = _fake_adb_ok
    result = pt.capture_camera_photo()
    assert result == str(pt._CAMERA_CAPTURE_PATH), result
    assert Path(result).read_bytes() == b"fake-png-bytes"
    assert ("shell", "rm", "/sdcard/fred_camera_capture.png") in calls, calls

    pt._CAMERA_CAPTURE_PATH.unlink(missing_ok=True)
finally:
    # time.sleep is the REAL global time module's attribute, not a
    # phone_tools-local copy — leaving it patched breaks every other
    # test collected after this one in the same pytest process.
    # Confirmed live 2026-08-20: an earlier version of this file did
    # exactly that and broke test_screen_watcher.py's timing-dependent
    # assertions when the full suite ran, despite passing in isolation.
    pt._device_ready = _real_device_ready
    pt._adb = _real_adb
    time.sleep = _real_sleep

print("ok")
