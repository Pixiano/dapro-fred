# Successor search for FRED's main LLM (≤ ~4B, GGUF ≤ 6 GB), 2026-09-30

This is agent research: reading only, nothing downloaded. Most benchmark
numbers are self-reported by the vendors. The only independent numbers come
from Artificial Analysis (AA). BFCL scores for the same model vary with the
harness (Qwen3.5-4B: 30–56). MiniCPM5-2B's card claims it beats Qwen3.5-4B;
in FRED's harness it lost by about 5 points. **Only FRED's harness decides.**

## Verdict: no clear successor

No small model has evidence of beating Qwen3.5-4B on tools, reasoning,
knowledge, honesty and Hinglish at once. There is also no newer small Qwen:
Qwen3.6 and 3.8 open weights are 27B+ or 35B-A3B.

Qwen3.5-4B baseline (self-reported): BFCL-V4 50.3, TAU2 79.9, IFEval 89.8,
MMLU-Pro 79.1, GPQA-D 76.2.

## Challengers

1. **LFM2.5-2.6B** (Liquid, 2026-07-28). Weights:
   `LiquidAI/LFM2.5-2.6B-GGUF`, `LFM2.5-2.6B-Q8_0.gguf` (2.87 GB).
   - For:
     - BFCLv4 56.9 vs 50.6, self-reported.
     - AA, independent: best instruction following (IFBench 59%) and 79%
       non-hallucination.
     - Tiny KV cache: ~0.4 GB at 24k context.
   - Against:
     - Weak knowledge (the vendor itself says so).
     - Always thinks.
     - Custom lfm1.0 license.
     - Hinglish unknown.
   - Integration: tool calls are Pythonic, between `<|tool_call_start|>` and
     `<|tool_call_end|>`. That needs a new parser and probably detokenizing
     with special tokens.
2. **Nanbeige4.2-3B** (Apache). Weights:
   `bartowski/Nanbeige_Nanbeige4.2-3B-GGUF`, Q6_K (3.60 GB) or Q5_K_M
   (3.08 GB).
   - For:
     - Strongest agentic claims (Pinch-Bench, MCP-Atlas above Qwen3.5-9B).
     - AA, independent: BFCL 76%, 9B level.
   - Against:
     - AA non-hallucination only 33%.
     - English and Chinese only.
     - Looped layers make it slow.
     - KV ~4.3 GB fp16 at 24k (estimate), so it needs a q8 KV cache or a
       16k context to fit in 6 GB.
     - Needs llama.cpp with PR #25994, so llama-cpp-python must be rebuilt.
3. **Drop-in controls** (no parser work):
   - `mradermacher/AgentMercury-Qwen3.5-4B-GGUF` Q6_K (3.56 GB). An RL tune
     of Qwen3.5-4B on MCP tools; small, cherry-picked gains.
   - `ibm-granite/granite-4.2-3b-GGUF` Q6_K (3.01 GB). Same `qwen3_coder`
     tool format; weak at chat and multilingual.

Also considered:
- K2-Horizon-3.7B: always uses high reasoning effort, bad for voice.
- Nemotron-3-Nano-4B: English only; AA index 7.
- Gemma-4-E4B: 8B stored.
- 2025-era function-calling tunes (xLAM-2-3b, Hammer2.1-3b, Arch-Agent-3B):
  older bases, some with restrictive licenses.

## Fine-tunes: thin

- AgentMercury is the only Qwen3.5-4B tune with real agentic RL and numbers.
- `limnmn/Qwen3.5-4B-AgentTools-GGUF` claims to fix the "answers in prose
  instead of calling the tool" problem, but has zero evaluations.
- Jackrong's reasoning distills: tool calling likely regresses.
- `Jackrong/Qwen-Tool-Calling-Template-Fix` is not a model. It is a Jinja
  template fix for repeated-call loops caused by tool-call history rendering
  in llama.cpp.
- Nothing new at ≤4B from the xLAM, ToolACE, Hammer or Arch labs in 2026.

## Sources

- Model cards: the repos above.
- https://llm-stats.com/benchmarks/bfcl-v4
- https://artificialanalysis.ai/articles/mobile-phone-intelligence-inference
- https://artificialanalysis.ai/models/open-source/tiny
- https://github.com/ggml-org/llama.cpp/pull/25994
