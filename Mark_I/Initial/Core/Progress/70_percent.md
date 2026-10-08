Progress snapshot at about 70% (P0 to P5 built, P6 built in shadow mode, 2026-10-08). Earlier detail is in `20_percent.md` (P0 to P1), `35_percent.md` (P2 to P3) and `40_percent.md` (skills and the brutal test); this one adds what changed since.

## The idea (unchanged)
Every layer is separate with a clear input and output and its own self-check. The brain is Qwen3.5-4B; a small talker model (MiniCPM5-2B) handles chat and short spoken lines; the embedder is Qwen3-Embedding-0.6B.

## What was added since the 40% snapshot
- **P4, voice out:** Kokoro speech at normal speed, started sentence by sentence (first sound about 0.8 s), a 2-3 word filler when a reply takes longer than 0.8 s, talking over FRED cuts it off in about 40 ms and only the words you heard are remembered, and a lockdown swaps the 4B for the talker.
- **P5, the pill:** a floating pill with state, caption, a type box, a cancel button (stops speech and thinking) and a tick button (stops speech only); sharp on scaled displays, follows the active monitor, stays on top, the ask box takes the keyboard. A browser HUD page shows the same feed. Full-screen games cannot be overlaid; the Windows toast carries warnings there.
- **Scam watch:** reads the screen text (no model), recognises lookalike login pages and spoofed senders, shows a toast from "FRED", a pill caption and now says it aloud.
- **Notes editing (`edit_note`, experimental, off unless a notes folder is set):** the 4B proposes a find-and-replace, code checks reject guesses (two similar entries, too many lines, removed headers, added full stops), you hear a one-line summary and say yes or no, an undo copy is kept. 8 of 10 on invented memory files; the two misses still need your yes/no to catch them. Whole-file rewrite (0/10) and thinking mode (1/10) were rejected.
- **"Sir" (dormant):** code that adds ", sir" to 85% of replies at the output edge, never to history. Default rate is 0; switch it on in P8.
- **P6, proactive:**
  - Reminders and timers (set, list, cancel by voice), saved to disk, spoken when due, missed ones announced late. Tested live on the 4B 6 of 6; the 4B does not know the date, so the skill takes a clock time and a number of days ahead.
  - The ask queue's either/or question is asked 90 s after a conversation ends, spoken and in the pill, and a spoken answer is recorded without reaching the 4B.
  - The judge runs in shadow mode: hard rules, then an embedding classifier (CPU, no extra VRAM), threshold 0.6. On 154 invented cases: talker-only 0.675, classifier 0.82, hybrid 0.90, wrongly speaking 2.1% at 0.5. It only logs event kinds, never text.

## Honest gaps
- Not run end to end on the real microphone and display since P4/P5; DPI above 100%, a second monitor, full-screen overlay and the look of the pill are unverified.
- The judge was tested on invented cases only (no real screen events existed). It must be reviewed and retrained on 2-3 days of real shadow log before it may speak.
- No producer yet sets a critical system alert, so that rule is idle. No spoken wording templates, no on-request digest.
- Wake word, voice ID and presence figures from the 40% snapshot are unchanged.

## What this means for real-life FRED
- **It can now:** hear you, answer aloud with a pill, keep reminders, warn about scam pages, edit a note with your approval, and ask its memory questions after a chat.
- **Not yet:** nothing speaks unprompted except reminders, scam warnings and the ask question; no nightly memory jobs (P7), no 8-hour soak and tuning (P8).
