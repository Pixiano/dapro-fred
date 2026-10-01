# Brain contender 2: LFM2.5-2.6B (still in testing)

Liquid AI, released 2026-07-28. A hybrid of 22 conv and 8 GQA layers,
2.69B params. Picked by the successor search
(`docs/2026/09/30/research_small_llm_successor.md`).

What the vendor and independent sources report:
- Tool calling: BFCLv4 56.9 vs the baseline's 50.6 (vendor numbers).
- Instruction following: IFBench 59%, best of the small models
  (Artificial Analysis, independent).
- Honesty: 79% non-hallucination (Artificial Analysis).
- Knowledge: weak; the vendor says so.
- Always thinks before answering.
- License: custom `lfm1.0`.

## Weights

`models/LFM2.5-2.6B-Q8_0.gguf` (2.87 GB, gitignored), from
`LiquidAI/LFM2.5-2.6B-GGUF` @ `e7caca5d`. Restore with
`..\..\..\fetch_model.py lfm25`.

## Sandbox

`lfm_patch.py` is applied at benchmark time only:
- **Tool-call parser.** LFM writes Pythonic calls between
  `<|tool_call_start|>` and `<|tool_call_end|>`. These markers are not
  special tokens, so they survive decoding. Calls are parsed with `ast` and
  literal arguments only.
- **Template fix.** It drops the template's HF-only `{% generation %}` tags,
  which plain Jinja2 rejects.

Self-check: `Core\venv\Scripts\python.exe lfm_patch.py` → OK.

The template opens `<think>` itself, so the reply starts mid-thought. FRED
already strips text up to an orphan `</think>`. FRED's `enable_thinking`
switch is ignored because this model always thinks, so watch latency and
max_tokens.

## Log

- 2026-10-01: set up; patch written, self-check OK; downloading.
