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
