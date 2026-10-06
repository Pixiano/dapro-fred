"""Live P0 gate: the client against a REAL playground (GPU). Not a unit test; run it by hand, one run at a time.

  python tests/live_playground.py [chat-model-id]      default unsloth/qwen3.5-4b
Playground address: FRED_PLAYGROUND_URL (default http://localhost:8090). Needs a playground running the P0 server.py (slots chat/embed/talk/vision, /api/window).
Checks: chat text, schema-forced JSON, a tool call, streaming, embeddings, a second slot (talk) alongside chat, vision (a generated red square),
the unload/reload window (stack unloaded, job model answers, stack restored). Leaves every slot empty at the end.
Self-contained: no memory, no personal data; images and prompts are invented.
"""
import os
import struct
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from models.client import Chat, Embedder, Playground, image_part

MODEL = sys.argv[1] if len(sys.argv) > 1 else "unsloth/qwen3.5-4b"
JOB_MODEL = os.environ.get("FRED_JOB_MODEL", "qwen/qwen3-8b")
URL = os.environ.get("FRED_PLAYGROUND_URL") or config.get().playground_url
CTX = 4096


def png(rgb, size=64) -> bytes:
    """A solid-colour PNG without any imaging library."""
    row = b"\x00" + bytes(rgb) * size
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b"")


def step(name, fn):
    t = time.time()
    out = fn()
    print(f"ok  {name:<34} {time.time() - t:5.1f}s  {out if out is not None else ''}", flush=True)


def main():
    pg = Playground(URL)
    assert pg.healthy(), f"no playground at {URL}"
    st = pg.status()
    assert set(st["slots"]) >= {"chat", "embed", "talk", "vision"}, f"old playground server (slots {list(st['slots'])}): restart it with the P0 server.py"
    assert not any(st["slots"].values()), "slots are not empty: another run is using the playground, stop and retry later"
    chat = Chat(MODEL, url=URL, temperature=0.2, max_tokens=300)

    step("load chat", lambda: pg.load(MODEL, ctx=CTX)["slots"]["chat"]["model"])
    step("chat text", lambda: chat.complete("Reply with just the word: pong", think=False).text[:40])
    schema = {"type": "object", "title": "colour", "properties": {"colour": {"type": "string"}, "warm": {"type": "boolean"}}, "required": ["colour", "warm"], "additionalProperties": False}

    def forced():
        j = chat.complete("Give one colour and whether it is warm.", schema=schema, think=False).json()
        assert isinstance(j["colour"], str) and isinstance(j["warm"], bool)
        return j
    step("schema-forced JSON", forced)
    tool = {"name": "get_time", "description": "Current time in a timezone", "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": ["timezone"]}}

    def tool_call():
        r = chat.complete("What time is it in UTC? Use the tool.", tools=[tool], think=False)
        assert r.tool_calls and r.tool_calls[0].name == "get_time" and r.tool_calls[0].error is None, r
        return r.tool_calls[0].args
    step("tool call parsed", tool_call)

    def streamed():
        ev = list(chat.stream("Count from 1 to 5, separated by spaces.", think=False))
        assert ev[-1][0] == "done" and sum(1 for k, _ in ev if k == "text") > 1
        return ev[-1][1].text[:30]
    step("streaming", streamed)

    emb = Embedder(config.get().embedder, "Instruct: Given a search query, retrieve relevant notes\nQuery: ", url=URL)

    def embeds():
        a, b, c = emb.embed(["turn the lights off", "switch off the lamp", "capital of France"])
        dot = lambda x, y: sum(p * q for p, q in zip(x, y)) / ((sum(p * p for p in x) * sum(q * q for q in y)) ** 0.5)
        assert dot(a, b) > dot(a, c) and len(emb.embed("x", is_query=True)) == len(a)
        return f"dim {len(a)}, near {dot(a, b):.2f} far {dot(a, c):.2f}"
    step("embeddings", embeds)

    talk = Chat(MODEL, slot="talk", url=URL, max_tokens=60)

    def second_slot():
        pg.load(MODEL, slot="talk", ctx=CTX)
        s = pg.status()["slots"]
        assert s["chat"] and s["talk"] and s["chat"]["pid"] != s["talk"]["pid"]
        return talk.complete("Say hello in two words.", think=False).text[:30]
    step("talk slot beside chat", second_slot)
    step("unload talk", lambda: pg.unload("talk")["slots"]["talk"])

    def vision():
        pg.load(MODEL, slot="vision", ctx=CTX, vision=True)
        r = Chat(MODEL, slot="vision", url=URL, max_tokens=30).complete(
            [{"role": "user", "content": [{"type": "text", "text": "What single colour is this image? One word."}, image_part(png((255, 0, 0)))]}], think=False)
        assert "red" in r.text.lower(), r.text
        return r.text.strip()
    step("vision (red square)", vision)
    step("unload vision", lambda: pg.unload("vision")["slots"]["vision"])

    def window():
        before = {k: v["model"] for k, v in pg.status()["slots"].items() if v}
        assert pg.window_open()["window_open"] and not any(pg.status()["slots"].values())
        r = Chat(JOB_MODEL, url=URL, max_tokens=40).complete("Reply with just: done", think=False).text      # a job model gets the whole GPU
        pg.window_close()
        after = {k: v["model"] for k, v in pg.status()["slots"].items() if v}
        assert after == before and not pg.status()["window_open"], (before, after)
        return f"job said {r.strip()[:20]!r}; restored {sorted(after)}"
    step("window open -> job -> close", window)
    pg.unload()
    print("live P0 gate passed; all slots empty")


if __name__ == "__main__":
    main()
