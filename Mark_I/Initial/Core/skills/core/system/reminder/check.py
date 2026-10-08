"""Self-check for reminder: a real Scheduler on a temp file with a fake clock (no waiting)."""
import importlib.util
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import events as ev
from proactive.scheduler import Scheduler
from skills import Unavailable


def _selfcheck():
    spec = importlib.util.spec_from_file_location("reminder_run", Path(__file__).with_name("run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try:
        m.bind({})
        raise AssertionError("bound without scheduler")
    except Unavailable:
        pass
    t0 = datetime(2031, 3, 4, 15, 0).timestamp()
    clock = [t0]
    s = Scheduler(ev.EventBus(), Path(tempfile.mkdtemp()) / "s.json", now=lambda: clock[0])
    m.bind({"scheduler": s})

    def bad(**kw):
        try:
            m.run(**kw)
        except Exception as ex:
            assert ex.kind == "bad_args", kw
            return ex.speakable
        raise AssertionError(kw)
    assert m.run("list") == "You have nothing scheduled."
    assert m.run("set", text="check the oven", minutes=20) == "Okay, I'll remind you at 3:20 PM today to check the oven."
    assert m.run("set", text="call the dentist", at="09:00", days_ahead=1) == "Okay, I'll remind you at 9:00 AM tomorrow to call the dentist."
    assert m.run("set", minutes=10) == "Okay, timer set for 3:10 PM today." and s.items()[0]["kind"] == "timer"
    assert m.run("list").startswith("Coming up: timer at 3:10 PM today; check the oven at 3:20 PM today; call the dentist")
    assert "When" in bad(action="set", text="x") and "What should" in bad(action="set", at="09:00")
    assert "already passed" in bad(action="set", text="x", at="14:00") and "What time" in bad(action="set", text="x", at="tomorrow")
    assert "Which" in bad(action="cancel") and "Do you want" in bad(action="wipe")
    assert m.run("cancel", match="oven") == "Cancelled 1 reminder." and m.run("cancel", match="zebra") == "I don't have a reminder like that."
    assert m.run("cancel", match="all") == "Cancelled 2 reminders." and not s.items()
    print("reminder check ok")


if __name__ == "__main__":
    _selfcheck()
