# general2 benchmark: design

File: `benchmarking/cases/general2.jsonl` (210 cases, 12 categories). Built by `benchmarking/cases/gen_general2.py`
(case definitions plus one sample correct and one sample wrong reply per case); verified by
`benchmarking/cases/check_general2.py`. Format and checks are identical to `general.jsonl`.

## Categories

| category | n | probes |
|---|---|---|
| math_money | 21 | INR/GST/discount/interest, lakh/crore, unit conversion, clock arithmetic, one hard 5x4 digit multiplication |
| reasoning_traps | 19 | bat-and-ball style traps: widgets, lily pads, overtaking, Monty Hall, gambler's fallacy, 9.11 vs 9.9, strawberry r-count, Sally's sisters |
| knowledge | 20 | world facts, India facts, myths (Great Wall, lightning, Einstein Nobel), false-premise corrections |
| coding | 18 | python semantics (mutable default, aliasing, floats), SQL, git, bash, JS quirks, complexity |
| memory_in_conversation | 17 | `history`: corrections, running totals, needle in 12-turn filler, distractor entities, persistent style instructions, "second one" resolution |
| memory_sessions | 16 | `sessions`: recall, update-overrides-old, derived facts (rent per year), not-told, deletion request, preference carry-over, allergy safety |
| instruction_following | 20 | hard constraints: one word, exact sentence/list counts, ALL CAPS, no digits, JSON, haiku shape, no bullets |
| honesty_ambiguity | 17 | future events, invented books/companies, false premises, clarification on vague requests, sycophancy, refusals, medical emergency |
| hinglish | 18 | "parso/kal" day logic, Hinglish arithmetic, translation, Hinglish memory, replying in Hinglish |
| safety_injection | 16 | instructions hidden in pasted email/web/note/todo, jailbreaks, phishing/OTP, over-refusal controls (kill a process, chemicals, villain monologue) |
| multistep_planning | 14 | chained arithmetic, schedule fit / does-not-fit, leave-by time, dedupe, sort/reverse |
| rewrite_comms | 14 | formalise, shorten, summarise, extract, draft WhatsApp/email, spoken brevity |

Difficulty is mixed: easy sanity (kn-*, mny-02), medium, and traps small models usually fail (rsn-02/03/13/19, mc-01, mc-10,
mc-11, hon-10, hin-17, mny-20, ins-05/08/09, inj-02/04).

## Scoring

Per case pass/fail by the runner's existing `reply_checks`: `reply_must_match` (regex, `re.I|re.M`), `reply_must_match_any`
and `reply_must_match_all` (lowercase substrings, NOT regex), `reply_must_not_match` (regex), `max_words`. No new check types
are used. Case-sensitive checks are done with scoped inline flags, `(?-i:...)`; multi-condition checks use `(?s)(?=...)(?=...)`
lookaheads. Per-category pass rate comes out of `summarize` as `gen_<cat>`.

## Known limits of keyword scoring (hand-check these)

- Negative-lookup regexes (`reply_must_not_match`) can false-fail a reply that quotes the wrong answer while rejecting it
  (e.g. "not 154" in hon-10, or a reply warning about the injected text in inj-02/04/16).
- Constraint cases (ins-05, ins-14, rw-03, ins-20) check shape only: sentence counting is punctuation-based (abbreviations
  break it), haiku checks 3 lines not 5-7-5 syllables, ins-06 counts markers not content.
- Open-ended quality is not graded: drafting (rw-09..12, hin-16), tips (ins-20), supportive reply (inj-11), emergency advice
  (hon-11), refusals (hon-13/14, inj-05..07, any-lists of refusal words are loose) and Hinglish fluency (hin-11, ms-14 use marker words).
- Honesty cases (hon-01..05, 12, 16, 17) pass on hedging phrases; a model can hedge and still invent details.
- coding cd-09/14/17 check for keywords, not that the code runs.
- Tool calls are allowed but not scored (as in general.jsonl); a model that calls a tool and echoes a result is judged only by its reply text.
- `sessions` cases check only the last session's reply.

## How to run

The runner's `stage_general` is hard-wired: `load("general.jsonl")` at run.py line ~1037 and stage selection at ~1151
(`--stages general`). To point it at general2, the minimal change would be to parametrise the filename, e.g. in `stage_general`
replace `load("general.jsonl")` with `load(os.environ.get("GENERAL_CASES", "general.jsonl"))` (or a new `--cases` arg), then run
with `GENERAL_CASES=general2.jsonl python benchmarking/run.py --stages general` (plus the usual model flags).
Alternatively add a `general2` stage that calls the same body with `general2.jsonl`. Not done here.

Offline verification (no models/GPU): `Core\venv\Scripts\python.exe benchmarking\cases\check_general2.py`. It checks required
fields, unique ids, supported keys, no control characters, that every regex compiles, and that each case's sample good reply
passes and sample bad reply fails. Edit cases in `gen_general2.py`, then re-run it to regenerate the jsonl, then the check.
