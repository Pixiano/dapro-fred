"""Live hold-to-talk test: hold left Ctrl + left Alt, talk, let go.

On release MiniCPM-o 2.6 (NF4) gets: what you said (raw audio, no STT), four
screens from the last few seconds (for "what just happened") and the current
screen (upstream slicing, up to 1.8 MP, for small text). The reply is printed
and spoken with Kokoro (FRED's voice, GPU). Pressing the chord again while
it talks cuts it off. Not duplex: it never speaks unless asked.

    venv\\Scripts\\python.exe live_minicpmo.py [--mic "Rockerz,Lavalier"] [--no-voice]

Logs: console, plus live_events.jsonl (one record per turn).
"""

import argparse
import collections
import ctypes
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"  # kokoro-onnx's own GPU check never fires

import numpy as np
import sounddevice as sd
import torch
from PIL import ImageGrab

from smoke_nf4 import FRED_PROMPT, ask, load_model, log

SR = 16000
KOKORO_DIR = Path(__file__).resolve().parents[3] / "Core" / "models" / "kokoro"  # FRED's copy
VK_LCONTROL, VK_LMENU = 0xA2, 0xA4
MIN_HOLD = 0.4  # s; shorter chord presses are UI shortcuts, not speech (candidate 1, 2026-09-28)
ASK = ("The images are his screen over the last few seconds, oldest first; the last one is now. "
       "Vatsal just said something to you out loud (the audio). Answer him.")


def chord():
    k = ctypes.windll.user32.GetAsyncKeyState
    return bool(k(VK_LCONTROL) & 0x8000 and k(VK_LMENU) & 0x8000)


def open_mic(names, callback):
    """First working device matching the comma-separated names; MME first (copied from candidate 1)."""
    devices, apis = sd.query_devices(), [a["name"] for a in sd.query_hostapis()]
    order = sorted(range(len(devices)), key=lambda i: apis[devices[i]["hostapi"]] != "MME")
    for name in names.split(","):
        for i in order:
            d = devices[i]
            if not (d["max_input_channels"] and name.strip().lower() in d["name"].lower()):
                continue
            try:
                s = sd.InputStream(device=i, samplerate=SR, channels=1, dtype="float32",
                                   blocksize=SR // 10, callback=callback)
                s.start()
                return s, d["name"]
            except Exception as e:
                log(f"mic {d['name']!r} unavailable ({e}); trying the next one")
    raise SystemExit(f"no working mic matching any of: {names}")


def speakable(text):
    """Answers may come in markdown; Kokoro would read the asterisks."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]+", "", text)
    return re.sub(r"^\s*(\d+\.|-)\s+", "", text, flags=re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", default="Rockerz,Lavalier", help="input devices in priority order")
    ap.add_argument("--window", type=int, default=8, help="seconds of screen history (4 screens sampled from it)")
    ap.add_argument("--history", type=int, default=2, help="previous turns kept (audio + reply) for follow-ups")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--system", default=FRED_PROMPT)
    args = ap.parse_args()

    # Kokoro first: load_model caps this process at the VRAM free when it runs.
    kokoro = None
    if not args.no_voice:
        from kokoro_onnx import Kokoro
        kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        kokoro.create("Ready.", voice="am_michael", speed=1.2, lang="en-us")  # warm-up
        log(f"Kokoro on {kokoro.sess.get_providers()[0]}")
    model, tokenizer = load_model()

    frames = collections.deque(maxlen=args.window)  # (time, PIL screen) at ~1 fps
    stop = threading.Event()

    def screen_loop():
        while not stop.is_set():
            t = time.time()
            try:
                frames.append((t, ImageGrab.grab()))
            except OSError:  # lock screen / UAC desktop
                pass
            stop.wait(max(0.0, 1.0 - (time.time() - t)))

    threading.Thread(target=screen_loop, daemon=True).start()

    mic_buf = collections.deque(maxlen=SR * 120 // (SR // 10))  # last 2 min of 100 ms blocks

    def on_mic(indata, *_):
        mic_buf.append((time.time(), indata[:, 0].copy()))

    mic, mic_name = open_mic(args.mic, on_mic)
    log(f"mic: {mic_name}")

    speech_q, cancel = queue.Queue(), threading.Event()

    def voice_loop():
        while True:
            text, t_ready = speech_q.get()
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if cancel.is_set() or not sent.strip():
                    continue
                samples, rate = kokoro.create(sent, voice="am_michael", speed=1.2, lang="en-us")  # FRED's voice
                if cancel.is_set():
                    break
                if t_ready:
                    ev("first_audio", after_reply_s=round(time.time() - t_ready, 2))
                    t_ready = None
                sd.play(samples, rate)
                sd.wait()

    if kokoro:
        threading.Thread(target=voice_loop, daemon=True).start()

    events = open("live_events.jsonl", "a", encoding="utf-8")
    run = datetime.now().strftime("%Y%m%d_%H%M%S")

    def ev(kind, **f):
        events.write(json.dumps({"run": run, "at": datetime.now().strftime("%H:%M:%S.%f")[:-3], "kind": kind, **f},
                                default=str) + "\n")
        events.flush()

    ev("run_start", mic=mic_name, window=args.window, history=args.history, voice=not args.no_voice,
       prompt=args.system)
    history = collections.deque(maxlen=args.history * 2)
    log("ready -- hold left Ctrl + left Alt and talk; Ctrl+C to quit")
    pressed_at, held = None, False
    try:
        while True:
            time.sleep(0.05)
            now_held = chord()
            if now_held and not held:
                pressed_at = time.time()
            if now_held and not held and kokoro:
                cancel.set()  # barge-in: stop talking the moment he presses
                sd.stop()
            held_prev, held = held, now_held
            if not (held_prev and not held) or time.time() - pressed_at < MIN_HOLD:
                continue

            t_rel = time.time()
            cancel.clear()
            blocks = [b for t, b in list(mic_buf) if t >= pressed_at - 0.3]
            audio = np.concatenate(blocks) if blocks else np.zeros(SR, np.float32)
            peak = float(np.abs(audio).max())
            if peak > 0.005:  # quiet mics (lavalier peaked at 0.1): normalise like FRED's AGC target
                audio = audio * min(0.7 / peak, 100.0)
            shots = [img for _, img in list(frames)]
            if not shots:
                log("no screen frame yet; skipped")
                continue
            screens = shots[::-2][:4][::-1]  # 4 screens, 2 s apart, ending with now
            content = screens + [audio, ASK]
            log(f"heard {len(audio) / SR:.1f} s (peak {peak:.3f}); thinking...")
            try:
                reply, s = ask(model, tokenizer, content, system=args.system, history=list(history),
                               max_new_tokens=200)
            except torch.OutOfMemoryError as e:  # the VRAM cap in load_model: fail, never spill
                log(f"OUT OF VRAM this turn ({e}); try a shorter --window")
                ev("oom", error=str(e)[:200])
                torch.cuda.empty_cache()
                continue
            t_reply = time.time()
            print(f"\nFRED> {reply}\n", flush=True)
            ev("turn", audio_s=round(len(audio) / SR, 2), mic_peak=round(peak, 4), screens=len(screens),
               model_s=round(s["s"], 2), peak_gb=round(s["peak_gb"], 2),
               release_to_reply_s=round(t_reply - t_rel, 2), reply=reply)
            history.extend([("user", [audio]), ("assistant", reply)])
            if kokoro:
                speech_q.put((speakable(reply), t_reply))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        mic.stop()
        ev("run_end")
        log("stopped")


if __name__ == "__main__":
    main()
