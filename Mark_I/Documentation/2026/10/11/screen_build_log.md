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

## F10: one brain copy with its image projector, vision slot retired (2026-10-11)

**Change.** `vision_shared` (config.py, default True). When it is on and `describe_model` is the brain, `models.client.vision_slot()` returns "chat": the chat slot is loaded with `vision=True` (llama-server `--mmproj mmproj-BF16.gguf`) by `engine/live.py` (start and the lockdown release) and `engine/cli.py`, and `senses/describe.py` sends its image_url requests to that slot. Different models, or `vision_shared=False`, keep the old separate "vision" slot. A chat slot that is already loaded without the projector is reloaded with it. The playground needed no change (it already takes `vision` per load and keeps a loaded slot as it is).
**Thinking/tools.** Describer calls are plain chat calls: `think=False`, no tools, `describe_max_tokens` 300, temperature 0.2. Unchanged from before, only the slot differs.
**Concurrency.** The chat llama-server is `-np 1`, so a description that starts first holds a turn that arrives meanwhile (image in 0.5 s measured on the test image; at most a few seconds for a busy screen). The tools (`whats_on_screen`, `look_through_camera`) run inside a turn, so nothing competes; the glance path (not wired) goes through `gpu.run` at SENSE priority. No new lock was added.
**VRAM (measured, nvidia-smi delta, private llama-server on :8199 with the playground's exact flags, ctx 16000; the playground itself was not touched).** Old vision slot (4B Q6_K, no projector): +3475 MiB. Brain with projector: +4171 MiB (+4211 after an image and a chat request). So the projector costs about 0.7 GB and removing the vision slot nets about **2.8 GB saved** (3.5 GB freed, 0.7 GB spent), not the 4 GB estimated in the audit.
**Real check.** Image of "Invoice due 14 Oct" answered "Invoice due 14 Oct" in 0.5 s, then a text question ("42") in 0.1 s from the same server. The server was killed afterwards.
**Tests.** Self-checks: `client.py` (vision_slot), `describe.py` (default slot), `test_voice_out.py` (the lockdown release loads the brain with the projector); `tests/run_all.py` 101/101.
**Not done.** The live playground still holds the old layout (chat without projector + a vision slot): the next FRED start reloads chat with the projector, but the stale vision slot must be unloaded by hand (`Playground().unload("vision")`). `tests/live_describe.py`, `live_vision_hard.py` and `live_playground.py` still load the vision slot explicitly. `--brain X` different from `describe_model` with `vision_shared` on would make the Describer ask the chat slot for the describer model (a swap): only for test runs.


# Screen memory, Phases A and B (2026-10-11)

Decisions from Vatsal (final): everything FRED sees on screen is remembered EXCEPT bank/payment pages and details, passwords and password managers, the vault, the lock screen and FRED's own windows. The pause switch stops it; the scam watch is untouched.

## Built
| File | What |
|---|---|
| `Core/proactive/screenmem.py` (new) | `ScreenMemory`: a reader callback tracks the current visit (hwnd + title). A visit lasting >= 10 s (`screen_mem_dwell_s`) is stored once as a memory event type `screen`, importance 0.1, ts = visit start, content = date/time + one-line caption (`exe: title - first 3 OCR lines`) + up to 1000 chars of text. Saving runs in a worker thread (the reader is never blocked). The memory's own index embeds the content (Phase A: the existing Qwen3 embedder, no new model). Near-duplicate: same app + title in the last 6 h and token overlap >= 0.8 (`screen_mem_dup`) -> the old visit's `seen` and end time are updated, nothing new stored. Side table `screen_visits(event_id, app, title, ts, ts_end, seen, img_model, img)` in the memory database. `excluded()` is checked when a visit starts and on every text update, before anything is stored or embedded; an excluded visit leaves no event, no vector, no image. `search()` serves the recall skill. Pause: a paused period drops the current visit; nothing from it comes back after resume. |
| `Core/senses/imgembed.py` (new) | Phase B: `ImageEmbedder` (transformers, `google/siglip2-base-patch16-224`, fp16, CUDA only), lazy load, unloaded after `screen_img_idle_s` (60 s) idle by the screen memory's tick, `shared()` singleton. Image vector is made from an in-memory crop of the foreground window at store time (never written to disk) via `gpu.run(..., BACKGROUND)`; stored as a unit float32 vector in `screen_visits.img`. Queries are embedded by the same model (text tower), at INTERACTIVE priority from the skill. Fusion with the text ranking is reciprocal-rank (as in the memory's hybrid search). |
| `Core/skills/extra/vision/screen_recall/` (new skill) | "What was I looking at around 3pm / yesterday", "what was that page about X". `when` -> time window (a clock time = +-45 min; a day = the whole day), `query` -> text + image ranking. Returns `Day dd Mon HH:MM \| exe \| title \| caption`. Flag `reads_untrusted`. |
| `Core/config.py` | `screen_mem_enabled` (True), `screen_mem_dwell_s` (10), `screen_mem_chars` (1000), `screen_mem_dup` (0.8), `screen_img_enabled` (True), `screen_img_model`, `screen_img_idle_s` (60). |
| `Core/engine/live.py` | In the `--no-screen` block: `ScreenMemory(reader, screen, rt.news.mem, imgembed.shared())` when memory is on (`--memory`) and `screen_mem_enabled`; start/stop in the closers. |
| `Core/requirements.txt` | `transformers==5.19.0` (the one new package; installed in `Core/.venv`). |

Exclusion rules (`excluded()`): no exe and no title (lock/secure desktop); LockApp / LogonUI / consent / Windows Security / "lock screen"; titles starting `FRED` / `FRED HUD`; password-manager exe or title (KeePass, 1Password, Bitwarden, LastPass, Dashlane, Enpass, NordPass, RoboForm, Keeper, Proton Pass ...); private-browsing titles (InPrivate, Incognito, Private Browsing) and any title containing "password"; bank/payment words in exe or title (bank, banking, UPI, Paytm, PhonePe, PayPal, payment, checkout, card, wallet, big bank brands); the vault folder name (from `vault_dir`) in title or text; text patterns: 12-19 digit runs (card numbers, spaces/dashes allowed), IFSC codes, UPI ids, "account number ... 9-18 digits", and the words password / passcode / OTP / CVV / card number / security code / UPI PIN (a login form). Deliberately strict: a few harmless pages (a "Bank holiday list", any page with the word "password" in its text) are not remembered.

## Verified (self-checks, invented text, fake memory embedder, fake image model, fake clock)
- `proactive/screenmem.py`: 20+ exclusion cases (fake card number in two formats, IFSC, UPI id, account number, bank title, checkout, KeePassXC and Bitwarden, password and private-browsing titles, OTP text, LockApp / LogonUI / empty window, FRED titles, vault name in title and text), 8 s dwell not stored and 12 s stored once, event type/importance/caption/image vector, near-duplicate merge (seen=2), excluded visits add no event and no embedding row, a page that turns into a card form after it started is dropped, pause drops the paused visit (not back after resume), a short visit is not stored, 1000-char cap, search by time window / text / text+image.
- `skills/extra/vision/screen_recall/check.py`: time windows ("yesterday at 3pm", "yesterday", "today", "last year"), text query, image query with a fake model, nothing found, unbound without memory.
- `senses/imgembed.py` with the REAL model on the GPU: a red-square image and a text-page image are matched to their descriptions by text queries; load, idle unload and reload work.
- `tests/run_all.py` with a temp FRED_DATA: 104/104 (was 101). `engine/live.py` imports.
- Measured (FRED stopped, GPU shared): the model needs 740 MiB allocated by torch (798 MiB at the driver level; the 0.4 GB estimate was low because the text tower is loaded as well), 13 ms per 1280x720 image (the processor resizes to 224x224) after warm-up, first load about 4 s from the cache; freed again by unload (740 MiB back). First download of the model took several minutes (HF cache `models--google--siglip2-base-patch16-224`, about 1.5 GB on disk in fp32).

## Not verified (first live run)
- A real Reader loop with real OCR and real foreground windows feeding `ScreenMemory`; real exe names via `win_exe` (psutil + win32process); the window titles of FRED's own HUD and pill (matched on a title starting with `FRED`; the HUD window's actual title was not checked).
- The embedding of a screen event by the real Qwen3 embedder through the playground (the index hook runs in the worker thread; a failure there is logged as `screenmem_error`, the event itself stays and `index.sync()` embeds it later).
- VRAM and latency with Whisper and the playground models loaded; a SigLIP load of 0.75 GB fits the roughly 3.5 GB headroom measured earlier, but it was only run alone.
- Voice routing of "what was I looking at" to `screen_recall` (examples not run through `skills/rank_check.py`; needs the embedder), and that the brain does not confuse it with `look_at_screen`.
- The `screen` events also appear in the general memory recall (they are ordinary events); with importance 0.1 they rank low, but a first-day check of recall quality is wise.

## Open questions
- Image quality: SigLIP2 at 224x224 sees layout and colour, not small text; the text vector does the "what was it about" work. A larger input (naflex variant) is possible later.
- A visit is stored once, with the text seen when it reached 10 s; later scrolling in the same window does not add text (only a new visit after switching away does). Re-storing on big text changes was left out (ponytail).
- Retention: there is no separate pruning; low-weight events follow the memory's existing decay. Hundreds of `screen_visits` per day will need a count check after a week.
- Multi-monitor: the image crop uses the foreground rect on the captured monitor (same limitation as the Reader).
