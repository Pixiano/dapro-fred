"""
Empirical calibration of the frame-skip similarity threshold for live_duplex.py.
CPU only. No ML. No screenshots of the real screen are ever written to disk
(only aggregate numbers). Synthetic scenes may be saved.

Usage:
    python calibrate.py            # full run (~2.5 min, includes 2 min passive sample)
    python calibrate.py --fast     # skip the 2 min passive sample (for iterating)
"""
import ctypes
import json
import random
import statistics
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageGrab

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"
OUT_DIR = r"C:\Users\Dhiraj Vatsal\VatsalDaPro\Projects\Project_FRED\Mark_II_Testing\Realtime_Venus\1\skip_threshold"

R_DEFAULT = getattr(Image, "Resampling", Image).BICUBIC  # PIL default for resize() on "L"
R_BOX = getattr(Image, "Resampling", Image).BOX

rng = random.Random(42)

# ---------------------------------------------------------------- metrics --

def gray_np(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def sim_thumb(gray_l_img, size, resample):
    """gray_l_img: PIL 'L' image (full res). Returns (thumb_ndarray, seconds)."""
    t0 = time.perf_counter()
    thumb = np.asarray(gray_l_img.resize(size, resample), dtype=np.float32)
    dt = time.perf_counter() - t0
    return thumb, dt


def sim_from_thumbs(a, b):
    return 1 - float(np.abs(a - b).mean()) / 255.0


def sim_tilemax(gray_a, gray_b, tiles=(16, 9)):
    t0 = time.perf_counter()
    h, w = gray_a.shape
    tw, th = w // tiles[0], h // tiles[1]
    worst = 0.0
    for j in range(tiles[1]):
        for i in range(tiles[0]):
            a = gray_a[j * th:(j + 1) * th, i * tw:(i + 1) * tw]
            b = gray_b[j * th:(j + 1) * th, i * tw:(i + 1) * tw]
            d = float(np.abs(a - b).mean())
            if d > worst:
                worst = d
    dt = time.perf_counter() - t0
    return 1 - worst / 255.0, dt


METRICS = [
    ("current_64x36_default", (64, 36), R_DEFAULT),
    ("64x36_BOX", (64, 36), R_BOX),
    ("128x72_BOX", (128, 72), R_BOX),
    ("160x90_BOX", (160, 90), R_BOX),
    ("256x144_BOX", (256, 144), R_BOX),
]


def compute_all(gray_before_l, gray_after_l, gray_before_np, gray_after_np):
    """gray_before_l/after_l: PIL 'L' images (full res). *_np: float32 ndarrays of same."""
    out = {}
    for name, size, resample in METRICS:
        ta, dta = sim_thumb(gray_before_l, size, resample)
        tb, dtb = sim_thumb(gray_after_l, size, resample)
        sim = sim_from_thumbs(ta, tb)
        out[name] = (sim, dta + dtb)
    sim, dt = sim_tilemax(gray_before_np, gray_after_np)
    out["tile_max_16x9"] = (sim, dt)
    return out


# ------------------------------------------------------------ scene build --

def font(sz):
    return ImageFont.truetype(FONT_PATH, sz)


def synth_terminal(seed=0):
    r = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), (12, 12, 12))
    d = ImageDraw.Draw(img)
    f = font(16)
    lines = [
        "C:\\Users\\dev> git status", "On branch main", "nothing to commit, working tree clean",
        "C:\\Users\\dev> python run.py", "loading model...", "listening on :8080",
        "[INFO] request handled in 12ms", "[INFO] request handled in 9ms",
    ]
    y = 20
    for i in range(50):
        txt = r.choice(lines) + f" #{i}"
        d.text((20, y), txt, font=f, fill=(210, 210, 210))
        y += 18
    return img


def synth_document(seed=0):
    r = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), (250, 250, 248))
    d = ImageDraw.Draw(img)
    f = font(18)
    words = ["the", "quick", "report", "shows", "that", "system", "latency", "remains",
             "within", "budget", "across", "all", "regions", "and", "services", "today"]
    y = 60
    for i in range(40):
        line = " ".join(r.choice(words) for _ in range(r.randint(8, 14)))
        d.text((100, y), line, font=f, fill=(30, 30, 30))
        y += 26
    return img


def synth_editor(seed=0):
    r = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), (30, 30, 30))
    d = ImageDraw.Draw(img)
    f = font(16)
    colors = [(86, 156, 214), (206, 145, 120), (106, 153, 85), (220, 220, 170), (180, 180, 180)]
    for i in range(55):
        d.text((10, 20 + i * 18), f"{i+1:>4}", font=f, fill=(100, 100, 100))
        line = "".join(r.choice("abcdefghij = ():.\"'") for _ in range(r.randint(10, 50)))
        d.text((70, 20 + i * 18), line, font=f, fill=r.choice(colors))
    return img


def real_base():
    """One in-memory grab of the real screen. Never written to disk."""
    img = ImageGrab.grab()
    return img.convert("RGB")


# ------------------------------------------------------------ edit makers --
# Each returns (before, after, label, subtype)

def edit_new_terminal_line(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y = r.randint(0, 400), r.randint(0, 900)
    w, h = 480, 140
    f = font(16)
    d0 = ImageDraw.Draw(before)
    d0.rectangle([x, y, x + w, y + h], fill=(10, 10, 10))
    for i in range(6):
        d0.text((x + 8, y + 8 + i * 18), f"line {i} output ready", font=f, fill=(200, 200, 200))
    after = before.copy()
    d1 = ImageDraw.Draw(after)
    d1.text((x + 8, y + 8 + 6 * 18), "new output line appeared here", font=f, fill=(200, 200, 200))
    return before, after, "must_change", "new_terminal_line"


def edit_typed_words(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y = r.randint(0, 500), r.randint(0, 950)
    f = font(16)
    d0 = ImageDraw.Draw(before)
    d0.rectangle([x, y, x + 500, y + 20], fill=(10, 10, 10))
    d0.text((x + 4, y + 2), "C:\\Users\\dev>", font=f, fill=(200, 200, 200))
    after = before.copy()
    d1 = ImageDraw.Draw(after)
    d1.text((x + 4, y + 2), "C:\\Users\\dev> git", font=f, fill=(200, 200, 200))
    return before, after, "must_change", "typed_words"


def edit_toast(base, seed):
    before = base.copy()
    after = before.copy()
    x, y, w, h = 1920 - 380, 1080 - 120, 360, 100
    d = ImageDraw.Draw(after)
    d.rectangle([x, y, x + w, y + h], fill=(40, 40, 45), outline=(90, 90, 95))
    d.text((x + 16, y + 16), "New notification", font=font(16), fill=(240, 240, 240))
    d.text((x + 16, y + 44), "Something happened just now", font=font(14), fill=(190, 190, 190))
    return before, after, "must_change", "toast"


def edit_dialog(base, seed):
    before = base.copy()
    after = before.copy()
    x, y, w, h = 660, 390, 600, 300
    d = ImageDraw.Draw(after)
    d.rectangle([x, y, x + w, y + h], fill=(240, 240, 240), outline=(60, 60, 60))
    d.text((x + 24, y + 24), "Warning", font=font(20), fill=(20, 20, 20))
    d.text((x + 24, y + 64), "Do you want to continue?", font=font(16), fill=(20, 20, 20))
    return before, after, "must_change", "dialog"


def edit_window_switch(a_scene, b_scene, seed):
    return a_scene, b_scene, "must_change", "window_switch"


def edit_scroll(base, seed):
    r = random.Random(seed)
    before = base.copy()
    # fixed text-dense region (top-left, where synth scenes always draw lines) +
    # small jitter that stays inside it, so the shift always crosses real content
    x, y, w, h = 20 + r.randint(0, 10), 20 + r.randint(0, 10), 700, 500
    arr = np.asarray(before, dtype=np.uint8).copy()
    region = arr[y:y + h, x:x + w].copy()
    shift = 3 * 16  # 3 lines of 16px text
    shifted = np.empty_like(region)
    shifted[:-shift] = region[shift:]
    shifted[-shift:] = region[-1:]  # bottom rows repeat last row (new content scrolled in)
    arr[y:y + h, x:x + w] = shifted
    after = Image.fromarray(arr, "RGB")
    return before, after, "must_change", "scroll"


def edit_video_region(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y, w, h = r.randint(0, 1400), r.randint(0, 800), 480, 270
    noise1 = np.asarray(before, dtype=np.uint8).copy()
    frame1 = np.random.RandomState(seed).randint(0, 255, (h, w, 3), dtype=np.uint8)
    noise1[y:y + h, x:x + w] = frame1
    before = Image.fromarray(noise1, "RGB")
    frame2 = np.random.RandomState(seed + 999).randint(0, 255, (h, w, 3), dtype=np.uint8)
    noise2 = np.asarray(before, dtype=np.uint8).copy()
    noise2[y:y + h, x:x + w] = frame2
    after = Image.fromarray(noise2, "RGB")
    return before, after, "must_change", "video_region"


def edit_caret(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y = r.randint(0, 1800), r.randint(0, 1050)
    d = ImageDraw.Draw(before)
    d.rectangle([x, y, x + 2, y + 16], fill=(230, 230, 230))
    after = base.copy()  # caret off, rest identical
    return before, after, "must_skip", "caret_blink"


def edit_clock_tick(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y = 1920 - 80, 1080 - 24
    f = font(14)
    d0 = ImageDraw.Draw(before)
    d0.rectangle([x, y, x + 60, y + 16], fill=(20, 20, 20))
    m1 = r.randint(0, 58)
    d0.text((x + 2, y), f"10:{m1:02d}", font=f, fill=(200, 200, 200))
    after = before.copy()
    d1 = ImageDraw.Draw(after)
    d1.rectangle([x, y, x + 60, y + 16], fill=(20, 20, 20))
    d1.text((x + 2, y), f"10:{m1+1:02d}", font=f, fill=(200, 200, 200))
    return before, after, "must_skip", "clock_tick"


def edit_hover(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y = r.randint(0, 1700), r.randint(0, 1000)
    d0 = ImageDraw.Draw(before)
    d0.rectangle([x, y, x + 120, y + 28], fill=(50, 50, 50))
    after = before.copy()
    d1 = ImageDraw.Draw(after)
    d1.rectangle([x, y, x + 120, y + 28], fill=(65, 65, 65))
    return before, after, "must_skip", "hover_highlight"


def edit_noise(base, seed):
    before = base.copy()
    arr = np.asarray(before, dtype=np.int16)
    noise = np.random.RandomState(seed).randint(-3, 4, arr.shape, dtype=np.int16)
    after_arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    after = Image.fromarray(after_arr, "RGB")
    return before, after, "must_skip", "jpeg_noise"


def edit_wallpaper_margin(base, seed):
    r = random.Random(seed)
    before = base.copy()
    x, y, w, h = 0, 0, 1920, 30
    arr = np.asarray(before, dtype=np.uint8).copy()
    after_arr = arr.copy()
    delta = r.choice([-6, -4, -3, -2, 2, 3, 4, 6])
    strip = after_arr[y:y + h, x:x + w].astype(np.int16) + delta
    after_arr[y:y + h, x:x + w] = np.clip(strip, 0, 255).astype(np.uint8)
    after = Image.fromarray(after_arr, "RGB")
    return before, after, "must_skip", "wallpaper_margin"


def real_mouse_cursor_test():
    """Verify ImageGrab excludes the cursor: grab, move cursor, grab again, restore."""
    import ctypes.wintypes as wt
    a = ImageGrab.grab().convert("RGB")
    orig = wt.POINT()
    try:
        ctypes.windll.user32.GetCursorPos(ctypes.byref(orig))
        ctypes.windll.user32.SetCursorPos(orig.x + 40, orig.y + 40)
        b = ImageGrab.grab().convert("RGB")
    finally:
        ctypes.windll.user32.SetCursorPos(orig.x, orig.y)
    return a, b, "must_skip", "mouse_cursor_move"


# --------------------------------------------------------------- pipeline --

def build_pairs():
    base = real_base()
    t = synth_terminal(1)
    doc = synth_document(2)
    ed = synth_editor(3)

    pairs = []
    for seed in range(4):
        pairs.append(edit_new_terminal_line(base, seed))
        pairs.append(edit_new_terminal_line(t, 100 + seed))
    for seed in range(3):
        pairs.append(edit_typed_words(base, seed))
        pairs.append(edit_typed_words(t, 100 + seed))
    for seed in range(3):
        pairs.append(edit_toast(base, seed))
        pairs.append(edit_toast(doc, 100 + seed))
    for seed in range(3):
        pairs.append(edit_dialog(base, seed))
        pairs.append(edit_dialog(ed, 100 + seed))
    pairs.append(edit_window_switch(t, doc, 0))
    pairs.append(edit_window_switch(doc, ed, 1))
    pairs.append(edit_window_switch(ed, t, 2))
    pairs.append(edit_window_switch(base, t, 3))
    for seed in range(3):
        pairs.append(edit_scroll(doc, seed))
        pairs.append(edit_scroll(t, 100 + seed))
    for seed in range(3):
        pairs.append(edit_video_region(base, seed))
        pairs.append(edit_video_region(ed, 100 + seed))

    for seed in range(6):
        pairs.append(edit_caret(base, seed))
        pairs.append(edit_caret(t, 100 + seed))
    for seed in range(4):
        pairs.append(edit_clock_tick(base, seed))
        pairs.append(edit_clock_tick(doc, 100 + seed))
    for seed in range(4):
        pairs.append(edit_hover(base, seed))
        pairs.append(edit_hover(ed, 100 + seed))
    for seed in range(4):
        pairs.append(edit_noise(base, seed))
        pairs.append(edit_noise(t, 100 + seed))
    for seed in range(3):
        pairs.append(edit_wallpaper_margin(base, seed))
        pairs.append(edit_wallpaper_margin(doc, 100 + seed))
    for _ in range(4):
        pairs.append(real_mouse_cursor_test())

    del base  # drop real-screen reference; no real image ever saved to disk
    return pairs


def run_pairs(pairs):
    results = {m[0]: {"must_change": [], "must_skip": []} for m in METRICS}
    results["tile_max_16x9"] = {"must_change": [], "must_skip": []}
    timings = {name: [] for name in list(results.keys())}
    subtype_rows = []

    for before, after, label, subtype in pairs:
        gb_l = before.convert("L")
        ga_l = after.convert("L")
        gb_np = np.asarray(gb_l, dtype=np.float32)
        ga_np = np.asarray(ga_l, dtype=np.float32)
        out = compute_all(gb_l, ga_l, gb_np, ga_np)
        row = {"label": label, "subtype": subtype}
        for name, (sim, dt) in out.items():
            results[name][label].append(sim)
            timings[name].append(dt)
            row[name] = round(sim, 5)
        subtype_rows.append(row)

    return results, timings, subtype_rows


def passive_sample(seconds=120):
    """Real screen, 1 Hz, in-memory only. Returns per-metric similarity list
    (consecutive-frame) -- numbers only, no images retained after each step."""
    sims = {name: [] for name in [m[0] for m in METRICS]}
    sims["tile_max_16x9"] = []
    prev = None
    t_end = time.time() + seconds
    while time.time() < t_end:
        img = ImageGrab.grab().convert("RGB")
        gl = img.convert("L")
        gnp = np.asarray(gl, dtype=np.float32)
        if prev is not None:
            pgl, pnp = prev
            for name, size, resample in METRICS:
                a, _ = sim_thumb(pgl, size, resample)
                b, _ = sim_thumb(gl, size, resample)
                sims[name].append(sim_from_thumbs(a, b))
            s, _ = sim_tilemax(pnp, gnp)
            sims["tile_max_16x9"].append(s)
        prev = (gl, gnp)
        del img
        time.sleep(1.0)
    return sims


def time_full_pipeline(n=15):
    """Time grab + convert + resize + diff, per metric, on real frames."""
    out = {name: [] for name in [m[0] for m in METRICS]}
    out["tile_max_16x9"] = []
    prev = None
    for _ in range(n):
        t0 = time.perf_counter()
        img = ImageGrab.grab().convert("RGB")
        gl = img.convert("L")
        gnp = np.asarray(gl, dtype=np.float32)
        t_grab = time.perf_counter() - t0
        if prev is not None:
            pgl, pnp = prev
            for name, size, resample in METRICS:
                t1 = time.perf_counter()
                a, _ = sim_thumb(pgl, size, resample)
                b, _ = sim_thumb(gl, size, resample)
                _ = sim_from_thumbs(a, b)
                out[name].append((time.perf_counter() - t1 + t_grab) * 1000)
            t1 = time.perf_counter()
            _ = sim_tilemax(pnp, gnp)
            out["tile_max_16x9"].append((time.perf_counter() - t1 + t_grab) * 1000)
        prev = (gl, gnp)
        del img
    return out


def stat(v):
    if not v:
        return (float("nan"),) * 3
    return (min(v), statistics.median(v), max(v))


def choose_threshold(change_sims, skip_sims):
    if not change_sims:
        return None
    max_change = max(change_sims)
    min_skip = min(skip_sims) if skip_sims else 1.0
    gap = min_skip - max_change
    T = max_change + 1e-6
    false_skip = sum(1 for s in change_sims if s >= T) / len(change_sims)
    false_send = sum(1 for s in skip_sims if s < T) / len(skip_sims) if skip_sims else 0.0
    return {"threshold": T, "gap": gap, "false_skip": false_skip, "false_send": false_send}


def main():
    fast = "--fast" in sys.argv
    print("Building pairs (includes one in-memory real-screen grab)...")
    pairs = build_pairs()
    print(f"{len(pairs)} pairs built "
          f"({sum(1 for p in pairs if p[2]=='must_change')} must_change, "
          f"{sum(1 for p in pairs if p[2]=='must_skip')} must_skip)")

    results, timings, subtype_rows = run_pairs(pairs)

    print("Timing full pipeline on real frames...")
    pipeline_ms = time_full_pipeline(n=15)

    idle = {}
    if not fast:
        print("Passive idle sampling for ~120s (real screen, in-memory only, numbers kept)...")
        idle = passive_sample(120)
    else:
        print("Skipping passive sample (--fast).")

    analysis = {}
    for name in [m[0] for m in METRICS] + ["tile_max_16x9"]:
        ch = results[name]["must_change"]
        sk = results[name]["must_skip"]
        thr = choose_threshold(ch, sk)
        idle_skip_rate = None
        if idle.get(name):
            idle_skip_rate = sum(1 for s in idle[name] if s >= thr["threshold"]) / len(idle[name])
        analysis[name] = {
            "change_stat": stat(ch),
            "skip_stat": stat(sk),
            "threshold": thr,
            "avg_ms": statistics.mean(pipeline_ms[name]) if pipeline_ms[name] else None,
            "compute_only_ms": statistics.mean(timings[name]) * 1000 if timings[name] else None,
            "idle_stat": stat(idle.get(name, [])),
            "idle_skip_rate": idle_skip_rate,
        }

    # ---- write results.md ----
    lines = []
    lines.append("# Frame-skip threshold calibration\n")
    lines.append(f"Pairs: {len(pairs)} total "
                  f"({sum(1 for p in pairs if p[2]=='must_change')} must_change / "
                  f"{sum(1 for p in pairs if p[2]=='must_skip')} must_skip)\n")
    if idle:
        lines.append(f"Passive idle sample: {len(next(iter(idle.values())))} consecutive-second "
                      "diffs from the real screen (numbers only, no images retained).\n")
    else:
        lines.append("Passive idle sample: SKIPPED (--fast run) -- idle_skip_rate below is UNVERIFIED.\n")

    lines.append("\n## Evidence table\n")
    lines.append("| metric | must_change min/med/max | must_skip min/med/max | threshold | false_skip | false_send | idle skip rate | ms/frame (grab+compute) | ms compute-only |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name, a in analysis.items():
        cs = a["change_stat"]; ss = a["skip_stat"]; th = a["threshold"]
        isr = f"{a['idle_skip_rate']:.3f}" if a["idle_skip_rate"] is not None else "n/a"
        lines.append(
            f"| {name} | {cs[0]:.4f}/{cs[1]:.4f}/{cs[2]:.4f} | {ss[0]:.4f}/{ss[1]:.4f}/{ss[2]:.4f} "
            f"| {th['threshold']:.5f} | {th['false_skip']:.3f} | {th['false_send']:.3f} "
            f"| {isr} | {a['avg_ms']:.2f} | {a['compute_only_ms']:.3f} |"
        )

    lines.append("\n## Per-subtype raw values (crafted pairs)\n")
    lines.append("| label | subtype | " + " | ".join(n for n, *_ in METRICS + [("tile_max_16x9", None, None)]) + " |")
    lines.append("|---|---|" + "---|" * (len(METRICS) + 1))
    for row in subtype_rows:
        vals = " | ".join(f"{row[n]:.4f}" for n, *_ in METRICS + [("tile_max_16x9", None, None)])
        lines.append(f"| {row['label']} | {row['subtype']} | {vals} |")

    if idle:
        lines.append("\n## Idle distribution (real screen, 1 Hz, ~120s)\n")
        lines.append("| metric | min | median | max |")
        lines.append("|---|---|---|---|")
        for name, a in analysis.items():
            ist = a["idle_stat"]
            lines.append(f"| {name} | {ist[0]:.5f} | {ist[1]:.5f} | {ist[2]:.5f} |")

    with open(OUT_DIR + r"\results.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(OUT_DIR + r"\raw_results.json", "w", encoding="utf-8") as f:
        json.dump({"subtype_rows": subtype_rows,
                    "idle": idle,
                    "pipeline_ms": pipeline_ms}, f, indent=1)

    print("\nDone. Wrote results.md and raw_results.json")
    for name, a in analysis.items():
        th = a["threshold"]
        print(f"{name}: thr={th['threshold']:.5f} false_skip={th['false_skip']:.3f} "
              f"false_send={th['false_send']:.3f} avg_ms={a['avg_ms']:.2f} "
              f"compute_ms={a['compute_only_ms']:.3f} idle_skip={a['idle_skip_rate']}")


if __name__ == "__main__":
    main()
