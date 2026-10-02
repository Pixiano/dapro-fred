# Embedder_Testing

Head-to-head of FRED's embedder: Qwen3-Embedding-4B Q4_K_M (current) vs Qwen3-Embedding-0.6B Q8_0 (previous), plus cheap fixes for the 0.6B. Write-up: `docs/2026/10/01/embedder_4b_vs_06b.md`. Nothing in `Core/` is modified; pure Core modules (`tool_router`, `intent`, `dispatcher`, `vault_md`) are loaded read-only so the routing numbers use FRED's own code.

Use only the Core venv (`Core\venv\Scripts\python.exe`), GPU only (`n_gpu_layers=-1`; the harness aborts if a model does not land in VRAM).

```
fetch_model.py qwen3emb06 / qwen3rerank06        # in Mark_II_Testing/: pinned commits, SHA-256 verified -> models/ (gitignored)
bench_embedders.py selftest                      # metric sanity checks (no models)
bench_embedders.py embed                         # embeds every text each eval needs, cached by SHA-1 under $EMB_TMP/cache
bench_embedders.py rerank scifact                # Qwen3-Reranker-0.6B scores for the 0.6B top-20; run as several processes
bench_embedders.py rerank tools memory           #   (one per subset, each with its own cache file; merged by `eval`)
bench_embedders.py rerank general vault
bench_embedders.py eval                          # all metrics -> results.json (numpy only, ~30 s)
bench_embedders.py latency                       # VRAM / load / latency / throughput, one model alone on the GPU, run LAST and serially
report.py                                        # tables.md (the doc's tables are copied from it)
```

Files: `memgen.py` (invented memory data, seeded), `general_pairs.py` (200 authored query/passage pairs), `dump_tools.py` (tool names + descriptions -> `data/tools.json`), `data/`, `results.json`, `latency.json`, `tables.md`, `ctx1024_check.json`, `reranker_sanity.json`.

Evals: A tool routing, B memory recall, C vault retrieval (private vault, aggregates only), D general retrieval (+ SciFact slice), E sensitivity (instruction prefix x centering). Variants: A = per-job instruction + centering + BM25/RRF (+ title), F = frozen 0.6B + trained routing head, C = 0.6B top-20 -> reranker, plus a 0.6B top-20 -> 4B re-rank upper bound. Headline numbers are English-only; Hinglish is reported separately.

Privacy: `$EMB_TMP` (default: the job tmp dir) holds the embedding caches and per-case vault ranks; nothing from the vault is written under the repo. Ties are scored against the gold item. Seed 20261001.
