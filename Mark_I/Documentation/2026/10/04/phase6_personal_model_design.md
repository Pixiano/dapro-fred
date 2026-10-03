# Phase 6: the personal model (designed with Vatsal, built 2026-10-04)

## Decisions (Vatsal)
- **Delivery: dynamic only.** No fixed profile card in the system prompt (it would crowd the prompt and make every answer about his personality). To avoid "nothing triggers the lookup", the lookup is automatic and costs no model call: for each user turn, plain code compares the turn to the model items by embedding and injects up to 3 items above a similarity cut-off (0.45, tune with the real embedder), or nothing at all.
- **Promotion is dynamic by size of claim:** light (trivial, observable) 1 week, normal (most things) 4 weeks, deep (traits, values, motivations) 8 weeks. Defaults by category: project = light, routine/decision/relationship = normal, trait_hypothesis = deep. The weekly model may raise a tier, never lower it.
- **Rebuild: light weekly + deeper monthly.** `update(llm, week, "light")` after each weekly run looks only at new or changed inferences. `update(llm, week, "full")` reconsiders everything; Phase 7 schedules it monthly.
- **Control: yes, on request.** `show()`, `correct()`, `add()`, `remove()`. FRED never recites the model unprompted. A user item is locked (no update ever changes it), and a correction rejects the inference behind it so it cannot return.

## How it works
1. Weekly inferences carry `support_weeks` and a `tier`. A candidate reaches the model only if the **code gate** passes, whatever any model says: status proposed/supported, weeks >= tier requirement, >= 2 supporting events, counter-evidence at most a third of the support.
2. A model call (small batches, JSON) decides for each candidate: include (with one short neutral wording), hold, or reject (counter-evidence wins). Inclusion is the promotion: a proposed inference becomes `supported`, including traits (the review is the authority the weekly run does not have). Confidence is clamped (0.9, deep 0.8).
3. Items whose inference later turns weakened/rejected/outdated are demoted by code, no model needed. Everything is logged (`model_item_log`, `model_versions`), nothing deleted.
4. Wording rules in the prompt: behavioural, "tends to...", hypotheses not diagnoses, no clinical labels, no details about other people.

## Failure cases
| Case | Handling |
|---|---|
| Model returns bad JSON | Batch skipped, nothing changes; next run sees the same candidates |
| Model includes an inference that did not pass the gate, or an unknown id | Ignored in code |
| Model inflates confidence | Clamped |
| Model tries to rewrite a user item | Never: user items are only reachable by `correct`/`remove` |
| Item restates an existing one | `replaces_item_id` or same inference id updates instead of duplicating |
| Nothing relevant to the turn | `relevant()` returns [], nothing injected |
| Same (week, mode) run twice | Skipped (`model_versions` unique) |

## Not yet
- Wiring `relevant()` into FRED's turn pipeline and spoken "show me / that's wrong" commands (integration).
- Staleness-based demotion; a monthly schedule (Phase 7).
- Cut-off and tier weeks are guesses until a few real weeks exist.
