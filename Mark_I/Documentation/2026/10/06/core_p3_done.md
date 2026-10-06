# Core P3 (the thinker) done, 2026-10-06

**Built (under `Mark_I/Initial/Core/`)**
- `engine/`: `classify.py` (cue groups, embedding narrowing to 2-6 tools, compound/social/reasoning detection), `loop.py` (Engine: coercion, no identical retries, 2-failure stop, 4 rounds/4 calls caps, parallel calls, chain offer, confirmation, read/send separation, reply guards, refusal nudge once), `gates.py`, `prompt.py`, `cli.py` (`--live`), `live.py` (real-mic voice loop, replies printed; TTS is P4).
- `tools/` (44 tools, 18 groups): clock (time, calculate, days_until), audio_dev, files, windows (+close_window), reminders (timer, reminder, alarm, recurring), system (brightness, media, clipboard, processes, screenshot, network, lock, power), apps (launch, website, path), vision (screen, camera), agenda, selfdoc (describe_self), memory tools (show/remember/correct/forget, recall_recent_conversation).
- `memory_bridge/`: `link.py` (opens the memory in-process), `feed.py` (turns, tool calls, speech, perception, errors into the archive streams), `turn_recall.py` (per-turn personal model, judged search on memory-bound turns, "digging deeper" line, alias question).
- Tests: `test_engine.py` (16 scenarios), `test_memory_bridge.py`, `replay_voice.py` (reply 0.6-2.0 s after audio end), `acceptance.py` (old cases).

**Deferred (need a scope decision):** network (weather), phone, Gmail, WhatsApp, smart AC, restart_fred, reindex/search index, ask_about_myself. Those cases count as "unsupported" in acceptance.

**Numbers:** see `Core/Progress/35_percent.md`. The overall tools score is held down by unsupported cases; supported-only is ~88-90%. The brain is stochastic: conversation moves 16-19/21 between runs.

**Watch:** the model sometimes refuses or answers without calling a tool (a refusal nudge fires once); `say` tools can answer "Done." to a how-to question (neg-04).
