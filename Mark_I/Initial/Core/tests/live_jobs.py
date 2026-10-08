"""Live P7 gate: the nightly and weekly runners through the real playground window on an INVENTED week in a temp memory (no personal data). The live stack (4B chat + embedder) is loaded first;
the run must consolidate the days (Qwen3-8B), build the week (gpt-oss-20b) and then bring the live stack back. Monthly, yearly and quarterly runs are switched off here (they think for many minutes);
they use the same window code.   python tests/live_jobs.py        Takes a few minutes; leaves every slot empty at the end.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from memory_bridge.jobs import Jobs
from memory_bridge.link import open_memory
from models.client import Embedder, Playground

DAYS = {"2026-08-31": ["Today I started the timber workshop build with Tavi Brennock. We agreed the shed goes behind the house in Dunmarrow.",
                       "Decision: use treated pine for the frame, ordered from the Pryce yard. Tavi will pick it up on Wednesday."],
        "2026-09-02": ["Pine arrived. Tavi and Zora Pryce unloaded it, the frame is 4 metres wide. I am worried the groundwork is not level yet.",
                       "Note to self: hire a plate compactor for Thursday."],
        "2026-09-04": ["Groundwork levelled and compacted. First wall frame is up. Tavi says the roof trusses come next week.",
                       "Mood: tired but pleased. The workshop build is on schedule."]}


def main():
    config.set_data_dir(tempfile.mkdtemp())
    pg = Playground()
    st = pg.status().get("slots") or {}
    assert not any(st.get(k) for k in ("chat", "talk", "vision")), "slots are not empty: stop and retry later"
    c = config.get()
    c.jobs_quiet_s = 0
    mem = open_memory(Path(tempfile.mkdtemp()))
    for day, texts in DAYS.items():
        for i, t in enumerate(texts):
            mem.archive.write("conversation", {"role": "user", "text": t}, ts=f"{day}T{10 + i:02d}:00:00+00:00")
    pg.load(c.brain, slot="chat")
    st.get("embed") or pg.load(c.embedder, slot="embed")
    try:
        mods = Jobs._real_mods()
        off = lambda *a, **k: None
        for n, f in (("period", ("run_monthly_pending", "run_yearly_pending")), ("selfanalysis", ("run_selfanalysis_pending",)), ("maintenance", ("run_maintenance_pending",))):
            mods[n] = type("Off", (), {k: staticmethod(off) for k in f})
        j = Jobs(mem, pg, Embedder().embed, present=lambda: False, mods=mods, backup_dir=tempfile.mkdtemp())
        t0 = time.time()
        out = j.run_once()
        print(f"run {time.time() - t0:.0f} s:", {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})
        t1 = time.time()
        slots = pg.status()["slots"]
        print("after the window:", {k: (v or {}).get("model") for k, v in slots.items() if v}, f"({time.time() - t1:.1f} s)")
        ev = mem.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        wk = [tuple(r) for r in mem.store.db.execute("SELECT status FROM weekly_runs")] if mem.store.db.execute("SELECT name FROM sqlite_master WHERE name='weekly_runs'").fetchone() else None
        print("events:", ev, "weekly runs:", wk)
        ok = (not out.get("aborted") and not out.get("error") and len(out["nightly"]) == 3 and ev > 0 and (slots.get("chat") or {}).get("model") == c.brain and bool(slots.get("embed")))
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        pg.unload("all")


if __name__ == "__main__":
    sys.exit(main())
