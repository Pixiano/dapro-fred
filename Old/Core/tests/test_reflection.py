# Core/tests/test_reflection.py
#
# Pure unit tests for orchestrator/reflection.py — volume-gate threshold,
# interrupt-discard, friend-file free-reign writes, staged-review
# creation/offer/reviewed lifecycle. Mocks the LLM and every path
# (SESSION_DIR/PEOPLE_DIR/REFLECTION_PENDING_DIR/REFLECTION_STATE_PATH)
# entirely — no real vault, no real inference, same shape as
# test_sleep_mode.py / test_consolidation.py.

import json

import pytest

from orchestrator import reflection, sleep_mode


class _FakeLLM:
    def __init__(self, answer=None):
        self._answer = answer or {"friend_entries": [], "self_facts": []}

    def generate(self, messages, tier=None, local_only=False):
        assert tier == "Reflect"
        assert local_only is True
        return json.dumps(self._answer)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    people_dir = tmp_path / "people"
    pending_dir = tmp_path / "pending"
    state_path = tmp_path / "reflection_state.json"

    monkeypatch.setattr(reflection, "SESSION_DIR", session_dir)
    monkeypatch.setattr(reflection, "PEOPLE_DIR", people_dir)
    monkeypatch.setattr(reflection, "REFLECTION_PENDING_DIR", pending_dir)
    monkeypatch.setattr(reflection, "REVIEWED_DIR", pending_dir / "reviewed")
    monkeypatch.setattr(reflection, "REFLECTION_STATE_PATH", state_path)

    sleep_mode._sleeping = False
    yield session_dir, people_dir, pending_dir, state_path


def _write_events(session_dir, name, n, ts_base="2026-08-21T10:00:00"):
    path = session_dir / name
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({
                "type": "user_speech", "text": "hi", "ts": f"{ts_base}.{i:03d}",
            }) + "\n")


def test_below_threshold_skips_and_leaves_state_untouched(paths):
    session_dir, people_dir, pending_dir, state_path = paths
    reflection._save_state({"last_run_ts": "2026-08-20T00:00:00"})
    _write_events(session_dir, "session_2026-08-21.jsonl", reflection.REFLECTION_MIN_NEW_EVENTS - 1)
    reflection.configure(_FakeLLM())

    result = reflection._run_if_due()

    assert result is None
    assert reflection._load_state()["last_run_ts"] == "2026-08-20T00:00:00"
    assert not people_dir.exists()


def test_at_threshold_runs_and_writes_both_paths(paths):
    session_dir, people_dir, pending_dir, state_path = paths
    reflection._save_state({"last_run_ts": "2026-08-20T00:00:00"})
    _write_events(session_dir, "session_2026-08-21.jsonl", reflection.REFLECTION_MIN_NEW_EVENTS)
    reflection.configure(_FakeLLM({
        "friend_entries": [{"person": "Test Friend", "file_action": "new", "content": "likes tea"}],
        "self_facts": [{"content": "works best late at night"}],
    }))
    sleep_mode._sleeping = True

    result = reflection._run_if_due()

    assert result is not None and "test-friend.md" in result
    assert (people_dir / "test-friend.md").exists()
    assert "likes tea" in (people_dir / "test-friend.md").read_text(encoding="utf-8")
    drafts = list(pending_dir.glob("*.md"))
    assert len(drafts) == 1
    assert "works best late at night" in drafts[0].read_text(encoding="utf-8")
    assert reflection._load_state()["last_run_ts"] != "2026-08-20T00:00:00"


def test_friend_file_written_with_no_confirmation_step(paths):
    """Free-reign write path: no gate, no pending-confirmation state —
    the file exists the instant the pass completes."""
    session_dir, people_dir, pending_dir, state_path = paths
    reflection._save_state({"last_run_ts": "2026-08-20T00:00:00"})
    _write_events(session_dir, "session_2026-08-21.jsonl", reflection.REFLECTION_MIN_NEW_EVENTS)
    reflection.configure(_FakeLLM({
        "friend_entries": [{"person": "Another Person", "file_action": "new", "content": "prefers evening calls"}],
        "self_facts": [],
    }))
    sleep_mode._sleeping = True

    reflection._run_if_due()

    path = people_dir / "another-person.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "prefers evening calls" in text
    assert "unknown — recorded by FRED's sleep-mode reflection pass" in text


def test_interrupt_mid_pass_discards_everything(paths):
    session_dir, people_dir, pending_dir, state_path = paths
    reflection._save_state({"last_run_ts": "2026-08-21T00:00:00"})
    _write_events(
        session_dir, "session_2026-08-22.jsonl", reflection.REFLECTION_MIN_NEW_EVENTS,
        ts_base="2026-08-22T10:00:00",
    )
    reflection.configure(_FakeLLM({
        "friend_entries": [{"person": "Ghost", "file_action": "new", "content": "should never be written"}],
        "self_facts": [{"content": "should never be staged"}],
    }))
    sleep_mode._sleeping = False  # already "awake" before the pass even starts
    state_before = reflection._load_state()

    result = reflection._run_if_due()

    assert result is None
    assert reflection._load_state() == state_before
    assert not people_dir.exists()
    assert not pending_dir.exists()


def test_turn_in_progress_discards_even_if_sleeping(paths, monkeypatch):
    """Regression for the 2026-08-25 crash: is_sleeping() alone isn't
    enough to gate a chunk's LLM call — a real turn can be running (a
    hotkey/wake-word/HUD command) before is_sleeping()'s own camera
    debounce has caught up. _turn_in_progress() must catch that even
    while sleep_mode still says "sleeping"."""
    session_dir, people_dir, pending_dir, state_path = paths
    reflection._save_state({"last_run_ts": "2026-08-21T00:00:00"})
    _write_events(
        session_dir, "session_2026-08-22.jsonl", reflection.REFLECTION_MIN_NEW_EVENTS,
        ts_base="2026-08-22T10:00:00",
    )
    reflection.configure(_FakeLLM({
        "friend_entries": [{"person": "Ghost", "file_action": "new", "content": "should never be written"}],
        "self_facts": [{"content": "should never be staged"}],
    }))
    sleep_mode._sleeping = True  # still "sleeping" by the camera-debounced signal

    class _FakeLock:
        def locked(self):
            return True

    class _FakeApp:
        _turn_lock = _FakeLock()

    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: _FakeApp())
    state_before = reflection._load_state()

    result = reflection._run_if_due()

    assert result is None
    assert reflection._load_state() == state_before
    assert not people_dir.exists()
    assert not pending_dir.exists()


def test_review_offer_primes_carry_and_marks_reviewed(paths, monkeypatch):
    session_dir, people_dir, pending_dir, state_path = paths
    pending_dir.mkdir(parents=True)
    (pending_dir / "2026-08-21_self-observations.md").write_text("- a fact\n", encoding="utf-8")

    primed = []
    reflection.configure(_FakeLLM(), prime_carry=lambda names: primed.append(names))

    assert reflection.has_pending_review()
    text = reflection.offer_review_text()
    assert "review" in text.lower()
    assert primed == [["review_pending_reflection"]]

    monkeypatch.setattr(reflection.os, "startfile", lambda p: None, raising=False)
    msg = reflection.review_pending()

    assert "Opened" in msg
    assert not reflection.has_pending_review()
    assert list((pending_dir / "reviewed").glob("*.md"))


def test_decline_leaves_draft_in_place_for_recurring_nudge(paths):
    """No 'yes' ever happens — the draft must stay exactly where it is,
    still offerable, nothing marks it reviewed on its own."""
    session_dir, people_dir, pending_dir, state_path = paths
    pending_dir.mkdir(parents=True)
    (pending_dir / "2026-08-21_self-observations.md").write_text("- a fact\n", encoding="utf-8")

    reflection.configure(_FakeLLM())  # no prime_carry — nothing to route

    assert reflection.has_pending_review()
    reflection.offer_review_text()  # spoken, declined — nothing calls review_pending()
    assert reflection.has_pending_review()  # still there, still pending
