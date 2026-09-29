"""Try 1 smoke test: official Qwen2.5-Omni-7B, NF4 on the Thinker's LLM only, no talker.

Loads onto cuda:0 (no CPU offload -- fails loudly if it doesn't fit), checks
every weight is on the GPU, then times four turns: text, one screenshot,
one audio clip, and 15 s of video with its audio.

    venv\\Scripts\\python.exe smoke_nf4.py
"""

import os
import time
from datetime import datetime
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"  # never silently fetch assets

import numpy as np
import torch
from transformers import BitsAndBytesConfig, Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor, set_seed

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "Qwen2.5-Omni-7B"
FIXTURES = HERE.parents[2] / "benchmarking" / "fixtures"
SAMPLE_VIDEO = HERE.parents[1] / "Realtime_Venus" / "1" / "models" / "Realtime-Venus-Omni" / "assets" / "sample_1_real.mp4"
# Quantize the Qwen2.5-7B LLM linears only; vision/audio encoders and the
# output head stay BF16 (same split as candidate 1). Talker/token2wav are
# never built (enable_audio_output=False): Kokoro is FRED's voice.
SKIP = ["visual", "audio_tower", "lm_head", "talker", "token2wav"]
# Same placeholder as candidate 1's live test, minus Venus's mode line.
# Frames given as a PIL list bypass qwen-omni-utils' video pixel cap and go in
# at full size: 30 frames of 1280x720 made the vision encoder's dense SDPA mask
# ask for 111 GB. 448x252 per frame ~= candidate 1's 448 px -> 144 tokens per
# 2 frames. Single screenshots stay native (FRED's vision sees them whole).
VIDEO_FRAME_PIXELS = 448 * 252
FRED_PROMPT = "You are FRED, Vatsal's personal assistant. You can see his screen and hear him. Be brief, friendly and direct."


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def vram():
    free, total = torch.cuda.mem_get_info()
    return f"alloc {torch.cuda.memory_allocated() / 2**30:.2f} GB, reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB, device free {free / 2**30:.2f}/{total / 2**30:.2f} GB"


def _segmented_encoder_attention():
    """Encoder attention per segment instead of one dense block mask.

    transformers 4.52's SDPA path for the vision and audio encoders builds a
    [seq, seq] bool mask with True only inside each cu_seqlens segment (one
    frame window / audio chunk) and runs attention over the whole thing: a
    1280x720 screenshot (4.8k patches) reserved ~7 GB, 15 s of 448 px video
    (8.6k patches) spilled past 16 GB into system RAM. Running SDPA on each
    segment separately is the same maths (flash-attn varlen does this) at
    per-segment memory. Needed on Windows: no flash-attn wheel.
    """
    import torch.nn.functional as F
    from transformers.models.qwen2_5_omni import modeling_qwen2_5_omni as m

    def per_segment(q, k, v, cu_seqlens):  # q/k/v: [heads, seq, dim]
        cu = cu_seqlens.tolist()
        # 4-D (batch of 1): with 3-D inputs SDPA silently takes its math kernel,
        # which materialises the fp32 score matrix (6.4 GB for a 1080p screen).
        return torch.cat([F.scaled_dot_product_attention(q[None, :, a:b], k[None, :, a:b], v[None, :, a:b])[0]
                          for a, b in zip(cu[:-1], cu[1:]) if b > a], dim=1)

    def vision_forward(self, hidden_states, cu_seqlens, rotary_pos_emb=None):
        n = hidden_states.shape[0]
        q = self.q(hidden_states).reshape(n, self.num_heads, -1)
        k = self.k(hidden_states).reshape(n, self.num_heads, -1)
        v = self.v(hidden_states).reshape(n, self.num_heads, -1)
        q = m.apply_rotary_pos_emb_vision(q.unsqueeze(0), rotary_pos_emb).squeeze(0)
        k = m.apply_rotary_pos_emb_vision(k.unsqueeze(0), rotary_pos_emb).squeeze(0)
        out = per_segment(q.transpose(0, 1), k.transpose(0, 1), v.transpose(0, 1), cu_seqlens)
        return self.proj(out.transpose(0, 1).reshape(n, -1))

    def audio_forward(self, hidden_states, cu_seqlens=None):
        n = hidden_states.shape[0]
        q = self.q_proj(hidden_states).reshape(n, self.num_heads, -1)
        k = self.k_proj(hidden_states).reshape(n, self.num_heads, -1)
        v = self.v_proj(hidden_states).reshape(n, self.num_heads, -1)
        out = per_segment(q.transpose(0, 1), k.transpose(0, 1), v.transpose(0, 1), cu_seqlens)
        return self.out_proj(out.transpose(0, 1).reshape(n, self.embed_dim))

    m.Qwen2_5OmniVisionSdpaAttention.forward = vision_forward
    m.Qwen2_5OmniAudioSdpaAttention.forward = audio_forward


def load_model():
    """NF4 Thinker on cuda:0 + processor. Asserts all-on-GPU and >1 GB left free."""
    set_seed(42)
    _segmented_encoder_attention()
    free0, total = torch.cuda.mem_get_info()
    # GPU-only rule, enforced: cap this process at what's free now minus 0.5 GB,
    # so running out raises OutOfMemoryError instead of Windows silently
    # spilling VRAM into system RAM (what the first runs did).
    torch.cuda.set_per_process_memory_fraction((free0 - 2**29) / total)
    log(f"start: {vram()}")
    t = time.perf_counter()
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        str(MODEL),
        local_files_only=True,
        enable_audio_output=False,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16,
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
    n4 = sum(1 for m in model.modules() if m.__class__.__name__ == "Linear4bit")
    log(f"all weights on GPU; {n4} Linear4bit layers; talker {'ON' if model.has_talker else 'off'}; "
        f"VRAM grew {(free0 - torch.cuda.mem_get_info()[0]) / 2**30:.2f} GB")
    # GPU-only rule: near 0 free, Windows silently spills to system RAM.
    assert torch.cuda.mem_get_info()[0] > 2**30, "under 1 GB VRAM free -- would spill to system RAM"
    processor = Qwen2_5OmniProcessor.from_pretrained(str(MODEL), local_files_only=True)
    return model, processor


def ask(model, processor, content, system=FRED_PROMPT, history=(), max_new_tokens=160, fps=2.0, audio_track=None):
    """One turn. content: the user message's content list (Qwen chat format).
    fps: frame rate of a video given as a frame list (TMRoPE timing).
    audio_track: 16 kHz audio spanning the (single) video, interleaved with its
    frames by time (use_audio_in_video) instead of a separate clip.
    Returns (reply, stats). Greedy decoding so benchmark runs repeat."""
    from qwen_omni_utils import process_mm_info
    conv = [{"role": "system", "content": [{"type": "text", "text": system}]}]
    conv += [{"role": r, "content": c} for r, c in history]
    conv.append({"role": "user", "content": content})
    t0 = time.perf_counter()
    text = processor.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
    audios, images, videos = process_mm_info(conv, use_audio_in_video=False)
    audio_in_video = audio_track is not None
    if audio_in_video:
        audios = [audio_track]
    inputs = processor(text=text, audio=audios, images=images, videos=videos, return_tensors="pt",
                       padding=True, use_audio_in_video=audio_in_video, fps=fps).to(model.device).to(model.dtype)
    torch.cuda.synchronize()
    t1 = time.perf_counter()
    shapes = {k: tuple(v.shape) for k, v in inputs.items() if hasattr(v, "shape") and k != "attention_mask"}
    log(f"  generate: {shapes}, free {torch.cuda.mem_get_info()[0] / 2**30:.1f} GB")
    out = model.generate(**inputs, use_audio_in_video=audio_in_video, return_audio=False,
                         max_new_tokens=max_new_tokens, do_sample=False)
    torch.cuda.synchronize()
    t2 = time.perf_counter()
    new = out[:, inputs["input_ids"].shape[1]:]
    peak = torch.cuda.max_memory_allocated() / 2**30
    torch.cuda.reset_peak_memory_stats()
    reply = processor.batch_decode(new, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0].strip()
    stats = {"prep_s": t1 - t0, "gen_s": t2 - t1, "in_tokens": inputs["input_ids"].shape[1],
             "out_tokens": new.shape[1], "peak_gb": peak}
    return reply, stats


def sample_clip(seconds=15, fps=2):
    """First `seconds` of candidate 1's sample video: PIL frames at `fps` + 16 kHz mono audio."""
    import av
    import librosa
    frames, audio, sr = [], [], None
    with av.open(str(SAMPLE_VIDEO)) as c:
        step, nxt = 1 / fps, 0.0
        for f in c.decode(video=0):
            if f.time is not None and f.time >= seconds:
                break
            if f.time is not None and f.time >= nxt:
                frames.append(f.to_image())
                nxt += step
    with av.open(str(SAMPLE_VIDEO)) as c:
        for f in c.decode(audio=0):
            if f.time is not None and f.time >= seconds:
                break
            a = f.to_ndarray()
            audio.append(a.mean(0) if a.ndim > 1 else a)
            sr = f.sample_rate
    wav = librosa.resample(np.concatenate(audio).astype(np.float32), orig_sr=sr, target_sr=16000)
    return frames, wav


def main():
    model, processor = load_model()
    from PIL import Image
    import soundfile as sf
    frames, clip_audio = sample_clip()
    wav, sr = sf.read(FIXTURES / "it_crashed_again.wav", dtype="float32")
    assert sr == 16000
    tests = [
        ("text", [{"type": "text", "text": "In one sentence: why is the sky blue?"}]),
        ("screenshot", [{"type": "image", "image": Image.open(FIXTURES / "cuda_oom.png").convert("RGB")},
                        {"type": "text", "text": "What's the problem on my screen?"}]),
        ("audio clip", [{"type": "audio", "audio": wav},
                        {"type": "text", "text": "What did I just say, and how do I sound?"}]),
        (f"video 15 s ({len(frames)} frames) + audio", [
            {"type": "video", "video": frames, "max_pixels": VIDEO_FRAME_PIXELS}, {"type": "audio", "audio": clip_audio},
            {"type": "text", "text": "What is happening in this video?"}]),
    ]
    torch.cuda.reset_peak_memory_stats()
    for warm in (True, False):  # first pass warms kernels; the second is the timing that counts
        for name, content in tests:
            log(f"{'warm-up' if warm else 'timed'}: {name}")
            reply, s = ask(model, processor, content)
            if not warm:
                log(f"{name}: prep {s['prep_s']:.2f}s gen {s['gen_s']:.2f}s ({s['in_tokens']} in / "
                    f"{s['out_tokens']} out, {s['out_tokens'] / s['gen_s']:.1f} tok/s) peak {s['peak_gb']:.2f}GB\n"
                    f"    -> {reply!r}")
        if warm:
            log("warm-up pass done")
    log(f"peak alloc {torch.cuda.max_memory_allocated() / 2**30:.2f} GB; {vram()}")


if __name__ == "__main__":
    main()
