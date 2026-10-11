"""Nightly/weekly/monthly/yearly memory jobs through the GPU window. (P7; the runners themselves are the frozen memory/*.py modules.)

Sockets
  in : Jobs(mem, playground, embed, present=None, pm=None, now=time.time, mods=None, backup_dir=None)
         playground = models.client.Playground (window_open / window_close / load); embed = Embedder().embed; pm = the PersonalModel (monthly rebuilds it); mods = the memory modules (tests inject fakes)
       .start() / .stop(): a check every jobs_check_s;  .maybe() does one check;  .run_once() runs everything that is due, now
  out: stages in order, each only when it has work (the model is loaded on its FIRST model call, so an empty night never touches the GPU):
         Qwen3-8B (thinking)  consolidate.run_pending (every finished day), hygiene.run_hygiene
         gpt-oss-20b          weekly, maintenance (every 7 days), monthly (+ personal-model full rebuild), yearly, quarterly self-analysis
         no model             backup (weekly, outside the project, in ~/FRED_Backups)
       The first model call opens the playground window (live stack unloaded, embedder loaded for the memory index) and the run ends by closing it (live stack reloaded), also when it fails.
       run_once() -> {"nightly": [days], "weekly": [...], ..., "aborted": bool, "error": str}
  when: maybe() runs jobs only if the last turn / speech is older than jobs_quiet_s AND presence is not "present" (no camera reading = only 01:00-06:00), and the last run is older than jobs_gap_h
        (an aborted or failed run retries after jobs_retry_s (30 min)). If Vatsal comes back or speaks DURING a run, the next model call raises Aborted: the window closes and the live stack returns; the runners are
        resumable, so the unfinished day/week is simply done next time. A single model call (minutes, for the yearly) cannot be cut short.
Needs memory on (--memory). Alerts' yearly recompute (memory/alerts.run_alerts_pending) is NOT scheduled: it needs a source for the movable festival dates (undecided).
Self-check: python memory_bridge/jobs.py (fake playground, fake runners)
"""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from memory_bridge.link import memory_modules


class Aborted(BaseException):                                                # not an Exception: the frozen runners catch Exception per day and would swallow the abort and carry on
    pass


class Jobs:
    def __init__(self, mem, playground, embed, present=None, pm=None, now=time.time, mods=None, backup_dir=None, bus=None, presence=None):
        self.mem, self.pg, self.embed, self.pm, self.now, self.backup_dir = mem, playground, embed, pm, now, backup_dir
        self.present = present or (lambda: None)
        self.presence, self._peek = presence, 0.0                                    # presence is paused while a run is going; _need takes a one-shot look every jobs_peek_s instead
        self.mods = mods or self._real_mods()
        self.last_turn, self.last_run, self.retry_at = now(), 0.0, 0.0                   # FRED just started: not quiet yet
        self._open, self._cur, self._stop = False, None, threading.Event()
        bus and bus.subscribe(lambda e: setattr(self, "last_turn", e.ts), kinds=("turn", "start", "end", "wake", "state"), sources=("engine", "tts", "audio", "clock"))

    @staticmethod
    def _real_mods():
        m = memory_modules()
        import importlib
        for n in ("consolidate", "weekly", "period", "maintenance", "selfanalysis", "hygiene", "backup", "run_local"):
            m[n] = importlib.import_module(n)
        return m

    def _need(self, model):
        """Called before every model call: stop if Vatsal is back, open the window once, and make `model` the loaded chat model."""
        if self._stop.is_set() or self.present() is True or self.now() - self.last_turn < config.get().jobs_quiet_s:
            raise Aborted()
        if self.presence and self.now() - self._peek >= config.get().jobs_peek_s:
            self._peek = self.now()
            r = self.presence.poll()
            if r and r["faces"]:
                raise Aborted()                                                      # someone is at the desk
        if not self._open:
            from models.client import ModelError
            try:
                self.pg.window_open()
            except ModelError as ex:
                if "already open" not in str(ex):
                    raise                                                            # a window left open by a killed run is adopted; its stash is the live stack
            self._open = True                                                        # from here the window must be closed, whatever fails next
            self.pg.load(config.get().embedder, slot="embed")
        if self._cur != model:
            self.pg.load(model, slot="chat")
            self._cur = model

    def _llm(self, cfg, stage, effort=None, want_json=False):
        rl = self.mods["run_local"]
        calls = config.memory_root() / "jobs_calls.jsonl"
        calls.parent.mkdir(parents=True, exist_ok=True)
        inner = rl.make_llm(cfg, stage, calls, effort, want_json)

        def llm(x):
            self._need(cfg["model"])
            return inner(x)
        return llm

    def run_once(self) -> dict:
        m, rl, out = self.mods, self.mods["run_local"], {}
        index = m["semantic"].SemanticIndex(self.mem.store, self.embed, rl.EMB)
        nightly = self._llm(rl.NIGHTLY, "nightly", None, True)
        gpt = lambda stage: self._llm(rl.GPT, stage, rl.EFFORT.get(stage, "medium"))
        pm_update = (lambda end: self.pm.update(gpt("monthly"), end, mode="full")) if self.pm else None
        stages = [("nightly", lambda: m["consolidate"].run_pending(self.mem, nightly, "qwen3-8b")),
                  ("hygiene", lambda: m["hygiene"].run_hygiene(self.mem)),
                  ("weekly", lambda: m["weekly"].run_weekly_pending(self.mem, gpt("weekly"), index, "gpt-oss-20b")),
                  ("maintenance", lambda: m["maintenance"].run_maintenance_pending(self.mem, gpt("weekly"), index, "gpt-oss-20b")),
                  ("monthly", lambda: m["period"].run_monthly_pending(self.mem, gpt("monthly"), index, "gpt-oss-20b", pm_update=pm_update)),
                  ("yearly", lambda: m["period"].run_yearly_pending(self.mem, gpt("yearly"), index, "gpt-oss-20b")),
                  ("selfanalysis", lambda: m["selfanalysis"].run_selfanalysis_pending(self.mem, gpt("yearly"), index, "gpt-oss-20b", pm=self.pm))]
        self.presence and self.presence.stop()
        try:
            for name, fn in stages:
                out[name] = fn()
        except Aborted:
            out["aborted"] = True
        except Exception as ex:
            out["error"] = type(ex).__name__
            log.session("jobs_error", stage=name, error=type(ex).__name__)
        finally:
            if self._open:
                try:
                    self.pg.window_close()
                except Exception as ex:
                    out["close_error"] = type(ex).__name__
                    log.session("jobs_error", stage="window_close", error=type(ex).__name__)
                finally:
                    self._open, self._cur = False, None
        self.presence and self.presence.start()                                        # the camera watch is back (the backup below needs no model)
        try:
            out["backup"] = bool(m["backup"].run_backup_pending(self.mem, self.backup_dir))
        except Exception as ex:
            out["backup_error"] = type(ex).__name__
            log.session("jobs_error", stage="backup", error=type(ex).__name__)
        failed = "aborted" in out or "error" in out or "close_error" in out
        self.last_run = 0.0 if failed else self.now()                                 # a failed run is retried after jobs_retry_s, not after the 4 h gap
        self.retry_at = self.now() + config.get().jobs_retry_s if failed else 0.0
        log.session("jobs", **{k: (len(v) if isinstance(v, list) else bool(v)) for k, v in out.items()})
        return out

    def maybe(self):
        c, n = config.get(), self.now()
        quiet = n - self.last_turn >= c.jobs_quiet_s
        away = self.present() is False or (self.present() is None and 1 <= datetime.fromtimestamp(n).hour < 6)
        due = n >= self.retry_at and n - self.last_run >= c.jobs_gap_h * 3600
        return self.run_once() if (c.jobs_enabled and quiet and away and due) else None

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.wait(config.get().jobs_check_s):
            try:
                self.maybe()
            except Exception as ex:
                log.session("jobs_error", stage="loop", error=type(ex).__name__)

    def stop(self):
        self._stop.set()
        if self._open:                                                              # FRED is closing mid-run: give the live models back
            try:
                self.pg.window_close()
            except Exception:
                pass
            self._open = False


def _selfcheck():
    import tempfile
    config.reload()
    config.get().jobs_quiet_s = 600
    calls, state = [], {"present": False, "clock": 100000.0}

    class PG:
        def window_open(s):
            calls.append("open")
            if state.get("stale"):
                from models.client import ModelError
                raise ModelError("bad_request", "HTTP 409: window already open")
        window_close = lambda s: calls.append("close")
        load = lambda s, model, slot=None, **k: calls.append(f"load {slot}")

    rl = type("RL", (), {"EMB": "e", "NIGHTLY": {"model": "N"}, "GPT": {"model": "G"}, "EFFORT": {}, "make_llm": staticmethod(lambda cfg, stage, to, effort, js: lambda x: calls.append(f"call {stage}") or "{}")})
    pending = {"nightly": 0, "weekly": 0, "boom": False}

    def nightly(mem, llm, model):
        return [llm("x") and "d" for _ in range(pending["nightly"])]

    def weekly(mem, llm, index, model):
        if pending["boom"]:
            raise RuntimeError("x")
        return [llm("x") and "w" for _ in range(pending["weekly"])]
    none = lambda *a, **k: None
    mods = {"semantic": type("S", (), {"SemanticIndex": lambda *a: None}), "run_local": rl, "consolidate": type("C", (), {"run_pending": staticmethod(nightly)}),
            "hygiene": type("H", (), {"run_hygiene": staticmethod(none)}), "weekly": type("W", (), {"run_weekly_pending": staticmethod(weekly)}),
            "maintenance": type("M", (), {"run_maintenance_pending": staticmethod(none)}), "period": type("P", (), {"run_monthly_pending": staticmethod(none), "run_yearly_pending": staticmethod(none)}),
            "selfanalysis": type("A", (), {"run_selfanalysis_pending": staticmethod(none)}), "backup": type("B", (), {"run_backup_pending": staticmethod(lambda mem, d: None)})}
    j = Jobs(type("Mem", (), {"store": None})(), PG(), None, present=lambda: state["present"], now=lambda: state["clock"], mods=mods)
    assert j.last_turn == state["clock"] and j.maybe() is None                      # just started: not quiet yet
    j.last_turn = 0.0
    # an empty night never touches the GPU
    assert j.run_once() == {"nightly": [], "hygiene": None, "weekly": [], "maintenance": None, "monthly": None, "yearly": None, "selfanalysis": None, "backup": False} and calls == []
    # work: window opens once, qwen for the nightly then gpt for the weekly, window closes
    pending.update(nightly=2, weekly=1)
    r = j.run_once()
    assert r["nightly"] == ["d", "d"] and r["weekly"] == ["w"]
    assert calls == ["open", "load embed", "load chat", "call nightly", "call nightly", "load chat", "call weekly", "close"], calls
    calls.clear()
    state["stale"] = True
    pending.update(nightly=1, weekly=0)
    assert j.run_once()["nightly"] == ["d"] and calls[-1] == "close", calls                  # a window left open by a killed run is adopted, not a failure
    state["stale"] = False
    # Vatsal comes back mid-run: aborted at the next model call, the live stack is restored
    calls.clear()
    state["present"] = None
    pending.update(nightly=1, weekly=1)
    def swallowing(mem, llm, model):                                               # like the frozen runners: catches Exception per day and carries on
        done = []
        for _ in range(2):
            try:
                llm("x")
                state.update(present=True)
                done.append("d")
            except Exception:
                pass
        return done
    mods["consolidate"].run_pending = staticmethod(swallowing)
    r = j.run_once()
    assert r.get("aborted") and calls[0] == "open" and calls[-1] == "close" and "call weekly" not in calls          # the abort got through the runner's except Exception
    assert j.retry_at == state["clock"] + 1800 and j.last_run == 0.0                                               # retried after 30 min, not after the 4 h gap
    # the window closes, and a failing close is reported, not raised
    calls.clear(); state["present"] = False
    mods["consolidate"].run_pending = staticmethod(nightly); pending["nightly"] = 1
    PG.window_close = lambda s: (_ for _ in ()).throw(RuntimeError("reload failed"))
    r = j.run_once()
    assert r["close_error"] == "RuntimeError" and j.last_run == 0.0 and j._open is False
    PG.window_close = lambda s: calls.append("close")
    # FRED closing mid-run gives the models back
    j._open = True; calls.clear(); j.stop(); assert calls == ["close"] and not j._open
    j._stop.clear()
    # a failing stage is reported, the window still closes
    calls.clear(); state["present"] = False; pending["boom"] = True; mods["consolidate"].run_pending = staticmethod(nightly); pending["nightly"] = 0
    r = j.run_once()
    assert r["error"] == "RuntimeError" and not calls
    # maybe(): only when quiet, away and due
    runs = []
    j.run_once = lambda: runs.append(1) or {}
    j.retry_at, j.last_run, j.last_turn = 0.0, 0.0, state["clock"] - 60
    assert j.maybe() is None                                                       # spoke a minute ago
    j.last_turn = 0.0; state["present"] = True; assert j.maybe() is None             # at the desk
    state["present"] = None; state["clock"] = datetime(2031, 3, 4, 14).timestamp(); assert j.maybe() is None   # no camera reading, afternoon
    state["clock"] = datetime(2031, 3, 4, 3).timestamp(); assert j.maybe() == {} and len(runs) == 1             # no camera reading, 03:00
    j.last_run = state["clock"] - 3600; assert j.maybe() is None                     # ran an hour ago
    state["present"] = False; j.last_run = 0.0; j.retry_at = state["clock"] + 100; assert j.maybe() is None; j.retry_at = 0.0; assert j.maybe() == {}
    # camera watch: paused for the run, one snapshot per jobs_peek_s before a model call, resumed after
    log2, face = [], [0]
    class Pres:
        stop = lambda s: log2.append("stop")
        start = lambda s: log2.append("start")
        poll = lambda s: log2.append("poll") or {"faces": face[0]}
    j2 = Jobs(type("Mem", (), {"store": None})(), PG(), None, present=lambda: False, mods=mods, presence=Pres(), now=lambda: state["clock"])
    j2.last_turn = 0.0; pending["nightly"] = 2; calls.clear()
    j2.run_once()
    assert log2[0] == "stop" and log2[-1] == "start" and log2.count("poll") == 1                     # two model calls close together: one look
    face[0] = 1; log2.clear(); state["clock"] += 120; j2._peek = 0.0
    r = j2.run_once()
    assert r.get("aborted") and log2[-1] == "start"                                                   # a face at the desk: stop, and the watch is back
    print("jobs ok")


if __name__ == "__main__":
    _selfcheck()
