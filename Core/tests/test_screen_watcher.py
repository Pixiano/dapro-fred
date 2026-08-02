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
