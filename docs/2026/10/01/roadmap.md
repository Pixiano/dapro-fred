# Roadmap (Vatsal, 2026-10-01)

1. Pipeline fixes: dispatcher mis-routes, calculator parsing (sqrt, percent), false "I haven't actually done that" guard, persona weather example, follow-up tool carry-over, leftover checker errors (time-01 fixed date), thinking cap (~96 tokens), one model call per tool turn. Touches `Core/`: ask Vatsal first, sandbox copy first.
2. In parallel: (a) fine-tune LFM2.5 (1k-2k-example LoRA, Vatsal's call 2026-10-01 instead of a 500 pilot, native Pythonic tool format, thinking kept ON, correct loss masks for the `{% generation %}` tags and the auto-opened `<think>`); (b) perfect the benchmark: wire general2 (`stage_general` is hard-wired to general.jsonl, line ~1037; needs a `GENERAL_CASES` env var or `--cases`), hand-check the 81-case tools suite, run Mercury (cap 96) as the control.
3. Compare tuned LFM vs Mercury (and baseline): tools, general2, median/p90 latency, VRAM. Choose one, or another LFM round if close and still improving.
   - Rule: LFM must beat Mercury on general2 AND reach roughly Mercury's 4-5 s median, else Mercury (light tune) takes the slot.
4. Judge MiniCPM5-2B (fast talker/judge): needed or redundant with a small brain. Decide with latency numbers.
5. Build the assembled architecture (`Mark_II_Testing/ASSEMBLED_FRED.md`) and its components. Targets: barge-in stop <200 ms, peak VRAM <=15 GB, 8 h overnight with flat VRAM.
6. Polish FRED in general and make it ready for daily use.

Scores to beat (177 scored cases, hand-corrected): Mercury 149, baseline Qwen3.5-4B 145, LFM2.5 143 (best per suite), MiniCPM5-2B 137.
