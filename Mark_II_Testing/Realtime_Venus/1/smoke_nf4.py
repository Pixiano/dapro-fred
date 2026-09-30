"""Try 1 smoke test: official Realtime-Venus-Omni, NF4 on the LLM only, speech off.

Loads onto cuda:0 (no CPU offload -- fails loudly if it doesn't fit), checks
every weight is on the GPU, then streams the first N seconds of the bundled
sample video through the duplex loop and logs per-second latency.

    venv\\Scripts\\python.exe smoke_nf4.py [--seconds 70]
"""

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"  # never silently fetch assets
os.environ["MAX_NUM_FRAMES"] = "100000"

import torch
from transformers import AutoModel, BitsAndBytesConfig, set_seed

HERE = Path(__file__).resolve().parent
# Same architecture/API as base MiniCPM-o 4.5 (candidate 6 runs these scripts with
# DUPLEX_MODEL=..\MiniCPM-o-4.5\models\MiniCPM-o-4_5).
MODEL = Path(os.environ.get("DUPLEX_MODEL", HERE / "models" / "Realtime-Venus-Omni"))
# OpenBMB's own skip list for MiniCPM-o 4.5 AWQ: quantize the Qwen3 LLM only.
SKIP = ["vpm", "apm", "resampler", "audio_projection_layer", "audio_avg_pooler", "tts", "lm_head"]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def vram():
    free, total = torch.cuda.mem_get_info()
    return f"alloc {torch.cuda.memory_allocated() / 2**30:.2f} GB, reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB, device free {free / 2**30:.2f}/{total / 2**30:.2f} GB"


def timed(obj, attr, acc, key):
    """Wrap obj.attr so each call adds its GPU-synced wall time to acc[key]."""
    fn = getattr(obj, attr)

    def wrapper(*a, **k):
        torch.cuda.synchronize()
        t = time.perf_counter()
        out = fn(*a, **k)
        torch.cuda.synchronize()
        acc[key] = acc.get(key, 0.0) + time.perf_counter() - t
        return out

    setattr(obj, attr, wrapper)


def profile_prefill(duplex, acc):
    """Split each unit's prefill: image prep (CPU), vision (GPU), audio features (CPU), audio encoder, LLM feed."""
    timed(duplex.processor, "process_image", acc, "img_prep")
    timed(duplex.model, "get_vision_embedding", acc, "vision")
    timed(duplex.processor, "process_audio_streaming", acc, "audio_prep")
    timed(duplex.model, "get_audio_embedding_streaming", acc, "audio_enc")
    timed(duplex.decoder, "feed", acc, "llm_feed")


def load_duplex(speech=False, fp16_vocoder=True, steps=8, talker_sdpa=True, window=5000):
    """NF4 model on cuda:0 as a prepared duplex session; speech=True loads the voice (Token2wav)."""
    set_seed(42)
    free0 = torch.cuda.mem_get_info()[0]
    log(f"start: {vram()}")

    t = time.perf_counter()
    if not speech:
        # The ~180-line "weights ... were not used" list is the talker we skip on
        # purpose (init_tts=False); it buried real errors in the console.
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
    model = AutoModel.from_pretrained(
        str(MODEL),
        trust_remote_code=True,
        local_files_only=True,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16,
        device_map="cuda:0",
        # Without Venus's own voice the talker (20-layer Llama + embeddings) is
        # never called (streaming_generate returns before it), so don't build it.
        init_tts=speech,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            llm_int8_skip_modules=SKIP,
        ),
    ).eval()
    log(f"loaded in {time.perf_counter() - t:.0f} s: {vram()}")

    off_gpu = [n for n, p in model.named_parameters() if p.device.type != "cuda"]
    off_gpu += [n for n, b in model.named_buffers() if b.device.type != "cuda"]
    assert not off_gpu, f"not on GPU: {off_gpu[:10]}"
    n4 = sum(1 for m in model.modules() if m.__class__.__name__ == "Linear4bit")
    log(f"all weights on GPU; {n4} Linear4bit layers; VRAM grew {(free0 - torch.cuda.mem_get_info()[0]) / 2**30:.2f} GB")

    # as_duplex() always loads Token2wav (+3.2 GB measured) even with speech
    # off, leaving 0 GB free. It's never used when generate_audio=False.
    if not speech:
        model.init_tts = lambda *a, **k: None
    # Vocoder settings from the community 5060 Ti realtime run: FP16, 8 solver steps
    # (upstream default: FP32, 10). Only affects the frozen speech decoder, not Venus.
    # Upstream ships duplex with sliding_window_mode="off": ~75 tokens/s (64 vision
    # + ~10 audio) pile into the KV cache forever (~0.14 MB/token) until VRAM runs
    # out and Windows pages to RAM (17 s/unit seen live). "basic" trims back to
    # 75% of `window` tokens whenever it passes `window` (5000 ~= the last minute).
    duplex = model.as_duplex(
        generate_audio=speech, enable_float16=fp16_vocoder, n_timesteps=steps,
        sliding_window_mode="basic", basic_window_high_tokens=window,
        basic_window_low_tokens=int(window * 0.75),
    )
    if speech:
        if talker_sdpa:
            # init_tts_module forces the talker (20-layer Llama) to eager attention
            # unless flash-attn is installed; 4.51 reads this per forward.
            model.tts.model.config._attn_implementation = "sdpa"
        # Token2wav preallocates its flow attention cache for 16 solver steps
        # (1.95 GB fp32) but only indexes [step-1] for the steps it runs.
        t2w = duplex.model.tts.audio_tokenizer
        if fp16_vocoder:
            # Upstream bug: set_stream_cache runs the FP16 flow outside autocast
            # ("mat1 and mat2 must have the same dtype, Float and Half").
            orig = t2w.set_stream_cache

            def set_stream_cache(*a, **k):
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    return orig(*a, **k)

            t2w.set_stream_cache = set_stream_cache
        dec = t2w.flow.decoder
        dt = torch.float16 if fp16_vocoder else dec.att_cache_buffer.dtype
        dec.att_cache_buffer = dec.att_cache_buffer[:steps].to(dt).clone()
        dec.cnn_cache_buffer = dec.cnn_cache_buffer[:steps].to(dt).clone()
        torch.cuda.empty_cache()
    log(f"duplex ready (speech {'on' if speech else 'off, token2wav skipped'}): {vram()}")
    # GPU-only rule: near 0 free, Windows silently spills to system RAM.
    assert torch.cuda.mem_get_info()[0] > 2**30, "under 1 GB VRAM free -- would spill to system RAM"
    duplex.prepare()
    return duplex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=70)
    ap.add_argument("--speech", action="store_true")
    ap.add_argument("--fp32-vocoder", action="store_true")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--eager-talker", action="store_true")
    ap.add_argument("--slices", type=int, default=1, help="max_slice_nums (1 = upstream duplex default)")
    ap.add_argument("--batch-feed", action="store_true", help="upstream batch_vision_feed: one LLM pass per frame")
    ap.add_argument("--scale", type=int, default=448,
                    help="image processor scale_resolution (448 = trained; higher = sharper, same 64 tokens)")
    args = ap.parse_args()
    duplex = load_duplex(speech=args.speech, fp16_vocoder=not args.fp32_vocoder, steps=args.steps,
                         talker_sdpa=not args.eager_talker)
    duplex.processor.image_processor.scale_resolution = args.scale
    spoken = []

    # Per-unit time split: prefill parts, plus talker/vocoder when Venus speaks.
    split = {}
    profile_prefill(duplex, split)
    if args.speech:
        timed(duplex.model.tts, "generate_chunk", split, "talker")
        timed(duplex.model.tts.audio_tokenizer, "stream", split, "vocoder")
    pre_parts = ("img_prep", "vision", "audio_prep", "audio_enc", "llm_feed")
    pre_sum = {k: 0.0 for k in pre_parts}

    import cv2
    import minicpmo.utils as mu

    def duration(path):  # upstream shells out to ffprobe, which isn't installed
        cap = cv2.VideoCapture(str(path))
        d = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return d

    mu.get_video_duration = duration
    get_video_frame_audio_segments = mu.get_video_frame_audio_segments

    video = HERE.parents[1] / "shared" / "sample_1_real.mp4"  # moved out when Venus weights were deleted
    frames, audios, _ = get_video_frame_audio_segments(str(video), stack_frames=1, use_ffmpeg=True, adjust_audio_length=True)
    n = min(args.seconds, len(audios))
    questions = {10: "What do you see in the video so far?", 60: "Describe what just happened in one sentence."}
    log(f"streaming {n}/{len(audios)} s of {video.name} (frame {frames[0].size}, slices {args.slices}, "
        f"batch_feed {args.batch_feed}, scale {args.scale}); questions at {sorted(questions)}")

    times = []
    torch.cuda.reset_peak_memory_stats()
    for sec in range(1, n + 1):
        frame, audio = frames[sec - 1], audios[sec - 1]
        split.clear()
        t0 = time.perf_counter()
        duplex.streaming_prefill(
            audio_waveform=audio,
            frame_list=[frame] if frame is not None else None,
            text_list=[questions[sec]] if sec in questions else None,
            max_slice_nums=args.slices,
            batch_vision_feed=args.batch_feed,
        )
        t1 = time.perf_counter()
        pre = {k: split.get(k, 0.0) for k in pre_parts}  # snapshot: generate also calls feed
        for k in pre_parts:
            pre_sum[k] += pre[k]
        r = duplex.streaming_generate()
        t2 = time.perf_counter()
        times.append((t1 - t0, t2 - t1, r["is_listen"]))
        if not r["is_listen"] and r.get("audio_waveform") is not None:
            spoken.append(r["audio_waveform"])
        state = "listen" if r["is_listen"] else f"speak> {r['text']!r}"
        q = f"  [Q: {questions[sec]}]" if sec in questions else ""
        free = torch.cuda.mem_get_info()[0] / 2**30  # ~0 means Windows may be spilling to RAM
        parts = (f" (talker {split.get('talker', 0):.2f} vocoder {split.get('vocoder', 0):.2f})"
                 if args.speech and not r["is_listen"] else "")
        pre_s = " ".join(f"{k} {v:.2f}" for k, v in pre.items())
        log(f"[{sec:3d}] prefill {t1 - t0:.2f}s [{pre_s}] gen {t2 - t1:.2f}s{parts} total {t2 - t0:.2f}s free {free:.1f}GB  {state}{q}")

    listen = [a + b for a, b, l in times if l]
    speak = [a + b for a, b, l in times if not l]
    fmt = lambda xs: f"n={len(xs)} mean {sum(xs) / len(xs):.2f}s max {max(xs):.2f}s" if xs else "n=0"
    log(f"listening seconds: {fmt(listen)}")
    log(f"speaking seconds:  {fmt(speak)}")
    log(f"realtime (<1.0 s per 1 s unit): {sum(1 for x in listen + speak if x < 1.0)}/{n}")
    log("prefill mean per unit: " + ", ".join(f"{k} {v / n:.3f}s" for k, v in pre_sum.items()))
    log(f"peak alloc {torch.cuda.max_memory_allocated() / 2**30:.2f} GB; {vram()}")
    if spoken:
        import numpy as np
        import soundfile as sf

        wav = np.concatenate([np.asarray(w, dtype=np.float32).reshape(-1) for w in spoken])
        sf.write(HERE / "smoke_speech.wav", wav, 24000)
        log(f"wrote smoke_speech.wav ({len(wav) / 24000:.1f} s, peak {abs(wav).max():.2f})")


if __name__ == "__main__":
    main()
