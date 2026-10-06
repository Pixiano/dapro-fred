# 2026-10-06: Core P1b (screen sense) built

Plan: `Initial/Core/PLAN.md` P1 1b. Code in `Initial/Core/senses/`; sockets in each docstring, self-checks in `tests/run_all.py`. Camera/presence (1c) is next.

## Modules
| File | Behaviour |
|---|---|
| `senses/screen.py` | `ScreenSense`: capture (mss, primary monitor) on a fixed schedule (no drift, overruns counted not queued), `TileDetector` (every `screen_stride`-th pixel, green channel, 64x36 tile grid, tile similarity = 1 - mean abs diff / 255, frame score = the lowest tile, changed when < `screen_threshold` 0.990), union bbox in native pixels, `crop()` at native resolution (+`crop_pad_px`), `png()`; first frame / resolution change = baseline only; frames only in memory, events carry numbers never pixels; grab errors are one `screen.error` per 30 s |
| `senses/describe.py` | `Describer` on the playground **vision slot** (Qwen3.5-4B + its image projector): generic description or a question answered from the screen (two short sentences, thinking off by default, `think=True` available); the grounding rule `ground()`: quoted spans and multi-digit numbers must appear in the OCR text of the same crop, otherwise replaced by "(unverified text)" / listed in `dropped`; with OCR off the result is `verified=False` with the quoted spans listed |
| `senses/ocr.py` | the OCR socket, **off** (`ocr_enabled` False) until Vatsal approves CPU use: `read()` returns None; enabling it before an engine exists raises `OcrUnavailable` (never an empty string, which would read as "no text on screen"). The Windows.Media.Ocr engine itself is NOT written yet |

## Capture-period benchmark (Vatsal's request: 0.5 s and the other intervals, resources and the discrepancy larger gaps cause)
`python tests/bench_screen.py` (1920x1080, tile grid 64x36, stride 2).

**A. Resources, live real screen, 25 s each:**

| interval | ticks | CPU (% of 1 core) | RAM growth | grab | detect | overruns |
|---|---|---|---|---|---|---|
| 0.5 s | 50 | 1.0 | 0.3 MB | 19.2 ms | 0.8 ms | 0 |
| 0.7 s | 36 | 0.7 | 0.2 MB | 19.2 ms | 0.8 ms | 0 |
| 1.0 s | 25 | 0.5 | 0 | 19.6 ms | 0.8 ms | 0 |
| 1.2 s | 21 | 0.4 | 0 | 20.5 ms | 0.7 ms | 0 |

No GPU, and RAM is one frame (~8 MB at 1080p). The cost is the GDI grab (~19 ms); detection is under 1 ms. Going from 1.2 s to 0.5 s costs about 0.6 % of one core. So the interval is NOT a resource question; the cost that scales is what runs per change event (the describer, ~1 s of GPU each), which is controlled by debounce, not by the capture period.

**B. Discrepancy, synthetic timelines on the invented fixtures, 80 random phases per cell, real detector.**

*Flash* (a toast on screen for D seconds): chance any change event fires.

| D | 0.5 s | 0.7 s | 1.0 s | 1.2 s |
|---|---|---|---|---|
| 0.2 s | 41% | 28% | 15% | 21% |
| 0.4 s | 81% | 59% | 42% | 20% |
| 0.6 s | 100% | 82% | 61% | 44% |
| 1.0 s | 100% | 100% | 100% | 81% |
| 2.0 s | 100% | 100% | 100% | 100% |

Delay to the event when seen: 0.2-0.3 s at 0.5 s, 0.3-0.5 s at 1.0 s, 0.3-0.55 s at 1.2 s.

*Steady change* (stays on screen), mean / worst delay to the event: 0.26 / 0.49 s (0.5), 0.38 / 0.69 (0.7), 0.49 / 0.99 (1.0), 0.63 / 1.19 (1.2): about half the interval on average, the full interval at worst.

*Merge* (change A, then change B g seconds later): chance the two are NOT both seen as separate events, i.e. the intermediate screen is lost (columns 0.5 / 0.7 / 1.0 / 1.2 s): g = 0.3 s: 38 / 48 / 85 / 75 %; g = 0.6 s: 0 / 14 / 35 / 51 %; g = 1.0 s: 0 / 0 / 0 / 15 %.

*Progress bar over 10 s*: 20 / 15 / 10 / 8 events, mean changed region 60 / 75 / 104 / 122 px (the unseen part per event grows with the gap).

Reading: at 1.0-1.2 s a notification that lives under 1 s is missed more often than not (a typical Windows toast lives 3-5 s, so those are fine), and quick back-to-back changes collapse. 0.5 s catches nearly everything above 0.4 s and halves the delay, for about 0.5 % more of one core. **Recommendation: 0.5 s** (`screen_interval_s`; the default is still 1.0 until you decide). Caveat: on your live screen 49 of 50 ticks at 0.5 s reported a change (an active desktop; threshold 0.990 with a single changed tile is very sensitive), so a debounce / minimum-tiles / "same region again" policy is needed before any describer call; tune in P8 on real use.

## Describer gate: the 12-screenshot set (`tests/live_describe.py`)
Cases v-01..v-12 of the old multimodal benchmark (invented screens, read in place), Qwen3.5-4B in the vision slot (load 2.6 s), thinking off, two-sentence answers: **11/12** (old baseline for this image path: 12/12), about **1.0 s per answer**. The one miss, v-06 (sum of a spreadsheet column), reads a digit wrong and gets 4,180 instead of 4,280; with thinking on it passes but takes several times longer. Arithmetic over screen values belongs to the brain; the describer's job is to read. v-12 (no hallucination) passes only with a deviation from the old rule: a negated mention ("no visible errors") no longer counts as the forbidden word "error" (the old regex tripped on correct answers). In repeat runs every other case passed every time.

## A finding that changes earlier numbers
`localhost` costs a flat **2.0 s per request** on this machine (Windows tries IPv6 first; the playground listens on 127.0.0.1 only): 2.07 s versus 0.047 s for the same call. Every live timing from the P0 gate ("chat text 2.1 s", the 2.3-2.6 s calls, 7.8 s talk slot) and this phase's first runs (3-5 s per describe) contained it. Fixed: `config.playground_url` is now `http://127.0.0.1:8090`. NOT fixed (frozen pipeline, flagged for you): `memory/run_local.py` and other memory scripts default to `http://localhost:8090/v1`; setting `LLM_URL=http://127.0.0.1:8090/v1` saves 2 s per call.

## Decisions taken (change on request)
- Detector definition: the L0 doc gave "tile-max difference, 0.990, 1 fps" from earlier measurements but no code survived in the old repo, so similarity = 1 - mean abs diff / 255 per tile and the score is the lowest tile similarity.
- OCR engine not written (waiting for your approval); `ground()` and the off-switch are in place and tested with a scripted OCR text.
- Describer defaults: thinking off, "at most two short sentences", 300 max tokens.
