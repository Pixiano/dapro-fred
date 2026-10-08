Progress snapshot at about 70% (P0 to P5 built, P6 built in shadow mode, 2026-10-08). The earlier detail is in `20_percent.md` (P0 to P1), `35_percent.md` (P2 to P3) and `40_percent.md` (skills and the brutal test); it is not repeated here.

## The idea
Old FRED was one big program where listening, looking, thinking and tools all knew about each other. The rebuild keeps every layer separate, each with a clear input and output and its own self-check. With P4 and P5 FRED gets a voice and a face, and with P6 it starts to act on its own, but only in ways you can see and stop.

## P4: the voice
- **Speech:** Kokoro runs on the CPU at normal speed. It starts speaking sentence by sentence, so the first sound comes about 0.8 s after the reply is ready.
- **Fillers:** if a reply takes longer than 0.8 s, a 2 to 3 word filler ("On it.") plays first.
- **Barge-in:** talking over FRED stops it in about 30 to 40 ms. Only the words you actually heard are written into the conversation history, so a follow-up never relies on something you did not hear.
- **Bluetooth:** a short lead-in and tail stop headphones from clipping the first and last words.
- **Lockdown:** it swaps the 4B brain for the small talker, which is then the only model loaded. The brain comes back on unlock, and that reply waits a few seconds for the load.
- **Test:** 81 of 81 checks passed at the end of P5.

## P5: the pill and the HUD
- **The pill:** a small floating window that shows listening, thinking and speaking, with your words as a caption. It has a cancel button (stops speech and thinking), a tick button (stops speech only) and a type box for typed messages.
- **Screen handling:** it stays sharp on scaled displays, follows the monitor you are working on, and re-asserts itself on top every second. When the ask box appears it takes the keyboard.
- **Cancel:** cancelling a turn stops it between model calls, drops it from the history and sends no event.
- **HUD:** a browser page shows the same feed and has a command console.
- **Limit:** a game in exclusive full-screen cannot be overlaid. The Windows toast carries warnings there.
- **Review:** an agent reviewed the pill and its findings were fixed (scaling, monitors, focus, cancel). The look on your real screen is still unverified.

## Scam watch
- **How it works:** it reads the screen text with Windows OCR (no model) and recognises lookalike login pages and spoofed senders. Spaced-out web addresses are handled.
- **Warning:** a Windows toast whose sender is "FRED", a red pill caption and a spoken line. At most 6 warnings an hour and one per domain per 30 minutes.
- **Test:** 21 of 21 on real OCR of rendered invented pages; the 3 scam pages warn and everything else, including a genuine login page, stays quiet.
- **Vision model:** Qwen3.5-4B stays the screen model (18 of 20 on the hard screen test). MiniCPM-V scored 9 of 20 and was deleted.

## Notes editing (experimental, off unless a notes folder is set)
- **How it works:** you say "update my city in my profile". The 4B proposes a find-and-replace, code checks reject guesses, FRED reads a one-line summary and you say yes or no. An undo copy is kept before anything is written.
- **Code checks:** two similar entries (two Sams), too many lines changed, a removed heading or header, and a full stop you did not say.
- **Evaluation on invented files:** find-and-replace beat rewriting the whole file (0 of 10 on memory files, it drops the header). Thinking mode was far worse (10 of 30, with 19 of 30 hitting the length cap). With the checks, 26 of 30 overall and 8 of 10 on memory files, with no false blocks and 4 wrong edits that slip through, which is why your yes or no is required.

## "Sir"
- **Code:** a function adds ", sir" to about 85% of replies, interim lines and talker lines at the output edge. It is never put into the conversation history, or the model would start adding it itself.
- **State:** it is switched off (rate 0) until the P8 tuning. P8 also puts it into the talker's prompt.

## P6: proactive
- **Reminders and timers:** set, list and cancel by voice, saved to disk and spoken when due. A reminder missed while FRED was off is announced as late. Tested live on the 4B, 6 of 6. The 4B does not know today's date, so it gives a clock time plus a number of days ahead and code builds the date.
- **Ask queue:** 10 s after a conversation ends FRED may ask one either/or question it is unsure of, spoken and in the pill. A spoken answer is recorded and never reaches the 4B.
- **Spoken scam warnings:** the watch now runs in the live program and says its warnings aloud.
- **The judge (shadow mode):** it decides whether FRED should speak unprompted but only logs the decision. Hard rules first, then an embedding classifier on the CPU with no extra VRAM, speaking only above a 0.6 score. On 154 invented cases: the talker model alone 0.675 accuracy, the classifier alone 0.82, rules plus classifier 0.90 with 2.1% wrong interruptions. The log keeps event kinds only, never text.

## Open items
- Still unverified on your hardware: the pill's look, scaling above 100%, a second monitor, full-screen games, and the real mic and speaker flow end to end.
- The judge has only seen invented cases and speaks nothing until you have reviewed the shadow log.
- No keyboard shortcut for the type box, no spoken progress narration of long tool runs, no on-request digest.
- Wake word, voice ID and presence figures are unchanged from the 40% report.

## What this means for real-life FRED
- **It can now:** hear you, answer aloud with a pill on screen, keep reminders, warn about scam pages, ask its memory questions after a chat, and edit a note with your approval.
- **Not yet:** nothing speaks unprompted except reminders, scam warnings and the ask question. No nightly memory jobs (P7) and no 8-hour soak (P8).
