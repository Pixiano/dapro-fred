"""Mark I memory: runs the synthetic year through the REAL local models (LM Studio's OpenAI-style server) and scores the result.
Models are swapped by hand/`lms` between stages (they do not fit the GPU together): nightly = Qwen3-8B (thinking on), weekly+ = gpt-oss-20b.
Usage: python run_local.py oracle|nightly|weekly|monthly|yearly <run folder> [synthetic folder] [last day to consolidate, e.g. 2026-01-18]
The run folder is a disposable Memory root, resumable (finished days/weeks are skipped), plus calls.jsonl (every model call: seconds, tokens, effort).
Nothing here touches a real memory folder.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import consolidate
import period
import run_synthetic as rs
import weekly
from manager import Memory
from semantic import SemanticIndex

URL = "http://localhost:1234/v1"
NIGHTLY = {"model": "qwen/qwen3-8b", "temperature": 0.6, "top_p": 0.95, "max_tokens": 12000}   # Qwen3 thinks by default; <think> is stripped
GPT = {"model": "openai/gpt-oss-20b", "temperature": 0.6, "max_tokens": 9000}
NIGHTLY["model"] = os.environ.get("NIGHTLY_MODEL", NIGHTLY["model"])   # model comparison: NIGHTLY_MODEL / ANALYST_MODEL override the defaults
GPT["model"] = os.environ.get("ANALYST_MODEL", GPT["model"])   # high effort thinks long
CRITIC = os.environ.get("CRITIC_MODEL")   # monthly/yearly: a different model for the critic pass (period.Critic); default = ANALYST_MODEL
EFFORT = {"weekly": "medium", "monthly": "high", "yearly": "high"}    # production levels (Vatsal, 2026-10-04)
EMB = "text-embedding-qwen3-embedding-4b"
QPREFIX = "Instruct: Given a search query, retrieve relevant memory notes\nQuery: "


def _post(path, body, timeout=3600):
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except OSError:
            if attempt:
                raise
            time.sleep(5)


def embed(text, is_query):
    return _post("/embeddings", {"model": EMB, "input": (QPREFIX + text) if is_query else text})["data"][0]["embedding"]


ACTION = {"type": "json_schema", "json_schema": {"name": "action", "strict": True, "schema": {"type": "object", "properties": {
    "tool": {"type": "string"}, "args": {"type": "object"}}, "required": ["tool", "args"]}}}   # gpt-oss otherwise answers on a channel LM Studio drops: empty content


NOTE = {"type": "json_schema", "json_schema": {"name": "note", "strict": True, "schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}}}


_ARR = {"type": "array", "items": {}}
NIGHT = {"type": "json_schema", "json_schema": {"name": "night", "strict": True, "schema": {"type": "object", "properties": {
    **{k: _ARR for k in ("events", "states", "aliases", "relationships", "corrections", "contradictions", "open_questions")}, "summary": {"type": "string"}},
    "required": ["events", "states", "aliases", "relationships", "corrections", "contradictions", "open_questions", "summary"]}}}   # the nightly answer for gpt-oss (role swap test)


CHECKS = {"type": "json_schema", "json_schema": {"name": "checks", "strict": True, "schema": {"type": "object", "properties": {"checks": _ARR}, "required": ["checks"]}}}   # the second nightly pass


def make_llm(cfg, stage, log_to, effort=None, want_json=False):
    """llm(prompt or messages) -> text. Thinking text is dropped; want_json cuts the answer down to its outermost {...}."""
    def llm(x):
        msgs = [{"role": "user", "content": x}] if isinstance(x, str) else x
        body = dict(cfg, messages=msgs)
        if effort:
            body["reasoning_effort"] = effort
            first = msgs[0]["content"] if msgs and isinstance(msgs[0], dict) else ""
            body["response_format"] = NIGHT if stage == "nightly" else CHECKS if stage == "check" else NOTE if first.startswith("You write ONE short YEAR NOTE") else ACTION   # tool loops speak one JSON action per turn; year notes one {"note"}
        t = time.time()
        r = _post("/chat/completions", body)
        text = r["choices"][0]["message"].get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if want_json:
            a, b = text.find("{"), text.rfind("}")
            text = text[a:b + 1] if 0 <= a < b else text
        u = r.get("usage", {})
        with open(log_to, "a", encoding="utf-8") as f:
            f.write(json.dumps({"stage": stage, "model": cfg["model"], "effort": effort, "secs": round(time.time() - t, 1), "prompt_tokens": u.get("prompt_tokens"),
                                "completion_tokens": u.get("completion_tokens"), "finish": r["choices"][0].get("finish_reason")}) + "\n")
        print(f"[{stage}] call {time.time() - t:.0f}s {u.get('completion_tokens')} tok", flush=True)
        return text
    return llm


def critic_llm(calls):
    """CRITIC_MODEL set: the yearly critic pass runs on that model (team: Qwen3-8B drafts, gpt-oss criticises). The model is swapped with lms right
    before the critic's first call (they do not fit the GPU together). None = the analyst model criticises its own draft."""
    if not CRITIC:
        return None
    inner, state = make_llm(dict(GPT, model=CRITIC), "yearly", calls, EFFORT["yearly"]), {"swapped": False}
    def llm(x):
        if not state["swapped"]:
            lms = str(Path.home() / ".lmstudio" / "bin" / "lms")
            for cmd in (["unload", "--all"], ["load", CRITIC, "--context-length", "20000", "--gpu", "max", "-y"], ["load", EMB, "-y"]):
                subprocess.run([lms, *cmd], check=True, capture_output=True)
            state["swapped"] = True
        return inner(x)
    return llm


def open_run(run, folder):
    run = Path(run)
    m = Memory(run / "memory")
    ix = SemanticIndex(m.store, embed, EMB)
    ix.attach()
    days, ex = rs.load(folder)
    if not m.store.db.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0]:
        for recs in days.values():
            for r in recs:
                m.archive.write(r["stream"], r["data"], session=r.get("session"), ts=r["ts"])
    return m, ix, days, ex, run


def report(m, ex, folder, run, stage, extra):
    out = {"stage": stage, **extra}
    out["failed_days"] = [(r["day"], r["detail"]) for r in m.store.db.execute("SELECT day, detail FROM consolidation_runs WHERE status!='done'")]
    out["counts"] = {t: m.store.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "entities", "entity_aliases", "entity_states", "relationships", "review_queue")}
    out["events_in_key"] = sum(len(o.get("events", [])) for o in ex.values())
    tf = Path(folder) / "truth.json"
    if tf.exists():
        out["score"] = {k: f"{p}/{n}" + (f"  FAIL: {fails}" if fails else "") for k, (p, n, fails) in rs.score(m, json.loads(tf.read_text(encoding="utf-8")), UNTIL).items()}
    Path(run, f"{stage}_report.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    stage, run = sys.argv[1], sys.argv[2]
    folder = sys.argv[3] if len(sys.argv) > 3 else Path(__file__).parent / "testdata" / "synthetic_year"
    UNTIL = sys.argv[4] if len(sys.argv) > 4 else "9999"   # last day to consolidate; weeks/months follow whatever events exist
    import logging
    logging.getLogger("memory").setLevel(logging.ERROR)
    Path(run).mkdir(parents=True, exist_ok=True)
    consolidate.USER = "Arin"   # the synthetic year is Arin's, not Vatsal's
    import memtools
    for mod in (weekly, period, memtools):   # the agents' prompts name the user: show them the synthetic one
        for k, v in list(vars(mod).items()):
            if k.isupper() and isinstance(v, str) and "Vatsal" in v:
                setattr(mod, k, v.replace("Vatsal", "Arin"))
    weekly.Week.cands = bool(os.environ.get("WEEK_CANDS"))   # WEEK_CANDS=1: recurring candidates + free review phase in the weekly run
    m, ix, days, ex, run = open_run(run, folder)
    calls = run / "calls.jsonl"
    t0 = time.time()
    if stage == "oracle":   # the key's own perfect nightly answers: lets weekly/monthly/yearly be tested without the nightly model's mistakes
        done = [d for d in sorted(days) if d <= UNTIL and consolidate.consolidate_day(m, d, rs.oracle(ex), "oracle")]
        report(m, ex, folder, run, stage, {"days_done_this_run": len(done)})
    elif stage == "nightly":
        llm = make_llm(NIGHTLY, "nightly", calls, os.environ.get("NIGHTLY_EFFORT"), want_json=True)   # NIGHTLY_EFFORT=medium with NIGHTLY_MODEL=openai/gpt-oss-20b
        check = make_llm(dict(NIGHTLY, model=os.environ.get("NIGHTLY_CHECK_MODEL", NIGHTLY["model"])), "check", calls, os.environ.get("NIGHTLY_EFFORT"), want_json=True) if os.environ.get("NIGHTLY_CHECK") else None   # NIGHTLY_CHECK=1: second fact-check pass, NIGHTLY_CHECK_MODEL=other model for it (swap by hand with lms between runs)
        done = [d for d in sorted(days) if d <= UNTIL and consolidate.consolidate_day(m, d, llm, "qwen3-8b", embed=embed, check_llm=check)]   # a small batch first, not the whole year
        report(m, ex, folder, run, stage, {"days_done_this_run": len(done), "minutes": round((time.time() - t0) / 60, 1)})
    elif stage == "weekly":
        llm = make_llm(GPT, "weekly", calls, EFFORT["weekly"])
        done = [w for w in os.environ["WEEKS"].split(",") if weekly.run_weekly(m, w, llm, ix, "gpt-oss-20b")] if os.environ.get("WEEKS") else             weekly.run_weekly_pending(m, llm, ix, "gpt-oss-20b")   # WEEKS=2026-01-18,... runs only those Sundays
        report(m, ex, folder, run, stage, {"weeks_done_this_run": done, "minutes": round((time.time() - t0) / 60, 1)})
    elif stage in ("monthly", "yearly"):
        llm = make_llm(GPT, stage, calls, EFFORT[stage])
        if stage == "monthly" and os.environ.get("MONTHS"):   # MONTHS=2026-01,2026-02
            done = [x for x in os.environ["MONTHS"].split(",") if period.run_monthly(m, x, llm, ix, "gpt-oss-20b")]
        elif stage == "yearly" and os.environ.get("YEAR_END"):   # YEAR_END=2026-05-04: the year ending that day
            done = bool(period.run_yearly(m, os.environ["YEAR_END"], llm, ix, "gpt-oss-20b", critic_llm=critic_llm(calls)))
        else:
            done = (period.run_monthly_pending if stage == "monthly" else period.run_yearly_pending)(m, llm, ix, "gpt-oss-20b")
        report(m, ex, folder, run, stage, {"done_this_run": done, "minutes": round((time.time() - t0) / 60, 1)})
    else:
        raise SystemExit("stage: nightly|weekly|monthly|yearly")
    m.close()
