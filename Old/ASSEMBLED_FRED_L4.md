# Assembled FRED: L4 Proactivity (finalised as designed, 2026-10-02)

Layer 4 lets FRED speak up unprompted, only when it is useful. Parent plan:
`Mark_II_Testing/ASSEMBLED_FRED.md` section 9. Inputs come from L0 (screen,
speech, system events). Vatsal accepted the design as is; the open items below
are still to be tested and completed.

## Picks

| # | Component | Pick | Runs on | VRAM | Status |
|---|---|---|---|---:|---|
| 1 | Event log | JSONL ring of the last ~10 min, persisted to the daily log. One line per event: id, time, source (screen / speech / sys), text, how read (ocr / vlm), confidence, optional tone | CPU | 0 | to build |
| 2 | Judge | MiniCPM5-2B, non-thinking, temperature 0.2, own process (1B vs 2B decided by the L2 talker test) | GPU | shared with the L2 talker (~2.1 GB at 2B) | pending L2 test |
| 3 | Judge output | `{"speak":bool,"score":1-5,"because":[event ids],"line":"<=20 words"}` | - | - | design |
| 4 | Delivery | Score 4: text in the pill only. Score 5: spoken via `_speak_proactive` | CPU | 0 | reuses existing code |

## Cadence

On each salient event (new error text, speech not addressed to FRED, app
switch), at most every 3 s; idle ticks every 30 s. The judge sees the last 90 s
(<=600 tokens), today's context line and the open reminders.

## Scoring (a trimmed Inner Thoughts)

1. Is it about what he is doing right now?
2. Is there an information gap he would want filled?
3. Is it urgent (cost of waiting)?
4. What does interrupting cost now (typing fast, in a call, media playing)?

## Gate (all must hold)

- Score >= 4 on 2 consecutive runs.
- `because` names real event ids.
- If the event's source is VLM text, the quoted text must also appear in the OCR output, so nothing unseen is ever spoken.
- He is present, not talking, not in a call, not watching media.
- Cooldown: 10 min per topic, 3 min global (reuse `proactive_checks`' naturalness gate and state file).
- Nothing else may speak unprompted. The confirmation answer never comes from the judge.

## Failure modes it avoids

- False alarms from false wakes: no reply without a trigger, except a gated judge line.
- The alert rule leaking into unrelated answers: judge rules live only in the judge process; the answering prompt gets recent events as data with "mention only if relevant".
- Invented details: the 2B never answers a question, it only raises a flag; grounding plus OCR cross-check.

## Rollout

Shadow mode first (log only, do not speak) for 2-3 days. Tune thresholds on
Vatsal's "useful / not now" labels, then enable speech.

## Open items (to test and complete)

1. **Judge model:** MiniCPM5-1B vs 2B, from the L2 talker test (VRAM in the full stack, latency, quality on judge JSON).
2. **Cadence and thresholds** (score 4/5, streak 2, cooldowns 10/3 min): tune on real use in shadow mode.
3. **"In a call" / "media playing" detection:** not built.
4. **Dependency on L0 screen reading:** MiniCPM-V 4.6 loading in our llama.cpp build, Windows OCR accuracy on crops, the OCR cross-check rule (L0 open checks 1-3).
5. **Prompt-injection filter (on hold):** only matters for screen text the judge reads; revisit when the hold lifts (22M Prompt Guard vs Qualifire sentinel v2).
6. **Judge accuracy test:** labelled event logs (useful / not useful) to measure false-alarm rate before enabling speech.
7. **Event-log privacy:** the log persists screen text to the daily log; decide retention and what is excluded before it runs on real use.
