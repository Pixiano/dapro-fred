# Fine-tuning dossier: FRED brain candidates (2026-10-01)

Legend: **[DOC]** = stated in a cited source. **[INF]** = my inference, not verified. **[GAP]** = could not find evidence.
Research was web-only (no weights downloaded, no GPU, no code touched). Several fetches were summarised by a small model, so exact numbers should be re-checked against the primary page before relying on them.

## 0. Cross-cutting findings (apply to all three)

- **Dataset sizes (evidence):**
  - Liquid: "500 to 5,000 task examples", quality/distribution over volume, LoRA first [DOC] https://docs.liquid.ai/lfm/fine-tuning/overview.md
  - TinyToolCaller: 1.5B model, QLoRA, 5,000 examples, valid tool calls more often than baseline [DOC, secondary] https://app.readytensor.ai/publications/tinytoolcaller-qlora-fine-tuning-of-a-15b-llm-for-reliable-function-calling-a95jlwdioKYatbzl8OKNS
  - xLAM/APIGen: ~60k examples for the full dataset; xLAM-1B/7B rank high on BFCL [DOC] https://arxiv.org/pdf/2406.18518
  - Hinglish: Qwen2.5-3B/7B LoRA/QLoRA on >3,000 synthetic dialogues (~5M tokens, Gemini-2.0-Flash generated) gave +35-41% fluency; sub-3B models still limited even with good data [DOC] https://arxiv.org/html/2504.19070v1
  - ms-swift Qwen3.5-4B example: LoRA r=8, alpha=32, all-linear, LR 1e-4, 1 epoch, max len 2048, warmup 0.05, 4,500 samples, ~12 min on 4x20GB [DOC] https://swift.readthedocs.io/en/v4.3/BestPractices/Qwen3_5-Best-Practice.html
- **Suggested ranges for FRED [INF, built from the above plus general practice; no source gives per-category numbers]:**

| Data type | Minimum useful | Typical | Diminishing returns |
|---|---|---|---|
| (a) Tool-calling SFT (format + restraint) | ~300-500 | 1.5k-5k | >10k (unless adding new tools) |
| (b) Persona/tone/spoken brevity | ~100-300 | 500-1.5k | >3k (overfits style, hurts general) |
| (c) Hinglish / code-switching | ~500-1k | 2k-5k (3k dialogues is the only cited point) | >10k |
| (d) Memory-grounded answers + "I don't know" | ~200-400 | 1k-2k (include ~20-30% unanswerable) | >4k |
| (e) DPO pairs | ~500-1k | 2k-5k | >10k; GRPO needs a verifiable reward env, tens to hundreds of steps |

- **Hyperparameters commonly used for 2-4B LoRA [DOC where cited, else INF]:** r=8-16, alpha=16-32 (Unsloth LFM: r=16; ms-swift Qwen3.5: r=8 alpha=32), LR 1e-4 to 2e-4 (LFM Unsloth notebook 2e-4; ms-swift 1e-4), 1-3 epochs (docs use 1), per-device batch 1-4 with grad accumulation 4-8, max_len 2048-4096. Voice-assistant turns are short: expect ~150-800 tokens/example, tool-heavy multi-turn 1-3k. DPO LR is much lower (5e-6 to 5e-7 is common practice [INF]).
- **Example types to include [INF, standard recipe]:** single call; parallel calls; multi-turn with tool results; no-tool-needed (the main guard against over-calling, aim 25-35% of tool data); error/empty/timeout stub results where the reply must say the tool failed and not invent; injection inside tool results or memory snippets that must be ignored; unanswerable-from-memory cases; spoken brevity (no markdown, no lists, short sentences, TTS-friendly numbers); Hinglish in Roman script for both input and output; persona-consistent refusals.
- **Synthetic data and licences:** Gemini-2.0-Flash was used for Hinglish in the paper above. Check ToS before using any hosted model's outputs: Anthropic and OpenAI terms restrict using outputs to build competing models [INF; verify current terms]. Safer sources: the Apache-2.0 base itself or another Apache/MIT model, human-written seeds, and Vatsal's own logs (never commit vault content, per project memory rules).
- **Windows note [INF]:** Unsloth, TRL and bitsandbytes work on Windows, but WSL2 is the lower-risk path; flash-linear-attention / causal-conv1d kernels (needed for Qwen3.5 speed) are Linux-first.
- **GGUF return path (generic) [DOC/INF]:** train LoRA in bf16 -> merge to 16-bit HF safetensors -> `convert_hf_to_gguf.py` (f16/bf16) -> `llama-quantize` to Q6_K or Q8_0. Unsloth advertises one-call export to q4_k_m/q8_0/f16 [DOC] https://unsloth.ai/docs/models/qwen3.5/fine-tune.md . Always quantise the merged 16-bit model, never merge into an already-quantised one. Unsloth troubleshooting: if the exported model behaves worse in another runtime, the usual cause is the wrong chat template / EOS at inference [DOC]. Community reports of "GGUF lost the fine-tune" exist, usually template/precision mismatch [DOC, weak] https://discuss.huggingface.co/t/finetuned-llm-model-conversion-to-gguf-performance-drop/93179
- **Evaluation [INF]:** freeze a hold-out (>=10-15% of each category, never trained on) plus a regression suite = FRED's existing Model_Testing benchmark cases, run before/after on the same Q6_K GGUF and same llama.cpp build. Add checks for: tool-call parse rate, over-calling rate on no-tool prompts, "tool failed" honesty, think-mode on/off behaviour, EOS/stop behaviour, TTS-safe output, and a small general-ability sample (a few hundred MMLU/IFEval-style items) to detect forgetting.

---

## 1. Qwen3.5-4B (baseline)

**Facts**
- Licence Apache-2.0; hybrid Gated DeltaNet architecture; thinking on by default, `enable_thinking=False` disables; tool calling via the `qwen3_coder` parser; native ctx 262k; unified vision-language model [DOC] https://huggingface.co/Qwen/Qwen3.5-4B
- Unsloth: supports 0.8B/2B/4B/9B/27B/35B-A3B/122B-A10B; **4B bf16 LoRA ~10 GB VRAM**; full FT ~4x LoRA VRAM; 1.5x faster and 50% less VRAM than FA2 setups; **QLoRA (4-bit) "not recommended"** on Qwen3.5 (quantisation differences); needs **transformers v5**; to preserve reasoning keep **>=75% reasoning examples** in the mix, otherwise reasoning can be omitted; exports GGUF/vLLM [DOC] https://unsloth.ai/docs/models/qwen3.5/fine-tune.md
- ms-swift 4.3 supports Qwen3.5-4B LoRA (recipe above); needs `flash-linear-attention>=0.4.2` (and Python 3.12) for speed; has `--loss_scale ignore_empty_think` and `--add_non_thinking_prefix` for empty-think handling; packing/padding_free needs ms-swift>=4.3.1 [DOC] (swift link above).
- Without flash-linear-attention the DeltaNet layers fall back to a slow fp32 torch loop (0.70 vs 1.22 s/step in one report) [DOC] https://github.com/huggingface/transformers/issues/48718
- LoRA gotcha: in one framework `target_modules=["all"]` put no adapter on the linear-attention mixer (only the 1-in-4 full-attention layers got q/k/v/o) [DOC, other framework, but the trap is architectural] https://github.com/invergent-ai/surogate/pull/218 . Packing documents can leak Gated DeltaNet state/conv across boundaries [DOC] https://github.com/invergent-ai/surogate/pull/216
- FLA 0.5.0 bug: Qwen3.5-4B outputs "!!!!!!" [DOC] https://github.com/fla-org/flash-linear-attention/issues/792 (pin the FLA version).
- TRL `assistant_only_loss=True` needs `{% generation %}` tags; TRL auto-patches known families like Qwen3, but Qwen3.5 resolves via the VLM/processor path and TRL blocks assistant_only_loss for VLMs, so it can trigger even for text-only data [DOC] https://discuss.huggingface.co/t/sfttrainerflags-blocks-assistant-only-loss-true/176210 . Unsloth's `train_on_responses_only` with explicit markers is the usual workaround [INF].
- GGUF: llama.cpp arch is `qwen35`; LoRA-adapter GGUFs made with an old `convert_lora_to_gguf.py` tag `qwen3` and fail with an arch mismatch; some Qwen LoRA merges needed a V-head reorder fix [DOC, community] https://friendli.ai/models/Lathly/Qwen3.8-27B-Samantha_Uncensored_1.1_LoRA . Merging to HF 16-bit first, then converting, sidesteps adapter conversion [INF].

**Method [INF]:** bf16 LoRA (r=16, alpha=32; include attention + MLP + DeltaNet projections; verify target names with `print(model)`), SFT then optional DPO. Not QLoRA. Full FT does not fit 16 GB.
**Think handling [INF]:** FRED likely runs no-think for latency. Train through the same template path as production (the enable_thinking=False render with empty think block) so no-think behaviour is what gets shaped. If thinking must survive, keep >=75% reasoning-bearing rows per Unsloth, or do not rely on thinking after SFT and test it.
**Risks:** thinking breakage if SFT data lacks think blocks while eval uses them; tool-format drift if training JSON differs from the qwen3_coder template; loss of multilingual ability.
**Hardware:** bf16 LoRA ~10 GB plus activations: fits 16 GB at seq 2-4k with gradient checkpointing, batch 1-2 [DOC/INF]. Time: ms-swift reports ~12 min for 4.5k short samples on 4 GPUs; one 16 GB card, 3-5k examples x 2 epochs plausibly 0.5-2 h [INF].

## 2. AgentMercury-Qwen3.5-4B

**Facts**
- On-policy GRPO, 200-step MCP agentic RL, ~2,300 environments, environment-state verifiers; Apache-2.0; MCP tool use; released at a "clean-minimum" checkpoint because further steps added verbosity and repetition; served with sglang [DOC] https://huggingface.co/Minbyul/AgentMercury-Qwen3.5-4B
- Same architecture as Qwen3.5-4B, so all Qwen3.5 tooling, bugs and GGUF notes apply [INF].

**Method [INF]:** same bf16 LoRA SFT. Already RL-tuned on tool use, so it needs fewer tool rows (a few hundred to re-anchor FRED's own schema) and more persona/spoken-brevity/honesty data. SFT on top of a GRPO checkpoint can erode the RL gains: low LR (<=5e-5 to 1e-4), 1 epoch, small rank, compare against baseline on the tool suite. Do not stack more RL without a verifiable reward environment; the card warns of verbosity creep (bad for voice).
**Risks:** verbosity/repetition tendency [DOC]; tool format may follow MCP conventions rather than FRED's [INF]; [GAP] no published fine-tune recipe or community reports of SFT-after-GRPO on this checkpoint. Card says training focused on agentic decisions rather than reasoning chains [DOC]; behaviour with `enable_thinking` after SFT is untested [GAP].
**Hardware / GGUF:** identical to Qwen3.5-4B. [GAP] whether an official GGUF exists.

## 3. LFM2.5-2.6B

**Facts**
- Licence "lfm1.0" (LFM Open License v1.0): commercial use not licensed for legal entities above **$10M annual revenue**; derivatives allowed with licence copy, attribution, notice of modifications; automatic termination on violation [DOC] https://huggingface.co/LiquidAI/LFM2.5-2.6B and https://huggingface.co/LiquidAI/LFM2.5-2.6B/blob/main/LICENSE . Fine for a personal assistant [INF, not legal advice].
- 30 layers: 22 double-gated short-conv blocks + 8 GQA; ChatML-like; template inserts `<think>` when the assistant turn starts; Pythonic tool calls between `<|tool_call_start|>`/`<|tool_call_end|>` (JSON override possible); recommended sampling temp 0.1, top_k 50, repetition_penalty 1.1; recommended for agentic/tool use/extraction/RAG, **not** for coding or knowledge-heavy tasks [DOC] model card above.
- Official fine-tuning: LEAP Finetune (SFT, LoRA, DPO, GRPO; GGUF export + quantisation built in), TRL (SFT/DPO/GRPO), Unsloth (SFT, CPT) [DOC] https://docs.liquid.ai/lfm/fine-tuning/overview.md
- Liquid says: **train on the native Pythonic tool-call format, not OpenAI JSON**; apply the chat template and keep training formatting **character-for-character identical to production**; try prompt optimisation and constrained decoding first; 500-5,000 examples; LoRA first; a 1.2B LoRA run takes minutes to tens of minutes on one GPU [DOC] same page.
- Unsloth LFM recipe: r=16, alpha 16 (another page says 32), dropout 0, LR 2e-4, batch 2 x accum 4, targets q_proj,k_proj,v_proj,out_proj,in_proj,w1,w2,w3 (covers conv `in_proj` and MLP `w1-3`); 4-bit QLoRA supported with "minimal quality loss" per Liquid, no Qwen-style warning [DOC] https://docs.liquid.ai/lfm/fine-tuning/unsloth.md , https://unsloth.ai/docs/models/tutorials/lfm2.5.md . Those pages are about the 1.2B; 2.6B should scale similarly [INF].
- llama.cpp has day-one LFM2.5 support [DOC, secondary] (Unsloth tutorial above).
- `{% generation %}` tags: TRL `assistant_only_loss=True` needs them in the template. I found no source documenting TRL + LFM2.5 assistant-only masking with tool turns and the auto-opened `<think>` [GAP]. Pitfall [INF]: since the template opens `<think>` at generation, decide whether the trained target includes an empty think block or only the opened tag, and mirror it exactly in llama.cpp; mismatch is the likeliest source of drift.

**Method [INF]:** bf16 LoRA r=16 via Unsloth or TRL (or LEAP Finetune for the integrated GGUF path), LR 1e-4 to 2e-4, 1-3 epochs, 1.5k-5k examples; QLoRA viable but unnecessary at 2.6B.
**Risks:** Liquid says it is weak at knowledge-heavy tasks, so SFT will not add knowledge; memory recall must stay retrieval-based. Hinglish: sub-3B models were still limited in the Hinglish paper [DOC]; [GAP] LFM2.5 Hinglish quality undocumented.
**Hardware [INF]:** 2.6B bf16 LoRA about 6-9 GB; full FT (~16-20+ GB with optimizer states) is marginal on 16 GB. Time: well under an hour for a few thousand short examples.
**GGUF:** supported in llama.cpp; no LFM-specific conversion bug found [GAP: absence of evidence]; verify with a round-trip test (same prompt, HF vs Q8_0 vs Q6_K).

---

## Comparison

| Candidate | Best method | Framework | Data volume | Hardware fit (16 GB) | GGUF export risk | Licence |
|---|---|---|---|---|---|---|
| Qwen3.5-4B | bf16 LoRA SFT (+DPO); avoid QLoRA | Unsloth (transformers v5) or ms-swift; TRL with caveats | 2-6k mixed; keep reasoning rows if thinking is kept | LoRA ~10 GB fits; full FT no | Medium: new `qwen35` arch, adapter-tag/V-head issues, FLA kernel version traps; merge-then-convert avoids most | Apache-2.0 |
| AgentMercury-Qwen3.5-4B | Light LoRA SFT, low LR, 1 epoch; no more RL | Same as Qwen3.5 | 1-3k (persona/brevity/honesty heavy, few tool rows) | Same as Qwen3.5 | Medium (same arch) | Apache-2.0 |
| LFM2.5-2.6B | bf16 LoRA SFT/DPO, Pythonic tool format | LEAP Finetune, Unsloth, TRL | 0.5-5k per Liquid | LoRA ~6-9 GB comfortable; full FT marginal | Low-medium: native llama.cpp support; template/think alignment is the risk | LFM Open v1.0 (free under $10M revenue) |

## Recommendation

**Most fine-tunable: LFM2.5-2.6B**, on process maturity: Liquid publishes the recipe (500-5,000 examples, Pythonic format, LoRA, LEAP/Unsloth/TRL, built-in GGUF export), it fits 16 GB with headroom, trains in minutes, and has no QLoRA warning. The licence is irrelevant for personal use. **Higher quality ceiling: Qwen3.5-4B / AgentMercury** (bigger, Apache-2.0, stronger multilingual), but the toolchain is younger (transformers v5, FLA kernels, VLM-path TRL block, hybrid-arch LoRA target traps) and QLoRA is off the table. Between the two Qwens, use AgentMercury only if it beats the baseline on FRED's tool benchmark; otherwise the baseline avoids RL-then-SFT interactions.

**Main uncertainty:** none of this is validated on FRED's data. No source covers Hinglish quality at 2.6B, SFT on top of a GRPO checkpoint, or LFM2.5 `{% generation %}` masking with tool turns and the auto-opened `<think>`. A 500-example pilot LoRA on each, scored on the existing benchmark, would settle it cheaply.
