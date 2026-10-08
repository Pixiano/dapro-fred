Progress snapshot at the end of the build (P0 to P8 built, 2026-10-08). The earlier detail is in `20_percent.md`, `35_percent.md`, `40_percent.md`, `70_percent.md` and `80_percent.md`; the all-in-one user overview is `upto_80_percent.md`. This one adds only what changed since the 80% report.

## The idea
The rebuild keeps every layer separate, each with a clear input and output and its own self-check. P8 is the last build step: the review fixes, the always-on camera, "sir", and the move to the final folder. What is left needs your hardware, so it happens in live testing.

## Code review (full table: `Mark_I/Documentation/2026/10/08/code_review_fixes.md`)
- **How:** six read-only Sonnet reviews of P0 to P7. Every finding was checked against the code first; only clear ones were fixed.
- **Fixed (34):** the night-job abort that the old runners swallowed, windows left open, filler delaying the answer, pill hiding mid-turn, scam-watch false positives (real CDNs, lookalike words), ask answers swallowing real requests, lockdown archiving the passphrase, the playground and HUD servers answering any web page, and more.
- **Programs need a yes:** opening an `.exe`, `.bat`, `.lnk` or similar always asks, and the question reads out the model's short reason ("This program will run, to install the driver").
- **India time:** every stored time and day bucket is now `+05:30`. The real memory was backed up first (`FRED_Backups\pre_ist_*`) and its 57 timestamps converted.

## P8
- **Camera presence is always on.** The night jobs pause it themselves: while a run is going the camera is not polled; before a model call, at most once a minute, one snapshot looks for a face, and a face stops the run and brings the watch back. Start-up flag to skip it: `--no-presence`.
- **"Sir" is on at 0.85.** Code adds it at the output edge to replies, interim and talker lines (never to history), and the small talker is told to say it too. The 2 to 3 word fillers stay bare. Set `sir_rate` to 0 to turn it off.
- **Cut-over:** Core now lives in `Mark_I/Core`. The frozen memory pipeline and the playground stay in `Mark_I/Initial`. 90 of 90 self-checks and tests pass from the new place.
- **Festivals:** left as they are. The movable festival dates come from `memory/calendar_india.json`, and the yearly recompute (`run_alerts_pending`) still needs a date source, so it is not scheduled.
- **Event log:** left as it is (transcripts and replies stay in `FRED_DATA/events`, never pruned).

## Not done, on purpose (needs the real machine; this is the live-testing list)
- **8 hour soak** with flat VRAM, and the numbers from the Mark II master plan section 7.
- **Measure and tune:** the filler delay (0.8 s), preroll and postroll (1.5 s / 1.0 s) on your headphones, the 0.9 s turn cap against 1.2 and 1.5, the judge threshold (from the shadow log), talker 1B or 2B, the thinking cap.
- **Headphone auto-switch:** the old classifier and photos survive in `Old/Core/data`. It was wrong three times live in the old build, so it is rebuilt with you in front of the camera, not blind.
- **Full self-documentation:** `read_own_docs` reads the READMEs and progress reports today.
- **Judge:** shadow log for 2 to 3 days, then retrain on real events, then decide whether it speaks.
- **Still unverified on your hardware:** the pill's look, scaling above 100%, a second monitor, full-screen games, the real mic and speaker flow.

## What this means for real-life FRED
- **It can now:** everything in the 80% report, plus it addresses you as sir, watches for you with the camera, pauses that watch for its night work, and asks before running a program.
- **Left:** live testing, then the tuning that comes out of it.
