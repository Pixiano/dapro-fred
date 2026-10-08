Everything up to 80%, in one place, written for you as the user (2026-10-08). It combines `20_percent.md`, `35_percent.md`, `40_percent.md`, `70_percent.md` and `80_percent.md`, so you can read this one file to know what Mark I is, what it can do today, how you use it, and where it is weak. For the detail behind any number, open the report it came from.

## In one paragraph
Mark I is the rebuilt FRED: a voice assistant that runs on your PC with local models, listens for "Hey FRED", thinks with a 4B model and a set of tools, answers aloud, shows a small pill on screen, remembers things, keeps reminders, warns about scam pages and tidies its own memory overnight. It is built in nine phases (P0 to P8). P0 to P7 are built, which is the "80%". What is left is P8: an 8-hour soak test, tuning, and moving the code to its final place. FRED itself has stayed switched off during the revamp, so almost everything was proven with recordings, fakes, invented data and live model tests, not by living with it.

## How the parts fit
- **Event bus:** every part announces what happened ("wake word heard", "screen changed", "reminder due") and anyone interested listens. Parts never reach into each other, so one can be replaced without touching the rest.
- **Layers:** senses (ears, eyes, camera) feed a turn clock that decides when you are done talking, then the thinker (brain plus tools), then the voice and the pill. Memory and proactivity run beside them.
- **Safety by design:** private data lives in `~/FRED_Private` outside the repo; logs keep event kinds and counts, not what you said; nothing destructive runs without your yes; after reading untrusted content (a file, screen, web page) FRED refuses to send anything out in the same turn.

## The models and what each one does
- **Brain, Qwen3.5-4B:** understands you, picks tools, writes answers. Always loaded.
- **Embedder, Qwen3-Embedding-0.6B:** matches your words to the right tool and finds memories by meaning. Always loaded.
- **Talker, MiniCPM5-2B:** answers plain chat without tools and speaks short lines while the brain works (such as "drafting that now"). Also the only model while locked down.
- **Screen model:** the same Qwen3.5-4B describes the screen when asked. The 2B vision model test is parked.
- **Whisper:** speech to text on the GPU, about 0.2 s for a short command.
- **Kokoro:** speech out, on the CPU, normal speed.
- **Smart Turn, voice detection, wake word, face detector:** small models on the CPU.
- **Night models:** Qwen3-8B for the daily memory pass and gpt-oss-20b for the weekly, monthly, yearly and self-analysis passes. They only load in the night window, when the live models are unloaded.
- **Rule:** inference always runs fully on the GPU, except the small CPU models above.

## Starting it
- Start the playground (`Mark_I/Initial/playground/server.py`), then run `python engine/live.py [seconds] [--memory] [--silent] [--no-pill] [--no-hud] [--no-screen] [--brain MODEL]`.
- `--memory` switches on the long-term memory, the ask queue and the nightly jobs. `--silent` is text only. The other flags switch off the pill, the browser HUD or the screen watch.
- Settings live in one file (`config.py`) and can be overridden by environment variables. The ones you are most likely to touch: `sir_rate`, `filler_after_s`, `tts_speed`, `jobs_*`, `judge_threshold`, `alert_*`, `voice_id_enabled`.

## A normal day, as you would experience it
1. You say "Hey FRED, ..." or hold Left Ctrl + Left Alt. The pill shows listening, with your words as a caption.
2. A turn clock decides when you are done. A pause to think does not cut you off, and a "mm-hm" does not count as a turn.
3. The brain answers or calls a tool. If the reply takes more than 0.8 s you hear a short filler first ("On it."). The first sound of a reply starts about 0.8 s after it is ready.
4. If you talk over FRED, it stops in about 30 to 40 ms and only the words you heard are remembered.
5. You can cancel with the pill's cancel button, stop only the speech with the tick, or type in the pill's text box instead of speaking.
6. Anything destructive gets a yes/no first, naming the real target. "Yeah go ahead" counts; "no wait yes" does not.
7. About 10 s after a conversation ends, FRED may ask one either/or question about something it is unsure of. You answer by voice or in the pill, or ignore it.

## What you can ask for
- **Time and numbers:** the time and date, days until a date, a calculator with unit conversion.
- **Your computer:** speaker volume (set, up, down, mute), CPU/GPU/RAM/disk stats, open an app, find a file.
- **Reminders and timers:** "remind me in 20 minutes to check the oven", "set a timer for 10 minutes", "remind me tomorrow at 9 to call the dentist", "what reminders do I have", "cancel the dentist reminder". They survive a restart, and one missed while FRED was off is announced as late.
- **Memory:** remember, recall, forget and correct facts. FRED digs deeper when you refer to the past and says so ("digging deeper").
- **Web and screen:** web search, weather, and "what is on my screen" (OCR plus the vision model, only when asked).
- **Email:** read mail, and write a draft from a one-line gist that opens for your review. Nothing is sent without your yes.
- **Notes editing (experimental, only when a notes folder is set):** "update my city in my profile" gives a one-line summary, you say yes or no, and an undo copy is kept.
- **About itself:** what it can do, and its own docs. It cannot invent abilities, because the list is built from the real skills.

## What it does by itself
- **Reminders and timers** are spoken when due.
- **Scam warnings:** it reads the screen text (no model) and recognises lookalike login pages and spoofed senders. You get a Windows toast whose sender is "FRED", a red pill caption and a spoken line. At most 6 an hour, one per domain per 30 minutes.
- **Ask queue:** the either/or question described above, limited per day and per item.
- **System alerts:** an unplugged battery at 10% or less, or a nearly full system drive, raises a critical alert (once per kind per 30 minutes).
- **Nightly memory jobs:** when you have been quiet 10 minutes and are away (or between 01:00 and 06:00 without a camera reading), FRED consolidates finished days, tidies, reviews the week, month, year and quarter, and makes a weekly backup in `FRED_Backups`. The live models unload for this and reload afterwards. If you come back mid-run it stops and restores FRED within seconds.
- **The judge:** it decides whether FRED should speak unprompted, but today it only logs what it would have done. It speaks nothing until you have reviewed its log.

## What FRED remembers
- **Everything is archived** on separate streams (conversation, tool calls, speech, perception, errors), outside the repo.
- **Each turn** gets your personal model, plain code and one short embed. The deeper search runs only when you refer to the past.
- **Overnight** the days are turned into events, people, places and projects with relationships, and weekly, monthly and yearly passes find patterns. The quarterly self-analysis keeps evidence, inference and guesses apart.
- **Doubts** (is "Ro" the same as "Rohan", a contradiction) go to the ask queue, and your answers become precedents it can use later.
- **Yearly alerts** (birthdays, festivals) exist in the memory, but they are not yet read out at the first greeting of the day.

## Where it is weak (numbers)
- **Wake word:** catches 78.6% of real "Hey FRED" and falsely fires on 37.5% of hard non-wake speech. It is frozen; real tuning waits for the recording plan.
- **Voice ID:** off. On live-mic clips the current threshold would reject about 89%.
- **Presence (camera):** says "someone", not "you", and flips more than 4 times a day on your data. It is built but not started in the live program.
- **Screen reading:** OCR gets 76% of phrases exactly (88% with a small tolerance); it is weak on dark code and terminals, thumbnails and long URLs. Vision passed 11 of 12 and 18 of 20 on the hard set.
- **Tool choice:** 91.8% on the old tool suite, 90.5% on conversation (moves between runs because the brain is not deterministic), 8 of 8 on safety and confirmation.
- **Notes editing:** 8 of 10 on invented memory files. Two of ten edits are wrong in a way the checks miss, which is why your yes/no is required.
- **Reminders:** 6 of 6 live. The 4B does not know today's date, so it gives a clock time and days ahead.
- **The judge:** trained on 154 invented cases only (0.90 accuracy, 2.1% wrong interruptions there). The previous FRED's logs were checked and cannot be used: its "proactive" speech came from fixed rules.
- **Overlay limits:** a game in exclusive full-screen cannot show the pill; the toast carries warnings.

## What has and has not been checked on your machine
- **Checked:** 89 of 89 self-checks and tests, live runs of each model-dependent feature on the playground, the night jobs on an invented week (445 s, live models back at once), real OCR on rendered pages.
- **Not checked:** the pill's look on your screen, scaling above 100%, a second monitor, full-screen games, the real microphone and speaker flow end to end since voice out and the pill were added, Bluetooth timing on your headset, and any long run.

## What is left
- **Shadow review:** 2 to 3 days of real use so the judge's log can be reviewed and it can be retrained on real events.
- **P8:** an 8-hour soak with flat VRAM, tuning, then moving `Initial/Core` to `Mark_I/Core`. Tuning covers the judge threshold, the talker size (1B or 2B), the thinking cap, switching "sir" on at 85% and adding it to the talker's prompt.
- **Polish:** headphone auto-switch, a full self-documentation system.
- **Later:** a spoken digest of what happened while you were away, spoken wording for judge lines, a keyboard shortcut for the type box, spoken progress narration of long tool runs, a contacts source for email, agenda capture, scheduling the yearly festival-date recompute (needs a date source).

## Decisions waiting for you
- Start the camera presence sense in the live program (it keeps the camera on)? It lets the night jobs run whenever you leave, not only 01:00 to 06:00.
- When to launch FRED for the shadow run, since the judge cannot be reviewed before that.
- Where the festival dates for the yearly alerts should come from.
