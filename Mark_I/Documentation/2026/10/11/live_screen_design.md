# Live screen watching and proactivity: audit and design (2026-10-11, design only, nothing built)

Complaint (Vatsal): proactivity is bad, FRED catches nothing on the screen. Wanted: a live look about once a second that notices things (first case: a deadline shown on screen -> ask -> reminders at 18:00 the evening before and 06:00 on the day, 12:00 on weekends), plus a local screen memory.
Read for this: `Core/senses/{screen,ocr,glance,describe,presence}.py`, `Core/proactive/{judge,scamwatch,scheduler}.py`, `Core/engine/live.py`, `Core/memory_bridge/{ask,feed}.py`, `Core/config.py`, `screen_reactions_design.md`, the 10/08 docs, and the private event and session logs (counts only; no content was copied here).

## (a) What exists today, and why it catches nothing

| Piece | State |
|---|---|
| `senses/screen.py` ScreenSense | Runs **5 times a second** already (`screen_interval_s` 0.2), mss/BitBlt on monitor 1 only, a 36x64 tile diff, emits `screen.change` and, after 0.5 s with no change, `screen.settled` (with `duplicate`). Pixels stay in memory. |
| `proactive/scamwatch.py` | The **only** screen reactor. On a non-duplicate `settled` it runs Windows OCR on the **whole frame**, applies regex rules, toasts and speaks a "high" warning. |
| `senses/ocr.py` | Windows.Media.Ocr, CPU, about 40-50 ms for a 1280x720 frame (Vatsal approved CPU, 2026-10-06). |
| `senses/glance.py`, `describe.py` | Vision-slot description, only when the user speaks or asks to look at the screen. Glance is not wired into `live.py`. Not proactive. |
| `proactive/judge.py` | **Shadow mode.** Logs a decision and never speaks. Rules plus a logistic-regression model trained on 154 invented cases. |
| `proactive/scheduler.py`, `alerts.py`, `news` | Reminders, battery and disk alerts, news. These do speak. |
| Deadline watch, screen memory | **Not built.** Both exist only in `screen_reactions_design.md`. |

Evidence from the logs (`FRED_Private/events/2026-10-08, -09, -11.jsonl`, `logs/session.jsonl`):
- **No `proactive.*` event in any log, and no `scam_warning` in the session log.** The scam watch ran OCR about 490 times (non-duplicate settled events: 77 + 22 + 391) and found nothing, as expected, because no phishing page was opened.
- **The judge looked 877 times and spoke 0 times** (145 quiet by rule, 732 quiet by the model). The highest model score was **0.42** (mean 0.30) against a 0.6 threshold, so it could not have spoken even outside shadow mode.
- **The judge cannot see the screen.** Its window is made of event kinds plus `data.text/name/title`, and `screen.*` events carry no text. What it did see was mostly noise: `camera.fault` appeared in 651 of its windows, then `clock.state` 325, `audio.vad` 315 and `screen.change` 250.
- **Settle starvation.** The screen changes 2-3 times a second, and 57% of change events cover less than 1% of the screen (caret, clock, spinner; a live wallpaper, mpv under Lively, was running). A burst only ends after 0.5 s of total stillness, so:

| Day | Minutes with changes | Minutes with no settled event at all | Settled burst length p90 / max | Longest gap between settled events |
|---|---|---|---|---|
| 10-08 | 34 | 20 | 26 s / 211 s | 1493 s |
| 10-09 | 25 | 13 | 171 s / 324 s | 14409 s |
| 10-11 | 83 | 24 | 16 s / 305 s | 1275 s |

  With a video, a caret or an animation on screen, OCR never runs, so a deadline on a page next to a playing video would never be read.
- Capture errors: 33 `screen.error` BitBlt failures on 10-09 and 2 on 10-11. This happens on the lock screen or secure desktop, and the errors are rate-limited to one every 30 s.
- Camera: 2885 `camera.fault` on 10-09 (wrong index), 4 on 10-11 after `camera_index` was changed to 0. Presence was blind on 10-09.
- Test pollution: 25 `subscriber_error` lines in the real session log come from `_selfcheck.<locals>.<lambda>` (self-checks writing to the real log). There are also 42 `TurnController._on_event audio.vad` errors, which are outside this design.

**Root cause in one line:** the capture side is fine and already faster than 1 Hz. Nothing downstream reads the screen for anything except scams, the one general "should I speak" component is shadow-only and blind to screen content, and the OCR trigger starves whenever anything on screen moves.

## (b) Fixes for the existing path

| id | problem | evidence | fix | files | effort | risk | how to verify |
|---|---|---|---|---|---|---|---|
| F1 | Settle starvation: OCR never runs while a caret, clock, video or animated wallpaper changes | 20/34, 13/25, 24/83 minutes changing with no settle; bursts up to 305 s | (1) A burst also ends after `screen_burst_max_s` (3 s): emit `settled` with `forced=True` and start a new burst. (2) Changes of `<= screen_noise_tiles` (2) tiles do not open or extend a burst (they still emit `change`). | `senses/screen.py`, `config.py` | S (~15 lines) | Low. More OCR passes (at most one every 3 s per burst) | Self-check: a blinking 2x16 caret plus a moving 400x300 "video" region gives settled events every 3 s and none for the caret alone. Re-run the burst stats over a new event log: "changing, never settled" minutes about 0 |
| F2 | Whole-frame OCR mixes text from different windows (review row 65) and costs more | `scamwatch.check_once` OCRs `screen.latest()` | Add `ScreenSense.window_crop()`: foreground window rect (`tools/windows.foreground()` + `GetWindowRect`), clipped to the monitor. Scam watch and the new reactors OCR that crop. | `senses/screen.py`, `proactive/scamwatch.py` | S | Low. A dialog outside the foreground window is missed; add `bbox` of the burst when it lies outside | Invented frame with a URL in window A and "password" in window B: no warning. Same text in one window: warning |
| F3 | Judge shadow-only and blind; it can never speak | 877 looks, 0 speaks, max p 0.42 < 0.6 | Do **not** route screen offers through the classifier. Add a deterministic **speak gate** (section c.7) that the new reactors call. The classifier stays shadow until there are real labelled logs. | `proactive/judge.py` (move `rules()` into a shared `gate()`), `engine/live.py` | S | Low | Unit table of gate inputs to expected speak / toast / quiet |
| F4 | Judge window full of noise | `camera.fault` in 651 windows, `clock.state` 325 | Drop `camera.*`, `clock.state`, `audio.vad`, `audio.wake_near`, `presence.poll` and `screen.change` from `window()`. Retrain later from real logs only. | `proactive/judge.py` | XS | None (shadow) | Self-check: window has no noise kinds |
| F5 | `in_turn` turns False 8 s after the last state; "reminder due" and "critical" stay true for minutes (review row 62) | review doc | Gate reads live state: `speaker.speaking`, `TurnController.state`, last `audio.vad speaking` < 2 s; "due" and "critical" are consumed once handled. | `proactive/judge.py` | S | Low | Self-check with a fake clock |
| F6 | No yes/no reply path for a proactive question without `--memory` (AskDelivery is tied to the memory ask queue) | `live.py` builds AskDelivery only when memory is on | Copy the **news pattern**: the reactor owns `pending` and `answer(text) -> "yes"/"no"/None`, checked first in `handle()`. The pill's `ask` popup gives the yes/no buttons. | `engine/live.py`, new reactor | S | Low | Replay: an offer, then "yes" -> 2 scheduler items; "no" -> key blocked |
| F7 | Capture fails on the lock or secure desktop; reactors keep waiting | 35 BitBlt errors | After 3 errors in a row, emit `screen.locked` once and pause the reactors; resume on the first good grab | `senses/screen.py` | XS | None | Self-check with the `Boom` source |
| F8 | Only monitor 1 is watched | `screen_monitor` 1 | One `TileDetector` per mss monitor (`monitors[1:]`). Settle per monitor; the OCR crop comes from the monitor holding the foreground window. | `senses/screen.py` | S | Low; grab cost x monitors (~10-20 ms each at 1080p) | ListSource with two monitors |
| F9 | Self-checks write to the real session log | 25 `_selfcheck` subscriber errors in `session.jsonl` | Self-checks call `config.set_data_dir(tempfile.mkdtemp())` first (scamwatch already does) | `events.py`, `clock/*` self-checks | XS | None | Run all self-checks; real log unchanged |
| F10 | The vision slot holds a second full copy of Qwen3.5-4B Q6_K (the brain is the same model), and it was loaded without the projector | llama-server :8194 = the same gguf as :8191, no `--mmproj` | Load the brain once in the chat slot **with** `mmproj-BF16.gguf` and point `describe_model` at the chat slot. Drop the vision slot. Frees about 4 GB. | `senses/describe.py`, playground `server.py`, `engine/live.py` | S | Medium: a caption competes with a turn on one `-np 1` server; `gpu.run` priorities (INTERACTIVE > SENSE) handle it | `tests/live_describe.py` 12 screenshots and `live_vision_hard.py` give the same score; check VRAM before and after |

## (c) Design: the live screen system

### c.1 Components

```
 mss grab 5 Hz (per monitor)                       foreground window (win32gui: hwnd, title, exe, rect)
        |                                                      |
   TileDetector --change--> burst logic (F1: settle | forced 3 s | noise ignored)
        |                                   |
        |                         screen.settled {bbox, forced}
        v                                   v
   latest frame  ------------------>  LOOK (1 Hz max, one worker thread)
                                      1. privacy gate: paused? locked? excluded window? -> stop
                                      2. crop = foreground window rect (F2)
                                      3. OCR (Windows OCR, CPU) -> text, text_hash
                                      4. same (hwnd, text_hash) as last look -> stop
                                      5. bus: screen.read {hwnd_title_hash, n_chars} (no text)
                                         and in memory only: Reading(text, title, exe, ts)
                     +--------------------+--------------------+
                     v                    v                    v
              ScamWatch.assess     DeadlineWatch.find     ScreenMemory.observe
              (exists)             (pure extractor)       (dwell + dedupe -> memory event)
                     |                    |                    |
                     +----> proactive.offer {kind, key, line, due} / proactive.warn
                                          |
                                     SPEAK GATE (deterministic) -> speak + pill ask | toast only | hold | drop
                                          |
                     answer() in live.handle -> Scheduler.add (18:00 eve / 06:00 or 12:00 day) + memory "deadline" 0.6
```

### c.2 Capture
- Keep **mss** (installed, works, about 2.7% of a core at 5 Hz per `bench_screen.py`). The 5 Hz tile detector is the cheap change detector; heavy work (LOOK) runs at most once a second, and only after a change.
- DXGI Desktop Duplication (dxcam or windows-capture) would give dirty rectangles and capture DRM or overlay windows that BitBlt shows black. Add it only if black windows show up in practice. It is a new dependency and buys nothing at 1 Hz otherwise.
- Multi-monitor: F8. DPI: `live.main()` already sets per-monitor-v2 awareness before capture, so window rects and mss pixels are both native.

### c.3 OCR
- **Windows OCR (CPU) stays.** It is approved, installed and good on screen text. On a foreground-window crop it takes about 30-120 ms; at most one pass a second, and only after a change, is under 10% of one core.
- GPU OCR (PaddleOCR or RapidOCR with CUDA, docTR) would mean a new dependency, about 0.5-1 GB of VRAM, and no proven gain on screen text. Skip it unless Windows OCR misses real deadlines in the tests. Note: the GPU-only rule has Vatsal's explicit OCR exception (2026-10-06); it does not extend to new CPU models.

### c.4 Caption and visual embedding (screen memory)
- **Phase A (no new model):** caption = `"{exe}: {window title}"` + the first 2-3 OCR lines. Vector = the **existing** Qwen3-Embedding-0.6B (text, already on GPU) over the caption plus up to 1000 OCR characters. This answers "what was I looking at yesterday" for every text screen, which is most of a desk day. VRAM +0.
- **Phase B (the visual embedder Vatsal asked for):** one small CLIP/SigLIP-class model in torch (cu128 is installed), fp16 on GPU, run only on stored records (about 1 every 10 s at most), never every second. Candidate: SigLIP2-base-patch16-224, about 0.4 GB VRAM and about 10-20 ms per image. Pick one; per the no-benchmarking rule, check it with a small standalone test only. It needs a new package (open_clip_torch or transformers), and that is the open question. Vectors are stored per model id, like the text index. Cloud Gemini stays out.
- **Phase C (optional):** a real one-line VLM caption from the brain with the projector (after F10), SENSE priority, at most one a minute, skipped while a turn is pending. Only if A and B prove too thin.

### c.5 What is stored
- Memory event type `"screen"`, importance **0.1** (below news at 0.2), with `ts` (IST), `app` (exe), `title`, `caption`, the vector or vectors, and no pixels. While `memory_testing` is on it lives in `Initial/Testing_Memory` like everything else.
- **One record per visit**, not per second: written when the foreground window or content changes after a dwell of at least `screen_mem_dwell_s` (10 s). If the token overlap with the last record of the same window is at least 0.8, the end time of that record is extended instead. Expected: a few hundred records a day.
- Retention: low-weight events go through the existing memory decay. Nothing else is written; the event log keeps counts and hashes only.
- **Exclusions** (checked before OCR; nothing is stored, nothing is extracted):
  - A block list on exe or title: password managers, the vault's editor or folder (matched on `vault_dir`), `InPrivate` / `Incognito` / `Private Browsing` titles, banking (title contains a bank brand or registrable domain from the scam watch's `BRANDS` banks), FRED's own HUD and pill.
  - A page whose OCR shows a password or OTP field: not stored, not quoted.
  - Lock or secure desktop (F7).
  - The **scam watch still assesses blocked banking windows in memory**, because a fake bank page is exactly its job; open question Q2.
- **Pause switch:** `screen_watch_enabled` in config, a tray toggle, and voice ("stop watching the screen" / "watch the screen again", plus a timed "for an hour"). It is shown as a dot on the pill. Pausing stops LOOK; the scam watch follows the same switch unless Vatsal decides otherwise.

### c.6 Deadline extractor (pure code, stdlib only)
`find_deadlines(text, now) -> [Candidate(line, due: datetime, key)]`. Rules from `screen_reactions_design.md`:
- A date phrase (14 Oct, Oct 14, 14/10 read day-first, 14-10-2026, Friday, tomorrow, tonight, in 3 days, 11:59 pm, "EOD") within 6 words of a cue (due, deadline, submit by, last date, closes, ends, exam on, expires, before).
- A date with no time means 23:59 that day. A date already past is dropped. A year-less date already past this year rolls to next year only if the cue is present and the result is under 90 days away.
- `key` = sha1(normalised line + due date); `FRED_Private/screen_deadlines.json` holds `{key: asked|accepted|declined, day}`.
- **Reminders on yes:** 18:00 the evening before and 06:00 on the day; 12:00 on the day if it falls on a Saturday or Sunday. A reminder already in the past is skipped, so a deadline due tomorrow morning seen at 19:00 gets only the morning one. If both are past, FRED offers "in an hour" instead. A memory event `"deadline"` with importance 0.6 is added when memory is on.

### c.7 Proactive decision (speak gate, deterministic, no model)
Input: the offer plus live state. Output: `speak` (voice + pill ask with yes/no), `toast` (toast + pill caption only), `hold` (retry in 60 s, up to 20 min) or `drop`.

| Condition | Action |
|---|---|
| Paused, locked, or the screen is excluded | drop |
| User speaking (VAD < 2 s), mid-turn, FRED speaking, a reply pending, or a night job holds the GPU | hold |
| FRED spoke < `judge_cooldown_s` (120 s) ago | hold |
| Presence absent | hold (ask when back, if still relevant) |
| 22:00-07:00, or fullscreen video or game (foreground rect = monitor and the window keeps changing) | toast |
| Deadline under 6 h away | speak, even past the rate limit |
| Spoken offers: at most 1 per 10 min and 6 a day; scam "high" is exempt | over the limit -> toast |
| Otherwise | speak |

Do-not-disturb ("don't disturb me for an hour") sets a flag that turns every speak into toast except scam "high" and due reminders. No answer within 20 s: the pill closes; it is re-offered at most once later the same day (existing design).

### c.8 Latency and budget per second (1080p, one monitor)

| Step | When | Cost |
|---|---|---|
| Grab + tile diff | 5 Hz always | ~3% of a core (measured) |
| Foreground window query | each LOOK | < 1 ms |
| OCR on the window crop | ≤ 1 Hz, after a change only | 30-120 ms CPU |
| Extractors (scam, deadline) | per new text | < 5 ms |
| Text embedding of a memory record | ≤ 1 per 10 s | ~20-40 ms GPU (existing embedder) |
| Visual embedding (Phase B) | ≤ 1 per 10 s | ~10-20 ms GPU, +0.4 GB VRAM |
| VLM caption (Phase C) | ≤ 1 per min | ~1-3 s GPU at SENSE priority |

From change to spoken question: about 1-2 s (settle 0.5 s or forced 3 s, plus OCR, plus the gate).

**VRAM (measured 2026-10-11, FRED stopped):** 10.3 of 16.3 GB used, with the playground holding the 4B chat model, the embedder, the 2B talker, **a second copy of the 4B in the vision slot**, and desktop apps (Resolve, Chrome, the Lively mpv wallpaper). Whisper adds about 2.2 GB when FRED runs (measured in P1a), for about 12.5 GB in total and about 3.5 GB of headroom. Phases A and B fit as they are (+0 / +0.4 GB). F10 frees about 4 GB more. Kokoro, OCR, VAD and the presence detector stay on CPU as already approved.

### c.9 Failure modes
- OCR misreads a date: the question quotes the line, so Vatsal sees the mistake and says no, and no reminder is set without a yes.
- Repeated offers: the per-key store is checked before any offer, and the key survives restarts.
- A scrolling feed produces a new text hash every second: the LOOK cap (1 Hz) plus the dedupe by key keep the extractor cost bounded.
- A capture error or locked screen: F7. The OCR engine is missing: `OcrUnavailable` is logged once and LOOK is disabled (no silent "").
- A GPU contention spike: memory embeddings run BACKGROUND or SENSE through `gpu.run`; a turn always goes first.
- The memory store is off or unavailable: the deadline watch still works (scheduler only); screen memory is skipped.
- Privacy leak by logs: the bus and logs carry hashes, counts and keys only. The only screen text that leaves the detector is the one quoted line in the spoken question and the pill.

### c.10 Privacy
Everything stays local. There are no cloud calls on screen data (Gemini embedding excluded; screen text is never sent to a cloud brain unless Vatsal asks about the screen in a turn). The exclusion list and pause switch apply before OCR. Stored records hold text and vectors only, never pixels. Exclusions are deliberately strict for the vault (see the memory rule: vault content never goes into the repo, docs or tests).

### c.11 Testing plan (invented content only)
- **Extractor table test:** about 60 invented lines ("Lab report 3 due Fri 17 Oct 11:59 pm", "Submissions close tomorrow", "Posted 14/10" -> none, "Offer ends Sunday" -> yes, past dates -> none, a weekend due date -> 12:00). The reminder-time math is tested with a fake clock across 18:00 and midnight.
- **Rendered screenshots:** OpenCV/PIL draw invented pages (a course portal with a due date, a fake bank title, an `InPrivate` title, a video region next to a deadline, a caret blinking) and feed them to `ListSource`. Assertions: a settled or forced event happens, OCR finds the line, the exclusion blocks it, the gate decides as expected, and nothing personal is in the bus payloads.
- **End-to-end replay:** fake speaker, fake toast, real scheduler in a temp `data_dir`. Offer -> "yes" -> two schedule items at the right times; "no" -> never again.
- **Live check:** an invented HTML page opened in a browser while FRED runs; measure time to the question and CPU and VRAM before and after.
- No real screenshots in the repo or docs.

### c.12 Phased build (sockets first, per the Mark build method)
1. **P0 sockets and fixes:** F1, F2, F7, F8 (screen.py), F4 and F5 (judge), F9 (self-checks), plus the LOOK worker emitting `Reading` in memory and `screen.read` counts. Gate function plus the pause switch. No new behaviour is visible yet.
2. **P1 deadline watch:** extractor, key store, offer -> gate -> pill ask and voice -> `answer()` in `live.handle` (F6) -> scheduler plus memory event. Exclusions active.
3. **P2 screen memory A:** visit records with the existing text embedder; a `recall` path for "what was I looking at ..."; memory overview doc updated (keep-overview rule).
4. **P3 visual embedder B:** one small model in torch on GPU, after Vatsal approves the dependency.
5. **P4 VRAM cleanup F10**, then the optional VLM caption (C).
6. **P5:** after 1-2 weeks of real logs, label the judge's shadow decisions and decide whether the classifier ever goes live. More reactors on the same LOOK hook (meeting links, unanswered chat messages) only on request.

## Open questions for Vatsal
1. LOOK rate: at most 1 OCR a second after a change (proposed), or literally every second even when nothing changed? The second costs CPU and finds nothing new.
2. Banking and incognito windows: blocked for storage and deadlines, but should the scam watch still read them in memory? (Proposed: yes for the scam watch only.)
3. Screen memory text: store up to 1000 OCR characters per visit (needed for good recall), or only title plus caption?
4. Visual embedder: OK to add one package (open_clip_torch or transformers) and about 0.4 GB VRAM for a SigLIP-class model? Or is the text-only phase A enough?
5. F10: OK to load the brain with its image projector in the chat slot and retire the separate vision slot (about 4 GB freed)?
6. Night hours for toast-only (22:00-07:00?) and the spoken cap (1 per 10 min, 6 a day)?
7. When a deadline is under 6 h away, speak even during do-not-disturb?
8. Should the pause switch also pause the scam watch?
