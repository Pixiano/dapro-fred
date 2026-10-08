"""Proactive judge, SHADOW MODE: decides "should FRED speak unprompted now?" and only logs the decision. Nothing is spoken from here. (P6)
Hybrid chosen from a 154-case invented comparison (talker LLM alone 0.675 accuracy / recall 0.23; embedding classifier alone 0.82; hard rules + classifier 0.90, false-speak 2.1% at 0.5):
hard rules decide the clear cases, the embedding classifier (logistic regression on the embedder's vector of the event window, CPU numpy) decides the rest at judge_threshold 0.6 (best precision with useful recall).
The talker model is NOT used for the decision; when the judge is allowed to speak (after the shadow log is reviewed) it will only phrase the line, from a fixed template per category.

Sockets
  in : Judge(bus, eventlog, embed, model=None, now=time.time).start() / stop() / look() / decide(ctx) / window()
         embed(text) -> vector (Embedder.embed); model = <data_dir>/judge_model.json written by train(); without it the rules still decide and the rest is "quiet"
       ctx = {hour, since_spoke_s, in_turn, reminder_due, scam_level, critical, events: [[age_s, "source.kind", text]]}   built by window() from the EventLog, tts, clock and proactive events
       train(cases_path, embed, out) -> n   fits on labelled cases (tests/cases/judge_cases.jsonl, invented); retrain on the real shadow log after review
  rules (in order): reminder due -> speak; FRED spoke < judge_cooldown_s ago -> quiet; user in a turn -> quiet; scam level high or a critical system alert (event kind alert, data.critical) -> speak; else the classifier
  out: decide -> {"speak": bool, "via": "rule"|"model", "p": float|None}; log.session("judge", speak, via, p, kinds) in shadow mode: kinds are event kinds only, never event text
       It looks when a reminder comes due or a warning is raised, and every judge_every_s if something happened since.
Self-check: python proactive/judge.py (fake embedder, tiny invented set); python proactive/judge.py --train needs the playground embedder.
"""
import json
import math
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log


def render(c: dict) -> str:
    ev = "\n".join(f"- {int(a)}s ago, {k}: {t}" for a, k, t in c["events"])
    return (f"Time of day: {c['hour']:02d}:00. Seconds since FRED last spoke: {int(c['since_spoke_s'])}. User is in a turn right now: {'yes' if c['in_turn'] else 'no'}.\n"
            f"Recent events:\n{ev}")


def rules(c: dict):
    if c.get("reminder_due"):
        return True
    if c["since_spoke_s"] < config.get().judge_cooldown_s or c["in_turn"]:
        return False
    if c.get("scam_level") == "high" or c.get("critical"):
        return True
    return None


class Judge:
    def __init__(self, bus, eventlog, embed, model=None, now=time.time):
        self.bus, self.log, self.embed, self.now, self._stop = bus, eventlog, embed, now, threading.Event()
        self.path = Path(model) if model else config.data_dir() / "judge_model.json"
        try:
            m = json.loads(self.path.read_text(encoding="utf-8"))
            self.w, self.b = m["w"], m["b"]
        except Exception:
            self.w = self.b = None
        self.spoke = self.turn_end = self.due = self.warn = None
        self.last_state, self.hot = "END_OF_TURN", threading.Event()
        bus.subscribe(self._on, kinds=("end", "due", "warn", "state"))

    def _on(self, e):
        t = e.ts
        if e.source == "tts" and e.kind == "end":
            self.spoke = t
        elif e.source == "proactive" and e.kind == "due":
            self.due = t
            self.hot.set()
        elif e.source == "proactive" and e.kind == "warn":
            self.warn = (t, e.data.get("level"))
            self.hot.set()
        elif e.source == "clock" and e.kind == "state":
            self.last_state, self.turn_end = e.data.get("state"), t

    def window(self) -> dict:
        n = self.now()
        evs = [e for e in self.log.recent(300) if e.source != "tts"][-12:]
        return {"hour": datetime.fromtimestamp(n).hour, "since_spoke_s": n - self.spoke if self.spoke else 1e9,
                "in_turn": self.last_state not in ("END_OF_TURN", "IGNORE", "BACKCHANNEL") and self.turn_end is not None and n - self.turn_end < 8,
                "reminder_due": bool(self.due and n - self.due < 60), "scam_level": self.warn[1] if self.warn and n - self.warn[0] < 120 else None,
                "critical": any(e.data.get("critical") for e in evs if e.kind == "alert"),
                "events": [[n - e.ts, f"{e.source}.{e.kind}", str(e.data.get("text") or e.data.get("name") or e.data.get("title") or "")[:80]] for e in evs]}

    def decide(self, c: dict) -> dict:
        r = rules(c)
        if r is not None:
            return {"speak": r, "via": "rule", "p": None}
        if self.w is None or not c["events"]:
            return {"speak": False, "via": "model", "p": None}
        s = sum(a * b for a, b in zip(self.w, self.embed(render(c)))) + self.b
        p = 1 / (1 + math.exp(-s))
        return {"speak": p >= config.get().judge_threshold, "via": "model", "p": round(p, 3)}

    def look(self) -> dict:
        c = self.window()
        d = self.decide(c)
        log.session("judge", speak=d["speak"], via=d["via"], p=d["p"], kinds=sorted({k for _, k, _ in c["events"]}), since_spoke_s=int(min(c["since_spoke_s"], 99999)))
        return d

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            hot = self.hot.wait(config.get().judge_every_s)
            self.hot.clear()
            if self._stop.is_set():
                return
            try:
                if hot or self.log.recent(config.get().judge_every_s):
                    self.look()
            except Exception as ex:
                log.session("judge_error", error=type(ex).__name__)

    def stop(self):
        self._stop.set()
        self.hot.set()


def train(cases_path, embed, out) -> int:
    from sklearn.linear_model import LogisticRegression
    cases = [json.loads(line) for line in open(cases_path, encoding="utf-8")]
    m = LogisticRegression(C=4.0, max_iter=2000).fit(embed([render(c) for c in cases]), [int(c["label"]) for c in cases])
    Path(out).write_text(json.dumps({"w": m.coef_[0].tolist(), "b": float(m.intercept_[0]), "n": len(cases)}), encoding="utf-8")
    return len(cases)


def _selfcheck():
    import tempfile
    import events as ev
    config.reload()
    base = {"hour": 15, "since_spoke_s": 3000, "in_turn": False, "reminder_due": False, "scam_level": None, "events": [[5, "screen.settled", "x"]]}
    assert rules({**base, "reminder_due": True, "in_turn": True}) is True and rules({**base, "since_spoke_s": 30, "scam_level": "high"}) is False
    assert rules({**base, "in_turn": True, "scam_level": "high"}) is False and rules({**base, "scam_level": "high"}) is True and rules({**base, "critical": True}) is True and rules(base) is None

    def embed(t):                                                                  # fake: two features, "alert" and "chat"
        one = lambda x: [float("alert" in x), float("chat" in x)]
        return one(t) if isinstance(t, str) else [one(x) for x in t]
    mk = lambda word, y: {**base, "events": [[3, "system.alert" if word == "alert" else "speech.turn", word]], "label": y}
    tmp = Path(tempfile.mkdtemp())
    (tmp / "c.jsonl").write_text("\n".join(json.dumps(mk(w, y)) for w, y in [("alert", True)] * 6 + [("chat", False)] * 6), encoding="utf-8")
    assert train(tmp / "c.jsonl", embed, tmp / "m.json") == 12
    bus = ev.EventBus()
    el = ev.EventLog(bus, persist=False)
    clock = [10000.0]
    j = Judge(bus, el, embed, tmp / "m.json", now=lambda: clock[0])
    a, c = {**base, "events": [[3, "system.alert", "alert"]]}, {**base, "events": [[3, "speech.turn", "chat"]]}
    assert j.decide(a)["via"] == "model" and j.decide(a)["speak"] is True and j.decide(c)["speak"] is False
    assert j.decide({**base, "events": []}) == {"speak": False, "via": "model", "p": None}
    assert Judge(bus, el, embed, tmp / "none.json").decide(a)["speak"] is False   # no model yet: rules only
    j.spoke = clock[0] - 30
    assert j.window()["since_spoke_s"] == 30 and j.look()["speak"] is False         # just spoke: quiet
    j.due = clock[0] - 5
    assert j.window()["reminder_due"] and j.look() == {"speak": True, "via": "rule", "p": None}
    j.last_state, j.turn_end, j.due = "LISTEN", clock[0] - 2, None
    assert j.window()["in_turn"] and j.look()["speak"] is False
    print("judge ok")


if __name__ == "__main__":
    if "--train" in sys.argv:
        from models.client import Embedder
        print("trained on", train(Path(__file__).resolve().parents[1] / "tests" / "cases" / "judge_cases.jsonl", Embedder().embed, config.data_dir() / "judge_model.json"), "cases")
    else:
        _selfcheck()
