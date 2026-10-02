"""Can Venus read small terminal text? Scores exact reads per frame setting.

Renders a 1920x1080 dark terminal with 16 px Consolas (Command Prompt default)
holding random codes, streams it with near-silent audio, asks a question, and
checks the spoken answer for the exact code. One model load, all settings.

    venv\\Scripts\\python.exe readtest.py
"""

import random
import re
import time

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from live_duplex import focus_crop, grey_tiles, tile_diffs
from smoke_nf4 import HERE, load_duplex, log

# (scale_resolution, max_slice_nums, focus crop). Run 1 (2026-09-28): 448/1 0/9,
# 672/1 0/9, 896/1 0/9, 448/2 0/9, 448/4 2/9 -- only the new crop settings rerun.
SETTINGS = [(448, 1, False), (448, 1, True), (672, 1, True)]
TRIALS = 3
FOCUS_THR = (1 - 0.990) * 255  # same per-tile threshold as live_duplex --similar 0.990


def terminal(rng):
    code, got, total = rng.randint(1000, 9999), rng.randint(10, 49), rng.randint(50, 99)
    branch = f"feature/{rng.choice(['login', 'billing', 'search', 'upload'])}-{rng.randint(100, 999)}"
    lines = [
        "Microsoft Windows [Version 10.0.26200.9457]",
        "(c) Microsoft Corporation. All rights reserved.",
        "",
        r"C:\Projects\alpha>npm install",
        f"Downloaded {got} of {total} packages",
        "npm warn deprecated inflight@1.0.6: This module is not supported",
        r"C:\Projects\alpha>npm run build",
        f"Error: build failed with exit code {code} in module payments",
        "",
        rf"C:\Projects\alpha>git checkout {branch}",
    ]
    blank = Image.new("RGB", (1920, 1080), (12, 12, 12))
    img = blank.copy()
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 16)
    for i, line in enumerate(lines):
        d.text((8, 8 + i * 19), line, font=font, fill=(204, 204, 204))
    qa = [
        ("What exit code does the error line in the terminal show? Answer with the number.", str(code)),
        ("How many packages were downloaded, out of how many? Answer with the two numbers.", f"{got}|{total}"),
        ("Which git branch is being checked out on the last line?", branch.split("-")[-1]),
    ]
    # What the live detector would crop: the text appearing on an empty terminal.
    crop = focus_crop(img, tile_diffs(grey_tiles(img), grey_tiles(blank)), FOCUS_THR)
    return img, crop, qa


def ask(duplex, frames, question, slices):
    duplex.prepare()
    quiet = (np.random.randn(16000) * 1e-4).astype(np.float32)
    text, t_img = "", []
    for unit in range(1, 25):
        t = time.perf_counter()
        duplex.streaming_prefill(audio_waveform=quiet, frame_list=frames, max_slice_nums=slices,
                                 text_list=[question] if unit == 3 else None, batch_vision_feed=True)
        t_img.append(time.perf_counter() - t)
        r = duplex.streaming_generate()
        if not r["is_listen"]:
            text += r["text"]
        elif text:  # answered and went back to listening
            break
    return text.strip(), sum(t_img) / len(t_img)


def hit(answer, want):
    nums = set(re.findall(r"\d+", answer))
    return all(w in nums for w in want.split("|"))


def main():
    duplex = load_duplex(speech=False)
    rng = random.Random(7)
    screens = [terminal(rng) for _ in range(TRIALS)]
    screens[0][0].save(HERE / "readtest_terminal.png")  # synthetic, safe to keep
    screens[0][1].save(HERE / "readtest_crop.png")
    log(f"focus crop size {screens[0][1].size}")
    summary = []
    for scale, slices, focus in SETTINGS:
        duplex.processor.image_processor.scale_resolution = scale
        ok = n = 0
        pre = []
        tag = f"scale {scale} slices {slices} crop {'on ' if focus else 'off'}"
        for img, crop, qa in screens:
            for q, want in qa:
                ans, p = ask(duplex, [img, crop] if focus else [img], q, slices)
                pre.append(p)
                n += 1
                ok += hit(ans, want)
                log(f"{tag} | want {want:>9} | {'OK ' if hit(ans, want) else 'MISS'} | {ans[:90]!r}")
        summary.append((tag, ok, n, sum(pre) / len(pre)))
        torch.cuda.empty_cache()
    log("=== results (16 px Consolas on 1920x1080)")
    for tag, ok, n, p in summary:
        log(f"{tag} : {ok}/{n} exact reads, mean prefill {p:.2f} s/unit")


if __name__ == "__main__":
    main()
