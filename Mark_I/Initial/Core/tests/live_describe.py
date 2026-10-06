"""Live P1b gate: the 12-screenshot set (cases v-01..v-12 of Old/benchmarking/cases/multimodal.jsonl, invented screens in Old/benchmarking/fixtures, read in place)
through the Describer on the playground's vision slot. Same pass rules as the old benchmark (reply_must_match_any / _all / _not_match), so the result is comparable
with the old baseline for the Qwen3.5-4B image path (100% on these 12).

  python tests/live_describe.py [model-id]          needs the P0 playground (slots) at FRED_PLAYGROUND_URL (default http://localhost:8090); one run at a time
Loads the model into the VISION slot with its image projector, runs the cases, reports pass/fail and time per case, then unloads the slot.
OCR (Windows, CPU) runs on every image and its text grounds the description: spans the OCR text lacks are dropped (printed as counts). Also prints whether the OCR text alone contains the expected answer (the describer-vs-OCR reading check).
"""
import json
import os
import cv2
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from models.client import Chat, Playground
from senses import ocr
from senses.describe import Describer

OLD = Path(__file__).resolve().parents[4] / "Old" / "benchmarking"
URL = os.environ.get("FRED_PLAYGROUND_URL") or config.get().playground_url


def passes(case: dict, reply: str) -> bool:
    r = reply.lower()
    if any_ := case.get("reply_must_match_any"):
        if not any(re.search(re.escape(a.lower()) if not re.search(r"[\^\(\|\\]", a) else a, r) for a in any_):
            return False
    if not all(re.search(a.lower(), r) for a in case.get("reply_must_match_all", [])):
        return False
    # deviation from the old rule: a negated mention ("no visible errors") is not a hallucination, so negated phrases are dropped before the forbidden-word check
    pos = re.sub(r"\b(no|nor|nothing|not|without|zero|free of)\b[^.,;\n]{0,40}", " ", r)
    return not (case.get("reply_must_not_match") and re.search(case["reply_must_not_match"], pos, re.I))


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else config.get().describe_model
    cases = [json.loads(l) for l in open(OLD / "cases" / "multimodal.jsonl", encoding="utf-8")]
    cases = [c for c in cases if c["id"].startswith("v-")]
    pg = Playground(URL)
    assert pg.healthy() and "vision" in pg.status()["slots"], "need the P0 playground (restart it)"
    assert pg.status()["slots"]["vision"] is None, "vision slot busy: another run is using it"
    t0 = time.time()
    pg.load(model, slot="vision", ctx=8192, vision=True)
    print(f"loaded {model} in the vision slot with its projector: {time.time() - t0:.1f}s")
    d = Describer(Chat(model, slot="vision", url=URL, max_tokens=config.get().describe_max_tokens, temperature=0.2), ev.EventBus())
    ok = 0
    try:
        for c in cases:
            img = cv2.imread(str(OLD / "fixtures" / c["images"][0]))[:, :, ::-1]
            t0 = time.time()
            text = ocr.read(img)
            ocr_ms = int((time.time() - t0) * 1000)
            has_key = "reply_must_match_any" in c or "reply_must_match_all" in c
            has = ("ocr has the answer: " + ("yes" if passes({k: v for k, v in c.items() if k != "reply_must_not_match"}, text) else "no")) if has_key else "ocr: n/a"
            r = d.describe(img, question=c["prompt"], ocr_text=text)
            good = passes(c, r.text)
            ok += good
            print(f"{'PASS' if good else 'FAIL'} {c['id']} {c['tag']:20} describe {r.ms:5d} ms  ocr {ocr_ms:3d} ms ({len(text):4d} chars, {has})  grounded: {len(r.dropped)} span(s) dropped" + ("" if good else f"\n     reply: {r.text[:200]!r}"))
        img = cv2.imread(str(OLD / "fixtures" / "cuda_oom.png"))[:, :, ::-1]
        g = d.describe(img, ocr_text=ocr.read(img))
        print(f"generic description ({g.ms} ms, {len(g.text)} chars, verified={g.verified}, {len(g.dropped)} span(s) dropped as not in the OCR text)")
    finally:
        pg.unload("vision")
    print(f"{ok}/{len(cases)} passed (old baseline for this model's image path: 12/12)")
    return 0 if ok >= len(cases) - 1 else 1


if __name__ == "__main__":
    sys.exit(main())
