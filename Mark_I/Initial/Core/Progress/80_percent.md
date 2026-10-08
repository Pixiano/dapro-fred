Progress snapshot at about 80% (P0 to P7 built, P6 judge in shadow mode, 2026-10-08). Earlier detail: `20_percent.md`, `35_percent.md`, `40_percent.md`, `70_percent.md`; the day-to-day usage is in `Mark_I/Documentation/2026/10/08/usage_so_far.md`.

## What was added since the 70% snapshot
- **Judge training, corrected:** it is trained on the 154 invented cases only. The previous FRED's logs were checked and are not usable: its "proactive" speech was startup greetings, welcome-backs, stand-up reminders and headphone/away announcements from fixed rules, not judgment calls. The real teacher is the Mark I shadow log after 2-3 days of use.
- **Critical alerts:** battery at 10% or less (unplugged) or a nearly full system drive raises a critical alert, which the judge's rules treat as "speak" (still only logged).
- **P7, nightly memory jobs:** consolidation of finished days (Qwen3-8B), hygiene, weekly review, maintenance, monthly review with a personal-model rebuild, yearly review, quarterly self-analysis (gpt-oss-20b) and a weekly backup, all through the GPU window. The model loads only when a stage has work, a night with nothing to do never touches the GPU, it runs only when you are away (or 01:00-06:00 without a camera reading), it stops and restores FRED if you come back, and it closes the window even on failure.
- **Live proof:** an invented week (3 days, 1 week) ran through the real playground in 445 s and the 4B and embedder came back at once. Monthly, yearly and quarterly runs use the same code but were not run live (they think for many minutes).
- **Docs:** the usage document and README index for 2026-10-08.

## Honest gaps
- Still unverified on your hardware: the pill's look, scaling above 100%, a second monitor, full-screen games, the real mic and speaker flow end to end.
- The judge has never seen a real day; it speaks nothing until you have reviewed the shadow log.
- Presence (camera) is not started in the live program, so the jobs wait for 01:00-06:00 quiet hours. The yearly festival-date recompute is not scheduled (needs a date source).
- Digest, spoken judge wording, keyboard shortcut, tool-stage narration, "sir" (dormant) and the polish items are still open.

## What this means for real-life FRED
- **It can now:** everything in the 70% report, plus it tidies and summarises its own memory overnight without you noticing, and comes back with the live models loaded.
- **Left:** P8, the 8-hour soak and tuning (thresholds, talker size, "sir" at 0.85 and in the talker prompt), then cut-over to `Mark_I/Core`.
