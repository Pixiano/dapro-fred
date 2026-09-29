# Candidate 1 — Realtime-Venus-Omni (official, NF4) — verdict

Try 1, 2026-09-27 → 2026-09-29. Evidence: `README.md` (this folder),
`docs/2026/09/28/done.md`, `docs/2026/09/29/done.md`, `live_events.jsonl`
runs, `readtest.py`, `skip_threshold/`, `review_venus_for_fred.md`.

## Verdict

**Viable as FRED's "ears, eyes and turn-taking" layer — not as a drop-in
replacement.** It runs realtime on the RTX 5060 Ti, sees and hears
continuously, and stays stable for hours. But on its own it rarely decides
to answer Vatsal, its vision is low-resolution unless helped, and it has no
tool use: FRED's tools, confirmation gates and memory would sit behind it,
reached through delegation (not built yet).

## What works (measured)

| Area | Result |
|---|---|
| Runs on 16 GB, GPU only | NF4 LLM + BF16 encoders: 7.3 GB weights, 7.6–8.9 GB in use; no spill in 7.8 h |
| Realtime | 1 s units: median 0.37–0.65 s, listening ~0.5 s, speaking ~0.75–0.95 s |
| Stability | 7.8 h overnight: no crash, VRAM flat, context capped (~5000 tokens), lock-screen grabs skipped |
| Voice (Kokoro on GPU, FRED's voice) | first audio 0.1–0.4 s after Venus starts replying |
| Screen reading (small text) | whole screen 0/9 exact reads → **8/9 with the native-res focus crop** |
| Seeing changes | forced replies describe what just happened accurately ("opened a new terminal window", "closed the settings menu") |
| No random chatter | 0 unprompted replies in 7.8 h with the 0.5 speak threshold (was 20.6/h) |
| Proactivity when asked for | alert rule test: it announced a video starting, unprompted (p 0.65) |
| Frame skipping | calibrated tile-max similarity 0.990; ~37% of idle seconds skipped |

## What doesn't (measured)

| Problem | Evidence | Status |
|---|---|---|
| **Rarely answers on its own** | 10/24, 13/41, 0/3 turns answered; "speaking to me" signal barely above idle; no `--listen-scale` fixes it without chatter | Worked around: hold Ctrl+Alt / "hey FRED" forces a reply |
| **Wake word unreliable** | 19 false wakes overnight → invented replies ("a message from your mother"); 0 detections with the TV on | Wake now needs speech after it; model frozen → chord is the reliable path |
| **Invents what it can't see** | confident wrong numbers when text is unreadable; a terminal called "a video chat with two people" (overview-only frame) | Crop helps small text; big-change frames stay low-res |
| **Standing prompt rules hijack answers** | alert rule → "a video clip is playing on his screen" to unrelated questions | Alert is opt-in only |
| **Felt delay** | 1 s decision rhythm + ~0.65 s compute before any voice | Structural; CUDA graphs parked |
| **Own voice not realtime** | native talker 1.43 s per 1 s | Replaced by Kokoro |
| **No tools / delegation wired** | `<delegate>` harness exists upstream; FRED's gates must stay in FRED | Not built |
| **Shares the GPU badly** | Recognize face scan 02:00–04:00 → units up to 19 s, 43 min backlog | Stale-audio drop added; scheduling is Vatsal's call |
| Forced replies can repeat | "Hello, Vatsal. How can I help you today?Hello, V…" | Open |

## Fit as FRED's backend

- **Better than FRED today:** always-on screen + voice awareness, hears tone
  and background instead of transcripts, trained interruption handling, no
  wake-word dependency for context, one model deciding when to talk.
- **Worse than FRED today:** answering reliability (FRED answers every
  hotkey press), tool routing (FRED 78–83%; Venus delegation 76% on its own
  benchmark and it under-delegates), no memory, hallucination on unclear
  input.
- **Integration shape if chosen:** Venus = perception + turn-taking + voice
  text; FRED = tools, confirmation gates, memory, via Venus's
  `DelegateBackend` protocol (plan/execute/oralize); hold-to-talk chord as
  the primary "talk to me"; Kokoro as the voice. Checklist: `README.md` →
  "Integration checklist".

## Scores to beat for the next candidates

Answered-turn rate (unforced), false unprompted replies/hour, small-text
exact reads, forced-reply first-audio latency, VRAM, and overnight
stability — same scripts (`live_duplex.py`, `analyse_live.py`,
`readtest.py`) so every candidate is measured identically.

## Not tested

Delegation/tools, long-video memory module, camera as the video source,
benchmark suite adapter (`benchmarking/run.py`), full-precision (BF16/FP8)
behaviour, the CUDA-graph speed-up.
