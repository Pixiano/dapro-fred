"""P3 gate: every rule of the tool loop, against a scripted model (no GPU, no playground).

  python tests/test_engine.py
One scenario per rule in engine/loop.py's docstring. A rule that stops holding fails here, not in a live conversation.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from engine.loop import HONEST_NOTHING_RAN, Engine
from models.client import ChatResult
from models.fake import FakeChat, tool_call
from models.toolparse import ToolCall
from tools.registry import Registry, ToolError, ToolSpec


def calls(*cs):
    return ChatResult(tool_calls=[ToolCall(n, a, f"c{i}") for i, (n, a) in enumerate(cs)], finish_reason="tool_calls")


class Rig:
    def __init__(self, script, sleep=0.0):
        self.n = {"flaky": 0, "slow_a": 0, "slow_b": 0, "wipe": 0, "mail": 0, "page": 0, "vol": 0, "time": 0}
        r = self.reg = Registry()

        def bump(k):
            self.n[k] += 1

        def flaky(x: int = 0):
            bump("flaky")
            raise ToolError("not_found", "I can't find that.", f"nothing for {x}")

        def slow(k):
            def f():
                bump(k)
                time.sleep(sleep)
                return k
            return f
        r.register(ToolSpec("flaky", flaky, {"type": "object", "properties": {"x": {"type": "integer"}}}, "flaky thing", group="f", cues=("flaky",)))
        r.register(ToolSpec("slow_a", slow("slow_a"), description="a", group="s", cues=("slowly",)))
        r.register(ToolSpec("slow_b", slow("slow_b"), description="b", group="s"))
        r.register(ToolSpec("wipe", lambda path="": bump("wipe") or "wiped", {"type": "object", "properties": {"path": {"type": "string"}}}, "delete", group="w", cues=("wipe",), destructive=True))
        r.register(ToolSpec("page", lambda: bump("page") or "page text", description="web", group="net", cues=("browse",), reads_untrusted=True))
        r.register(ToolSpec("mail", lambda: bump("mail") or "sent", description="send", group="net", sends=True))
        r.register(ToolSpec("vol", lambda: bump("vol") or "Volume 30.", description="volume", group="audio", cues=("volume",), say=True))
        r.register(ToolSpec("time", lambda: bump("time") or "It is 10:00.", description="time", group="clock", cues=("what time",), say=True))
        self.chat = FakeChat(script)
        self.e = Engine(self.chat, r)


def setup():
    config.set_data_dir(tempfile.mkdtemp())


def t_identical_failure_refused():
    g = Rig([tool_call("flaky", x=1), tool_call("flaky", x=1), "I couldn't find that."])
    r = g.e.run("run the flaky thing")
    assert g.n["flaky"] == 1 and r.text == "I couldn't find that.", (g.n, r)
    tool_msgs = [m["content"] for m in g.chat.calls[2]["messages"] if m["role"] == "tool"]
    assert "repeat of a call that already failed" in tool_msgs[1] and "nothing for 1" in tool_msgs[1]


def t_two_failures_stop_the_tool():
    g = Rig([tool_call("flaky", x=1), tool_call("flaky", x=2), tool_call("flaky", x=3), "Sorry, that isn't working."])
    r = g.e.run("run the flaky thing")
    assert g.n["flaky"] == 2, g.n                                                              # the third call never ran
    last = [m["content"] for m in g.chat.calls[3]["messages"] if m["role"] == "tool"][-1]
    assert "fault" in last and "stopped" in last and r.text == "Sorry, that isn't working."


def t_chain_offer_widens_to_the_group():
    g = Rig([tool_call("slow_b"), "ok done."])
    g.e.run("do it slowly")                                                                    # cue offers group s (slow_a, slow_b): slow_b is in it
    g = Rig([tool_call("vol"), "Volume is 30."])                                               # vol is outside the `f` menu
    r = g.e.run("run the flaky thing")
    assert g.n["vol"] == 1 and "vol" in r.offered and "flaky" in r.offered, r
    g = Rig([tool_call("slow_a"), tool_call("page"), "Done, nothing more."])                   # a later round reaching outside offers that group's tools to the next call
    g.e.run("do it slowly")
    assert {"page", "mail"} <= {t["function"]["name"] for t in g.chat.calls[2]["tools"]}


def t_parallel_and_duplicates():
    g = Rig([calls(("slow_a", {}), ("slow_b", {}), ("slow_a", {})), "both done."], sleep=0.4)
    t0 = time.monotonic()
    r = g.e.run("do it slowly")
    dt = time.monotonic() - t0
    assert dt < 0.75 and g.n["slow_a"] == 1 and g.n["slow_b"] == 1, (dt, g.n)                  # two calls ran together, the duplicate once
    assert len([m for m in g.chat.calls[1]["messages"] if m["role"] == "tool"]) == 3 and r.text == "both done."


def t_destructive_needs_yes():
    g = Rig([tool_call("wipe", path="a.txt"), "never used"])
    r = g.e.run("wipe a.txt")
    assert r.via == "confirm" and g.n["wipe"] == 0 and "wipe" in r.text and "a.txt" in r.text
    r = g.e.run("yes")
    assert g.n["wipe"] == 1 and r.text == "wiped" and len(g.chat.script) == 1                  # ran without another model call
    g = Rig([tool_call("wipe", path="a.txt"), "Sure, tell me more."])
    g.e.run("wipe a.txt")
    assert g.e.run("no").text == "Okay, cancelled." and g.n["wipe"] == 0
    g = Rig([tool_call("wipe", path="a.txt"), "It is a chat answer."])
    g.e.run("wipe a.txt")
    g.e.run("hmm, tell me a story")                                                            # anything else drops the question
    assert g.e.confirm.pending is None and g.n["wipe"] == 0


def t_read_then_send_refused():
    g = Rig([tool_call("page"), tool_call("mail"), "I can't send that."])
    r = g.e.run("browse and mail it")
    assert g.n["page"] == 1 and g.n["mail"] == 0, g.n
    assert "denied" in [m["content"] for m in g.chat.calls[2]["messages"] if m["role"] == "tool"][-1]


def t_false_completion_chat_path():
    g = Rig(["Lockdown engaged.", "Lockdown engaged."])                                         # no tool, twice: honest line
    r = g.e.run("tell me about the weather")
    assert r.text == HONEST_NOTHING_RAN and r.via == "fault"
    g = Rig(["Deleted a.txt.", tool_call("wipe", path="a.txt")])                               # widened: the model now reaches for the tool, which asks first
    r = g.e.run("tell me about the weather")
    assert r.via == "confirm" and g.n["wipe"] == 0


def t_unsupported_claim_after_tool():
    g = Rig([tool_call("page"), "Saved notes.md.", "I haven't saved anything."])               # something ran (page), but not a write: the claim is refused and the menu widened
    r = g.e.run("browse the page")
    assert r.text == "I haven't saved anything." and len(g.chat.calls) == 3, r
    assert len(g.chat.calls[2]["tools"]) == len(g.reg.names())


def t_leaked_syntax_regenerated():
    g = Rig(['<tool_call>{"name": "vol"', "Plain answer."])
    r = g.e.run("tell me a joke")
    assert r.text == "Plain answer." and g.chat.calls[1]["tools"] is None


def t_failure_must_be_reflected():
    g = Rig([tool_call("flaky", x=1), "Done, all set."])
    r = g.e.run("run the flaky thing")
    assert r.text == "I can't find that." and r.via == "fault", r
    g = Rig([tool_call("flaky", x=1), "I couldn't do that one."])
    assert g.e.run("run the flaky thing").text == "I couldn't do that one."                  # an honest reply stays


def t_round_cap():
    g = Rig([tool_call("vol")] * 8 + ["x"])
    config.get().max_tool_rounds = 2
    r = g.e.run("what is the volume and also what time")                                       # compound: no say shortcut
    config.reload()
    assert g.n["vol"] == 2 and r.via == "fault", (g.n, r)


def t_bad_json_arguments_become_bad_args():
    g = Rig([ChatResult(tool_calls=[ToolCall("flaky", {}, "c0", "arguments are not valid JSON: '{x:'")]), "I couldn't run that."])
    r = g.e.run("run the flaky thing")
    assert g.n["flaky"] == 0 and r.calls[0]["error_kind"] == "bad_args"


def t_say_shortcut_and_compound():
    g = Rig([tool_call("time")])
    r = g.e.run("what time is it")
    assert r.text == "It is 10:00." and r.via == "say" and len(g.chat.calls) == 1               # no second model round
    g = Rig([tool_call("time"), "It is ten, and I will leave the rest."])
    r = g.e.run("what time is it and what is the volume")
    assert r.via != "say" and len(g.chat.calls) == 2
    g = Rig([tool_call("time"), "Three days."])
    r = g.e.run("what time is it, how long until midnight")                                    # derived from the result, not the result: the model must see it
    assert r.text == "Three days." and len(g.chat.calls) == 2


def t_bus_events():
    import events as ev
    g = Rig([tool_call("vol"), "x"])
    bus, seen = ev.EventBus(), []
    bus.subscribe(seen.append, sources={"engine"})
    g.e.bus = bus
    g.e.run("what is the volume and also hello", source="voice")                               # compound: two model rounds, one tool event, one turn event
    kinds = [(e.kind, e.data.get("name") or e.data.get("origin")) for e in seen]
    assert ("tool", "vol") in kinds and ("turn", "voice") in kinds and seen[0].data["args"] == {} and "result" in seen[0].data


def t_refusal_with_tools_offered_is_nudged_once():
    g = Rig(["I can't change the volume.", tool_call("vol"), "Volume is 30."])
    r = g.e.run("turn the volume down")                                                         # the menu has vol; the first reply refused it
    assert g.n["vol"] == 1 and r.via == "say", (g.n, r)
    nudge = g.chat.calls[1]["messages"][-1]["content"]
    assert "vol" in nudge and nudge.startswith("You do have tools for this")
    assert all("You do have tools" not in m["content"] for m in g.e.history)                    # the nudge never enters the conversation
    g = Rig(["I can't do that.", "I really can't do that."])                                    # still refuses: accepted, no loop
    assert g.e.run("turn the volume down").text == "I really can't do that." and len(g.chat.calls) == 2
    g = Rig(["I can't tell you about the moon landing."])                                       # no tools offered (chat turn): never nudged
    assert g.e.run("tell me about the moon landing").text.startswith("I can't") and len(g.chat.calls) == 1


def t_lockdown_limits_everything_but_chat_and_time():
    from engine import lockdown
    g = Rig([tool_call("time"), "I can't do that.", tool_call("vol"), "Sorry, no.", tool_call("vol"), "Volume is 30."])
    g.e.run("hello there") if False else None
    g.e.history[:] = [{"role": "user", "content": "my secret plan"}, {"role": "assistant", "content": "ok"}]
    seen = []
    g.e.recall = type("R", (), {"context": lambda self, *a, **k: seen.append(1)})()
    r = g.e.run("lockdown")
    assert r.via == "lockdown" and "anyone can end it" in r.text and g.e.locked and not g.e.history and g.e.registry.only == frozenset({"time", "days_until", "calculate"})
    assert g.e.run("what time is it").text == "It is 10:00." and g.n["time"] == 1                    # the allowed tool still works
    assert g.e.run("turn the volume up").text == "I can't do that." and g.chat.calls[-1]["tools"] is None      # a blocked tool is not even offered
    r = g.e.run("hello")                                                                              # the model reaching for a hidden tool gets "no such tool"
    assert g.n["vol"] == 0 and not seen and not g.e.history, (g.n, seen, g.e.history)
    assert g.e.registry.call("vol").error_kind == "not_found"
    assert g.e.run("end lockdown").text == "Lockdown ended." and not g.e.locked and g.e.registry.only is None
    r = g.e.run("turn the volume up")
    assert g.e.registry.spec("vol") is not None and r.via in ("say", "tools", "chat"), (r.via, r.text)


def t_lockdown_passphrase_and_small_model():
    from engine import lockdown
    lockdown.set_passphrase("blue falcon 42")
    g = Rig(["a"])
    g.e.lock_chat = FakeChat(["from the small model"])
    assert "Say unlock and your passphrase" in g.e.run("lock down now").text
    from models.fake import FakeEmbedder
    g.e.embedder = FakeEmbedder()
    assert g.e.run("hi").text == "from the small model" and not g.chat.calls                       # only the talker answered; the big model never saw it
    vf = config.data_dir() / "lockdown_vectors.jsonl"
    rows = vf.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and "small model" not in rows[0] and '"hi"' not in rows[0], rows         # one vector, no text; the engage line and the unlock attempts leave none
    r = g.e.run("unlock blue falcon 41")
    assert r.text == "That's not the passphrase." and g.e.locked
    assert g.e.run("end lockdown").text == "That's not the passphrase." and g.e.locked
    assert g.e.run("Unlock, Blue Falcon 42!").text == "Lockdown ended." and not g.e.locked
    log_text = "".join(f.read_text(encoding="utf-8", errors="ignore") for f in config.data_dir().rglob("*") if f.is_file() and f.name != "lockdown.json")
    assert "falcon" not in log_text.lower(), "a spoken passphrase must never reach the logs"
    assert g.e.run("hello").text == "a"                                                              # the big model is back
    assert len(vf.read_text(encoding="utf-8").splitlines()) == 1 and not g.e.history[:0]            # nothing was stashed after the unlock


def t_time_note_after_quiet_turns():
    g = Rig(["a"] * 7 + [tool_call("time")] + ["b"] * 8)
    config.get().time_note_every = 3
    sysm = lambda: g.chat.calls[-1]["messages"][0]["content"]
    for _ in range(3):
        g.e.run("hello")
        assert "Background note" not in sysm()
    g.e.run("hello")                                                                                 # the 4th: three quiet turns behind it
    assert "Background note, not from the user: it is " in sysm()
    g.e.run("hello")
    assert "Background note" not in sysm()                                                          # shown once, then the count restarts
    g.e.run("hello"); g.e.run("hello")
    g.e.run("what time is it")                                                                       # the time tool resets the count
    for _ in range(3):
        g.e.run("hello")
        assert "Background note" not in sysm()
    config.get().time_note_every = 0
    for _ in range(5):
        g.e.run("hello")
        assert "Background note" not in sysm()
    config.reload()


def t_ambient_digest_reaches_the_model():
    g = Rig(["answer"] * 8)
    seen = []

    def amb(text):
        seen.append(text)
        if "slow" in text:
            time.sleep(1.0)
        if "boom" in text:
            raise RuntimeError("x")
        return "DIGEST about swift" if "Swift" in text else None
    g.e.registry.register(ToolSpec("lookup", lambda: "x", description="look things up", group="web", ambient=amb))
    sysm = lambda: g.chat.calls[-1]["messages"][0]["content"]
    g.e.run("Who is Taylor Swift")
    assert "DIGEST about swift" in sysm() and seen == ["Who is Taylor Swift"]
    g.e.run("how are you")
    assert "DIGEST" not in sysm()
    config.get().ambient_timeout_s = 0.2
    g.e.run("slow Swift")                                                                           # slower than the cap: dropped, the answer still comes
    assert "DIGEST" not in sysm()
    g.e.run("boom Swift")                                                                           # a failing hook never breaks the turn
    assert "DIGEST" not in sysm()
    config.get().ambient_timeout_s = 8
    config.get().ambient_enabled = False
    n = len(seen)
    g.e.run("Who is Taylor Swift")
    assert "DIGEST" not in sysm() and len(seen) == n                                                # switched off: never called
    config.get().ambient_enabled = True
    g.e.run("lockdown")
    n = len(seen)
    g.e.run("Who is Taylor Swift")
    assert "DIGEST" not in sysm() and len(seen) == n                                                # locked: never called
    config.reload()


def t_history_rolls():
    g = Rig(["a"] * 30)
    config.get().history_turns = 3
    for i in range(10):
        g.e.run("hello")
    config.reload()
    assert len(g.e.history) == 6


def main():
    ts = [v for k, v in sorted(globals().items()) if k.startswith("t_")]
    for t in ts:
        setup()
        t()
        print("ok  ", t.__name__)
    config.reload()
    print(f"{len(ts)}/{len(ts)} engine scenarios passed")


if __name__ == "__main__":
    main()
