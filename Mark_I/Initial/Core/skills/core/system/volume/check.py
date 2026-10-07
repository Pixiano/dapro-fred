"""Self-check for volume: step logic and argument rules (pure), then the real mixer is read once and left exactly as it was (no change of the user's volume)."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    spec = importlib.util.spec_from_file_location("vol_run", Path(__file__).with_name("run.py"))
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    assert v.next_level(50, "up") == 70 and v.next_level(50, "down", "small") == 40 and v.next_level(95, "up", "large") == 100 and v.next_level(5, "down") == 0 and v.next_level(50, "up", "bogus") == 70
    reg, problems = skill("volume", {})
    assert not problems and not reg.spec("volume").destructive
    assert reg.call("volume", {"action": "set"}).error_kind == "bad_args"                    # set without a level
    assert reg.call("volume", {"action": "set", "level": 140}).error_kind == "bad_args"       # outside 0 to 100: refused by the schema, the mixer is never touched
    assert reg.call("volume", {"action": "explode"}).error_kind == "bad_args"
    import json
    from engine.loop import Engine
    from models.fake import FakeChat, tool_call
    ran = []
    from tools.registry import Registry
    import dataclasses
    guarded = Registry()
    guarded.register(dataclasses.replace(reg.spec("volume"), fn=lambda **kw: (ran.append(kw), "Volume set.")[1]))
    assert reg.spec("volume").refuse_if                                                           # the guard comes from the manifest
    for said, expect_run in (("set microphone to 20", False), ("switch to the headphones", False), ("set the input level to 50", False), ("engage lockdown", False),
                             ("set the volume to 30", True), ("turn it up a bit", True), ("mute", True)):
        ran.clear()
        e = Engine(FakeChat([tool_call("volume", action="set", level=20), "ok."]), guarded, None, "")
        e.run(said, source="test")
        assert bool(ran) == expect_run, (said, ran)                                              # refused before the speakers are touched, or allowed
    r = reg.call("volume", {"action": "get"})
    assert r.ok and ("Volume is at" in r.data or "muted" in r.data) or r.error_kind == "unavailable", r
    print("volume check ok")


if __name__ == "__main__":
    _selfcheck()
