# Assembled FRED: VRAM budget, redone with the L0-L4 picks (2026-10-02)

Supersedes `Mark_II_Testing/ASSEMBLED_FRED.md` section 7. Card: RTX 5060 Ti,
16 GB. Target: peak <= 15 GB, flat over 8 h. [measured] = measured here,
[plan] = the plan's earlier estimate, [file] = from a model card's file size,
all other figures are estimates until the last-stage test.

## Co-resident (always loaded)

| Item | GB | Basis |
|---|---:|---|
| Windows / desktop | 1.0 | [plan] |
| Brain: Qwen3.5-4B Q6_K + image encoder, 16k ctx (Mercury and Qwen3.8-4B-Distill share this architecture) | 5.6 | 5.0 [measured] + 0.6 image encoder [plan]; ~5.0 if the image encoder is dropped (MiniCPM-V does the screen) |
| Talker + judge: MiniCPM5-2B Q4_K_M, 8k ctx | 2.1 | [plan]; ~1.1 at 1B (estimate) |
| Embedder: Qwen3-Embedding-0.6B Q8_0 | 1.4 | 1.4 GiB [measured, embedder report]; the plan's 0.7 was too low |
| Speech-to-text: faster-whisper large-v3-turbo fp16 | 1.6 | [plan] |
| Screen describer: MiniCPM-V 4.6 Q4_K_M + image encoder | 1.9 | [file] |
| Tone: emotion2vec+ base | 0.4 | [plan]; layer still unplaced |
| **Total with 2B talker** | **14.0** | 2.0 GB headroom on 16 |
| **Total with 1B talker** | **13.0** | 3.0 GB headroom |

## On demand / candidates (not in the total)

| Item | GB | Effect |
|---|---:|---|
| GLM-OCR 0.9B (complex OCR, loaded then unloaded) | ~1.5 | peak 15.5 with 2B talker (over target), 14.5 with 1B. Mitigation: unload the screen describer while it runs, or use the 1B talker |
| VoxCPM2 (TTS candidate) | ~8 | does not fit (22.0 total). Needs a quantised build under ~1.5 GB to be considered, else stays Kokoro on CPU |
| Embedder 4B instead of 0.6B | +2.0 | 16.0 with 2B talker: does not fit |
| Qwen3.8-4B-Distill, Q6_K file | 3.6 | smaller file than the baseline's 5.0 total; same shape |

## CPU / zero VRAM

Wake word, VAD, Smart Turn, voice ID, Nemotron diarization, Windows OCR (pending
approval), Kokoro TTS, the interaction clock, event log, dispatcher and tools.

## Rules for the last-stage test

- Measure with everything loaded for 8 h: VRAM must be flat.
- Other GPU users on this PC (a video editor, the :8000 server, the nightly Recognize job) were holding 13.9 GB of the card during the benchmark runs. The budget above is FRED alone.
- `LLM_IDLE_UNLOAD_SECONDS` interacts with an always-on loop; decide which models are exempt.
- Fallback if over: drop the screen describer and use the brain's image path (-1.9 GB, contention); use the 1B talker (-1.0 GB).
