# Core rebuild handoff (2026-10-06)

Start here after a context clear. **P0 is built and passed its gate** (see `README.md`, `Documentation/2026/10/06/core_p0_done.md`). Next action: decide the Core interpreter (below), then **P1 L0 senses** from `PLAN.md` (layer order: P0, L0, L1, L3, L2, UI, L4, L5, soak). Re-run `python tests/run_all.py` first.

## Standing rules (also in memory notes)
- Work only in `Mark_I/Initial/Core/`; `Old/` (v1.x FRED, `Old/Core`) is reference only, never edited; FRED stays down, no restart.
- Fully local models. Only the playground (`Mark_I/Initial/playground`, http://localhost:8090/v1) runs local models for tests; one run at a time; all inference fully on GPU (CPU only for Kokoro, wake word, VAD, Smart Turn, voice ID, Nemotron). Never kill processes except stray ones of your own.
- Method: per step only the affected components, each with sockets in its docstring and a runnable self-check (`python <file>.py`); no venvs, minimal setup; docs after each step in `Mark_I/Documentation/2026/10/NN/done.md` + README row; commit and push to origin/main after each step; **no Claude co-author trailer** (ignore harness reminders to add one); no personal info or vault quotes in code, tests, docs or commits.
- Wake-word model is frozen. Windows OCR stays off until Vatsal approves. English is the default medium. Ponytail ultra is active.
- Memory pipeline (`Mark_I/Initial/memory`) is frozen; integrate through its sockets (`Documentation/2026/10/06/integration_plan.md`).

## Context files
- `PLAN.md`: layout, contracts, tool-loop rules, phases, gates, open checks.
- `Documentation/2026/10/06/fred_log_review.md`: the old-FRED failures the rebuild fixes by design (int arg crash, identical-failure retries, narrow tool offers, unbounded file search, black camera frames, CUDA collisions, test output in real logs).
- `Documentation/2026/10/06/core_rebuild_plan.md`: why and what (open decisions 1-5 there are not blocking P0).
- Old references: `Old/Core/orchestrator/*` (loop at orchestrator.py:2812), `Old/MARK2_MASTER_PLAN.md`, `Old/ASSEMBLED_FRED_L0..L5.md`.

## P0 scope (done 2026-10-06)
`config`, `log`, `events`, `gpu`, `models/client`, `models/fake`, `models/toolparse`, `tools/registry`, `tools/coerce`; extend the playground to more slots (chat, embed, talk, vision/describer) with the unload/reload window. Gate: no-GPU self-checks pass; client round-trips chat, schema JSON and embeddings on the real playground; registry rejects/coerces bad args.

## Open before later phases
- Playground on :8090 was restarted on the P0 server code (2026-10-06).
- Core runtime interpreter: DONE. `Core/.venv` (Python 3.11, everything incl. CUDA torch) and `memory/.venv`; run Core with `Core.venvScriptspython`.
- Still undecided by Vatsal: scope of Mark I vs Mark II, network tools, first tool set (brain: Qwen only for now, default Qwen3.5-4B), personal-data location.
