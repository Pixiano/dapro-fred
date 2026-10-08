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
