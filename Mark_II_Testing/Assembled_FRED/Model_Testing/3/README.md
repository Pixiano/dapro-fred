# Brain contender 3: AgentMercury-Qwen3.5-4B (still in testing)

`Minbyul/AgentMercury-Qwen3.5-4B` (Apache-2.0) is the baseline Qwen3.5-4B
after GRPO reinforcement learning on MCP tool environments. Its own harness
reports small gains: BFCL 30.35 → 31.93, τ³ 0.706 → 0.747. Only the gains
are listed. It was picked as a **drop-in control**: same architecture, chat
template and tool-call format as the baseline, so no parser patch is
needed. It tests whether tool RL alone moves FRED's numbers.

## Weights

`models/AgentMercury-Qwen3.5-4B.Q6_K.gguf` (3.56 GB, gitignored; the same
quant as the baseline), from `mradermacher/AgentMercury-Qwen3.5-4B-GGUF` @
`8751c57b`. Restore with `..\..\..\fetch_model.py mercury`.

## Log

- 2026-10-01: set up; downloading.
