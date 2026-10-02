# FRED Mark II: Master Plan (single source of truth)

Written 2026-10-02 for a fresh agent with no prior context. Everything needed to
build, test and integrate the Mark II revamp is here or linked from here.
If two files disagree, this file wins for decisions made up to 2026-10-02; the
linked files hold the evidence.

Legend: **[measured]** = measured on this PC. **[card]** = from a model card or
vendor, unverified here. **[est]** = my estimate. **[open]** = undecided.

---

## 0. Standing rules (read first, obey always)

1. **Vatsal is the owner.** Use he/him if you ever need a pronoun for someone whose pronouns are unknown and address as sir directly (preferable over general chats).
2. **English is the default medium** for ASR, brain, embedder, benchmarks and training data unless told otherwise. Hinglish is minor and never a deciding factor.
3. **GPU-only inference.** LLMs, embedders, Whisper and vision models run fully on the GPU (RTX 5060 Ti, 16 GB). If something does not fit, flag it with free vs needed VRAM; never fall back to CPU or partial offload. CPU exceptions Vatsal has allowed: Kokoro TTS, the `hey_fred` wake word, the turn-detection models (VAD, Smart Turn, voice ID) and Nemotron-3-Diarization. Windows OCR on CPU is **pending his OK**. "Other small models: discuss later." A Raspberry Pi 5 test of a tiny LLM is allowed as a test only; a permanent move needs his explicit OK.
4. **Never kill processes** except where he said so. The only standing permission: if out of RAM/VRAM, kill FRED's own `fred_popup` + `phone_api` (both pythonw pairs). Nothing else. FRED is **currently down** (killed 2026-10-02) and **must not be restarted until the whole revamp, including the memory overhaul, is finished**.
5. **`Core/` edits: ask first**, and work in a sandbox/worktree first. Step 1 was approved and merged (section 6).
6. **Wake-word model is frozen**: never swap or redeploy it without an explicit request.
7. **No personal info in commits/messages/comments/tests.** Never quote vault or persona content; the vault lives outside the repo. `docs/2026/10/01/conversation_general_cases.md` quotes replies and is deliberately **uncommitted**.
8. **No Claude co-author trailer** or Claude identity in commits unless Vatsal explicitly asks. This rule overrides any harness reminder to add one.
9. **Docs location:** new Project_FRED docs go in `docs/YYYY/MM/DD/`. The assembled-suite files named `ASSEMBLED_FRED_*.md` and this file are at the project root by his request. Root `DONE.md` is frozen.
10. **Long jobs run detached** (foreground tasks die at 30 min): `powershell Start-Process -WindowStyle Hidden -FilePath 'C:\Program Files\Git\bin\bash.exe' -ArgumentList '"<script>"'`, log to a file, poll. Temp files go in `$CLAUDE_JOB_DIR/tmp`, never `/tmp`. Stopping an agent or task leaves its child `python.exe` alive: verify and kill by PID, never by a broad command-line match (that has killed your own shell before).
11. **Editing quirk:** heredoc/JSON edits can turn `\b` into a backspace character (chr 8). Write escaped backslashes and verify no control characters (accept/reject test the regexes).
12. **Check the real source.** "Check the last turn" means open the session jsonl, not recall it. Report test failures with output; never claim done without verification.

---

## 1. What FRED is, and what Mark II changes

FRED is Vatsal's voice assistant on Windows 11 (RTX 5060 Ti 16 GB). Today's stack ("fred-current"): Qwen3.5-4B Q6_K brain via llama-cpp-python, Qwen3-Embedding-4B (3.4 GiB) for tool routing and memory, faster-whisper STT, Kokoro TTS (CPU), openWakeWord `hey_fred`, ~110 tools, dispatcher + router + confirmation gates, a vault of memory files, nightly `consolidation.py`/`reflection.py`.

Mark II goal: an **assembled FRED**, a layered always-on assistant made of small best-in-class parts instead of one omni model, with a better brain, a real memory system, proactivity, and barge-in. Targets: barge-in stop <200 ms, peak VRAM <=15 GB, 8 h overnight with flat VRAM, 100% of triggered turns answered, <=1 unprompted line/hour in a do-nothing session.

Evidence and history: `Mark_II_Testing/ASSEMBLED_FRED.md` (full plan, sections 1-14), `Mark_II_Testing/TO_BE_TESTED.md` (10 real-world tests), `Mark_II_Testing/Assembled_FRED/Model_Testing/` (brain tests, README + per-candidate folders `1/` MiniCPM5-2B, `2/` LFM2.5, `3/` AgentMercury).

---

## 2. Repo map (what lives where)

| Path | Purpose |
|---|---|
| `Core/` | the live FRED. venv at `Core/venv/Scripts/python.exe` (use this python for everything) |
| `Core/orchestrator/{orchestrator,dispatcher,intent,tool_router,agents,consolidation,reflection,proactive_checks}.py` | turn pipeline, routing, agents, nightly jobs |
| `Core/tools/` (`registry.py`, `assist_tools.py`...) | tool implementations |
| `Core/llm/` (`vision_server.py`, `think_cap.py` new) | model plumbing |
| `Core/audio/` (`tts_kokoro.py` `speak(cancel=)`, `fillers.py`, `phrase_cache.py`, `stt_whisper.py`, `media_state.py`) | audio |
| `Core/input/` (`wakeword.py`, `hotkey.py`, `voice_activity.py` built-unwired, `voice_id.py` built-unwired) | input |
| `Core/ui/pill_app.py` | UI, turn thread, `_on_tool_event`, `_speak_proactive` |
| `Core/memory/memory_manager.py`, FAISS memory index, vault `chunks.json` | memory (vault itself is outside the repo) |
| `Core/config/settings.py` | all settings (embedder path ~lines 1027-1058; new speed options) |
| `benchmarking/run.py` | benchmark runner; cases in `benchmarking/cases/*.jsonl`; results in `benchmarking/results/*.json` |
| `Mark_II_Testing/ASSEMBLED_FRED.md` | the architecture plan |
| `Mark_II_Testing/Assembled_FRED/Model_Testing/` | brain-candidate tests (own `models/` folders hold weights) |
| `Mark_II_Testing/Assembled_FRED/Embedder_Testing/` | embedder benchmark harness, results, `data/` (gitignored) |
| `Mark_II_Testing/fetch_model.py` | download pinned-commit weights with SHA check; entries `minicpm5`, `lfm25`, `mercury`, `qwen3emb06`, `qwen3rerank06`... |
| `ASSEMBLED_FRED_L0.md` ... `L5.md`, `ASSEMBLED_FRED_VRAM.md` | per-layer picks and VRAM budget (project root) |
| `FRED_Memory_Overhaul.md` (root, 2061 lines) | the memory system spec; roadmap = section 40 |
| `docs/2026/10/01/` | `roadmap.md`, `finetune_candidates.md`, `finetune_embedder_06b.md`, `embedder_4b_vs_06b.md`, `general2_benchmark_design.md` |
| model weights | LM Studio folder `C:\Users\Dhiraj Vatsal\.lmstudio\models` (FRED's own) and the candidate `models/` folders above |

---

## 3. Target architecture (assembled FRED)

```
 MIC 16k -> wakeword stream (80 ms) -+-> openWakeWord hey_fred (CPU, frozen)
                                     +-> VAD (webrtcvad now, Silero v5 later; CPU)
                                     +-> ring buffer -> Smart Turn v3.2 (CPU, on pause)
                                     +-> voice_id ECAPA (CPU, on segment)
                                     +-> Nemotron-3-Diarization (CPU, optional)
                                     +-> stt_stream: faster-whisper windows (GPU)
                                           partials/final + tone (emotion2vec+, GPU)
 SCREEN 1 fps -> change_detector (tile-max 0.990) -> focus crop
                  -> Windows OCR (CPU, pending OK)  +  MiniCPM-V 4.6 caption (GPU)
                  -> GLM-OCR on demand for dense docs (GPU, unloads)
                                           |
        event_log (JSONL ring, ~10 min, persisted to daily log)
            |                                         |
 [main process]                          [talker/judge llama-server process]
 L1 turn_controller (160 ms tick)        L4 proactive judge (MiniCPM5 1B/2B)
 LISTEN/EOT/BARGE_IN/BACKCHANNEL/IGNORE   every <=3 s on salient event
     | EOT (chord release / wake+speech / follow-up window)
     v
 pill_app turn thread -> orchestrator.process_stream  (L3 thinker)
   dispatcher -> classify -> router -> tools -> gates -> memory
     | ack/filler/tool caption immediately (phrase_cache)  (L2 talker)
     v
 Kokoro TTS (CPU)  speak(cancel=) <- BARGE_IN cancels in ~100-150 ms
 Nightly/weekly/monthly (L5): dynamic-load a bigger local model, unload live stack first
```

Layer contract (each layer has its own file with picks, rationale, open checks):

| Layer | Job | File | Status |
|---|---|---|---|
| L0 Senses | mic, wake word, VAD, diarization, screen change/crop, OCR, screen description, camera presence | `ASSEMBLED_FRED_L0.md` | picks final, checks open |
| L1 Interaction clock | 160 ms tick -> one state; end-of-turn, barge-in, identity gate, echo | `ASSEMBLED_FRED_L1.md` | picks final, checks open |
| L2 Talker + voice | instant acks, cover lines, short replies, TTS | `ASSEMBLED_FRED_L2.md` | draft; 1B vs 2B and VoxCPM2 open |
| L3 Thinker | FRED's orchestrator + brain + embedder + memory | this file, section 5 | brain decided by benchmark |
| L4 Proactivity | event log + judge -> speak? score, gates, cooldowns | `ASSEMBLED_FRED_L4.md` | design final, open items |
| L5 Consolidation | nightly/weekly/monthly local models, dynamic loading | `ASSEMBLED_FRED_L5.md` | draft |

Key integration rules:
- **End of turn:** chord release is the end. In open mic (wake+speech, or the 8 s follow-up window after FRED speaks): VAD silence >=200 ms AND Smart Turn p >=0.5, or silence >=1.2 s hard cap. Optional LiveKit text model adds a transcript condition only if its A/B shows a gain.
- **Barge-in** (while TTS plays): speech >=300 ms AND voice_id says it is Vatsal AND (headphones on OR level above the TTS echo floor) -> cancel Kokoro. <=2 words and Smart Turn says incomplete = BACKCHANNEL, resume. Else a new turn carrying `interrupted_at` + ghost text.
- **Speculative start:** when turn probability >0.5, the thinker prefills/generates on the partial transcript; commit at END_OF_TURN, discard if the transcript changes by more than one word or Smart Turn drops <0.3. **Tools never run speculatively.**
- **Confirmation** answers must come from an explicit user turn, never the judge, a backchannel or a speculative start.
- **Proactive speech** comes only from L4. Nothing else may speak unprompted.
- **Process split:** the talker/judge runs as its own llama-server process (pattern: `llm/vision_server.py`) so it never contends with the brain's model lock. FRED has crashed once on two concurrent decodes on one model (`screen_watcher.py` header).
- **Grounding rule:** any text the vision model quotes must also appear in the OCR output, so nothing unseen is ever spoken.
- New small modules (prototype under `Mark_II_Testing/Assembled/` before `Core/`): `turn_controller.py`, `stt_stream.py`, `event_log.py`, `change_detector.py`, `proactive_judge.py`, `tone.py`. Reuse unchanged: orchestrator, dispatcher, tool_router, agents, registry, memory_manager, tts_kokoro, fillers, phrase_cache, pill_app, hotkey, wakeword, proactive_checks, notifier, consolidation, reflection. Wire and lightly edit: voice_activity, voice_id, headphone_watch, media_state, screen_watcher/screen_context, stt_whisper (windowed mode), `_parse_text_tool_calls` (XML branch if a MiniCPM brain).

---

## 4. Components, models, quants, engines

### 4.1 Always-resident (VRAM table in section 4.4)

| Role | Pick | Quant / format | Engine / runtime | Runs on | Size | Notes |
|---|---|---|---|---|---|---|
| Brain (L3) | **[open] Mercury / tuned LFM2.5 / Qwen3.5-4B baseline / Qwen3.8-4B-Distill** | Q6_K today; **Q4_K_M marked to explore** | llama-cpp-python 0.3.31 (Jinja2ChatFormatter) | GPU | ~5.0 GB incl. KV [measured, baseline] | selection in section 6 step 3 |
| Talker + judge (L2/L4) | MiniCPM5-1B **and** MiniCPM5-2B, test both | Q4_K_M (1B 0.69 GB file; 2B ~1.56 GB) | own llama-server process | GPU | ~1.1 / ~2.1 GB [est/plan] | final MiniCPM5 weights, not the -SFT checkpoint |
| Embedder | Qwen3-Embedding-0.6B (**swap ON HOLD, waiting for Vatsal to resume**) | Q8_0; **Q6_K and Q4_K_M marked to test** | llama-cpp-python | GPU | 1.4 GiB [measured] vs 3.4 GiB for the 4B | tool-specific query instruction + centering for tools only; no instruction for memory |
| STT | faster-whisper large-v3-turbo (**only pick**; Confucius4-R2T2 on hold) | fp16 | faster-whisper, VAD-windowed re-decode ~1 s | GPU | ~1.6 GB [plan] | RTF 0.095 [measured]; ~0.15 s tail |
| Tone | emotion2vec+ base | - | funasr/onnx | GPU | ~0.4 GB [plan] | layer still unplaced; sarcasm likely missed |
| Screen describer | MiniCPM-V 4.6 | Q4_K_M 0.53 GB + f16 image encoder 1.11 GB | llama.cpp (support unverified) | GPU | ~1.9 GB | fallback: Qwen3.5-4B image path |
| TTS | Kokoro (existing) | - | onnxruntime CPU | CPU | 0 | VoxCPM2 candidate below |

### 4.2 CPU models (zero VRAM)

| Role | Pick | Notes |
|---|---|---|
| Wake word | openWakeWord `hey_fred` | frozen |
| VAD | webrtcvad (installed) -> Silero v5 ONNX; alt TEN VAD | |
| End of turn (audio) | Smart Turn v3.2 int8 ONNX, 8M params (BSD-2) | ~12 ms on modern CPU, ~60 ms on a cheap cloud instance [card] |
| End of turn (text, challenger) | LiveKit turn-detector (~135M, INT8 ONNX, text-only, custom licence) | LiveKit's benchmark shows 9.9% false cutoffs @300 ms vs Smart Turn v3.2 35.2% [search summary, vendor-run, multilingual, row identity unconfirmed]; adopt only if A/B on Vatsal's speech shows a gain |
| Speaker identity | `voice_id` ECAPA (existing, ~75 ms [measured]) | the identity gate |
| Speaker separation | Nemotron-3-Diarization (~99M, OpenMDW-1.1, 0.32 s buffer config, up to 8 anonymous speakers) via NeMo or NeMo-Speech.cpp | optional layer beside voice_id; adopt only if it cuts false barge-ins |
| OCR | Windows.Media.Ocr on the focus crop | CPU approval pending; ~50-150 ms [est] |
| Screen change + crop | tile-max difference 0.990 at 1 fps; native-res crop of the changed region | crop took small-text reads 0/9 -> 8/9 [measured, Venus work] |

### 4.3 On-demand and candidates

| Item | Role | Size | Status |
|---|---|---|---|
| GLM-OCR 0.9B (MIT; `ggml-org/GLM-OCR-GGUF`) | dense documents, tables, formulas; load then unload | ~1.5 GB while loaded [est] | llama.cpp run unverified; test on the 12 screenshots + dense pages |
| VoxCPM2 (`openbmb/VoxCPM2`, 2B, 48 kHz, voice design/cloning, Apache-2.0) | TTS candidate | **~8 GB** [card], RTF 0.30 on a 4090 | does not fit the budget; **test real VRAM and quants**; Kokoro stays until proven |
| Qualifire sentinel v2 (Qwen3-0.6B fine-tune; Q4_K_M 397 MB, Q8_0 639 MB) | prompt-injection filter | | **ON HOLD**; Prompt Guard 2 22M also on hold; compare on the injection suite when lifted |
| Confucius4-R2T2 (2.04B streaming ASR, vLLM backend) | STT alternative | ~4 GB bf16 | **ON HOLD** |
| Nemotron / Smart Turn / Silero | see 4.2 | | |
| Raspberry Pi 5 | host wake word + VAD (+ maybe Smart Turn) and own the mic over LAN/Tailscale; **test** MiniCPM5-1B Q4_K_M as a pick-a-phrase talker (few output tokens) | | RAM size of Vatsal's Pi unknown; mic ownership undecided |
| Gemma 4 E4B/E2B | dropped 2026-10-02 (~6 GB, no VRAM saving, weak tool use) | | do not revisit unless asked |

### 4.4 VRAM budget (16 GB card; target peak <=15 GB; see `ASSEMBLED_FRED_VRAM.md`)

| Item | GB |
|---|---:|
| Windows / desktop | 1.0 |
| Brain Qwen3.5-4B-class Q6_K + image encoder, 16k ctx | 5.6 (5.0 without image encoder) |
| Talker/judge MiniCPM5 2B Q4_K_M, 8k ctx | 2.1 (1B: ~1.1) |
| Embedder 0.6B Q8 | 1.4 |
| Whisper large-v3-turbo | 1.6 |
| MiniCPM-V 4.6 + image encoder | 1.9 |
| emotion2vec+ | 0.4 |
| **Total (2B talker / 1B talker)** | **14.0 / 13.0** |

Brain at Q4_K_M saves ~1 GB (total ~13.0 / 12.0). GLM-OCR on demand: peak 15.5 (2B) / 14.5 (1B); mitigate by unloading the describer meanwhile. 4B embedder = 16.0, does not fit. VoxCPM2 +8 = 22, does not fit. Other apps (DaVinci Resolve, the `:8000` uvicorn server, the nightly Recognize job) have held ~11-14 GB of the card; the budget is FRED alone. `LLM_IDLE_UNLOAD_SECONDS` interacts with an always-on loop; decide which models are exempt.

### 4.5 L5 consolidation models (loaded alone, at night; live stack unloaded first)

| Job | Model | Quant | VRAM | Thinking |
|---|---|---|---:|---|
| Nightly | Qwen3.8-9B-Distill (`empero-ai/Qwen3.8-9B-Distill-GGUF`) or a fine-tune | Q4_K_M / Q5_K_M (test) | ~6-8 GB [est] | toggle, test |
| Weekly | gpt-oss-20b (`ggml-org/gpt-oss-20b-GGUF` MXFP4 12.1 GB; or `unsloth/gpt-oss-20b-GGUF` Q4_K_M 11.6 GB) | | ~13-14 GB [est] | low/medium/high effort |
| Monthly, yearly, deep | same as weekly | | | high |

Qwen3.8 family: 2B, 4B, 9B, 27B (Ridge), 35B-A3B (the last two do not fit 16 GB). No 8B. A 10-14B dense Q4 (~8-9 GB) is a possible weekly alternative, unchosen. Local models only, no cloud (Vatsal 2026-10-02). Dynamic loading only in the nightly window; skip if Vatsal is present.

---

## 5. Brain candidates (L3) and how to run them

All run through `benchmarking/run.py` with `FRED_LLM_MODEL` (GGUF path) and `FRED_LLM_PATCH` (parser/template patch). Nothing in `Core/` is edited for a candidate test.

| Candidate | File / source | Notes |
|---|---|---|
| Baseline Qwen3.5-4B Q6_K | FRED's configured model (`FRED_LLM_MODEL` unset) | thinking on above 175 chars (`THINKING_LENGTH_THRESHOLD`) |
| AgentMercury-Qwen3.5-4B Q6_K | `fetch_model.py mercury` -> `Model_Testing/3/models/AgentMercury-Qwen3.5-4B.Q6_K.gguf` | same architecture/template as baseline; GRPO tool-use RL; thinking via `enable_thinking` switch |
| LFM2.5-2.6B Q8_0 | `fetch_model.py lfm25` -> `Model_Testing/2/models`; patch `Model_Testing/2/lfm_patch.py` | template always opens `<think>`; Pythonic tool calls (`[f(a='x')]`, markers stripped by llama-cpp); `LFM_NO_THINK=1` pre-closes think but **hurts badly** (memory 100% -> 0%); keep thinking ON; recommended sampling temp 0.1, top_k 50, rep. penalty 1.1; weak at knowledge-heavy tasks |
| MiniCPM5-2B Q8_0 | `fetch_model.py minicpm5`; patch `Model_Testing/1/minicpm5_patch.py` | XML-style tool calls; llama arch |
| **Qwen3.8-4B-Distill** (added 2026-10-02) | `empero-ai/Qwen3.8-4B-Distill-GGUF`: Q4_K_M 2.78 GB, Q5_K_M 3.16, Q6_K 3.56, Q8_0 4.61 | Qwen3.5-4B architecture (needs a recent llama.cpp with Gated DeltaNet), always opens `<think>`; card: MMLU-CoT 0.553 vs 0.354 base (suspect harness), gsm8k 0.785 vs 0.850; no tool numbers. Test Q6_K and Q4_K_M, with and without the 96-token thinking cap |
| Others noted, not scheduled | Parable-Granite-4.1-3B (coding-agent trained on Claude traces; low priority, check data terms), Llama-3.2-1B (dropped) | |

Scores to beat (177 scored cases, hand-corrected 2026-10-01): **Mercury 149, baseline 145, LFM2.5 143 (best per suite), MiniCPM5-2B 137.** Per-suite detail and latency are in `Model_Testing/README.md`. Baseline latency: median 4.9 s, p90 11.4 s on the main suite; LFM median 9.9 s, p90 39.7 s (always thinks).

Thinking-cap findings: generate <=N thinking tokens with stop `</think>`, then force `</think>` and generate the answer. Cap ~96 is best for Mercury (11/12 at 1.7 s on the 12-question think test); LFM modes are within noise except OFF (worse). Decision rule for LFM: must beat Mercury on general2 AND reach roughly Mercury's 4-5 s median, else Mercury (light tune) takes the slot.

---

## 6. The roadmap in steps (Vatsal's order)

### Step 1 - Pipeline fixes **DONE 2026-10-02** (merge `3caabdf`, pushed, 658 Core tests pass)
Fixed: dispatcher mis-routes ("time complexity", quoted-echo `say '...'`), calculator spoken maths (sqrt/cube root/squared/cubed/power, trailing "minus P percent"; math-01 = 847x23 minus 15% = 16558.85, Vatsal confirmed), false-completion guard skips wh-questions, follow-up tool carry-over (dispatcher turns now re-classify the previous user turn), time-01 checker computes days to 25 Dec from today (`days_until` field). Speed options added, **default OFF** in `Core/config/settings.py`: `THINKING_TOKEN_CAP` (suggest 96; `Core/llm/think_cap.py`, logits processor) and `DIRECT_TOOL_REPLY_MAX_CHARS` (suggest 160). These two are only tested on synthetic input, not with a model. Persona example skipped on purpose. Result on baseline: 144 -> 152 of 177 (conversation 15 -> 18/21; six general-suite flips are sampling noise); median/p90 latency unchanged (4.67/10.7 s -> 4.70/10.4 s). Accepted side effect: after a dispatcher turn, a non-social next message offers that tool. Remaining optional work: benchmark with the speed options on; measure prompt size and token counts per tool turn.

### Step 2 - memory overhaul and benchmark (no fine-tuning yet: build the house first, test the wiring later)

**2c. Memory overhaul first** (`FRED_Memory_Overhaul.md`, section 40; it must settle tool schemas before LFM training data is generated):
1. Foundation: SQLite schema, entity system, event system, state history, provenance, daily log table, basic memory manager.
2. FRED integration: conversation pipeline, existing FAISS, memory manager, project state, tool events; every meaningful interaction yields structured memory candidates.
3. Daily consolidation: nightly scheduler, event extraction, state updates, daily summary, contradiction queue (`consolidation_runs` table).
4. Temporal graph: entities, relationships, temporal validity, retrieval.
5. Weekly meta-agent (tool-using: search, compare, inspect, reason, write inference).
6. Personal model: patterns, trajectories, preference changes, confidence/evidence.
7. Monthly/yearly consolidation.
8. Deep self-analysis, separating evidence, inference, uncertainty, speculation.
The real-time brain only does capture/recall tool calls (overhaul section 32); consolidation runs on the L5 models. Touches `Core/memory/`, so ask first.

**2b. Perfect the benchmark:**
- Wire general2: `stage_general` in `benchmarking/run.py` is hard-wired to `general.jsonl` (~line 1037); add `GENERAL_CASES` env var or `--cases` support. `benchmarking/cases/general2.jsonl` (210 cases, 12 categories; design `docs/2026/10/01/general2_benchmark_design.md`; builder `gen_general2.py`; self-check `check_general2.py`) has not been run on any model.
- Hand-check the 81-case tools suite (`tools_end_to_end`) for checker mistakes (like the rounding bug found earlier).
- Mercury with the thinking cap at 96 as the control.
- Fix remaining checker issues; hand-check MiniCPM5-2B general results (uncorrected).


### Step 3 - Compare and choose the brain
Run **untuned** LFM, Mercury (cap 96), baseline and Qwen3.8-4B-Distill (Q6_K, Q4_K_M; with/without cap) on: tools (81), conversation (21), delegation, memory, general (+general2), median/p90 latency, VRAM. Gate for LFM in section 5. Also test **Q4_K_M vs Q6_K** of the winner. Provisional pick only; the tuned-LFM-vs-Mercury decision is re-made in Step 7.

### Step 4 - Judge the MiniCPM5 talker/judge
Test 1B and 2B Q4_K_M **in the full stack at the last stages** for total VRAM and latency; quality on cover lines, short replies, judge JSON (`{"speak","score","because","line"}`), and tool calls. Needs: 2B must pass >=77.8% tools and >=76.2% conversation to replace the 4B as responder (plan bar). Pi 5 pick-a-phrase test (section 4.3). The fine-tuned small talker (7b) is tested later, in Step 7.

### Step 5 - Build the assembled architecture (phased, smallest useful step first)
- **P0 (no FRED change):** brain/talker tests above; embedder quant test; Whisper-turbo VRAM.
- **P1:** barge-in (VAD + voice_id + `speak(cancel=)`) and the 8 s follow-up window with Smart Turn end-of-turn. Offline turn-clock phase on recordings first.
- **P2:** change detector + crop + OCR/caption -> event log fed to the answering prompt as data (targets real tests 1, 3, 10).
- **P3:** proactive judge in **shadow mode 2-3 days** (log only) -> calibrate on Vatsal's useful / not-now labels -> pill-only -> voice.
- **P4:** streaming partials + speculative start (+~28% LLM compute expected).
- **P5:** tone; one 2B as the only LLM if it passes; extras (Smart Turn fine-tune on Vatsal's turns, addressee head).
Prototype under `Mark_II_Testing/Assembled/` before touching `Core/`; add a `--candidate assembled` adapter to `benchmarking/run.py`.

### Step 6 - Polish for daily use
Then restart FRED (only now), run all suites and the 10 real tests, the 8 h soak, tune thresholds.

### Step 7 - Big fine-tunes (LAST, after the house is built and polished)
Needs the final tool set and memory tool schemas (Step 2c) and the assembled stack (Step 5), so the training format matches production. After each tune, re-run the Step 3 comparison and the full suites; swap models only on an explicit request from Vatsal.

**7a. Fine-tune LFM2.5-2.6B (Vatsal chose 1k-2k examples, not 500):**
- Method: bf16 LoRA r=16 alpha 16-32, LR 1e-4 to 2e-4, 1-3 epochs, dropout 0; targets `q_proj,k_proj,v_proj,out_proj,in_proj,w1,w2,w3`; Unsloth, TRL or LEAP Finetune. Windows is workable but WSL2 is lower risk. GGUF return path: merge to 16-bit -> `convert_hf_to_gguf.py` -> `llama-quantize` (Q6_K/Q8_0); verify with a round-trip prompt test (HF vs Q8_0 vs Q6_K).
- Format: LFM's **native Pythonic** tool calls, never OpenAI JSON; training text character-for-character identical to the production template; **thinking kept ON**; correct `{% generation %}` / `<think>` loss masks (TRL `assistant_only_loss` needs the tags; decide empty-think vs opened-tag target and mirror exactly in llama.cpp; mismatch is the likeliest failure).
- Data must include: single calls; **multi-tool turns** (same tool 2+ times or different tools, up to 4 calls per turn, `[f(a=..), g(b=..)]`); no-tool cases (25-35% of tool rows, main guard against over-calling); error/empty/timeout stubs; **"I don't know" cases that also state the tool fault** (failed / unreachable / returned nothing), never a bare "I don't know"; memory-grounded answers incl. 20-30% unanswerable; spoken brevity, no markdown in speech; English first. Tool schemas must match the **final** tool set incl. memory-vault tools.
- Hold out >=10-15% per category, never trained on. Regression suite = the benchmark below. Licence for synthetic data: check provider ToS (Anthropic/OpenAI outputs restrict training competing models); safer sources are own logs (`tool_call_log`), templates, open datasets.
- Open dataset to consider: `openbmb/UltraData-SFT-Agent-2609` (see 7b).

**7b. Small-talker fine-tune (Vatsal 2026-10-02):** fine-tune an even smaller talker (1B or below) with good tool calling on `openbmb/UltraData-SFT-Agent-2609` (483,661 trajectories, Apache-2.0; Tool-Use section 82,760). XML-style tool format needs conversion to FRED's; filter to English and FRED's tool shapes; add multi-tool and "I don't know + fault" cases; verify against the L2 test set and tools suite. Note: the MiniCPM5-2B card says its post-training used this set; the MiniCPM5-1B card does not mention it, so its tool use is unverified.

### Parallel item: embedder swap (ON HOLD, waiting for Vatsal)
Qwen3-Embedding-0.6B Q8_0 replaces the 4B (saves ~2 GiB): tool-specific query instruction + centering for routing, no instruction for memory, no reranker/routing head/fine-tune unless a bigger tools test (n>=300) shows a >3-point gap. Also test Q6_K/Q4_K_M. After the swap: delete the vault `chunks.json` by hand and rebuild the FAISS memory index (same dimension does not self-heal); fix the 4096-vs-512 comment (llama-cpp truncates inputs to `n_batch=512`); keep `SEMANTIC_FLOOR=0.65` under review; human spot-check 20-30 real vault questions. Evidence: `docs/2026/10/01/embedder_4b_vs_06b.md`, `Embedder_Testing/`. Touches `Core/`: ask first. Fine-tune plan if needed: `finetune_embedder_06b.md`.

---

## 7. Test catalogue

| Test | Where | Bar / use |
|---|---|---|
| Brain benchmark | `benchmarking/run.py --stages llm general` (add `conversation`, `delegation`, `memory` stages as in `run.py` main); `--cases id ...` for single cases; env `FRED_LLM_MODEL`, `FRED_LLM_PATCH`, `LFM_NO_THINK` | per-case isolation (carry-over reset, scratch-memory wipe); checks `reply_must_match` (regex re.I\|re.M), `reply_must_match_any/all` (lowercase substrings, not regex), `reply_must_not_match`, `max_words`, tool-call expectations; ~18-20 min for llm+general on the 4B |
| Suites | tools_end_to_end 81, conversation 21, delegation 8, memory 4, safety_injection 7, safety_links 1, general (10 categories, ~51) | baseline today: tools 77.8%, conversation 71-76%, delegation 87.5%, memory 75-100%, injection/links 100% |
| general2 | 210 cases, 12 categories | not yet wired or run |
| Think-mode tester | `Model_Testing/2/think_modes.py <lfm\|mercury> on off brief cap32 cap64 cap96`; 12 scored prompts | standalone |
| Embedder harness | `Embedder_Testing/bench_embedders.py`, `tools_large.py`; data in `Embedder_Testing/data/` | routing and memory retrieval |
| Routing / gates / stt / wakeword / vision / unsupported stages | `run.py --stages ...` | routing 83.0/80.2, gates 100%, vision 12/12 are the fred-current bars |
| 10 real-world tests | `Mark_II_Testing/TO_BE_TESTED.md` | error+reaction, small text, what changed, honesty, tone, TV noise, silence 10 min, follow-up, can't-do honesty, video |
| Live metrics | `analyse_live.py`-style script | answered rate 100%, follow-up window >=90%, <=1 unprompted line/hour, proactive useful-rate >=70%, barge-in <200 ms, release->first word p50 <=0.8 s, 0 responses to TV, peak VRAM <=15 GB, 8 h flat |
| Component checks | per layer files | L0: MiniCPM-V in llama.cpp, Windows OCR vs MiniCPM-V vs GLM-OCR on the 12 screenshots, Nemotron on CPU in real time + false-barge-in A/B (TV, 2 speakers, overlap), Silero vs webrtcvad. L1: Smart Turn on Vatsal's long pauses, threshold calibration (200 ms, 0.5, 1.2 s), LiveKit text A/B (+ read its licence), echo speakers vs headphones. L2: 1B vs 2B, phrase-cache coverage, barge-in cancel time, VoxCPM2 VRAM/quants/first audio vs Kokoro. L4: judge accuracy on labelled logs, call/media detection, event-log privacy/retention. L5: nightly quality (Qwen3.8-9B Q4/Q5/Q6 vs 4B, gpt-oss-20b effort levels), reload time of the live stack |

Regression rule: after any `Core/` change run Core tests (`cd Core; ./venv/Scripts/python.exe -m pytest tests -q`, 658 pass today) and the brain benchmark; the model's sampling makes single cases flaky, so re-run a flipped case 3x per side before calling a regression.

---

## 8. Engines and frameworks (versions and gotchas)

- **llama-cpp-python 0.3.31** with `Jinja2ChatFormatter`; llama-cpp truncates embedding inputs to `n_batch=512`; `n_ctx_seq` warnings are harmless. Qwen3.5/3.8 need a build with **Gated DeltaNet** (`qwen35` arch). LFM2.5 supported natively. The `{% generation %}` tags are stripped when rendering for inference.
- **Qwen3.5 / Mercury template:** `enable_thinking` switch (`<think>\n` open vs `<think>\n\n</think>\n\n` closed). **LFM2.5:** generation prompt `<|im_start|>assistant\n<think>`. `lfm_patch.py` pre-closes think when `LFM_NO_THINK` is set (conditional so FRED's embedding-model template does not crash it).
- **Fine-tuning:** Unsloth (transformers v5; no QLoRA for Qwen3.5), TRL (assistant_only_loss needs generation tags; Qwen3.5 resolves via the VLM path and can block it), ms-swift 4.3 (needs flash-linear-attention >=0.4.2, pin the version; FLA 0.5.0 outputs "!!!!"), LEAP Finetune for LFM. LoRA target-module trap on Qwen3.5: `all` may skip the linear-attention mixer; verify with `print(model)`. Old `convert_lora_to_gguf.py` tags `qwen3` and fails on `qwen35`; merge then convert. Hardware: Qwen3.5-4B bf16 LoRA ~10 GB; LFM2.5-2.6B ~6-9 GB; full FT does not fit 16 GB.
- **MiniCPM5:** plain `LlamaForCausalLM` architecture; XML-style tool calls; SGLang is the recommended tool-call backend (parser `minicpm5`); llama-server works for plain generation. Use the final RL+OPD weights.
- **Audio:** faster-whisper, openWakeWord, webrtcvad/Silero ONNX, Smart Turn ONNX (ONNX Runtime CPU), ECAPA voice_id, Kokoro (onnxruntime CPU), Nemotron via NeMo or NVIDIA NeMo-Speech.cpp (Windows support unchecked).
- **Vision/OCR:** Windows.Media.Ocr, GLM-OCR via llama.cpp/Ollama/vLLM, MiniCPM-V 4.6 via llama.cpp (`minicpmv4_6` support unverified).
- **Downloads:** `Mark_II_Testing/fetch_model.py <name>` (pinned commit, SHA check, resumable, `HF_HUB_DISABLE_XET=1`); gated repos (Llama, Prompt Guard, Qualifire) need access first.

---

## 9. Open decisions (need Vatsal)

1. Resume the embedder swap (and which quant after the test).
2. Windows OCR on CPU: approve or use a GPU model.
3. VoxCPM2 as TTS only if a quantised build fits well under ~1.5 GB; else Kokoro.
4. L5 weekly alternative to gpt-oss-20b, if it underperforms.
5. Which layer owns STT and tone (currently unplaced in L0-L5; STT = faster-whisper, tone = emotion2vec+).
6. Pi 5: RAM model, who owns the mic, permanent GPU-only exemption for a Pi talker.
7. Event-log retention and exclusions before L4 runs on real use.
8. Whether the brain's image encoder is dropped once MiniCPM-V does the screen (-0.6 GB).

## 10. Risks (carry forward)

Co-resident VRAM depends on the 0.6B embedder and Q4 brain (both untested for accuracy); MiniCPM5 in FRED's llama-cpp build (template, XML tools, Blackwell kernels) unverified; MiniCPM-V 4.6 and GLM-OCR llama.cpp support unverified; echo/barge-in on speakers without AEC; open-mic addressedness without a trained head (v1 sidesteps with chord, wake word, 8 s follow-up window); Whisper on short fragments; GPU contention with the Recognize job and DaVinci; judge usefulness unproven (shadow mode first); tone is not sarcasm; LFM template/think alignment during fine-tuning; classifier non-determinism makes single benchmark cases flaky; Qwen3.8-4B-Distill numbers are self-reported and its teacher model is unverifiable.

## 11. Immediate next actions for a fresh agent

1. Read sections 0 and 6; confirm FRED is still down and the repo is clean (`git status`; only `conversation_general_cases.md` untracked).
2. Ask Vatsal before starting **memory overhaul Phase 1** (touches `Core/memory/`); design the schema first in a sandbox and publish the final memory tool schemas.
3. In parallel (no `Core/` edits): wire general2 into `run.py`'s sandbox copy, hand-check the 81 tools cases, run Mercury cap-96 and Qwen3.8-4B-Distill Q6_K/Q4_K_M through the benchmark (VRAM must be free; check `nvidia-smi`; detached jobs).
4. LFM and small-talker fine-tunes are Step 7 (last); do not start them before the assembled build and polish.
