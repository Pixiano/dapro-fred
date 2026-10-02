# Assembled FRED: L1 Interaction clock and verification (finalised 2026-10-02)

Layer 1 decides whose turn it is: is someone talking, is it Vatsal, has the
turn ended, is this a real interruption. Parent plan:
`Mark_II_Testing/ASSEMBLED_FRED.md` (sections 4 and 6). L0 inputs:
`ASSEMBLED_FRED_L0.md`. English is the working language.

## Final picks

| # | Component | Pick | Runs on | VRAM | Latency | Status |
|---|---|---|---|---:|---|---|
| 1 | Interaction clock | One tick every 160 ms, one output state: LISTEN / END_OF_TURN / BARGE_IN / BACKCHANNEL / IGNORE | CPU | 0 | cheap | to build |
| 2 | VAD | from L0 (webrtcvad now, Silero v5 later) | CPU | 0 | <1 ms | see L0 |
| 3 | End-of-turn (audio) | Smart Turn v3.2 int8 ONNX, 8M parameters | CPU | 0 | ~12 ms | primary; to wire |
| 4 | End-of-turn (meaning) | LiveKit turn-detector (~135M, text-only, INT8 ONNX) as a second opinion | CPU | 0 | not measured | **challenger: adopt only if the A/B below shows a gain** |
| 5 | Speaker gate | `voice_id` ECAPA (existing) | CPU | 0 | ~75 ms [measured] | existing |
| 6 | Speaker separation | Nemotron-3-Diarization (optional, from L0) | CPU | 0 | 0.32 s buffer | to test |
| 7 | Echo handling | Headphones: none. Speakers: voice ID + level gate | CPU | 0 | - | to build |

**L1 GPU total: 0.** All L1 models are CPU (turn-detection models and Nemotron diarization are on Vatsal's CPU allowance).

## Rules the clock applies

- **End of turn:** VAD silence >= 200 ms AND Smart Turn probability >= 0.5, or silence >= 1.2 s as a hard cap. With the chord held, release is the end. If the challenger is adopted it adds a second condition on the transcript (threshold tuned on Vatsal's recordings, not guessed).
- **Barge-in:** VAD + voice ID. A real interruption cancels speech and any pending tool; a short "mm-hm" is a backchannel and does not.
- **Open mic:** wake word + speech, or the 8 s follow-up window after FRED speaks.
- **Not Vatsal / TV / second voice:** IGNORE (voice ID, plus diarization if adopted).

## Why

- **One clock, outside the brain:** Venus showed "should I talk" can live outside the brain. A tick is cheap, so it can run faster than the 480 ms to 1 s of the other designs.
- **Smart Turn v3.2 first:** audio-native (hears pauses, pitch, trailing words), needs no transcript, 8M parameters, ~12 ms on CPU, BSD-2.
- **LiveKit turn-detector as the second opinion:** best published accuracy among open models under 200M. On LiveKit's own end-of-turn benchmark (`eot-bench`, 14 languages) it cut false cutoffs at a 300 ms budget to 9.9%, versus 35.2% for Smart Turn v3.2 and 27.8% for LiveKit's own mini model. Those figures come from a search summary of the leaderboard (the page itself did not fetch), the benchmark is run by the model's vendor, and it is multilingual, not English-only. It is also unconfirmed that the benchmark's "v1" row is the same ~135M open-weights model (the "v1-mini" row may be). So it earns a test, not a swap.
- **Why not adopt it outright:** it reads the transcript only (no pauses or intonation), so it can only run after speech-to-text produces words (our STT re-decodes about once a second); it sees at most 128 tokens over 6 turns; and its licence is the custom "livekit-model-license", which must be read before use.
- **voice ID stays the identity gate:** diarization labels voices as speaker 0..7; it never says "this is Vatsal".

## Considered and not picked

| Candidate | Why not |
|---|---|
| Easy Turn (ASLP-lab) | Best accuracy on its own test set (96.3% complete, 97.7% incomplete, 91% backchannel, 98% wait) and the only one with a backchannel state, but ~850 MB, 263 ms on a 4090 and trained mostly on Chinese data; over the size limit |
| TEN Turn Detection | Needs a 7B language model plus an ASR pass |
| Smart Turn v2 | Superseded by v3.x |
| Tiny Turn Detector | 73% on its own validation set |
| UltraVAD | Large context-aware model; over the size limit |
| CAM++ speaker verification | Not needed: ECAPA voice ID already works. (A pasted plan paired CAM++ with the speechbrain ECAPA repo; those are different models) |

## Open checks (gates before this is final)

1. **A/B on Vatsal's own speech** (the plan's offline turn-clock phase): Smart Turn alone vs Smart Turn + LiveKit text model vs VAD silence only. Record mid-sentence pauses, thinking pauses, short commands, backchannels. Metrics: false cutoffs (interrupting him) and added wait.
2. **Latency of the text path:** end of speech to decision, including the STT text. If it adds more than ~200 ms over Smart Turn alone, it stays out.
3. **LiveKit licence:** read `livekit-model-license` for personal local use.
4. **Thresholds (200 ms, 0.5, 1.2 s):** calibrate from the recordings.
5. **False barge-in rate** with and without diarization (shared with L0 check 4).
6. **Echo:** speakers vs headphones, tested separately.
7. **Smart Turn v3.2 on this PC** (ONNX Runtime CPU) measured latency.

## Still unplaced

Speech-to-text (current plan: faster-whisper large-v3-turbo with VAD-windowed re-decode, ~1.6 GB GPU) and tone detection (emotion2vec+) are in the parent plan's component table but not in L0 or L1. Layer to be assigned when L2/L3 are finalised.

## Pi 5 (discuss later)

L1 is the best candidate for the Raspberry Pi 5: everything here is small and CPU-only. Questions: ARM builds of ONNX Runtime and NeMo-Speech.cpp, network round-trip to the PC, and where the microphone lives. Nothing in this file depends on it.
