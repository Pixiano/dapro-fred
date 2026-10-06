"""Screen capture period benchmark: what each interval costs, and what the larger gaps lose.

  python tests/bench_screen.py [--live-s 25] [--trials 60] [--intervals 0.5,0.7,1.0,1.2]
Part A, resources (live, your real screen): ScreenSense runs for --live-s seconds per interval; reports process CPU (% of one core), RAM growth, mean grab and detect
  time, overruns, tick count. Frames stay in memory, nothing is saved or logged; only numbers are printed.
Part B, discrepancy (synthetic, deterministic): timelines built from the invented screens in Old/benchmarking/fixtures, sampled at each interval with random phase through the
  REAL detector. Shows what a longer gap does:
    flash    a toast that is on screen for D seconds: chance any sample sees it (and so any change event fires), and the delay from appearing to the event
    steady   a change that stays: delay to the event (about interval / 2 by construction)
    merge    two different changes g seconds apart: chance they collapse into ONE event (the intermediate screen is never seen)
    progress a bar filling over 10 s: events fired and the mean size of each event's changed region (bigger gap = fewer, bigger events)
"""
import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from senses.screen import ListSource, ScreenSense, TileDetector

FIX = Path(__file__).resolve().parents[4] / "Old" / "benchmarking" / "fixtures"


def part_a(intervals, live_s):
    import psutil
    p = psutil.Process()
    print(f"\nPART A  resources, live capture of the real screen, {live_s}s per interval")
    print(f"{'interval':>9} {'ticks':>6} {'CPU % of 1 core':>16} {'RAM growth MB':>14} {'grab ms':>8} {'detect ms':>10} {'overruns':>9} {'changes':>8}")
    for T in intervals:
        config.get().screen_interval_s = T
        s = ScreenSense(ev.EventBus())
        s.tick()                                     # open the capture, set the baseline
        s.det.reset()
        rss0, c0, w0 = p.memory_info().rss, sum(p.cpu_times()[:2]), time.perf_counter()
        s._n = {"ticks": 0, "changes": 0, "overruns": 0}
        s._grab_s = s._detect_s = 0.0
        s.start()
        time.sleep(live_s)
        s.stop()
        wall, cpu = time.perf_counter() - w0, sum(p.cpu_times()[:2]) - c0
        st = s.stats()
        print(f"{T:8.1f}s {st['ticks']:6d} {cpu / wall * 100:16.1f} {(p.memory_info().rss - rss0) / 2**20:14.1f} {st['grab_ms']:8.1f} {st['detect_ms']:10.1f} {st['overruns']:9d} {st['changes']:8d}")
        s._source.close()
    f = s.latest()
    print(f"          screen {f.shape[1]}x{f.shape[0]}, grid {config.get().screen_grid_cols}x{config.get().screen_grid_rows}, stride {config.get().screen_stride}")


def load(name):
    from PIL import Image
    return np.asarray(Image.open(FIX / name).convert("RGB"))[:, :, ::-1].copy()   # BGR


def to4(x):
    return np.concatenate([x, np.full(x.shape[:2] + (1,), 255, dtype=np.uint8)], axis=2)


def run(frame_at, T, phase, t_end):
    """Sample frame_at(t) every T from `phase - 2T`; return [(t_sample, Change)] from the real detector."""
    det, out, t = TileDetector(), [], phase - 2 * T
    while t <= t_end:
        ch = det.update(frame_at(t))
        if ch:
            out.append((t, ch))
        t += T
    return out


def part_b(intervals, trials):
    rnd = random.Random(7)
    base, other, third = to4(load("clean_desktop.png")), to4(load("clean_editor.png")), to4(load("cuda_oom.png"))
    toast_src = to4(load("disk_full_toast.png"))
    d = np.abs(toast_src[:, :, :3].astype(int) - base[:, :, :3].astype(int)).sum(axis=2) > 30
    ys, xs = np.where(d)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    with_toast = base.copy()
    with_toast[y0:y1, x0:x1] = toast_src[y0:y1, x0:x1]
    print(f"\nPART B  discrepancy from larger gaps, synthetic timelines on the invented fixtures ({trials} random phases per cell), toast region {x1 - x0}x{y1 - y0}px")

    print("\nflash: a toast on screen for D seconds -> chance it is seen at all / mean delay to the event (s)")
    ds = (0.2, 0.4, 0.6, 1.0, 2.0)
    print(f"{'D (s)':>8} " + " ".join(f"{'T=' + str(T):>14}" for T in intervals))
    for D in ds:
        row = []
        for T in intervals:
            seen, lat = 0, []
            for _ in range(trials):
                ph = rnd.uniform(0, T)
                t0 = 3.0
                ev_ = run(lambda t: with_toast if t0 <= t < t0 + D else base, T, ph, t0 + D + 3 * T)
                hit = [t for t, _ in ev_ if t0 <= t < t0 + D]    # a sample landed inside the toast: the appearance is seen
                if hit:
                    seen += 1
                    lat.append(hit[0] - t0)
            row.append(f"{seen / trials:5.0%} /{(np.mean(lat) if lat else float('nan')):5.2f}s")
        print(f"{D:8.1f} " + " ".join(f"{r:>14}" for r in row))

    print("\nsteady: a change that stays -> mean / worst delay to the event (s)")
    for T in intervals:
        lat = []
        for _ in range(trials):
            ph, t0 = rnd.uniform(0, T), 3.0
            e = run(lambda t: with_toast if t >= t0 else base, T, ph, t0 + 3 * T)
            lat.append([t for t, _ in e if t >= t0][0] - t0)
        print(f"   T={T}: mean {np.mean(lat):.2f}  worst {max(lat):.2f}")

    print("\nmerge: change A then change B g seconds later -> chance the two collapse into ONE event (intermediate screen never seen)")
    gs = (0.3, 0.6, 1.0)
    print(f"{'g (s)':>8} " + " ".join(f"{'T=' + str(T):>8}" for T in intervals))
    for g in gs:
        row = []
        for T in intervals:
            merged = 0
            for _ in range(trials):
                ph, t0 = rnd.uniform(0, T), 3.0
                e = run(lambda t: base if t < t0 else (other if t < t0 + g else third), T, ph, t0 + g + 3 * T)
                merged += len([1 for t, _ in e if t >= t0]) < 2   # two separate events = both screens were seen; fewer = the intermediate state was lost
            row.append(f"{merged / trials:8.0%}")
        print(f"{g:8.1f} " + " ".join(f"{r:>8}" for r in row))

    print("\nprogress: a bar filling over 10 s -> events fired / mean changed-region width (px) per event")
    bx0, bx1, by0, by1 = 200, 1080, 400, 440

    def bar(t):
        f = base.copy()
        w = int(min(1.0, max(0.0, t / 10.0)) * (bx1 - bx0))
        f[by0:by1, bx0:bx0 + w, :3] = (60, 160, 60)
        return f
    for T in intervals:
        e = run(bar, T, 0.0, 10.5)
        widths = [c.bbox[2] - c.bbox[0] for _, c in e]
        print(f"   T={T}: {len(e):3d} events, mean region {np.mean(widths):5.0f}px, per-event new bar {(bx1 - bx0) / max(1, len(e)):5.0f}px")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-s", type=int, default=25)
    ap.add_argument("--trials", type=int, default=60)
    ap.add_argument("--intervals", default="0.5,0.7,1.0,1.2")
    ap.add_argument("--skip-live", action="store_true")
    a = ap.parse_args()
    iv = [float(x) for x in a.intervals.split(",")]
    if not a.skip_live:
        part_a(iv, a.live_s)
    part_b(iv, a.trials)


if __name__ == "__main__":
    main()
