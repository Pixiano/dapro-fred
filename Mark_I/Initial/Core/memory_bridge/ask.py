"""Ask delivery: when a conversation winds down FRED asks the ask queue's one either/or question, spoken and (when a pill exists) in the pill's text box. (P6; the queue itself is the frozen memory/askqueue.py.)

Sockets
  in : AskDelivery(bus, mem, speak, show=None, now=time.time, idle_s=90, ttl_s=300, ask=None, answer=None)
         speak(text): FRED says it; show(item, answer, done): the pill popup (PillController.ask); ask/answer default to askqueue.ask / askqueue.answer, injectable for tests
       .start() / .stop(): a 5 s tick; .tick() does one check (tests call it)
       .take(user_text) -> reply | None: engine/live.py offers every typed or spoken turn here first; a clear answer to the waiting question is recorded and acknowledged, anything else returns None and goes to the engine
  out: "the conversation is over" = at least one turn happened, FRED is not speaking, and idle_s have passed with no new turn. Then askqueue.ask(mem, topic, turns) runs ONCE for that conversation (its own rules decide
       whether anything is asked: turns, daily cap, snooze). A question waits ttl_s for an answer, then is dropped (it comes back on a later day per the queue's own limits).
       Topic = the words of the conversation's user turns, so questions about what was just discussed go first. Nothing from the conversation is logged here.
Self-check: python memory_bridge/ask.py
"""
import importlib
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_bridge.link import memory_modules


class AskDelivery:
    def __init__(self, bus, mem, speak, show=None, now=time.time, idle_s=90.0, ttl_s=300.0, ask=None, answer=None):
        if ask is None:
            memory_modules()
            q = importlib.import_module("askqueue")
            ask, answer = q.ask, q.answer
        self.mem, self.speak, self.show, self.now, self.idle_s, self.ttl_s, self._ask, self._answer = mem, speak, show, now, idle_s, ttl_s, ask, answer
        self.turns, self.topic, self.last, self.pending, self.asked_at, self.talking = 0, [], now(), None, 0.0, False
        self._stop = threading.Event()
        bus.subscribe(self._turn, kinds=("turn",), sources=("engine",))
        bus.subscribe(lambda e: setattr(self, "talking", e.kind == "start"), kinds=("start", "end"), sources=("tts",))

    def _turn(self, e):
        self.turns, self.last = self.turns + 1, self.now()
        self.topic.append(e.data.get("text", ""))

    def tick(self):
        now = self.now()
        if self.pending and now - self.asked_at > self.ttl_s:
            self.pending = None
        if self.pending or not self.turns or self.talking or now - self.last < self.idle_s:
            return
        turns, topic, self.turns, self.topic = self.turns, " ".join(self.topic), 0, []
        item = self._ask(self.mem, topic, turns)
        if not item:
            return
        self.pending, self.asked_at = item, now
        self.speak(f"{item['text']} " + ", or ".join(o["label"] for o in item["options"]) + "?")
        self.show and self.show(item, lambda i, r: self._answer(self.mem, i, r), lambda r: setattr(self, "pending", None))

    def take(self, text: str):
        if not self.pending:
            return None
        r = self._answer(self.mem, self.pending["id"], text)
        if not r.get("ok"):
            return None                                                             # not an answer: a normal turn (the popup stays up)
        self.pending = None
        return "Noted." if r["choice"] != "later" else "Alright, later."

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.wait(5.0):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self):
        self._stop.set()


def _selfcheck():
    import events as ev
    bus, clock, said, shown, asked = ev.EventBus(), [0.0], [], [], []
    item = {"id": "p:1", "text": "Is Rohan the same as Ro?", "options": [{"key": "yes", "label": "Same person"}, {"key": "no", "label": "Two people"}]}
    script = [item, None]
    ask = lambda mem, topic, turns: asked.append((topic, turns)) or script.pop(0)
    answer = lambda mem, i, r: {"ok": True, "choice": "yes"} if r == "same" else {"error": "unclear"}
    d = AskDelivery(bus, "mem", said.append, lambda it, a, done: shown.append((it, a, done)), now=lambda: clock[0], idle_s=90, ttl_s=300, ask=ask, answer=answer)
    d.tick(); assert not asked                                                      # no conversation yet
    bus.emit("engine", "turn", text="we talked about Rohan"); bus.emit("engine", "turn", text="and the trip")
    clock[0] = 30; d.tick(); assert not asked                                       # still warm
    bus.emit("tts", "start"); clock[0] = 200; d.tick(); assert not asked            # FRED is speaking: wait
    bus.emit("tts", "end", cancelled=False); d.tick()
    assert asked == [("we talked about Rohan and the trip", 2)] and said == ["Is Rohan the same as Ro? Same person, or Two people?"] and shown[0][0] is item
    d.tick(); assert len(asked) == 1                                                # once per conversation, and not while one waits
    assert d.take("hmm what") is None and d.pending                                 # not an answer: goes to the engine, question stays
    assert d.take("same") == "Noted." and d.pending is None and d.take("same") is None
    bus.emit("engine", "turn", text="next chat"); clock[0] = 400; d.tick()          # queue says nothing to ask
    assert len(asked) == 2 and len(said) == 1
    script.append(item); bus.emit("engine", "turn", text="again"); clock[0] = 600; d.tick(); assert d.pending
    clock[0] = 1000; d.tick(); assert d.pending is None                             # unanswered question expires
    shown[-1][2]({"ok": True}); d.pending = item; shown[-1][2]({"ok": True}); assert d.pending is None   # answered in the popup
    print("ask delivery ok")


if __name__ == "__main__":
    _selfcheck()
