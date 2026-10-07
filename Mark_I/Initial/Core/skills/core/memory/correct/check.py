"""Self-check for the correct skill: item fix, name fix, bad input. No GPU."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import memory_ctx, skill


def _selfcheck():
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        rc = ctx["recall"]
        rc.model.add("routine", "wakes up at six")
        reg, _ = skill("correct", ctx)
        s = reg.spec("correct")
        assert s.destructive and s.preview({"wrong": "Navya", "right": "Nitya"}) == "Navya to Nitya" and "wakes up at six" in s.preview({"item": "wakes", "text": "wakes up at seven"})
        assert reg.call("correct", {"item": "wakes", "text": "wakes up at seven"}).data == "Corrected, thanks." and "seven" in rc.model.show() and "six" not in rc.model.show()
        rc.graph.resolve("person", "Navya")
        assert reg.call("correct", {"wrong": "Navya", "right": "Nitya"}).data == "Got it, it's Nitya, not Navya."
        assert reg.call("correct", {"wrong": "Zorp", "right": "Nitya"}).error_kind == "not_found" and reg.call("correct", {"item": "nothing like it", "text": "x"}).error_kind == "not_found"
        assert reg.call("correct", {"wrong": "only one half"}).error_kind == "bad_args" and reg.call("correct", {}).error_kind == "bad_args"
        mem.close()
    print("correct check ok")


if __name__ == "__main__":
    _selfcheck()
