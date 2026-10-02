# Duplex landscape, assembled duplex, and candidate 2 feasibility (2026-09-30)

Research agent report, requested by Vatsal ("search for more duplex models …
or architectures held together … the other 4 candidates [in Duplex-MPE] …
is candidate [2] plausible with workarounds, since hours of duplex data is
impossible"). Tags: [C] = read this session with source, [P] = prior
knowledge not re-checked, [I] = inference.

## A. Full-duplex / streaming models

| Model (org, date) | Base | Modalities | Duplex mechanism | License | Fits 16 GB? | Tools | Notes |
|---|---|---|---|---|---|---|---|
| Moshi (Kyutai, 2024-09) | 7B Helium + Mimi | audio | 2 audio streams 12.5 Hz + inner monologue | CC-BY-4.0 | yes (bf16 ~15 GB tight; q8) | none | 160–200 ms. MoshiVis adds static image input |
| FLM-Audio (BAAI, 2025-09) | 7B (Qwen2.5-VL LLM) | audio | native duplex | research only | community AWQ | none | 1M h + 200K synthetic dialogues |
| Voila-autonomous (Maitrix, 2025-05) | ~8B Llama-3 arch | audio | two-stream | MIT | 4/8-bit | none | 195 ms, "preview" |
| Freeze-Omni (Tencent, 2024-11) | frozen Qwen2-7B | audio | 3-class chunk state head beside frozen LLM | Apache-2.0 | int4/8 | LLM keeps text skills | ~0.75–1.2 s |
| MiniCPM-o 4.5 (OpenBMB, 2026) | 9B | audio + vision | listen/speak token | — | INT4 ~11 GB | weak | best of 5 on Duplex-MPE |
| NemotronLabs VoiceChat 11B (NVIDIA, 2026-08-03) | Nemotron-Nano-9B-v2 | audio, English | end-to-end duplex + tool head | OpenMDW-1.1 | yes, Q4 GGUF ~6.1 GB (llama.cpp fork) | native tool channel, FDB-v3 82.5% | ~450 ms, 550k h audio |
| PersonaPlex-7B (NVIDIA, 2026-01) | Moshi-based | audio | Moshi-style | gated | ~Moshi | none | persona/voice control |
| DuplexOmni (XJTU/Meituan, 2026-06) | Qwen3-Omni-30B-A3B | audio + video | 480 ms slices + async thinking layer | Apache-2.0 | no (≥8×H20) | via thinking layer | 0.5 s |
| Qwen2.5-Omni / Qwen3-Omni | — | audio + vision | streaming, half-duplex | Apache-2.0 | 3B/7B yes | — | Qwen3-Omni duplex hack needs 62 GB RAM |
| JoyAI-Talker / Joy-Duplex (JD, 2026-08) | 48.9B MoE + separate 1.7B predictor | audio | external predictor: partial/complete/backchannel/accept/reject | not stated | — | — | the pattern matters |
| RoboEgo / FLM-Ego (BAAI, 2025-06) | ? | audio + vision | native duplex, 80 ms | weights not found | ? | — | |
| Others (mostly half-duplex) | GLM-4-Voice, VITA-1.5, Baichuan-Omni-1.5, Kimi-Audio, Step-Audio-2-mini, LLaMA-Omni2, Mini-Omni2, SyncLLM, Hertz-dev, Ultravox, Sesame CSM | | | | | | |

Duplex-MPE (arXiv 2609.31948): MiniCPM-o 4.5 response on fresh onset 0.95,
answer accuracy 0.47, silence preservation 0.92; Freeze-Omni silence
0.0002; Moshi silence 0.27, accuracy ~1–2%; FLM-Audio 2/1901 correct;
Voila silence 0.84. "Full-Duplex Speech Models Take the Floor When Asked,
Not When Needed" (2609.19596): only 4–7% of hazard-relevant replies warn —
native duplex models react to being addressed, not to what's happening
(matches Venus live).

Top picks [I]: 1. MiniCPM-o 4.5 (only open duplex + vision + accurate);
2. NemotronLabs VoiceChat 11B Q4 (audio front with native tools/barge-in,
no vision, English); 3. Qwen2.5-Omni as a perception model inside an
assembled pipeline; 4. Moshi/MoshiVis only as a backchannel front.

## B. Assembled ("faked") duplex

| Component | Adds | Numbers | Notes |
|---|---|---|---|
| TEN VAD / Silero | frame VAD, barge-in trigger | ms | ONNX |
| Pipecat Smart Turn v3/v3.1 | audio end-of-turn classifier (Whisper-tiny + head, ~8M) | 12 ms CPU, 8 MB | open |
| LiveKit turn-detector | text end-of-turn (Qwen2.5-0.5B distilled) | 50–160 ms CPU | open |
| Easy Turn | acoustic+linguistic, complete/incomplete/backchannel/wait | 263 ms on 4090, 1,145 h | Apache-2.0 |
| Nemotron streaming ASR 0.6B | streaming partials | ~86–100 ms | Windows unverified |
| Kyutai STT-1B + Unmute | STT + semantic VAD, wraps any LLM | <1 s e2e | Linux/Rust-first |
| Endpoint anticipation (2606.13450) | predict end of turn up to 2.56 s early, speculative LLM+TTS | −505 ms for +28% compute | method |
| Talker-Reasoner / DuplexOmni thinking layer / Venus delegation | fast talker + slow async reasoner/tools | weaker thinker barely hurts duplex quality | pattern |
| Joy-Duplex | external 1.7B predictor gating an unchanged model | FDB v1.5 0.88 interruption response, 0.10 false triggers | pattern |
| Pipecat, LiveKit Agents, TEN, RealtimeSTT/TTS, speaches | plumbing, interruption, AEC hooks | — | mostly Windows-OK |

Recommended assembled FRED [I]:
1. Always-on perception: streaming ASR partials; screen frames through a
   change detector → small VLM captioner only on change.
2. Turn controller (CPU, ~20 ms/tick): VAD + Smart Turn v3 + small text
   scorer on partials → LISTEN / END-OF-TURN / BARGE-IN / BACKCHANNEL;
   barge-in stops Kokoro within ~100 ms; AEC (WebRTC) or mic gating on the
   TTS reference.
3. Speculative responder: Qwen3.5-4B starts on "likely end of turn" with
   prefix cache; discarded if he keeps talking.
4. Slow path: existing router + tools + confirmation gates async; spoken
   filler / "on hold" line; result spliced in.
5. Proactivity as its own slower loop: every 2–5 s or on change, the LLM
   sees a compact event log and decides "interrupt? y/n + reason" with a
   threshold and cooldown — where native models fail.

Latency [I]: ~0.5–0.7 s after end of speech; ~0.2–0.4 s with endpoint
anticipation. vs native duplex: faster, all 110 tools, tunable; loses true
overlap and natural backchannel timing (minor for a desktop assistant).

## C. Candidate 2 feasibility (MiniCPM5-2B + Venus-style duplex)

| System | Duplex / interaction data | Compute |
|---|---|---|
| Moshi | 7M h pretrain; 2,000 h Fisher; 170 h real multi-stream; >20k h synthetic | large |
| Freeze-Omni | frozen LLM; 110k h ASR; 3k h TTS; 60k synthetic Q&A for the state head | 8 GPUs |
| OmniFlatten (Qwen2-0.5B) | ~100k h align; 2,000 h simulated duplex from 390k text dialogues via CosyVoice | — |
| SALMONN-omni | 60k h + 10k h; synthetic turn-taking/barge-in | — |
| DuplexOmni | 3.02M synthetic conversations (Qwen3-TTS) | 128×H20 |
| Venus | 2.8M synthetic samples (~37% proactive duplex) | large |
| FLM-Audio | 1M h + 200K synthetic dialogues | — |
| Decoupled FSM (2609.03321) | Qwen3-4B; turn-taking from ~673k tokens Switchboard+Fisher; F1 0.65 vs 0.34 | 4×A6000, 300–700 iters |
| Smart Turn / LiveKit / Easy Turn | 8M head; ~1.5k-step distillation; 1,145 h | trivial–small |

Low-data evidence: nearly all duplex data in these papers is synthetic
(LLM script → TTS → timing simulation); converting half-duplex dialogues
works; frozen LLM + small state head works (Freeze-Omni); external predictor
driving an unchanged model works (Joy-Duplex); a few-million-param
end-of-turn head is enough for reactive turn-taking. Fisher/Switchboard are
paid LDC corpora; open duplex sets exist (2509.04093, DuplexChat 2607.04941).

Controller-only: viable for reactive turn-taking and barge-in; NOT for
content-driven proactivity (needs the LLM or a distilled judge in the loop).

On one 16 GB GPU [I]: turn head (≤0.5B) hours; frozen-LLM projector
stage-1 (Whisper ~1k h ASR, SigLIP2 ~0.5M captions) ~1–3 days each; LoRA
SFT 2B on 20–50k synthetic sessions ~days; full joint pretraining (Venus /
MiniCPM-o stage 2) not feasible.

**Verdict: plausible only with workarounds** — frozen/LoRA LLM (re-run
FRED's suites after each stage); speak/listen in a small external
controller trained on synthetic data (local LLM scripts, Kokoro + other
voices, simulated overlap/noise, a slice of real open duplex data);
proactivity + tools left to the FRED harness; teacher-labelling with
MiniCPM-o 4.5 / Venus + a big-LLM judge. A full Venus-style native retrain
of MiniCPM5-2B is not plausible on this hardware/data — and design B gets
most of the benefit with no training.

## Key links

Duplex-MPE 2609.31948 · DuplexOmni 2606.09186 · Freeze-Omni 2411.00774 ·
Moshi 2410.00037 · FLM-Audio 2509.02521 · Voila 2505.02707 · OmniFlatten
2410.17799 · SALMONN-omni 2411.18138 · Decoupled FSM 2609.03321 ·
JoyAI-Talker 2608.01119 · floor-taking 2609.19596 · endpoint anticipation
2606.13450 · Talker-Reasoner 2410.08328 ·
huggingface.co/nvidia/NVIDIA-NemotronLabs-VoiceChat-11B ·
huggingface.co/hoidhxd/NVIDIA-NemotronLabs-VoiceChat-11B-GGUF ·
huggingface.co/pipecat-ai/smart-turn-v3 · huggingface.co/livekit/turn-detector ·
github.com/ASLP-lab/Easy-Turn · github.com/kyutai-labs/unmute ·
github.com/kyutai-labs/moshivis · github.com/thecodacus/qwen3-omni-duplex
