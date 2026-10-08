# Code review of Mark I (P0 to P7) and what was fixed, 2026-10-08

Six conservative reviews (P0+P2, P1, P3, P4+P5, P6, P7) were run by Sonnet agents, read-only. Their findings were checked against the code before anything was changed. Only clear, low-risk fixes were made. Anything that changes behaviour you would notice, or needs a measurement, is in the second table for your decision. After the fixes: 90 of 90 self-checks and tests pass. Nothing was run live on the microphone, screen or GPU, so the fixes are checked by self-checks, not by living with them.

## Fixed

| # | Area | Problem found | Fix |
|---|------|---------------|-----|
| 1 | Night jobs (`memory_bridge/jobs.py`) | The abort when you come back was swallowed by the frozen runners' `except Exception`, so a night run carried on | `Aborted` is now a `BaseException`; self-check uses a runner that swallows exceptions |
| 2 | Night jobs | If loading the embedder failed, the GPU window was left open | The window is marked open straight after `window_open()`, so the `finally` always closes it |
| 3 | Night jobs | A failing `window_close` was silent and the run counted as done | Reported (`close_error`), run counted as failed and retried |
| 4 | Night jobs | A failed run waited the full 4 h gap before retrying | A failed run retries after `jobs_retry_s` (30 min) |
| 5 | Night jobs | Closing FRED mid-run left the live models unloaded | `stop()` closes the window if it is open |
| 6 | Night jobs | At start-up FRED counted as "quiet", so a run could begin at once | Start-up counts as a turn; quiet is counted from there. Wake and clock events also count as activity |
| 7 | Playground (`playground/server.py`) | If one model failed to reload on window close, the rest were never reloaded and the window could not be retried | Each slot reloads on its own; the stash is kept until all are back |
| 8 | Playground | Any web page you visit could load/unload models or rewrite `models.json` (CORS `*`, no origin check) | CORS headers removed; POSTs with a foreign Origin get 403 |
| 9 | HUD server | Any web page could POST to `/command`, even answer a yes/no | Origin must match the Host, and `Content-Type` must be JSON (checked: 200 / 403 / 403 / 200) |
| 10 | Voice (`audio/tts.py`, `engine/live.py`) | A filler already speaking delayed the real answer by seconds, or played after it | The filler has its own cancel, set the moment the reply is ready |
| 11 | Voice | Fillers and interim lines had the 1.5 s lead-in and 1.0 s tail every time | No tail for quiet lines; the lead-in is skipped when audio ended less than `tts_preroll_gap_s` (3 s) ago |
| 12 | Pill and HUD | A filler or interim line ending hid the pill mid-turn; a late end after a barge-in hid it mid-sentence | Quiet lines are ignored by the pill/HUD, and an end only counts while the state is "speaking" |
| 13 | Reminders, silent setup | With no speech a due reminder was lost (consumed, never shown) | The pill shows it as a caption |
| 14 | Engine history | A multi-line reply that was fully spoken was compared by exact text, so the sirified spoken text overwrote history | Compared with whitespace normalised |
| 15 | Scam watch | Real sites (amazonaws, github.io, google.co.in, paypalobjects, discordapp, icicidirect) and look-alike substrings (`purchase-orders` contains "chase", `apply` ~ "apple") raised a spoken phishing warning | Real domains added; substring and typo matching only for brand names of 6+ letters; regression cases added |
| 16 | Scam watch | A brand look-alike plus any one of "password / PIN / urgent" was "high" | High needs credentials AND pressure (or a disguised executable) |
| 17 | Scam watch | "Toll free 1800..." or "call support" alone was a tech-support scam | A fear phrase AND a phone number are required |
| 18 | Warnings | Medium warnings were spoken too | Only high is spoken; medium is toast and caption only |
| 19 | Ask delivery | The 10 s was counted from the reply being ready, so after a long spoken reply the question came 2 s later | Counted from the last spoken word (`tts.end`) |
| 20 | Ask delivery | "yes, set a timer" or "sure, what's the weather" was swallowed as an answer | Only replies of 3 words or fewer count; answer window 300 s to 90 s |
| 21 | Ask delivery | Pair questions read their choices twice | The choices are added only if the text does not already contain them |
| 22 | Alerts | "Disk under 10 GB or under 5 %" fired on a 2 TB drive with 100 GB free | GB only (`alert_disk_pct` removed) |
| 23 | Routing (`engine/classify.py`) | "thanks, now open spotify" and similar were routed as small talk with no tools | The small-talk shortcut applies only to 3 words or fewer |
| 24 | Engine loop | A retry that succeeded left the old failure in place, so the true reply was replaced by the old error | A success clears that tool's earlier failures |
| 25 | Lockdown | A spoken PIN "1, 2, 3, 4" never matched "1234" | Digit-only phrases compare without spaces |
| 26 | Memory feed | While locked, the spoken "unlock <passphrase>", tool results and screen text were archived | Not archived while locked |
| 27 | Reminders | "Cancel the timer" found nothing (timers have no text) | Matching includes the kind |
| 28 | Memory recall | A "yes" hours later confirmed a pending name alias | The pending question expires after 60 s |
| 29 | Notes editing | Every edit turned an LF file into CRLF on Windows | Line endings are kept |
| 30 | Config | A tuple env var (`FRED_LOCKDOWN_TOOLS=time,calculate`) became single letters; two settings were defined twice | Comma-split; duplicates removed |
| 31 | Tool log | A failed call wrote its argument values (message text, memory content) to `tools.jsonl` | Argument names only |
| 32 | Files | `find_files` could open `.exe`, `.bat`, `.lnk` and other programs without a yes | Programs always ask first, with the model's short reason read out ("This program will run, to install the driver. Yes or no?"). No reason, no question: the model is told to give one. It cannot skip the question by passing the path itself |
| 33 | Time | All stored times and day buckets were UTC; you are on IST | Everything is stored and bucketed in India time (`+05:30`) through one door (`to_local`); end-of-day stamps fixed; backup file names stay true UTC. The 57 timestamps in the real memory were converted after a backup (`FRED_Backups\pre_ist_*`) |
| 34 | Tests | New regression checks: `tests/test_review_fixes.py`, plus new cases in the jobs, tts, scam watch, ask and voice self-checks | 90 of 90 pass |

## Not changed: your decision, or it needs a measurement

| Area | Finding | Why I held back |
|------|---------|-----------------|
| Privacy | The live event log writes every event to `FRED_DATA/events/*.jsonl` in clear (transcripts, replies, screen events), never pruned. The overview says logs keep kinds and counts only | Switching it off or trimming it changes what you can debug with. Your call: turn off, or keep kinds and counts and add a retention limit |
| Privacy | `engine/loop.py` logs full `asked` and `reply` for every turn, including locked chat; the note-edit summary carries up to 50 characters of old/new text | Same decision |
| Presence | Camera presence is still not started in the live program | Keeps the camera on; yours to decide |
| Voice | The preroll/postroll apply once per utterance at 1.5 s/1.0 s; a real measurement of first-sound latency is still needed | Needs a measurement on your headphones |
| Voice | `filler_after_s` 0.8 s has no basis; better set from measured first-sound latency | Same |
| Turn clock | The 0.9 s hard cap cuts off a long thinking pause; no measurement behind the value | Replay the offline clock set with 0.9, 1.2 and 1.5 before changing |
| Turn clock | `headphones` is never set, so speaker echo can make FRED cancel itself | Needs a setting or device detection |
| GPU | A running glance holds the single GPU worker, so a short command waits for it | Design change (take glance off the queue); needs a measurement |
| Skills | `find_files` reindex runs inside the first search of the day (can take longer than the tool's 60 s) and nothing calls `reindex_all` at night | Wiring change in the night jobs |
| Skills | `forget` asks to confirm "forget nothing" when the item is not found | Needs the preview to resolve the item first |
| Skills | The web "ambient digest" (untrusted) goes into the system message; `web_search` and `weather` can carry private text out | Design change (role of the message, `sends` flags) |
| Skills | `claims_completed_action` treats "Start by chopping..." as a completion claim and widens the tool menu | Small, but touches the gates; needs its own test |
| Skills | Confirm window of 60 s is short for the email flow; stray "now/please/okay" count as yes | Behaviour change |
| Skills | `days_ahead` has no code guard; "at 9" after 9 am is refused rather than rolled to 9 pm | Needs a design choice |
| Proactive | Judge: `in_turn` falls to False 8 s after the last state event; "critical" and "reminder due" stay true for minutes | Shadow-only today; fix when the judge goes live, with a "handled" flag |
| Proactive | Missed reminders each announce "Sorry, I missed this one" in their own thread; removal happens before the event (a crash loses it) | Small; combine into one line later |
| Proactive | Judge threshold 0.6 and the 0.90 accuracy come from 154 invented cases; cooldowns and caps have no measured basis | Treat as placeholders; set from the real shadow log |
| Proactive | The scam watch combines a URL in one window with a word in another (whole-screen OCR) | Needs per-window text |
| Docs | Docstrings that no longer match: `turn_controller` (hard cap 1.2 vs 0.9), `hud_feed` (old contract), `tts` ("preroll costs no time"), `days_until`, `web_search` (0.5 vs 0.6), `loop.py` (TTS "later (P4)") | Cosmetic; do with the P8 polish |
| HUD | The timer pod reads a sqlite file that no longer exists; the HUD mute/device buttons send ordinary turns | Needs the HUD wired to the new scheduler |
| Playground | Proxy timeout 3600 s keeps a slot busy for up to an hour; mid-stream read errors are not wrapped in `ModelError` | Needs per-call timeouts decided |
| Persona | An `edit_note` change to the profile or persona file is not seen until restart | Say so, or reload on change |
| Frozen | The ask queue says "Quick one, sir:" regardless of `sir_rate` | The memory modules are frozen (bugs only) |
