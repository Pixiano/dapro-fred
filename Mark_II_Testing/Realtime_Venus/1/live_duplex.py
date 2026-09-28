"""Live duplex test: your mic + one video source -> Venus (NF4), text + voice out.

Talk to it; it decides every second whether to listen or speak. Voice is
Kokoro (FRED's TTS, CPU) by default: Venus's own talker isn't realtime yet.
Ctrl+C to stop. Output also goes to live_duplex.log; what it heard to live_mic.wav.

    venv\\Scripts\\python.exe live_duplex.py --source screen   (or camera)
"""

import argparse
import ctypes
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pynvml as nvml
import sounddevice as sd
import soundfile as sf
import torch

from smoke_nf4 import load_duplex, log, profile_prefill
from speech_level import NoiseFloor

SR = 16000
KOKORO_DIR = Path(__file__).resolve().parents[3] / "Core" / "models" / "kokoro"  # FRED's copy
# Per-mic default gain. Lavalier measured 2026-09-28: speech peaked at -38 dB
# (0.1 FS) over a -50 dB room -> 8x (+18 dB). Headset mic not measured yet.
MIC_GAIN = {"lavalier": 8.0}

# "Answer me now": FRED's frozen hey_fred.onnx with FRED's own constants
# (Core/config/settings.py WAKEWORD_THRESHOLD, Core/input/wakeword.py AGC and
# cooldown), plus FRED's hold-to-talk chord (left Ctrl + left Alt,
# Core/input/hotkey.py). Not imported from FRED: its listener writes every
# score to FRED's real wake-word log / training captures.
WAKE_MODEL = Path(__file__).resolve().parents[3] / "Core" / "models" / "wakeword" / "hey_fred.onnx"
WAKE_THRESHOLD, WAKE_COOLDOWN = 0.35, 2.0
AGC_TARGET, AGC_MAX, AGC_MIN, AGC_SMOOTH = 0.7, 100.0, 0.01, 0.3
VK_LCONTROL, VK_LMENU = 0xA2, 0xA4


class Addressed:
    """Tracks whether Vatsal is explicitly talking to Venus (wake word or held chord)."""

    def __init__(self, wake=True):
        self.at = None  # monotonic time of the last wake / chord press
        self.held = False
        self._pressed_at = 0.0
        self.source = None
        self._oww, self._buf, self._gain, self._last_fire = None, np.zeros(0, np.float32), 1.0, 0.0
        if wake:
            from openwakeword.model import Model

            self._oww = Model(wakeword_models=[str(WAKE_MODEL)], inference_framework="onnx")

    def chord(self):
        k = ctypes.windll.user32.GetAsyncKeyState
        return bool(k(VK_LCONTROL) & 0x8000 and k(VK_LMENU) & 0x8000)

    MIN_HOLD = 0.4  # s. 2026-09-28: 8 chord "presses" were sub-second taps landing on
    # UI shortcuts with no speech; hold-to-talk is always longer than this.

    def feed(self, raw):
        """Called with each raw 100 ms mic block (pre-gain, like FRED's listener)."""
        held, now = self.chord(), time.monotonic()
        if held and not self.held:
            self._pressed_at = now
        if held and now - self._pressed_at >= self.MIN_HOLD:
            self.at, self.source = now, "hotkey"
        self.held = held
        if self._oww is None:
            return None
        self._buf = np.concatenate([self._buf, raw])
        score = None
        while len(self._buf) >= 1280:  # openwakeword's 80 ms frame
            block, self._buf = self._buf[:1280], self._buf[1280:]
            peak = float(np.abs(block).max())
            if peak > AGC_MIN:  # FRED's AGC: snap down instantly, ramp up slowly
                want = min(AGC_TARGET / peak, AGC_MAX)
                self._gain = want if want < self._gain else self._gain + (want - self._gain) * AGC_SMOOTH
            pcm = np.clip(np.clip(block * self._gain, -1, 1) * 32767, -32768, 32767).astype(np.int16)
            score = float(next(iter(self._oww.predict(pcm).values()), 0.0))
            now = time.monotonic()
            if score > WAKE_THRESHOLD and now - self._last_fire > WAKE_COOLDOWN:
                self._last_fire, self.at, self.source = now, now, "wake_word"
                self._oww.reset()
                return score
        return None

    def active(self, window=10.0):
        return self.at is not None and time.monotonic() - self.at < window

    def clear(self):
        self.at = self.source = None
# Placeholder persona for testing, not FRED's real prompt.
FRED_PROMPT = (
    "Streaming Omni Conversation. You are FRED, Vatsal's personal assistant. "
    "You can see his screen and hear him. Be brief, friendly and direct."
)
# Proactivity test trigger (Vatsal chose it 2026-09-28): replicable on demand by
# pressing play on any video. --alert "" disables.
ALERT = " Whenever a video starts playing on his screen, say so in one short sentence, even if he hasn't spoken."
# Dropped 2026-09-28: "speak only when he talks to you or something on screen
# truly needs his attention" -- Vatsal: no prompt magic for turn-taking; it
# answered only 10/24 times he spoke with it. Venus decides when to talk itself.


def frame_source(kind):
    if kind == "screen":
        from PIL import ImageGrab

        def grab():
            # Primary monitor at full res: the processor does its own resize, and
            # --scale / --slices need the real pixels to read small text.
            return ImageGrab.grab()

        return grab, lambda: None

    import cv2
    from PIL import Image

    cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    assert cam.isOpened(), "camera 0 not available"

    def grab():
        ok, bgr = cam.read()
        return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)) if ok else None

    return grab, cam.release


CROP_W, CROP_H = 640, 320  # ~= 448^2 px, the area the processor resizes to -> kept at native res
GRID = (9, 16)  # tile rows x cols for change detection


def tile_diffs(a, b):
    """Per-tile mean abs grey diff (0-255), GRID-shaped; a/b from grey_tiles()."""
    return np.abs(a - b).mean(axis=(1, 3))


def grey_tiles(img):
    g = np.asarray(img.convert("L"), dtype=np.int16)
    th, tw = g.shape[0] // GRID[0], g.shape[1] // GRID[1]
    return g[: th * GRID[0], : tw * GRID[1]].reshape(GRID[0], th, GRID[1], tw)


def focus_crop(img, diffs, thr):
    """Native-res crop around the tiles that changed, or None for no/huge change.

    A 16 px terminal font is ~5 px tall in the whole-screen frame (unreadable,
    readtest.py); in this crop it arrives full size. Costs one extra 64-token image.
    """
    rows, cols = np.nonzero(diffs > thr)
    if not len(rows):
        return None
    W, H = img.size
    th, tw = H // GRID[0], W // GRID[1]
    x0, x1, y0, y1 = cols.min() * tw, (cols.max() + 1) * tw, rows.min() * th, (rows.max() + 1) * th
    if (x1 - x0) * (y1 - y0) > 0.5 * W * H:  # window switch / big scroll: the overview covers it
        return None
    w, h = min(W, max(CROP_W, x1 - x0)), min(H, max(CROP_H, y1 - y0))
    left = min(max((x0 + x1) // 2 - w // 2, 0), W - w)
    top = min(max((y0 + y1) // 2 - h // 2, 0), H - h)
    crop = img.crop((left, top, left + w, top + h))
    crop.info["box"] = (left, top, left + w, top + h)  # for the event log
    crop.info["changed_tiles"] = int(len(rows))
    return crop


def open_mic(names, callback):
    """First working device matching the comma-separated names, in order (headset, then backup).

    Prefers the MME entries (Windows' normal devices). 2026-09-28: with the
    Rockerz disconnected, its raw Bluetooth hands-free WDM-KS endpoint was still
    listed, matched first, opened, then failed -> run died before unit 1. So a
    device only counts once its stream has actually started.
    """
    devices = sd.query_devices()
    apis = [a["name"] for a in sd.query_hostapis()]
    order = sorted(range(len(devices)), key=lambda i: apis[devices[i]["hostapi"]] != "MME")
    for name in names.split(","):
        for i in order:
            d = devices[i]
            if not (d["max_input_channels"] and name.strip().lower() in d["name"].lower()):
                continue
            try:
                stream = sd.InputStream(device=i, samplerate=SR, channels=1, dtype="float32",
                                        blocksize=SR // 10, callback=callback)
                stream.start()  # prove it streams; the main loop starts it again when ready
                stream.stop()
                return stream, d["name"]
            except Exception as e:  # headset charging / disconnected / driver endpoint that won't stream
                log(f"mic {d['name']!r} ({apis[d['hostapi']]}) unavailable ({e}); trying the next one")
    raise SystemExit(f"no working mic matching any of: {names}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["screen", "camera"], default="screen")
    ap.add_argument("--mic", default="Rockerz,Lavalier",
                    help="input device names in priority order (headset first, lavalier as backup)")
    ap.add_argument("--system", default=FRED_PROMPT, help="system prompt ('' = upstream's 'Streaming Omni Conversation.')")
    ap.add_argument("--voice", choices=["kokoro", "venus", "off"], default="kokoro",
                    help="kokoro = FRED's TTS on CPU (realtime); venus = native talker (~1.4 s per 1 s, +1.4 GB VRAM)")
    ap.add_argument("--frame-every", type=int, default=1, help="send a screen/camera frame every N seconds")
    # readtest.py 2026-09-28 (16 px Consolas, 1920x1080): exact reads 448/1 0/9,
    # 672/1 0/9, 896/1 0/9 (3.2 s prefill), 448/2 0/9, 448/4 2/9 -> no realtime
    # setting reads terminal text; keep the fast default.
    ap.add_argument("--slices", type=int, default=1,
                    help="HD tiles per frame (max_slice_nums): 1 = upstream (~600x340); 2 = 3x tokens, 4 = 4x")
    ap.add_argument("--scale", type=int, default=448,
                    help="image scale_resolution: 448 = trained; 672 = sharper single frame, same 64 tokens (untested quality)")
    ap.add_argument("--no-focus", dest="focus", action="store_false",
                    help="don't add the native-res crop of the changed region")
    ap.add_argument("--no-skip", action="store_true", help="send a frame every second even if the screen is unchanged")
    # Calibrated 2026-09-28 (skip_threshold/results.md, 88 pairs): new terminal
    # line 0.9853-0.9864, toast/dialog/window/scroll far lower; caret blink
    # 0.9970+, clock 0.9979+. 0.990 catches every must-change case except a few
    # typed characters (0.994-0.997, left to --keepalive); hover highlights
    # (0.9866-0.9872) may send a harmless extra frame.
    ap.add_argument("--similar", type=float, default=0.990,
                    help="send a frame when similarity to the last sent one drops below this "
                         "(1 - worst-tile mean abs diff/255 over a 16x9 grid, full-res grey; 1.0 = identical)")
    ap.add_argument("--keepalive", type=int, default=5, help="send a frame at least every N seconds even if unchanged")
    ap.add_argument("--window", type=int, default=5000, help="context tokens kept (~75/s; 5000 ~= last minute)")
    ap.add_argument("--listen-scale", type=float, default=1.0,
                    help="upstream listen_prob_scale: multiplies the <|listen|> logit each second "
                         "(<1 = readier to speak, incl. unprompted). 1.0 = upstream; pick from logged logits")
    ap.add_argument("--no-wake", action="store_true",
                    help="disable 'hey FRED' / Ctrl+Alt forcing a reply (the chord still works unless --no-hotkey)")
    ap.add_argument("--no-hotkey", action="store_true", help="disable the left Ctrl + left Alt force-reply chord")
    ap.add_argument("--kokoro-device", choices=["gpu", "cpu"], default="gpu",
                    help="Kokoro on the GPU (onnxruntime-gpu) or CPU (FRED's allowed exception)")
    ap.add_argument("--selftest-force-at", type=int, help="testing: act as if 'hey FRED' was heard at unit N")
    ap.add_argument("--barge", choices=["intent", "energy", "off"], default="intent",
                    help="what stops the voice mid-reply: intent = Ctrl+Alt (any tap), 'hey FRED', or Venus "
                         "itself yielding while you talk over it (default); "
                         "energy = any loud mic sound (TV / room noise cut both replies on 2026-09-28, "
                         "quiet rooms + headphones only); off")
    ap.add_argument("--barge-db", type=float,
                    help="fixed mic level (after gain) that counts as talking over the voice; "
                         "default = adaptive (room noise floor + 12 dB)")
    ap.add_argument("--profile", action="store_true", help="log the prefill split per second")
    ap.add_argument("--gain", type=float, help="mic gain multiplier, clipped at +-1 (default per mic: lavalier 8, else 1)")
    # Off by default: in run 22:09 the rule hijacked answers ("A video clip is playing
    # on his screen" to unrelated questions, 2 false alarms, repeats every ~30 s).
    ap.add_argument("--speak-threshold", type=float, default=0.5,
                    help="speak only if Venus's own P(speak) >= this at the decision (0 = upstream random sampling)")
    ap.add_argument("--alert-video", action="store_true", help="proactivity test: add the play-a-video alert rule")
    args = ap.parse_args()
    if args.system and args.alert_video:
        args.system += ALERT

    duplex = load_duplex(speech=args.voice == "venus", window=args.window)
    if args.system:
        duplex.prepare(prefix_system_prompt=args.system)
    duplex.processor.image_processor.scale_resolution = args.scale
    grab, close = frame_source(args.source)

    # Verbose diagnostics (Vatsal: "make the logging extremely verbose"):
    # live_events.jsonl gets one JSON record per event -- a full record per
    # 1 s unit, plus run start, voice (Kokoro) events, barge-ins, context trims.
    # Callbacks on the audio thread only push to evq; the main loop writes.
    events = open("live_events.jsonl", "a", encoding="utf-8")
    ev_lock, evq = threading.Lock(), queue.Queue()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def ev(kind, **fields):
        rec = {"run": run_id, "t": round(time.time(), 3), "at": datetime.now().strftime("%H:%M:%S.%f")[:-3],
               "kind": kind, **fields}
        with ev_lock:
            events.write(json.dumps(rec, default=str) + "\n")
            events.flush()

    # Confidence: the first decode() of each second picks <|listen|> vs speaking
    # from that second's logits. Record the model's raw P(listen) there (before
    # temperature/top-k), so the log shows near-misses, not just the outcome.
    decision, orig_decode = {}, duplex.decoder.decode

    def decode(logits, **kw):
        if "p_listen" not in decision and decision.get("force"):
            # Addressed ("hey FRED" / chord) and he has stopped talking: block
            # <|listen|> at this second's decision so Venus answers now. The
            # raw numbers below are still logged from the unmodified logits.
            forced = logits.clone()
            forced[0, duplex.listen_token_id] = float("-inf")
            decision["forced"] = True
            first_call = True
        else:
            first_call, forced = "p_listen" not in decision, None
        if first_call:
            # duplex.listen_token_id = "<|listen|>", what streaming_generate checks
            # (decoder.listen_id is the tokenizer's eos, a different token).
            lg = logits[0].float()
            decision["p_listen"] = torch.softmax(lg, -1)[duplex.listen_token_id].item()
            # Raw logits, so --listen-scale can be chosen from data: upstream's
            # decode() multiplies the <|listen|> LOGIT by listen_prob_scale.
            other = lg.clone()
            other[duplex.listen_token_id] = float("-inf")
            decision["listen_logit"] = lg[duplex.listen_token_id].item()
            decision["best_other"] = other.max().item()
            p, ids = torch.softmax(lg, -1).topk(5)
            toks = duplex.tokenizer.convert_ids_to_tokens(ids.tolist())
            decision["top5"] = [[t, round(v, 4)] for t, v in zip(toks, p.tolist())]
            # Upstream samples this decision (temperature 0.7), so a 15% "speak" fires
            # one second in seven: 22:16:55 "I'll help you check your system" in silence
            # (p 0.15), false video alarms at 0.26-0.44; the real alert was 0.65.
            if forced is None and args.speak_threshold and 1 - decision["p_listen"] < args.speak_threshold:
                decision["thresholded"] = True
                duplex.decoder.context += " "  # what upstream's own listen path in decode() does
                return torch.tensor([duplex.listen_token_id], device=logits.device)
        return orig_decode(logits=forced if forced is not None else logits, **kw)

    duplex.decoder.decode = decode
    prof = {}
    profile_prefill(duplex, prof)  # always: the event log records the prefill split every second
    nvml.nvmlInit()
    gpu = nvml.nvmlDeviceGetHandleByIndex(0)

    # Voice: 24 kHz chunks (Venus talker or Kokoro), played back-to-back on the Windows default output.
    play, left = queue.Queue(), [np.zeros(0, dtype=np.float32)]
    # Kokoro: Venus's text is gathered into sentences and synthesised on a worker
    # thread. `turn` bumps on every new reply so a stale reply's audio is dropped;
    # `muted` silences the rest of a reply you talked over; `first` = no audio queued yet.
    say, turn, pending, muted, first = queue.Queue(), [0], [""], [False], [True]

    def barge_in(db_, why):
        dropped = sum(len(a) for a in list(play.queue)) + len(left[0])
        turn[0] += 1
        muted[0] = True
        while not play.empty():
            play.get_nowait()
        left[0] = np.zeros(0, dtype=np.float32)
        evq.put(("barge_in", {"why": why, "mic_db": round(db_, 1), "dropped_audio_s": round(dropped / 24000, 2),
                              "turn": turn[0]}))
        print("  [you talked -> voice stopped]", end="", flush=True)

    # Barge-in, checked every 100 ms mic block: 2 loud blocks (200 ms) while
    # the voice is playing cut it. Needs headphones (speakers would trigger it:
    # 2026-09-28 it cut its own reply through the Realtek speakers), so `auto`
    # turns it off when the output device looks like speakers.
    loud, barge_on, on_speakers = [0], [False], [False]
    nf = NoiseFloor()  # the room's own level; "talking" / "stopped" are relative to it

    addressed = Addressed(wake=not args.no_wake)
    if args.no_hotkey:
        addressed.chord = lambda: False

    def on_mic(data, *_):
        x = data[:, 0].copy()
        mic.put(x)
        held_before = addressed.held
        wake_score = addressed.feed(x)
        playing = len(left[0]) or not play.empty()
        if wake_score is not None and playing and on_speakers[0]:
            # Probably Kokoro's own "Fred" through the speakers: don't address or interrupt.
            addressed.clear()
            evq.put(("wake_word_ignored", {"score": round(wake_score, 3), "why": "own voice on speakers"}))
            wake_score = None
        if wake_score is not None:
            evq.put(("wake_word", {"score": round(wake_score, 3)}))
        if addressed.held != held_before:
            evq.put(("hotkey", {"held": addressed.held}))
        if args.voice != "kokoro" or not barge_on[0] or not playing:
            loud[0] = 0
            return
        if args.barge == "intent":  # only a deliberate signal interrupts: chord press or "hey FRED"
            # (through speakers a wake word while playing was already dropped above)
            if (addressed.held and not held_before) or wake_score is not None:
                barge_in(-999.0 if wake_score is None else 0.0, why="hotkey" if wake_score is None else "wake_word")
            return
        db_ = 20 * np.log10(max(float(np.sqrt(((x * gain) ** 2).mean())), 1e-6))
        limit = args.barge_db if args.barge_db is not None else nf.floor + NoiseFloor.TALK_DB + 4
        loud[0] = loud[0] + 1 if db_ > limit else 0
        if loud[0] >= 2:
            loud[0] = 0
            barge_in(db_, why="energy")

    mic = queue.Queue()
    stream, mic_name = open_mic(args.mic, on_mic)
    gain = args.gain or next((g for k, g in MIC_GAIN.items() if k in mic_name.lower()), 1.0)
    kokoro_providers = None
    if args.voice == "kokoro":
        from kokoro_onnx import Kokoro

        # kokoro-onnx's own GPU check looks for a module named "onnxruntime-gpu",
        # which can't exist (hyphen), so it always picks CPU; ONNX_PROVIDER is
        # its documented override. Needs onnxruntime-gpu in this venv.
        if args.kokoro_device == "gpu":
            os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
            import onnxruntime as ort

            ort.set_default_logger_severity(3)  # hide harmless CUDA EP perf notices (memcpy nodes, ScatterND)
        kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        kokoro_providers = kokoro.sess.get_providers()
        t_w = time.perf_counter()
        kokoro.create("Ready.", voice="am_michael", speed=1.2, lang="en-us")  # warm-up: first GPU call took 1.09 s live
        log(f"Kokoro on {kokoro_providers[0]} (warm-up {time.perf_counter() - t_w:.2f}s)")

        def tts_worker():
            while True:
                t, text, queued_at = say.get()
                if t != turn[0]:
                    ev("voice_dropped_stale", turn=t, current_turn=turn[0], text=text)
                    continue
                t_s = time.perf_counter()
                samples, rate = kokoro.create(text, voice="am_michael", speed=1.2, lang="en-us")  # FRED's voice
                assert rate == 24000, rate
                synth = time.perf_counter() - t_s
                fresh = t == turn[0]
                if fresh:
                    play.put(samples.astype(np.float32))
                ev("voice_synth", turn=t, text=text, chars=len(text), synth_s=round(synth, 3),
                   audio_s=round(len(samples) / rate, 2), rtf=round(synth / max(len(samples) / rate, 1e-3), 3),
                   waited_in_queue_s=round(t_s - queued_at, 3), played=fresh, play_queue=play.qsize())

        threading.Thread(target=tts_worker, daemon=True).start()

    def speak_text(text, final=False):
        """Queue text for Kokoro. Called once per 1 s unit with that unit's words.

        Sentence ends always flush. Venus writes only ~4-5 words a second, so
        waiting for whole sentences left ~1 s before the first audio and ~2 s
        silent gaps mid-reply (self-test 2026-09-28). So also flush at the end
        of a unit once the first chunk has >= 3 words, or later chunks >= 6
        words, cutting at the last complete word. On final, flush the rest."""
        if muted[0]:
            return
        pending[0] += text
        p = pending[0]
        cut = len(p) if final else max((m.end() for m in re.finditer(r"[.!?](\s|$)", p)), default=0)
        if not cut:
            words = p.split()
            if len(words) >= (3 if first[0] else 6):
                cut = p.rstrip().rfind(" ") if not p.endswith(" ") else len(p)  # keep a half-written word back
        if cut and p[:cut].strip():
            say.put((turn[0], p[:cut].strip(), time.perf_counter()))
            ev("voice_queued", turn=turn[0], text=p[:cut].strip(), first_chunk=first[0], final=final)
            first[0] = False
        pending[0] = p[cut:]

    def out_cb(outdata, frames, *_):
        buf_ = left[0]
        was_silent = not len(buf_)
        while len(buf_) < frames and not play.empty():
            buf_ = np.concatenate([buf_, play.get_nowait()])
        n = min(frames, len(buf_))
        if was_silent and n:  # audio starts (or resumes) -- timestamp for the voice-latency log
            evq.put(("voice_audio_start", {"turn": turn[0], "at_perf": time.perf_counter()}))
        outdata[:n, 0] = buf_[:n]
        outdata[n:, 0] = 0
        left[0] = buf_[n:]

    speaker = None if args.voice == "off" else sd.OutputStream(samplerate=24000, channels=1, dtype="float32", callback=out_cb)
    out_name = sd.query_devices(kind="output")["name"] if speaker else ""
    # energy barge-in also needs headphones: through speakers it hears itself.
    on_speakers[0] = "speaker" in out_name.lower()
    barge_on[0] = args.voice == "kokoro" and (args.barge == "intent" or (args.barge == "energy" and not on_speakers[0]))
    logf = open("live_duplex.log", "a", encoding="utf-8")
    # Exactly what Venus got, per run. 16-bit: float32 was ~230 MB/hour (soak tests).
    heard = sf.SoundFile("live_mic.wav", "w", samplerate=SR, channels=1, subtype="PCM_16")
    logf.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} source={args.source} listen_scale={args.listen_scale} "
               f"system={args.system!r}\n")

    buf, unit, sent_frames = np.zeros(0, dtype=np.float32), 0, 0
    last_thumb, last_sent = [None], [-10**9]
    speaking, reply_text = False, ""
    reply_started = {}  # turn -> perf time Venus began the reply (for voice latency)
    ev("run_start", args=vars(args), mic=mic_name, mic_gain=gain,
       output=sd.query_devices(kind="output")["name"] if speaker else None,
       gpu=nvml.nvmlDeviceGetName(gpu), vram_total_gb=round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
       venus_reserved_gb=round(torch.cuda.memory_reserved() / 2**30, 2),
       window=duplex.decoder._window_config.basic_window_high_tokens,
       scale_resolution=duplex.processor.image_processor.scale_resolution,
       torch=torch.__version__, prompt=args.system, kokoro_providers=kokoro_providers,
       wake_word=not args.no_wake, hotkey=not args.no_hotkey, barge=barge_on[0])
    log(f"LIVE on {mic_name} (gain {gain:g}) + {args.source}. Talk to it. Ctrl+C to stop.")
    stream.start()
    if speaker:
        speaker.start()
        mode = "off" if not barge_on[0] else (
            "Ctrl+Alt only" if args.barge == "intent" and on_speakers[0] else
            "Ctrl+Alt or 'hey FRED'" if args.barge == "intent" else "any loud sound")
        log(f"voice ({args.voice}) out on {out_name}; interrupt: {mode}")
    try:
        while True:
            while len(buf) < SR:
                buf = np.concatenate([buf, mic.get()])
            audio, buf = buf[:SR], buf[SR:]
            audio = np.clip(audio * gain, -1, 1)
            heard.write(audio)
            db = 20 * np.log10(max(float(np.sqrt((audio ** 2).mean())), 1e-6))  # speech ~-30..-15, silence < -50
            lag = mic.qsize() / 10 + len(buf) / SR  # seconds of audio waiting

            t0 = time.perf_counter()
            unit += 1  # a frame is 64 of the ~75 tokens/s, so sparser frames = longer memory
            prof.clear()
            frame, sim, img = None, -1.0, None
            if unit % args.frame_every == 0:
                try:
                    img = grab()
                except OSError as e:  # locked screen / secure desktop: no frame this second, keep running
                    evq.put(("grab_failed", {"error": str(e)[:120]}))
            if img is not None:
                # #6: skip unchanged frames. Worst tile of a 16x9 grid: a whole-
                # screen average washes out one new terminal line (0.9997, same as
                # noise); per-tile doesn't. sha256 can only say identical-or-not.
                thumb = grey_tiles(img)
                diffs = (tile_diffs(thumb, last_thumb[0])
                         if last_thumb[0] is not None and last_thumb[0].shape == thumb.shape else None)
                sim = 1 - float(diffs.max()) / 255 if diffs is not None else 0.0
                if args.no_skip or sim < args.similar or unit - last_sent[0] >= args.keepalive:
                    frame, last_thumb[0], last_sent[0] = [img], thumb, unit
                    # Overview + a native-res crop of what just changed (if small).
                    crop = focus_crop(img, diffs, (1 - args.similar) * 255) if args.focus and diffs is not None else None
                    if crop is not None:
                        frame.append(crop)
            t_grab = time.perf_counter() - t0
            duplex.streaming_prefill(audio_waveform=audio, frame_list=frame,
                                     max_slice_nums=args.slices, batch_vision_feed=True)
            sent_frames += frame is not None
            pre = f" | grab {t_grab:.3f} " + " ".join(f"{k} {v:.3f}" for k, v in prof.items()) if args.profile else ""
            decision.clear()
            # Force a reply once he has addressed Venus and stopped talking
            # (chord released / a quiet second after "hey FRED").
            if unit == args.selftest_force_at:
                addressed.at, addressed.source = time.monotonic(), "selftest"
            addr_source = addressed.source
            floor = nf.floor
            # Chord released = he's done (hold-to-talk). After "hey FRED" wait for a quiet
            # second, but with a TV on it never comes (22:12-22:13), so give up after 2.5 s.
            done = (addressed.source == "hotkey" or nf.quiet(db)
                    or (addressed.at is not None and time.monotonic() - addressed.at >= 2.5))
            decision["force"] = addressed.active() and not addressed.held and done and not speaking
            nf.add(db)
            r = duplex.streaming_generate(listen_prob_scale=args.listen_scale)
            if not r["is_listen"] or not addressed.active():
                addressed.clear()
            p_speak = 1 - decision.get("p_listen", 1.0)  # everything that isn't <|listen|>
            dt = time.perf_counter() - t0

            slow = f"  [{dt:.2f}s, lag {lag:.1f}s]" if dt > 1.0 or lag > 1.0 else ""
            # Per-second GPU line. "Venus" = what torch holds; everything else on the
            # card goes in parentheses (Windows won't give per-process VRAM).
            venus = torch.cuda.memory_reserved() / 2**30
            other = nvml.nvmlDeviceGetMemoryInfo(gpu).used / 2**30 - venus
            seen = ("frame+crop" if len(frame) > 1 else "frame     ") if frame else "skip      "
            stat = (f"speak {p_speak:4.0%} | {seen} (sim {sim:.4f}, sent {sent_frames}/{unit}) | mic {db:4.0f} dB | GPU {nvml.nvmlDeviceGetUtilizationRates(gpu).gpu:3d}% | {dt:.2f}s/unit | VRAM Venus {venus:.1f} GB"
                    + (f" (+{other:.1f} GB else)" if other > 0.1 else "") + f" | lag {lag:.1f}s")
            ctypes.windll.kernel32.SetConsoleTitleW(stat)  # visible while it's speaking too
            # GPU-only rule: never page to system RAM. Near the limit, drop the oldest
            # context and shrink the window for good, then keep going.
            # Room Venus can still use = device free + torch's own cached-but-unused blocks.
            room = lambda: torch.cuda.mem_get_info()[0] + torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
            if room() < 2**29:
                d, before = duplex.decoder, duplex.decoder.get_cache_length()
                cfg = d._window_config
                cfg.basic_window_high_tokens = max(3000, int(before * 0.8))  # never under ~40 s of context
                cfg.basic_window_low_tokens = int(cfg.basic_window_high_tokens * 0.75)
                d.enforce_window()
                torch.cuda.empty_cache()
                ev("context_trim", unit=unit, tokens_before=before, tokens_after=d.get_cache_length(),
                   window_now=cfg.basic_window_high_tokens, room_gb=round(room() / 2**30, 2))
                print(f"\n[VRAM low: context {before} -> {d.get_cache_length()} tokens, "
                      f"window now {cfg.basic_window_high_tokens}]", flush=True)
                if room() < 2**28 and d.get_cache_length() >= before:
                    print(f"STOP: under 256 MB VRAM free and nothing left to drop ({stat}). "
                          "Something outside Venus is using the card.", flush=True)
                    break
            if r["is_listen"]:
                if speaking:
                    print(flush=True)
                    speaking = False
                    if args.voice == "kokoro":
                        # Venus stopped its reply. If he was talking at that moment it
                        # yielded to him (its trained interruption handling -- it keeps
                        # talking through TV/background speech), so cut the queued
                        # Kokoro audio. A normal ending (he's quiet) plays out in full.
                        # Only a mid-sentence stop is a yield: with the TV on "talking" is
                        # always true, and cutting natural endings clipped 3 replies (22:11-22:13).
                        yielded = not re.search(r"[.!?]\s*$", reply_text)
                        if args.barge != "off" and yielded and nf.talking(db) and (len(left[0]) or not play.empty()):
                            barge_in(db, why="venus_yielded")
                        else:
                            speak_text("", final=True)
                print(f"\r  listening  {stat}   ", end="", flush=True)
            else:
                if not speaking:
                    print(f"\n[{datetime.now():%H:%M:%S}] VENUS: ", end="", flush=True)
                    speaking = True
                    # New reply: drop anything still queued or playing from the last one.
                    turn[0] += 1
                    reply_started[turn[0]] = time.perf_counter()
                    pending[0], muted[0], first[0] = "", False, True
                    reply_text = ""
                    while not play.empty():
                        play.get_nowait()
                    left[0] = np.zeros(0, dtype=np.float32)
                reply_text += r["text"]
                print(r["text"] + slow, end="", flush=True)
                if args.voice == "kokoro":
                    speak_text(r["text"])
                elif speaker and r["audio_waveform"] is not None:
                    play.put(np.asarray(r["audio_waveform"], dtype=np.float32).reshape(-1))
            logits_s = (f" | logit listen {decision['listen_logit']:.2f} best_other {decision['best_other']:.2f}"
                        if "listen_logit" in decision else "")
            logf.write(f"{datetime.now():%H:%M:%S} {stat}{pre}{logits_s} | "
                       f"{'listen' if r['is_listen'] else 'speak ' + repr(r['text'])}\n")
            logf.flush()

            # Full per-unit record for diagnostics.
            crop_img = frame[1] if frame and len(frame) > 1 else None
            ev("unit", unit=unit, decision="listen" if r["is_listen"] else "speak", text=r.get("text", ""),
               end_of_turn=r.get("end_of_turn"), p_speak=round(p_speak, 4),
               listen_logit=decision.get("listen_logit"), best_other=decision.get("best_other"),
               top5=decision.get("top5"), listen_scale=args.listen_scale,
               forced=decision.get("forced", False), thresholded=decision.get("thresholded", False), addressed_by=addr_source if decision.get("force") else None,
               mic_db=round(db, 1), noise_floor_db=round(floor, 1), talking=db > floor + NoiseFloor.TALK_DB,
               mic_peak=round(float(np.abs(audio).max()), 3),
               mic_clipped_frac=round(float((np.abs(audio) >= 0.999).mean()), 4),
               frame="frame+crop" if crop_img else ("frame" if frame else "skip"), sim=round(sim, 5),
               crop_box=crop_img.info.get("box") if crop_img else None,
               crop_changed_tiles=crop_img.info.get("changed_tiles") if crop_img else None,
               since_last_frame_s=unit - last_sent[0], sent_frames=sent_frames,
               unit_s=round(dt, 3), grab_s=round(t_grab, 3), prefill_split={k: round(v, 4) for k, v in prof.items()},
               lag_s=round(lag, 2), context_tokens=duplex.decoder.get_cache_length(),
               vram_alloc_gb=round(torch.cuda.memory_allocated() / 2**30, 3), vram_reserved_gb=round(venus, 3),
               vram_other_gb=round(other, 3), vram_free_gb=round(torch.cuda.mem_get_info()[0] / 2**30, 3),
               gpu_util=nvml.nvmlDeviceGetUtilizationRates(gpu).gpu, turn=turn[0], voice_queue=say.qsize(),
               play_queue=play.qsize(), playing_s=round(len(left[0]) / 24000, 2), muted=muted[0])
            while not evq.empty():  # events from the audio thread
                kind, f = evq.get_nowait()
                if kind == "voice_audio_start" and f["turn"] in reply_started:
                    f["since_reply_start_s"] = round(f["at_perf"] - reply_started[f["turn"]], 3)
                f.pop("at_perf", None)
                ev(kind, **f)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        stream.stop()
        if speaker:
            speaker.stop()
        close()
        ev("run_end", units=unit, sent_frames=sent_frames, turns=turn[0])
        logf.close()
        events.close()
        heard.close()


if __name__ == "__main__":
    main()
