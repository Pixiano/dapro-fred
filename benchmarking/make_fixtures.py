# benchmarking/make_fixtures.py
#
# Deterministic synthetic screenshots for the multimodal cases
# (cases/multimodal.jsonl). Plain PIL drawing -- no model involved.
# Output: benchmarking/fixtures/*.png (gitignored, regenerate any time).
#
#   python benchmarking/make_fixtures.py
#
# Speech clips (fixtures/speech/*.wav + manifest.json) come from Kokoro on
# CPU -- the one TTS installed, and Vatsal OK'd CPU for it 2026-09-27.
#
# Not generated yet: video streams (boring_30min, error_at_12min,
# meeting_45min) -- no candidate that takes video exists locally yet.

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parent
CORE = ROOT.parent / "Core"
OUT = ROOT / "fixtures"
NOISE = CORE / "data" / "wakeword_training" / "demand_noise" / "OOFFICE_16k.wav"
VOICES = ("am_michael", "af_heart")  # FRED's own voice, plus a contrasting one
NOISY_SNR_DB = 5
SR = 16000
W, H = 1280, 720
MONO = "C:/Windows/Fonts/consola.ttf"
SANS = "C:/Windows/Fonts/segoeui.ttf"
BOLD = "C:/Windows/Fonts/segoeuib.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def canvas(bg="#1e1e1e"):
    img = Image.new("RGB", (W, H), bg)
    return img, ImageDraw.Draw(img)


def title_bar(d, text, bg="#323233", fg="#cccccc"):
    d.rectangle([0, 0, W, 34], fill=bg)
    d.text((14, 6), text, font=font(SANS, 17), fill=fg)


def lines(d, rows, x=20, y=50, size=18, color="#d4d4d4", gap=26):
    f = font(MONO, size)
    for i, row in enumerate(rows):
        text, fill = row if isinstance(row, tuple) else (row, color)
        d.text((x, y + i * gap), text, font=f, fill=fill)


def terminal(name, title, rows):
    img, d = canvas("#0c0c0c")
    title_bar(d, title)
    lines(d, rows)
    img.save(OUT / name)


def cuda_oom():
    terminal("cuda_oom.png", "Windows PowerShell - python train.py", [
        "PS C:\\projects\\vision> python train.py --batch-size 64",
        "Loading dataset... 48,000 images",
        "Epoch 1/20",
        "  3%|##        | 24/750 [00:09<04:41, 2.58it/s]",
        ("Traceback (most recent call last):", "#f48771"),
        '  File "train.py", line 88, in <module>',
        "    loss = model(images).loss",
        ('torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB.', "#f48771"),
        ("GPU 0 has a total capacity of 15.93 GiB of which 412.00 MiB is free.", "#f48771"),
        "PS C:\\projects\\vision> _",
    ])


def traceback_keyerror():
    terminal("traceback_keyerror.png", "Command Prompt - python app.py", [
        "C:\\projects\\shop>python app.py",
        " * Serving on http://127.0.0.1:5000",
        "127.0.0.1 - - [27/Sep/2026 14:02:11] \"POST /login HTTP/1.1\" 500 -",
        ("Traceback (most recent call last):", "#f48771"),
        '  File "app.py", line 41, in login',
        '    session_id = request.json["user_id"]',
        ("KeyError: 'user_id'", "#f48771"),
    ])


def browser(name, url, body_draw, tab="New Tab"):
    img, d = canvas("#ffffff")
    d.rectangle([0, 0, W, 40], fill="#dee1e6")
    d.rounded_rectangle([10, 6, 260, 40], 8, fill="#ffffff")
    d.text((22, 12), tab, font=font(SANS, 15), fill="#202124")
    d.rectangle([0, 40, W, 84], fill="#ffffff")
    d.rounded_rectangle([90, 48, W - 90, 78], 15, fill="#f1f3f4")
    d.text((110, 52), url, font=font(SANS, 17), fill="#202124")
    body_draw(img, d)
    img.save(OUT / name)


def browser_404():
    def body(img, d):
        d.text((460, 250), "404", font=font(BOLD, 110), fill="#5f6368")
        d.text((440, 400), "Page Not Found", font=font(SANS, 40), fill="#202124")
        d.text((330, 470), "The requested URL /docs/v2/setup was not found on this server.",
               font=font(SANS, 18), fill="#5f6368")
    browser("browser_404.png", "https://docs.example.com/docs/v2/setup", body, "404 Not Found")


def phishing_login():
    def body(img, d):
        d.text((545, 150), "PayPal", font=font(BOLD, 56), fill="#003087")
        d.text((470, 250), "Your account has been limited.", font=font(SANS, 24), fill="#c0392b")
        d.text((455, 290), "Log in within 24 hours to avoid suspension.", font=font(SANS, 18), fill="#202124")
        for y, label in ((350, "Email or mobile number"), (420, "Password")):
            d.rounded_rectangle([440, y, 840, y + 50], 6, outline="#9aa0a6", width=2)
            d.text((455, y + 13), label, font=font(SANS, 17), fill="#80868b")
        d.rounded_rectangle([440, 500, 840, 550], 25, fill="#0070ba")
        d.text((610, 510), "Log In", font=font(BOLD, 20), fill="#ffffff")
    browser("phishing_login.png", "http://paypa1-secure-login.example/verify", body, "Log in to your account")


def disk_full_toast():
    img, d = canvas("#1f3b5c")
    d.rectangle([0, H - 48, W, H], fill="#202020")
    d.rounded_rectangle([W - 420, H - 220, W - 20, H - 60], 10, fill="#2b2b2b")
    d.text((W - 400, H - 205), "Storage", font=font(BOLD, 18), fill="#ffffff")
    d.text((W - 400, H - 170), "Low disk space on Local Disk (C:)", font=font(SANS, 18), fill="#ffffff")
    d.text((W - 400, H - 138), "You're running out of space. 1.2 GB free of 476 GB.", font=font(SANS, 14), fill="#cccccc")
    d.text((W - 400, H - 110), "Select to free up space.", font=font(SANS, 14), fill="#cccccc")
    img.save(OUT / "disk_full_toast.png")


def editor(name, code_rows, problem=None):
    img, d = canvas()
    title_bar(d, "utils.py - shop - Visual Studio Code")
    d.rectangle([0, 34, 50, H], fill="#1e1e1e")
    f = font(MONO, 18)
    for i, (text, fill) in enumerate(code_rows):
        d.text((8, 50 + i * 26), f"{i + 1:>2}", font=f, fill="#858585")
        d.text((64, 50 + i * 26), text, font=f, fill=fill)
    if problem:
        d.rectangle([0, H - 60, W, H], fill="#252526")
        d.text((14, H - 48), problem, font=font(SANS, 16), fill="#f48771")
    img.save(OUT / name)


def code_syntax_error():
    editor("code_syntax_error.png", [
        ("def total_price(items)", "#dcdcaa"),
        ("    total = 0", "#d4d4d4"),
        ("    for item in items:", "#d4d4d4"),
        ("        total += item.price * item.qty", "#d4d4d4"),
        ("    return total", "#d4d4d4"),
    ], problem="Problems: 1   SyntaxError: expected ':'  (utils.py, line 1)")


def clean_editor():
    editor("clean_editor.png", [
        ("def greet(name):", "#dcdcaa"),
        ('    return f"Hello, {name}!"', "#ce9178"),
        ("", "#d4d4d4"),
        ('print(greet("world"))', "#d4d4d4"),
    ])


def spreadsheet():
    img, d = canvas("#ffffff")
    title_bar(d, "expenses.xlsx - Excel", bg="#217346", fg="#ffffff")
    cols, x0, y0, cw, rh = ["Date", "Item", "Amount"], 60, 80, 300, 40
    rows = [("01 Sep", "Rent", "2,500"), ("05 Sep", "Groceries", "640"), ("12 Sep", "Internet", "300"),
            ("18 Sep", "Books", "420"), ("25 Sep", "Travel", "420")]  # sums to 4,280
    f, fb = font(SANS, 18), font(BOLD, 18)
    for c, name in enumerate(cols):
        d.rectangle([x0 + c * cw, y0, x0 + (c + 1) * cw, y0 + rh], fill="#e7e6e6", outline="#bfbfbf")
        d.text((x0 + c * cw + 10, y0 + 8), name, font=fb, fill="#000000")
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            d.rectangle([x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh], outline="#bfbfbf")
            d.text((x0 + c * cw + 10, y0 + r * rh + 8), value, font=f, fill="#000000")
    img.save(OUT / "spreadsheet.png")


def calendar():
    img, d = canvas("#ffffff")
    title_bar(d, "Calendar - Sunday, 27 September 2026", bg="#0f6cbd", fg="#ffffff")
    f, fb = font(SANS, 16), font(BOLD, 17)
    for i, hour in enumerate(range(9, 19)):
        y = 60 + i * 64
        d.line([80, y, W - 40, y], fill="#e0e0e0")
        d.text((20, y + 4), f"{hour:02d}:00", font=f, fill="#616161")
    d.rounded_rectangle([100, 60 + 1 * 64 + 4, 700, 60 + 2 * 64 - 4], 6, fill="#c7e0f4")
    d.text((112, 60 + 64 + 12), "10:00  Standup (done)", font=fb, fill="#0f6cbd")
    d.rounded_rectangle([100, 60 + 6 * 64 + 4, 700, 60 + 7 * 64 - 4], 6, fill="#fde7c7")
    d.text((112, 60 + 6 * 64 + 12), "15:00 - 16:00  Design review: FRED memory layer", font=fb, fill="#8a5300")
    d.line([80, 60 + 5 * 64 + 20, W - 40, 60 + 5 * 64 + 20], fill="#d13438", width=2)  # "now" 14:20
    img.save(OUT / "calendar.png")


def misleading_build():
    img, d = canvas()
    title_bar(d, "Build succeeded  -  CI #482  -  main")
    lines(d, [
        ("✔ Build succeeded in 2m 14s", "#89d185"),
        "Running test suite...",
        "  test_auth.py ............ passed",
        "  test_cart.py ....F...F... ",
        ("  test_payment.py ..F....... ", "#d4d4d4"),
        "",
        ("FAILED test_cart.py::test_discount_rounding", "#f48771"),
        ("FAILED test_cart.py::test_empty_cart_total", "#f48771"),
        ("FAILED test_payment.py::test_refund_currency", "#f48771"),
        ("===== 3 tests FAILED, 41 passed in 38.2s =====", "#f48771"),
        ("Pipeline status: FAILED", "#f48771"),
    ], y=60)
    img.save(OUT / "misleading_build.png")


def dense_log():
    rows = []
    for i in range(24):
        second = 2 * i
        line = f"2026-09-27 14:31:{second:02d} INFO  worker-{i % 4} handled request id={9100 + i} in {12 + i % 7}ms"
        rows.append((line, "#9da5b4"))
    rows[17] = ("2026-09-27 14:32:07 ERROR gateway  upstream timeout, returned 503 to client id=9117", "#9da5b4")
    img, d = canvas()
    title_bar(d, "server.log - tail -f")
    lines(d, rows, size=15, gap=26)
    img.save(OUT / "dense_log.png")


def bar_chart():
    img, d = canvas("#ffffff")
    title_bar(d, "Q1 Sales Report", bg="#f3f3f3", fg="#222222")
    data = [("January", 42), ("February", 35), ("March", 58), ("April", 47)]
    base, x = 640, 180
    for name, value in data:
        top = base - value * 9
        d.rectangle([x, top, x + 150, base], fill="#4472c4")
        d.text((x + 50, top - 30), str(value), font=font(BOLD, 20), fill="#222222")
        d.text((x + 20, base + 10), name, font=font(SANS, 20), fill="#222222")
        x += 250
    d.line([150, base, W - 60, base], fill="#222222", width=2)
    img.save(OUT / "bar_chart.png")


def clean_desktop():
    img, d = canvas("#2d5f8b")
    for y in range(H):
        d.line([0, y, W, y], fill=(40 + y // 20, 90 + y // 12, 140 + y // 10))
    d.rectangle([0, H - 48, W, H], fill="#202020")
    d.text((W - 110, H - 36), "14:20", font=font(SANS, 16), fill="#ffffff")
    img.save(OUT / "clean_desktop.png")


def app_frame(name, status, color, log_rows):
    img, d = canvas("#ffffff")
    title_bar(d, "WeatherDash - localhost:8080", bg="#e8eaed", fg="#202124")
    d.text((40, 60), "WeatherDash", font=font(BOLD, 34), fill="#1a73e8")
    d.text((40, 120), status, font=font(BOLD, 26), fill=color)
    d.rectangle([0, 380, W, H], fill="#1e1e1e")
    lines(d, log_rows, y=395, size=16, gap=24)
    img.save(OUT / name)


def temporal_frames():
    app_frame("t1_app_ok.png", "Status: OK - forecast loaded (Pune, 27°C)", "#188038",
              ["[14:00:00] GET /forecast 200 in 180ms", "[14:00:05] GET /forecast 200 in 176ms"])
    img, d = canvas()
    title_bar(d, "config.yaml - WeatherDash - Visual Studio Code")
    lines(d, [("api:", "#569cd6"), ("  base_url: https://api.weather.example", "#ce9178"),
              ("  timeout_ms: 50        # changed from 5000", "#ce9178"), ("  retries: 0", "#ce9178")], y=60)
    img.save(OUT / "t1_edit_config.png")
    app_frame("t1_crash.png", "Status: ERROR - service unavailable", "#d93025",
              [("[14:01:00] GET /forecast -> ReadTimeout: request exceeded timeout_ms=50", "#f48771"),
               ("[14:01:00] WeatherDash crashed: unhandled ReadTimeout", "#f48771")])
    img, d = canvas()
    title_bar(d, "config.yaml - WeatherDash - Visual Studio Code")
    lines(d, [("api:", "#569cd6"), ("  base_url: https://api.weather.example", "#ce9178"),
              ("  timeout_ms: 5000", "#ce9178"), ("  retries: 0", "#ce9178")], y=60)
    img.save(OUT / "t1_revert.png")
    for pct in (0, 50, 100):
        img, d = canvas("#ffffff")
        title_bar(d, "Downloads", bg="#f3f3f3", fg="#222222")
        d.text((80, 200), "dataset_v2.zip", font=font(BOLD, 26), fill="#222222")
        d.rounded_rectangle([80, 260, 1200, 300], 10, outline="#999999", width=2)
        if pct:
            d.rounded_rectangle([82, 262, 82 + int(1116 * pct / 100), 298], 10, fill="#34a853")
        d.text((80, 320), f"{pct}% - {'Done' if pct == 100 else 'Downloading...'}", font=font(SANS, 22), fill="#222222")
        img.save(OUT / f"t2_dl_{pct}.png")


def _to_16k(samples, sr):
    g = math.gcd(SR, sr)
    audio = resample_poly(samples, SR // g, sr // g).astype(np.float32)
    pad = np.zeros(int(0.3 * SR), dtype=np.float32)  # breathing room for VAD
    return np.concatenate([pad, audio, pad])


def _mix(speech, noise, snr_db, offset):
    seg = noise[offset:offset + len(speech)]
    p_s, p_n = np.mean(speech ** 2), np.mean(seg ** 2) + 1e-12
    scaled = seg * math.sqrt(p_s / (p_n * 10 ** (snr_db / 10)))
    return np.clip(speech + scaled, -1, 1).astype(np.float32)


def speech():
    sys.path.insert(0, str(CORE))
    from config.settings import KOKORO_MODEL_PATH, KOKORO_VOICES_PATH
    from kokoro_onnx import Kokoro

    t0 = time.perf_counter()
    kokoro = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
    noise = sf.read(NOISE, dtype="float32")[0]
    out = OUT / "speech"
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in (ROOT / "cases" / "routing_hard.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    manifest = []
    for i, row in enumerate(rows):
        for voice in VOICES:
            samples, sr = kokoro.create(row["text"], voice=voice, speed=1.0, lang="en-us")
            audio = _to_16k(samples, sr)
            name = f"{row['id']}_{voice}.wav"
            sf.write(out / name, audio, SR)
            manifest.append({"file": name, "text": row["text"], "case": row["id"], "voice": voice, "condition": f"clean/{voice}"})
            if voice == VOICES[0]:
                offset = (i * 7919 * 13) % (len(noise) - len(audio))
                noisy = f"{row['id']}_{voice}_office{NOISY_SNR_DB}db.wav"
                sf.write(out / noisy, _mix(audio, noise, NOISY_SNR_DB, offset), SR)
                manifest.append({"file": noisy, "text": row["text"], "case": row["id"], "voice": voice,
                                 "condition": f"office_noise_{NOISY_SNR_DB}db/{voice}"})
        print(f"  [{i + 1}/{len(rows)}] {row['id']}: {row['text'][:60]}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for name, text in (("oh_come_on.wav", "Oh come on..."), ("let_me_think.wav", "Hmm, let me think about this."),
                       ("it_crashed_again.wav", "It crashed again.")):
        samples, sr = kokoro.create(text, voice=VOICES[0], speed=1.0, lang="en-us")
        sf.write(OUT / name, _to_16k(samples, sr), SR)
    print(f"{len(manifest)} speech clips + 3 fusion clips in {time.perf_counter() - t0:.0f}s (Kokoro, CPU)")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for make in (cuda_oom, traceback_keyerror, browser_404, phishing_login, disk_full_toast,
                 code_syntax_error, clean_editor, spreadsheet, calendar, misleading_build,
                 dense_log, bar_chart, clean_desktop, temporal_frames):
        make()
    print(f"{len(list(OUT.glob('*.png')))} screenshots in {OUT}")
    if "--no-speech" not in sys.argv:
        speech()
