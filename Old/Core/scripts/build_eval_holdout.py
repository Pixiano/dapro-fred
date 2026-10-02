# Core/scripts/build_eval_holdout.py
#
# Freezes a slice of Core/data/tool_call_log.jsonl as a held-out eval
# set for the planned tool-calling LoRA (see MVP Plan (v1.0 - v1.1).txt's
# pre-fine-tune checklist and roadmap_pre-finetune_2026-08-26.md).
#
# Why this needs to exist: right now there's no baseline number for
# "how good is tool-calling today," so a fine-tune (real or synthetic-
# augmented) can't be honestly compared against anything afterward.
# Freezing a fixed set of turn_ids once, up front, means future rows
# (real or synthetic) can be excluded from training by turn_id without
# ever needing to re-run this split.
#
# Reuses tool_call_report.py's own row-loading/exclusion logic rather
# than re-implementing it — same known-bug EXCLUSIONS list, so a row
# that's an artefact of a since-fixed bug (see that module's own
# comments) never ends up in the eval set either.
#
# Idempotent by design: refuses to overwrite an existing holdout file.
# The whole point is a FROZEN set — re-running this after new rows
# have accumulated must never silently change which turn_ids are
# "eval," or every future eval number stops being comparable to the
# last one. Delete the output file yourself if you genuinely intend to
# re-freeze (e.g. before any row in the current holdout has ever been
# used for a real comparison).

import argparse
import json
import sys
from pathlib import Path

import numpy as np

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from config.settings import DATA_DIR
from scripts.tool_call_report import is_excluded, load_rows, split_and_join

LOG_PATH = DATA_DIR / "tool_call_log.jsonl"
HOLDOUT_PATH = DATA_DIR / "tool_call_eval_holdout.jsonl"

# 15% of ~1085 rows is ~163 — enough to move the needle on a percentage
# without gutting the training set. Not derived from anything more
# precise than "the same ballpark as REAL_POSITIVE_TEST_FRACTION/
# REAL_NEGATIVE_TEST_FRACTION elsewhere in this project."
HOLDOUT_FRACTION = 0.15

# Same fixed-seed convention as wakeword_train.py's own real-data
# splits (ingest_real_negative_clips) — reproducible, not security-
# sensitive, just needs to be stable across runs if ever re-derived.
SEED = 3


def select_holdout(rows: list, fraction: float, seed: int) -> list:
    """Deterministic split. `rows` must already be tool-call rows only
    (no feedback rows, no excluded bug-artefact rows) — see main()."""
    rng = np.random.default_rng(seed)
    indices = np.arange(len(rows))
    rng.shuffle(indices)
    n_holdout = max(1, int(len(rows) * fraction))
    holdout_idx = set(indices[:n_holdout].tolist())
    return [r for i, r in enumerate(rows) if i in holdout_idx]


def main():
    parser = argparse.ArgumentParser(
        description="Freeze a held-out eval slice of tool_call_log.jsonl (one-time; refuses to overwrite)."
    )
    parser.add_argument("--log-path", type=Path, default=LOG_PATH)
    parser.add_argument("--out", type=Path, default=HOLDOUT_PATH)
    parser.add_argument("--fraction", type=float, default=HOLDOUT_FRACTION)
    args = parser.parse_args()

    if args.out.exists():
        print(f"{args.out} already exists — refusing to overwrite a frozen eval set. "
              f"Delete it yourself first if you really mean to re-freeze.")
        sys.exit(1)

    if not args.log_path.exists():
        print("no data yet")
        sys.exit(1)

    rows = load_rows(args.log_path)
    joined = split_and_join(rows)
    # Eval set must be genuine tool-call rows, not bug-artefact rows —
    # same standard tool_call_report.py already applies before anything
    # gets called "training-eligible."
    clean = [row for row, _fb in joined if not is_excluded(row)]

    holdout = select_holdout(clean, args.fraction, SEED)
    holdout_ids = {r["turn_id"] for r in holdout}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for row in holdout:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Froze {len(holdout)}/{len(clean)} clean rows ({len(holdout) / len(clean):.1%}) "
          f"to {args.out}")
    print(f"Remaining {len(clean) - len(holdout)} rows stay train-eligible.")
    print("Any training/synthetic-generation pipeline must exclude turn_ids in this "
          "file's rows — never train on a holdout turn_id, real or synthetic.")
    return holdout_ids


def _self_check():
    """Pure split logic, synthetic rows, no real file touched — runs
    before main() so a broken split is caught before anything real gets
    frozen."""
    fake_rows = [{"turn_id": f"t{i}", "tool_called": "x"} for i in range(1000)]
    h1 = select_holdout(fake_rows, 0.15, seed=3)
    h2 = select_holdout(fake_rows, 0.15, seed=3)
    assert len(h1) == 150, f"expected 150, got {len(h1)}"
    assert {r["turn_id"] for r in h1} == {r["turn_id"] for r in h2}, "split must be deterministic"
    train_ids = {r["turn_id"] for r in fake_rows} - {r["turn_id"] for r in h1}
    assert len(train_ids) == 850
    print("build_eval_holdout self-check: all passed\n")


if __name__ == "__main__":
    _self_check()
    main()
