# Core/tests/test_headphone_watch.py
#
# Pure logic tests for headphone_watch.py: confirmation-prompt reply
# handling (handle_confirmation_reply), and check_and_switch's
# media-playing priority signal + headphone device fallback chain
# (media_state.is_media_playing, presence, and device_info all mocked
# — no real camera/classifier/hardware needed for these decision
# paths). Everything requiring a real camera/insightface/trained model
# is validated live, same convention as headphone_features.py's own
# __main__ self-check.

import numpy as np

from orchestrator import headphone_watch as hw


def _reset():
    hw._pending_confirmation = None


def _reset_switch_state():
    hw._last_state = None
    hw._streak = 0
    hw._pending_state = None
    hw._switch_failed_count = 0
    hw._pending_confirmation = None
    hw._last_check_ts = 0.0


def _make_enrolled(tmp_path, monkeypatch):
    """check_and_switch's first gate is HEADPHONES_ON_PATHS[0].exists()
    and the OFF equivalent — fake both to real (empty) files so the
    "not enrolled yet" early-return doesn't fire in these tests."""
    on_path = tmp_path / "on0.jpg"
    off_path = tmp_path / "off0.jpg"
    on_path.touch()
    off_path.touch()
    monkeypatch.setattr(hw, "HEADPHONES_ON_PATHS", [on_path])
    monkeypatch.setattr(hw, "HEADPHONES_OFF_PATHS", [off_path])


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


def _drive_to_switch(monkeypatch):
    """Runs check_and_switch() HEADPHONE_CHECK_STREAK times — the
    number of consecutive confirmed reads it takes to actually trigger
    a switch (see check_and_switch's own streak/debounce logic).
    Resets _last_check_ts before each call so the real self-throttle
    gate (see check_and_switch's own comment on it) doesn't block these
    back-to-back calls, which happen with no real time between them."""
    for _ in range(hw.HEADPHONE_CHECK_STREAK):
        hw._last_check_ts = 0.0
        hw.check_and_switch(notify=None)


def test_media_playing_skips_camera_and_targets_headphones(tmp_path, monkeypatch):
    """Vatsal's own ask 2026-08-25: media playing anywhere on the
    machine means headphones, no camera/classifier check needed — no
    presence check either (2nd ask, same day) — and no streak debounce
    either (3rd ask, same day): a single call switches instantly, since
    media-playing is a deterministic signal, not a shaky camera read."""
    _reset_switch_state()
    _make_enrolled(tmp_path, monkeypatch)
    monkeypatch.setattr(
        hw.presence, "is_present",
        lambda: (_ for _ in ()).throw(AssertionError("must not check presence when media's playing")),
    )
    monkeypatch.setattr(
        hw.presence, "last_poll_frame_and_face",
        lambda: (_ for _ in ()).throw(AssertionError("camera path must be skipped")),
    )
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: True)
    monkeypatch.setattr(
        hw, "_wearing_headphones",
        lambda frame, face: (_ for _ in ()).throw(AssertionError("camera path must be skipped")),
    )
    monkeypatch.setattr(
        hw.device_info, "list_output_devices",
        lambda: [{"name": hw.HEADPHONE_OUTPUT_DEVICE_NAME, "index": 5}],
    )
    switched = []
    monkeypatch.setattr(hw.device_info, "set_output_device", lambda index: switched.append(index))

    hw.check_and_switch(notify=None)  # ONE call — must switch immediately, no debounce wait

    assert switched == [5]
    assert hw._last_state is True


def test_fallback_device_used_when_primary_missing(tmp_path, monkeypatch):
    """Only the Realme fallback is connected — headphones still switches,
    just to the second device in the chain."""
    _reset_switch_state()
    _make_enrolled(tmp_path, monkeypatch)
    monkeypatch.setattr(hw.presence, "is_present", lambda: True)
    monkeypatch.setattr(
        hw.presence, "last_poll_frame_and_face",
        lambda: (np.zeros((4, 4, 3), dtype=np.uint8), object()),
    )
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: True)
    monkeypatch.setattr(
        hw.device_info, "list_output_devices",
        lambda: [{"name": hw.HEADPHONE_OUTPUT_DEVICE_FALLBACK_NAME, "index": 9}],
    )
    switched = []
    monkeypatch.setattr(hw.device_info, "set_output_device", lambda index: switched.append(index))

    _drive_to_switch(monkeypatch)

    assert switched == [9]
    assert hw._last_state is True


def test_failure_phrase_when_neither_headphone_device_present(tmp_path, monkeypatch):
    """Neither the primary nor the fallback device is connected — falls
    through to the (now shortened/generalized) failure phrase pool,
    never calls set_output_device."""
    _reset_switch_state()
    _make_enrolled(tmp_path, monkeypatch)
    monkeypatch.setattr(hw.presence, "is_present", lambda: True)
    monkeypatch.setattr(
        hw.presence, "last_poll_frame_and_face",
        lambda: (np.zeros((4, 4, 3), dtype=np.uint8), object()),
    )
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: True)
    monkeypatch.setattr(hw.device_info, "list_output_devices", lambda: [])
    monkeypatch.setattr(
        hw.device_info, "set_output_device",
        lambda index: (_ for _ in ()).throw(AssertionError("must not switch")),
    )
    spoken = []

    for _ in range(hw.HEADPHONE_CHECK_STREAK):
        hw._last_check_ts = 0.0  # bypass the self-throttle for these back-to-back calls
        hw.check_and_switch(notify=lambda msg, title=None: spoken.append(msg))

    assert spoken and spoken[0] in hw._SWITCH_FAILED_TO_HEADPHONES_PHRASES
    assert hw._last_state is None  # never actually switched


def test_self_throttle_skips_a_too_soon_recheck(tmp_path, monkeypatch):
    """Regression for the 2026-08-25 cadence change: the scheduler job
    now fires every HEADPHONE_POLL_SECONDS_ON_HEADPHONES (3s), faster
    than the on-speakers cadence check_and_switch should actually run
    at — a call within min_interval of the last real check must do
    nothing at all, not even touch the streak."""
    _reset_switch_state()
    _make_enrolled(tmp_path, monkeypatch)
    monkeypatch.setattr(hw.presence, "is_present", lambda: True)
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: False)
    monkeypatch.setattr(
        hw.presence, "last_poll_frame_and_face",
        lambda: (_ for _ in ()).throw(AssertionError("throttled call must not do any work")),
    )
    hw._last_check_ts = hw.time.monotonic()  # "just checked" — nothing due yet

    hw.check_and_switch(notify=None)

    assert hw._streak == 0  # untouched — the throttle returned before any real work


def test_media_stopping_still_debounces_the_switch_to_speakers(tmp_path, monkeypatch):
    """Vatsal's own question 2026-08-25: after a media-triggered instant
    switch to headphones (_streak forced to HEADPHONE_CHECK_STREAK, see
    the test above this file's media-priority test), does the camera
    path's own debounce still hold once media stops? Yes — it's a
    completely separate branch from the media fast-path, untouched by
    it. This pins that with a real sequence: media on (instant switch),
    then media off with the camera saying "off" HEADPHONE_CHECK_STREAK
    times — the first HEADPHONE_CHECK_STREAK - 1 of those must NOT
    switch, only the last one does."""
    _reset_switch_state()
    _make_enrolled(tmp_path, monkeypatch)
    monkeypatch.setattr(hw.presence, "is_present", lambda: True)
    monkeypatch.setattr(
        hw.presence, "last_poll_frame_and_face",
        lambda: (np.zeros((4, 4, 3), dtype=np.uint8), object()),
    )
    monkeypatch.setattr(
        hw.device_info, "list_output_devices",
        lambda: [
            {"name": hw.HEADPHONE_OUTPUT_DEVICE_NAME, "index": 5},
            {"name": hw.SPEAKER_OUTPUT_DEVICE_NAME, "index": 1},
        ],
    )
    switched = []
    monkeypatch.setattr(hw.device_info, "set_output_device", lambda index: switched.append(index))

    # Media on: instant switch to headphones, one call.
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: True)
    hw.check_and_switch(notify=None)
    assert switched == [5]
    assert hw._last_state is True

    # Media off: camera now reads "off" (speakers) every call — must
    # NOT switch until the HEADPHONE_CHECK_STREAK-th consecutive read.
    monkeypatch.setattr(hw.media_state, "is_media_playing", lambda: False)
    monkeypatch.setattr(hw, "_wearing_headphones", lambda frame, face: False)
    for i in range(hw.HEADPHONE_CHECK_STREAK - 1):
        hw._last_check_ts = 0.0
        hw.check_and_switch(notify=None)
        assert switched == [5], f"switched too early on attempt {i + 1}"
        assert hw._last_state is True

    hw._last_check_ts = 0.0
    hw.check_and_switch(notify=None)
    assert switched == [5, 1]
    assert hw._last_state is False
