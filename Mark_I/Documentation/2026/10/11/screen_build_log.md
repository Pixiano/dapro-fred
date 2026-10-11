# Screen reading, speak gate, deadline watch: build log (2026-10-11, design phases P0 and P1)

Built to the decisions in the task (they supersede `live_screen_design.md` where different). Nothing was run live; FRED stayed stopped.

## Built
| File | What |
|---|---|
| `Core/proactive/screenread.py` (new) | `Reader`: about once a second OCR of the FOREGROUND WINDOW only (rect from win32gui, cropped from the latest frame). The tile diff only skips the OCR when nothing changed; it never waits for stillness (a video is read every second, which replaces the F1 forced 3 s settle). A screen.change of 2 tiles or fewer (`screen_noise_tiles`) is ignored, but the window is re-read every `screen_reread_s` (10 s) and at once when the foreground window or title changes. Callbacks get text only when it changed. Texts never go to the bus or logs. Exposes `fullscreen()`. |
| `Core/proactive/gate.py` (new) | `Gate.decide(urgent) -> speak / toast / hold`; the pause switch (`paused()`, `set_paused()`: `config.screen_paused` or the file `<data_dir>/screen_paused.flag`). Rules: hold while the user talks, mid-turn (45 s max), FRED speaking or spoke < 120 s ago, user away. Quiet hours 23:30 to 06:00, start extended up to 01:00 when the user was active in the last 20 min (toast only). Fullscreen: toast only. Caps: 1 spoken offer per 10 min, 10 per day (over: toast). Urgent (deadline < 12 h) overrides quiet hours, fullscreen and the caps, never the holds. |
| `Core/proactive/deadlines.py` (new) | `find_deadlines(text, now)` (cue word within 6 words of a date phrase, same line or the next line when the cue line has no date), reminder-time maths, `DeadlineWatch`: one question per distinct deadline ever (key = line without the date phrase + due), state in `<data_dir>/deadlines.json` (asked, accepted, declined, reminders), `answer(text)` (yes/no within 40 s handled here), reminders 18:00 evening before and 06:00 on the day (12:00 weekends) through the gate, "in an hour" fallback, memory event `deadline` importance 0.6 when memory is available (optional). |
| `Core/proactive/scamwatch.py` (changed) | New `reader=` argument: fed with the foreground text through `check_text`, registered as `always=True`, so the pause switch does not stop it. It reads every window, banking and private ones included. The old settled-event path remains when no reader is given. |
| `Core/skills/extra/vision/screen_watch/` (new skill) | Voice pause: pause / resume / status (writes the flag file). Scam watch is not affected. |
| `Core/config.py` (appended) | Settings below. |
| `Core/engine/live.py` (minimal wiring) | `Runtime.deadlines` field; `handle()` asks `rt.deadlines.answer(text)` first; `main()` builds Reader, Gate, DeadlineWatch next to the screen sense and starts/stops them. |

Settings added: `screen_paused`, `screen_read_s` (1.0), `screen_reread_s` (10), `screen_noise_tiles` (2), `gate_after_fred_s` (120), `gate_turn_s` (45), `quiet_start` (23:30), `quiet_end` (06:00), `quiet_latest` (01:00), `quiet_active_min` (20), `offer_gap_s` (600), `offers_per_day` (10), `deadline_watch_enabled`, `deadline_urgent_h` (12), `deadline_answer_s` (40).

## Verified (self-checks only, invented text, fake OCR, fake clock, fake speaker and toast)
- `python proactive/gate.py`: holds (talking, mid-turn, tts, spoke recently, away), urgency, fullscreen, 10 min gap, 10 a day, quiet hours edges (23:29, 23:31, 03:00, 05:59, 06:01), the 20 min extension to 01:00, pause flag and config flag.
- `python proactive/screenread.py`: only the window rect is OCR'd, skip when unchanged, caret noise ignored, periodic re-read, continuous change read every tick, new window at once, fullscreen flag, pause (scam watch still gets text, others not), nothing read when paused with no scam watch, missing OCR stops the reader, thread start/stop.
- `python proactive/deadlines.py`: 20 positive lines (absolute and relative dates, weekdays and short weekdays, times, next-line cue, year rollover), 19 negatives (no cue, past dates, far-apart cue and date, version numbers, fractions, etc.), key stability across days, reminder times (weekday, Saturday noon, evening already past, too late), the service (ask once, answer yes/no, restart memory, evening reminder spoken, hold while the user talks, "in an hour" fallback, quiet hours toast vs urgent spoken, answer window closes, memory event).
- `python proactive/scamwatch.py` still green plus reader mode; `skills/extra/vision/screen_watch/check.py`.
- `tests/run_all.py` with a temp FRED_DATA: 101/101 passed. `engine/live.py` imports.

## Not verified
- Nothing ran live: no real OCR through the Reader, no real foreground window loop over time, no toast, no speech, no CPU measurement of the once-a-second OCR.
- The routing of the yes/no through the real `handle()` was not replayed (only the service's `answer`).
- The `screen_watch` skill's example phrases were not run through `skills/rank_check.py` (needs the embedder).

## Notes and open questions
- The toast tool has no buttons, so the toast carries text ("Say yes to be reminded"); the answer is by voice or typed text.
- A deadline that is offered during quiet hours or fullscreen is toast-only and counts as asked (it can still be answered with yes for 40 s).
- Multi-monitor: the reader assumes the foreground window lies on the monitor that `screen_monitor` captures (rect clipped to the frame; origin offsets of a non-primary monitor are not handled).
- F1 settle starvation, F3 (judge), F4, F5, F7, F8, F9 and F10 were not part of this step. F1's goal is met for the readers by the once-a-second design, but `screen.settled` itself is unchanged.
- `screen.read` bus event from the design was left out (YAGNI; `Reader.stats()` has the counts).
