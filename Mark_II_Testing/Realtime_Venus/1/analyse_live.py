"""Summarise the last live_duplex.py run: delay, and whether Venus answered when you talked.

    venv\\Scripts\\python.exe analyse_live.py
"""

import re
from pathlib import Path

log = (Path(__file__).parent / "live_duplex.log").read_text(encoding="utf-8").split("\n=== ")[-1].splitlines()
pat = re.compile(r"speak\s+(\d+)% \| (\S+).*?mic\s+(-?\d+) dB.*?\| ([\d.]+)s/unit.*?lag ([\d.]+)s.*\|\s*(listen|speak .*)$")
lg = re.compile(r"logit listen (-?[\d.]+) best_other (-?[\d.]+)")
rows = []
for line in log[1:]:
    m = pat.search(line)
    if m:
        g = lg.search(line)
        rows.append(dict(t=line[:8], p=int(m[1]), fr=m[2], db=int(m[3]), unit=float(m[4]), lag=float(m[5]), out=m[6],
                         L=float(g[1]) if g else None, O=float(g[2]) if g else None))
n = len(rows)
print(f"run {log[0][:19]}: {n} s, {sum(r['out'] != 'listen' for r in rows)} speaking s; frames: "
      + ", ".join(f"{k} {sum(r['fr'] == k for r in rows)}" for k in ("frame+crop", "frame", "skip")))
u = sorted(r["unit"] for r in rows)
print(f"unit time median {u[n // 2]:.2f}s, p90 {u[int(n * .9)]:.2f}s, max {u[-1]:.2f}s; seconds with lag > 0.5 s: "
      f"{sum(r['lag'] > 0.5 for r in rows)}")

# You talking = consecutive seconds with mic above -40 dB (after gain).
bursts, cur = [], []
for i, r in enumerate(rows):
    if r["db"] > -40:
        cur.append(i)
    elif cur:
        bursts.append(cur)
        cur = []
if cur:
    bursts.append(cur)
answered = 0
for b in bursts:
    win = rows[b[0]: b[-1] + 6]
    replies = [r["out"] for r in win if r["out"] != "listen"]
    answered += bool(replies)
    start = next((j - b[-1] for j in range(b[-1] + 1, min(n, b[-1] + 8)) if rows[j]["out"] != "listen"), None)
    print(f"  {rows[b[0]]['t']} you talked {len(b)}s (peak {max(rows[i]['db'] for i in b)} dB): "
          f"answered={bool(replies)}, max speak% {max(r['p'] for r in win)}, reply starts {start}s after you stop"
          + (f" -> {replies[0][:60]}" if replies else ""))
print(f"answered {answered}/{len(bursts)} times you talked")

# --listen-scale from data. Upstream multiplies the <|listen|> logit L by the
# scale; greedily, speaking wins once L*s < best other logit O, i.e. s < O/L
# (for L > 0). Misses = seconds right after you talked where it listened;
# idle = seconds with no talking nearby where it listened (speaking there
# would be unprompted: wanted if something on screen changed, noise if not).
near = {j for b in bursts for j in range(b[0], min(n, b[-1] + 3))}
def need(r):
    return r["O"] / r["L"] if r["L"] and r["L"] > 0 else None
miss = [need(rows[j]) for b in bursts for j in range(b[-1] + 1, min(n, b[-1] + 3))
        if rows[j]["out"] == "listen" and need(rows[j]) is not None
        and not any(r["out"] != "listen" for r in rows[b[0]: b[-1] + 6])]
idle = [need(r) for i, r in enumerate(rows) if i not in near and r["out"] == "listen" and need(r) is not None]
if miss or idle:
    q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(len(xs) * f))] if xs else float("nan")
    print(f"scale that would have answered missed turns: median {q(miss, .5):.2f}, 90% of misses by {q(miss, .1):.2f} "
          f"({len(miss)} s)")
    print(f"scale at which idle seconds start speaking unprompted: 1% of idle s by {q(idle, .99):.2f}, "
          f"10% by {q(idle, .9):.2f} ({len(idle)} s)")
    for s in (0.95, 0.9, 0.85, 0.8, 0.7):
        print(f"  --listen-scale {s}: answers {sum(x > s for x in miss)}/{len(miss)} missed-turn s, "
              f"speaks in {sum(x > s for x in idle)}/{len(idle)} idle s")
else:
    print("(no logit data in this run -- it predates logit logging)")
