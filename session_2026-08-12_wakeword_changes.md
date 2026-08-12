# FRED — Session log (2026-08-11 → 2026-08-12)

Record of what was tried, what broke, what got fixed, and where things stand. Started as a wake-word retraining session (Parts 1-6); grew into a wider bug-fix pass once live testing surfaced unrelated issues (Parts 7-10). Companion reading: `Core/input/wakeword_train.py` (the pipeline itself, heavily commented inline) and `Core/config/settings.py`'s `WAKEWORD_THRESHOLD`/`TTS_PREROLL_SEC`/`TTS_POSTROLL_SEC` history comments.

**Git commits this session:** `c72ca54` (wake-word AGC/crash-guard/real-voice pipeline), `aa2c8ff` (TTS postroll, dispatcher fix, STT silence-skip), `44d98aa` (VatsalDaPro path alias). Trained model files, recordings, and this doc itself are local-only (gitignored / untracked by convention).

---

## Part 1 — Starting point (inherited from 2026-08-10)

Wake-word detection ("Hey FRED", via openWakeWord) had already been through: AGC (adaptive gain targeting a safe peak level), a per-callback crash guard (an unguarded numpy-bool `TypeError` had been silently killing detection), a dedicated `Core/data/wakeword_log.jsonl` for score/gain/threshold logging, and three threshold moves in one day (0.6 → 0.4 → 0.25 → 0.35) chasing live measurements. That threshold-tuning had visibly hit its ceiling: at 0.25 a real false positive fired at 0.701; at 0.35, real attempts were still landing at 0.05–0.28, well under threshold. Diagnosis at the time: synthetic-Kokoro-only training data, not a number to tune further.

## Part 2 — Round 1: negatives only (2026-08-11 afternoon)

**What changed:**
- 13-minute room-ambient recording (Humble lavalier mic — the actual deployment mic) added as negative training data. Recorded as MP4, extracted to 16kHz mono WAV via ffmpeg (no MP3 detour needed — soundfile's MP4 support doesn't exist, but the training pipeline reads WAV natively). Lives at `Core/data/wakeword_training/room_recording/negative_room_13min.wav`.
- 34 phonetic-neighbor words added as negatives (`PHONETIC_NEIGHBOR_WORDS` in `wakeword_train.py`) — Red, Bread, Bred, Bled, Dead, Dread, Fed, Fled, Led, Sled, Sped, Spread, Shed, Shred, Tread, Wed, Ted, Ned, Head, Said, Friend, Fresh, Front, Frank, Fright, Fret, Free, French, Freddy, Fridge, Friday, Thread, Instead — synthesized via Kokoro (same 6 voices × 3 speeds as the existing negative-speech pipeline), triggered directly by a live false positive (STT heard "a little bit of ball" after the wake model spiked to 0.76 on unrelated speech).

**Bug found and fixed:** `install_runtime_model()` crashed on this run. openWakeWord's torch→ONNX export splits large models into a graph file (`hey_fred.onnx`) and external weights (`hey_fred.onnx.data`); the install step copies both to the runtime location. FRED was running during the retrain and had `hey_fred.onnx.data` memory-mapped — `shutil.copy`'s `open(dst, 'wb')` truncate failed with `OSError: [Errno 22]`, but only *after* the graph file had already been overwritten. Result: a live mismatched pair on disk (new graph structure, yesterday's weights) that would have silently loaded on the next restart.

Fix: `_replace_file()` — copy to a temp file, then `os.replace()` over the target. Verified this actually survives the real lock (not just assumed): launched FRED, confirmed the model was loaded, then ran `os.replace()` against the live `.onnx.data` from a separate process — succeeded. (A naive unit test using a plain Python `open()` to simulate the lock *fails* the same way the bug did — onnxruntime opens with delete-sharing, plain `open()` doesn't — so the portable test only covers the atomicity/content-swap, not the lock survival; that part is verified live, documented in the test file's comment.) Order matters: data file replaced before the graph file, so a partial failure leaves the *old* graph paired with *new* data (safe — architecture is identical across retrains) rather than the reverse (what actually broke).

Manually completed the interrupted install, then wrote the fix into `wakeword_train.py` for future runs. Tests: `Core/tests/test_wakeword_train_install.py`.

**Eval (held-out test data, never trained on):**
| Set | n | Result |
|---|---|---|
| Phonetic-neighbor words | 594 | 1 false positive (0.2%) — the fix worked |
| General negative_test | 419 | 8 false positives, all from the auto-generated adversarial-phrase set (fabricated names ending in fred-like syllables: "heyward fred", "jorge fredrick", "hey frenzel"...) — pre-existing weak spot, not new |
| Positive_test | 5 | Inconclusive — this set is a single deliberately-reversed held-out phrase ("Fred, hey"), not representative of real usage |

Live production log after this went in: clean. A genuine high-confidence fire (0.97) at 18:39 on headphone mic, confirmed by the user as a real "Hey Fred" test — best real-world score logged yet.

## Part 3 — The recordings

**Negative room recording:** `Core/training/Recordings/Negative_13mins.mp4` → extracted to `room_recording/negative_room_13min.wav` (Part 2).

**Positive recordings:** user recorded variations of "Hey/Hi/Hello FRED" at varying distance and volume (script: `Core/data/wakeword_training/real_positive_script.txt`, 105 lines across the three phrasings — not all used). Saved as one continuous 171.5s file, `Core/training/Recordings/positive_2,5min.mp4`, not one-file-per-utterance as the script asked for.

**Segmentation** (continuous → individual clips, since `ingest_real_positive_clips()` treats each file as one training example): energy-based splitting, not a full VAD model —
1. 20ms frames, 10ms hop, RMS in dB.
2. Adaptive threshold: 20th-percentile noise floor + 14dB margin (adaptive rather than a fixed absolute level, since distance/volume were deliberately varied).
3. Merge across gaps ≤180ms (keeps "Hey...Fred" together), require ≥120ms to count as a real segment.
4. Second pass: reattach any resulting fragment <0.5s to its nearer neighbor if the gap is <0.5s (catches a word split off by pass 1 without loosening the main threshold enough to bridge separate takes).
5. Segments >2.8s dropped as merged multi-takes rather than guessed at further — 6 dropped this way (up to 14s long, clearly several utterances run together with too little pause).

Result: **54 clean individual clips**, 0.52–2.79s each, mean 1.40s. Saved to `Core/data/wakeword_training/real_positive/` — later moved to `real_positive_disabled/` (see Part 5).

## Part 4 — Round 2: 54 real positives (2026-08-12 morning) — REVERTED

`ingest_real_positive_clips()` split the 54 into 46 train / 8 test. Retrained; install succeeded cleanly this time (Part 2's fix held).

**Eval:**
| Set | Result |
|---|---|
| Real positive_test (8 held-out) | Split 4/8 at 0.97–0.999 (excellent), 4/8 at 0.001–0.007 (near-zero). Caveat: eval scores raw file audio with no AGC applied, but the live app always gains quiet/far audio toward a target peak before it reaches the model — so the near-zero half is likely a pessimistic floor, not necessarily real-world performance, since it doesn't replicate what live deployment actually does to quiet audio. |
| General negative_test | 5 false positives (down from 8) — but not a clean subset: 3 of the original 8 got fixed, a *new* pair ("hait frieda") crossed into false-positive territory that hadn't before. Boundary shifted, not uniformly improved. |
| Phonetic-neighbor words | 1/594 (holding steady) |

**Live result: regression.** 43 wake-word fires in one hour, mostly high-confidence (0.6–0.99), cross-referenced against the session log — the vast majority had no matching conversation turn logged at all (mic opened, nothing said, silence timeout). Confirmed by direct user report as false positives, not real usage.

**Working theory:** 46 real training examples is a small, volume/distance-skewed sample. Some close/loud takes plus AGC's own behavior (always pushing ambient audio toward the same target peak) likely let the model partially key on loudness/proximity characteristics rather than purely the phonetic content — meaning ordinary loud ambient transients (after gain) started resembling the positive class. The synthetic negative_test (419 clips) didn't catch this because it doesn't cover the same breadth of "ordinary loud ambient sound in this specific room" that a real hour of use does.

**Action:** reverted. Removed `real_*.wav` from `positive_train`/`positive_test`, moved the 54 recordings from `real_positive/` to `real_positive_disabled/` (preserved, not deleted — just excluded from the next `ingest_real_positive_clips()` run), retrained.

**Eval after revert — cleanest of all three runs:**
| Set | Result |
|---|---|
| Phonetic-neighbor words | **0/594** false positives |
| General negative_test | **2/419** — both the same persistent "hafer fredell"/"heyward fred" pair that's shown up in every version |
| Positive_test | Flat ~0.001 (same reversed-phrase caveat as always) |

Restarted FRED on this model, confirmed clean.

## Part 5 — Round 3: 20 of 54 real positives (2026-08-12) — REVERTED, worse than Round 2

Hypothesis: a smaller, still-random slice (20 clips, `random.seed(42)`: `take_002, 003, 006, 007, 008, 009, 014, 015, 016, 018, 029, 035, 037, 040, 043, 045, 046, 051, 054, 055`) might add real-voice signal without swamping the boundary the way 46 did. Copied into `real_positive/`, retrained with `--overwrite`. Install succeeded cleanly (Part 2's fix holding across every run now).

**Eval — worse than Round 2 on every axis, not better:**
| Set | Round 2 (46 clips) | Round 3 (20 clips) |
|---|---|---|
| Real positive_test | 4/8 excellent, 4/8 near-zero | 3 held out this time: 1 excellent (0.993), 1 near-zero (0.001), 1 mid (0.267) |
| General negative_test FPs | 5/419 | **14/419** |
| Phonetic-neighbor FPs | 1/594 | **3/594** |

14 general false positives included **7 patterns never flagged in any prior run** ("hagens predicated", "hales frankie", "hastey cressman", "hey flooded", "heyden frevert", "heying freck", "ryohei fridson"), on top of the recurring ones. The "smaller = safer" intuition was backwards: with fewer real examples, whatever's idiosyncratic about a couple of the 20 (a specific take's noise floor, proximity, or recording artifact) gets *proportionally more* weight in the positive class average, not less — less data averaging out variance, not more.

**Not tested live** — the eval alone was decisive enough not to risk repeating Round 2's false-positive storm. Model file on disk was immediately reverted back to the Part 4 negatives-only version (same procedure: clear `real_*.wav` from `positive_train`/`positive_test`, empty `real_positive/`, retrain) *before* any restart could pick up the bad one — FRED itself was never restarted onto this model, so live behavior was never affected. The 20 files were duplicates of clips already in `real_positive_disabled/`, so nothing was lost by deleting them from `real_positive/`.

**Current model state (end of Part 5):** back to Part 4's negatives-only model (0/594 phonetic-neighbor FPs, 2/419 general FPs) — the validated known-good baseline. Superseded by Part 6 below.

## Part 6 — Round 3 deployed live anyway, on request

Despite Part 5's synthetic eval, the user asked to put the 20-clip model live for a real test rather than take the offline numbers as final — a reasonable call given eval and live behavior had already diverged once this session (the AGC caveat in Part 4). Rebuilt the exact same 20-clip selection (same `random.seed(42)` file list), retrained, installed, restarted FRED.

**Live result, ~36 minutes observed (14:15-14:51):** 3 fires (0.426, 0.942, 0.991) — nowhere near Part 4's 43-fires-in-an-hour storm from the 46-clip version. Meaningfully better live than the eval numbers suggested, though the observation window is short and partly contaminated by FRED restarts from Parts 7-9's unrelated fixes (each restart's first `resume()` logs unpaired, same artifact noted back in the very first session-log investigation). Not yet a full validation either way — the honest status is "looks fine so far," not "confirmed good."

**Current model state: the 20-clip real-positive model, live.** This is a live experiment, not a settled outcome — if false positives climb, the Part 4 negatives-only revert procedure (documented above) is the known-good fallback, unchanged and still valid.

## Part 7 — TTS playback cutting off ~1s early

Unrelated to wake-word — reported once wake-word work was live and the user started using FRED normally again. Symptom: replies losing their last ~1 second, especially on Bluetooth output.

**Root cause:** the mirror image of a bug already fixed once (`TTS_PREROLL_SEC`, 2026-08-10 — Bluetooth links ramp up slowly from idle, swallowing the first ~1-1.5s). sounddevice's `Stream.stop()` (`Core/audio/tts_kokoro.py`) only blocks until PortAudio's own host buffer has drained — it has no visibility into a Bluetooth link's own downstream buffering/transmission latency past that point. `stream.stop(); stream.close()` right after the last real write could tear the stream down while the device was still catching up on genuinely spoken audio.

**Fix:** `TTS_POSTROLL_SEC` (`Core/config/settings.py`, starting value 1.0s, same "floor not a ceiling" framing as the preroll constant) — a trailing silence write before `stop()`/`close()`, giving the device runway to actually finish. Only on natural completion (`natural_end` tracked explicitly through the play loop) — skipped on an interrupt, which should still cut off promptly rather than linger. Tests: `Core/tests/test_tts_postroll.py`.

## Part 8 — Dispatcher swallowing entire sentences as filenames

Real failure, caught from the session log at the user's request ("it failed badly just now, look into that"). Transcript:

> "Create a file on the desktop called daily logs dash today's date and then in it you can write three tasks which is CHEMMAS, second one is ENGMAS and then third one is SSMAPS."

Actual result: a file literally named `on the desktop called daily logs dash today's date and then in it you can write three tasks which is CHEMMAS, second one is ENGMAS and then third one is SSMAPS..txt`, in the wrong directory, empty — no date resolved, no content written, wrong location.

**Root cause:** `Core/orchestrator/dispatcher.py`'s `_route_create_text_file`/`_route_create_folder` regex (`^create (?:a |an )?(?:text )?file (?:named |called )?(?P<target>.+)$`) captured *everything* after "called" as the literal filename, and neither route ever sends a `content` argument or resolves a date — they can only ever handle a bare "create a file called X". This is the deterministic-dispatcher-intercepts-before-the-LLM class of bug already fixed once for web search (2026-08-01, `_route_web_search`'s pronoun decline).

**Fix:** same pattern — `_FILE_COMPLEXITY_CUES` (write/contains/today's date/"and then"/etc.) makes both routes decline (return `None`) and fall through to the LLM tool path, which can actually fill in `content` and a real date, on anything more than a bare filename. Garbage file removed from disk. Tests: `Core/tests/test_dispatcher_create_file.py` (includes the real failing transcript as a regression case).

## Part 9 — Wake-triggered turns "still listening" for several seconds on silence

Also reported live. Not actually the wake-word silence-timeout itself (`SILENCE_TIMEOUT_SECONDS = 1.2` in `wakeword.py`, deliberately generous so a real trailing word isn't cut off, and unchanged all session) — most of the delay was `Core/audio/stt_whisper.py`'s `stop_and_transcribe()` running a **full Whisper inference pass** over a buffer that never had any sound in it at all, every single time a false wake trigger was followed by true silence.

**Fix:** `_SILENCE_PEAK_FLOOR` — checks the recorded buffer's peak amplitude (not mean RMS, which a mostly-silent buffer would wash out even with one real loud word in it) against the same floor `wakeword.py` already uses (`SPEECH_RMS_FLOOR = 0.02`), and skips straight to `""` without ever touching the model if the whole buffer never crossed it. Sits alongside the existing too-short-clip guard (`min_seconds`). Tests: `Core/tests/test_stt_whisper_silence_skip.py` (asserts the model is never touched for pure silence, and still reached normally for real signal).

## Part 10 — VatsalDaPro home-folder alias

Requested directly, delegated to a subagent. Investigated every hardcoded folder-name/alias table in the codebase (`_HOME_FOLDERS` in `assist_tools.py`'s `resolve_user_path`, app-launch aliases in `system_tools.py`, the vault's hardcoded-filenames list) and found `_HOME_FOLDERS` was the only genuine fit — the others are unrelated lookup tables that happen to share the word "alias". Added `"vatsaldapro"`; `"VatsalDaPro/x"` now resolves to `~/VatsalDaPro/x` case-insensitively with real on-disk casing preserved, same as `Downloads`/`Documents`/etc. already did. Verified it doesn't collide with `DEFAULT_DOCS` (the vault, a separate location entirely). Tests: extended `Core/tests/test_vault_relative_paths.py`.

## What's still open

- **Wake-word model is a live experiment** (Part 6) — the 20-clip real-positive model looked fine in ~36 minutes of observation but isn't fully validated. Keep half an eye on `Core/data/wakeword_log.jsonl` for a false-positive climb; the Part 4/5 negatives-only revert procedure is the known-good fallback if it's needed again.
- Two of the three real-voice attempts (46 clips, then 20) showed worse *offline eval* numbers than the negatives-only baseline; only the 20-clip version has been tried live, and it held up better than the eval predicted. That gap between eval and live behavior (also seen once before, Part 4's AGC caveat) means the synthetic eval script is a useful sanity check but not a reliable predictor of real-room false-positive rate — worth remembering before trusting it alone on any future retrain.
- The persistent "hafer fredell"/"heyward fred" adversarial false positives have survived every retrain this session unchanged — a genuine weak spot in the auto-generated adversarial-phrase negative set, not urgent but worth addressing eventually (upweight `N_ADVERSARIAL_PHRASES` or hand-curate against these specific patterns).
- If real-voice training is revisited again: probably needs a much larger sample (the original 105-line script, done in full, one take per file as originally asked, rather than 54 clips salvaged from a merged continuous recording), consistent volume/distance rather than deliberately varied, and holding out entire takes for test rather than random individual clips so near-duplicate-condition leakage doesn't inflate apparent quality. None of that has been tried yet.
- `TTS_POSTROLL_SEC = 1.0` and the STT silence-peak floor are both first-pass estimates from a single live report each, same as `TTS_PREROLL_SEC` was before it got tuned up to 1.5s against real hardware feedback — treat both as floors, not final numbers, if the symptoms (tail-clipping / still-transcribing-silence) recur.
- The 54 segmented real-voice recordings (all of them, not just the 20 or 46 used) remain preserved in `Core/data/wakeword_training/real_positive_disabled/` for any future attempt.
