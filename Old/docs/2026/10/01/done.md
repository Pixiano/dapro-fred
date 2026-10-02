# 2026-10-01 — work log

- **Assembled plan:** the architecture stays the direction, but its
  specifics aren't decided. The phased build order, including Phase 0
  pipeline fixes, is cancelled (Vatsal).
- **CPU allowed** for the turn-detection models: VAD, Smart Turn v3, voice
  ID (Vatsal).
- **Freed ~42 GB of disk** (C: 86 → 126 GB free). Deleted:
  - MiniCPM-o 2.6 weights (17 GB);
  - MiniCPM-o 4.5 weights (19 GB);
  - MiniCPM5-2B GGUF (2.5 GB);
  - Venus `models/wheels` torch cache (3.3 GB).

  All code and adapters stay in git. Restore the weights with
  `Mark_II_Testing/fetch_model.py minicpmo26|minicpmo45|minicpm5`; Venus's
  wheel is re-fetched per its README.
