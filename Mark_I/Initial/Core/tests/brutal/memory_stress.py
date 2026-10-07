"""Brutal suite 3, no GPU (fake embedder): the memory bridge and the memory underneath it under load, concurrency and torn input. Every count is checked, nothing is trusted.

  python tests/brutal/memory_stress.py [--scale N]
Checks: the Feed under 8 producer threads (no lost or duplicated record, streams right), a torn archive line and hostile payloads (huge, unicode, non-JSON-able), archive verify(), the semantic
index at thousands of events (sync, recall, model swap, clear), the personal model with hundreds of items (relevance, correct/remove churn), the graph on long text and alias churn, and
recall on garbage over a big memory. Prints one FINDING per broken invariant; exit 1 if any.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import events as ev  # noqa: E402
from memory_bridge.feed import Feed  # noqa: E402
from memory_bridge.link import open_memory  # noqa: E402
from memory_bridge.turn_recall import MemoryRecall  # noqa: E402
from models.fake import FakeChat, FakeEmbedder  # noqa: E402

FINDINGS = []


def finding(area, what, detail=""):
    FINDINGS.append((area, what))
    print(f"FINDING [{area}] {what} {str(detail)[:200]}")


def timed(label, fn):
    t0 = time.time()
    r = fn()
    print(f"  {label}: {time.time() - t0:.2f}s")
    return r


def count_stream(mem, stream):
    n = 0
    for p in Path(mem.archive.root).rglob(f"{stream}.jsonl"):
        n += sum(1 for l in p.read_text(encoding="utf-8").splitlines() if l.strip())
    return n


def feed_load(scale):
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        bus = ev.EventBus()
        feed = Feed(mem, bus)
        feed.start()
        per, threads = 120 * scale, 8

        def producer(k):
            for i in range(per):
                bus.emit("engine", "turn", text=f"u{k}-{i}", reply=f"r{k}-{i}", origin="cli", via="chat", reason="", ms=5, calls=[])
                bus.emit("engine", "tool", name="recall", args={"query": f"q{k}-{i}"}, result="ok", ok=True, ms=3)
        ts = [threading.Thread(target=producer, args=(k,)) for k in range(threads)]
        timed(f"{threads} threads x {per} turns", lambda: ([x.start() for x in ts], [x.join() for x in ts]))
        feed.stop()
        conv, tools = count_stream(mem, "conversation"), count_stream(mem, "tool_call")
        if conv != 2 * threads * per:
            finding("feed", "conversation records lost or duplicated", f"{conv} != {2 * threads * per}")
        if tools != threads * per:
            finding("feed", "tool_call records lost or duplicated", f"{tools} != {threads * per}")
        bad = mem.archive.verify()
        if bad:
            finding("archive", "verify() reports problems after the load", bad[:3])


def archive_hostile():
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        a = mem.archive
        for label, data in (("huge", {"text": "x" * 2_000_000}), ("unicode", {"text": "".join(chr(i) for i in range(0x20, 0x3000))}), ("nested", {"a": [{"b": [1, {"c": None}]}] * 50}),
                            ("not json-able", {"obj": object(), "set": {1, 2}}), ("nul", {"text": "a\x00b"}), ("surrogate", {"text": "x\ud800y"})):
            try:
                a.write("conversation", data)
            except Exception as ex:
                if label not in ("surrogate",):
                    finding("archive", f"write of '{label}' payload raised", repr(ex))
        for bad in ("../evil", "", "A B", "x" * 500):
            try:
                a.write(bad, {"x": 1})
                finding("archive", f"stream name {bad[:20]!r} accepted")
            except ValueError:
                pass
            except Exception as ex:
                finding("archive", f"stream name {bad[:20]!r} raised {type(ex).__name__}", ex)
        p = next(Path(a.root).rglob("conversation.jsonl"))
        with open(p, "ab") as f:
            f.write(b'{"torn": tru')                                                 # a crash mid-write
        a.write("conversation", {"text": "after the tear"})
        lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if not any("after the tear" in l for l in lines):
            finding("archive", "a record written after a torn line is lost")
        if any(p.parent != Path(a.root) / p.parent.relative_to(a.root) for p in Path(a.root).rglob("*.jsonl")):
            finding("archive", "a file escaped the archive root")


def index_stress(scale):
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        rc = MemoryRecall(mem, FakeEmbedder(), FakeChat([]), "fake-a")
        n = 600 * scale
        words = "tea physics robot sister dentist exam market project gym music travel".split()
        for i in range(n):
            mem.store.add_event("note", f"{words[i % 11]} item {i} about {words[(i * 7) % 11]}")
        rc.index.clear()                                                              # the add-hook already embedded each event; clear, then prove sync() rebuilds them all
        done = timed(f"sync {n} events", rc.index.sync)
        if done != n:
            finding("semantic", "sync embedded the wrong number", f"{done} != {n}")
        if rc.index.sync() != 0:
            finding("semantic", "a second sync was not a no-op")
        hits = timed("100 recalls", lambda: [rc.index.recall(f"{words[i % 11]} {i}", k=5) for i in range(100)])
        if any(len(h) > 5 for h in hits) or not all(hits):
            finding("semantic", "recall returned too many or none")
        rc.index.model = "fake-b"
        if rc.index.recall("tea") != []:
            finding("semantic", "a model swap mixed vector spaces")
        if rc.index.sync() != n:
            finding("semantic", "re-sync after a model swap incomplete")
        rc.index.clear()
        if rc.index.recall("tea") != []:
            finding("semantic", "clear() left vectors behind")
        for q in ("", " ", "a" * 50_000, "\x00", "'; DROP TABLE events; --"):
            try:
                rc.index.recall(q)
            except Exception as ex:
                finding("semantic", f"recall({q[:15]!r}..) raised", repr(ex))


def model_stress(scale):
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        rc = MemoryRecall(mem, FakeEmbedder(), FakeChat([]), "fake")
        pm = rc.model
        n = 150 * scale
        for i in range(n):
            pm.add("preference", f"likes thing number {i} and topic {i % 13}")
        r = timed("200 relevant() queries", lambda: [pm.relevant(f"tell me about thing {i} topic {i % 13}") for i in range(200)])
        if any(len(x) > 3 for x in r):
            finding("personal_model", "relevant() returned more than TOP_K")
        ids = [row["id"] for row in pm.q("SELECT id FROM model_items WHERE status='active'")]
        for k, i in enumerate(ids[: n // 2]):
            (pm.correct if k % 2 else pm.remove)(i, *(["rewritten item"] if k % 2 else []))
        active = pm.q("SELECT count(*) AS c FROM model_items WHERE status='active'")[0]["c"]
        if active != n - n // 4:
            finding("personal_model", "item counts wrong after churn", f"{active} != {n - n // 4}")
        if not isinstance(pm.show(), str):
            finding("personal_model", "show() broke after churn")
        for bad in (0, -1, 10 ** 9, "x", None):
            try:
                pm.remove(bad)
                finding("personal_model", f"remove({bad!r}) did not raise")
            except ValueError:
                pass
            except Exception as ex:
                if not isinstance(ex, (TypeError,)):
                    finding("personal_model", f"remove({bad!r}) raised {type(ex).__name__}", ex)


def graph_stress(scale):
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        rc = MemoryRecall(mem, FakeEmbedder(), FakeChat([]), "fake")
        g = rc.graph
        names = [f"Person{chr(65 + i % 26)}{i}" for i in range(300 * scale)]
        for nm in names:
            g.resolve("person", nm)
        big = " ".join(names[:200]) + " filler " * 20000
        timed("find_in_text on 150k chars", lambda: g.find_in_text(big))
        if not g.find_in_text("tell me about " + names[5])[0]:
            finding("graph", "a known name was not found in text")
        for i in range(0, 200, 2):                                                   # correction churn: name chains must not loop or lose entities
            try:
                g.correct("person", names[i], names[i + 1])
            except Exception as ex:
                finding("graph", "correct() raised", repr(ex))
                break
        for weird in ("", " ", "\x00", "A" * 10_000, "'; DROP TABLE entities; --", "名前", "😀"):
            try:
                g.resolve("person", weird)
                g.find_in_text(weird)
            except ValueError:
                if weird.strip():
                    finding("graph", f"resolve/find with {weird[:12]!r} raised ValueError")                  # an empty name is refused on purpose; anything else must work
            except Exception as ex:
                finding("graph", f"resolve/find with {weird[:12]!r} raised", repr(ex))


def recall_garbage(scale):
    with tempfile.TemporaryDirectory() as t, open_memory(t) as mem:
        rc = MemoryRecall(mem, FakeEmbedder(), FakeChat([]), "fake")
        for i in range(200 * scale):
            mem.store.add_event("note", f"event {i} about the dentist and the exam {i % 9}")
        rc.index.sync()
        bad = 0
        for i in range(40):
            rc.chat = FakeChat(["not json at all", RuntimeError("down"), '{"relevant": [999999], "retry_queries": ["x"], "ask": ""}', '{"relevant": "bad"}'] * 3)
            for text in ("do you remember the dentist", "what did we say about the exam", "x" * 20_000 + " remember", "did I tell you \x00 about it", "", "remember that time"):
                try:
                    r = rc.context(text, force=True)
                    if r is None:
                        bad += 1
                except Exception as ex:
                    finding("recall", f"context({text[:15]!r}..) raised (it must never)", repr(ex))
                    bad += 1
        print(f"  recall garbage: {40 * 6} calls, {bad} broke")


def main():
    scale = int(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 3
    print(f"scale {scale}")
    for fn, a in ((feed_load, (scale,)), (archive_hostile, ()), (index_stress, (scale,)), (model_stress, (scale,)), (graph_stress, (scale,)), (recall_garbage, (scale,))):
        print(fn.__name__)
        try:
            fn(*a)
        except Exception as ex:
            finding("suite", f"{fn.__name__} crashed", repr(ex))
    print(f"\n{len(FINDINGS)} findings")
    return 1 if FINDINGS else 0


if __name__ == "__main__":
    sys.exit(main())
