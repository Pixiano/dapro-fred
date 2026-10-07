"""Self-check for the remember skill: confirm-first wiring, saving, bad input. No GPU."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import memory_ctx, skill


def _selfcheck():
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        reg, _ = skill("remember", ctx)
        s = reg.spec("remember")
        assert s.destructive and s.say and s.preview({"text": "I prefer dark mode"}) == "that I prefer dark mode"          # the engine asks "Just to confirm: remember that ... Yes or no?" first
        assert reg.call("remember", {"text": "I prefer dark mode", "category": "preference"}).data == "Noted, I'll remember that."
        assert "I prefer dark mode" in ctx["recall"].model.show() and "yours" in ctx["recall"].model.show()                 # saved as the user's own (locked) item
        assert reg.call("remember", {"text": "x", "category": "nonsense"}).error_kind == "bad_args"                       # the schema enum is enforced before the skill runs
        assert reg.call("remember", {"text": "  "}).error_kind == "bad_args"
        mem.close()
    print("remember check ok")


if __name__ == "__main__":
    _selfcheck()
