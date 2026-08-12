# FRED — Wake-word retraining session (2026-08-11 → 2026-08-12)

Record of what was tried, what broke, what got fixed, and where things stand. Companion reading: `Core/input/wakeword_train.py` (the pipeline itself, heavily commented inline) and `Core/config/settings.py`'s `WAKEWORD_THRESHOLD` history comment (unchanged this session — every change here is data-side, not the threshold).

**Nothing here is committed to git except the code changes** (`wakeword_train.py`, two new test files). Trained model files, recordings, and this doc itself are local-only.

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

**Current model state:** back to Part 4's negatives-only model (0/594 phonetic-neighbor FPs, 2/419 general FPs) — the validated known-good baseline.

## What's still open

- Two real-voice attempts (46 clips, 20 clips) both made false-positive rate worse, in different ways and by different margins. Real-voice training is **not currently viable** with this recording (171.5s, 54 usable segmented clips) — pending either a genuinely larger/more consistent recording session, or a different approach (see below), the negatives-only model is what should stay deployed.
- The persistent "hafer fredell"/"heyward fred" adversarial false positives have survived all four retrains unchanged — a genuine weak spot in the auto-generated adversarial-phrase negative set, not urgent but worth addressing eventually (upweight `N_ADVERSARIAL_PHRASES` or hand-curate against these specific patterns).
- If real-voice training is revisited: probably needs a much larger sample (the original 105-line script, done in full, one take per file as originally asked, would give far more and cleaner data than 54 clips salvaged from a merged continuous recording), consistent volume/distance rather than deliberately varied, and holding out entire takes for test rather than random individual clips so near-duplicate-condition leakage doesn't inflate apparent quality. None of that has been tried yet.
