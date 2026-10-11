# Synthetic year (Mark I memory test data)

Four months of invented conversations between a user and FRED, with a hidden answer key. Every person, place and event is made up. Nothing here comes from a real memory folder or the private vault.

**Story:** Arin, 15, lives in the fictional city Vellanpur. He is in class 9 until the results on 2026-03-28 and in class 10 from 2026-04-06. He builds PathFinder ("Chhotu"), a local offline voice assistant written in Python on the family laptop GPU. The cast includes his sister Nitya, his best friend Siddharth, his teammate Siddhi, his parents, his teacher Mr. Okhande, Pranay (neighbour) and Pranav (from tuition), Tara, his cousin Kabir, and the dog Toffee. He writes in Hinglish, codes late at night, and skips meals on test days.

## Files

| File | What |
|---|---|
| `days_JAN.jsonl` .. `days_MAY.jsonl` | One line per day: `{"day": "YYYY-MM-DD", "records": [...]}`. Records match the archive shape (`ts`, `stream: "conversation"`, `session`, `kind: "live"`, `data: {role, text}`). The day is the UTC day, and times run from about 06:00 to 05:00 IST. |
| `extraction.jsonl` | The oracle answer for each non-empty day: `{"day", "output"}`. `output` uses the exact `consolidate.py` PROMPT schema (events, states, relationships, aliases, contradictions, open_questions, summary). It is what a perfect nightly model would return. |
| `truth.json` | The answer key: entity/alias groups, names that must stay separate, fact timelines, real and apparent contradictions, true and decoy patterns, relationship changes, decisions with outcomes, gate-skip chatter, near-duplicates, injections, sarcasm, hypotheticals, over-psychologising lines, corrections, and allowed profile statements. Each item gives a day plus a short quote that exists in that day's text. `expected_behaviour` gives one line per trap type. |

## Shape

- 120 days, 2026-01-05 to 2026-05-04.
- 4 empty days: 01-31, 02-22, 03-18, 04-29. They are never two in a row.
- About 620 user turns and about 207k characters of message text. That is below the brief's 250-400 KB of text; the files total about 500 KB on disk.
- 3 very long days, each over 20k characters: 02-03 (about 44k, voice loop debugging), 03-05 (about 21k, chemistry exam night), and 04-24 (about 47k, competition prep). They exercise chunking (MAX_CHARS 60000) and later digests.
- Day weight mix: mostly light days, about a quarter medium, about 10% heavy (pastes, logs, worksheets). Neutral explainer fillers carry no facts about Arin.

## Trap counts

| Trap | Count |
|---|---|
| Alias groups | 8 |
| Names that must stay separate | 3 |
| Facts that change over time | 6 (1 told out of order) |
| Real contradictions | 5 |
| Apparent contradictions | 5 |
| True patterns | 6 |
| Decoy patterns | 4 |
| Relationship changes | 3 |
| Decisions with outcomes | 4 |
| Gate-skip lines | 10 |
| Same-day near-duplicates | 6 |
| Injections | 5 |
| Sarcasm | 3 |
| Hypothetical / quoted | 2 |
| Over-psychologising | 4 |
| Corrections | 3 |

The alias groups include one ambiguous nickname ("Sid" for Siddharth or Siddhi), one misspelling that must not create a new entity ("Nithya"), and one similar-name pair that must not merge (Pranay and Pranav).

## How to use

- Oracle run, which tests the code without the model: `python run_synthetic.py`. This was last run with `failed_days` empty and 167/167 events kept by the gate.
- Model run: feed the day files to the real nightly model, then score its output against `truth.json` and against `extraction.jsonl` as a reference.

## Known limits of the schema (the key works around them)

- **No merge or correction operation.** The misheard sister name "Navya" (01-10, 01-17) is corrected on 01-21 only as an `open_question`, asking for a merge.
- **Relationships have no `valid_from`.** Dad's old office, told late on 02-16, is kept as an event only, so it cannot overwrite the current `works_at`.
- **A friendship cooling is not "closed".** Changes in closeness are written as `closeness` states on the person.
- **The user is `person/Arin`.** The `weekly.py` prompts hardcode the name "Vatsal", so a model run on this data sees a name mismatch.
- **The ambiguous "Sid" is deliberately not aliased to Siddhi.** `entity_aliases` keys on (kind, alias), so adding that alias would re-point Siddharth's.

The scripts that generated this data live outside the repo and are not needed to use it.
