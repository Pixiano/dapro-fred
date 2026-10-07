"""Self-check for read_own_docs on the real repo docs: list, readme, paging, keyword match, ambiguity, unknown names, and that nothing outside the allowed documents can be read."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    reg, problems = skill("read_own_docs", {})
    assert not problems and not reg.spec("read_own_docs").destructive
    call = lambda **a: reg.call("read_own_docs", a)
    lst = call()
    assert lst.ok and "Initial/Core/README.md" in lst.data and "Progress/" in lst.data, lst.data[:300]
    r1 = call(doc="readme")
    assert r1.ok and r1.data.startswith("Initial/Core/README.md [page 1 of ") and len(r1.data) < 2700, r1.data[:200]
    pages = int(r1.data.split(" of ")[1].split("[")[0].split(",")[0].split("]")[0])
    if pages > 1:
        assert call(doc="readme", page=2).ok and call(doc="readme", page=pages + 1).error_kind == "bad_args"
    assert call(doc="progress").ok and "match" in call(doc="progress").data                      # two or more snapshots: asks which, never guesses
    assert call(doc="20 percent").data.startswith("Initial/Core/Progress/20_percent.md")
    nope = call(doc="zebra juggling")
    assert nope.error_kind == "bad_args" and "README" in nope.message
    for sneaky in ("../../etc/passwd", "C:\\Windows\\win.ini", "weights", ".env", "config.py"):
        r = call(doc=sneaky)
        assert not r.ok or "[page" not in r.data, (sneaky, r)                                      # only the listed markdown documents are readable
    print("read_own_docs check ok")


if __name__ == "__main__":
    _selfcheck()
