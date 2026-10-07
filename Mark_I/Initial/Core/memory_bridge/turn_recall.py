"""Recall in the turn: before the model answers, add what FRED remembers that bears on what was just said.

Sockets
  in : MemoryRecall(memory, embedder, chat, model="", graph=True).context(text, say=None) -> Recalled(block, ask, direct)
         memory = manager.Memory; embedder = anything with embed(text, is_query) -> vector (models.client.Embedder / FakeEmbedder);
         chat = anything with complete(messages, schema=, think=) -> ChatResult (the judge: keeps the useful candidates, proposes a wider search, may ask);
         say(text) = the "digging deeper" voice: called once, with the memory's own status line, before the wider second search (a broken callback is ignored)
       .index (SemanticIndex), .graph (Graph), .model (PersonalModel): the memory objects, shared with tools/memory_tools.py
  out: Recalled.block   text for the system prompt ("" when nothing is relevant: nothing is injected, the prompt is never padded)
       Recalled.ask     a question the memory needs answered first ("Do you mean Priya when you say 'Pri'?"): the engine speaks it instead of answering
       Recalled.direct  a finished spoken reply (a confirmed alias, "Got it"): the engine speaks it, no model call
Policy (what runs when):
  - the personal model's `relevant(text)` runs on EVERY turn (one short embed, plain code, no model call); it only returns items close to what was said
  - the judged search (an embed + one judge call, ~1 s) runs only when the turn looks memory-bound: it asks about the past ("remember", "last week", "did I", "what did..."),
    or names someone/something the graph knows. Everything else costs nothing extra.
  - an unsure name ("Pri" for Priya) is never used silently: the question is asked first; a yes on the next turn confirms the alias (graph.confirm_alias)
Self-check: python memory_bridge/turn_recall.py (fake embedder + scripted judge, no GPU)
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import log
from engine import classify
from memory_bridge.link import memory_modules

MAX_EVENTS, MAX_CHARS = 5, 1400
JUDGE_SCHEMA = {"title": "recall_judge", "type": "object", "additionalProperties": False, "required": ["relevant", "retry_queries", "ask"],
                "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}, "retry_queries": {"type": "array", "items": {"type": "string"}}, "ask": {"type": "string"}}}
_PAST = re.compile(r"\b(?:remember(?! (?:that|to|this|it)\b(?! (?:time|day|night|week)))|recall|remind me what|last (?:time|week|month|year|night)|yesterday|earlier|previously|before|did (?:i|we)|what did (?:i|we|you)|"
                   r"told you|i mentioned|we (?:said|discussed|talked)|talked about|have i (?:ever|told)|when (?:did|was)|who (?:is|was)|what do you know about(?! me\b| myself\b))\b", re.IGNORECASE)


@dataclass
class Recalled:
    block: str = ""
    ask: str | None = None
    direct: str | None = None


class MemoryRecall:
    def __init__(self, memory, embedder, chat, model: str = "", graph: bool = True):
        m = memory_modules()
        embed = lambda text, is_query=False: embedder.embed(text, is_query)
        self.memory, self.chat = memory, chat
        self.index = m["semantic"].SemanticIndex(memory.store, embed, model or "unset")
        self.index.attach()
        self.graph = m["graph"].Graph(memory.store) if graph else None
        self.model = m["personal_model"].PersonalModel(memory, embed, model or "unset")
        self._judged = m["recall"].judged_search
        self._pending = None                                              # (word, entity_id) awaiting a yes/no from the user

    def _llm(self, prompt: str) -> str:
        return self.chat.complete([{"role": "user", "content": prompt}], schema=JUDGE_SCHEMA, think=False).text

    def wants_search(self, text: str) -> bool:
        if _PAST.search(text or ""):
            return True
        try:
            found, maybe = self.graph.find_in_text(text) if self.graph else ({}, [])
            return bool(found or maybe)
        except Exception:
            return False

    def context(self, text: str, say=None, force: bool = False) -> Recalled:
        """`force` = search even if the text does not look memory-bound (the recall skill: the user asked to look something up)."""
        try:
            return self._context(text, say, force)
        except Exception as ex:                                           # memory never breaks a turn: no recall beats no answer
            log.session("recall_error", error=repr(ex), asked=text[:120])
            return Recalled()

    def _context(self, text: str, say, force: bool = False) -> Recalled:
        if self._pending:
            (word, eid), self._pending = self._pending, None
            if classify.is_affirmative(text):
                self.graph.confirm_alias(word, eid)
                return Recalled(direct="Got it, I'll remember that.")
            if classify.is_negative(text):
                return Recalled(direct="Okay, I won't assume that.")
        lines, about = [], [f"- {i['text']}" for i in self.model.relevant(text)]
        if force or self.wants_search(text):
            r = self._judged(self.index, text, self._llm, k=MAX_EVENTS, say=say, graph=self.graph)
            if r.get("maybe") and not r.get("events") and not r.get("facts"):
                self._pending = tuple(r["maybe"][0])
                return Recalled(ask=r["ask"])
            for name, facts in (r.get("facts") or {}).items():
                lines.append(f"- {name}: " + ", ".join(f"{k} = {v}" for k, v in facts.items()))
            if r.get("groups"):
                for g in r["groups"]:
                    lines += [f"- [{e['ts'][:10]}] ({g['entity']}) {e['content']}" for e in g["events"]]
                lines.append(f"(This fits more than one project. After answering, ask: {r['follow_up']})")
            else:
                lines += [f"- [{e['ts'][:10]}] {e['content']}" for e in r.get("events", [])]
            if r.get("ask") and not lines and not about:
                return Recalled(ask=r["ask"])
        parts = []
        if lines:
            parts.append("What you remember that bears on this (from your memory; may be incomplete, say so if unsure):\n" + "\n".join(lines))
        if about:
            parts.append("What you know about Vatsal that bears on this (use it silently, never recite it):\n" + "\n".join(about))
        return Recalled(block="\n\n".join(parts)[:MAX_CHARS])


def _selfcheck():
    import tempfile
    from memory_bridge.link import open_memory
    from models.fake import FakeChat, FakeEmbedder
    with tempfile.TemporaryDirectory() as t:
        mem = open_memory(t)
        judge = FakeChat([lambda m, tools: json.dumps({"relevant": [int(l.split(":")[0]) for l in m[0]["content"].splitlines() if l[:1].isdigit() and "dentist" in l], "retry_queries": [], "ask": ""})] * 3)
        rc = MemoryRecall(mem, FakeEmbedder(), judge, "fake")
        mem.store.add_event("note", "the dentist appointment is on Thursday at 4 pm")
        mem.store.add_event("note", "bought tomatoes at the market")
        assert rc.context("hello there how are you").block == ""                                     # not memory-bound, nothing close: nothing injected
        r = rc.context("do you remember when my dentist appointment is")
        assert "dentist appointment is on Thursday" in r.block and "tomatoes" not in r.block and r.ask is None, r
        assert rc.context("what time is it").block == "" and len(judge.calls) == 1                   # the judge ran once, for the one memory-bound turn
        said = []
        rc.chat = FakeChat([json.dumps({"relevant": [], "retry_queries": [], "ask": ""})] * 3)
        rc.context("did we talk about the zebra project", say=said.append)                            # nothing found: the wider try announces itself
        assert said == ["Nothing obvious yet, digging deeper..."]
        rc.chat = FakeChat([RuntimeError("model down")])
        assert rc.context("do you remember my dentist").block != "x"                                  # a failing judge falls back to the plain top-k, never an error
        g = rc.graph
        pri = g.resolve("person", "Priya")
        mem.store.add_event("note", "Priya likes cyan")
        rc.chat = FakeChat([json.dumps({"relevant": [], "retry_queries": [], "ask": ""})] * 3)
        r = rc.context("what is Pri's favourite colour?")
        assert r.ask and "Priya" in r.ask and rc._pending, r                                          # unsure name: asked first
        assert rc.context("yes").direct == "Got it, I'll remember that." and rc._pending is None
        assert not rc.wants_search("remember that I prefer dark mode") and not rc.wants_search("what do you know about me") and rc.wants_search("do you remember my dentist")       # saving and "show what you know" belong to the skills
        assert rc.wants_search("remember that time we went hiking") and rc.wants_search("what do you know about Priya")
        assert rc.wants_search("what is Pri's favourite colour?") and rc.graph.find_in_text("Pri")[0]  # the alias now resolves
        mem.close()
    print("turn_recall ok")


if __name__ == "__main__":
    _selfcheck()
