# Core/tests/test_consolidation.py
#
# Pure unit tests for the pending-state lifecycle in
# orchestrator/consolidation.py — mocks session_summary/vault_map/notify
# so no real vault or LLM is needed, same shape as test_sleep_mode.py.

from orchestrator import consolidation


def _reset(monkeypatch):
    consolidation._pending = None
    calls = []
    monkeypatch.setattr(consolidation, "notify", lambda msg, title="F.R.E.D.": calls.append((msg, title)))
    # No staged reflection draft in any of these tests — on_sleep_exit
    # now checks this too, and without stubbing it real tests would hit
    # the real vault path.
    monkeypatch.setattr(consolidation.reflection, "has_pending_review", lambda: False)
    return calls


def test_sleep_exit_with_nothing_pending_is_a_noop(monkeypatch):
    calls = _reset(monkeypatch)
    consolidation.on_sleep_exit()
    assert calls == []
    assert consolidation._pending is None


def test_sleep_enter_builds_bundled_recap(monkeypatch):
    calls = _reset(monkeypatch)
    monkeypatch.setattr(
        consolidation.session_summary, "preview_session_summary",
        lambda day=None, llm=None: "3 requests today.",
    )
    monkeypatch.setattr(
        consolidation.vault_map, "preview_missing",
        lambda: "2 vault files aren't mapped yet: a.md, b.md.",
    )

    consolidation.on_sleep_enter()

    assert consolidation._pending is not None
    assert "3 requests today." in consolidation._pending
    assert "2 vault files" in consolidation._pending
    assert calls == []  # nothing spoken yet — that's on_sleep_exit's job


def test_sleep_enter_omits_gap_line_when_map_is_current(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        consolidation.session_summary, "preview_session_summary",
        lambda day=None, llm=None: "Nothing logged today yet, sir.",
    )
    monkeypatch.setattr(consolidation.vault_map, "preview_missing", lambda: "")

    consolidation.on_sleep_enter()

    assert "Also," not in consolidation._pending


def test_sleep_enter_failure_never_raises(monkeypatch):
    _reset(monkeypatch)
    def _boom(day=None, llm=None):
        raise RuntimeError("no logs")
    monkeypatch.setattr(consolidation.session_summary, "preview_session_summary", _boom)

    consolidation.on_sleep_enter()  # must not raise

    assert consolidation._pending is None


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
        test_sleep_enter_builds_bundled_recap,
        test_sleep_enter_omits_gap_line_when_map_is_current,
        test_sleep_enter_failure_never_raises,
        test_sleep_exit_speaks_once_then_clears,
    ):
        mp = _Monkeypatch()
        try:
            fn(mp)
        finally:
            mp.undo()
    print("test_consolidation: all passed")
