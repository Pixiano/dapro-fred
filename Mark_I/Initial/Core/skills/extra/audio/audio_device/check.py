"""Self-check for audio_device with a FAKE backend and invented device names (no real device is touched, nothing personal here).

Covers: list, a headset paired but not connected, elimination ("my new earbuds" with one headset-like device) switching speaker and microphone together, the yes/no that saves an alias to
memory and the alias used next time, "already using", speaker / monitor / microphone categories with the current device eliminated, an explicit name (no teaching question), two fitting
headsets (asks), one unrecognisable device (asks "Do you mean X?" and teaches after the yes), bad input, and the manifest guard.
"""
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from engine.gates import Confirmer
from skills.testkit import memory_ctx

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("audio_device_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Fake:
    def __init__(self, devs):
        self.devs, self.set_calls = devs, []

    def devices(self):
        return list(self.devs)

    def set_default(self, dev_id):
        d = next(x for x in self.devs if x.id == dev_id)
        for x in self.devs:
            if x.flow == d.flow:
                x.default = x.id == dev_id
        self.set_calls.append(dev_id)


def world(m, buds="unplugged", orion=False, zephyr=False):
    D = m.Dev
    devs = [D("o1", "Speakers (Desk Audio)", "out", "active", True), D("o2", "Speakers (Side USB Audio)", "out", "active"), D("o3", "ZX99 (NVIDIA High Definition Audio)", "out", "active"),
            D("i1", "Lavalier Mic (Side USB Audio)", "in", "active", True), D("i2", "Microphone (Studio 40)", "in", "active"),
            D("o4", "Zeta Buds (Zeta Buds)", "out", buds), D("o5", "Headset (Zeta Buds Hands-Free)", "out", buds), D("i4", "Headset (Zeta Buds Hands-Free)", "in", buds)]
    if orion:
        devs += [D("o6", "Headphones (Orion X1)", "out", "active"), D("o7", "Headset (Orion X1 Hands-Free)", "out", "active"), D("i6", "Headset (Orion X1 Hands-Free)", "in", "active")]
    if zephyr:
        devs += [D("o8", "Zephyr 512 (Zephyr 512 ANC)", "out", "active")]
    return devs


def _selfcheck():
    m = _load()
    config.set_data_dir(tempfile.mkdtemp())
    mem = None
    try:
        t = tempfile.mkdtemp()
        mem, ctx = memory_ctx(t)

        class Reg:
            confirmer = Confirmer()
        ctx["registry"] = Reg()
        events = []

        class Bus:
            def emit(self, *a, **k):
                events.append((a, k))
        ctx["bus"] = Bus()
        m.bind(ctx)
        conf = Reg.confirmer
        alias_rows = lambda: ctx["recall"].model.q("SELECT text FROM model_items WHERE text LIKE 'audio device alias:%'")

        # paired but not connected: honest, nothing changes
        m.BACKEND = fake = Fake(world(m))
        r = m.run(device="my earbuds")
        assert "can't find a connected device" in r and "Zeta Buds" in r and not fake.set_calls, r
        lst = m.run(action="list")
        assert lst.startswith("Outputs: ") and "Speakers (Desk Audio) (current)" in lst and "Paired but not connected: Zeta Buds" in lst and lst.count("Zeta Buds (Zeta Buds)") == 1, lst

        # connected: elimination (speakers and the display are contradicted) -> speaker AND microphone, the stereo output rather than the hands-free twin
        m.BACKEND = fake = Fake(world(m, "active"))
        r = m.run(device="my new earbuds")
        assert r.startswith("Switched to Zeta Buds (Zeta Buds) for speaker and microphone."), r
        assert fake.set_calls == ["o4", "i4"], fake.set_calls
        assert "Should I remember that earbuds means Zeta Buds (Zeta Buds)? Yes or no?" in r and conf.pending is not None and not alias_rows()          # asked first, nothing saved yet
        assert events[-1][0] == ("audio", "device_changed") and events[-1][1]["output"].startswith("Zeta") and events[-1][1]["input"].startswith("Headset")
        verdict = conf.resolve("yes please")                                                                                                            # what the engine runs on "yes"
        assert verdict[:2] == ("run", "audio_device") and verdict[2]["action"] == "remember_alias", verdict
        assert "remember" in m.run(**verdict[2]).lower()
        assert [x["text"] for x in alias_rows()] == ["audio device alias: earbuds = zeta buds"]
        assert m.run(device="my new earbuds").startswith("Already using Zeta Buds"), "second time"

        # next time the alias carries it, even when another headset is also connected, and no teaching question follows
        m.BACKEND = fake = Fake(world(m, "active", orion=True))
        r = m.run(device="my earbuds")
        assert r.startswith("Switched to Zeta Buds") and "Should I remember" not in r and fake.set_calls == ["o4", "i4"], (r, fake.set_calls)

        # speakers / monitor / microphone with the current device eliminated; an explicit name is not re-taught
        conf._p = None
        m.BACKEND = fake = Fake(world(m))
        r = m.run(device="speakers")
        assert r.startswith("Switched to Speakers (Side USB Audio)") and fake.set_calls == ["o2"], (r, fake.set_calls)
        conf._p = None
        m.BACKEND = fake = Fake(world(m))
        assert m.run(device="the monitor").startswith("Switched to ZX99") and fake.set_calls == ["o3"]
        conf._p = None
        m.BACKEND = fake = Fake(world(m))
        assert m.run(device="the monitor's speakers").startswith("Switched to ZX99") and fake.set_calls == ["o3"]                       # monitor beats speakers when both are said
        conf._p = None
        m.BACKEND = fake = Fake(world(m))
        r = m.run(device="the other mic")
        assert r.startswith("Switched to Microphone (Studio 40) for the microphone") and fake.set_calls == ["i2"], (r, fake.set_calls)
        m.BACKEND = fake = Fake(world(m))
        r = m.run(device="the lavalier mic")
        assert r.startswith("Already using Lavalier") and not fake.set_calls and "Should I remember" not in r, r

        # two fitting headsets: asks, and the name finishes it with no teaching question
        mem.close()
        t2 = tempfile.mkdtemp()
        mem, ctx = memory_ctx(t2)
        ctx["registry"], ctx["bus"] = Reg(), Bus()
        m.bind(ctx)
        conf = Reg.confirmer
        m.BACKEND = fake = Fake(world(m, "active", orion=True))
        r = m.run(device="my headphones")
        assert r.startswith("Which one: ") and "Zeta" in r and "Orion" in r and not fake.set_calls, r
        r = m.run(device="the orion")
        assert r.startswith("Switched to Headphones (Orion X1) for speaker and microphone") and "Should I remember" not in r and fake.set_calls == ["o6", "i6"], (r, fake.set_calls)

        # one connected device whose name says nothing: never switched to on category wording alone; asks, and after the yes teaches
        conf._p = None
        m.BACKEND = fake = Fake(world(m, zephyr=True))
        r = m.run(device="my earbuds")
        assert r == "Nothing connected is named like earbuds. Do you mean Zephyr 512 (Zephyr 512 ANC)? Yes or no?" and not fake.set_calls, r
        verdict = conf.resolve("yes")
        assert verdict[:2] == ("run", "audio_device") and verdict[2]["as_word"] == "earbuds", verdict
        r = m.run(**verdict[2])
        assert r.startswith("Switched to Zephyr 512 (Zephyr 512 ANC)") and fake.set_calls == ["o8"] and "Should I remember that earbuds means Zephyr 512" in r, (r, fake.set_calls)

        # bad input
        for bad in ({"device": ""}, {"device": "   "}):
            try:
                m.run(**bad)
                raise AssertionError(bad)
            except Exception as ex:
                assert getattr(ex, "kind", "") == "bad_args", ex
        m.BACKEND = None
    finally:
        if mem:
            mem.close()
        m.BACKEND = None

    # the real loader accepts the manifest; volume and mute wording is refused before the skill runs, device wording is not
    from skills.loader import HERE as SK, load
    reg, problems = load(SK, ctx={"recall": None})
    assert not [p for p in problems if "audio_device" in p], problems
    spec = reg.spec("audio_device")
    assert spec and spec.group == "audio" and spec.refuse_if and spec.say
    assert any(re.search(p, "turn the volume up", re.I) for p in spec.refuse_if) and not any(re.search(p, "switch to my headphones", re.I) for p in spec.refuse_if)
    print("audio_device check ok")


if __name__ == "__main__":
    _selfcheck()
