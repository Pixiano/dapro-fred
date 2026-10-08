"""Regression checks for the 2026-10-08 code-review fixes (one assert per fix that no other self-check covers). Run: python tests/test_review_fixes.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events as ev
import config
from engine import lockdown
from engine.classify import looks_social
from memory_bridge.feed import Feed
from proactive.scheduler import Scheduler


def main():
    assert lockdown._norm("1, 2, 3, 4") == "1234" and lockdown._norm("Open Sesame!") == "open sesame"        # a spoken PIN
    assert config._coerce("time, calculate", ("x",)) == ("time", "calculate")                                  # tuple from an env var
    with tempfile.TemporaryDirectory() as t:
        bus, now = ev.EventBus(), [1000.0]
        s = Scheduler(bus, Path(t) / "s.json", now=lambda: now[0])
        s.add(1600, "", "timer"); s.add(1700, "call mum")
        assert s.cancel("timer") == 1 and len(s.items()) == 1                                                   # "cancel the timer" finds a text-less timer
        wrote = []

        class Mem:
            class archive:
                new_session = staticmethod(lambda: "s")
                write = staticmethod(lambda stream, data, session=None, ts=None: wrote.append(stream))
        locked = [True]
        f = Feed(Mem, bus, lambda: locked[0])
        f.start()
        bus.emit("stt", "transcript", text="unlock secret", final=True, audio_s=1, ms=1)
        assert "speech" not in wrote                                                                             # lockdown: the passphrase is not archived
        locked[0] = False
        bus.emit("stt", "transcript", text="hello", final=True, audio_s=1, ms=1)
        assert "speech" in wrote
    assert looks_social("thanks") and looks_social("how are you") and not looks_social("thanks, now open spotify")
    print("review fixes ok")


if __name__ == "__main__":
    main()
