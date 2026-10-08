"""Measures the similarity thresholds against the live embedder (playground embed slot) and prints the gap between "should match" and "should not" for each.

  python tests/tune_thresholds.py [-v]     -v lists every pair with its score
Needs the playground (127.0.0.1:8090). Pairs are invented and generic (no personal text). Output per threshold: lowest positive, highest negative, the gap, the midpoint, and the value now.
Thresholds: FIELD_SIM (memory/consolidate.py), MIN_SIM (memory/personal_model.py, query vs item), precedents SIM (memory/precedents.py), semantic_floor (config.py, routing cases).
"""
import json
import math
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from models.client import Embedder

FIELD = {"+": [("phone", "mobile_phone"), ("email", "email_address"), ("birthday", "date_of_birth"), ("job", "job_title"), ("address", "home_address"), ("allergy", "allergies")],
         "-": [("favorite_day", "favorite_food"), ("coffee", "chai"), ("sister_name", "brother_name"), ("school", "hobby"), ("height", "weight"), ("breakfast", "dinner")]}
MODEL = {"+": [("what should I cook tonight", "likes spicy vegetarian food"), ("any tips for my exam next week", "studying for the physics exam"), ("recommend some music for the gym", "listens to rock while working out"),
               ("what time should I wake up tomorrow", "wakes up at six on weekdays"), ("should I take the bus to school", "commutes to school by bus"), ("what's a good gift for my sister", "has a younger sister")],
         "-": [("what is the capital of france", "likes spicy vegetarian food"), ("how many days until christmas", "studying for the physics exam"), ("turn the volume down", "has a younger sister"),
               ("what time is it", "listens to rock while working out"), ("open the browser", "wakes up at six on weekdays"), ("who wrote hamlet", "commutes to school by bus")]}
PRECEDENT = {"+": [("should I skip the gym today since I'm tired", "should I skip my workout, I'm exhausted"), ("can I stay up late and finish the game", "should I play video games past midnight tonight"),
                   ("should I buy the expensive headphones", "is it worth paying more for good headphones"), ("do I need to study for the quiz tonight", "should I revise for tomorrow's test now")],
             "-": [("should I skip the gym today since I'm tired", "should I buy the expensive headphones"), ("can I stay up late and finish the game", "do I need to study for the quiz tonight"),
                   ("should I buy the expensive headphones", "should I skip my workout, I'm exhausted"), ("do I need to study for the quiz tonight", "should I play video games past midnight tonight")]}


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1)


def gap(name, pos, neg, now):
    """pos/neg: lists of (score, label)."""
    lo, hi = min(pos), max(neg)
    print(f"{name:15} positives >= {lo[0]:.3f} ({lo[1]})   negatives <= {hi[0]:.3f} ({hi[1]})   gap {lo[0] - hi[0]:+.3f}   midpoint {(lo[0] + hi[0]) / 2:.3f}   now {now}" + ("" if lo[0] > hi[0] else "   OVERLAP"))
    if "-v" in sys.argv:
        for sc, lab in sorted(pos + neg, reverse=True):
            print(f"    {sc:.3f} {'+' if (sc, lab) in pos else '-'} {lab}")


def pairs(e, d, qfirst):
    return [[(cos(e.embed([a], is_query=qfirst)[0], e.embed([b])[0]), f"{a} | {b}") for a, b in d[s]] for s in "+-"]


def routing(e):
    import config
    from tools.builtin import build_registry
    from engine.classify import Router
    reg = build_registry()
    rt = Router(reg, e)
    pos, neg = [], []
    for line in (CORE / "tests/cases/routing_hard.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        s = dict(rt.rank(c["text"]))
        built = [t for t in c.get("tools", []) if t in s]
        if built:
            pos.append((max(s[t] for t in built), c["id"]))
        elif not c.get("tools"):
            neg.append((max(s.values()), c["id"]))
    for line in (CORE / "tests/cases/conversation.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if not c.get("tools") and not c.get("stub"):
            neg.append((max(s for _, s in rt.rank(c["text"])), c["id"]))
    gap("semantic_floor", pos, neg, config.get().semantic_floor)


def main():
    e = Embedder()
    gap("FIELD_SIM", *pairs(e, FIELD, False), 0.85)
    gap("MIN_SIM", *pairs(e, MODEL, True), 0.45)
    gap("precedent SIM", *pairs(e, PRECEDENT, False), 0.75)
    routing(e)


if __name__ == "__main__":
    main()
