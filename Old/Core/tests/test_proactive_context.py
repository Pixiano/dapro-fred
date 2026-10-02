# Core/tests/test_proactive_context.py
#
# A reminder fires, FRED speaks it, and the next thing the user says is
# about it. The transcript carried the words but nothing marked them as
# unprompted, so a follow-up read as though FRED had volunteered the
# remark mid-conversation rather than a timer having gone off.
#
# The fix keeps the transcript clean on purpose: whatever the recorder
# stores becomes a line attributed to FRED, so a "[Reminder]" prefix in
# there would be both something he never said and a format the model
# would copy aloud. The kind/recency lives beside the transcript instead.

import time

from utils import notifier


def _silence(monkeypatch):
    monkeypatch.setattr(notifier, "_voice", None)
    monkeypatch.setattr(notifier, "Notification", lambda **kw: type(
        "N", (), {"show": lambda self: None, "set_audio": lambda self, *a: None}
    )())


def test_transcript_still_gets_the_raw_sentence(monkeypatch):
    _silence(monkeypatch)
    seen = []
    notifier.set_recorder(seen.append)

    notifier.notify("Here's your reminder: call the bank.", title="Reminder")

    # Unchanged behaviour - no wrapping, no prefix, nothing FRED didn't say.
    assert seen == ["Here's your reminder: call the bank."]


def test_kind_and_recency_are_available_beside_it(monkeypatch):
    _silence(monkeypatch)
    notifier.set_recorder(lambda m: None)

    notifier.notify("Here's your reminder: call the bank.", title="Reminder")

    last = notifier.last_proactive()
    assert last is not None
    assert last["kind"] == "Reminder"
    assert "call the bank" in last["message"]


def test_an_old_interruption_stops_being_context(monkeypatch):
    # An hour-old reminder must not keep colouring an unrelated
    # conversation - it stops being what the user is talking about.
    _silence(monkeypatch)
    notifier.set_recorder(lambda m: None)

    notifier.notify("Old news.", title="Reminder")
    notifier._last_proactive["at"] = time.time() - 3600

    assert notifier.last_proactive() is None
    assert notifier.last_proactive(within_seconds=7200) is not None


def test_nothing_proactive_yet_is_not_an_error(monkeypatch):
    monkeypatch.setattr(notifier, "_last_proactive", None)
    assert notifier.last_proactive() is None
