# Brutal test of everything built (P0-P3 + skills), 2026-10-07

Aggregates only. The user's real usage (old logs, outside this repo's published content) was projected onto the built components; no utterance text is quoted here. Nothing unbuilt was built.

## Usage profile projected
1846 user turns (1228 distinct), median 4 words, p95 19 words, 45 active days; tool log dominated by device, screen, reminder, mute and time requests. Presence log: 98,884 readings over 42 days. Wake log: 24,016 score events over 53 days.

## Suites (all in `tests/brutal/`, orchestrated by hand, results below)
| Suite | What | Result |
|---|---|---|
| `fuzz.py` (seeds 1-3, 7; 400 rounds) | text, routing, item lookup, args, loader, engine, confirm, memory skills | 0 findings after fixes (found: a skill calling `sys.exit()` at import escaped the loader; skill names unvalidated) |
| `engine_replay.py --mode skills` | 600 real turns, live Qwen3.5-4B + 0.6B embedder, built skills only | 0 crashes, 0 unconfirmed destructive runs, p50 0.80 s, p95 2.3 s, RSS flat at 55 MB |
| `engine_replay.py --mode legacy` | same 600 turns against the old 44 tools (recording stubs) | 0 crashes, 0 unconfirmed destructive runs, p50 2.8 s, 81 of 132 labelled-and-built routed to the old label (rest mostly valid alternatives or asked for detail) |
| `memory_stress.py` (scale 2) | 8 threads x 240 turns into the archive, torn lines, 1 MB and hostile payloads, 1200-event index, model swap, 300 items churn, 150k-char graph text, garbage judge replies | 0 findings; feed lost or duplicated nothing |
| `timelines.py` | real presence weeks through the debounce, real wake scores through threshold/cooldown | see below |
| `run_all.py` | every self-check + test files | 56/56 |
| `rank_check.py --live` | each skill example routes to its own skill | 31/31 |

## Bugs found and fixed
1. `what_can_you_do` answered "do you have X?" (no) with an error; the engine treated it as a failed tool and stopped with a fault (9 of 600 turns). Now an honest "I don't have a skill for X" plus the group list. Faults 9 -> 0, tool errors 21 -> 0 on the labelled set.
2. `recall` with no query returned bare items; the model once described them as "your screen". Output now says it is FRED's saved memory of the user.
3. Loader: `sys.exit()` in a skill at import, and path-like skill names. Fixed (earlier).
4. `tests/voice_eval.py` crashed on clips shorter than `voice_min_s` (the verifier refuses to score them, correctly). Harness now reports "too short to score".

## Honest findings (not fixed, by rule)
- **Built skills cover little of real usage.** All 300 labelled requests map to tools that are no longer built. With skills only, FRED answered 56 of 300 by calling a skill anyway (memory or what_can_you_do), and the rest by chat; it never claimed an unsupported action as done (0 false completions flagged). The coverage gap is the revamp, not a defect.
- **Presence:** state flips 4.5/day; 112 of 189 flips are under 5 min apart (flapping) on this camera data; Core disagrees with the raw reading 0.7% of the time; an absence is noticed after 59 s median (49 s rule + poll), max 174 s. One long-absence case is a harness artifact (log hole inside a session, the rule needs a poll after 49 s).
- **Wake word (frozen, measured only):** at threshold 0.35 the Core replays the old log to 142.5 fires/day vs 142.4 old. Real positives: 78.6% recall; real negatives: 37.0% false fires (63.0% correct rejects); synthetic negatives 13.8% false fires. Threshold sweep 0.25 to 0.7 moves fires/day only from 160 to 81, so the scores alone do not separate.
- **Voice ID:** same speaker across recordings p5 0.66; at 0.3, 0% false reject on studio audio, 0% false accept on synthetic voices. On real live-mic 1 s clips p50 is only 0.22, so 89% would be rejected at 0.3. Not enrolled, as directed; needs the better live recordings.
- **Turn clock (Smart Turn rule B):** complete sentences 1/20 wrong turn count, median wait 0.36 s; mid-sentence pause false cutoffs 0.4 s: 15%, 0.7 s: 0-5%, 1.0 s: 5%; barge-in 5/5, backchannel 0.15 s 5/5 but 0.25 s 0/5; unaddressed speech ignored 5/5. The 0.9 s hard cap alone: 0% false cutoffs but median wait 1.05 s.
- **STT:** 4 invented sentences, WER 0-14% (numerals only), 170-220 ms each, VRAM +2.2 GB, unload frees it.
- **Vision:** 11/12 (baseline 12/12): the table-total case computed a wrong sum. OCR: 76% exact, 89% tolerant over 12 invented screens (weak on dense diagrams, low contrast, terminal).
- **Screen capture:** 0.5 s polling resolves 0.3 s change pairs only 25%; the longer the period the more events merge (table in the run output).
- **Stochasticity:** conversation behaviour varies run to run (16-19/21 earlier); this run's faults came from the brain choosing a skill, not from a crash.
