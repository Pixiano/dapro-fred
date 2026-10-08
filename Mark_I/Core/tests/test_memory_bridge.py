"""P3b gate: the memory bridge end to end with a scripted brain (no GPU): turns land in the archive on the right streams, and recall changes an answer.

  python tests/test_memory_bridge.py
Invented memories only. The scripted brain answers from whatever its system prompt contains, so the test fails if recall does not actually reach the model.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from engine.loop import Engine
from memory_bridge.feed import Feed
from memory_bridge.link import open_memory
from memory_bridge.turn_recall import MemoryRecall
from models.fake import FakeChat, FakeEmbedder, tool_call
from tools.builtin import build_registry


def brain(messages, tools):
    sysmsg = messages[0]["content"]
    return "Your dentist is on Thursday at 4 pm." if "Thursday at 4 pm" in sysmsg else "I don't know."


def judge(messages, tools):
    return json.dumps({"relevant": [int(l.split(":")[0]) for l in messages[0]["content"].splitlines() if l[:1].isdigit() and "dentist" in l], "retry_queries": [], "ask": ""})


def t_recall_changes_the_answer_and_turns_land_in_the_archive():
    with tempfile.TemporaryDirectory() as t, open_memory(Path(t) / "memory") as mem:        # the context closes the database before the folder is removed
        config.set_data_dir(t)
        bus = ev.EventBus()
        emb = FakeEmbedder()
        recall = MemoryRecall(mem, emb, FakeChat([judge] * 5), "fake")
        mem.store.add_event("note", "the dentist appointment is on Thursday at 4 pm")
        mem.store.add_event("note", "bought tomatoes at the market")
        feed = Feed(mem, bus)
        sid = feed.start()
        q = "do you remember when my dentist appointment is"
        without = Engine(FakeChat([brain]), build_registry(), emb, "you are fred", bus=bus).run(q, source="cli")          # same question, no memory attached
        withmem = Engine(FakeChat([brain, tool_call("get_current_time")]), build_registry(), emb, "you are fred", bus=bus, recall=recall)
        a = withmem.run(q, source="voice")
        assert without.text == "I don't know." and a.text == "Your dentist is on Thursday at 4 pm.", (without.text, a.text)         # recall changed the answer
        b = withmem.run("what time is it", source="voice")                                                                          # a tool turn: tool_call stream
        feed.stop()
        day = datetime.now(config.IST).strftime("%Y-%m-%d")
        conv = mem.archive.read("conversation", day)
        assert [(r["data"]["role"], r["data"]["text"][:20]) for r in conv][:2] == [("user", q[:20]), ("assistant", "I don't know.")]
        assert len(conv) == 6 and all(r["session"] == sid for r in conv)                                                              # three turns, two lines each
        tc = mem.archive.read("tool_call", day)
        assert [r["data"]["name"] for r in tc] == ["get_current_time"] and tc[0]["data"]["ok"] is True
        assert mem.archive.verify() == [] and b.via == "say"


def t_interim_line_and_memory_questions():
    with tempfile.TemporaryDirectory() as t, open_memory(Path(t) / "memory") as mem:        # the context closes the database before the folder is removed
        config.set_data_dir(t)
        bus = ev.EventBus()
        emb, said, seen = FakeEmbedder(), [], []
        bus.subscribe(seen.append, kinds={"say"})
        nothing = lambda m, tools: json.dumps({"relevant": [], "retry_queries": [], "ask": ""})
        recall = MemoryRecall(mem, emb, FakeChat([nothing] * 6), "fake")
        mem.store.add_event("note", "bought tomatoes at the market")
        e = Engine(FakeChat(["I can't recall that."]), build_registry(), emb, "x", bus=bus, recall=recall, on_say=said.append)
        r = e.run("did we talk about the zebra project last week")
        assert said == ["Nothing obvious yet, digging deeper..."] and seen[0].data["text"] == said[0] and r.via == "memory" and "keyword" in r.text      # the interim line comes first; finding nothing, the memory asks for a keyword instead of letting the model guess
        pri = recall.graph.resolve("person", "Priya")
        mem.store.add_event("note", "Priya likes cyan")
        e2 = Engine(FakeChat([]), build_registry(), emb, "x", recall=recall)
        r = e2.run("what is Pri's favourite colour")
        assert r.via == "memory" and "Priya" in r.text                                                                                 # an unsure name is asked about, the model never guesses
        assert e2.run("yes").text == "Got it, I'll remember that." and e2.history[-1]["content"].startswith("Got it")


def main():
    for k, t in sorted(globals().items()):
        if k.startswith("t_"):
            t()
            print("ok  ", k)
    config.reload()


if __name__ == "__main__":
    main()
