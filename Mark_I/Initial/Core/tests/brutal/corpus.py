"""The user's real usage as test input, read at run time from the old FRED logs (gitignored, outside what this repo publishes). Nothing here prints or stores utterance text in a report.

Sockets
  in : load_blocks(n_blocks, block_len, seed) -> list of blocks, each a list of consecutive real utterances (chronological, so context carries like a real session)
       load_labelled(n, seed)                 -> list of (utterance, old_tool_or_None) from the tool-call log (what the user asked for and which tool the old FRED used)
       profile()                              -> aggregate numbers only (counts, word-length quantiles, tool mix, busiest hours)
  out: plain Python lists/dicts. Missing logs -> empty lists (the callers report "no corpus" instead of failing).
Self-check: python tests/brutal/corpus.py (counts only)
"""
import collections
import json
import random
import statistics
from pathlib import Path

OLD = Path(__file__).resolve().parents[5] / "Old" / "Core" / "data"
MEMLOG, TOOLLOG = OLD / "memory" / "default_user.jsonl", OLD / "tool_call_log.jsonl"


def _rows(path):
    try:
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def user_turns() -> list:
    """(timestamp, text) for every real user turn, oldest first, cleaned of empties and over-long pastes."""
    out = [(r.get("timestamp", ""), r["content"].strip()) for r in _rows(MEMLOG) if r.get("role") == "user" and isinstance(r.get("content"), str) and 2 <= len(r["content"].strip()) <= 600]
    return sorted(out)


def load_blocks(n_blocks: int = 15, block_len: int = 20, seed: int = 11) -> list:
    turns, rng = user_turns(), random.Random(seed)
    if len(turns) < block_len:
        return []
    starts = sorted(rng.sample(range(0, len(turns) - block_len), min(n_blocks, len(turns) - block_len)))
    return [[t for _, t in turns[s:s + block_len]] for s in starts]


def load_labelled(n: int = 300, seed: int = 11) -> list:
    rows = [(r["utterance"].strip(), r.get("tool_called")) for r in _rows(TOOLLOG) if isinstance(r.get("utterance"), str) and 2 <= len(r["utterance"].strip()) <= 600]
    random.Random(seed).shuffle(rows)
    seen, out = set(), []
    for u, t in rows:                                                              # one of each wording: the log repeats test phrases many times
        if u.lower() not in seen:
            seen.add(u.lower())
            out.append((u, t))
    return out[:n]


def profile() -> dict:
    turns, lab = user_turns(), [r for r in _rows(TOOLLOG) if "utterance" in r]
    words = [len(t.split()) for _, t in turns] or [0]
    hours = collections.Counter(ts[11:13] for ts, _ in turns if len(ts) > 13)
    q = statistics.quantiles(words, n=20) if len(words) > 20 else words
    return {"user_turns": len(turns), "distinct_turns": len({t.lower() for _, t in turns}), "words_median": statistics.median(words), "words_p95": q[18] if len(q) > 18 else max(words),
            "tool_log_rows": len(lab), "no_tool_share": round(sum(r.get("tool_called") is None for r in lab) / max(1, len(lab)), 3),
            "top_tools": collections.Counter(r.get("tool_called") for r in lab if r.get("tool_called")).most_common(8), "busiest_hours": hours.most_common(4),
            "days_active": len({ts[:10] for ts, _ in turns})}


def _selfcheck():
    p = profile()
    print({k: v for k, v in p.items()})
    b = load_blocks(3, 5)
    assert all(len(x) == 5 for x in b)


if __name__ == "__main__":
    _selfcheck()
