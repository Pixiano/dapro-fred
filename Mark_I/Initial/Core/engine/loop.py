"""The tool loop: one user turn in, one spoken reply out, with the rules that stop the failures seen in the old logs.

Sockets
  in : Engine(chat, registry, embedder=None, system="", bus=None).run(text, source="voice") -> Reply(text, calls, offered, reason, rounds, ms, via)
         chat = anything with complete(messages, tools=, think=) -> ChatResult (models/client.Chat or models/fake.FakeChat); registry = tools/registry.Registry
         Engine.history is the rolling conversation (settings.history_turns pairs); Engine.confirm is the confirmation gate (engine/gates.Confirmer)
  out: Reply.text (what FRED says), Reply.calls [{name, args, ok, error_kind, ms}], Reply.offered (the menu), Reply.reason (why that menu), Reply.via
         ("chat" | "tools" | "say" | "confirm" | "memory" | "fault"); bus events engine.turn / engine.tool (when a bus is given). No raw exception ever leaves run().
Rules (PLAN.md "The tool loop"):
  - arguments are coerced by the registry before the call; a tool call whose arguments were not valid JSON becomes bad_args, not a crash
  - an identical call (tool + canonical args) that already failed this turn is not run again: the earlier error comes back, marked as a repeat
  - after 2 failures on one tool this turn the tool is stopped and the model gets a `fault` result; the reply must say what failed
  - rounds capped (settings.max_tool_rounds), calls per model message capped (max_calls_per_round); independent calls in one message run in parallel; an identical
    call twice in one message runs once
  - chain offer: a call to a real tool outside the current menu widens the menu to that tool's whole group (never the full menu by default)
  - destructive tool -> nothing in that message runs; the user is asked, and the next yes/no turn decides (gates.Confirmer)
  - read/send separation: after an untrusted-content tool ran this turn, a `sends` tool is refused
  - refusal nudge: the reply says "I can't ..." although the menu had tools for the request and none ran -> the model is asked again once, told which tools it has
  - reply guards (gates.py): leaked tool syntax -> regenerate once without tools; a completion claim no tool supports -> widen the menu once, then an honest line;
    a failed tool whose failure the reply does not mention -> the failure's speakable replaces the reply
  - `say` tools (data is already a spoken sentence) skip the second model round unless the request is compound or derived ("how many days until...")
Memory (3b): `recall` = memory_bridge/turn_recall.MemoryRecall (anything with context(text, say) -> Recalled(block, ask, direct)); its block joins the system prompt for that turn only,
  an ask/direct reply is spoken without the model; `on_say(text)` / bus engine.say carries the interim "digging deeper" line.
Not here (later phases): sentence streaming to TTS (P4), agent allowlists (L4).
Self-check: python engine/loop.py (scripted FakeChat, no GPU); the full scenario suite is tests/test_engine.py
"""
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from engine import classify, gates, lockdown
from models import toolparse
from tools.registry import Result

HONEST_NOTHING_RAN = "I haven't actually done that, nothing ran. Say it again and I'll run it."


@dataclass
class Reply:
    text: str
    calls: list = field(default_factory=list)
    offered: list = field(default_factory=list)
    reason: str = ""
    rounds: int = 0
    ms: int = 0
    via: str = "chat"


class _Turn:
    """Per-turn state: what failed, how often, what ran, what was read."""

    def __init__(self, asked: str):
        self.asked = asked
        self.failed: dict[str, Result] = {}
        self.fails: dict[str, int] = {}
        self.results: list[str] = []          # strings of every result this turn (evidence for the guards)
        self.calls: list[dict] = []
        self.read_untrusted = False
        self.widened = False
        self.nudged = False                   # the refusal nudge fires at most once per turn
        self.rounds = 0


def _canon(name: str, args: dict) -> str:
    return name + json.dumps(args, sort_keys=True, default=str)


def _text(res: Result) -> str:
    if res.ok:
        d = res.data
        return d if isinstance(d, str) else json.dumps(d, default=str)
    return f"ERROR ({res.error_kind}): {res.message}"


class Engine:
    def __init__(self, chat, registry, embedder=None, system: str = "", bus=None, confirm: gates.Confirmer | None = None, recall=None, on_say=None, lock_chat=None):
        self.chat, self.registry, self.system, self.bus = chat, registry, system, bus
        self.recall, self.on_say, self.embedder = recall, on_say, embedder
        self.lock_chat, self.locked, self._private = lock_chat, False, False      # lockdown (engine/lockdown.py): lock_chat = the small talker model that answers while locked
        self.router = classify.Router(registry, embedder) if embedder is not None else None
        self.confirm = confirm or gates.Confirmer()
        registry.confirmer = self.confirm          # skills may ask a yes/no of their own (audio_device: "Do you mean X?"); the next turn's yes runs the call they stored
        self.history: list[dict] = []
        if lockdown.saved():                       # locked before the restart: still locked
            self._set_lockdown(True)
        self._lock = threading.Lock()         # one turn at a time: a second utterance waits for the first reply

    # ---- public --------------------------------------------------------------------------------------------------------------
    def run(self, text: str, source: str = "voice") -> Reply:
        with self._lock:
            t0 = time.monotonic()
            try:
                r = self._run(text)
            except Exception as ex:                                                    # the model server down, a bug: the user hears a clean line, the log has the rest
                log.session("engine_error", error=repr(ex), asked=text[:200])
                r = Reply("I hit a problem and couldn't finish that.", via="fault")
            r.ms = int((time.monotonic() - t0) * 1000)
            private, self._private = self._private, False
            quiet = self.locked or private                  # locked turns and unlock attempts leave no history, no bus event, and a spoken passphrase is never logged
            if not quiet:
                self._remember(text, r.text)
            elif self.locked and not private and self.embedder is not None and r.via not in ("fault", "lockdown"):     # locked chat: a vector only, kept apart from memory
                try:
                    lockdown.stash(self.embedder.embed([f"{text}\n{r.text}"])[0])
                except Exception as ex:
                    log.session("lockdown_stash_error", error=repr(ex))
            log.session("turn", source=source, asked="[lockdown]" if private else text, reply=r.text, via=r.via, reason=r.reason, offered=r.offered, calls=r.calls, rounds=r.rounds, ms=r.ms)
            if self.bus and not quiet:
                self.bus.emit("engine", "turn", text=text, reply=r.text, via=r.via, calls=[c["name"] for c in r.calls], ms=r.ms, origin=source)
            return r

    # ---- internals -----------------------------------------------------------------------------------------------------------
    def _set_lockdown(self, on: bool) -> Reply:
        self.locked = on
        self.registry.only = frozenset(config.get().lockdown_tools) if on else None
        self.history.clear()
        self.confirm._p = None
        lockdown.save(on)
        if on:
            how = "Say unlock and your passphrase to end it." if lockdown.has_passphrase() else "No passphrase is set, so anyone can end it by saying end lockdown."
            return Reply(f"Locked down. I'll chat and tell the time, nothing else. {how}", via="lockdown", reason="lockdown")
        return Reply("Lockdown ended.", via="lockdown", reason="lockdown")

    def _remember(self, user: str, reply: str):
        self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
        del self.history[: -2 * config.get().history_turns]

    def _complete(self, msgs, tools):
        return (self.lock_chat if self.locked and self.lock_chat else self.chat).complete(list(msgs), tools=tools or None, think=config.get().engine_think)      # a copy: the loop edits msgs afterwards

    def _say(self, text: str):
        """An interim spoken line while a slow step runs ("digging deeper..."): a hook, never an error."""
        try:
            self.on_say and self.on_say(text)
            self.bus and self.bus.emit("engine", "say", text=text)
        except Exception as ex:
            log.session("say_error", error=repr(ex))

    def _messages(self, text: str, memory: str = "") -> list:
        system = (self.system + ("\n\n---\n\n" + memory if memory else "")).strip()
        return ([{"role": "system", "content": system}] if system else []) + self.history + [{"role": "user", "content": text}]

    def _run(self, text: str) -> Reply:
        c = config.get()
        cmd = lockdown.command(text, self.locked)
        if cmd == "engage":
            return self._set_lockdown(True)
        if cmd:
            self._private = True
            if lockdown.has_passphrase() and not lockdown.check(cmd[1]):
                return Reply("That's not the passphrase.", via="lockdown", reason="lockdown")
            return self._set_lockdown(False)
        r = None if self.locked else self.confirm.resolve(text)
        if r:
            return self._confirmed(r, text)
        rc = self.recall.context(text, self._say) if self.recall and not self.locked else None
        if rc and (rc.direct or (rc.ask and not rc.block)):                          # the memory needs an answer first, or has already answered
            return Reply(rc.direct or rc.ask, via="memory", reason="memory")
        d = classify.classify(text, self.registry, self.router)
        t = _Turn(text)
        msgs = self._messages(text, rc.block if rc else "")
        offered = list(d.names) if d.needs_tools else []
        res = self._complete(msgs, self.registry.schemas(offered) if offered else None)
        if not d.needs_tools and gates.unsupported_claim(res.text, [], text):          # the chat path runs no tools, so a completion claim is false every time
            offered, t.widened = self.registry.names(), True
            res = self._complete(msgs, self.registry.schemas(offered))
        via = "tools" if offered else "chat"
        while True:
            calls = res.tool_calls[: c.max_calls_per_round]
            if not calls:
                if offered and not t.results and not t.nudged and gates.looks_like_refusal(res.text):      # "I can't..." with tools on the menu: remind the model what it has, once
                    t.nudged = True
                    msgs.append({"role": "assistant", "content": res.text})
                    msgs.append({"role": "user", "content": "You do have tools for this: " + ", ".join(offered) + ". If one of them can do what I asked, call it now. Otherwise answer normally."})
                    res = self._complete(msgs, self.registry.schemas(offered))
                    msgs[-2:] = []                                                       # the nudge is not part of the conversation
                    continue
                done = self._finish(res, t, msgs, offered)
                if done is None:                                                       # a guard widened the menu: ask again
                    offered = self.registry.names()
                    res = self._complete(msgs, self.registry.schemas(offered))
                    continue
                return Reply(done[0], t.calls, offered, d.reason, t.rounds, via=done[1] or via)
            if t.rounds >= c.max_tool_rounds:
                return Reply(self._exhausted(res, t), t.calls, offered, d.reason, t.rounds, via="fault")
            t.rounds += 1
            ask = next((x for x in calls if (self.registry.spec(x.name) or _NoSpec).destructive), None)
            if ask:                                                                    # nothing in this message runs until the user says yes
                return Reply(self.confirm.ask(ask.name, ask.args, self._confirm_prompt(ask.name, ask.args)), t.calls, offered, d.reason, t.rounds, via="confirm")
            for x in calls:                                                            # chain offer: the model reached for a real tool outside the menu
                s = self.registry.spec(x.name)
                if s and x.name not in offered:
                    offered = list(dict.fromkeys(offered + self.registry.groups()[s.group]))
            msgs.append({"role": "assistant", "content": res.text or None, "tool_calls": [{"id": x.id or f"call_{i}", "type": "function", "function": {"name": x.name, "arguments": json.dumps(x.args)}} for i, x in enumerate(calls)]})
            results = self._execute(calls, t)
            for i, (x, rr) in enumerate(zip(calls, results)):
                msgs.append({"role": "tool", "tool_call_id": x.id or f"call_{i}", "content": _text(rr)})
            specs = [self.registry.spec(x.name) for x in calls]
            if all(s and s.say for s in specs) and all(rr.ok for rr in results) and not classify.looks_compound(text) and not classify.needs_reasoning(text):
                return Reply(" ".join(_text(rr) for rr in results), t.calls, offered, d.reason, t.rounds, via="say")
            if classify.looks_compound(text):
                msgs.append({"role": "user", "content": f'That was part of this request: "{text}". If any part of it has not been done yet, call the tool for it now. If it is all done, reply normally.'})
            res = self._complete(msgs, self.registry.schemas(offered))

    def _finish(self, res, t: _Turn, msgs, offered):
        """The model made no tool call: apply the reply guards. Returns (text, via) or None to ask again with the full menu."""
        raw = res.text or ""
        if toolparse.looks_leaked(raw):
            if t.results:
                return " ".join(t.results), "fault"
            return toolparse.strip_debris(self._complete(msgs, None).text) or HONEST_NOTHING_RAN, None
        text = toolparse.strip_debris(raw)
        if gates.unsupported_claim(text, t.results, t.asked):
            if not t.widened:
                t.widened = True
                return None
            return HONEST_NOTHING_RAN, "fault"
        if t.failed and not gates.reflects_failure(text):
            return list(t.failed.values())[-1].speakable, "fault"
        return text, None

    def _exhausted(self, res, t: _Turn) -> str:
        if t.failed:
            return list(t.failed.values())[-1].speakable
        text = toolparse.strip_debris(res.text or "")
        return text if text and not toolparse.looks_leaked(res.text) else " ".join(t.results)

    def _confirm_prompt(self, name: str, args: dict) -> str:
        spec = self.registry.spec(name)
        try:
            shown = spec.preview(args) if spec and spec.preview else ""
        except Exception:
            shown = ""
        shown = shown or ", ".join(f"{k} {v}" for k, v in args.items())
        return f"Just to confirm: {name.replace('_', ' ')}{(' ' + shown) if shown else ''}. Yes or no?"

    def _confirmed(self, r, text: str) -> Reply:
        if r[0] == "cancel":
            return Reply("Okay, cancelled.", via="confirm")
        t = _Turn(text)
        res = self._one(r[1], r[2], t)
        say = res.speakable if not res.ok else (_text(res) if len(_text(res)) < 200 else "Done.")
        return Reply(say, t.calls, [r[1]], "confirmed", 1, via="confirm")

    # ---- executing calls -----------------------------------------------------------------------------------------------------
    def _one(self, name: str, args: dict, t: _Turn, error: str | None = None) -> Result:
        """Run one call with every per-turn rule. Not thread-safe on its own: _execute serialises the bookkeeping."""
        pre = self._pre(name, args, t, error)
        res = pre if pre is not None else self.registry.call(name, args)
        self._post(name, args, t, res)
        return res

    def _pre(self, name, args, t: _Turn, error):
        if error:
            return Result.fail("bad_args", message=error)
        key = _canon(name, args)
        if key in t.failed:
            e = t.failed[key]
            return Result.fail(e.error_kind, e.speakable, f"repeat of a call that already failed this turn ({e.message}); change the arguments or stop")
        if t.fails.get(name, 0) >= 2:
            return Result.fail("failed", f"I couldn't get {name.replace('_', ' ')} to work.", f"fault: {name} failed twice this turn and is stopped; tell the user it failed")
        spec = self.registry.spec(name)
        if spec and any(re.search(p, t.asked, re.I) for p in spec.refuse_if):
            return Result.fail("denied", f"I can't do that with {name.replace('_', ' ')}.", f"refused: the request mentions something {name} must not handle (see its not_for); tell the user plainly you cannot do that")
        if gates.untrusted_then_send(self.registry.spec(name), t.read_untrusted):
            return Result.fail("denied", "I won't send anything after reading untrusted content.", "denied: sending after reading untrusted content in the same turn")
        return None

    def _post(self, name, args, t: _Turn, res: Result, ran: bool = True):
        key = _canon(name, args)
        if not res.ok and key not in t.failed:
            t.failed[key] = res
            t.fails[name] = t.fails.get(name, 0) + 1
        s = self.registry.spec(name)
        if res.ok and s and s.reads_untrusted:
            t.read_untrusted = True
        t.results.append(_text(res))
        t.calls.append({"name": name, "args": args, "ok": res.ok, "error_kind": res.error_kind, "ms": res.ms})
        if self.bus:
            self.bus.emit("engine", "tool", name=name, args=args, ok=res.ok, error_kind=res.error_kind, ms=res.ms, result=_text(res)[:300])

    def _execute(self, calls, t: _Turn) -> list:
        """Pre-checks in order (they depend on earlier failures), the real calls in parallel, bookkeeping in order."""
        out: list = [None] * len(calls)
        todo, first = [], {}
        for i, x in enumerate(calls):
            key = _canon(x.name, x.args)
            if key in first and not x.error:                                           # identical call twice in one message: run once
                continue
            pre = self._pre(x.name, x.args, t, x.error)
            if pre is not None:
                out[i] = pre
            else:
                first[key] = i
                todo.append(i)
        if len(todo) > 1:
            with ThreadPoolExecutor(max_workers=len(todo)) as ex:
                for i, rr in zip(todo, ex.map(lambda j: self.registry.call(calls[j].name, calls[j].args), todo)):
                    out[i] = rr
        else:
            for i in todo:
                out[i] = self.registry.call(calls[i].name, calls[i].args)
        for i, x in enumerate(calls):
            if out[i] is None:
                out[i] = out[first[_canon(x.name, x.args)]]
                continue
            self._post(x.name, x.args, t, out[i])
        return out


class _NoSpec:
    destructive = False


def _selfcheck():
    import tempfile
    from models.fake import FakeChat, tool_call
    from tools.registry import Registry, ToolSpec
    config.set_data_dir(tempfile.mkdtemp())
    reg = Registry()
    reg.register(ToolSpec("get_time", lambda: "It is 10:00.", description="current time", group="clock", cues=(r"what time",), say=True))
    e = Engine(FakeChat(["Hello there."]), reg, system="you are fred")
    r = e.run("tell me a joke")
    assert r.text == "Hello there." and r.via == "chat" and r.calls == [] and len(e.history) == 2
    e = Engine(FakeChat([tool_call("get_time")]), reg)
    r = e.run("what time is it")
    assert r.text == "It is 10:00." and r.via == "say" and [c["name"] for c in r.calls] == ["get_time"], r
    e = Engine(FakeChat([RuntimeError("server down")]), reg)
    assert e.run("hi").via == "fault"                                                   # an exception never escapes
    print("loop ok")


if __name__ == "__main__":
    _selfcheck()
