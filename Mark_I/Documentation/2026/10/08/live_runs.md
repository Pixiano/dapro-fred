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
