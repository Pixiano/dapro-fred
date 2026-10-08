"""Tools: Vatsal's spoken control over what FRED remembers (integration plan step 5): show, add, correct, remove, fix a name.

Sockets
  in : register(registry, recall) with recall = memory_bridge.turn_recall.MemoryRecall (it owns the PersonalModel and Graph)
         show_what_you_know()                  the personal model as text ("#id [category] text"); only ever on request, never recited unprompted
         remember_about_me(text, category)     adds a user item (locked: no inference ever changes it)
         correct_memory(item_id, text)         his wording replaces an item and is locked; the inference behind it is rejected
         forget_memory(item_id)                destructive (asks first): hidden from everything, kept in the log
         correct_name(wrong, right)            "my sister is Nitya, not Navya": merges the wrong name into the right one (journaled; the graph can undo it)
         recall_recent_conversation(days)      the last turns from the archive (today, and yesterday with days=2), newest last: "what did we talk about yesterday"
  out: spoken-style confirmations; an unknown id is not_found; nothing here deletes data (remove hides, merges are journaled)
Backup/restore by voice waits for the L5 jobs phase (P7) that owns the schedules.
Self-check: python tools/memory_tools.py
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolError, ToolSpec

CATS = ["priority", "preference", "working_style", "project", "routine", "decision", "relationship", "trait_hypothesis"]


def register(reg, recall):
    pm, graph = recall.model, recall.graph

    def show() -> str:
        return pm.show()

    def remember(text: str, category: str = "preference") -> str:
        pm.add(category, text)
        return "Noted, I'll remember that."

    def _item(fn, item_id, *a):
        try:
            fn(item_id, *a)
        except ValueError:
            raise ToolError("not_found", f"I don't have a memory number {item_id}.", f"no item {item_id}; show_what_you_know lists the ids")

    def correct(item_id: int, text: str) -> str:
        _item(pm.correct, item_id, text)
        return "Corrected, thanks."

    def forget(item_id: int) -> str:
        _item(pm.remove, item_id)
        return "Forgotten."

    def correct_name(wrong: str, right: str) -> str:
        done = graph.correct("person", wrong, right)
        if not done:
            raise ToolError("not_found", f"I don't know anyone called {wrong}.", f"no entity named {wrong!r}")
        return f"Got it, it's {right}, not {wrong}."

    def recent(days: int = 1) -> str:
        from datetime import datetime, timedelta, timezone
        import config
        lines = []
        for k in range(days - 1, -1, -1):
            day = (datetime.now(config.IST) - timedelta(days=k)).strftime("%Y-%m-%d")
            lines += [f"[{day}] {'You' if r['data'].get('role') == 'user' else 'FRED'}: {r['data'].get('text', '')[:200]}" for r in recall.memory.archive.read("conversation", day)]
        return "\n".join(lines[-24:]) or "We haven't talked in that time."

    cues = (r"what did we (?:talk|speak|discuss)", r"what do you know about me", r"show me what you know", r"what have you (?:learned|remembered)", r"forget (?:that|this|about)", r"remember that", r"that's (?:wrong|not right|incorrect)",
            r"not \w+,? (?:it's|its) \w+", r"is actually", r"memory", r"correct (?:that|your)")
    reg.register(ToolSpec("show_what_you_know", show, description="List what FRED has learned about Vatsal, with item numbers. Only when he asks.", group="memory", cues=cues, examples=("what do you know about me", "show me what you remember about me")))
    reg.register(ToolSpec("recall_recent_conversation", recent, {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 7, "default": 1}}},
                          "Read back the most recent conversation turns (days=1 today, 2 includes yesterday).", group="memory", cues=cues + (r"yesterday", r"earlier today", r"last time we"), examples=("what did we talk about yesterday", "what were we just discussing")))
    reg.register(ToolSpec("remember_about_me", remember, {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "category": {"type": "string", "enum": CATS, "default": "preference"}}},
                          "Remember a fact or preference about Vatsal that he states.", group="memory", say=True, cues=cues, examples=("remember that I prefer dark mode", "note that I work best in the morning")))
    reg.register(ToolSpec("correct_memory", correct, {"type": "object", "required": ["item_id", "text"], "properties": {"item_id": {"type": "integer"}, "text": {"type": "string"}}},
                          "Replace a remembered item (by its number from show_what_you_know) with Vatsal's correct wording.", group="memory", say=True, cues=cues, examples=("that's wrong, it should say I start at nine",)))
    reg.register(ToolSpec("forget_memory", forget, {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}}},
                          "Forget a remembered item by its number (asks the user first).", group="memory", destructive=True, say=True, cues=cues, examples=("forget number three", "forget that about me")))
    reg.register(ToolSpec("correct_name", correct_name, {"type": "object", "required": ["wrong", "right"], "properties": {"wrong": {"type": "string"}, "right": {"type": "string"}}},
                          "Fix a person's name in memory: 'my sister is Nitya, not Navya'.", group="memory", say=True, cues=cues, examples=("my sister is Nitya, not Navya",)))


def _selfcheck():
    import tempfile
    from memory_bridge.link import open_memory
    from memory_bridge.turn_recall import MemoryRecall
    from models.fake import FakeChat, FakeEmbedder
    from tools.registry import Registry
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        rc = MemoryRecall(mem, FakeEmbedder(), FakeChat([]), "fake")
        r = Registry()
        register(r, rc)
        assert r.call("show_what_you_know").data == "Nothing yet."
        assert r.call("remember_about_me", {"text": "prefers dark mode", "category": "preference"}).ok and "prefers dark mode" in r.call("show_what_you_know").data and "yours" in r.call("show_what_you_know").data
        assert r.call("correct_memory", {"item_id": 1, "text": "prefers light mode in daytime"}).ok and "light mode" in r.call("show_what_you_know").data
        assert r.call("correct_memory", {"item_id": 99, "text": "x"}).error_kind == "not_found" and r.call("forget_memory", {"item_id": 99}).error_kind == "not_found"
        assert r.call("recall_recent_conversation").data == "We haven't talked in that time."
        from datetime import datetime, timezone
        import config
        mem.archive.write("conversation", {"role": "user", "text": "hello fred"}, ts=datetime.now(config.IST).isoformat())
        assert "You: hello fred" in r.call("recall_recent_conversation", {"days": 2}).data
        assert r.spec("forget_memory").destructive and not r.spec("correct_memory").destructive
        assert r.call("forget_memory", {"item_id": 1}).ok and r.call("show_what_you_know").data == "Nothing yet."
        rc.graph.resolve("person", "Navya")
        assert "Nitya" in r.call("correct_name", {"wrong": "Navya", "right": "Nitya"}).data and r.call("correct_name", {"wrong": "Zorp", "right": "Nitya"}).error_kind == "not_found"
    print("memory_tools ok")


if __name__ == "__main__":
    _selfcheck()
