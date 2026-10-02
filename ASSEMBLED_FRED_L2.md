# Assembled FRED: L2 Talker and voice out (draft, 2026-10-02)

Layer 2 speaks fast while the thinker (L3) works: acks, cover lines, short
replies, then the voice. Parent plan: `Mark_II_Testing/ASSEMBLED_FRED.md`.
English is the working language. Status: **draft, TTS pick open.**

## Picks so far

| # | Component | Pick | Runs on | VRAM | Status |
|---|---|---|---|---:|---|
| 1 | Phrase cache | `fillers.py` + tool captions, pre-synthesised | CPU | 0 | existing |
| 2 | Talker model | **Test both MiniCPM5-1B and MiniCPM5-2B** (Q4_K_M, own llama-server) in the whole setup, last stages; measure total VRAM and latency; decide then | GPU, own process | ~1.1 / ~2.1 GB | decided by Vatsal 2026-10-02 |
| 3 | TTS | Kokoro (existing) | CPU (exception) | 0 | current pick; VoxCPM2 under review, see below |
| 4 | Barge-in cancel | `speak(cancel=)` called by L1 | CPU | 0 | to measure (<200 ms target) |

## Decisions (Vatsal, 2026-10-02)

- **Talker:** test 1B and 2B of MiniCPM5 on VRAM in the full assembled setup and on latency. Llama-3.2-1B-Instruct is not in the test (older, gated, custom licence).
- **Speech-to-text:** Whisper (faster-whisper large-v3-turbo) is the only pick. **Confucius4-R2T2 is on hold.**
- **Prompt Guard 22M injection filter: on hold.** The 86M original is not wanted.

## Marked to test (Vatsal, 2026-10-02)

- **VoxCPM2:** test real VRAM and the available quants on this GPU, plus first-audio time. Not a pick yet; Kokoro stays the current TTS.
- **Prompt-injection filter (on hold, candidate noted):** `qualifire/prompt-injection-jailbreak-sentinel-v2` (GGUF repo `qualifire/...-GGUF`; base `rogue-security/...-v2`). A Qwen3-0.6B fine-tune (~0.6B parameters; Q4_K_M 397 MB, Q8_0 639 MB), gated, licence "other". Its "best" claim is unverified: the card is gated and I could not read it. Compare with Prompt Guard 2 22M on FRED's injection suite when the hold lifts.

## TTS: VoxCPM2 (open)

- 2B parameters, 30 languages, 48 kHz, voice design and cloning, Apache-2.0, streaming.
- Card says **~8 GB VRAM** and RTF ~0.30 on an RTX 4090 (~0.13 with Nano-vLLM). Kokoro is 0 GB on CPU. The assembled budget is 13.3 GB with everything else, ceiling 15 GB, so 8 GB does not fit unless something else goes.
- First-audio latency not stated; no GGUF/quantised build found on the card.
- Open: measure first-audio time and real VRAM on this GPU, and decide whether the quality gain is worth ~8 GB.

## MiniCPM5-2B vs MiniCPM5-2B-SFT

SFT is the checkpoint before RL and OPD (the final model merges 16 RL experts, 5 of them agentic). The final `MiniCPM5-2B` is the one to use as talker. The SFT checkpoint is only interesting as a fine-tuning start.

## Open checks

1. 1B vs 2B talker: VRAM in the full stack, latency, quality on cover lines and short replies (roadmap step 4).
2. Phrase-cache coverage of FRED's tool turns.
3. Barge-in cancel time on this PC.
4. VoxCPM2 vs Kokoro (VRAM, first audio, English quality).

## Talker on the Pi 5 (asked 2026-10-02, not decided)

- MiniCPM5-1B Q4_K_M is a 0.69 GB file, so it fits a Pi's RAM; speed on the Pi's CPU is unmeasured. My guess is single-digit to low-teens tokens per second, so a 30-token cover line would take about 3 s and would be noticed. Pre-synthesised phrases and a pick-a-phrase role (a few output tokens) would not.
- It would need an explicit exception to the GPU-only rule (LLMs are GPU-only so far).
- The L4 judge reads up to 600 tokens every few seconds; that does not fit a Pi. The talker and judge currently share one model process, so moving only the talker means two model processes.
- Saves about 1.1 GB on the PC.
- Training data: the MiniCPM5-2B card says its post-training used UltraData-SFT-Agent-2609 (483,661 agent trajectories, 82,760 of them tool use, Apache-2.0). The MiniCPM5-1B card lists only UltraData-SFT-2605 and UltraData-Math and does not mention the agent set, so the 1B's tool use is unverified and must be measured.
