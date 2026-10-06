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
| 2026-10-06 | [done](2026/10/06/done.md) | Ask queue (doubts asked as either/or at conversation end), two-project answers, precedents (decision memory) tested on gpt-oss/Qwen/Gemma, llama.cpp playground; **memory pipeline frozen**, see [integration plan](2026/10/06/integration_plan.md); FRED log review ([findings](2026/10/06/fred_log_review.md)), Core rebuild ([plan](2026/10/06/core_rebuild_plan.md), [build path](2026/10/06/core_build_path.md)); **Core P0 built** ([done](2026/10/06/core_p0_done.md)); **Core P1a audio built** ([done](2026/10/06/core_p1a_done.md)); **Core P1b screen built** ([done](2026/10/06/core_p1b_done.md)) |
