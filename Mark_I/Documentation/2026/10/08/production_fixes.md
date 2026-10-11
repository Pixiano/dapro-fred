# Production fixes: the first live session (2026-10-08)

First time the full system ran on the real lavalier mic and the Rockerz headset. These are the problems the session logs showed and what was changed. Nothing here has been tested live yet: the self-checks pass (90 of 90), and the next live session is the proof. Every number below is a first guess to tune.

## What happened (from `FRED_Private/events/2026-10-08.jsonl`)
- **The pill flashed about once a second.** In 91 s there were 37 LISTEN events and 25 IGNORE "not_addressed" ones: the voice detector kept hearing speech-like sound and the pill showed "listening" on each one. The wake word fired once (score 0.52); it was not the cause.
- **Why the detector fired:** the lavalier picks up the room, and the auto-gain had boosted it 23 times (logged on the wake event), so room noise looked like speech. 58 short bursts, median 0.7 s.
- **A runaway loop after the one real wake word:**
  1. The wake word opened the 8 s follow-up window, so anything heard counted as addressed.
  2. Whisper wrote text for noise: "All right. Thank you.", "I'll see you next time." (its usual hallucinations on near-silence).
  3. It also wrote "Open the file." (three times), "What's on my agenda?" and "Search the web." These came from our own `whisper_prompt` hint list, which Whisper repeats on noise. FRED answered "I can't open files" to a request nobody made.
  4. Each reply was cut off 0.1 to 0.3 s after it started by barge-in (8 times), and each cut-off reopened the follow-up window.

## Fixed

| # | Problem | Fix | Setting |
|---|---------|-----|---------|
| 1 | Pill (and HUD) showed "listening" for every sound | The clock now announces a new state `ADDRESSED` once per utterance, when it is for FRED: at once for the hotkey, or after 0.3 s for a wake word or the follow-up window. The pill and HUD show "listening" only from then. A heard-but-ignored utterance never touches the screen | `listen_show_s` 0.3 |
| 2 | Room noise amplified up to 100 times | Quiet sound is no longer boosted (floor raised), and the maximum boost is lower | `agc_min_peak` 0.01 to 0.03, `agc_max_gain` 100 to 40 |
| 3 | Detector too trigger-happy | Stricter mode, longer onset | `vad_mode` 2 to 3, `vad_onset_ms` 100 to 200 |
| 4 | Whisper repeated our hint list on noise | The hint is now just "Fred." | `whisper_prompt` |
| 5 | Whisper invented text for noise | Segments that are probably not speech and low confidence are dropped | `stt_no_speech_prob` 0.6, `stt_min_logprob` -1.0 |
| 6 | Any noise right after FRED spoke counted as an answer | Inside the follow-up window the speech must last 0.4 s to count | `followup_min_s` 0.4 |
| 7 | A reply was cut off by 0.3 s of noise | Barge-in needs 0.5 s of speech | `barge_ms` 300 to 500 |
| 8 | Mic and speaker were the system defaults | Mic is the lavalier, speaker is the Rockerz headset (name match, case-insensitive; an empty setting means the system default) | `mic_device` "Lavalier", `speaker_device` "Rockerz" |

## Tuning knobs for the next live session
If the pill still flickers: raise `agc_min_peak` or `listen_show_s`. If "Hey FRED" is missed: lower `agc_min_peak` back toward 0.02, or `vad_mode` to 2. If real interruptions feel sluggish: `barge_ms` back to 400. If follow-ups without the wake word stop working: lower `followup_min_s`.

## Still open (not fixed)
- `headphones` is never set, so the barge-in cannot tell FRED's own echo from you. A voice-ID match would fix it but voice ID is off.
- System audio input (loopback) is not built; waiting on your answers to the design questions.
- Everything above needs a live check: the same hour of use, with the event log read again.

## Added after run 2 (see `live_runs.md`)

| # | Problem | Fix | Setting |
|---|---------|-----|---------|
| 9 | Replies became "(interrupted)": a cut-off reply left that placeholder in the history and the 4B model copied it | A turn that was not heard at all is dropped from the history; a partly heard one keeps the words that were heard | none |
| 10 | TV noise became "Thank you." turns | Whisper ghost phrases are dropped when the utterance was quieter than a real one | `stt_ghosts`, `stt_ghost_peak` 0.1 |
| 11 | No tray icon or HUD in Mark I | Ported from the old build, rebranded (`ui/tray.py`, `ui/hud_launcher.py`) | `--no-hud` turns both off |
| 12 | Voice ID gate off, TV voices became turns | On, enrolled from real lavalier clips, threshold 0.33 | `voice_id_enabled`, `voice_threshold` |
| 13 | "remember all this" went to memory search | Not a memory search; examples added to look_at_screen | none |
| 14 | A foreign or too-short voice cut FRED off mid-sentence | Barge-in needs a passed voice check | none |
| 15 | "sir" missed in 15% of lines | Every line, fillers included; MUST in the prompt of every model | `sir_rate` 1.0 |
| 16 | "Who am I?" answered by the screen tool | Treated as small talk | none |
| 17 | No real-world data saved | Central usage store, by component / year / month / day | `usage_clip_cap`, `usage_wake_clip_s` |
| 18 | A tool call written as text was spoken, and the HUD showed nothing | Such replies become a plain apology, never spoken, shown or remembered | none |
| 19 | Idle memory leak of 35 MB/min (webcam open/close leaks in OpenCV) | Each snapshot is read in a child process | none |
| 20 | Night job failed (409) after a killed run left the playground window open | An already-open window is adopted | none |
| 21 | 344 false utterances overnight from 50 Hz hum read as speech | The voice detector only hears above 200 Hz | `vad_highpass_hz` 200 |
| 22 | Speaker open crashed when the Rockerz was off (only the hands-free WDM-KS entry left), no reply spoken | Unusable outputs skipped, falls back to the system default each time FRED speaks | `speaker_device` |
| 23 | Hold-to-talk started no turn if the detector missed the voice | Key-down starts the capture itself | none |
| 24 | "sir" twice in a reply | Prompt: exactly once, at the end | `sir_rate` |
| 25 | Own voice from the speakers became turns; wake word sent to the model | Echo filter against FRED's last lines; wake phrase stripped | none |
| 26 | Memory data and code layout | Data moved to `Initial/Testing_Memory` (gitignored, `memory_testing` True); memory code moved out of Initial to `Mark_I/memory`, next to Core | `memory_testing` |
| 27 | Presence dead: webcam index moved after replug (real camera 0, index 1 now a frozen virtual one) | `camera_index` 0 | `camera_index` |
| 28 | Own short phrases rejected by voice ID (0.26 to 0.33) | 9 real-use clips added to the enrolment, threshold 0.38 | `voice_threshold` |
| 29 | FRED answered the user talking to someone else right after a reply | Follow-up window 8 s -> 5 s (voice ID already gates it) | `followup_s` |
| 30 | Equations and word problems answered in the model's head, no calculator | `calculate` description tells it to decompose into steps + 6 examples for routing | none |
| 31 | New: daily news digest (06:00 and 18:00) in Core | `Core/news/` + `news` tool, stored short + detailed per story, low-weight memory events | `news_hours` |
