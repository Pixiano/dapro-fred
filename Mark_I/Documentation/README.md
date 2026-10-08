# Mark I Documentation

Mark I (aka v2) is the clean rebuild of FRED. The predecessor (v1.x, `Core/`) is untouched.

## Rules
- One folder per day: `YYYY/MM/DD/`. Multiple files per day are fine, built coherently, named by topic (no date in the filename). Every day has a `done.md`.
- This README is the index. Update it when a day or document is added.
- Predecessor docs are imported only with a label header: `> IMPORTED from predecessor: <origin path>, written <date>. Not Mark I truth.`

## Layout
- `Mark_I/Initial/` is where everything is built. Finished work is extracted to `Mark_I/`.
- `Mark_I/Initial/memory/` is the first component (memory overhaul, Phase 1).
- `Mark_I/Initial/pulse/` is the second division: the daily news feed (placeholder, not designed yet).

## Index
| Day | Files | What |
|---|---|---|
| 2026-10-02 | [done](2026/10/02/done.md), [memory build order](2026/10/02/memory_build_order.md) | Mark I set up; memory Phase 1 complete; Layer 1 raw archive |
| 2026-10-03 | [done](2026/10/03/done.md), [memory system overview](2026/10/03/memory_system_overview.md) | Phases 3-4 built (daily consolidation, graph + names); full memory system explained |
| 2026-10-04 | [done](2026/10/04/done.md), [phase 5 design](2026/10/04/phase5_weekly_agent_design.md), [phase 6 design](2026/10/04/phase6_personal_model_design.md), [phase 7 design](2026/10/04/phase7_monthly_yearly_design.md), [summary through phase 7](2026/10/04/summary_through_phase7.md) | Phases 5-7 built (weekly, personal model, monthly/yearly), designs, and the summary through Phase 7 |
| 2026-10-05 | [done](2026/10/05/done.md), [phase 8 design](2026/10/05/phase8_self_analysis_design.md) | Phase 8 built (deep self-analysis); local-model runs and fixes; check of the deletable-space report ([FRED part](2026/10/05/deletable_report_check.md)); [model comparison](2026/10/05/model_comparison_2026-10-05.md), [test outputs](2026/10/05/test_outputs_2026-10-05.md), [role swap and contradiction test](2026/10/05/role_swap_and_contradiction_test.md) |
| 2026-10-06 | [done](2026/10/06/done.md) | Ask queue (doubts asked as either/or at conversation end), two-project answers, precedents (decision memory) tested on gpt-oss/Qwen/Gemma, llama.cpp playground; **memory pipeline frozen**, see [integration plan](2026/10/06/integration_plan.md); FRED log review ([findings](2026/10/06/fred_log_review.md)), Core rebuild ([plan](2026/10/06/core_rebuild_plan.md), [build path](2026/10/06/core_build_path.md)); **Core P0 built** ([done](2026/10/06/core_p0_done.md)); **Core P1a audio built** ([done](2026/10/06/core_p1a_done.md)); **Core P1b screen built** ([done](2026/10/06/core_p1b_done.md)); **Core P1c camera + presence built** ([done](2026/10/06/core_p1c_done.md)); **Core summary P0-P1** ([summary](2026/10/06/core_summary_p0_p1.md)); **Core P2 clock built** ([done](2026/10/06/core_p2_done.md)) |
| 2026-10-06 | [Core P3 done](2026/10/06/core_p3_done.md) | Core thinker: tool loop engine, 44 tools, memory bridge, acceptance |
| 2026-10-07 | [done](2026/10/07/done.md) | Embedder switched to Qwen3-Embedding-0.6B; skills loader and ranking check |
| 2026-10-07 | [brutal test report](2026/10/07/brutal_test_report.md) | Brutal test of everything built with real usage projected; 4 bugs fixed; voice ID switch |
| 2026-10-08 | [usage so far](2026/10/08/usage_so_far.md) | What Mark I can do and how it is used (P0 to P7), honest limits |
| 2026-10-08 | [code review fixes](2026/10/08/code_review_fixes.md) | Six-agent review of P0 to P7: what was fixed, what waits for your decision |
| 2026-10-08 | [production fixes](2026/10/08/production_fixes.md) | First live session: why the pill flashed, the noise loop, and the fixes |
| 2026-10-08 | [live runs](2026/10/08/live_runs.md) | Log of each live run: setup, what the logs showed, what changed |
| 2026-10-08 | [usage data](2026/10/08/usage_data.md) | The central usage store: what is saved, where, by component and day |
