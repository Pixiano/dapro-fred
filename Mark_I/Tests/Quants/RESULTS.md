# Quant comparison, 2026-10-13

Live quants (brain Qwen3.5-4B Q6_K, talker MiniCPM5-2B Q8_0, embedder Qwen3-Embedding-0.6B Q8_0) against Q4_K_M builds, same prompts, one model on the GPU at a time. Script: `run_quants.py <embed|talk|brain>`; full replies in `results_*.json`. Small invented test sets, so read differences of a few points as noise. The Q4 embedder is a community quant (Mungert, imatrix); the official Qwen repo only ships Q8 and f16. Q4 files are in `models/` (git-ignored).

## Embedder Q8_0 vs Q4_K_M
| | Q8_0 | Q4_K_M |
|---|---|---|
| Skill examples ranked own-skill first | 173/173 | 173/173 |
| Invented recall@1 / @3 (15 questions over 30 facts) | 15/15, 15/15 | 15/15, 15/15 |
| Server VRAM (8192 ctx) | not measured (playground) | 1714 MB |
Same text, Q8 vs Q4 vectors: mean cosine 0.62, minimum 0.12; top-5 neighbours overlap only 46%. Both do the tasks equally well, but the vector spaces differ a lot, so switching means re-embedding all memory and retraining `judge_model.json`. The file is 0.24 GB smaller; not worth it.

## Talker MiniCPM5-2B Q8_0 vs Q4_K_M
8/8 facts both; average 25.8 vs 25.5 words on facts and 18.9 vs 21.4 on chat; no empty replies, no cut-offs, no tool markup in either. Q8 about 120 tok/s (Q4 not measured by the private server). VRAM: Q4 1873 MB (8192 ctx). Replies read as equivalent (side by side in `results_talk.json`).

## Brain Qwen3.5-4B Q6_K vs Q4_K_M
| | Q6_K | Q4_K_M |
|---|---|---|
| Right tool called (cases the router put in its top 5, 173 cases) | 154/173 | 111/173 |
| 8 general facts | 8/8 | 8/8 |
| Chat answers | normal | normal, 2 slightly less specific |
| Speed | 74 to 80 tok/s | 89 to 97 tok/s |
| Server VRAM (16000 ctx) | n/a | 3385 MB |
Q4 loses 43 tool calls to Q6's 19, mostly "calculate" prompts where it answers without calling the tool (12 times 8, 17 percent of 300, 45 plus 38). Facts and chat stay equal. Q4 is faster and about 1.1 GB smaller, but the tool misses are the thing FRED cannot afford.

## Verdict
Keep the brain on Q6_K. The talker could go to Q4 (about 1 GB freed) with no measured loss. The embedder is not worth changing.
