Progress snapshot at about 80% (P0 to P7 built, the P6 judge still in shadow mode, 2026-10-08). The earlier detail is in `20_percent.md`, `35_percent.md`, `40_percent.md` and `70_percent.md`; the day-to-day usage is in `Mark_I/Documentation/2026/10/08/usage_so_far.md`. This one adds only what changed since the 70% report.

## The idea
The rebuild keeps every layer separate, each with a clear input and output and its own self-check. Since the 70% report the proactive layer got its last parts, and the memory now looks after itself overnight. After this, only the soak, tuning and cut-over remain.

## The judge, corrected
- **Training data:** the judge is trained on the 154 invented cases only. I first tried the previous FRED's session logs (31,484 rows) and then undid it: the "proactive" speech in them was startup greetings, welcome-backs, stand-up reminders, headphone-switch and "while you were away" announcements from fixed rules. They were never judgment calls, so there are no real labels to learn from.
- **What teaches it:** the Mark I shadow log after 2 to 3 days of real use. Until you have reviewed it, the judge speaks nothing.
- **Numbers (invented cases):** talker alone 0.675 accuracy, classifier alone 0.82, rules plus classifier 0.90 with 2.1% wrong interruptions. These show the design works, not how it will do on you.

## Critical alerts
- **What raises one:** an unplugged battery at 10% or less, or a system drive with under 10 GB or 5% free.
- **Limits:** one alert per kind every 30 minutes. A desktop has no battery, so only the disk applies there.
- **Use:** the judge's rules treat a critical alert as "speak", which is still only logged.

## P7: nightly memory jobs
- **What runs:** consolidation of every finished day (Qwen3-8B), hygiene, the weekly review, maintenance, the monthly review with a personal-model rebuild, the yearly review, the quarterly self-analysis (gpt-oss-20b) and a weekly backup. Backups go outside the project, to `FRED_Backups`.
- **GPU window:** the first model call opens the window (the live stack is unloaded and the embedder loaded for the memory index). When the run ends the window closes and the live stack reloads, also when a stage fails.
- **Cheap nights:** a model loads only when a stage actually has work, so a night with nothing to do never touches the GPU.
- **When it runs:** after 10 minutes without a turn or speech, and only when you are away. With no camera reading, that means 01:00 to 06:00 only. At least 4 hours between runs, and a retry after 30 minutes if a run was aborted.
- **If you come back mid-run:** it stops at its next model call, the window closes and FRED returns. The unfinished day or week is simply done next time. A single long model call, such as the yearly review, cannot be cut short.
- **Live test on an invented week:** 3 days and 1 week went through the real playground window in 445 s. The 4B and the embedder were loaded again at once afterwards, and the week finished as done. A first attempt exposed a missing log folder, which is fixed.
- **Not run live:** monthly, yearly and quarterly use the same code but think for many minutes.
- **Tests:** 89 of 89 pass.

## Docs
- The usage document and the README index entry for 2026-10-08.
- The progress reports were revised to follow the style of the 20% report.

## Open items
- Still unverified on your hardware: the pill's look, scaling above 100%, a second monitor, full-screen games, and the real mic and speaker flow end to end.
- The presence sense is built but not started in the live program, so the nightly jobs wait for the 01:00 to 06:00 quiet hours. Starting it keeps the camera on, so it is your call.
- The yearly festival-date recompute is not scheduled; it needs a source for the movable dates.
- Open: the on-request digest, spoken wording for judge lines, a keyboard shortcut for the type box, tool-stage narration, "sir" (switched off until P8) and the P8 polish items.

## What this means for real-life FRED
- **It can now:** everything in the 70% report, plus it tidies, summarises and backs up its own memory overnight, and is back with the live models loaded when you return.
- **Left:** P8. An 8-hour soak with flat VRAM, tuning (judge thresholds after the shadow review, talker size, "sir" at 85% and in the talker prompt), then the cut-over to `Mark_I/Core`.
