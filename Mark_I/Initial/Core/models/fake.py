"""Scripted stand-ins for the model client, so every layer above the model is tested with no GPU and no playground.

Sockets
  in : FakeChat(script)  script = list of replies consumed one per call; a reply is
         str (plain text) | ChatResult | dict (an assistant message) | Exception (raised) | callable(messages, tools) -> any of these
       FakeEmbedder(dim=64)  deterministic hashed bag-of-words vectors: texts sharing words score closer
       tool_call(name, **args) -> ChatResult carrying one tool call
  out: the same surface as client.Chat / client.Embedder: complete(), stream(), __call__(), embed(); `.calls` records what each call was sent
       (messages, tools, schema, effort, think) so tests assert on the offer and the prompt, not only the reply.
A script that runs out raises AssertionError: a loop that calls the model more often than the test expects fails loudly.
Self-check: python models/fake.py
"""
import hashlib
import math
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import toolparse
from models.client import ChatResult


def tool_call(name: str, **args) -> ChatResult:
    return ChatResult(tool_calls=[toolparse.ToolCall(name, args, f"call_{name}")], finish_reason="tool_calls")


class FakeChat:
    def __init__(self, script=(), model: str = "fake"):
        self.script, self.model, self.calls = list(script), model, []

    def _next(self, messages, tools) -> ChatResult:
        assert self.script, f"FakeChat script exhausted at call {len(self.calls)}"
        r = self.script.pop(0)
        if callable(r):
            r = r(messages, tools)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, str):
            return ChatResult(r, finish_reason="stop")
        if isinstance(r, dict):
            valid = {(t.get("function") or t)["name"] for t in tools} if tools else None
            return ChatResult(toolparse.strip_thinking(r.get("content") or ""), toolparse.parse(r, valid), r.get("reasoning_content") or "", "stop")
        return r

    def complete(self, messages, tools=None, schema=None, effort=None, think=None, stream=False, **extra):
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        self.calls.append({"messages": messages, "tools": tools, "schema": schema, "effort": effort, "think": think, **extra})
        r = self._next(messages, tools)
        return self._stream_of(r) if stream else r

    @staticmethod
    def _stream_of(r: ChatResult):
        if r.reasoning:
            yield "reasoning", r.reasoning
        for w in re.findall(r"\S+\s*", r.text):
            yield "text", w
        yield "done", r

    def stream(self, messages, tools=None, schema=None, effort=None, think=None, **extra):
        return self.complete(messages, tools, schema, effort, think, stream=True, **extra)

    def __call__(self, prompt, **kw) -> str:
        return self.complete(prompt, **kw).text


class FakeEmbedder:
    def __init__(self, dim: int = 64, model: str = "fake-embed", query_prefix: str = ""):
        self.dim, self.model, self.query_prefix, self.calls = dim, model, query_prefix, []

    def _vec(self, text: str) -> list:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts, is_query: bool = False):
        one = isinstance(texts, str)
        items = [texts] if one else list(texts)
        self.calls.append({"n": len(items), "is_query": is_query})
        vecs = [self._vec((self.query_prefix if is_query else "") + t) for t in items]
        return vecs[0] if one else vecs


def _selfcheck():
    f = FakeChat(["hello", tool_call("get_time", tz="UTC"), {"content": "<think>x</think>done", "tool_calls": []}, lambda m, t: f"saw {len(m)}", ValueError("down")])
    assert f("hi") == "hello"
    r = f.complete([{"role": "user", "content": "time?"}], tools=[{"name": "get_time"}])
    assert r.tool_calls[0].args == {"tz": "UTC"}
    assert f.complete("x").text == "done"
    assert f.complete("x").text == "saw 1" and f.calls[1]["tools"][0]["name"] == "get_time"
    try:
        f.complete("x")
        raise AssertionError
    except ValueError:
        pass
    try:
        f.complete("x")
        raise AssertionError
    except AssertionError as ex:
        assert "exhausted" in str(ex)
    s = list(FakeChat(["one two"]).stream("x"))
    assert [k for k, _ in s] == ["text", "text", "done"] and s[-1][1].text == "one two"
    e = FakeEmbedder()
    a, b, c = e.embed(["turn on the lights", "turn off the lights", "weather in paris"])
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))
    assert dot(a, b) > 0.5 > dot(a, c) and abs(dot(a, a) - 1) < 1e-9 and len(e.embed("x")) == 64
    print("fake ok")


if __name__ == "__main__":
    _selfcheck()
