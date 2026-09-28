"""Live duplex test: your mic + one video source -> Venus (NF4), text + voice out.

Talk to it; it decides every second whether to listen or speak. Voice is
Kokoro (FRED's TTS, CPU) by default: Venus's own talker isn't realtime yet.
Ctrl+C to stop. Output also goes to live_duplex.log; what it heard to live_mic.wav.

    venv\\Scripts\\python.exe live_duplex.py --source screen   (or camera)
"""

import argparse
import ctypes
import json
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

SR = 16000
KOKORO_DIR = Path(__file__).resolve().parents[3] / "Core" / "models" / "kokoro"  # FRED's copy
# Per-mic default gain. Lavalier measured 2026-09-28: speech peaked at -38 dB
# (0.1 FS) over a -50 dB room -> 8x (+18 dB). Headset mic not measured yet.
MIC_GAIN = {"lavalier": 8.0}
# Placeholder persona for testing, not FRED's real prompt.
FRED_PROMPT = (
    "Streaming Omni Conversation. You are FRED, Vatsal's personal assistant. "
    "You can see his screen and hear him. Be brief, friendly and direct."
)
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
    """First device matching the comma-separated names, in order (headset, then backup)."""
    devices = sd.query_devices()
    for name in names.split(","):
        for i, d in enumerate(devices):
            if d["max_input_channels"] and name.strip().lower() in d["name"].lower():
                try:
                    stream = sd.InputStream(device=i, samplerate=SR, channels=1, dtype="float32",
                                            blocksize=SR // 10, callback=callback)
                    return stream, d["name"]
                except Exception as e:  # e.g. headset charging / disconnected
                    log(f"mic {d['name']!r} unavailable ({e}); trying the next one")
                break
    raise SystemExit(f"no mic matching any of: {names}")


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
    ap.add_argument("--no-barge", action="store_true", help="don't cut the voice when you start talking")
    ap.add_argument("--barge-db", type=float, default=-28.0,
                    help="mic level (after gain) that counts as you talking over the voice")
    ap.add_argument("--profile", action="store_true", help="log the prefill split per second")
    ap.add_argument("--gain", type=float, help="mic gain multiplier, clipped at +-1 (default per mic: lavalier 8, else 1)")
    args = ap.parse_args()

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
        if "p_listen" not in decision:
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
        return orig_decode(logits=logits, **kw)

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

    def barge_in(db_):
        dropped = sum(len(a) for a in list(play.queue)) + len(left[0])
        turn[0] += 1
        muted[0] = True
        while not play.empty():
            play.get_nowait()
        left[0] = np.zeros(0, dtype=np.float32)
        evq.put(("barge_in", {"mic_db": round(db_, 1), "dropped_audio_s": round(dropped / 24000, 2), "turn": turn[0]}))
        print("  [you talked -> voice stopped]", end="", flush=True)

    # Barge-in, checked every 100 ms mic block: 2 loud blocks (200 ms) while
    # the voice is playing cut it. Needs headphones (speakers would trigger it).
    loud = [0]

    def on_mic(data, *_):
        x = data[:, 0].copy()
        mic.put(x)
        if args.voice != "kokoro" or args.no_barge or not (len(left[0]) or not play.empty()):
            loud[0] = 0
            return
        db_ = 20 * np.log10(max(float(np.sqrt(((x * gain) ** 2).mean())), 1e-6))
        loud[0] = loud[0] + 1 if db_ > args.barge_db else 0
        if loud[0] >= 2:
            loud[0] = 0
            barge_in(db_)

    mic = queue.Queue()
    stream, mic_name = open_mic(args.mic, on_mic)
    gain = args.gain or next((g for k, g in MIC_GAIN.items() if k in mic_name.lower()), 1.0)
    if args.voice == "kokoro":
        from kokoro_onnx import Kokoro

        kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))

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
        """Queue whole sentences for Kokoro; on final, flush whatever is left.
        The first chunk of a reply may also break at a comma (after 40 chars) so
        the voice starts sooner, the same trick as FRED's split_for_speech."""
        if muted[0]:
            return
        pending[0] += text
        p = pending[0]
        cut = len(p) if final else max((m.end() for m in re.finditer(r"[.!?](\s|$)", p)), default=0)
        if not cut and first[0] and len(p) >= 40:
            cut = max((m.end() for m in re.finditer(r",\s", p)), default=0)
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
    logf = open("live_duplex.log", "a", encoding="utf-8")
    heard = sf.SoundFile("live_mic.wav", "w", samplerate=SR, channels=1)  # exactly what Venus got, per run
    logf.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} source={args.source} listen_scale={args.listen_scale} "
               f"system={args.system!r}\n")

    buf, unit, sent_frames = np.zeros(0, dtype=np.float32), 0, 0
    last_thumb, last_sent = [None], [-10**9]
    speaking = False
    reply_started = {}  # turn -> perf time Venus began the reply (for voice latency)
    ev("run_start", args=vars(args), mic=mic_name, mic_gain=gain,
       output=sd.query_devices(kind="output")["name"] if speaker else None,
       gpu=nvml.nvmlDeviceGetName(gpu), vram_total_gb=round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
       venus_reserved_gb=round(torch.cuda.memory_reserved() / 2**30, 2),
       window=duplex.decoder._window_config.basic_window_high_tokens,
       scale_resolution=duplex.processor.image_processor.scale_resolution,
       torch=torch.__version__, prompt=args.system)
    log(f"LIVE on {mic_name} (gain {gain:g}) + {args.source}. Talk to it. Ctrl+C to stop.")
    stream.start()
    if speaker:
        speaker.start()
        log(f"voice ({args.voice}) out on {sd.query_devices(kind='output')['name']}")
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
            frame, sim = None, -1.0
            if unit % args.frame_every == 0:
                img = grab()
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
            r = duplex.streaming_generate(listen_prob_scale=args.listen_scale)
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
                    while not play.empty():
                        play.get_nowait()
                    left[0] = np.zeros(0, dtype=np.float32)
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
               mic_db=round(db, 1), mic_peak=round(float(np.abs(audio).max()), 3),
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
