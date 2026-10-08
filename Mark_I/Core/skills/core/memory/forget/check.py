"""Self-check for the forget skill: matching by id or words, ambiguity, preview naming the item. No GPU."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import memory_ctx, skill


def _selfcheck():
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        pm = ctx["recall"].model
        pm.add("preference", "likes spicy food")
        pm.add("routine", "wakes up at six")
        pm.add("routine", "goes to the gym at six")
        reg, _ = skill("forget", ctx)
        assert reg.spec("forget").destructive and reg.spec("forget").preview({"item": "spicy"}) == "that you likes spicy food"
        assert reg.call("forget", {"item": "six"}).error_kind == "bad_args" and "wakes up" in pm.show()                      # two matches: asks which, forgets nothing
        assert reg.call("forget", {"item": "zebra"}).error_kind == "not_found" and "nothing" in reg.spec("forget").preview({"item": "zebra"})
        assert reg.call("forget", {"item": "spicy"}).data == "Forgotten." and "spicy" not in pm.show()
        assert reg.call("forget", {"item": "#2"}).ok and "wakes up" not in pm.show() and "gym" in pm.show()                 # by number
        mem.close()
    print("forget check ok")


if __name__ == "__main__":
    _selfcheck()
