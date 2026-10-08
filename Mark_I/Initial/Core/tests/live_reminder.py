"""Live gate for the reminder skill: the real 4B and embedder (playground) turn spoken requests into scheduled items. Invented requests, temp schedule, fake nothing else.   python tests/live_reminder.py"""
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from engine import prompt
from engine.loop import Engine
from models.client import Chat, Embedder, Playground
from proactive.scheduler import Scheduler
from skills.loader import build as build_skills

# (request, kind, minutes expected or None, text words expected, local hour expected or None)
CASES = [("remind me in 20 minutes to check the oven", "reminder", 20, "oven", None), ("set a timer for 10 minutes", "timer", 10, "", None),
         ("remind me tomorrow at 9 to call the dentist", "reminder", None, "dentist", 9), ("wake me in half an hour", "any", 30, "", None),
         ("remind me at 6 pm to water the plants", "reminder", None, "plants", 18), ("five minute timer", "timer", 5, "", None)]


def main():
    config.set_data_dir(tempfile.mkdtemp())
    pg, loaded = Playground(), []
    for slot, model in (("chat", config.get().brain), ("embed", config.get().embedder)):
        if not (pg.status().get("slots") or {}).get(slot):
            pg.load(model, slot=slot)
            loaded.append(slot)
    bad = 0
    try:
        s = Scheduler(ev.EventBus(), Path(tempfile.mkdtemp()) / "s.json")
        emb = Embedder()
        eng = Engine(Chat(config.get().brain), build_skills(None, scheduler=s, embed=emb.embed), emb, prompt.build_system(), bus=ev.EventBus())
        for req, kind, mins, words, hour in CASES:
            s.cancel("all")
            eng.history.clear()
            t0 = time.time()
            r = eng.run(req)
            its = s.items()
            ok = len(its) == 1 and kind in ("any", its[0]["kind"]) and all(w in its[0]["text"].lower() for w in words.split())
            if ok and mins:
                ok = abs(its[0]["at"] - time.time() - mins * 60) < 90
            if ok and hour is not None:
                ok = datetime.fromtimestamp(its[0]["at"]).hour == hour
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {req[:44]:44} {time.time() - t0:4.1f} s  via={r.via} items={[(i['kind'], i['text']) for i in its]} -> {r.text[:70]}")
    finally:
        for slot in loaded:
            pg.unload(slot)
    print(f"{bad} wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
