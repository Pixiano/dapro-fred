# Roadmap (Vatsal, 2026-10-01)

1. Pipeline fixes: dispatcher mis-routes, calculator parsing (sqrt, percent), false "I haven't actually done that" guard, persona weather example, follow-up tool carry-over, leftover checker errors (time-01 fixed date), thinking cap (~96 tokens), one model call per tool turn.
   - Embedder (ON HOLD per Vatsal 2026-10-01; the Gemma 4 E4B reason for the hold was dropped 2026-10-02, so the swap is waiting on Vatsal to resume it; decision from `embedder_4b_vs_06b.md`): go back to Qwen3-Embedding-0.6B Q8_0 (1.4 GiB vs 3.4 for the 4B), with per-job query instructions (tool-specific for routing, NONE for memory recall), centering for tools only; no reranker, no routing head, no fine-tune unless the tools test below shows a gap > 3 pts. After the swap: delete the vault `chunks.json` by hand and rebuild the FAISS memory index (same dimension does not self-heal). Edits `Core/`: ask Vatsal first. Also fix the 4096-vs-512-token comment (llama-cpp truncates inputs to n_batch=512). Pending: bigger tools-routing test (n>=300) to confirm before the swap. Touches `Core/`: ask Vatsal first, sandbox copy first.
2. In parallel: (a) fine-tune LFM2.5 (1k-2k-example LoRA, Vatsal's call 2026-10-01 instead of a 500 pilot, native Pythonic tool format, thinking kept ON, correct loss masks for the `{% generation %}` tags and the auto-opened `<think>`); 
   (b) perfect the benchmark: wire general2 (`stage_general` is hard-wired to general.jsonl, line ~1037; needs a `GENERAL_CASES` env var or `--cases`), hand-check the 81-case tools suite, run Mercury (cap 96) as the control.
   - LFM training data must include (Vatsal, 2026-10-01): **multi-tool turns**, the same tool 2+ times (e.g. weather for two cities) or different tools, up to 4 calls in one turn, in the model's native format (`[f(a=..), g(b=..)]`); and **"I don't know" cases that also say what went wrong with the tool** (tool failed / unreachable / returned nothing), not a bare "I don't know".
   - Tool schemas in the data must match the final tool set, including the memory-vault tools from `FRED_Memory_Overhaul.md`; settle those (overhaul Phase 1-2, section 40) before generating the data, so the model isn't retrained when they change.
   (c) Execute the `FRED_Memory_Overhaul.md`; settle those (overhaul Phase 1-2, section 40). Before (a) and (b).
3. Compare tuned LFM vs Mercury (and baseline): tools, general2, median/p90 latency, VRAM. Choose one, or another LFM round if close and still improving.
   - Rule: LFM must beat Mercury on general2 AND reach roughly Mercury's 4-5 s median, else Mercury (light tune) takes the slot.
4. Judge MiniCPM5-2B (fast talker/judge): needed or redundant with a small brain. Decide with latency numbers.
   - Memory vault overhaul (`FRED_Memory_Overhaul.md`, 2061 lines, 8 phases in section 40: foundation, FRED integration, daily consolidation, temporal graph, weekly meta-agent, personal model, monthly/yearly, deep self-analysis). Implement alongside. The real-time brain only does capture/recall tool calls (section 32); consolidation runs on larger periodic models, not the brain.
5. Build the assembled architecture (`Mark_II_Testing/ASSEMBLED_FRED.md`) and its components. Targets: barge-in stop <200 ms, peak VRAM <=15 GB, 8 h overnight with flat VRAM.
6. Polish FRED in general and make it ready for daily use.

Scores to beat (177 scored cases, hand-corrected): Mercury 149, baseline Qwen3.5-4B 145, LFM2.5 143 (best per suite), MiniCPM5-2B 137.

Brain candidates (2026-10-02): Mercury, tuned LFM2.5, current Qwen3.5-4B, Qwen3.8-4B-Distill (empero-ai; test Q6_K and Q4_K_M, with and without the 96-token thinking cap). Gemma 4 E4B/E2B dropped.
