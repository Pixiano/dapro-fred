# Candidate 2 research: giving MiniCPM5-2B eyes and ears, Realtime-Venus style

## Executive summary (plain words)

MiniCPM5-2B is a plain text model (standard Llama-style architecture, 2.5B params, 42 layers, GQA 16 query / 2 key-value heads, hidden size 2048, 131k context, Apache-2.0). It has no vision or audio input at all today. To make it watch a screen, watch a face camera, and listen to a microphone the way Realtime-Venus's 9B model does, you'd need to bolt on two encoders (a vision encoder like SigLIP2 and an audio encoder like Whisper), each with a small adapter network ("resampler"/"projector") that translates their output into the same 2048-wide token space MiniCPM5-2B's transformer already speaks — and then retrain (at least partially) so the model actually learns to use those tokens, decide when to talk, and hand hard tasks to a sub-agent.

None of the existing OpenBMB parts plug straight in: MiniCPM-o 4.5 and MiniCPM-V 4.5's language model runs at hidden size 4096, and even the smaller MiniCPM-V 4.6 (Qwen3.5-0.8B backbone) runs at hidden size 1024 — neither matches MiniCPM5-2B's 2048, so the adapters that map encoder output into the LLM's hidden space cannot be reused as-is; only the frozen encoders themselves (SigLIP2, Whisper-medium) transplant cleanly, and every adapter/projector must be retrained. Realtime-Venus's own training data and code for building the streaming duplex behavior are not public — only inference code and finished weights are — so producing FRED's own version means reproducing Venus's 3-stage synthetic-data pipeline from scratch with local tools (a local LLM for scripting, Kokoro for speech, screen/webcam capture for video). This is a multi-month research-engineering project, not a weekend integration, and the two-camera-stream (screen + face) case is untested in any paper found. A faster, lower-risk path exists (Qwen2.5-Omni-3B or MiniCPM-o 2.6, already-multimodal, plus lighter duplex fine-tuning) — see "Recommended path."

---

## 1. Reusable architecture pieces

**Vision encoder.** MiniCPM-o 4.5 and MiniCPM-V 4.5/4.6 all use a SigLIP2 ViT, reported as ~0.4B parameters, 27 transformer layers, hidden width 1152, 16 attention heads (head dim 72), 14×14 patches. This is a frozen, general-purpose image encoder (not modality-adapted to any particular LLM), so it is a strong transplant candidate as-is — you would download the standalone `google/siglip2-*` checkpoint (OpenBMB does not appear to ship it as a separate repo; it's embedded in the MiniCPM-V/-o weights) or extract it from the MiniCPM-V-4.6 weights. [Unverified: exact HF repo id / variant name for the standalone SigLIP2 checkpoint OpenBMB used — the searches only confirmed the architecture numbers, not a specific "siglip2-so400m-..." tag.]

**Resampler.** Each image "slice" is encoded to 1024 tokens by the ViT, then compressed to 64 tokens via a cross-attention resampler (a fixed, small number of learned query vectors attend over the 1024 patch tokens) — a 16× compression, notably higher than the common 4× ratio other MLLMs use. The resampler's output dimension is set to match the LLM's hidden size, which is exactly the part that cannot transplant: MiniCPM-o 4.5 / MiniCPM-V 4.5 use Qwen3-8B (hidden size 4096); MiniCPM-V 4.6 uses Qwen3.5-0.8B (hidden size 1024); MiniCPM5-2B's own hidden size is 2048. All three differ, so the resampler's output projection must be retrained regardless of which donor model you start from — there is no size match to exploit. Only the resampler's query vectors and cross-attention weights up to the projection layer might warm-start faster than random init, and even that is unverified in any source found.

**Audio encoder + projector.** Whisper-medium (~0.3B params, 24 layers, hidden size 1024, 16 heads) runs as a frozen streaming audio encoder producing 50 feature-tokens/second, then a 2-layer MLP projector compresses that to 10 tokens/second (5x reduction) and reprojects into the LLM's hidden size. Same story as vision: Whisper-medium itself transplants cleanly (it's an off-the-shelf OpenAI checkpoint, not custom-trained), but the 2-layer MLP projector must be retrained to output 2048-wide vectors for MiniCPM5-2B instead of 4096 (MiniCPM-o 4.5) or 1024 (MiniCPM-V 4.6).

**Serialization / Omni-Flow.** Both MiniCPM-o 4.5 and Realtime-Venus operate on fixed time chunks (1.0 second found to be optimal experimentally, with 0.1s/0.2s also tested). Within a chunk, projected visual tokens for that window and ~10 audio tokens/second are concatenated in temporal order and fed to the LLM alongside any output tokens; MiniCPM-o 4.5 calls this "Time-Aligned Interleaving (TAIL)," which adaptively controls the ratio of text-to-speech tokens emitted per chunk. Realtime-Venus's public description gives an explicit example of the per-chunk token layout:
```
chunk 6:  <unit> V A -> <|speak|> T <|chunk_eos|><|turn_eos|> </unit>
chunk 7:  <unit> V A -> <|speak|> <delegate> T </delegate> <|chunk_eos|> </unit>
chunk 14: <unit> V A <backend> T </backend> -> <|speak|> T </unit>
```
where V = visual tokens for that second, A = audio tokens for that second, T = text. Special tokens: `<|listen|>` (keep perceiving, say nothing), `<|speak|>` (start generating out loud), `<|turn_eos|>` (end of the assistant's speaking turn), and the delegation pair `<delegate>...</delegate>` / `<backend>...</backend>` (see section 3). MiniCPM-o 4.5 itself only has a `[listen]`-style silent-frame token in the base recipe; the `<delegate>`/`<backend>` pair is Venus's own addition on top.

**Bottom line on reuse:** transplant the frozen encoders (SigLIP2, Whisper-medium) as-is; treat every adapter/projector/resampler as something you must train from scratch because MiniCPM5-2B's hidden size (2048) doesn't match any existing OpenBMB omni model's LLM hidden size (4096 for the 4.5/8B line, 1024 for the 4.6/0.8B line). This also means MiniCPM5-2B's own weights (all 42 layers) are what you're aligning against — nothing in the LLM itself needs to change size, but its every layer will see a new kind of input token it wasn't pretrained on, which is the real difficulty (see risks, section 6).

---

## 2. Training plan for "MiniCPM5-2B-Omni (text output)"

Text-only output (feeding FRED's existing Kokoro TTS) removes the need to train a speech decoder or CosyVoice2/S3-style speech-token head — one whole stage of MiniCPM-o's recipe disappears. What's left maps onto MiniCPM-o 4.5's first three stages, adapted:

**Stage 0 — Component acquisition (no training).** Pull SigLIP2 vision encoder weights and Whisper-medium weights, both frozen for now. Build two new adapters from scratch: a cross-attention resampler (1024 vision tokens/slice -> 64 tokens, output dim 2048) and a 2-layer MLP audio projector (50 Whisper features/s -> 10 tokens/s, output dim 2048). Both adapters are randomly initialized.

**Stage 1 — Modality alignment (MiniCPM-o 4.5's Stage 1 equivalent).**
- Frozen: MiniCPM5-2B (all 42 layers), SigLIP2, Whisper-medium.
- Trained: vision resampler + audio projector only.
- Goal: get the two adapters to produce tokens the frozen LLM can already interpret reasonably (image captioning, ASR-as-text) without disturbing MiniCPM5-2B's existing tool-use/reasoning behavior.
- Data: open image-caption/instruction data (LLaVA-mix-665k style: ~665k samples combining LLaVA-Instruct, VQAv2, GQA, OK-VQA, OCRVQA, TextCaps, plus ~40k pure-text ShareGPT turns to keep some text signal in the mix) for vision; open ASR/audio-QA data for audio (LibriSpeech ~1000h read English is the standard small starting corpus; add a Chinese/English audio-QA set if bilingual support matters for FRED — none confirmed by name in this research, treat as unverified which specific one OpenBMB used).
- Method: full fine-tune of the (small) adapters only — this is cheap enough that LoRA is unnecessary; LoRA only matters once you're touching the LLM's own weights.

**Stage 2 — Joint pretraining (all parameters unfrozen).**
- Frozen: nothing.
- Trained: everything (encoders can stay frozen or be unfrozen with a much lower learning rate — MiniCPM-o 4.5's paper says "all parameters unfrozen" but doesn't specify differential learning rates; treat that detail as unverified).
- Data: a fixed-ratio mixture of vision-language, speech, and — this is the new, harder-to-source part — proactive/duplex samples with time-aligned text+visual+audio signal, i.e. examples where the "correct" output is silence (`<|listen|>`) as often as speech. MiniCPM-o 4.5's own mixture composition isn't disclosed in detail; Realtime-Venus's public breakdown (2.8M samples total, ~56% offline understanding / ~37% proactive duplex / ~6% delegation) is the closest disclosed analog and is a reasonable target ratio to imitate at whatever smaller scale FRED can afford.
- Method: full fine-tune, but this is the expensive stage — realistically the one where compute-constrained builders substitute a partial/LoRA fine-tune and accept a capability hit. Given FRED's hardware constraints (explicitly out of scope for this doc per Vatsal's instructions, but worth flagging that this stage is the one most people shrink), doing Stage 2 as LoRA-on-everything rather than full fine-tune is a defensible corner to cut, at a cost of weaker modality fusion.

**Stage 3 — Joint SFT (text-out only).**
- Frozen: encoders (recommended).
- Trained: adapters + LLM, LoRA or full depending on budget.
- Data: instruction-style multimodal QA over image/video + speech, video-QA sets (Video-ChatGPT-style, ActivityNet-QA-style — exact open sets OpenBMB used are unverified), and FRED-specific duplex/delegation examples built per section 3. This is also where the `<delegate>`/`<backend>` control-token behavior gets taught explicitly with labeled examples.
- Skip: MiniCPM-o 4.5's Stage 4 RL (GRPO + RLAIF-V) is for speech quality and hallucination reduction tied to the speech decoder and vision generation quality bar; with text-only output this stage is lower priority and can be deferred or dropped entirely — a reasonable place to stop for a first working version.

**Sample-count sanity check.** Realtime-Venus trained its duplex/delegation behavior on ~2.8M synthesized samples (breakdown given in section 3). MiniCPM-o 4.5's own joint pretraining scale is not disclosed in what was retrieved here — treat any specific number as unverified. A first FRED version should expect to need at least tens of thousands of duplex/delegation examples (not thousands) to get the `<|listen|>` vs `<|speak|>` decision to generalize, based on Venus's category sizes (e.g. their smallest categories — audio delegation, video delegation — are still 90k-95k samples each).

---

## 3. Building Venus-style streaming/duplex data locally

Venus's training data and data-generation code are not released — only weights + inference code (confirmed via the official GitHub repo and Hugging Face pages). Their described pipeline has 3 stages, which are reproducible with local tools at smaller scale:

1. **Scenario & interaction planning.** A local LLM (e.g. MiniCPM5-2B itself, or a bigger local model if available) writes structured scenario seeds: setting, participants, goals, "who's allowed to interrupt," acoustic environment, and a rough turn-by-turn plan (who speaks, what they intend, when intent changes). This is pure text generation — cheap, no GPU-heavy step.
2. **Paralinguistic/acoustic realization.** Turn the planned turns into actual speech. FRED already has Kokoro TTS locally, which can render each planned utterance; to match Venus's described realism (hesitations, filled pauses, self-corrections, overlapping secondary speakers, background audio) you'd need to either script Kokoro with SSML-like disfluency injection (repeat a word, insert "uh", clip a sentence) or accept a cleaner-than-Venus signal as a known limitation. Screen-capture and face-camera video are the visual analog: script real screen recordings (e.g. record yourself using an app while narrating) or synthesize simple screen-share sequences (browser automation + OBS-style capture) paired with a scripted "face reaction" video loop.
3. **Temporal alignment & materialization.** Lay every event (a spoken line, a screen change, a camera reaction, a system/tool call) onto one monotonic clock, in 1-second buckets, then serialize each bucket into the `<unit> V A -> ... </unit>` format shown in section 1. For FRED specifically, each chunk would carry two V streams (screen-frame tokens + face-camera-frame tokens, both through the same SigLIP2+resampler pipeline, concatenated) plus one A stream (system mic audio through Whisper+projector). This two-video-stream layout is not described or tested in either the MiniCPM-o 4.5 or Realtime-Venus papers found — both only handle a single video stream — so the concatenation order, token budget split between the two video sources, and whether the model can actually tell "screen" and "face" apart without an explicit tag is an open design question you'd have to test empirically (a stream-id token before each video's tokens, analogous to the `<|listen|>`/`<|speak|>` tokens, is the obvious mitigation but is not attested in any source).

**Mapping FRED's tool calls onto `<delegate>`/`<backend>`.** Venus's `<delegate>...</delegate>` marks a natural-language task request sent to a background agent (hidden from the user-facing transcript), and `<backend>...</backend>` marks that background agent's result being spliced back in once ready, potentially many chunks later ("chunk 7" delegates, "chunk 14" receives the backend answer in the example above). FRED's existing tool-call scheme (whatever function/XML tool-call format MiniCPM5-2B already emits for tool use, since MiniCPM5-2B natively emits XML-style tool calls) maps naturally: the existing tool-call text becomes the delegate span's payload, and the tool's return value becomes the backend span's payload, with the difference that Venus's version is explicitly asynchronous — the model keeps listening/speaking on the foreground track while the delegated call runs, rather than blocking. Training data for this would be built by taking FRED's existing tool-call transcripts and re-timing them onto the chunk clock, inserting several chunks of ordinary foreground activity between the delegate call and the backend result to teach the model not to block.

---

## 4. Realtime inference

**Chunk scheduling.** Both papers process fixed-size time windows (1 second) as the unit of work: at each chunk boundary the model is invoked once with that chunk's new visual+audio tokens appended to a growing KV cache, and produces either nothing (`<|listen|>`) or new text/speech tokens (`<|speak|>`). Realtime-Venus additionally schedules how many text tokens to emit per chunk based on how far behind/ahead the (in their case, spoken) output is from real time — a formula that picks the token count minimizing drift between playback position and current wall-clock chunk boundary. For a text-only FRED variant without a speech decoder to keep in sync, this exact scheduler isn't needed, but the core idea — decide once per second, don't regenerate from scratch — is essential: this is prefill-only most seconds (append new chunk's tokens, no decode) and prefill+decode on seconds where the model actually speaks.

**KV-cache growth.** Because every chunk appends tokens permanently to the sequence, a naive implementation grows the KV cache linearly with session length — at 1 fps + 10 audio tok/s + resampled 64 vision tokens per frame, a 10-minute session is already thousands of tokens before any speech is generated, and FRED's two-video-stream case roughly doubles the visual token rate. This is exactly why Realtime-Venus's long-video memory module (motion-compensated frame gating: drop near-duplicate frames using block-level motion matching, keep frames only on real visual change, with a cap on how many frames can be dropped consecutively; then MaxSim-based token retrieval to pull back relevant historical frames rather than keeping everything in the live KV cache) exists — it's a training-free mechanism (no fine-tuning needed) that FRED could adopt directly once it has a working omni model, independent of whether FRED trains its own version.

**Silence is cheap, mostly.** `<|listen|>` avoids the cost of decoding an actual response, but the model still has to process (prefill) that second's visual+audio tokens to decide silence is the right call — so per-chunk cost doesn't fully disappear during silence, it drops from "prefill + decode a response" to "prefill only," which for a 2B model at 1-second granularity should be well within a single second's compute budget on the sort of hardware Vatsal already runs FRED on. [Deliberately not sizing this further per the task's request to skip GPU/VRAM analysis.]

**Interruption handling.** Neither source gives a fully worked interruption mechanism beyond the general duplex framing (the model is always processing input concurrently with any output it's producing, so a new strong audio/visual signal during `<|speak|>` is just more input tokens in the next chunk that the model can react to by emitting `<|turn_eos|>` early or by continuing) — treat detailed interruption semantics as unverified beyond "the architecture makes it structurally possible" for both papers.

**Serving stacks.** 
- **transformers**: works for any custom architecture including a from-scratch modality-fusion model, is the natural first target for training and small-scale testing, but is the slowest for production serving.
- **vLLM**: MiniCPM-o 4.5's own benchmarks on an RTX 4090 report 212.3 tokens/s (INT4) / 154.3 tokens/s (BF16) throughput and 0.58s first-token latency with a 64-frame visual input, using 11GB (INT4) or 19GB (BF16) — vLLM already has a registered code path for MiniCPM-o's specific architecture, but a custom MiniCPM5-2B-Omni architecture would need its own vLLM model-registration file (a well-trodden but nontrivial engineering task, standard for any new architecture in vLLM).
- **SGLang**: explicitly the OpenBMB-recommended backend for MiniCPM5-2B's tool-calling (has a built-in parser for its XML-style tool calls); whether SGLang has multimodal/omni support comparable to vLLM's for MiniCPM-o specifically was not confirmed in this research — treat as unverified.
- **llama.cpp / mtmd**: llama.cpp's `mtmd` library already supports MiniCPM-o 2.6 and MiniCPM-o 4.0 as pre-quantized models handling both audio and image input (video and TTS are listed as future work, not yet in mainline mtmd as of the sources found). Separately, community forks named `llama.cpp-omni` (e.g. `tc-mb/llama.cpp-omni`, `xkeyC/llama.cpp-omni`) exist specifically for MiniCPM-o 4.5's omni pipeline and report a real-time factor (RTF) of 0.21 on an RTX 4090 at INT4 (RTF < 1.0 means faster than real time), using about 11GB at INT4 / 19GB at FP16 — but a custom MiniCPM5-2B-Omni model would need new integration work in whichever fork, since mainline mtmd doesn't yet support the full omni/duplex control-token behavior, only turn-based image+audio understanding.

**Latency numbers found for comparable small omni models:** MiniCPM-o 4.5's 0.58s first-token latency (RTX 4090, 64-frame input, vLLM) and llama.cpp-omni's RTF 0.21 are the only concrete numbers surfaced in this research; no equivalent numbers were found for Qwen2.5-Omni-3B or MiniCPM-o 2.6 beyond MiniCPM-o 2.6's claim of producing 75% fewer image tokens than "most models" for a 1.8-megapixel image (a token-count efficiency claim, not a latency number).

---

## 5. Faster alternatives

- **Qwen2.5-Omni-3B**: already a full omni model (text, image, audio, video in; text + streaming speech out) at a similar parameter count to MiniCPM5-2B, using a "Thinker-Talker" split — the Thinker is a decoder LLM with vision + AuT (a lightweight 12.5Hz audio encoder) already fused via a TMRoPE (time-aligned multimodal rotary position embedding) scheme, and the Talker is a separate dual-track decoder for streaming speech generation. Since FRED wants text-out only (Kokoro handles speech), you could use the Thinker half alone and skip the Talker entirely — this is the closest thing to "already done" among alternatives, at the cost of not being MiniCPM5-2B (different tokenizer, different tool-calling format, so FRED's existing prompt/tool scaffolding built around MiniCPM5-2B's XML tool-call format would need porting).
- **MiniCPM-o 2.6**: OpenBMB's earlier (pre-4.5) omni model, already supports vision+audio+text in and streaming output, has known real-time streaming benchmarks (StreamingBench) and llama.cpp `mtmd` support today — the most "just works" option, but it's built on an older, larger backbone than MiniCPM5-2B and doesn't have Venus's proactive-duplex or delegation training baked in (it's turn-based multimodal chat, not full-duplex).
- **Start from Realtime-Venus-Omni (9B) and distill down**: take the already-duplex-trained 9B Venus model as a teacher and distill its behavior (including the `<|listen|>`/`<|speak|>`/delegate decisions) into a smaller student initialized from MiniCPM5-2B plus fresh encoders. This reuses Venus's already-solved "when to talk" behavior via distillation rather than re-deriving it from scratch, at the cost of needing to generate a large volume of teacher-labeled data (running the 9B model over many synthetic sessions) and still leaves the resampler/projector-retraining problem from section 1 unsolved (Venus's own hidden size, 4096, doesn't match MiniCPM5-2B's 2048 either).

**Honest trade-off vs building on MiniCPM5-2B directly:** all three alternatives get to "watches and listens and sometimes stays quiet" faster because that behavior is either already trained in (Qwen2.5-Omni, MiniCPM-o 2.6, partially) or distillable from a teacher that already has it (Venus-9B). Building on bare MiniCPM5-2B means training every piece — modality alignment, joint pretraining, and duplex/delegation behavior — from zero, using FRED's own reproduced (and necessarily smaller and lower-fidelity) version of Venus's data pipeline. The upside of staying on MiniCPM5-2B is keeping its existing tool-use format, its 131k context window, and (if it matters to Vatsal) staying inside a model actually tied to FRED's existing text-only pipeline rather than porting to a different base model's conventions.

---

## 6. Biggest risks / unknowns

- **Catastrophic forgetting of tool-use.** MiniCPM5-2B's headline strengths are tool-calling, coding, and long-context reasoning. Every training stage in section 2 that unfreezes the LLM (Stage 2 onward) risks degrading that behavior unless the training mix explicitly keeps enough pure-text/tool-use examples in the loop (MiniCPM-o's own recipe does this — Stage 1's pure ShareGPT text data, mixed ratios in Stage 2 — but the exact ratios needed to protect a 2B model's tool-use specifically are unverified and would need empirical tuning against FRED's own tool-use test suite).
- **Data scale.** Venus's disclosed 2.8M-sample duplex/delegation dataset is Ant Group + Tsinghua-scale effort; FRED reproducing even a 10-50x smaller version locally (tens of thousands of samples) via a local-LLM-plus-Kokoro pipeline is itself a significant data-engineering project, and it's unverified whether that much smaller scale is sufficient to learn robust `<|listen|>` vs `<|speak|>` and delegation decisions, or whether the behavior only emerges past some data-volume threshold.
- **Two simultaneous video streams is untested.** Neither MiniCPM-o 4.5 nor Realtime-Venus (both single-video-stream designs, one visual token stream per chunk) address FRED's screen-plus-face-camera dual-video case. Token budget per chunk, stream-identification tokens, and whether one frozen SigLIP2 encoder can serve both streams without confusion are all open questions with no literature precedent found.
- **Resampler/projector cold-start.** Every adapter must be trained from random initialization (section 1) because no existing OpenBMB omni model shares MiniCPM5-2B's 2048 hidden size — this removes the "warm start from an existing adapter" shortcut that would otherwise cut Stage 1 training cost substantially.
- **Released-code gap.** Realtime-Venus's data-synthesis pipeline and full training code are not public (confirmed: GitHub repo has inference code and weights only) — this entire project's data stage (section 3) is FRED's own reconstruction of a described-but-unreleased method, with real risk that the reconstruction misses details that mattered in the original (e.g. exact disfluency injection method, exact scenario-planning prompt design) and underperforms accordingly.
- **RTF/latency numbers don't transfer directly.** The llama.cpp-omni RTF 0.21 and vLLM 0.58s first-token numbers are both measured on MiniCPM-o 4.5 (9B, Qwen3-8B backbone), not on a hypothetical MiniCPM5-2B-Omni; a smaller 2B backbone should be faster per-chunk in principle, but no source measures this directly, so any latency expectation for a MiniCPM5-2B-based version is an extrapolation, not a confirmed number.

---

## Recommended path

Given the effort implied by sections 1-3 (from-scratch adapter training, from-scratch duplex data synthesis, unverified two-video-stream behavior) versus the alternatives in section 5, the lower-risk route to FRED's actual goal — a realtime assistant that watches, listens, decides when to speak, and delegates — is:

1. Start from **MiniCPM-o 2.6** or **Qwen2.5-Omni-3B** (both already multimodal, already have some real-time streaming story, already have inference tooling: MiniCPM-o 2.6 has llama.cpp `mtmd` support today) rather than bare MiniCPM5-2B, to skip the from-scratch modality-alignment stage entirely.
2. Layer FRED's own **lighter-weight duplex/delegation fine-tune** on top (LoRA, not full fine-tune, given it's Stage 3-equivalent behavior teaching, not modality alignment), using a FRED-scale (thousands-to-low-tens-of-thousands, not millions) reproduction of Venus's 3-stage data pipeline from section 3 — local LLM for scenario scripts, Kokoro for speech (accepting cleaner audio than Venus's disfluency-rich corpus as a known simplification), scripted screen+face captures.
3. Treat the **MiniCPM5-2B-Omni-from-scratch** path (this document's main subject) as a longer-horizon research bet to revisit once (2) proves the duplex/delegation behavior is learnable at FRED's data scale at all — validating the hardest unknown (data scale, section 6) on an already-multimodal model before spending the extra effort of also training vision/audio alignment from zero.
4. Whichever base model is chosen, adopt Realtime-Venus's **long-video memory module** (motion-compensated frame gating + MaxSim retrieval) as-is — it's training-free and directly solves the KV-cache-growth problem from section 4 regardless of which LLM sits underneath.
5. Explicitly punt on the **two-video-stream (screen + face)** design until step 2's single-stream version works — introduce a second stream only after confirming the model can reliably do proactive turn-taking and delegation on one video source, since no paper found validates the two-stream case and it's the single biggest unverified assumption in Candidate 2's premise.

---

## Addendum (main session, 2026-09-27)

**Correction to section 4, interruptions.** The Venus paper does describe
interruption training concretely (read directly from arXiv 2609.13814): the
model is trained on sessions that contain interruptions, and it jointly
supervises the state switch and what comes next, with three interrupt
behaviours: *stop/reject* (close the turn, drop the unspoken remainder),
*repair/update* (take in the new information, regenerate the stale
continuation) and *redirect/follow-up* (suspend the current plan, move to
the new request). Acoustically similar overlaps are trained to lead to
different continuations. So interruption is a data problem (include
interruption sessions of all three kinds), not an architecture problem.

**If Candidate 2 is the bet anyway (Vatsal's stated call).** The
recommendation above says "prove it on an existing omni model first".
The Candidate-2-first version that still manages the risk:

1. **Milestone 1: audio only.** Whisper-medium + a new projector on
   MiniCPM5-2B, LLM frozen (Stage 1). Test: does it transcribe and answer
   spoken questions? Cheapest possible proof that the adapter approach
   works on this backbone, and FRED's STT benchmark (`benchmarking/`)
   already measures it.
2. **Milestone 2: one video stream.** SigLIP2 + resampler, same recipe.
   Test on the benchmark's 12 screenshot cases.
3. **Milestone 3: FRED tool-use check.** Re-run the routing/tool suites
   after every stage that touches the LLM; stop and rebalance the data mix
   the moment tool-use drops. This is the forgetting guard from section 6.
4. **Milestone 4: duplex + delegation** on a FRED-scale synthetic set
   (section 3), single video stream.
5. **Milestone 5: second video stream** with explicit stream-id tokens.

Each milestone is independently useful and independently measurable, so
the bet never has to be all-or-nothing.

---

## Sources

- [openbmb/MiniCPM5-2B · Hugging Face](https://huggingface.co/openbmb/MiniCPM5-2B)
- [openbmb/MiniCPM5-2B config.json](https://huggingface.co/openbmb/MiniCPM5-2B/resolve/main/config.json)
- [openbmb/MiniCPM5-2B-GGUF · Hugging Face](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF)
- [openbmb/MiniCPM5-2B-MLX · Hugging Face](https://huggingface.co/openbmb/MiniCPM5-2B-MLX)
- [openbmb/MiniCPM5-2B | vLLM Recipes](https://recipes.vllm.ai/openbmb/MiniCPM5-2B)
- [GitHub - openbmb/MiniCPM: MiniCPM5 SOTA on-device LLMs](https://github.com/openbmb/minicpm)
- [MiniCPM-o 4.5: Towards Real-Time Full-Duplex Omni-Modal Interaction (arXiv 2604.27393)](https://arxiv.org/html/2604.27393v1)
- [MiniCPM-o 4.5 PDF](https://arxiv.org/pdf/2604.27393)
- [MiniCPM-o 4.5: Omni-Modal Interaction AI (emergentmind)](https://www.emergentmind.com/topics/minicpm-o-4-5)
- [MiniCPM-o 4.5: 9B Vision, Audio, and Speech Model (LearnOpenCV)](https://learnopencv.com/minicpm-o-4-5-a-9b-model-that-can-see-hear-and-speak-at-the-same-time/)
- [GitHub - OpenBMB/MiniCPM-o-Demo](https://github.com/OpenBMB/MiniCPM-o-Demo)
- [Realtime-Venus (arXiv 2609.13814, HTML)](https://arxiv.org/html/2609.13814)
- [GitHub - inclusionAI/Realtime-Venus](https://github.com/inclusionAI/Realtime-Venus)
- [Realtime-Venus project page](https://realtime-venus.github.io/)
- [inclusionAI/Realtime-Venus · Hugging Face](https://huggingface.co/inclusionAI/Realtime-Venus)
- [Realtime-Venus: Ant Group's Quiet 9B Full-Duplex Release (orcarouter.ai)](https://www.orcarouter.ai/blog/realtime-venus-quiet-release)
- [openbmb/MiniCPM-V-4.6 · Hugging Face](https://huggingface.co/openbmb/MiniCPM-V-4.6)
- [openbmb/MiniCPM-V-4_6 config.json](https://huggingface.co/openbmb/MiniCPM-V-4_6/resolve/main/config.json)
- [MiniCPM-V 4.5: Cooking Efficient MLLMs via Architecture, Data, and Training Recipe (arXiv 2509.18154)](https://arxiv.org/pdf/2509.18154)
- [MiniCPM-V 4.5: Efficient 8B Multimodal Model (emergentmind)](https://www.emergentmind.com/topics/minicpm-v-4-5)
- [openbmb/MiniCPM-o-2_6 · Hugging Face](https://huggingface.co/openbmb/MiniCPM-o-2_6)
- [GitHub - MiniCPM-o (fuxiaoaio mirror)](https://github.com/fuxiaoaio/MiniCPM-o)
- [Qwen2.5-Omni-3B · Hugging Face](https://huggingface.co/Qwen/Qwen2.5-Omni-3B)
- [Qwen2.5 Omni blog](https://qwen.ai/blog?id=qwen2.5-omni)
- [Qwen2.5-Omni: A Real-Time Multimodal AI (LearnOpenCV)](https://learnopencv.com/qwen2-5-omni/)
- [Qwen3-Omni Technical Report (arXiv 2509.17765)](https://arxiv.org/pdf/2509.17765)
- [Multimodal Support in llama.cpp (mtmd) README](https://github.com/ggml-org/llama.cpp/blob/master/tools/mtmd/README.md)
- [llama.cpp docs/multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- [FOSDEM 2026: Multimodal support in llama.cpp](https://fosdem.org/2026/schedule/event/LRZJEH-llama-cpp-multimodal/)
- [GitHub - tc-mb/llama.cpp-omni](https://github.com/tc-mb/llama.cpp-omni)
- [GitHub - xkeyC/llama.cpp-omni](https://github.com/xkeyC/llama.cpp-omni)
- [GitHub - ASLP-lab/Easy-Turn: Open-source turn-taking detection model and dataset](https://github.com/ASLP-lab/Easy-Turn)
- [Easy Turn: Integrating Acoustic and Linguistic Modalities for Robust Turn-Taking (arXiv 2509.23938)](https://arxiv.org/pdf/2509.23938)
- [DuplexChat: Constructing Speaker-Separated Full-Duplex Dialogue Speech at Scale (arXiv 2607.04941)](https://arxiv.org/html/2607.04941v1)
- [DuplexCascade: VAD-Free Cascaded ASR-LLM-TTS Full-Duplex Dialogue (arXiv 2603.09180)](https://arxiv.org/html/2603.09180)
- [LibriSpeech ASR corpus (OpenSLR)](https://www.openslr.org/12)
- [openslr/librispeech_asr · Hugging Face Datasets](https://huggingface.co/datasets/openslr/librispeech_asr)

**Unverified / could not confirm:** the exact standalone HF repo id for the SigLIP2 checkpoint variant OpenBMB used; whether resampler query weights (not just the output projection) meaningfully warm-start across different LLM hidden sizes; MiniCPM-o 4.5's own Stage 2 joint-pretraining sample counts and encoder learning-rate treatment; which specific open audio-QA and video-QA datasets OpenBMB used in MiniCPM-o's own recipe (only architecture and general pattern were confirmed, not the exact dataset list); SGLang's level of multimodal/omni support versus vLLM's; detailed interruption-handling mechanics beyond the general duplex framing; any latency numbers for Qwen2.5-Omni-3B or MiniCPM-o 2.6 comparable to the MiniCPM-o 4.5 numbers cited.
