# Assembled FRED: L5 Consolidation, local models only (draft, 2026-10-02)

Scheduled agents from `FRED_Memory_Overhaul.md` (sections 14, 40): nightly,
weekly, monthly and longer. **Vatsal's rule (2026-10-02): local models only, no
cloud.** GPU-only rule applies. Models are loaded dynamically for the job and
the live stack is unloaded first; nothing is swapped in during a live session.

## Picks

| Job | Model | Quant | VRAM while running | Thinking |
|---|---|---|---:|---|
| Nightly (extract, classify, summarise, update state, daily log) | Qwen3.8-9B-Distill (empero-ai), or a fine-tuned one | Q4_K_M or Q5_K_M (to test); file sizes to confirm | ~6-8 GB incl. context (estimate) | toggle, tested |
| Weekly (cross-day patterns, evidence comparison, long context) | gpt-oss-20b | MXFP4 (12.1 GB file) or Unsloth Q4_K_M (11.6 GB) | ~13-14 GB with context (estimate); live stack fully unloaded | reasoning effort low / medium / high |
| Monthly, yearly, deep longitudinal | same as weekly | same | same | high |

## Fit and limits

- gpt-oss-20b is the only 10B+ model here that fits a 16 GB card whole: 12.1 GB file leaves roughly 3-4 GB for context. It must run alone, which is why the live stack unloads. 20B-class MoE weights must all sit in VRAM, so the Qwen3.8-35B-A3B (about 20 GB at Q4) and 27B do not fit under the GPU-only rule.
- Another 10-14B dense model at Q4 (about 8-9 GB) is a possible weekly alternative; none has been chosen or checked.
- The Qwen3.8 family has 2B, 4B, 9B, 27B and 35B-A3B; there is no 8B, so "8B" means the 9B.
- Thinking toggle: gpt-oss has reasoning effort levels natively. The Qwen3.8 distills open every answer with a thinking block, so a true off switch needs testing (chat template `enable_thinking`, or a closed think block as in `lfm_patch.py`).

## Scheduling rules

- Dynamic loading only in the nightly window: unload the live stack, check free VRAM, load the job model, run, unload, reload the live stack.
- No job starts if Vatsal is present or a live session is active; it waits or skips and records why in `consolidation_runs`.
- One model resident at a time; weekly and monthly runs reuse the same load when scheduled back to back.
- Output evidence-linked, as in the overhaul (every conclusion cites events).

## Open items

1. Quality test per job on real day logs: Qwen3.8-9B Q4/Q5/Q6 vs the 4B, and gpt-oss-20b at low/medium/high effort. Metrics: facts kept, contradictions caught, hallucinated entries, run time.
2. File sizes and real VRAM for Qwen3.8-9B-Distill quants (estimates above).
3. Whether a fine-tuned 9B is worth it for nightly (after the LFM fine-tune settles the recipe).
4. Thinking toggle behaviour of the Qwen3.8 distills.
5. Reload time of the live stack after a night job (cold start budget in the morning).
6. Overhaul phases 1-3 must exist first; the scheduler and `consolidation_runs` are not built.
7. Choice of the "other 10B+" weekly alternative if gpt-oss-20b underperforms.
