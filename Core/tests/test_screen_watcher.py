# Screen watcher safety/coordination logic. Real screenshot capture and
# real Vision-model inference are deliberately NOT exercised here (that
# needs a live GPU run, done separately) — these pin the two things that
# must be correct even without a model loaded: the cross-process VRAM
# check, and the on-demand tool's staleness handling.

import json

from vision import screen_watcher, screen_context
from tools import vision_tools


def test_skips_a_cycle_when_main_process_has_a_model_loaded(tmp_path, monkeypatch):
    status_path = tmp_path / "llm_status.json"
    status_path.write_text(json.dumps({"loaded": ["Standard"]}), encoding="utf-8")
    monkeypatch.setattr(screen_watcher, "LLM_STATUS_PATH", status_path)

    assert screen_watcher._main_process_has_a_model_loaded() is True


def test_proceeds_when_nothing_is_loaded(tmp_path, monkeypatch):
    status_path = tmp_path / "llm_status.json"
    status_path.write_text(json.dumps({"loaded": []}), encoding="utf-8")
    monkeypatch.setattr(screen_watcher, "LLM_STATUS_PATH", status_path)

    assert screen_watcher._main_process_has_a_model_loaded() is False


def test_missing_status_file_means_nothing_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_watcher, "LLM_STATUS_PATH", tmp_path / "nope.json")
    assert screen_watcher._main_process_has_a_model_loaded() is False


def test_corrupt_status_file_fails_safe_to_assume_busy(tmp_path, monkeypatch):
    """A read error must never be read as 'safe to load a second model' —
    the whole point is avoiding a VRAM collision on this machine's
    documented crash history."""
    status_path = tmp_path / "llm_status.json"
    status_path.write_text("not valid json{{{", encoding="utf-8")
    monkeypatch.setattr(screen_watcher, "LLM_STATUS_PATH", status_path)

    assert screen_watcher._main_process_has_a_model_loaded() is True


# ---------------------------------------------------------------
# whats_on_screen tool — staleness handling
# ---------------------------------------------------------------

def test_no_capture_yet_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "none.json")
    result = vision_tools.whats_on_screen()
    assert "haven't looked" in result


def test_fresh_capture_is_returned_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 300)
    screen_context.write("Chrome, watching a video")

    assert vision_tools.whats_on_screen() == "Chrome, watching a video"


def test_stale_capture_is_flagged_not_presented_as_current(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 1)
    screen_context.write("VS Code, editing a file")
    time.sleep(1.1)

    result = vision_tools.whats_on_screen()
    assert "stale" in result
    assert "VS Code" in result


# ---------------------------------------------------------------
# whats_on_screen always attempts a fresh capture — real bug from
# session_2026-08-12.jsonl: "explain the question on my screen" got
# routed to take_screenshot instead of whats_on_screen (a separate,
# tool-description-level fix), and even when whats_on_screen WAS
# called, a stale cache was handed back with no indication a fresh
# on-demand capture had even been tried, because the old
# _FORCE_CAPTURE_AGE_SECONDS gate only fired past a 10s-old cache.
# ---------------------------------------------------------------

class _FakeWatcher:
    def __init__(self, capture_result):
        self.capture_result = capture_result
        self.calls = 0

    def capture_now(self):
        self.calls += 1
        return self.capture_result


class _FakeApp:
    def __init__(self, capture_result):
        self.screen_watcher = _FakeWatcher(capture_result)


def test_capture_is_attempted_even_when_cache_is_already_fresh(tmp_path, monkeypatch):
    """The old gate skipped capture_now() entirely when the cache was
    under 10s old — this tool means 'look now', every call, not just
    when the passive watcher's cache happens to be stale."""
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 300)
    screen_context.write("Chrome, watching a video")

    fake_app = _FakeApp(capture_result=False)
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    vision_tools.whats_on_screen()

    assert fake_app.screen_watcher.calls == 1


def test_failed_fresh_attempt_is_distinguished_from_plain_staleness(tmp_path, monkeypatch):
    """capture_now() returning False (cloud rate-limited, local unsafe
    mid-turn — the exact shape of session_2026-08-12's repeated 429s)
    must read as 'I just tried and couldn't', not a generic old-cache
    hedge that looks identical to never having tried at all."""
    import time

    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 1)
    screen_context.write("VS Code, editing a file")
    time.sleep(1.1)

    fake_app = _FakeApp(capture_result=False)
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    result = vision_tools.whats_on_screen()

    assert "tried to look just now" in result
    assert "VS Code" in result


def test_successful_fresh_capture_is_returned_without_the_stale_hedge(tmp_path, monkeypatch):
    """When capture_now() succeeds, the freshly-written description must
    come back clean — no stale/couldn't-look hedge, even though the
    pre-capture on-disk value (read before capture_now runs) was old."""
    import time

    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 300)
    screen_context.write("VS Code, editing a file")
    time.sleep(1.1)

    def fake_capture_now():
        screen_context.write("Chrome, watching a video")
        return True

    fake_app = _FakeApp(capture_result=False)
    fake_app.screen_watcher.capture_now = fake_capture_now
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    result = vision_tools.whats_on_screen()

    assert result == "Chrome, watching a video"
