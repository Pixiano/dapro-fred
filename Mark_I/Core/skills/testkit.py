"""Helpers for a skill's check.py: a throw-away memory with the fake embedder, and loading one skill by name through the real loader.

Sockets
  in : memory_ctx(tmpdir, judge_replies=None) -> (memory, ctx)   ctx = {"recall": MemoryRecall} over a temp memory (no GPU); judge_replies = scripted answers of the recall judge
       skill(name, ctx) -> (registry, problems)               loads only skills/core/*/<name>
Self-check: python skills/testkit.py
"""
import json
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_bridge.link import open_memory
from memory_bridge.turn_recall import MemoryRecall
from models.fake import FakeChat, FakeEmbedder
from skills.loader import HERE, load


def memory_ctx(tmpdir, judge_replies=None):
    mem = open_memory(tmpdir)
    replies = judge_replies or [json.dumps({"relevant": [], "retry_queries": [], "ask": ""})] * 6
    return mem, {"recall": MemoryRecall(mem, FakeEmbedder(), FakeChat(replies), "fake")}


def skill(name: str, ctx: dict):
    import shutil
    t = Path(tempfile.mkdtemp())
    try:
        src = next((HERE / "core").glob(f"*/{name}"))
        shutil.copytree(src, t / "core" / src.parent.name / name)
        return load(t, include_extra=False, ctx=ctx)
    finally:
        shutil.rmtree(t, ignore_errors=True)


def _selfcheck():
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        reg, problems = skill("remember", ctx)
        assert reg.names() == ["remember"] and not problems
        mem.close()
    reg, problems = skill("remember", {})
    assert reg.names() == [] and "unavailable" in problems[0]
    print("testkit ok")


if __name__ == "__main__":
    _selfcheck()
