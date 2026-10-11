"""Model client for the playground (OpenAI-compatible llama-server proxy): chat, embeddings, vision, load/unload, health. Stdlib only.

Sockets
  in : Chat(model).complete(messages|prompt, tools=None, schema=None, effort=None, think=None, ...) -> ChatResult
       Chat(model).stream(...) -> iterator of ("text"|"reasoning", str) then ("done", ChatResult)
       Chat(model)(prompt|messages) -> text          the memory side's `llm(prompt)` shape
       Embedder(model).embed(texts, is_query=False) -> list[vector]
       image_part(path|bytes) -> a message content part, for vision models (messages=[{"role":"user","content":[{"type":"text",...}, image_part(p)]}])
       Playground().status() / healthy() / load(model, slot=None, ctx=None, vision=False) / unload(slot=None) / window_open() / window_close()
       vision_slot(brain=None) -> "chat" when settings.vision_shared and describe_model is the brain (one copy, loaded with the image projector), else "vision"
  out: ChatResult(text, tool_calls (toolparse.ToolCall list), reasoning, finish_reason, usage); never a raw HTTP error: ModelError(kind, message)
       kinds: unavailable (playground down), timeout, bad_request (4xx), failed (5xx / unreadable reply)
Same call shapes as models/fake.py (FakeChat, FakeEmbedder), so everything above the model runs unchanged with no GPU.
`slot` picks the playground slot (chat, embed, talk, vision); without it the playground routes by where the model is loaded, else by its kind.
Self-check: python models/client.py   (no GPU: against a local stub server; the real round trip is in tests/live_playground.py)
"""
import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from models import toolparse


class ModelError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message = kind, message


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    reasoning: str = ""
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)

    def json(self):
        """The reply parsed as JSON (for schema-forced calls)."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as ex:
            raise ModelError("failed", f"reply is not JSON ({ex}): {self.text[:80]!r}")


def image_part(src, mime: str | None = None) -> dict:
    data = Path(src).read_bytes() if isinstance(src, (str, Path)) else bytes(src)
    mime = mime or (mimetypes.guess_type(str(src))[0] if isinstance(src, (str, Path)) else None) or "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def vision_slot(brain: str | None = None) -> str:
    """Where image requests go. Shared: the chat slot, whose brain is loaded with vision=True (callers: `vision=slot == "chat" and vision_slot(model) == "chat"`)."""
    c = config.get()
    return "chat" if c.vision_shared and c.describe_model == (brain or c.brain) else "vision"


class _Http:
    def __init__(self, url: str | None = None, timeout: float | None = None, retries: int | None = None):
        c = config.get()
        self.base = (url or c.playground_url).rstrip("/")
        self.timeout = c.http_timeout_s if timeout is None else timeout
        self.retries = c.http_retries if retries is None else retries

    def open(self, path: str, body: dict | None = None):
        """Response object (caller closes). Retries connection errors only: a 4xx/5xx is an answer, not a flake."""
        req = urllib.request.Request(self.base + path, None if body is None else json.dumps(body).encode(), {"Content-Type": "application/json"})
        for attempt in range(self.retries + 1):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as ex:
                msg = ex.read().decode("utf-8", "replace")[:300]
                try:
                    msg = json.loads(msg).get("error", msg)
                    msg = msg.get("message", msg) if isinstance(msg, dict) else msg
                except (json.JSONDecodeError, AttributeError):
                    pass
                raise ModelError("bad_request" if ex.code < 500 else "failed", f"HTTP {ex.code}: {msg}")
            except TimeoutError:
                raise ModelError("timeout", f"no answer in {self.timeout}s from {path}")
            except OSError as ex:
                if isinstance(getattr(ex, "reason", None), TimeoutError):
                    raise ModelError("timeout", f"no answer in {self.timeout}s from {path}")
                if attempt == self.retries:
                    raise ModelError("unavailable", f"playground at {self.base} not reachable ({ex})")
                time.sleep(1)

    def json(self, path: str, body: dict | None = None):
        with self.open(path, body) as r:
            try:
                return json.load(r)
            except json.JSONDecodeError as ex:
                raise ModelError("failed", f"unreadable reply from {path}: {ex}")


class Playground(_Http):
    def status(self) -> dict:
        return self.json("/api/status")

    def healthy(self) -> bool:
        try:
            self.status()
            return True
        except ModelError:
            return False

    def models(self) -> list:
        return self.json("/v1/models")["data"]

    def load(self, model: str, slot: str | None = None, ctx: int | None = None, vision: bool = False) -> dict:
        return self.json("/api/load", {k: v for k, v in {"model": model, "slot": slot, "ctx": ctx, "vision": vision or None}.items() if v is not None})

    def unload(self, slot: str | None = None) -> dict:
        return self.json("/api/unload", {"slot": slot or "all"})

    def window_open(self) -> dict:
        """Unload the live stack (remembered by the playground) so a job model can use the whole GPU."""
        return self.json("/api/window", {"action": "open"})

    def window_close(self) -> dict:
        """Unload the job model(s) and reload the stack remembered at window_open."""
        return self.json("/api/window", {"action": "close"})


class Chat(_Http):
    def __init__(self, model: str | None = None, slot: str | None = None, url=None, timeout=None, retries=None, **defaults):
        super().__init__(url, timeout, retries)
        self.model, self.slot, self.defaults = model or config.get().brain or None, slot, defaults   # defaults: temperature, max_tokens, top_p, ...

    def _body(self, messages, tools, schema, effort, think, stream, extra) -> dict:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        b = {**self.defaults, **extra, "messages": messages, "stream": stream}
        if self.model:
            b["model"] = self.model
        if self.slot:
            b["slot"] = self.slot
        if tools:
            b["tools"] = [t if "function" in t else {"type": "function", "function": t} for t in tools]
        if schema:
            b["response_format"] = {"type": "json_schema", "json_schema": {"name": schema.get("title", "reply"), "strict": True, "schema": schema}}
        kw = dict(b.get("chat_template_kwargs") or {})
        if effort:
            kw["reasoning_effort"] = effort                   # gpt-oss style
        if think is not None:
            kw["enable_thinking"] = bool(think)               # Qwen3 style: False = no reasoning, the thinking cap
        if kw:
            b["chat_template_kwargs"] = kw
        return b

    @staticmethod
    def _result(choice: dict, usage: dict, tools) -> ChatResult:
        m = choice.get("message") or {}
        valid = {(t.get("function") or t)["name"] for t in tools} if tools else None
        calls = toolparse.parse(m, valid)
        return ChatResult(toolparse.strip_thinking(m.get("content") or ""), calls, m.get("reasoning_content") or "", choice.get("finish_reason") or "", usage or {})

    def complete(self, messages, tools=None, schema=None, effort=None, think=None, stream=False, **extra):
        if stream:
            return self.stream(messages, tools, schema, effort, think, **extra)
        r = self.json("/v1/chat/completions", self._body(messages, tools, schema, effort, think, False, extra))
        if not r.get("choices"):
            raise ModelError("failed", "reply has no choices")
        return self._result(r["choices"][0], r.get("usage"), tools)

    def stream(self, messages, tools=None, schema=None, effort=None, think=None, **extra):
        text, reasoning, calls, finish, usage = [], [], {}, "", {}
        with self.open("/v1/chat/completions", self._body(messages, tools, schema, effort, think, True, extra)) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    ch = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                usage = ch.get("usage") or usage
                for c in ch.get("choices") or []:
                    d = c.get("delta") or {}
                    if d.get("content"):
                        text.append(d["content"])
                        yield "text", d["content"]
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                        yield "reasoning", d["reasoning_content"]
                    for tc in d.get("tool_calls") or []:   # arguments arrive in fragments, keyed by index
                        slot = calls.setdefault(tc.get("index", 0), {"id": "", "function": {"name": "", "arguments": ""}})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
                    finish = c.get("finish_reason") or finish
        msg = {"content": "".join(text), "reasoning_content": "".join(reasoning), "tool_calls": [calls[k] for k in sorted(calls)]}
        yield "done", self._result({"message": msg, "finish_reason": finish}, usage, tools)

    def __call__(self, prompt, **kw) -> str:
        return self.complete(prompt, **kw).text


class Embedder(_Http):
    def __init__(self, model: str | None = None, query_prefix: str = "", url=None, timeout=None, retries=None):
        super().__init__(url, timeout, retries)
        self.model, self.query_prefix = model or config.get().embedder, query_prefix   # prefix is model-specific (Qwen3-Embedding wants an instruction)

    def embed(self, texts, is_query: bool = False) -> list:
        one = isinstance(texts, str)
        items = [texts] if one else list(texts)
        if is_query:
            items = [self.query_prefix + t for t in items]
        r = self.json("/v1/embeddings", {"model": self.model, "input": items})
        vecs = [d["embedding"] for d in sorted(r["data"], key=lambda d: d.get("index", 0))]
        if len(vecs) != len(items):
            raise ModelError("failed", f"asked for {len(items)} embeddings, got {len(vecs)}")
        return vecs[0] if one else vecs


def _selfcheck():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200, sse=False):
            self.send_response(code)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
            self.end_headers()
            self.wfile.write(obj if sse else json.dumps(obj).encode())

        def do_GET(self):
            self._send({"slots": {"chat": None}})

        def do_POST(self):
            b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Stub.last = b
            if self.path == "/v1/embeddings":
                return self._send({"data": [{"index": i, "embedding": [float(i), 1.0]} for i, _ in reversed(list(enumerate(b["input"])))]})
            if b["messages"][0]["content"] == "boom":
                return self._send({"error": {"message": "nope"}}, 400)
            if b["stream"]:
                ev = [{"choices": [{"delta": {"reasoning_content": "hm"}}]}, {"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
                      {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_time", "arguments": '{"tz":'}}]}}]},
                      {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"UTC"}'}}]}, "finish_reason": "tool_calls"}]}]
                return self._send(("".join(f"data: {json.dumps(e)}\n\n" for e in ev) + "data: [DONE]\n\n").encode(), sse=True)
            msg = {"content": '{"ok": true}' if b.get("response_format") else "<think>x</think>Hi", "tool_calls": [{"function": {"name": "get_time", "arguments": "{}"}}]}
            self._send({"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"total_tokens": 5}})

    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    assert Playground(url).healthy()
    c = Chat("m", slot="talk", url=url, temperature=0.2)
    r = c.complete("hi", tools=[{"name": "get_time", "parameters": {}}], think=False, effort="low")
    assert r.text == "Hi" and r.tool_calls[0].name == "get_time" and r.usage["total_tokens"] == 5
    sent = Stub.last
    assert sent["slot"] == "talk" and sent["temperature"] == 0.2 and sent["chat_template_kwargs"] == {"reasoning_effort": "low", "enable_thinking": False}
    assert sent["tools"][0]["type"] == "function"
    assert c.complete("hi", schema={"type": "object", "title": "t"}).json() == {"ok": True}
    assert Stub.last["response_format"]["json_schema"]["strict"] is True
    ev = list(c.complete("hi", tools=[{"name": "get_time"}], stream=True))
    assert [k for k, _ in ev][:3] == ["reasoning", "text", "text"]
    done = ev[-1][1]
    assert done.text == "Hello" and done.reasoning == "hm" and done.tool_calls[0].args == {"tz": "UTC"} and done.finish_reason == "tool_calls"
    try:
        c("boom")
        raise AssertionError
    except ModelError as ex:
        assert ex.kind == "bad_request" and "nope" in ex.message
    assert Embedder("e", "Q: ", url=url).embed(["a", "b"], is_query=True) == [[0.0, 1.0], [1.0, 1.0]] and Stub.last["input"] == ["Q: a", "Q: b"]   # restored to input order
    srv.shutdown()
    srv.server_close()
    try:
        Chat("m", url=url, retries=0)("hi")
        raise AssertionError
    except ModelError as ex:
        assert ex.kind == "unavailable"
    assert not Playground(url, retries=0).healthy()
    assert image_part(b"\x89PNG")["image_url"]["url"].startswith("data:image/png;base64,")
    c = config.get()
    assert vision_slot(c.describe_model) == ("chat" if c.vision_shared else "vision") and vision_slot("some/other-model") == "vision"   # shared only when the model is the describer
    print("client ok")


if __name__ == "__main__":
    _selfcheck()
