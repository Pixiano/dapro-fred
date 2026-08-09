# Core/tests/test_notifier.py
#
# 2026-08-09: a proactive check asked "are you prepped for the movie
# you just logged?", the user answered "No, not yet.", and FRED replied
# "I won't log the movie" — a reply to a question it had no record of
# ever asking. notify() spoke it, toasted it, printed it, but never told
# ConversationState, which is the only thing _build_messages reads for
# history. set_recorder closes that; these tests are against notify()
# in isolation, with Notification/TTS stubbed out so a test run doesn't
# actually pop a Windows toast or speak out loud.

import utils.notifier as notifier


def _silence(monkeypatch):
    """No real toast, no real speech — notify()'s other two channels
    are not what's under test here."""
    monkeypatch.setattr(notifier, "Notification", lambda **kw: type(
        "Fake", (), {"show": lambda self: None}
    )())
    monkeypatch.setattr(notifier, "_voice", lambda msg: None)


def test_notify_calls_the_recorder_with_the_message(monkeypatch):
    _silence(monkeypatch)
    seen = []
    notifier.set_recorder(seen.append)

    notifier.notify("Movie at 2:45 PM, sir — are you prepped for it?")

    assert seen == ["Movie at 2:45 PM, sir — are you prepped for it?"]


def test_notify_works_with_no_recorder_set(monkeypatch):
    _silence(monkeypatch)
    notifier.set_recorder(None)
    notifier.notify("This must not raise.")  # no assertion needed beyond "didn't raise"


def test_a_broken_recorder_does_not_break_notify(monkeypatch):
    """Fail-open, same convention as the toast/TTS try/excepts right
    next to it — a broken recorder must not silence the notification
    itself."""
    _silence(monkeypatch)
    spoken = []
    monkeypatch.setattr(notifier, "_voice", spoken.append)

    def _boom(msg):
        raise RuntimeError("boom")

    notifier.set_recorder(_boom)
    notifier.notify("Still gets said out loud.")

    assert spoken == ["Still gets said out loud."]


def test_recorder_gets_the_raw_message_no_title_wrapping(monkeypatch):
    _silence(monkeypatch)
    seen = []
    notifier.set_recorder(seen.append)

    notifier.notify("Plain text only.", title="Some Title")

    assert seen == ["Plain text only."]


def test_recorder_and_voice_both_fire_independently(monkeypatch):
    _silence(monkeypatch)
    spoken, recorded = [], []
    monkeypatch.setattr(notifier, "_voice", spoken.append)
    notifier.set_recorder(recorded.append)

    notifier.notify("Both channels.")

    assert spoken == ["Both channels."]
    assert recorded == ["Both channels."]
