"""Self-check for what_can_you_do: groups, a group, one skill, search, not found, and that 50 skills still fit the length cap. No GPU, no embedder."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.loader import HERE, load
from skills.testkit import memory_ctx


def _selfcheck():
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        reg, problems = load(HERE, include_extra=False, ctx=ctx)
        assert not problems, problems
        index = reg.call("what_can_you_do").data
        assert "memory (4): " in index and "self (1): what_can_you_do" in index
        grp = reg.call("what_can_you_do", {"topic": "memory"}).data
        assert all(f"{n}:" in grp for n in ("recall", "remember", "forget", "correct"))
        one = reg.call("what_can_you_do", {"topic": "forget"}).data
        assert "Arguments (* needed): item*" in one and "It asks for confirmation first." in one and "Not for:" in one
        assert reg.call("what_can_you_do", {"topic": "what do you remember about my dentist"}).data.startswith("recall:")        # search by words, no embedder
        assert reg.call("what_can_you_do", {"topic": "zebra juggling"}).error_kind == "not_found"
        mem.close()
    with tempfile.TemporaryDirectory() as t:                                                                                       # 50 skills in 10 groups
        root = Path(t)
        for i in range(50):
            d = root / "core" / f"group{i % 10}" / f"skill_{i}"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps({"name": f"skill_{i}", "description": f"Does thing number {i} with a deliberately long description so the lines add up quickly. More words here.",
                                                         "args": {"properties": {}}, "examples": ["a", "b", "c"]}), encoding="utf-8")
            (d / "run.py").write_text("def run(**a):\n    return 'x'\n", encoding="utf-8")
        d = root / "core" / "self" / "what_can_you_do"
        d.mkdir(parents=True)
        for f in ("manifest.json", "run.py"):
            (d / f).write_bytes((HERE / "core" / "self" / "what_can_you_do" / f).read_bytes())
        reg, _ = load(root, ctx={})
        for topic in ("", "group3", "group0"):
            assert len(reg.call("what_can_you_do", {"topic": topic}).data) <= 1300, topic
        assert "skill_7:" in reg.call("what_can_you_do", {"topic": "thing number 7"}).data                                          # search finds one skill among 51
    print("what_can_you_do check ok")


if __name__ == "__main__":
    _selfcheck()
