# Live runs log

One entry per live run of the full system: setup, what the event log and console showed, what was changed afterwards. Newest last. Counts come from `FRED_Private/events/<day>.jsonl` and the console output.

## Run 1 - 2026-10-08, evening (system default mic and speaker)
- Setup: pre-fix settings. Result: the pill flashed about once a second, the wake word fired once, then a self-sustaining loop of Whisper hallucinations and barge-ins. Stopped by hand.
- Diagnosis and fixes: see `production_fixes.md` (items 1-8).

## Run 2 - 2026-10-08, ~22:44 (lavalier mic, Rockerz speaker, first fixes in)
- Setup: commit `d65bffb`. A TV was playing in the room.
- 153 s window: 14 transcripts, 13 turns, 4 wake events, 15 spoken replies. Room noise was far calmer than run 1 (no pill flicker loop), but about a third of the utterances were still noise: "Thank you." (peak 0.05, 8 times), "Hey friend.", "Most of the time.", "Once in a day."
- Real usage that worked: "Hey Fred", "What's the time?" (answered correctly through the time tool, 735 ms).
- Problem found: from the 8th turn on, nearly every reply was literally "(interrupted)" or "(interrupted, sir)". Cause: when a spoken reply was cut by barge-in, the history stored the placeholder "(interrupted)" as FRED's words, and the 4B model copied it for the next turns. It was never an actual interruption of the wake word.
- Problem found: Whisper ghost phrases from TV noise ("Thank you.") arrived as real turns.
- Changes after the run: no placeholder in history (a turn nobody heard is forgotten; a partly heard turn keeps the heard words); ghost phrases dropped when the audio peak is below 0.1 (`stt_ghosts`, `stt_ghost_peak`); tray icon and HUD launcher ported from the old build (below).
- Stopped by hand. Next run: check that "(interrupted)" never appears, "Thank you." from the TV is gone, and the tray opens the HUD.

## Tray icon and HUD (also 2026-10-08)
Ported from the old FRED (`pill_app._start_tray`, `utils/hud_manager.py`) and rebranded "Mark I": `ui/tray.py` (left click Show HUD, Restart Mark I, Quit Mark I) and `ui/hud_launcher.py` (starts the HUD server on :8777 as its own process, opens a kiosk Chrome on click). Both start in `engine/live.py` unless `--no-hud`. `pystray` added to requirements. The HUD page itself still says "FRED" in its title and header; not rebranded yet.

## Run 3 and 4 - 2026-10-08, late (speed 1.17x)
- Run 3 was ended from the tray (clean exit). Run 4 was killed by hand: with the TV on, background voices still became turns ("What are you doing?" x3 from a 1.4 s clip, "Thank you." and similar). Volume cannot separate them: real phrases and TV clips both peak 0.05 to 0.13.
- Fix: the voice-ID gate switched on (`voice_id_enabled` True, `voice_threshold` 0.33), enrolled from 11 of Vatsal's own saved lavalier clips (the wake word and plain commands; the file stays in `FRED_Private`, never in the repo). Leave-one-out check on the saved clips: Vatsal 0.39 to 0.62, TV/room noise 0.15 to 0.28.
- Caveat: the gate only judges clips of 1 s or more (`voice_min_s`); shorter ones pass as "unknown". Eleven clips is a small enrolment; more real speech will make it steadier.

## Run 5 - 2026-10-08, ~23:04 (voice ID on, TV in the other room)
- Voice gate worked in use: about 12 turns, all Vatsal's own speech, no TV turns. Misheard words still happen on short phrases ("Dear Fred.", "Alfred.", "Hey friend." for "Hey Fred").
- Wrong route found: "Can you remember all this?" with a table on screen went to memory search ("I could not find that"). "remember" plus "all this" counted as asking about the past. Fix: "all this / these / those" no longer trigger a memory search (`turn_recall._PAST`), and `look_at_screen` got three examples ("can you remember all this", "what does this table say", "look at this table"). The new examples take effect at the next start.
- Also from run 5: "Who am I?" went to the screen tool and answered nonsense. Fixed: "who am I" and "what's my name" are small talk, answered by the model. "Sir" is now on every line (`sir_rate` 1.0, the fillers too) and the system prompt says MUST for every model. When the clock ignores an utterance while FRED is speaking (a voice that is not Vatsal, or one too short to judge) FRED now keeps talking: barge-in needs the voice check to pass (before, it cut in at 0.5 s because short clips were "unknown").
- Usage data: the central store started, see `usage_data.md`.
- Also from run 5: "Solve this - x*x + 4x + 4 = 0 for x" went to chat (the calculate skill does arithmetic and units, not equations), the model offered to solve it, and after "Yes" it wrote a tool call as plain text (`<function name="calculator">...`). That text was spoken as garbage and the HUD page swallowed it as an HTML tag, so the HUD showed no answer. The model never reached a tool (0 rounds). Fixed: a reply containing tool-call markup is replaced by "Sorry, I couldn't work that one out." and is never spoken, shown or remembered (`engine/loop.py`). Equation solving is a missing capability, logged under Phase 8.1.
- Phase 8.1 now also lists plugging in DU_Automation as a tool (logged only, see `Core/PLAN.md`).

## Soak run - started 2026-10-09 00:05 (silent, 5.5 h, voice gate on)
Idle overnight run (no screen change, no audio) to check stability: process alive, RAM and GPU memory over time, `Data/` growth, the night jobs. Health sampler every 10 minutes (process count, RSS, GPU memory and load, size of `Data/`). The playground server had to be started first (it was down). Result to be added here in the morning.
- Decision logged the same night: the playground becomes fully independent of FRED at the end of Phase 8 (see `Core/PLAN.md`).

## Soak run findings - 2026-10-09 night (found by the soak, as intended)
- **Memory leak:** the FRED process grew about 35 MB a minute while idle (56 MB to 4.5 GB in 90 minutes, a straight line). The Python-level profile (tracemalloc) stayed flat at 164 MB, so it was native memory. An A/B of four 11-minute runs showed it: default 35 MB/min, `--no-presence` 0 MB/min. So the camera presence was the leak.
- **Cause:** every webcam open and close inside the process leaks about 0.6 MB (OpenCV DirectShow), measured at 200 opens in a standalone test; the face detector itself is flat once loaded. Presence opens the camera every 7 s.
- **Fix:** each snapshot is now grabbed in a short-lived child process (`senses/camera.py`, `_grab_proc`), so the leak dies with the child and the camera is still released at once.
- **Also seen:** the real webcam (index 1) returned one frozen 640x480 picture at 2 AM and indices 0 and 2 stay frozen, so the PHOCUS was probably unplugged or asleep. Presence then faults and holds its state (by design). Check the USB connection.
- **Leak fix confirmed:** the restarted soak stayed flat at 1200 MB (was 2500 MB and climbing at the same point).
- **Night job failed on a stale window:** the nightly memory job got `HTTP 409: window already open` from the playground, because a previous run that was killed mid-job had left the playground's window open. The job now adopts an open window instead of failing (`memory_bridge/jobs.py`); its stash is the live stack, and closing it restores that.

## Soak result - night of 2026-10-08/09 (final 3 h run, 02:41 to 05:41, silent, voice gate on)
- **Stability:** ran the full 3 h and ended on its own timer, no crash. Memory stayed at 1.2 to 1.5 GB (+220 MB over 3 h, was +35 MB a minute before the camera fix). GPU 11.7 GB (it touched 15.8 of 16.3 GB while the night job had a larger model loaded; nothing was killed). The nightly memory job ran after the window fix.
- **Idle behaviour:** 3,537 events. 344 utterances heard, 343 ignored as "not addressed" and 1 as "not Vatsal"; the pill would have shown 3 times. 2 transcripts, no engine turn, no speech out. Almost all utterances came in the last two hours (168 in the 04:00 hour, 163 in the 05:00 hour, against 15 before). CORRECTION (first draft of this report guessed "voices in another room", which the clips disprove): the 344 clips are not speech. They are very quiet (peak about 0.01, -40 dB) and 82 to 88 percent of their energy is below 300 Hz with the strongest component at 47 to 52 Hz, i.e. mains hum or a low rumble, in short bursts (median 0.9 s, one every 8 s on average) from about 04:00. The source in the room is unknown (something that switched on around 04:00 near the mic or its cable). The voice detector read the hum as speech. Fix: the detector now listens only above 200 Hz (`vad_highpass_hz`); replayed on the saved clips the false onsets went from 6 to 0 while real speech stayed 26 of 26. A first attempt, a minimum frame loudness, was dropped because it also cut real quiet speech (23 of 24 clips down to 10).
- **Wake word:** 8 wake events overnight (scores 0.38 to 0.90, two of them 0.83 and 0.90) and 9 near-misses, with nobody addressing it. The clips are in `Data/wakeword/`: listen to find out whether these are real "Fred"-like sounds or false triggers. Tuning input for `wakeword_threshold`.
- **Webcam:** 1,539 camera faults ("frozen frame"), 0 snapshots: the PHOCUS webcam returned one frozen picture all night, so presence never worked. Probably unplugged or asleep; to check on the USB connection.
- **Screen:** 15 `BitBlt` capture errors (screen locked or asleep); handled, nothing broke.
- **Data store:** `Data/` holds 392 clips, 15 MB.

## Run 8 - 2026-10-09 ~06:41 (voice gate on, 200 Hz detector filter, headphones switched off mid-session)
- Understanding worked ("Hey Fred", "How are you?", hold-to-talk all became turns), but replies were not spoken: opening the speaker crashed (`PortAudioError: Blocking API not supported`, 10 times). The only output matching "Rockerz" was the Bluetooth hands-free phone endpoint (WDM-KS, 8 kHz, mono); the stereo entry was gone because the headphones were off. Fixed: unusable entries (WDM-KS, hands-free) are skipped and FRED falls back to the system default speakers, decided every time it starts speaking. Limit: PortAudio lists devices once when FRED starts, so headphones switched on later are only picked up at the next start; turned off later, the next sentence falls back at once.
- Hold-to-talk (Ctrl+Alt) registered but sometimes started no turn: the capture began only when the voice detector heard speech. Key-down now starts it directly, release ends it.
- The reply often said "sir" twice ("I'm doing well, sir. How can I help you, sir?"): the MUST prompt made the model add it. Prompt now says exactly once, at the end.
- Voice ID rejected some of the user's own short "Hey Fred" clips (`not_vatsal` right after the wake word) while accepting the follow-up; the threshold 0.33 is borderline for 1 s clips. To retune from the Data clips.

## Run 9 - 2026-10-09 ~06:46 (speakers, headphones off)
- With the PC speakers as output the mic heard FRED's own replies and some became turns ("It's 6.46 a.m. on Friday.", "That's correct, sir."), followed by garbled answers. Voice ID rejected most echo (scores 0.21 to 0.28) but not all. Fix: a heard line of 3+ words whose words are 80% FRED's own recent lines is dropped as echo (`engine/sir.is_echo`, saved under `Data/clock` as reason "echo").
- The wake word was sent to the model with the question ("Hey Fred, how are you?"). Now stripped from the start of the text; a bare "Hey Fred." stays.
- Voice ID also rejected the user's own "What's the date?" at 0.28 (their good clips score 0.41 to 0.43); threshold 0.33 left as is, to retune from the clips.
- Remaining by design: for 8 s after a reply any speech is treated as a follow-up, so the user talking to someone or something else right after a reply is answered ("provided a prompt"). Candidate: shorten `followup_s` or require voice ID to pass in the window.

## Voice ID retune - 2026-10-11 (from the saved clips of runs 8 and 9)
- Method: scored every saved clip of 1 s or more, transcribed it, and labelled FRED's own voice by matching its spoken lines.
- Before: the user's own speech scored 0.26 to 0.58 (several rejected under 0.33); FRED's voice from the speakers 0.21 to 0.36; TV/mumbles 0.14 to 0.37.
- Change: 9 more of the user's real-use clips added to the enrolment (11 clips picked by text; garbled echo such as "912,000." was left out on purpose, enrolling FRED's own voice would make it pass). References 11 -> 20 (`voice_refs.json`, outside the repo, backup `.bak` beside it). FRED's own voice still scores 0.21 to 0.36, unchanged.
- After: the user's previously rejected clips now score 0.47 to 0.63; everything else stays at or under 0.37. Threshold 0.33 -> 0.38 (`voice_threshold`) to use the new gap. Takes effect at the next start.
- Not done yet: the follow-up window (`followup_s`) decision, waiting for the user's pick.
- Follow-up window (2026-10-11): voice ID already gates every turn of 1 s or more, the follow-up window included; shorter clips pass as unknown (a bare "yes" to a yes/no question must work). So requiring it more would change nothing, and the user's own voice talking to someone else cannot be told apart by voice. The only real lever is the window length: `followup_s` 8 -> 5 s.

## Multi-step maths (2026-10-11)
No equation solver: the model breaks the problem into plain arithmetic and calls `calculate` once per step. Before: "Solve x^2 + 4x + 4 = 0" went to chat, 0 tool rounds, worked out in the model's head. Fix: `calculate`'s description now tells the model to decompose (read a, b, c, then the discriminant, then the two roots) and has 6 new examples, so the embedding routes such questions to it. After (4 typed questions, playground model): 3 of 4 called `calculate` (quadratic 2 rounds, roots 1 round, average speed 1 round); "Solve 3x + 7 = 25" still answered in the head (0 rounds, correct answer). Open: the 4B model does not always decompose; a harder nudge is a prompt rule or a retry when the question has numbers but no tool was called.

## Run 12 findings - 2026-10-11 ~13:00 ("it only hears me when I focus")
- Cause 1, voice ID too strict: after "Hey Fred" many of the user's own sentences were rejected (`not_vatsal`). Scores of the user's real, un-enrolled speech: 0.29 to 0.41 ("Hey Fred." 0.30, "How are you doing?" 0.35, "I'll see you next time." 0.32), below the 0.38 set that morning. The earlier retune measured clips that had just been enrolled (they match themselves), so it overstated the gap. Fix: `voice_threshold` 0.38 -> 0.25 for addressed turns; the wake word, the follow-up window and the echo filter carry the rest.
- Cause 2, the echo: FRED's voice from the speakers scores 0.21 to 0.36, so with a lower threshold it could interrupt itself. Fix: interrupting FRED while it speaks now needs either the wake word or a strong match (`barge_voice_threshold` 0.45), not the plain threshold. Self-check covers a foreign-scoring voice that says the wake word.
- Seen, not changed: short "Hey Fred" clips (0.7 to 1.3 s) are often misheard by Whisper ("Be afraid.", "He's ready to die."); and steady room noise at peak 0.01 keeps producing 1 s clips.
- Run 13 (13:13): after loosening the voice gate it listened too much: "Woo!" and "I'm sorry, I'm sorry." became turns, through the follow-up window that opens after FRED speaks (here after the spoken news offer). Fix: a turn addressed only by the follow-up window (no wake word, no hotkey) now needs a voice match of 0.33 (`followup_voice_threshold`); with the wake word the 0.25 stays. `followup_min_s` 0.4 -> 0.6 so a one-syllable noise is not an answer. Self-check 10b covers a 0.30 match: refused as a follow-up, accepted after the wake word.
- News check (13:15): the first spoken offer worked at start ("Good morning, sir. The 06:00 news is ready, 9 stories. Shall I read it?", 9 s). Only one edition exists (my test build at ~12:58, built without memory, so 0 news events in the memory yet); the live service builds the next one at 18:00. The test edition's stories are not in the memory.
