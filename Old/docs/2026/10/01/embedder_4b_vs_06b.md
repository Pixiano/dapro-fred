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

## Large tool-routing test

Added 2026-10-01 after the n=65 tool-routing result (-4.6 R@1, CI about +-10) could not separate the configs. Harness: `Mark_II_Testing/Assembled_FRED/Embedder_Testing/tools_large.py`; results `tools_large.json`, dev tuning `tools_large_dev.json`, utterances `data/tools_large_utterances.json` (invented content only). Nothing in `Core/` was edited; `Core/data` (the frozen real-utterance holdout) was not read.

**Configs (exactly three).** (1) 4B Q4_K_M with FRED's generic query instruction, no centering (today). (2) plain 0.6B Q8_0, same config. (3) 0.6B with a tool-specific query instruction + centering on the 110 tool vectors. No reranker, no tuned 4B.

**Data.** 439 English tool utterances written for this test (every one of the 110 tools has at least 3; imperative and question forms, short spoken style, typos and ASR slips such as "op" for OTP and "R.A.M.", email-vs-WhatsApp and read-vs-send pairs, calendar/tasks/reminders, near-duplicate tools, argument-heavy requests) plus 75 negatives (chit-chat, general knowledge, opinions). Gold is any-of (a few items accept two tools). 21 candidate utterances were dropped because they matched a `TOOL_EXAMPLES` phrase or a `benchmarking/cases` utterance verbatim. Split 50/50 by category (12 functional groups), seed 20261001: DEV 220 positives + 37 negatives, TEST 219 positives (99 distinct gold tools) + 38 negatives. All TEST numbers below are the TEST half only; nothing was tuned on it.

**DEV tuning (config 3 only).** Four instruction wordings x centering on/off (8 cells), picked by DEV MRR. Centering helped every wording (+7 to +9 pts R@1). Frozen: W2 + centering = "Given a short spoken command to a PC voice assistant, retrieve the tool whose description matches the action the user wants carried out". Tuned-on DEV numbers (R@1 / R@5 / MRR, n=220): config 1: 78.6 / 95.0 / 0.860; config 2: 75.9 / 95.9 / 0.839; config 3: 85.0 / 98.6 / 0.905. DEV is optimistic for config 3 because it was selected there (85.0 on DEV vs 78.5 on TEST); DEV is also an easier half for all three.

### TEST results (n=219; bootstrap 95% CIs, 4000 resamples)

| metric | (1) 4B today | (2) 0.6B plain | (3) 0.6B + recipe |
|---|---|---|---|
| R@1 | 73.5 [67.6, 79.5] | 71.2 [65.3, 77.2] | 78.5 [73.1, 84.0] |
| R@3 | 90.9 [86.8, 94.5] | 85.4 [80.8, 90.0] | 92.2 [88.6, 95.4] |
| R@5 | 94.1 [90.9, 96.8] | 90.0 [85.8, 93.6] | 95.9 [93.2, 98.2] |
| MRR | 0.825 [0.784, 0.864] | 0.796 [0.750, 0.839] | 0.862 [0.824, 0.898] |
| End-to-end tool reach (dispatcher -> cues -> embedding menu, floor 0.65) | 69.9 [63.9, 75.8] | 68.5 [62.1, 74.4] | 73.5 [67.6, 79.5] |
| False trigger, embedder-only path (n=38, floor 0.65) | 0/38 | 0/38 | 0/38 |
| False trigger, any path | 3/38 (7.9%) | 3/38 | 3/38 |

Gap to config (1), points [95% CI] and exact paired test on the same 219 utterances (A right / B wrong vs the reverse):

| gap | R@1 | R@3 | R@5 | MRR (pts) | top-1 paired | top-5 paired | reach gap | reach paired |
|---|---|---|---|---|---|---|---|---|
| (2) - (1) | -2.3 [-7.3, +3.2] | -5.5 [-10.0, -1.4] | -4.1 [-7.8, -0.9] | -2.9 [-6.4, +0.6] | 16 vs 21, p=0.51 | 3 vs 12, p=0.035 | -1.4 [-3.2, +0.5] | 1 vs 4, p=0.38 |
| (3) - (1) | +5.0 [-0.9, +11.4] | +1.4 [-1.8, +4.6] | +1.8 [-0.9, +4.6] | +3.7 [0.0, +7.5] | 30 vs 19, p=0.15 | 7 vs 3, p=0.34 | +3.7 [+0.5, +7.3] | 12 vs 4, p=0.077 |
| (3) - (2) | +7.3 [+1.4, +13.2] | +6.8 [+2.7, +11.0] | +5.9 [+2.7, +9.6] | +6.6 [+2.5, +10.6] | 30 vs 14, p=0.023 | 15 vs 2, p=0.002 | +5.0 [+2.3, +8.2] | 12 vs 1, p=0.003 |

### Reading the table

- **Plain 0.6B vs 4B (today): a tie on top-1, a small real loss deeper in the list.** R@1 -2.3 (CI crosses 0, p=0.51), but R@3/R@5 are -5.5/-4.1 with CIs excluding 0 (top-5 paired p=0.035). End-to-end reach differs by one-to-four utterances (p=0.38).
- **0.6B + recipe vs 4B (today): point estimates favour the 0.6B (+5.0 R@1, +3.7 MRR), but R@1 does not clear noise** (CI includes 0, p=0.15). End-to-end reach +3.7 pts has a CI that just excludes 0 (+0.5 to +7.3) while the paired test does not (p=0.077). Honest reading: no worse than the 4B; "better" is suggestive, not established at n=219.
- **The recipe is a real gain over plain 0.6B** (+7.3 R@1, p=0.023; R@5 +5.9, p=0.002; reach +5.0, p=0.003). The configuration lever is again larger than the model-size lever.
- **Selection caveat:** wording and centering were chosen on DEV; the DEV-to-TEST drop (85.0 -> 78.5) shows part of the DEV gain was selection. The TEST numbers are unbiased for these frozen settings.

### False triggers and the 0.65 floor

- No negative reaches the floor for any config: top-1 cosine >= 0.65 on 0/38 negatives (95% upper bound about 9% at n=38). Median top-1 on negatives 0.26 / 0.32 / 0.29 vs 0.54 / 0.58 / 0.62 on positives. Score separation (AUROC of top-1, positives vs negatives): 0.982 (4B), 0.954 (plain 0.6B), 0.941 (recipe). The 4B separates tool from non-tool best, but all three are good.
- The 3 any-path false triggers (7.9%) are the same 3 negatives for every config and come from FRED's cue regexes, not the embedder.
- The floor 0.65 is very conservative on the embedding-only path: it admits only 12.8% (4B), 18.7% (plain 0.6B) and 41.6% (recipe, because centered scores sit higher) of positives. Reach on the 39 embedding-only test utterances is 12.8% / 7.7% / 17.9%, on the 159 cue-matched ones 83.6% / 83.0% / 87.4%; the 21 dispatcher cases are 71.4% for all (embedder not involved). The floor is therefore not the 0.6B's problem, but it leaves reach on the table for every model.
- Secondary, floor re-calibrated on DEV negatives only (95th percentile of DEV-negative top-1): floors 0.371 / 0.466 / 0.412; reach rises to 88.6 / 85.8 / 86.8% with embedder-path false triggers of 2.6% / 5.3% / 5.3% on TEST negatives (1, 2, 2 of 38; any path 10.5 / 13.2 / 13.2%). Indicative only (n=38).
- The shipped 0.65 floor is safe for false triggers with the recipe's centered vectors (0/38), but it was never calibrated for centered scores.

### Per-category (TEST, R@1 %; R@5 is in the JSON)

| category | n | 4B today | 0.6B plain | 0.6B + recipe |
|---|---|---|---|---|
| system/info | 27 | 96.3 | 81.5 | 96.3 |
| audio/display | 22 | 86.4 | 86.4 | 86.4 |
| windows/apps | 19 | 73.7 | 78.9 | 84.2 |
| files | 29 | 69.0 | 58.6 | 75.9 |
| email | 14 | 78.6 | 57.1 | 78.6 |
| messaging/phone | 28 | 64.3 | 75.0 | 82.1 |
| reminders/tasks/agenda | 29 | 62.1 | 65.5 | 62.1 |
| self/vault/lockdown | 25 | 68.0 | 64.0 | 60.0 |
| ac | 9 | 77.8 | 77.8 | 88.9 |
| web/agents | 8 | 37.5 | 62.5 | 75.0 |
| dev | 5 | 80.0 | 80.0 | 100 |
| vision | 4 | 100 | 75.0 | 75.0 |

By style (4B / plain / recipe): plain (n=87) 81.6 / 77.0 / 81.6; question (34) 64.7 / 67.6 / 70.6; typo/ASR slip (18) 77.8 / 66.7 / 83.3; confusable/short (41) 63.4 / 61.0 / 75.6; argument-heavy (39) 71.8 / 74.4 / 79.5. Most cells have n under 30, so differences of one or two items are noise. The weak spots common to all three are reminders/tasks/agenda (62-66) and self/vault/lockdown (60-68), where tool meanings overlap.

### Tools most often confused (top-1 wrong; gold -> predicted)

- 4B today: lockdown_engage -> confirm_lockdown_lift (3), search_email -> read_email (3), web_search -> delegate_research (2), web_search -> get_current_time (2), set_brightness -> adjust_brightness (2), search_files -> find_file_smart (2), send_message -> read_messages (2), list_contact_tiers -> send_message (2).
- Plain 0.6B: search_email -> read_email (3), lockdown_engage -> confirm_lockdown_lift (2), add_task -> complete_task (2), check_email -> read_email (2); most-predicted wrong tools: read_email, create_text_file, call_phone.
- 0.6B + recipe: read_file -> open_vault_file (2); the rest are singletons (launch_application -> lockdown_engage / open_path, web_search -> delegate_research, set_input_device and set_output_device -> list_audio_devices); its wrong predictions are the most diffuse of the three.
- Recurring near-duplicate pairs across configs: search_email / read_email / check_email, lockdown_engage / confirm_lockdown_lift, web_search / delegate_research, get-vs-set-vs-adjust pairs (brightness, volume, AC), search_files / find_file_smart. These are description overlaps that an instruction cannot fully separate.

### Caveats

- Utterances were written by one author (the same one as the harness) and are tidy typed text, not real transcripts; real speech and the real-utterance holdout may be harder. The 18 typo/ASR items are a small slice.
- Gold labels are judgments: several near-duplicate pairs (add_task vs add_agenda_item, search_files vs find_file_smart, set_alarm vs schedule_reminder) are only partly separable from the utterance; any-of gold covers only the obvious ties, so all configs are penalised alike on arguable cases.
- TEST n=219 gives a single-config CI of about +-6 points and a paired-gap CI of about +-6; R@1 gaps under about 6 points are ties unless the paired test says otherwise. Per-category cells (n 4 to 29) are descriptive only.
- Negatives n=38: false-trigger rates have wide intervals (0/38 means an upper bound near 9%).
- Reach is dominated by FRED's regexes, not the embedder: 159 of 219 test utterances are cue-matched, 21 reach the dispatcher (6 of those 21 fire a different tool than gold, identically for all configs). Embedder differences in reach are diluted.
- The 4B is the shipped Q4_K_M, the 0.6B is Q8_0, as in the earlier benchmark. The metric code has a self-test (`tools_large.py selftest`, which also runs the base `selftest`).

### Verdict

The 0.6B Q8_0 with the tool-specific instruction + centering is **safe for tool routing**: on 219 held-out utterances it is at least as good as the 4B running FRED's current settings (R@1 78.5 vs 73.5, not significantly different; reach 73.5 vs 69.9) with no embedder false triggers on 38 negatives. The plain 0.6B ties the 4B on top-1 but loses about 4 to 6 points at R@3/R@5, so the recipe carries the safety, not the model swap alone. A routing head or fine-tune is **not required** to match today; it remains the route to go further, since every config sits at 70-80% R@1 and the errors are overlap pairs (email read/search, lockdown, web/research) that a trained head or sharper tool descriptions address more cheaply than a larger embedder. Revisit only if the real-utterance holdout shows the same ceiling.
