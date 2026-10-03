# Phase 8: deep self-analysis (design, decided with Vatsal 2026-10-05)

Goal (spec section 40): answer "What do you understand about me that I probably don't?" while keeping evidence, inference, uncertainty and speculation apart.

## Decisions
- **When:** on request, plus a quarterly run (latest completed quarter end, once per quarter). The report is saved and pulled, never injected.
- **Four labelled sections:** evidence, inference, uncertain, speculation. Each claim links to event ids. Code rejects evidence/inference/uncertain without real event ids; an inference needs events from at least two different days and has confidence capped at 0.8; speculation is capped at 0.3 and is never stored in the personal model.
- **Sensitivity:** everything is allowed, including uncomfortable patterns and blind spots (an inference can be flagged "blind spot"). Neutral behavioural wording, no clinical labels, no details about other people.
- **Personal model:** inference claims that pass the code gate (confidence at least 0.5, counter-evidence at most a third of the support) go straight into the personal model as source `analysis` (category trait_hypothesis, tier deep, confidence at most 0.8). Vatsal's remove/correct works on them like any item; a removed item is not re-added.
- **Model:** gpt-oss-20b, local, same `llm(messages)` socket as the weekly/monthly/yearly agents.

## How it runs
One tool loop over all of memory (budget 4x the weekly cap). The map is built by code: the personal model (so it adds what is not already believed), latest yearly and monthly reviews, chapters, longest-running inferences, and earlier deep findings (do not repeat). Read tools only: search, entity and event lookups, find_recurring, monthly/yearly digests, chapters, turning points, model diff, show_model. Every write tool of the other agents is refused. It can write only through `finish`.

## Failure behaviour
Unusable replies or the call cap give an `incomplete` report that is saved but never shown by `latest_report` (only `done` reports are). A failed run saves nothing and a quarterly run retries next time. Bad claims are returned once for the model to fix; on the second finish they are dropped, so a small model is never trapped.

## Tables
`self_analyses` (report), `self_claims` (section, confidence, blind_spot, counter events, the personal-model item it became), `self_claim_evidence`.
