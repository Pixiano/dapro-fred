# Core/scripts/tool_call_report.py
#
# Reads Core/data/tool_call_log.jsonl (written by
# orchestrator/tool_call_log.py) and reports per-tool call/error/
# interrupted counts, so there's a way to actually look at the data
# before building anything on top of it. See that module's docstring
# for the row shapes this joins: a tool-call row (has "tool_called")
# and a feedback row (has "feedback": true), joined by "turn_id".

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

# Windows consoles default to cp1252, which chokes on stray unicode that
# ends up in a result_preview (e.g. copied-from-web glyphs) — replace
# rather than crash mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from config.settings import DATA_DIR

LOG_PATH = DATA_DIR / "tool_call_log.jsonl"


def load_rows(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def split_and_join(rows: list):
    """Returns a list of (tool_call_row, feedback_row_or_None), one per
    tool-call row. Feedback rows have no "tool_called" of their own."""
    feedback_by_turn = {
        r["turn_id"]: r for r in rows if r.get("feedback")
    }
    tool_rows = [r for r in rows if not r.get("feedback")]
    return [(r, feedback_by_turn.get(r.get("turn_id"))) for r in tool_rows]


def aggregate(joined: list) -> dict:
    """tool_name -> {calls, errors, interrupted}"""
    stats = defaultdict(lambda: {"calls": 0, "errors": 0, "interrupted": 0})
    for row, fb in joined:
        s = stats[row.get("tool_called", "?")]
        s["calls"] += 1
        if row.get("result_error"):
            s["errors"] += 1
        if fb and fb.get("interrupted"):
            s["interrupted"] += 1
    return stats


def print_table(stats: dict):
    rows = []
    for tool, s in stats.items():
        rate = s["errors"] / s["calls"] if s["calls"] else 0.0
        rows.append((tool, s["calls"], s["errors"], rate, s["interrupted"]))
    rows.sort(key=lambda r: r[3], reverse=True)

    header = f"{'tool':<28}{'calls':>7}{'errors':>8}{'error_rate':>12}{'interrupted':>13}"
    print(header)
    print("-" * len(header))
    for tool, calls, errors, rate, interrupted in rows:
        print(f"{tool:<28}{calls:>7}{errors:>8}{rate:>12.1%}{interrupted:>13}")


def dump_rows(joined: list, filter_fn=None):
    for row, fb in joined:
        if filter_fn and not filter_fn(row, fb):
            continue
        print("-" * 60)
        print(f"turn_id:        {row.get('turn_id')}")
        print(f"tool_called:    {row.get('tool_called')}")
        print(f"utterance:      {row.get('utterance')}")
        print(f"arguments:      {row.get('arguments')}")
        print(f"result_preview: {row.get('result_preview')}")
        print(f"result_error:   {row.get('result_error')}")
        if fb:
            print(f"interrupted:    {fb.get('interrupted')}")
            if fb.get("note"):
                print(f"note:           {fb.get('note')}")


def main():
    parser = argparse.ArgumentParser(description="Report on Core/data/tool_call_log.jsonl")
    parser.add_argument("--tool", help="Dump full rows for this tool name only")
    parser.add_argument("--errors-only", action="store_true",
                         help="Dump every row that errored or was interrupted")
    parser.add_argument("--log-path", type=Path, default=LOG_PATH,
                         help="Override the log file path (default: DATA_DIR/tool_call_log.jsonl)")
    args = parser.parse_args()

    if not args.log_path.exists():
        print("no data yet")
        return

    rows = load_rows(args.log_path)
    if not rows:
        print("no data yet")
        return

    joined = split_and_join(rows)

    if args.tool:
        dump_rows(joined, lambda row, fb: row.get("tool_called") == args.tool)
    elif args.errors_only:
        dump_rows(joined, lambda row, fb: row.get("result_error") or bool(fb and fb.get("interrupted")))
    else:
        print_table(aggregate(joined))


if __name__ == "__main__":
    main()
