"""Tool: describe_self, FRED's account of what it can do, generated from the tools actually registered (so it can never claim a capability that does not exist).

Sockets
  in : register(registry)   -> describe_self(topic="")   topic = a word from a group or tool name to narrow the answer ("files", "reminders")
  out: a text listing each group's tools with their descriptions; the model words the spoken summary. An empty/unknown topic lists everything; a topic that matches nothing says so.
The group name `selfdoc` is special in engine/classify.py: "what can you do" is social phrasing everywhere else, but here it must offer this tool.
Self-check: python tools/selfdoc.py
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolSpec


def register(reg):
    def describe_self(topic: str = "") -> str:
        t = topic.strip().lower()
        out = []
        for g, names in sorted(reg.groups().items()):
            if g == "selfdoc":
                continue
            rows = [f"- {n}: {reg.spec(n).description}" for n in names if not t or t in n.lower() or t in g.lower() or t in reg.spec(n).description.lower()]
            if rows:
                out.append(f"{g}:\n" + "\n".join(rows))
        return "\n".join(out) if out else f"I don't have anything about {topic}."

    reg.register(ToolSpec("describe_self", describe_self, {"type": "object", "properties": {"topic": {"type": "string", "description": "optional: narrow to one area"}}},
                          "List what FRED can actually do, from its real tool set. Use for 'what can you do', 'what are your abilities'.", group="selfdoc",
                          cues=(r"what can you (?:actually )?do", r"what are you able to", r"your (?:abilities|capabilities|features|tools)", r"how can you help", r"what do you do", r"tell me about yourself", r"can you do\b"),
                          examples=("what can you actually do", "what are your abilities", "tell me about yourself")))


def _selfcheck():
    from tools.registry import Registry
    r = Registry()
    r.register(ToolSpec("get_current_time", lambda: "x", description="Get the time.", group="clock"))
    r.register(ToolSpec("set_volume", lambda: "x", description="Set volume.", group="audio"))
    register(r)
    all_ = r.call("describe_self").data
    assert "get_current_time: Get the time." in all_ and "set_volume" in all_ and "describe_self" not in all_
    assert "set_volume" in r.call("describe_self", {"topic": "audio"}).data and "get_current_time" not in r.call("describe_self", {"topic": "audio"}).data
    assert r.call("describe_self", {"topic": "zebra"}).data == "I don't have anything about zebra."
    print("selfdoc ok")


if __name__ == "__main__":
    _selfcheck()
