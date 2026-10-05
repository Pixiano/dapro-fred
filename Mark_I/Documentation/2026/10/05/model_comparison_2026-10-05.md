# Local model comparison, 2026-10-05 (trimmed, thin evidence)

Setup: synthetic year (invented teenager "Arin"), judged against the answer key (`testdata/synthetic_year/truth.json`). Analyst role = the weekly agent for the week ending 2026-04-26 on the answer-key memory (it holds all five real contradictions as open review items). Nightly role = the first five days. Every model loaded at 20k context on the 16 GB GPU alongside the Qwen3 4B embedder; a first attempt with LM Studio's default small context was thrown away because replies were cut off. Time caps: 20 min per analyst run, 8 min per nightly run. Quants are what LM Studio downloaded by default (Qwen3.5-9B about Q4, 6.55 GB; K2 Horizon Q4_K_M, 5.59 GB), not Q8.

## Baseline
gpt-oss-20b, medium effort, four weeks with all fixes: 2 of 5 real contradictions ruled real (maths date, peanut), 1 pattern, 96 min for 4 weeks (about 24 min a week). VRAM about 15.7 of 16.3 GB.

## Analyst role (one week, 20 min cap)
| Model | Finished | Real contradictions ruled real | Inferences | Calls / minutes | Note |
|---|---|---|---|---|---|
| gpt-oss-20b (baseline, 4 weeks) | yes | 2 of 5 | 3 (1 pattern, 2 one-off events) | 138 / 96 | slow but completes |
| Gemma 4 12B QAT | no, hit the cap | 0 of 5 (all closed as historical_change or apparent) | 2 (both one-off events) | 37 / 19 | about 30 s a call, 23 searches, never reached finish |
| Qwen3.5-9B (~Q4) | no, hit the cap | 0 of 5 (none ruled yet) | 1 (one-off event) | 13 / 19 | about 14 tokens a second, 60 to 250 s a call: too slow |
| K2 Horizon 7B (~Q4) | not tested | - | - | - | LM Studio answers "Failed to load model" for it (the HTTP 400 in the runner was this, not the request settings; with and without reasoning_effort and the JSON schema). Likely an architecture the installed runtime does not support yet; not checked further |

## Nightly role (first five days, 8 min cap)
| Model | Days done | Note |
|---|---|---|
| Qwen3-8B (current) | 5 of 5 in 9 min | 20 persons, 10 tasks, 19 relationships, 3 review items |
| Gemma 4 12B QAT | 2 of 5 | about 210 s and 9,000 tokens a day: thinks long, too slow and too verbose |
| Qwen3.5-9B (~Q4) | 0 | first day did not finish inside 8 min |

## What it means
- No candidate beat the current models on this slice; none was close inside the time caps. This is a thin test (one week, five days), not a verdict on the models. Gemma 4 and Qwen3.5 may do better with more time, but at 30 to 250 s a call they are not a drop-in replacement for either role.
- The weak spot is shared: every model closes real contradictions as "historical_change" or "apparent". That points to the task design (the agent is asked to judge), not only the model. See the open decision in `done.md`.
- The default models were not changed.
- Next, if wanted: give each candidate the full four weeks and a full nightly month with no time cap (several hours each), and try K2 Horizon after an LM Studio runtime update.
