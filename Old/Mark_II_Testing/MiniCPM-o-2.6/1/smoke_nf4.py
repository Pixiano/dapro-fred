"""Try 1 smoke test: official MiniCPM-o 2.6, NF4 on the Qwen2.5-7B LLM only, no TTS.

Loads onto cuda:0 (no CPU offload), checks every weight is on the GPU, caps
this process's VRAM so overflow raises instead of spilling to RAM, then times
four turns: text, one screenshot, one audio clip, 15 s of video+audio as
1 s omni units.

    venv\\Scripts\\python.exe smoke_nf4.py
"""

import os
import time
from datetime import datetime
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer, BitsAndBytesConfig, set_seed

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "MiniCPM-o-2_6"
FIXTURES = HERE.parents[2] / "benchmarking" / "fixtures"
SAMPLE_VIDEO = HERE.parents[1] / "shared" / "sample_1_real.mp4"  # Venus's sample clip, kept when its weights were deleted
# LLM linears only, as candidates 1 and 3 (OpenBMB's own AWQ skip list).
SKIP = ["vpm", "apm", "resampler", "audio_projection_layer", "audio_avg_pooler", "tts", "lm_head"]
FRED_PROMPT = "You are FRED, Vatsal's personal assistant. You can see his screen and hear him. Be brief, friendly and direct."


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def vram():
    free, total = torch.cuda.mem_get_info()
    return f"alloc {torch.cuda.memory_allocated() / 2**30:.2f} GB, reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB, device free {free / 2**30:.2f}/{total / 2**30:.2f} GB"


def load_model():
    """NF4 LLM on cuda:0 + tokenizer. Asserts all-on-GPU and caps VRAM (fail, never spill)."""
    set_seed(42)
    free0, total = torch.cuda.mem_get_info()
    torch.cuda.set_per_process_memory_fraction((free0 - 2**29) / total)
    log(f"start: {vram()}")
    t = time.perf_counter()
    model = AutoModel.from_pretrained(
        str(MODEL),
        trust_remote_code=True,
        local_files_only=True,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16,
        init_vision=True,
        init_audio=True,
        init_tts=False,  # FRED speaks with Kokoro
        device_map="cuda:0",
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
    # transformers 4.44's Qwen2 has no logits_to_keep: prefill computes fp32 logits
    # for every prompt position (121 s of omni units ~= 11k tokens x 152k vocab =
    # 6.5 GB -> OOM). Generation only reads the last position; no training here.
    head_fwd = model.llm.lm_head.forward
    model.llm.lm_head.forward = lambda h: head_fwd(h[:, -1:, :])
    n4 = sum(1 for m in model.modules() if m.__class__.__name__ == "Linear4bit")
    log(f"all weights on GPU; {n4} Linear4bit layers; VRAM grew {(free0 - torch.cuda.mem_get_info()[0]) / 2**30:.2f} GB")
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL), trust_remote_code=True, local_files_only=True)
    return model, tokenizer


def ask(model, tokenizer, content, system=FRED_PROMPT, history=(), max_new_tokens=160, omni=False):
    """One turn via upstream chat(). content: list of str / PIL image / 16 kHz np audio
    (omni=True: "<unit>", frame, 1 s audio, ... as upstream's video example).
    Greedy (do_sample=False) so benchmark runs repeat. Returns (reply, stats)."""
    msgs = [{"role": "system", "content": system}] + [{"role": r, "content": c} for r, c in history]
    msgs.append({"role": "user", "content": content})
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    reply = model.chat(msgs=msgs, tokenizer=tokenizer, sampling=True, do_sample=False, max_new_tokens=max_new_tokens,
                       omni_input=omni, use_image_id=False if omni else None, max_slice_nums=1 if omni else None)
    torch.cuda.synchronize()
    return reply.strip(), {"s": time.perf_counter() - t0, "peak_gb": torch.cuda.max_memory_allocated() / 2**30}


def sample_units(seconds=15):
    """First `seconds` of candidate 1's sample video as upstream's omni units: "<unit>", frame, 1 s audio."""
    import av
    import librosa
    frames, audio, sr = {}, [], None
    with av.open(str(SAMPLE_VIDEO)) as c:
        for f in c.decode(video=0):
            if f.time is None or f.time >= seconds:
                break
            frames.setdefault(int(f.time), f.to_image())
    with av.open(str(SAMPLE_VIDEO)) as c:
        for f in c.decode(audio=0):
            if f.time is not None and f.time >= seconds:
                break
            a = f.to_ndarray()
            audio.append(a.mean(0) if a.ndim > 1 else a)
            sr = f.sample_rate
    wav = librosa.resample(np.concatenate(audio).astype(np.float32), orig_sr=sr, target_sr=16000)
    units = []
    for s in range(seconds):
        units += ["<unit>", frames[s], wav[s * 16000:(s + 1) * 16000]]
    return units


def main():
    model, tokenizer = load_model()
    from PIL import Image
    import soundfile as sf
    wav, sr = sf.read(FIXTURES / "it_crashed_again.wav", dtype="float32")
    assert sr == 16000
    tests = [
        ("text", ["In one sentence: why is the sky blue?"], False),
        ("screenshot", [Image.open(FIXTURES / "cuda_oom.png").convert("RGB"), "What's the problem on my screen?"], False),
        ("audio clip", [wav, "What did I just say, and how do I sound?"], False),
        ("video 15 s omni units", sample_units() + ["What is happening in this video?"], True),
    ]
    for warm in (True, False):
        for name, content, omni in tests:
            log(f"{'warm-up' if warm else 'timed'}: {name}")
            reply, s = ask(model, tokenizer, content, omni=omni)
            if not warm:
                log(f"{name}: {s['s']:.2f}s peak {s['peak_gb']:.2f}GB\n    -> {reply!r}")
    log(f"done; {vram()}")


if __name__ == "__main__":
    main()
