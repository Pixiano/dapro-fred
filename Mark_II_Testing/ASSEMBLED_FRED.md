# Assembled FRED — design doc (living)

**Status:** design only, nothing built yet. Started 2026-09-30. **Chosen
direction for Mark II** (Vatsal, 2026-09-30): go all in on the assembled
architecture; the pre-made models are parked as references and fallbacks.

### Mark II final ranking (2026-09-30, agreed with Vatsal)

| # | Candidate | Basis |
|---|---|---|
| 1 | **Assembled FRED** (this doc) | FRED's pipeline + a text brain beat every omni model by 20–40 pts on conversation / tools / honesty [measured]; real-time half (turn clock, talker, early start) untested |
| 2 | MiniCPM-o 4.5 (cand. 6) | best real-time: 63/70 realtime, only one that spoke up proactively; weak brain, conversation 8/21 (38%) → fallback front end |
| 3 | MiniCPM-o 2.6 (cand. 5) | best omni brain: 12/21 (57%), screenshots 10/12, most honest |
| 4 | MiniCPM5-2B as FRED's brain (cand. 7) | isolated: tools 72.8% vs 4B 77.8%, general 80.0% vs 87.3%; a component of #1 (2B brain / 8 GB variant), fine-tune target |
| 5 | Qwen2.5-Omni (cand. 3) | 9/21 (43%), 9/12; needed segmented-attention + VRAM-cap fixes |
| 6 | Realtime-Venus (cand. 1) | 7/21 (33%), 7/12; invents unknowables |
| – | Qwen2.5-Omni-FREDtools (cand. 4), candidate 2 | not tested; dropped |

Omni-model scores predate the 2026-09-30 benchmark isolation fixes and ran
without FRED's pipeline. They are indicative, but not exactly comparable to
the FRED rows.
**Tags:** [confirmed: source] = read this session; [measured: file] = our
own measurement; [inference] = reasoning or estimate, needs a measurement.

## Why this exists

Candidate 6 (base MiniCPM-o 4.5, native duplex) is being tested now. Venus
(same architecture) showed that a native model hears, sees and takes turns
well but answers unprompted only 10/24 to 13/41 times, invents what it can't
know, and has no tools or memory (`Realtime_Venus/1/VERDICT.md`). This doc is
(a) the plan if 4.5 fails the same way, and (b) how to put FRED's brain
behind 4.5 if it passes. The approach: build the duplex behaviour from
small parts around FRED's existing router, tools, gates and memory, the way
DuplexOmni splits a fast interaction layer from a slow thinking layer.

## 1. The ideal assembled architecture (target design)

Five layers. Each one says what it borrows, and from where.

| Layer | What it does | Borrowed from | Why |
|---|---|---|---|
| **L0 Senses** (always on) | Mic at 16 kHz in 80 ms chunks (the wake-word InputStream we already have). Screen at 1 fps through a change detector, with a native-res focus crop on the changed region. Camera presence (already exists). | Venus/4.5: continuous audio+vision, 0.990 tile-max skip threshold, focus crop (0/9 → 8/9 small-text reads) | Most of the time nothing changes. Venus skipped ~37% of idle seconds [measured: VERDICT.md]. The crop is what made small text readable. |
| **L1 Interaction clock** (CPU, 160 ms tick) | Each tick emits one state: LISTEN / END_OF_TURN / BARGE_IN / BACKCHANNEL / IGNORE. Inputs: VAD, Smart Turn v3 probability, voice_id (is it Vatsal), TTS-playing flag, headphones flag. | DuplexOmni 480 ms slices; 4.5's 1 Hz speak/listen; Freeze-Omni 3-state chunk head; Joy-Duplex's external predictor (partial/complete/backchannel/accept/reject) | Venus showed the "should I talk" decision can live outside the brain. A tick is cheap, so it can run faster than 480 ms or 1 s. Replaces Venus's slow 1 s rhythm. |
| **L2 Talker** (fast, GPU) | Instant acks and cover lines ("checking your calendar"), spoken proactive lines, and later short chat replies. Pre-synthesised phrases are used where they fit. | DuplexOmni's interaction layer covering the wait; Talker-Reasoner's System 1; FRED's `fillers.py` + tool captions | DuplexOmni found interaction quality barely depends on how strong the thinker is (72.6 → 72.1). A small talker is enough. |
| **L3 Thinker** (async, GPU) | FRED's orchestrator: dispatcher, router, tools, confirmation gates, memory, Researcher/General agents. Its results go back to the talker as data. | DuplexOmni thinking layer (`[THINK]` in, `「…」` results out, `[WAIT]` resets it); Talker-Reasoner's Reasoner | The thinker sets the ceiling on reasoning (Big Bench Audio 77.2 → 50.3 with a weak thinker, 22.2 with none). FRED already has this layer and it works (tools 77.8%, gates 100%). |
| **L4 Proactivity** (slow loop, 2–5 s) | A compact timestamped event log goes to a judge, which returns speak? + score + a grounded line, then threshold, streak and cooldown checks. | Inner Thoughts (CHI '25) motivation scoring; PIRA-Bench/PIRF state tracking; "Take the floor when asked" (2609.19596) | Native duplex models speak when addressed or when there's silence, not because of content: only 4–7% warn about a hazard [confirmed: 2609.19596]. A deliberate content judge is the only known fix. |
| L5 Consolidation (nightly) | Unchanged: `consolidation.py`, `reflection.py`. | mark2_plan §27 | — |

Speculative start (endpoint anticipation, 2606.13450) sits across L1 and L3.
When the turn probability passes 0.5, the thinker begins prefill and
generation on the partial transcript. The result is committed at
END_OF_TURN and thrown away if Vatsal keeps talking. **Tools never run
speculatively** (see §5).

**What this gives vs a native duplex model [inference]:**
- It answers every triggered turn (FRED answers 100% of chord presses; Venus answered 42%).
- It keeps all ~110 tools, the confirmation gates and memory, and says "I'd have to check" instead of inventing (Venus: 0/3 on honesty).
- It reads exact small text via OCR, and every part is tunable and swappable.
- Its turn latency is equal or better (§6).
- It loses real overlapping speech, prosody-level hearing beyond an emotion label, natural backchannel timing (backchannels are canned), and trained ignoring of speech not aimed at it. Venus got 0.06 on that; here voice_id plus the wake word/chord is the substitute.
- It has more moving parts, so there are more failure seams.

### 1b. Realistic hybrid variant: MiniCPM-o 4.5 front end + MiniCPM5-2B brain

In this variant, 4.5 is L0+L1: continuous audio and screen, the 1 Hz
speak/listen decision, focus crop and stale-audio drop. Its speak/listen
output and its text draft go to a FRED bridge:
- A chat-only turn may use 4.5's draft.
- Anything else (the dispatcher or `_classify_turn` says tools are needed, or the chord is held) sends the utterance text to the brain. That is MiniCPM5-2B plus FRED's router, tools, gates and memory.
- The brain's reply is spoken by Kokoro. Tool results are fed back into 4.5's context as text, so its later turns know what happened (DuplexOmni-style injection).
- The utterance text comes from Whisper on the same audio. 4.5's own ASR is untested.

| VRAM (16 GB) | 4.5 + current FRED | 4.5 + 2B brain |
|---|---:|---:|
| Windows/desktop reserve | 1.0 | 1.0 |
| MiniCPM-o 4.5 NF4 (as Venus) [measured: VERDICT.md] | 7.6–8.9 | 7.6–8.9 |
| Brain LLM | Qwen3.5-4B Q6_K 5.0 [measured] | MiniCPM5-2B Q4_K_M ~2.1 [inference] |
| Router embedder | Qwen3-Embedding-4B 3.4 [measured] | Qwen3-Embedding-0.6B ~0.7 [inference] |
| Whisper turbo (utterance text) | ~1.6 [inference] | ~1.6 |
| **Total** | **18.6–19.9: does not fit** | **13.0–14.3: fits, 1.7–3 GB headroom** |

The 2B brain is what makes the hybrid fit. It only fits if the embedder
also shrinks, so the routing suites (83.0/80.2%) must be re-run first. The
risk that remains is GPU contention: 4.5's 1 s units must stay under 1 s
while the brain generates. Venus backed up 43 min when the Recognize job
shared the GPU. Measure 4.5's unit time with the brain decoding at the same
time before committing.

### 1c. Comparison: basic assembled vs ideal assembled vs 4.5 hybrid

All [inference] unless marked; FRED today = current pipeline (benchmark
baseline 2026-09-27). "Ideal" = §1 (turn controller + voice ID + echo
handling + talker/thinker split + speculative start + event-log judge).

| | Basic assembled (current FRED + streaming turn detection) | **Ideal assembled (§1)** | MiniCPM-o 4.5 hybrid (§1b) |
|---|---|---|---|
| Answers when addressed | every detected end of turn | every turn, and knows it's Vatsal (voice ID) | paper: 0.95 fresh-onset (Duplex-MPE); Venus 42% unforced [measured] |
| End of speech → first audio | ~1.0–1.4 s | ack ~0.3 s; first real audio ~0.55–0.75 s (speculative start); +1–3 s when a tool runs | ~1.2–1.6 s (Venus) |
| Tools (~110) | native, 77.8% [measured] | native, async behind the talker | via bridge; confirmation round-trip needed |
| Memory | FRED's | FRED's | FRED's via bridge |
| Invents unknowable facts | low (tools / "can't check") | low + explicit "I'll check" cover | unknown (Venus high, 2.6 low [measured]) |
| Small screen text | OCR, exact | OCR + focus crop, exact | weak unless focus crop fires |
| Scene / what just happened | captions only | captions + OCR + change history (second-hand) | native, continuous |
| Tone / background sounds | lost | labels via emotion + sound classifiers | native |
| Ignores TV / others talking | weak (wake word ~40 false/h [measured]) | voice ID + chord/wake | trained: 0.16–0.18 false (Venus 0.06–0.12) |
| Barge-in | VAD stop ~100 ms | VAD + Smart Turn: backchannel vs real interruption, cancels pending tool | 0.60 interruption response (paper) |
| Proactivity | none / basic | event-log judge, tunable, can use tools (shadow mode first) | native 1 Hz, reacts mostly to being addressed |
| True overlap / natural backchannels | no | partial (canned acks) | yes |
| VRAM | ~7.7 GB today [measured] + perception | **~13.3 GB** (needs 0.6B embedder; 16.0 with the 4B) | **13.0–14.3 GB** with 2B brain; 18.6–19.9 with current FRED |
| Training | none | none for v1 | none (integration work) |
| Engineering | small | large (many parts, tuning) | medium (bridge, remote-code fragility) |
| Upgrades | per part | per layer | monolithic front end (next omni ~2027) |

Reading: the ideal assembled design wins the assistant jobs (tools, memory,
honesty, latency, exact text, TV rejection, tunable proactivity); 4.5 wins
real perception (tone, visual understanding, overlap). Both only fit in
16 GB with a smaller router embedder, and the hybrid only with the 2B brain.

## 2. DuplexOmni (arXiv 2606.09186, 2026-06) — what it does

[confirmed: arXiv HTML v1, HF MuyeHuang/DuplexOmni, github MuyeHuang/DuplexOmni]

- **Split.** The interaction layer is Qwen3-Omni-30B-A3B, fine-tuned into a Thinker-Talker working on 480 ms slices. Each slice it reads the dialogue history, the thinking layer's results so far, and the previous slice's audio plus one video frame. Per slice it outputs:
  - a thinking-control signal
  - a semantic interpretation of the user's latest input
  - 480 ms of text and speech (six codec frames)
- **Protocol.**
  - `[THINK]` at the end of a user turn starts background reasoning. The thinking layer runs asynchronously and streams fragments back wrapped in `「…」`. They are injected before the assistant sentence that uses them.
  - `[WAIT]` suspends and resets the reasoning when the user adds or changes something. The interaction layer can also stop the thinker's stream with control tokens.
  - `^` marks where the other speaker begins. `[CUT]` marks where assistant audio stopped; the text after it is kept as "ghost text", meaning intended but unsaid. `[PENDNS]` encodes N seconds of shared silence.
  - The thinking layer is any OpenAI-compatible endpoint (`S2_THINK_BASE_URL`). In the paper it was Gemini-3.1-Flash-Lite.
- **Covering the wait.** Nothing special: the interaction layer keeps talking ("This is indeed a problem that needs careful analysis…") and folds the result in when it arrives.
- **Proactive speech.** 50% of samples are assistant-initiated, but they are scripted by scenario seeds (step-by-step guidance, troubleshooting), not triggered by content.
- **Data.** ~620K scenario seeds × ~3.02M text dialogues (UltraChat, WildChat, OASST2…). A Writer LLM writes a natural script from content plus a seed. A Director LLM adds the timing tokens without changing any words. Consistency filters drop conflicting tokens, missing triggers and results with no cause. Then TTS (Qwen3-TTS) and slicing. Qwen3.5-397B did all the writing and annotation; the data is ~70% Chinese.
  - Patterns: delayed reasoning 94.3%, shared silence 68.2%, overlap 49.8%, interruption+reset 41.9%, pure backchannel 3.1%.
  - Seed types: guidance 32.1%, turn-taking 31.3%, condition revision 18.4%, troubleshooting 13.9%, extended listening 4.4%.
- **Training.** Two-stage SFT, alternately training the Thinker and the Talker, on 128 H20 GPUs. Deployment needs ≥8 H20s, so it cannot run here.
- **Numbers.**
  - Full-Duplex-Bench v1.5 ToR 72.6%, latency 0.506 s (MiniCPM-o 4.5: 0.502 s).
  - Big Bench Audio 77.2%.
  - Ablation: weak thinker → FDB 72.1 / BBA 50.3; no thinker → 65.2 / 22.2; thinker alone on raw audio → BBA 58.9. The interaction layer also filters and organises what reaches the thinker.
  - WER on 1–5 word utterances is 25.1%, which matters for short commands.

### What FRED borrows

| DuplexOmni idea | FRED equivalent | Training needed? | Cheapest substitute (v1) |
|---|---|---|---|
| Interaction/thinking split | Talker (L2) + orchestrator (L3) in separate processes | No | Existing turn thread + a second llama-server for the talker/judge |
| `[THINK]` decision | Does this turn need tools? | No | `dispatcher.match` + `_classify_turn` (already exist) |
| `「…」` result injection | Tool result goes back as a data message | No | The thinker's reply is spoken directly; no rephrase hop (saves latency) |
| Talker covers the wait | Ack line before the tool result | No | `fillers.pick_filler` + `_on_tool_event` captions (pre-cached) |
| `[WAIT]` reset of pending thinking | New speech while the thinker is busy | No | Cancel generation; merge utterances; restart (§5 rules) |
| `[CUT]` + ghost text | Interrupted reply | No | Store `interrupted_at` + unsaid remainder in conversation state |
| `[PENDNS]` silence awareness | Silence duration in the event log | No | Timestamps in the event log |
| 480 ms slice decisions | L1 tick | No for v1 | Smart Turn + VAD + rules. Optional turn head later (§9) |
| Assistant-initiated turns | Proactive judge | No for v1 | L4 judge on the event log |
| Writer-Director data | Synthetic turn/addressee data | Only for §9 extras | Recipe in §9 |

## 3. Other work — FRED-relevant takeaways only

- **Joy-Duplex / JoyAI-Talker (2608.01119, 2026-08):** a plug-and-play state-driven gate beside an unchanged model; 0.88 response to interruptions, very low false triggers under background speech [confirmed: abstract; the 1.7B size is from the landscape doc]. → L1 is the same pattern, with a smaller predictor.
- **Freeze-Omni (2411.00774):** the LLM stays frozen and a small head classifies each chunk as continue / reply / ignore [prior knowledge]. → The minimal trainable turn head if Smart Turn isn't enough.
- **Talker-Reasoner (2410.08328):** a fast Talker speaks from the latest belief state and a slow Reasoner updates that state asynchronously [prior knowledge]. → The event log plus conversation state is FRED's belief state.
- **Decoupled FSM (2609.03321):** learns turn states from real human-human tapes and semantics from text dialogues, trained separately so the LM keeps its skills [confirmed: abstract]. → Don't fine-tune turn-taking into the brain; keep it outside.
- **Endpoint anticipation (2606.13450, Interspeech '26):** predicts end of turn up to 2.56 s early; −505 ms latency for +28.4% compute [confirmed: abstract]. → Speculative start (§6).
- **Smart Turn v3.x:** Whisper-tiny encoder + linear head, 8M params, 8 MB int8 ONNX, ~12 ms CPU, BSD-2, multilingual [confirmed: HF card + blog title]. **LiveKit turn-detector:** a text model on transcripts, now 134.5M (distilled from Qwen2.5-0.5B), 14 languages including Hindi, custom license [confirmed: HF]. → Smart Turn as primary; LiveKit as an optional second opinion on the transcript.
- **"Take the floor when asked, not when needed" (2609.19596, 2026-09-17):** native duplex models react to being addressed or to silence, rarely to content (14–15% challenge a false fact, 4–7% warn of a hazard) [confirmed]. → Justifies L4 as a separate content judge.
- **Inner Thoughts (CHI '25, 2501.00383):** generate a thought, score its intrinsic motivation 1–5 on 8 heuristics (relevance, information gap, expected impact, urgency, coherence, originality, balance, dynamics), and speak only above a threshold. Preferred 82% of the time vs a next-speaker baseline [confirmed]. → Judge prompt (§7).
- **PIRA-Bench (2603.08013):** the hard part of proactive GUI help is noisy browsing and multithreaded task switching. Their fix (PIRF) is memory-aware state tracking per task thread [confirmed: abstract]. → The event log carries `app`/`thread` keys.
- **Note:** 2507.09313 is **ProactiveVideoQA** (PAUC metric: reply timing matters as much as content), not ProactiveBench. ProactiveBench is 2410.12361 [prior knowledge]. Takeaway: score *when* FRED speaks, not only what it says.

## 4. MiniCPM5-2B for FRED

[confirmed: huggingface.co/openbmb/MiniCPM5-2B card, repo listing]

- **Repos:**
  - `openbmb/MiniCPM5-2B` (BF16, RL+OPD final)
  - `-GGUF` (F16 5.04 GB, Q8_0 2.68 GB, Q4_K_M 1.56 GB)
  - `-DSpark` (+GGUF), a speculative-decoding draft model
  - `-SFT`, `-Base`, `-GPTQ`
  - `openbmb/MiniCPM5-1B` (+`-GGUF`)
  - Uploads dated 2026-09-06..12. Apache-2.0.
- **Architecture:** standard `LlamaForCausalLM`, 2.52B params (1.98B non-embedding), 42 layers, GQA 16 Q / 2 KV heads, 131,072 context. Because it's plain Llama, llama.cpp support is expected; the card's own command is `llama-server … -ngl 99 -c 8192 --jinja`.
- **KV cache** [inference: from config, head dim 128, f16]: ~43 KB/token, so 8k ≈ 0.35 GB and 32k ≈ 1.4 GB.
- **Tools:** XML-style tool calls. SGLang has a `minicpm5` parser; in llama.cpp this depends on the `--jinja` template. FRED's `_parse_text_tool_calls` would need an XML branch [inference]. Thinking mode is toggled with `enable_thinking`. Recommended sampling is temp 1.0, top_p 0.95; for the judge we'd use low temperature (a deviation to test).
- **Card benchmarks, vendor-reported, 2B vs Qwen3.5-4B:**

  | Benchmark | MiniCPM5-2B | Qwen3.5-4B |
  |---|---:|---:|
  | BFCL v4 | 66.6 | 56.8 |
  | τ²-Telecom | 97.1 | 92.1 |
  | IFBench | 66.3 | 59.0 |
  | IFEval | 86.7 | 90.2 |
  | Multi-IF | 71.8 | 73.6 |
  | Average | 53.9 | 51.1 |

  FRED's own suites decide, not these.

| Role | 2B vs current Qwen3.5-4B [inference unless noted] |
|---|---|
| (a) Talker/responder with FRED router + tools | Better on the card's tool metrics and ~2× faster decode. Unknown on FRED's 81-case tool suite, honesty cases and "no markdown in speech". Must pass ≥77.8% tools and ≥76.2% conversation to replace the 4B. |
| (b) Turn/proactivity judge | Good fit: a short JSON output and a small prompt, and it can run in its **own process** so it never contends for the main model's lock. FRED already crashed once on two concurrent decodes on one model (`screen_watcher.py` header). |
| (c) Both | Target if (a) passes: one 2B for everything frees ~3 GB. Until then, 2B = judge + cover lines and 4B = thinker. |

MiniCPM5-1B (Q8 ~1.4 GB) is the fallback judge if VRAM gets tight.

## 5. Architecture (design A, no native model)

```
 MIC 16k ──► wakeword.py InputStream (80 ms) ──┬─► openWakeWord (CPU, frozen)
                                               ├─► VAD (webrtcvad/Silero, CPU)
                                               ├─► ring buffer ─► Smart Turn v3 (CPU, on pause)
                                               ├─► voice_id ECAPA (CPU, on segment)
                                               └─► stt_stream: faster-whisper windows (GPU)
                                                          │ partials/final + tone (emotion2vec, GPU)
 SCREEN 1 fps ─► change_detector (tile-max 0.990) ─► focus crop ─► OCR + V4.6 caption (GPU, on change)
                                                          │
                          ┌───────────────────────────────▼──────────────┐
                          │ event_log (JSONL ring, last ~10 min)          │
                          └──────┬─────────────────────────────┬──────────┘
            [PROCESS: main]      │                             │ [PROCESS: talker llama-server]
  L1 turn_controller (160 ms tick)│                  L4 proactive judge (MiniCPM5-2B)
   LISTEN/EOT/BARGE_IN/BACKCH/IGNORE                  every 2-5 s or on salient event
        │ EOT (or chord release / wake+speech)              │ speak? score≥4, streak 2, cooldown
        ▼                                                   ▼
  pill_app turn thread ─► orchestrator.process_stream ─► notifier/_speak_proactive
   (dispatcher → classify → router → tools → gates → memory)   (pill first, voice if score 5)
        │ ack/filler/tool caption immediately (phrase_cache)
        ▼
  Kokoro (CPU) speak(cancel=) ◄── BARGE_IN cancels within ~100-150 ms
```

Where each decision is made:
- **End of turn:** L1. With the chord, release is the end of turn (unchanged). In open mic (wake+speech, or the 8 s follow-up window after FRED speaks), END_OF_TURN = VAD silence ≥200 ms AND Smart Turn p ≥0.5, or silence ≥1.2 s as a hard cap.
- **Barge-in:** L1, while TTS is playing. Speech for ≥300 ms, AND voice_id says it's Vatsal, AND (headphones on, or level above the TTS echo floor) → cancel Kokoro. If the new speech is ≤2 words and Smart Turn says incomplete ("mm", "yeah"), it's a BACKCHANNEL: resume. Otherwise it's a new turn, carrying `interrupted_at` + ghost text.
- **Backchannel from FRED:** v1 has none. Canned "mm-hm" on long pauses is a later optional; Venus showed backchannels matter little on a desktop.
- **Proactive interrupt:** L4 only. Nothing else may speak unprompted.
- **Tool delegation:** L3 (`dispatcher` / `_classify_turn` / router / agents.py), unchanged.
- **Confirmation:** L3 `_request_confirmation`, unchanged. The answer must come from an explicit user turn: never from the judge, a speculative start or a backchannel.
- **`[WAIT]` rules:** new user speech during L3 work:
  - no tool has run → cancel generation, merge the two utterances, restart
  - a read-only tool is running → let it finish, then restart with both utterances
  - a gated tool has executed → never cancel; report what was done

## 6. Component choices

| Component | Pick | Alternative | VRAM | Latency | License | Win/Blackwell |
|---|---|---|---:|---|---|---|
| Wake word | openWakeWord hey_fred (frozen) | — | CPU | 80 ms frames | Apache-2.0 | verified (running) |
| VAD | webrtcvad (installed) → Silero ONNX | TEN VAD | CPU ⚑ | <1 ms/frame | MIT | webrtcvad verified |
| End of turn | Smart Turn v3.x int8 ONNX | LiveKit turn-detector 134.5M; Easy Turn | CPU ⚑ | ~12 ms | BSD-2 | unverified (ONNX, low risk) |
| Speaker gate | voice_id ECAPA (exists) | — | CPU ⚑ | ~75 ms [measured: voice_id.py] | Apache-2.0 | verified |
| STT | faster-whisper large-v3-turbo, VAD-windowed re-decode every ~1 s | Nemotron 3.5 ASR streaming 0.6B (80–1120 ms chunks, NeMo); Moonshine streaming-medium 266M (EN, MIT); Kyutai STT-1B (Rust/Linux-first) | ~1.6 [inference] | RTF 0.095 [measured]; ~0.15 s tail | MIT | verified |
| Tone | emotion2vec+ base on the final utterance | emotion2vec+ large | ~0.4 [inference] | ~30 ms [inference] | model license "other" | unverified |
| Thinker | Qwen3.5-4B Q6_K (existing) | MiniCPM5-2B if P0 passes | 5.0 [measured] | TTFT ~0.3 s [inference] | Apache-2.0 | verified |
| Talker + judge | MiniCPM5-2B Q4_K_M, own llama-server | Q8_0 (+1.1 GB); MiniCPM5-1B | ~2.1 | ~0.15 s for a short JSON [inference] | Apache-2.0 | unverified (plain Llama arch) |
| Router embedder | Qwen3-Embedding-0.6B | keep 4B (3.4 GB) | ~0.7 | ~20 ms | Apache-2.0 | verified family; re-run routing |
| Screen captioner | MiniCPM-V 4.6 Q4_K_M 0.53 GB + mmproj f16 1.11 GB | Qwen3.5-4B mmproj via vision_server (100% on 12 screenshots); Florence-2 | ~1.9 | ~0.5 s/caption [inference] | Apache-2.0 | unverified (llama.cpp minicpmv4_6 support) |
| OCR | Windows.Media.Ocr on the focus crop | PaddleOCR on GPU | CPU ⚑ | ~50–150 ms [inference] | OS | unverified |
| TTS | Kokoro (existing) | — | CPU (exception) | 0.1–0.4 s first audio [measured on GPU; CPU slower] | Apache-2.0 | verified |
| Echo handling | headphones → none; speakers → voice_id + level gate | WebRTC APM AEC | CPU | — | — | — |

⚑ = tiny model on CPU. Flagged per the GPU-only rule; Vatsal needs to OK
these (or move Smart Turn and Silero to CUDA ONNX, ~50 MB each).

## 7. VRAM budget (design A, everything co-resident)

| Item | GB | Basis |
|---|---:|---|
| Windows/WDDM/desktop | 1.0 | [inference] |
| Qwen3.5-4B Q6_K + mmproj, 16k ctx | 5.6 | 5.0 measured + mmproj [inference] |
| MiniCPM5-2B Q4_K_M, 8k ctx | 2.1 | 1.56 file + 0.35 KV + buffers |
| Qwen3-Embedding-0.6B | 0.7 | [inference] |
| Whisper large-v3-turbo fp16 | 1.6 | [inference] |
| MiniCPM-V 4.6 Q4_K_M + mmproj | 1.9 | file sizes + buffers |
| emotion2vec+ base | 0.4 | [inference] |
| **Total** | **13.3** | ~2.7 GB headroom |
| Same but keeping the 4B embedder | 16.0 | **does not fit** |

Risks:
- DaVinci Resolve and the uvicorn :8000 server held VRAM during the benchmark.
- The nightly Recognize job shares the GPU.
- The model-lifecycle idle unloads (`LLM_IDLE_UNLOAD_SECONDS`) interact with an always-on loop.

**Fallback:** drop the V4.6 captioner and caption on the thinker's second slot (−1.9 GB), accepting contention.

## 8. Latency budget (end of speech → first audio) [inference, to measure]

| Step | Chord, now | Design A | Design A + speculative |
|---|---:|---:|---:|
| End-of-turn detection | 0 (release) | 0.25 (200 ms silence + Smart Turn) | 0.25 (overlapped) |
| STT final | 0.3–0.5 (whole utterance) | 0.15 (tail window) | overlapped |
| Dispatcher/router/prompt | 0.05 | 0.05 | overlapped |
| LLM prefill + first sentence | 0.4–0.6 | 0.4–0.6 | ~0.1 (already running) |
| Kokoro first chunk (CPU) | 0.2–0.4 | 0.2–0.4 | 0.2–0.4 |
| **Real answer** | **~1.0–1.5** | **~1.0–1.4** | **~0.55–0.75** |
| Ack/filler (pre-cached) | — | ~0.3 | ~0.3 |

For comparison, Venus took ~1.2–1.6 s (1 s rhythm + 0.37–0.65 s compute).
Tool turns: the ack lands at ~0.3 s and the answer at tool time + ~0.6 s.
Speculative rules:
- The speculative generation is discarded if the transcript changes by more than one word or Smart Turn drops below 0.3.
- Tool calls wait for the commit.
- The expected cost is +~28% LLM compute (per 2606.13450).

## 9. Proactivity loop (L4)

**Event log.** A JSONL ring in memory, persisted to the daily log. One line per event:

```
{"id":"e812","t":"14:02:11","src":"screen","app":"Windows Terminal","kind":"new_text",
 "text":"KeyError: 'x'","how":"ocr","conf":0.93}
{"id":"e813","t":"14:02:14","src":"speech","who":"vatsal","addressed":false,
 "text":"oh come on","tone":"angry 0.61"}
{"id":"e814","t":"14:02:30","src":"sys","kind":"idle","secs":16}
```

**Cadence.** Run on each salient event (new error text, speech not addressed
to FRED, app switch) and at most every 3 s; idle ticks every 30 s. The judge
sees the last 90 s (≤600 tokens), today's context line and the open reminders.

**Judge prompt** (MiniCPM5-2B, non-thinking, temp 0.2). This is a trimmed
Inner Thoughts. It returns
`{"speak":bool,"score":1-5,"because":[event ids],"line":"≤20 words"}` and
scores four things:
1. Is it about what he's doing right now?
2. Is there an information gap he'd want filled?
3. Is it urgent (cost of waiting)?
4. What does interrupting cost now (typing fast, in a call, media playing)?

**Gate, all of which must hold:**
- score ≥4 on 2 consecutive runs (`PROACTIVE_INTERRUPT_STREAK` idea)
- `because` names real event ids
- if the event's source is VLM text, the quoted text must also appear in the OCR output, so nothing unseen gets said
- he's present (`presence`), not talking (`voice_activity`), not in a call or watching media (`media_state`)
- per-topic cooldown 10 min, global 3 min, reusing `proactive_checks`' naturalness gate and state file

Delivery: score 4 → pill text only; score 5 → spoken via `_speak_proactive`.

**Venus failures and how this avoids them:**
- *False alarms from false wakes:* no reply without a trigger, except a gated judge line. A wake detection needs speech after it plus a voice_id match.
- *Hijacked answers* (the alert rule leaking into unrelated replies): judge rules live only in the judge process. The answering prompt gets the recent events as data with "mention only if relevant" (the same pattern as `_screen_context`).
- *Invented details:* grounding plus OCR cross-check. The 2B never answers a question, it only raises a flag.

**Rollout:** shadow mode first (log only, don't speak) for 2–3 days. Tune the threshold on Vatsal's "useful / not now" labels, then enable.

## 10. Reuse vs new code (file level)

**Reuse unchanged:**
- `orchestrator/orchestrator.py`: `process_stream`, `_classify_turn`, `_request_confirmation`, `_generate_with_tools`
- `dispatcher.py`, `tool_router.py`, `agents.py`, `tools/registry.py`, `memory/memory_manager.py`
- `audio/tts_kokoro.py` `speak(cancel=)`, `audio/fillers.py`, `audio/phrase_cache.py`
- `ui/pill_app.py`: turn thread, `_on_tool_event`, `_speak_proactive`
- `input/hotkey.py`, `input/wakeword.py`
- `orchestrator/proactive_checks.py` gate, `utils/notifier.py`, `orchestrator/consolidation.py`, `reflection.py`
- `llm/vision_server.py` (llama-server subprocess pattern → talker server)

**Reuse and wire (small edits):**
- `input/voice_activity.py` (built, unwired)
- `input/voice_id.py` (built, unwired)
- `orchestrator/headphone_watch.py`
- `audio/media_state.py`
- `vision/screen_watcher.py` + `screen_context.py` (child-process pattern)
- `audio/stt_whisper.py`: add a windowed mode
- `_parse_text_tool_calls`: an XML branch if the 2B becomes the thinker

**From Mark II:** `Realtime_Venus/1/skip_threshold/` calibration, the focus-crop and stale-drop code in `live_duplex.py`, and `analyse_live.py` metrics.

**New (~6 small modules)**, prototyped under `Mark_II_Testing/Assembled/` before touching `Core/`:
- `turn_controller.py`
- `stt_stream.py`
- `event_log.py`
- `change_detector.py`
- `proactive_judge.py`
- `tone.py`

## 11. Training

None for v1. Optional later, in order of payoff:
1. **Threshold calibration, not training:** from the judge's shadow logs.
2. **Smart Turn fine-tune** on Vatsal's own logged turns plus Hinglish (their repo ships training code; hours on this card).
3. **Addressee/turn head** (Freeze-Omni-style classifier on MiniCPM5-1B hidden states, or LiveKit-size), trained on Writer-Director synthetic data:
   - seeds: DuplexOmni's 5 types + FRED-specific ones (TV speech, talking to someone else, sighing at an error, a mid-request correction)
   - content: `Core/data/tool_call_eval_holdout.jsonl`-style utterances
   - Writer and Director: Qwen3.5-4B, or a larger model overnight
   - Director tags `^ [CUT] [WAIT] [PENDNS]`
   - Kokoro voices + room noise at 0–10 dB; 5–20k samples
   - keep a slice of real open duplex data as a check
4. **LoRA on MiniCPM5-2B** for FRED's tool format from `tool_call_log`, only if P0 shows a tool gap. Re-run all suites after.

## 12. Evaluation

- `benchmarking/run.py --candidate assembled` (a new adapter): all suites. The bar is fred-current: routing 83.0/80.2, tools 77.8, conversation 76.2, gates 100, vision 12/12.
- `Mark_II_Testing/TO_BE_TESTED.md` 10 real tests, with a new "Assembled" column.
- Live metrics (an `analyse_live.py`-style script):

| Metric | Target |
|---|---|
| Answered rate on chord/wake+speech | 100% |
| Follow-up window answer rate | ≥90% |
| Unprompted lines per hour, do-nothing session | ≤1 |
| Useful-rate of proactive lines | ≥70% "useful" |
| Barge-in stop latency | <200 ms |
| Release→first word | p50 ≤0.8 s |
| Responses to TV speech | 0 |
| Peak VRAM | ≤15 GB |
| 8 h overnight | no crash, flat VRAM |

## 13. Risks / open questions

1. The co-resident budget depends on shrinking the embedder. That is untested for routing accuracy.
2. MiniCPM5-2B in FRED's llama-cpp-python build: template, XML tools, Blackwell kernels. All unverified.
3. MiniCPM-V 4.6 llama.cpp support is unverified.
4. Tiny CPU models (VAD, Smart Turn, voice_id, OCR) need Vatsal's OK under the GPU-only rule.
5. Echo/barge-in on speakers without AEC.
6. Open-mic addressedness without a trained head. v1 sidesteps it with the chord, wake word and follow-up window.
7. Whisper on short fragments: DuplexOmni's 25% WER on 1–5 words is a warning. Hinglish stays at 74%+ WER.
8. GPU contention with the Recognize job and DaVinci.
9. The judge's usefulness is unproven. Shadow mode first.
10. Tone ≠ sarcasm: emotion2vec won't catch "great, it broke again". Expect test 5 to be partial.

## 14. Phased build (smallest useful step first)

- **P0 (no FRED change):**
  - MiniCPM5-2B Q8_0 + Q4_K_M through `run.py` vs Qwen3.5-4B: tools, conversation, honesty, tok/s, VRAM.
  - Embedder 0.6B vs 4B on the routing suites.
  - Whisper-turbo VRAM.
- **P1:** barge-in (VAD + voice_id + `speak(cancel=)`) and the 8 s follow-up window with Smart Turn end-of-turn.
- **P2:** change detector + crop + OCR/caption → event log, fed to the answering prompt as data. Targets tests 1, 3, 10.
- **P3:** proactive judge in shadow mode → calibrate → pill-only → voice.
- **P4:** streaming partials + speculative start.
- **P5:** tone; the 2B as the only LLM if P0 passes; §11 extras as needed.

**Decision gate:** if MiniCPM-o 4.5 passes its tests, switch to §1b. P0 and P3 carry over unchanged.

## Sources (accessed 2026-09-30)

- DuplexOmni, arXiv 2606.09186 (2026-06): https://arxiv.org/html/2606.09186v1 · https://huggingface.co/MuyeHuang/DuplexOmni · https://github.com/MuyeHuang/DuplexOmni
- JoyAI-Talker / Joy-Duplex, arXiv 2608.01119 (2026-08-02): https://arxiv.org/abs/2608.01119
- Freeze-Omni, arXiv 2411.00774 (2024-11) · Talker-Reasoner, arXiv 2410.08328 (2024-10)
- Decoupled FSM, arXiv 2609.03321 (2026-09-03): https://arxiv.org/abs/2609.03321
- Endpoint anticipation, arXiv 2606.13450 (2026-06-11): https://arxiv.org/abs/2606.13450
- Take the floor when asked, arXiv 2609.19596 (2026-09-17): https://arxiv.org/abs/2609.19596
- Inner Thoughts, arXiv 2501.00383 / CHI '25: https://arxiv.org/html/2501.00383
- PIRA-Bench, arXiv 2603.08013 (2026-03-09) · ProactiveVideoQA, arXiv 2507.09313 (2025-07-12) · ProactiveBench, arXiv 2410.12361
- MiniCPM5-2B: https://huggingface.co/openbmb/MiniCPM5-2B · https://huggingface.co/openbmb/MiniCPM5-2B-GGUF · https://huggingface.co/openbmb/MiniCPM5-1B
- MiniCPM-V 4.6: https://huggingface.co/openbmb/MiniCPM-V-4.6 · https://huggingface.co/openbmb/MiniCPM-V-4.6-gguf
- Smart Turn v3: https://huggingface.co/pipecat-ai/smart-turn-v3 · LiveKit: https://huggingface.co/livekit/turn-detector
- Nemotron ASR streaming: https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b (3.5 multilingual note, 2026-06-04)
- Moonshine streaming: https://huggingface.co/moonshine-ai/moonshine-streaming-medium · Kyutai STT: https://huggingface.co/kyutai/stt-1b-en_fr
- emotion2vec+: https://huggingface.co/emotion2vec/emotion2vec_plus_base
- Internal: `docs/2026/09/30/research_duplex_landscape.md`, `docs/2026/09/27/benchmark_baseline.md`, `docs/2026/09/27/mark2_plan.md`, `Mark_II_Testing/Realtime_Venus/1/VERDICT.md`, `Mark_II_Testing/MiniCPM-o-4.5/1/README.md`

## Log

- 2026-09-30: MiniCPM5-2B under test as FRED's main LLM (candidate 7,
  sandbox): its tool-call markers are control tokens that llama-cpp-python
  drops from text — any integration must detokenize with special tokens on
  (or parse the stripped form, as the sandbox patch does). The 3.4 GB
  router/memory embedder is the next budget item after the LLM (§7): test
  Qwen3-Embedding-0.6B / a FRED-trained classifier on the routing +
  memory suites.
- 2026-09-30: Mark II closed with the ranking at the top. The assembled
  architecture is the chosen direction.

  Proposed build order (awaiting Vatsal's go):
  - Phase 0: FRED pipeline fixes (router, dispatcher, calculator, memory
    update).
  - Phase 1: turn clock offline. VAD + Smart Turn v3 on recordings of
    Vatsal. These are CPU models, so they need his OK.
  - Phase 2: live, with barge-in.
  - Phase 3: speculative early start.
  - Phase 4: the L4 judge in shadow mode.
  - Later: the 2B brain.

  A small-LLM successor search (≤4B, fine-tunes especially) is running.
