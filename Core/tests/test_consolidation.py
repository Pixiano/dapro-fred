# Core/tests/test_consolidation.py
#
# Pure unit tests for the pending-state lifecycle in
# orchestrator/consolidation.py — mocks session_summary/vault_map/notify
# so no real vault or LLM is needed, same shape as test_sleep_mode.py.
#
# Covers the 2026-08-22 change: on_sleep_enter() now auto-writes (no
# "save it"/"add them" needed first) and the spoken recap goes through
# an LLM polish pass — see test_sleep_enter_auto_writes_without_confirmation
# and test_sleep_enter_polishes_recap_with_llm below.

from orchestrator import consolidation


class _NoNote:
    """Stand-in for _daily_note_path()'s return value — no existing
    daily note, so on_sleep_enter's existing-content read is a no-op."""
    def exists(self):
        return False


def _reset(monkeypatch):
    consolidation._pending = None
    calls = []
    monkeypatch.setattr(consolidation, "notify", lambda msg, title="F.R.E.D.": calls.append((msg, title)))
    # No staged reflection draft in any of these tests — on_sleep_exit
    # now checks this too, and without stubbing it real tests would hit
    # the real vault path.
    monkeypatch.setattr(consolidation.reflection, "has_pending_review", lambda: False)
    monkeypatch.setattr(consolidation.session_summary, "_daily_note_path", lambda day=None: _NoNote())
    return calls


def test_sleep_exit_with_nothing_pending_is_a_noop(monkeypatch):
    calls = _reset(monkeypatch)
    consolidation.on_sleep_exit()
    assert calls == []
    assert consolidation._pending is None


def test_sleep_enter_auto_writes_without_confirmation(monkeypatch):
    """The write functions run directly from on_sleep_enter() now —
    no waiting on a spoken 'save it' / 'add them' first — and each
    auto-write carries the auto-logged marker."""
    calls = _reset(monkeypatch)
    written = {}
    monkeypatch.setattr(
        consolidation.session_summary, "summarise_today",
        lambda day=None, llm=None, existing_note=None: "3 requests today.",
    )

    def _fake_save(day=None, llm=None, summary="", auto=False):
        written["summary"] = summary
        written["auto"] = auto
        return "saved"

    monkeypatch.setattr(consolidation.session_summary, "save_session_summary", _fake_save)
    monkeypatch.setattr(consolidation.vault_map, "scan_missing", lambda: ["a.md", "b.md"])

    def _fake_append(auto=False):
        written["map_auto"] = auto
        return "added"

    monkeypatch.setattr(consolidation.vault_map, "append_missing", _fake_append)

    consolidation.on_sleep_enter()

    # Written immediately, not deferred behind a confirmation.
    assert written["summary"] == "3 requests today."
    assert written["auto"] is True
    assert written["map_auto"] is True
    assert consolidation._pending is not None
    assert calls == []  # nothing spoken yet — that's on_sleep_exit's job


def test_sleep_enter_skips_map_write_when_nothing_missing(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        consolidation.session_summary, "summarise_today",
        lambda day=None, llm=None, existing_note=None: "Nothing logged today yet, sir.",
    )
    monkeypatch.setattr(consolidation.session_summary, "save_session_summary", lambda **kw: "saved")
    monkeypatch.setattr(consolidation.vault_map, "scan_missing", lambda: [])

    def _boom(auto=False):
        raise AssertionError("append_missing should not be called when nothing is missing")

    monkeypatch.setattr(consolidation.vault_map, "append_missing", _boom)

    consolidation.on_sleep_enter()  # must not raise, must not call append_missing


def test_sleep_enter_failure_never_raises(monkeypatch):
    _reset(monkeypatch)
    def _boom(day=None, llm=None, existing_note=None):
        raise RuntimeError("no logs")
    monkeypatch.setattr(consolidation.session_summary, "summarise_today", _boom)
    monkeypatch.setattr(consolidation.vault_map, "scan_missing", lambda: [])

    consolidation.on_sleep_enter()  # must not raise

    assert consolidation._pending is None


def test_sleep_enter_polishes_recap_with_llm(monkeypatch):
    """The bundled recap material goes through one LLM polish call —
    reusing summary/vault content is fine (local_only=True, same
    sensitivity class as summarise_today's own call) — and the spoken
    result must not contain the old confirmation phrasing, since
    auto-write means there's nothing left to confirm."""
    _reset(monkeypatch)
    monkeypatch.setattr(
        consolidation.session_summary, "summarise_today",
        lambda day=None, llm=None, existing_note=None: "Worked on the vault today.",
    )
    monkeypatch.setattr(consolidation.session_summary, "save_session_summary", lambda **kw: "saved")
    monkeypatch.setattr(consolidation.vault_map, "scan_missing", lambda: ["a.md"])
    monkeypatch.setattr(consolidation.vault_map, "append_missing", lambda auto=False: "added")

    seen = {}

    class _FakeLLM:
        def generate(self, messages, local_only=False, max_tokens=None):
            seen["messages"] = messages
            seen["local_only"] = local_only
            seen["max_tokens"] = max_tokens
            return "While you were away, I saved today's summary and logged a new file to MAP.md."

    consolidation.configure(_FakeLLM())
    try:
        consolidation.on_sleep_enter()
    finally:
        consolidation.configure(None)

    assert seen["local_only"] is True
    assert seen["messages"][0]["role"] == "system"
    system_prompt = seen["messages"][0]["content"]
    assert "ONE short spoken sentence" in system_prompt
    assert "not a paragraph" in system_prompt
    assert "combine them into that one sentence" in system_prompt
    assert seen["max_tokens"] == consolidation._POLISH_MAX_TOKENS
    assert consolidation._pending == (
        "While you were away, I saved today's summary and logged a new file to MAP.md."
    )
    for phrase in ("say save it", "say add them", "Say save it", "Say add them"):
        assert phrase not in consolidation._pending


def test_sleep_exit_speaks_once_then_clears(monkeypatch):
    calls = _reset(monkeypatch)
    consolidation._pending = "While you were away: 3 requests today."

    consolidation.on_sleep_exit()
    assert len(calls) == 1
    assert "3 requests today." in calls[0][0]
    assert consolidation._pending is None

    consolidation.on_sleep_exit()  # fire-once: second call is a no-op
    assert len(calls) == 1


if __name__ == "__main__":
    import types

    class _Monkeypatch:
        def __init__(self):
            self._saved = []

        def setattr(self, obj, name, value):
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._saved):
                setattr(obj, name, value)

    for fn in (
        test_sleep_exit_with_nothing_pending_is_a_noop,
        test_sleep_enter_auto_writes_without_confirmation,
        test_sleep_enter_skips_map_write_when_nothing_missing,
        test_sleep_enter_failure_never_raises,
        test_sleep_enter_polishes_recap_with_llm,
        test_sleep_exit_speaks_once_then_clears,
    ):
        mp = _Monkeypatch()
        try:
            fn(mp)
        finally:
            mp.undo()
    print("test_consolidation: all passed")
