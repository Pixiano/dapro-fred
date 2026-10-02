"""Self-check for general2.jsonl (no models, no GPU). Run: Core\\venv\\Scripts\\python.exe benchmarking\\cases\\check_general2.py
Regenerates nothing; loads the jsonl, validates structure and control chars, then asserts that each case's
sample correct reply passes and sample wrong reply fails, using a copy of run.py's reply_checks."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gen_general2 import SAMPLES  # noqa: E402

CHECKS = {"reply_must_match", "reply_must_match_any", "reply_must_match_all", "reply_must_not_match", "max_words"}
ALLOWED = CHECKS | {"id", "cat", "text", "history", "sessions"}


def reply_checks(case, reply):  # verbatim logic of benchmarking/run.py reply_checks
    r = (reply or "").lower()
    problems = []
    anys = case.get("reply_must_match_any")
    if anys and not any(a.lower() in r for a in anys):
        problems.append("any")
    for a in case.get("reply_must_match_all", []):
        if a.lower() not in r:
            problems.append("all")
    if case.get("reply_must_match") and not re.search(case["reply_must_match"], reply or "", re.I | re.M):
        problems.append("match")
    if case.get("reply_must_not_match") and re.search(case["reply_must_not_match"], reply or "", re.I | re.M):
        problems.append("forbidden")
    if case.get("max_words") and len((reply or "").split()) > case["max_words"]:
        problems.append("words")
    return problems


raw = open(os.path.join(HERE, "general2.jsonl"), encoding="utf-8").read()
bad_ctl = [hex(ord(ch)) for ch in raw if ord(ch) < 32 and ch not in "\n"]
assert not bad_ctl, f"control characters in jsonl: {bad_ctl[:5]}"

cases = [json.loads(l) for l in raw.splitlines() if l.strip()]
ids = [k["id"] for k in cases]
assert len(ids) == len(set(ids)), "duplicate ids"
assert set(ids) == set(SAMPLES), f"samples mismatch: {set(ids) ^ set(SAMPLES)}"

cats = {}
fails = []
for k in cases:
    assert set(k) <= ALLOWED, (k["id"], set(k) - ALLOWED)
    assert k["id"].split("-")[0] and k.get("cat"), k["id"]
    assert ("text" in k) != ("sessions" in k), k["id"]
    assert set(k) & CHECKS, f"{k['id']} has no check"
    if "sessions" in k:
        assert all(isinstance(s, list) and len(s) == 1 for s in k["sessions"]), k["id"]
    for role, _ in k.get("history", []):
        assert role in ("user", "assistant")
    for key in ("reply_must_match", "reply_must_not_match"):
        if key in k:
            re.compile(k[key], re.I | re.M)
    for key in ("reply_must_match_any", "reply_must_match_all"):
        assert k.get(key, [""]) and all(isinstance(x, str) for x in k.get(key, [])), k["id"]
    good, bad = SAMPLES[k["id"]]
    g = reply_checks(k, good)
    b = reply_checks(k, bad)
    if g:
        fails.append(f"{k['id']}: GOOD sample rejected {g}: {good!r}")
    if not b:
        fails.append(f"{k['id']}: BAD sample accepted: {bad!r}")
    cats[k["cat"]] = cats.get(k["cat"], 0) + 1

if fails:
    print("\n".join(fails))
    sys.exit(f"{len(fails)} FAILURES")
for c_, n in cats.items():
    print(f"{c_:24} {n}")
print(f"OK: {len(cases)} cases, all good samples accepted, all bad samples rejected")
