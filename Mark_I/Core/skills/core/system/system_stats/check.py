"""Self-check for system_stats: every part, the part boundaries (cpu says nothing about memory and so on), the GPU parser on fixed lines, a bad drive, a bad part. Reads the real machine, read-only."""
import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    spec = importlib.util.spec_from_file_location("ss_run", Path(__file__).with_name("run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    g = m.gpu_parse("NVIDIA GeForce RTX 5060 Ti, 3, 13078, 16311, 39, 6.72")
    assert "; ".join(m._gpu_bits(g)) == "3 percent busy; 12.8 of 15.9 GB video memory used; 39 C; 7 W"
    assert m._gpu_bits(m.gpu_parse("Card X, 50, [N/A], [N/A], 60, [N/A]")) == ["50 percent busy", "60 C"]            # fields the driver does not report are left out
    try:
        m.gpu_parse("garbage")
        raise AssertionError
    except Exception as ex:
        assert getattr(ex, "kind", "") == "failed"

    reg, problems = skill("system_stats", {})
    assert not problems and not reg.spec("system_stats").destructive
    call = lambda **a: reg.call("system_stats", a)
    c = call(part="cpu")
    assert c.ok and re.match(r"CPU is at \d+ percent across \d+ threads", c.data) and "Temperature" in c.data and "Memory" not in c.data and "GB" not in c.data.split("running")[0], c.data
    r = call(part="ram")
    assert r.ok and re.match(r"Memory: [\d.]+ of [\d.]+ GB used \(\d+ percent\)", r.data) and "CPU" not in r.data, r.data
    d = call(part="disk")
    assert d.ok and "C: " in d.data and "GB free of" in d.data
    for spelling in ("C", "c:", "C:\\"):
        one = call(part="disk", drive=spelling)
        assert one.ok and one.data.startswith("C: ") and one.data.count("GB free") == 1, (spelling, one.data)
    bad = call(part="disk", drive="Q")
    assert bad.error_kind == "bad_args" and "C" in bad.message
    gp = call(part="gpu")
    assert gp.ok and "percent busy" in gp.data and "CPU" not in gp.data or gp.error_kind == "unavailable", gp
    a = call()
    assert a.ok and a.data.startswith("CPU ") and "Memory " in a.data and a.data.endswith("."), a
    assert call(part="all").data.startswith("CPU ")
    assert call(part="everything").error_kind == "bad_args"                                                   # not in the enum: refused before anything runs
    print("system_stats check ok")


if __name__ == "__main__":
    _selfcheck()
