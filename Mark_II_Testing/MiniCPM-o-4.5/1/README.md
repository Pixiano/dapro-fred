# Mark II testing — Candidate 6 (base MiniCPM-o 4.5), try 1

Added 2026-09-30 by Vatsal. Same procedure as candidates 1, 3, 5.

Status: **import running** (official `openbmb/MiniCPM-o-4_5` @ `503e7542`,
20.0 GB). Nothing on the GPU; waiting for Vatsal's go.

## Why it matters

- **Venus = MiniCPM-o 4.5 with only the LLM post-trained** (paper: "only the
  Thinker was post-trained"). Same SigLIP2 + Whisper-medium + CosyVoice2 +
  Qwen3-8B, same remote-code API (`as_duplex`, `streaming_prefill`,
  `streaming_generate`). So this is the **control for Venus**: every
  difference between the two is what Venus's training added (turn-taking,
  delegation, interruption) — or cost.
- Natively **full-duplex and proactive** (1 Hz speak/listen decision, per
  the model card) — the only candidate besides Venus that can speak on its
  own. Also has a turn-based `chat()` like 2.6, so it can be scored both ways.

## Reuse (disk ~41 GB free after the download)

| Needed | Reused from |
|---|---|
| env (transformers 4.51.x, minicpmo-utils, bnb, Kokoro, openwakeword) | candidate 1's venv — the card pins `transformers==4.51.0`, torch ≤ 2.8 (Venus uses 4.51.3 / 2.8 cu128) |
| NF4 loader, duplex loop, live script, logs, analysis | candidate 1's `smoke_nf4.py` / `live_duplex.py` / `analyse_live.py`, pointed at these weights |
| benchmark adapter | `VenusOmni` (duplex) + a `chat()` turn mode like `MiniCPMo` |

Expect Venus's fixes to carry over (Token2wav stub, sliding window, speak
threshold, focus crop); verify each rather than assume.

## Layout

| Path | What |
|---|---|
| `models/MiniCPM-o-4_5/` | official checkpoint (gitignored) |
| `../../fetch_model.py minicpmo45` | download/restore at `503e7542` + SHA-check |

## What the Venus paper says about base 4.5 (arXiv 2609.13814, read 2026-09-30)

Full-Duplex-Bench v1.5 / Table 2 / Table 6, base 4.5 vs Venus-Omni:

| | Base 4.5 | Venus-Omni |
|---|---:|---:|
| responds to user interruption ↑ | 0.60 | 0.60 |
| responds when user talks to others ↓ | 0.18 | **0.06** |
| responds to background speech ↓ | 0.16 | **0.12** |
| resumes after backchannel ↑ | **0.95** | 0.92 |
| ProactiveVideoQA | **47.5** | 46.2 |
| StreamingBench / OVO-Bench / Daily-Omni | 67.9 / 60.7 / 79.4 | **70.2 / 64.7 / 81.3** |

- **Delegation is Venus-only**: Venus added `<delegate>`/`<backend>` tokens
  (6% of 2.8M training samples). Base 4.5 has no delegation mechanism
  (moot for us: Venus delegated 0/8 anyway).
- Venus's gain is mostly **ignoring speech not aimed at it** (0.18 → 0.06),
  not answering more often; base may be slightly more proactive, and worse
  in a TV/noisy room.
- Not covered by the paper (only our tests can answer): answer rate when
  Vatsal speaks unforced, inventing unknowable facts when forced, small
  screen text, prompted tool use on the base.
- Correction: our notes had Venus-Omni interruption response at 75% — that
  is Venus-Audio; Omni is 0.60 (fixed in the Venus README/review).

## Ecosystem + successor check (agent, 2026-09-30)

- **Tool calling:** Qwen3 `<tool_call>` template is in the tokenizer but
  undocumented and not exposed by any official server; a community project
  (github.com/AlessandroBonomo28/Minicpm-o-booking-desk) reports native
  calling fails — "the model invents the answer instead of calling" — and
  uses a side LLM + state machine + injected `force_speak`/`force_listen`
  (0.5–1.2 s turns). Same pattern as Venus's delegate harness and DuplexOmni
  (arXiv 2606.09186): **tools stay in FRED, driven from outside the model.**
- **Turn-taking evidence:** Duplex-MPE (arXiv 2609.31948, 2026-09-25) —
  4.5 best of 5 open duplex models on when-to-speak / stay-silent / stop.
- **Official tech report:** arXiv 2604.27393 (Omni-Flow). LoRA via
  LLaMA-Factory/SWIFT doesn't reproduce the time-aligned duplex training →
  fine-tuning may damage duplex.
- **Deployment options:** our NF4-PyTorch path (proven on this card by
  Venus, same architecture: 7.6–8.9 GB; the official Linux demo's >28 GB is
  unquantized); fallback **llama.cpp-omni** (github.com/tc-mb/llama.cpp-omni,
  official, Windows, full duplex, Q4_K_M ~9 GB) — no Blackwell report yet.
  Prebuilt `ericleigh007/MiniCPM-o-4_5-BNB-Int4` exists; no NVFP4.
- **Successor:** none announced (no o 5 / 4.6 / V 5 teaser). Recent: V 4.6
  (2026-05-11, 0.8B edge), MiniCPM5-1B/2B (text). Omni cadence: o 2.6
  2025-01-14 → o 4.5 2026-02-03 (~12.5 months) → next omni likely ~Q1
  2027 (inference). Not worth waiting for; keep the backbone swappable
  (`fetch_model.py` + scripts outside `models/`).
