# FRED embedder: Qwen3-Embedding-4B (Q4_K_M, today) vs 0.6B (Q8_0, before 2026-08-03)

Nothing in FRED was changed. Harness, data and raw results: `Mark_II_Testing/Assembled_FRED/Embedder_Testing/` (`bench_embedders.py`, `results.json`, `latency.json`, `tables.md`). Run date 2026-10-01, seed 20261001.

**Standing rule applied:** English is the working language. Every headline number and the "within 3 points" check use English-only items. Hinglish items are kept, shown separately at the end, and do not drive the verdict.

## Verdict (plain language)

- **For FRED's real, English jobs the plain 0.6B is close to the 4B**: R@1 gap vs 4B is -2.4 pts on memory recall (n=245), -2.3 on general retrieval (n=172), -4.6 on tool routing (n=65), +7.3 on the vault proxy (n=151, 0.6B actually ahead), -3.3 on the SciFact slice (n=300). Only SciFact R@5 (-5.6, CI excludes 0) is a clearly significant loss. On the other four evals every R@1 gap has a 95% CI that includes 0 (vault's touches 0 at the low end). End to end through FRED's own dispatcher + cues + embedding menu, tool "reach" is 74.3% (0.6B) vs 75.7% (4B) on 70 cases, i.e. one case.
- **"Within ~3 points of the 4B" holds for at least one 0.6B variant on every eval**, including the plain 0.6B on four of five (tool routing is -4.6 plain, but A, F and C all land within 3 or above).
- **Which fix helps where.** Tool routing: all three fixes reach or beat the 4B (A 80.0 or 84.6 with the lighter recipe, F 76.1 vs 4B 77.6 on its own split, C 89.2). General/SciFact: C (Qwen3-Reranker-0.6B on the top 20) beats the 4B (+5.2 / +5.4 R@1). Memory recall: no fix beats plain 0.6B; A and C are 3-5 pts *below* the 4B on R@1, though R@5 is 100% for everyone. Vault: the plain 0.6B is already the best; C hurts it (-9 R@1).
- **Where the 0.6B still loses: Hinglish.** Memory R@1 52.7 vs 98.2 (n=55), general 78.6 vs 96.4 (n=28). C and A recover some of it (memory 76.4 / 74.5) but not all. Informational only, per the rule above.
- **Biggest finding is configuration, not size.** FRED today sends one generic instruction for all three jobs and no centering for tools. A tool-specific instruction lifts the *4B* from 75.4 to 89.2 R@1 on routing (n=65) and the 0.6B from 70.8 to 81.5. Centering helps tools slightly, but it hurts memory with FRED's generic prefix (4B 92.7 -> 86.1). So the 4B-vs-0.6B gap is smaller than the gap between the current and a tuned configuration of either.
- **Cost of the 4B:** 3.4 GiB VRAM vs 1.4 GiB, 40 vs 25 ms per short query, 155 vs 71 ms per 300-token chunk; re-indexing 1000 chunks takes 2.5 vs 1.2 min.
- **Not established:** that the 0.6B is safe to go back to. The proxy evals are small and mostly synthetic (see caveats), the historical "what are my priorities" failure did not reproduce either way, and a human should spot-check real traffic before anyone acts on this.

## Setup

| item | value |
|---|---|
| 4B (current) | `Qwen3-Embedding-4B-Q4_K_M.gguf` from `.lmstudio` (FRED's `EMBEDDING_MODEL_PATH`), hub repo `Qwen/Qwen3-Embedding-4B-GGUF` @ `f4602530db1d980e16da9d7d3a70294cf5c190be`, file size 2,496,703,776 B |
| 0.6B (previous) | `Qwen/Qwen3-Embedding-0.6B-GGUF` @ `370f27d7550e0def9b39c1f16d3fbaa13aa67728`, `Qwen3-Embedding-0.6B-Q8_0.gguf`, 639,150,592 B, SHA-256 verified against the hub LFS hash via `Mark_II_Testing/fetch_model.py qwen3emb06`. Stored in `Embedder_Testing/models/` (matched by the existing `Mark_II_Testing/**/models/` ignore rule; weights are not committable). |
| Reranker (variant C) | no official reranker GGUF exists (`Qwen/Qwen3-Reranker-0.6B-GGUF` is 404). Used the community conversion `QuantFactory/Qwen3-Reranker-0.6B-GGUF` @ `9bdee8f1ad01d7896a20823d5affd66c494eee8b`, `Qwen3-Reranker-0.6B.Q8_0.gguf`, SHA verified (`fetch_model.py qwen3rerank06`). Runs correctly in llama-cpp-python 0.3.31 (official yes/no prompt template, score = logit(yes) - logit(no) at the last token; sanity: relevant +6.8, irrelevant -11.4). Not an official artifact. |
| Loader | exactly FRED's: `Llama(embedding=True, n_ctx=..., n_gpu_layers=-1, verbose=False)`, `create_embedding(text)`; llama-cpp-python 0.3.31, FAISS 1.13.2 present, GPU only (the harness refuses to run if VRAM does not grow by about the file size). |
| Configs | 4B n_ctx 4096, 4B n_ctx 1024, 0.6B Q8 n_ctx 4096 |
| FRED's usage replicated | query prefix `Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: {q}` (queries only); tools: raw cosine over `name. description + TOOL_EXAMPLES` (FRED's own `SemanticToolRouter` and `intent.classify` loaded read-only); memory: L2-normalised inner product, no centering, content-only text, top-5; vault: corpus-mean centering, `title — heading\ntext` chunks from FRED's own chunker functions, cosine. |

**ctx 1024 vs 4096.** llama-cpp-python truncates every input to `n_batch` (512 tokens) regardless of `n_ctx`, so the two contexts give identical vectors: max abs difference 0.0 over 400 random test texts (min cosine 0.9999998). The 4B ctx1024 rows are therefore the same numbers as ctx4096 by construction; the ctx only changes VRAM (-581 MiB). Side finding: `vault_router.py` and `memory_manager.py` comments say 4096 tokens of headroom, but any text longer than 512 tokens is silently cut at 512 for embedding.

### Data and sample sizes

| eval | queries | corpus | content |
|---|---|---|---|
| A tools | 70 (65 English) | 110 tools | `routing_hard.jsonl` (64 cases with expected tools; "any expected tool" counts), 3 delegation cases from `agents_memory.jsonl`, 3 conversation tool cases. The frozen real-utterance holdout lives in `Core/data` and was not touched. 64 of 70 utterances are not verbatim TOOL_EXAMPLES phrases. |
| B memory | 300 (245 English) | 300 invented facts + 1500 distractors | generated by `memgen.py` (templated, invented names/numbers/places): numeric 88, near-duplicate pairs 58 (29 pairs, same person, different day/place), Hinglish 55, temporal 44, plain 33, negation 22. |
| C vault | 151 | 518 chunks from 127 files | private vault, aggregates only. 51 file-level queries ("what do I have about <file name words>", hit = any chunk of that file) and 100 heading-level queries (only headings made of generic structure words). Strongly lexical proxy. Plus 3 historical queries on a corpus extended with `active-priorities.md` (ranks and scores only). |
| D general | 200 (172 English) | 200 authored passages + 1000 distractors (SciFact abstracts) | fact 36, how-to 28, code 28, summary 16, Hinglish 28, near-duplicate hard negatives 24, needle-in-300-token passages 40 |
| D2 SciFact | 300 | 1783 (all 339 relevant + 1500 random) | BEIR SciFact test, `mteb/scifact` @ `cf10ab6856b15b0e670ef8ae5dae4e266c12d035` |

Ties are counted against the gold item (pessimistic) so saturated reranker scores or equal fusion scores cannot inflate a number. The metric code has a self-test (`bench_embedders.py selftest`); it also caught two real bugs during development (float32 ties after a +1e6 offset, and probability saturation of the reranker), both fixed before the numbers below.

## Headline table (English-only, R@1 / R@5, %)

| variant | A tools (n=65) | B memory (n=245) | C vault (n=151) | D general (n=172) | D2 SciFact (n=300; nDCG@10) |
|---|---|---|---|---|---|
| 4B Q4_K_M, ctx 4096 (FRED today) | 75.4 / 93.8 | 92.7 / 100 | 45.0 / 85.4 | 90.7 / 98.8 | 61.3 / 82.3 (73.1) |
| 4B ctx 1024 | identical | identical | identical | identical | identical |
| 0.6B Q8 plain, FRED config | 70.8 / 92.3 | 90.2 / 100 | 52.3 / 86.8 | 88.4 / 97.7 | 58.0 / 76.7 (69.4) |
| **A** 0.6B + instruction + centering + BM25 RRF (+ title on SciFact) | 80.0 / 93.8 | 87.8 / 100 | 51.0 / 91.4 | 65.1 / 82.6 | 58.7 / 76.7 (70.7) |
| A-light: per-job instruction + centering only | 84.6 / 100 | 89.0 / 100 | 50.3 / 85.4 | 88.4 / 98.3 | 59.0 / 79.3 (70.9) |
| **F** frozen 0.6B + trained routing head (routing only; n=67 own split, 0.6B plain 71.6 / 92.5, 4B 77.6 / 95.5 on the same split) | 76.1 / 97.0 | n/a | n/a | n/a | n/a |
| **C** 0.6B top-20 -> Qwen3-Reranker-0.6B | 89.2 / 98.5 | 87.8 / 100 | 43.0 / 84.1 | 95.9 / 99.4 | 66.7 / 82.7 (75.8) |
| A top-20 -> reranker | 90.8 / 98.5 | 87.8 / 100 | 39.1 / 81.5 | 90.1 / 93.0 | 67.7 / 85.0 (77.6) |
| 0.6B top-20 -> 4B re-rank (upper bound) | 75.4 / 95.4 | 92.7 / 100 | 45.0 / 84.8 | 91.9 / 98.8 | 62.0 / 80.7 (72.7) |

MRR for the same rows is in `tables.md`. Top-20 recall of the plain 0.6B (so the ceiling for any re-ranker): tools 97.1, memory 100, vault 96.7, general 98.5, SciFact 86.3.

### Gap to the 4B in R@1 points (0.6B variant minus 4B; negative = worse)

| eval | plain | A | A-light | F | C | best of A/F/C within 3 pts? |
|---|---|---|---|---|---|---|
| tools | -4.6 [-15.4, +7.7] | +4.6 | +9.2 | -1.5 (own split) | +13.8 | yes (all three) |
| memory | -2.4 [-6.1, +1.2] | -4.9 | -3.7 | n/a | -4.9 | no: -3.7 at best; plain 0.6B is the best 0.6B option (-2.4) |
| vault | +7.3 [0.0, +14.6] | +6.0 | +5.3 | n/a | -2.0 | yes |
| general | -2.3 [-6.4, +1.2] | -25.6 | -2.3 | n/a | +5.2 | yes (C, A-light) |
| SciFact | -3.3 [-7.3, +0.3] | -2.6 | -2.3 | n/a | +5.4 | yes |

Brackets are 95% bootstrap CIs on the plain-0.6B-minus-4B R@1 gap. Picking the best variant per eval after looking at the test set is optimistic; treat "yes" as "plausible", not proven. Honest summary for the fine-tuning decision: on memory the gap to the 4B (-2.4 to -3.7) is the only one that no tested fix shrinks, and it is inside the noise (n=245, CI -6.1 to +1.2).

## Per-category (R@1 / R@5, all items)

Tool routing: the 0.6B plain is behind the 4B mainly on typos (25 vs 75 R@1, n=4), email (71 vs 86, n=7) and args (75 vs 88, n=8); reranking (C) recovers these but gives up the Hinglish and email-vs-WhatsApp items (33 vs 67 R@1, n=3). Memory: near-duplicate facts are the 0.6B's weak spot among English items (84.5 vs 98.3 R@1, n=58; R@5 100), numeric 89.8 vs 93.2, negation tied. General: needle-in-long-passage 75.0 vs 77.5 (n=40, both weak at R@1), how-to 85.7 vs 89.3, fact 91.7 vs 97.2. Full tables in `tables.md`.

## Sensitivity (E): is part of the 0.6B's gap a configuration issue?

R@1, English-only, instruction x centering (FRED's choice marked):

| eval | 4B FRED cfg | 4B best cfg tried | 0.6B FRED cfg | 0.6B best cfg tried |
|---|---|---|---|---|
| tools | 75.4 (generic instr, no centering) | 90.8 (per-job instr + centering) | 70.8 | 84.6 (same) |
| memory | 92.7 | 97.1 (no instr + centering) / 96.7 (no instr) | 90.2 | 93.9 (no instr, no centering) |
| vault | 45.0 (centered) | 49.0 (per-job, uncentered) | 52.3 | 52.3 (FRED cfg) |
| general | 90.7 | 90.7 | 88.4 | 88.4 |
| SciFact | 61.3 | 64.3 (per-job + centering) | 58.0 | 59.0 |

Reading: (1) for memory, *dropping* the generic query instruction beats FRED's current setting for both models (+4 for the 4B, +3.7 for the 0.6B), because the stored items are conversational turns and the "search query" instruction is a mismatch. (2) For tools a job-specific instruction is worth +14 R@1 for the 4B and +11 for the 0.6B. (3) Centering is not free: with the generic instruction it lowers tools (-7.7, 4B) and memory (-6.6, 4B). So the 0.6B's gap is only partly a configuration issue (the recipe lifts the 0.6B by the same amount as the 4B), but FRED leaves a lot of accuracy on the table for *either* model. All 24 cells are in `results.json` (`sens:*`).

Heading prefix (vault chunker): removing the `title — heading` prefix drops R@1 from 45.0 to 23.8 (4B) and 52.3 to 21.2 (0.6B), though this proxy rewards the prefix because the queries contain file/heading words.

## Tool routing: what the embedder actually decides

FRED routes dispatcher first, then cue categories (the embedder only narrows/rescues), then embedding-only. Shares in this 70-case set: dispatcher 11, cue-matched 48, embedding-only 11.

| path (n) | 4B reach | 0.6B reach |
|---|---|---|
| dispatcher (11) | 81.8 | 81.8 (identical; no embedder involved) |
| cue-matched (48) | 91.7 | 89.6 |
| embedding-only (11) | 0.0 | 0.0 |
| all (70) | 75.7 | 74.3 |

Embedding-only reach is 0 for both because FRED's `SEMANTIC_FLOOR = 0.65` is met by only 16% (4B) / 17% (0.6B) of utterances; both models' score scales are similar (top-1 median 0.50 vs 0.54), so the 0.65 floor does not need recalibrating if the model is swapped. Raw ranking on the embedding-only subset (n=11): R@1 54.5 (4B) vs 45.5 (0.6B), R@5 90.9 vs 81.8.

## The historical "what are my priorities" failure

Ranks and scores only (corpus of 522 chunks = the indexed vault plus the chunks of `active-priorities.md`, FRED config, query "what are my priorities"): the best `active-priorities.md` chunk ranks **2 for both models** (4B centered score 0.390 vs top-1 0.421; 0.6B 0.396 vs top-1 0.534). Without the query instruction it is rank 3 (4B) / 2 (0.6B) centered, and rank 18 (4B) / 5 (0.6B) uncentered. "what are my machine specs": best chunk rank 2 (4B) vs 1 (0.6B). So the August failure (centered cosines of -0.002 to -0.141) is not reproduced with the Q8_0 0.6B: it is plausible that the original f16 run lacked the query instruction and the heading-prefixed chunks that were added at the same time. Cannot be settled from here; the file is now hardcoded into every prompt anyway.

## VRAM and latency (run serially, one model alone on the GPU, 200 runs each)

| config | load s | VRAM after load / after use (MiB) | short query median / p95 ms | 300-token chunk median / p95 ms | re-index chunks/s | 1000 chunks |
|---|---|---|---|---|---|---|
| 4B n_ctx 4096 | 3.4 | 3379 / 3411 | 40.3 / 48.1 | 155.3 / 171.7 | 6.7 | 2.5 min |
| 4B n_ctx 1024 | 3.3 | 2832 / 2830 | 35.6 / 38.7 | 153.0 / 168.6 | 6.9 | 2.4 min |
| 0.6B Q8 n_ctx 4096 | 2.7 | 1345 / 1366 | 24.5 / 26.1 | 71.5 / 76.0 | 14.0 | 1.2 min |

Short queries are the FRED-prefixed memory questions; chunks average 288 tokens; re-index throughput is 400 sequential `create_embedding` calls (FRED's pattern). VRAM deltas come from `nvidia-smi` before and after load (FRED's own ~3 GB of processes were present and unchanged). Reranker: +1.4 GiB, about 21 pairs/s per process.

## Hinglish (information only, not in the headline)

| eval (n) | 4B | 0.6B plain | 0.6B + A | 0.6B + A-light | 0.6B + C |
|---|---|---|---|---|---|
| memory (55) R@1 / R@5 | 98.2 / 100 | 52.7 / 85.5 | 74.5 / 87.3 | 41.8 / 74.5 | 76.4 / 100 |
| general (28) | 96.4 / 100 | 78.6 / 85.7 | 53.6 / 60.7 | 82.1 / 89.3 | 71.4 / 89.3 |
| tools (5) | 80 / 100 | 80 / 80 | 80 / 80 | 80 / 100 | 40 / 80 |

## Caveats

- **Sample sizes are small and CIs are wide.** Tool routing n=65 (a +-10 pt CI); only SciFact (n=300) and memory (n=245) are reasonably tight. Differences under about 5 pts on A/C are not distinguishable.
- **Memory and general data are templated or hand-authored by one author** (also the author of the harness), so paraphrase difficulty is moderate; real conversational turns will be messier. Memory R@5 is 100% everywhere, so the real risk is only top-1 ordering, and FRED injects all top-5.
- **Vault proxy is weak.** Queries are built from file names and generic headings, so they reward lexical overlap and the heading prefix. The 0.6B beating the 4B here should not be read as a vault-quality finding. A human should compare the two models on 20-30 real vault questions with the answer file known.
- **Selection effects:** the "best variant" per eval is chosen on the same data. Variant A (BM25 RRF) was not tuned; with equal-weight RRF it helps vault (R@5 91.4) but badly hurts paraphrase-style sets (general R@1 -23); the light recipe (instruction + centering) is the safer one.
- **Reranker is a community conversion** (QuantFactory), not an official file; its good numbers on tools/general/SciFact are real but unverified against the Qwen reference implementation beyond a two-pair sanity check. It adds a second model (+1.4 GiB; about 21 pairs/s per process with three processes sharing the GPU) per routed turn if used, which erases the VRAM saving of the 0.6B.
- **F** is trained on 145 phrases (FRED's `TOOL_EXAMPLES` plus conversation tool cases) and tested on a different source (`routing_hard` + delegation); a 5-fold cross-validation on `routing_hard` gives 76.6 vs plain 73.4 / 4B 79.7 (n=64). It cannot help memory or vault. The training phrases are also part of the tool text every model sees, so all rows share that advantage; 6 of 70 test utterances are verbatim example phrases.
- Not done: no real Hinglish/voice-transcript data (per instruction no new Hinglish data), no real memory log, no fine-tuning of the 0.6B. FRED's frozen real-utterance holdout (in `Core/data`) was deliberately not read.
- Parallelism: the three reranker passes ran as separate processes with separate caches (results independent of order); embedding passes and all latency/VRAM measurements ran with the GPU otherwise idle.
- No vault text appears in this document or in `results.json`; per-case vault ranks are kept only in the job tmp dir.
