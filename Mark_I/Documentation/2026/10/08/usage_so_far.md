# What Mark I can do so far, and how you would use it (2026-10-08)

Status in one line: about 80% built (P0 to P7 built; P8 soak, tuning and cut-over remain). Nothing has run on the real microphone and screen since P4/P5, because FRED stays down until the revamp is finished, so everything below was proven with fakes, recorded audio, invented data and live tests on the models, not by living with it. Progress reports: `Mark_I/Core/Progress/` (20, 35, 40, 70 percent, and the 80 percent one).

## Starting it
- Start the playground (`Mark_I/Initial/playground/server.py`), then `python engine/live.py [seconds] [--memory] [--silent] [--no-pill] [--no-hud] [--no-screen]`.
- It loads the 4B brain, the embedder and the 2B talker. `--memory` turns on the long-term memory, the ask queue and the nightly jobs.

## Talking to it
- Say "Hey FRED, ..." or hold Left Ctrl + Left Alt. A floating pill shows listening, thinking and speaking, with your words as a caption.
- A short filler ("On it.") plays if a reply takes more than 0.8 s. Talking over FRED stops it in about 40 ms, and only what you heard is remembered.
- The pill has a cancel button (stops speech and thinking), a tick (stops speech only) and a type box for typed messages. A browser HUD page shows the same feed.
- Lockdown swaps the 4B for the small talker until you unlock.

## What you can ask for (skills)
- **Time and numbers:** the time and date, days until a date, calculator and unit conversion.
- **Computer:** speaker volume (set, up, down, mute), CPU/GPU/RAM/disk stats, open an app, find a file.
- **Reminders and timers:** "remind me in 20 minutes to check the oven", "set a timer for 10 minutes", "remind me tomorrow at 9 to call the dentist", "what reminders do I have", "cancel the dentist reminder". They survive a restart, and a missed one is announced as late.
- **Memory:** remember, recall, forget and correct things.
- **Web and screen:** web search, weather, "what is on my screen".
- **Email:** read mail, and write a draft from a one-line gist that opens for your review (nothing is sent without your yes).
- **Notes editing (experimental, only when a notes folder is set):** "update my city in my profile" gives a one-line summary, you say yes or no, an undo copy is kept.
- **About itself:** what it can do, and its own docs.

## What it does by itself (proactive)
- **Reminders and timers** are spoken when due.
- **Scam warnings:** it reads the screen text and warns about lookalike login pages and spoofed senders with a Windows toast from "FRED", a pill caption and a spoken line.
- **Ask queue:** 10 s after a conversation ends it may ask one either/or question about something it is unsure of; you answer by voice or in the pill, or ignore it.
- **System alerts:** a battery at 10% or a nearly full system drive raises a critical alert.
- **Judge (shadow mode):** logs whether it would have spoken up, but speaks nothing. After 2-3 days of real use I review the log and retrain it.
- **Nightly memory jobs (P7):** when you are away or it is the small hours, the memory is consolidated (days, weeks, months, a yearly review, a quarterly self-analysis), tidied and backed up. The live stack is unloaded for the heavy models and comes back afterwards; if you return mid-run it stops and restores FRED.

## Not available yet
- "Sir" in about 85% of replies: the code is there but switched off until the P8 tuning.
- Spoken digest ("what happened while I was away"), spoken wording for judge lines, the judge actually speaking.
- Keyboard shortcut for the type box, spoken progress narration of long tool runs, a contacts source for email, agenda capture.
- Headphone auto-switch and full self-documentation (P8 polish).

## Honest limits
- Not verified on your hardware: the pill's look, scaling above 100%, a second monitor, full-screen games (nothing can overlay them; the toast carries warnings), and the real mic/speaker flow end to end.
- The 4B misses about 2 of 10 note edits, so read the summary before saying yes. It does not know today's date, which is why reminders take a clock time and "days ahead".
- The judge has only invented training cases; the old FRED's logs were checked and are not usable for this.
- Presence (camera) is built but not switched on in the live program, so the nightly jobs run only when you have been quiet for 10 minutes and it is between 01:00 and 06:00.
- Wake word, voice ID and presence accuracy are unchanged from the 40% report (wake word catches 78.6% of real "Hey FRED", with false fires on real speech; voice ID is off).
