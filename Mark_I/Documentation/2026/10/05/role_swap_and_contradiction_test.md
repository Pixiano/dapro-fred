# Role swap (Qwen3-8B <-> gpt-oss-20b) and the mini contradiction test, 2026-10-05

Small slices on the invented "Arin" year, answer-key memory for the analyst runs. All code fixes up to commit 2602550 were in. Not a full benchmark.

## Role swap
| Role | Model | Slice | Result |
|---|---|---|---|
| Nightly | Qwen3-8B (current) | 2026-01-05..01-14 | about 2 min a day, 25 events, 20 persons and 10 tasks, 7 review items, no failed day |
| Nightly | gpt-oss-20b, medium | same days | 7.5 min for 9 days, 23 events, 14 persons and 2 tasks, 0 review items, 1 failed day (2026-01-07: one state entry with an empty field and value; now dropped by the code instead of failing the day) |
| Weekly | gpt-oss-20b (guard test) | week ending 2026-04-26, all five real contradictions open | 63 calls, 58 min, 4 of 5 ruled real (maths: unknown), 0 inferences |
| Weekly | Qwen3-8B | same week and data | 29 calls, 5.8 min, 4 of 5 ruled real (maths: historical_change), 7 inferences, all one-off events from the tech-fair week, no true pattern |
| Monthly | gpt-oss-20b, high | March (earlier run) | about 48 min a month, 2 turning points recorded, both right |
| Monthly | Qwen3-8B | March | 45 calls, 9.6 min, 5 near-duplicate turning points all dated 03-16 about the tech fair (the real ones, the coffee quit and the Dad fight, are missing), 3 chapters |

## Mini contradiction test (four invented two-day scenarios about "Mira", own temp memory)
| Scenario | gpt-oss-20b (medium) | Qwen3-8B |
|---|---|---|
| dog age: "turned 4 today" vs "got him in grade 2" (planted) | missed | missed |
| plane: "never flown" vs "the flight to Pune" (planted) | flagged | missed |
| school: "Zoya goes to Greenfield" vs "same school, same section as me" (planted) | missed | missed |
| control: "I hate mushrooms" vs "I think I like them now" (a change of mind) | flagged | not flagged |
| planted contradictions caught | 1 of 3 | 0 of 3 |

## Reading
- With the code guards in (a note that says "contradict" cannot close an item as apparent, a contradiction needs cited events), the weekly rulings no longer depend much on the model: both models reached 4 of 5.
- Speed: Qwen3-8B as the analyst is about ten times faster (6 min against 58 for the week, 10 min against about 48 for a month) but writes junk: one-off events as "inferences", duplicate turning points. gpt-oss is slower and better at turning points.
- gpt-oss on the nightly role is faster than Qwen3-8B and gives a cleaner memory (fewer entities, no review noise). It needs the JSON schema the runner now sends, and it caught one more planted contradiction than Qwen3-8B (still only 1 of 3).
- Nightly contradiction flagging is weak in both models. The weekly agent catches most of what the nightly queue holds, but it can only rule on items the nightly stage flagged; a contradiction the nightly stage misses is never seen.
- No default was changed. Options for Vatsal: gpt-oss nightly (try it on a month), keep gpt-oss for monthly and yearly, and only use Qwen3-8B weekly if duplicate inferences and turning points are filtered.
