"""Brutal suite 4, no hardware: the user's real weeks of desk presence and wake-word scores replayed through the Core logic with a fake clock. Numbers only.

  python tests/brutal/timelines.py
Presence: Old/Core/data/presence_log.jsonl (one present/absent reading about every 15 s, weeks long) fed to senses.presence.Presence as if it were the camera. Checks: the debounce never flips
absent for a gap shorter than presence_absent_s, always flips for a longer one, how long the Core state disagrees with the raw readings, flapping (flips closer than 5 min), the lag to notice an absence.
Wake word: Old/Core/data/wakeword_log.jsonl score events through the Core threshold and cooldown: how many fires per day at the Core settings versus the old ones, and how the count moves with the threshold.
Missing logs -> the part is skipped and said so.
"""
import collections
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
import events as ev  # noqa: E402
from senses.presence import Presence  # noqa: E402

OLD = Path(__file__).resolve().parents[5] / "Old" / "Core" / "data"
FINDINGS = []


def finding(what, detail=""):
    FINDINGS.append(what)
    print(f"FINDING {what} {detail}")


def rows(p):
    try:
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def presence():
    log = [(datetime.fromisoformat(r["ts"]).timestamp(), bool(r["present"])) for r in rows(OLD / "presence_log.jsonl") if "ts" in r and "present" in r]
    if not log:
        print("presence: no log, skipped")
        return
    log.sort()
    c = config.get()
    absent_s = c.presence_absent_s
    sessions, cur = [], [log[0]]
    for a, b in zip(log, log[1:]):
        if b[0] - a[0] > 180:                                           # the machine was off or the sensor was paused: a new session
            sessions.append(cur)
            cur = []
        cur.append(b)
    sessions.append(cur)
    flips = disagree = total = fl_short = 0
    lags, flap = [], 0
    bad_short = bad_long = 0
    frame = np.zeros((4, 4, 3), np.uint8)
    for s in sessions:
        if len(s) < 3:
            continue
        clock = [s[0][0]]
        bus = ev.EventBus()
        states = []
        bus.subscribe(lambda e: states.append((clock[0], e.data["present"])), kinds={"state"}, sources={"presence"})
        cur_raw = [False]

        class Cam:
            def snap(self):
                return frame
        p = Presence(bus, Cam(), lambda rgb: 1 if cur_raw[0] else 0)
        p._clock = lambda: clock[0]
        p._last_face = s[0][0]
        last_flip = None
        last_present_raw = None
        for (t, raw), nxt in zip(s, s[1:] + [(s[-1][0] + 1, s[-1][1])]):
            clock[0], cur_raw[0] = t, raw
            before = p.is_present()
            p.poll()
            if raw:
                last_present_raw = t
            state = p.is_present()
            if before is not None and state != before:
                flips += 1
                if last_flip is not None and t - last_flip < 300:
                    flap += 1
                last_flip = t
                if state is False:
                    gap = t - (last_present_raw if last_present_raw is not None else s[0][0])
                    lags.append(gap)
                    if gap < absent_s - 0.001:
                        bad_short += 1
            dt = nxt[0] - t
            total += dt
            if state is not None and state != raw:
                disagree += dt
        # a gap in raw presence longer than absent_s must have produced an absent state before the next present reading
        run_start, last_absent_reading = None, None
        for (t, raw) in s:
            if not raw:
                run_start = t if run_start is None else run_start
                last_absent_reading = t
            if raw and run_start is not None:
                # the rule can only fire at a poll: needs a raw-absent reading at least absent_s after the run began (the old log sometimes has 100+ s holes inside a session)
                if last_absent_reading - run_start >= absent_s and not any(run_start < ts <= t and not st for ts, st in states):
                    bad_long += 1
                    print('  RAW around run:', [(round(x[0] - run_start), int(x[1])) for x in s if run_start - 40 <= x[0] <= t + 20][:12], '... last', [(round(x[0] - run_start), int(x[1])) for x in s if run_start - 40 <= x[0] <= t + 20][-6:])
                    print(f"  long-absence case: run of {t - run_start:.0f} s, absent readings {[round(x[0] - run_start) for x in s if run_start <= x[0] < t and not x[1]][:8]}, core states in session {[(round(ts - s[0][0]), st) for ts, st in states][:6]}, session starts present={s[0][1]}")
                run_start = None
    days = max(1, len({datetime.fromtimestamp(t).date() for t, _ in log}))
    print(f"presence: {len(log)} readings, {len(sessions)} sessions over {days} days; Core state flips {flips} ({flips / days:.1f}/day), flapping (<5 min apart) {flap}")
    print(f"  Core disagrees with the raw reading {100 * disagree / max(1, total):.1f}% of the time (expected: about the absent_s lag after each absence)")
    if lags:
        print(f"  absence noticed after {statistics.median(lags):.0f} s median (rule: {absent_s:.0f} s + one poll), max {max(lags):.0f} s")
    if bad_short:
        finding("absent flip for a gap shorter than presence_absent_s", bad_short)
    if bad_long:
        finding("a long absence never produced an absent state", bad_long)


def wake():
    ev_ = [r for r in rows(OLD / "wakeword_log.jsonl") if r.get("type") == "score"]
    if not ev_:
        print("wake: no log, skipped")
        return
    pts = sorted((datetime.fromisoformat(r["ts"]).timestamp(), float(r["score"]), bool(r.get("fired"))) for r in ev_)
    days = max(1, len({datetime.fromtimestamp(t).date() for t, _, _ in pts}))
    c = config.get()

    def fires(thr, cool):
        n, last = 0, -1e9
        for t, s, _ in pts:
            if s >= thr and t - last >= cool:
                n += 1
                last = t
        return n
    old = sum(1 for _, _, f in pts if f)
    print(f"wake: {len(pts)} logged score events over {days} days, old FRED fired {old} ({old / days:.1f}/day)")
    print(f"  at the Core setting (threshold {c.wakeword_threshold}, cooldown {c.wake_cooldown_s} s): {fires(c.wakeword_threshold, c.wake_cooldown_s)} fires ({fires(c.wakeword_threshold, c.wake_cooldown_s) / days:.1f}/day)")
    for thr in (0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7):
        print(f"    threshold {thr}: {fires(thr, c.wake_cooldown_s) / days:.1f} fires/day")
    hist = collections.Counter(int(s * 10) / 10 for _, s, _ in pts)
    print("  score histogram (logged events): " + ", ".join(f"{k:.1f}:{v}" for k, v in sorted(hist.items())))


def main():
    presence()
    wake()
    print(f"\n{len(FINDINGS)} findings")
    return 1 if FINDINGS else 0


if __name__ == "__main__":
    sys.exit(main())
