"""Text front end for the engine: type a request, see the reply and exactly what ran. The P3 way to talk to FRED before the voice output exists.

  python engine/cli.py --live [--brain MODEL] [--no-embed] [--keep] [--memory]       lines from stdin (interactive or piped); empty line or Ctrl+D quits
Loads the chat (and embedding) model into the playground if its slot is empty, and unloads what IT loaded on exit unless --keep.
--memory attaches the memory: turns are archived under <data_dir>/memory, recall joins the prompt, the memory tools exist (needs the embed slot).
Each reply is followed by a trace line: how it was routed (chat / tools / say / confirm / fault), the tools offered and called with their outcome, the rounds and the time.
Sockets: engine.loop.Engine over tools.builtin.build_registry(), models.client.Chat/Embedder. `run_lines(engine, lines, out)` is the testable core.
Self-check: python engine/cli.py (no --live: scripted model)
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from engine.loop import Engine


def trace(r) -> str:
    calls = ", ".join(f"{c['name']}({'ok' if c['ok'] else c['error_kind']})" for c in r.calls) or "-"
    return f"  [{r.via} | offered {len(r.offered)} | called {calls} | {r.rounds} round(s) | {r.ms} ms | {r.reason}]"


def run_lines(engine: Engine, lines, out=print):
    for line in lines:
        line = line.strip()
        if not line:
            break
        r = engine.run(line, source="cli")
        out(f"FRED: {r.text}")
        out(trace(r))


def _ensure(pg, model: str, slot: str, loaded: list, **kw):
    cur = (pg.status().get("slots") or {}).get(slot)
    if not cur:
        print(f"loading {model} into the {slot} slot ...")
        pg.load(model, slot=slot, **kw)
        loaded.append(slot)


def main(argv):
    from engine import prompt
    from models.client import Chat, Embedder, Playground
    from tools.builtin import build_registry
    brain = argv[argv.index("--brain") + 1] if "--brain" in argv else config.get().brain
    pg, loaded = Playground(), []
    if not pg.healthy():
        print("the playground is not running (python Mark_I/Initial/playground/server.py)")
        return 1
    _ensure(pg, brain, "chat", loaded)
    emb = None
    if "--no-embed" not in argv:
        _ensure(pg, config.get().embedder, "embed", loaded)
        emb = Embedder()
    bus, feed, mem, recall = None, None, None, None
    if "--memory" in argv:
        import events as ev
        from memory_bridge.feed import Feed
        from memory_bridge.link import open_memory
        from memory_bridge.turn_recall import MemoryRecall
        bus, mem, chat = ev.EventBus(), open_memory(), Chat(brain)
        recall, feed = MemoryRecall(mem, emb, chat, config.get().embedder), Feed(mem, bus)
        feed.start()
    e = Engine(Chat(brain), build_registry(recall=recall), emb, prompt.build_system(), bus=bus, recall=recall, on_say=lambda t: print(f"FRED (interim): {t}"))
    try:
        print("type a request (empty line quits)")
        run_lines(e, (l for l in sys.stdin))
    finally:
        feed and feed.stop()
        mem and mem.close()
        if loaded and "--keep" not in argv:
            for s in loaded:
                pg.unload(s)
    return 0


def _selfcheck():
    import tempfile
    from models.fake import FakeChat, tool_call
    from tools.builtin import build_registry
    config.set_data_dir(tempfile.mkdtemp())
    e = Engine(FakeChat([tool_call("get_current_time"), "Hello."]), build_registry())
    seen = []
    run_lines(e, ["what time is it", "hello there", "", "never reached"], seen.append)
    assert len(seen) == 4 and seen[0].startswith("FRED: It's ") and "say" in seen[1] and "get_current_time(ok)" in seen[1] and seen[2] == "FRED: Hello."
    print("cli ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) if "--live" in sys.argv else _selfcheck())
