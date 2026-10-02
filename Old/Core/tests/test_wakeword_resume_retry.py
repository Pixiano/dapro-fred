# resume()'s self-heal path: a failed stream.start() must immediately
# re-resolve the mic by name and retry once in the SAME call, not just
# log and wait for whoever happens to call resume() next — confirmed
# live 2026-08-15 that without an immediate retry, FRED sat deaf for an
# hour after a USB topology change with only a log line to show for it.

import types

from input import wakeword


class _FailThenSucceedStream:
    """First construction's .start() raises; the second's succeeds."""
    calls = 0

    def __init__(self, **kwargs):
        pass

    def start(self):
        _FailThenSucceedStream.calls += 1
        if _FailThenSucceedStream.calls == 1:
            raise RuntimeError("PaErrorCode -9999: dead index")


class _AlwaysFailStream:
    def __init__(self, **kwargs):
        pass

    def start(self):
        raise RuntimeError("PaErrorCode -9999: dead index")


def _listener(monkeypatch):
    listener = wakeword.WakewordListener()
    monkeypatch.setattr(listener, "_ensure_model", lambda: None)
    monkeypatch.setattr(
        "audio.device_info.input_extra_settings", lambda: None
    )
    return listener


def test_resume_retries_once_after_reselect_and_succeeds(monkeypatch):
    _FailThenSucceedStream.calls = 0
    listener = _listener(monkeypatch)
    monkeypatch.setattr(wakeword.sd, "InputStream", _FailThenSucceedStream)

    reselected = []
    monkeypatch.setattr(
        "audio.device_info.apply_saved_devices", lambda: reselected.append(True)
    )

    listener.resume()

    assert reselected == [True]
    assert _FailThenSucceedStream.calls == 2
    assert listener._stream is not None


def test_resume_gives_up_after_one_failed_retry_not_looping(monkeypatch):
    listener = _listener(monkeypatch)
    monkeypatch.setattr(wakeword.sd, "InputStream", _AlwaysFailStream)
    monkeypatch.setattr(
        "audio.device_info.apply_saved_devices", lambda: None
    )

    listener.resume()

    # Bounded to exactly one retry: stream stays None (safe to retry
    # again on a LATER resume() call), doesn't raise, doesn't spin.
    assert listener._stream is None


def test_resume_stays_none_if_reselect_itself_fails(monkeypatch):
    listener = _listener(monkeypatch)
    monkeypatch.setattr(wakeword.sd, "InputStream", _AlwaysFailStream)

    def _boom():
        raise RuntimeError("no devices at all")

    monkeypatch.setattr("audio.device_info.apply_saved_devices", _boom)

    listener.resume()  # must not raise
    assert listener._stream is None
