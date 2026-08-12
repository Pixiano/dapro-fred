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

class _FakeLLM:
    """Stands in for orchestrator.llm — only unload() matters here."""
    def __init__(self, dropped=1):
        self.dropped = dropped
        self.unload_calls = 0

    def unload(self):
        self.unload_calls += 1
        return self.dropped


class _FakeOrchestrator:
    def __init__(self, llm):
        self.llm = llm


class _FakeWatcher:
    def __init__(self, capture_result, force_local_result=None):
        self.capture_result = capture_result
        self.force_local_result = force_local_result
        self.calls = []  # force_local value passed on each call, in order

    def capture_now(self, force_local=False):
        self.calls.append(force_local)
        return self.force_local_result if force_local else self.capture_result


class _FakeApp:
    def __init__(self, capture_result, dropped=1, force_local_result=None):
        self.screen_watcher = _FakeWatcher(capture_result, force_local_result)
        self.orchestrator = _FakeOrchestrator(_FakeLLM(dropped))


def test_capture_is_attempted_even_when_cache_is_already_fresh(tmp_path, monkeypatch):
    """The old gate skipped capture_now() entirely when the cache was
    under 10s old — this tool means 'look now', every call, not just
    when the passive watcher's cache happens to be stale."""
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 300)
    screen_context.write("Chrome, watching a video")

    fake_app = _FakeApp(capture_result=True)
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    vision_tools.whats_on_screen()

    assert fake_app.screen_watcher.calls == [False]


def test_failed_fresh_attempt_is_distinguished_from_plain_staleness(tmp_path, monkeypatch):
    """capture_now() returning False (cloud rate-limited, local unsafe
    mid-turn — the exact shape of session_2026-08-12's repeated 429s)
    must read as 'I just tried and couldn't', not a generic old-cache
    hedge that looks identical to never having tried at all. Covers the
    case where the forced-local retry (below) also comes up empty."""
    import time

    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 1)
    screen_context.write("VS Code, editing a file")
    time.sleep(1.1)

    fake_app = _FakeApp(capture_result=False, dropped=1, force_local_result=False)
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    result = vision_tools.whats_on_screen()

    assert "tried to look just now" in result
    assert "VS Code" in result


def test_forced_local_retry_after_unloading_the_main_model(tmp_path, monkeypatch):
    """The actual 2026-08-12 fix: when the first attempt fails (cloud
    down, local blocked because the main model was resident), unload
    the main model and force one local-only retry rather than accepting
    the failure — this is what makes the already-configured local
    Vision fallback (QAT Gemma) reachable during a real conversation at
    all, instead of being permanently gated out by VRAM safety."""
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 300)
    screen_context.write("stale placeholder")

    fake_app = _FakeApp(capture_result=False, dropped=1, force_local_result=True)
    fake_app.screen_watcher.capture_result = False

    def fake_capture_now(force_local=False):
        fake_app.screen_watcher.calls.append(force_local)
        if force_local:
            screen_context.write("Chrome, watching a video")
            return True
        return False

    fake_app.screen_watcher.capture_now = fake_capture_now
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    result = vision_tools.whats_on_screen()

    assert fake_app.screen_watcher.calls == [False, True]
    assert fake_app.orchestrator.llm.unload_calls == 1
    assert result == "Chrome, watching a video"


def test_no_retry_when_nothing_was_loaded_to_free(tmp_path, monkeypatch):
    """If unload() drops nothing, the main model wasn't what blocked
    local fallback on the first attempt — local was already tried and
    already failed too, so retrying would just repeat the same failure
    for no benefit."""
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_PATH", tmp_path / "sc.json")
    monkeypatch.setattr(screen_context, "SCREEN_CONTEXT_MAX_AGE_SECONDS", 1)
    screen_context.write("VS Code, editing a file")

    fake_app = _FakeApp(capture_result=False, dropped=0)
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: fake_app)

    vision_tools.whats_on_screen()

    assert fake_app.screen_watcher.calls == [False]
    assert fake_app.orchestrator.llm.unload_calls == 1


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
