Progress snapshot at about 40% (P0 to P3 built, tools being rebuilt as skills, brutal test done, 2026-10-07). Earlier detail is in `20_percent.md` (P0 to P1) and `35_percent.md` (P2 to P3); this one adds what changed since and sums up everything built.

## The idea
Old FRED was one big program where listening, looking, thinking and tools all knew about each other. The rebuild keeps every layer separate, each with a clear input and output and its own self-check, so one part can be replaced without touching the rest.

## Everything built, in one pass
- **P0, plumbing:** one settings place (private data lives outside the repo), logs that never crash a turn, one GPU queue with priorities, one way to talk to the models, a tool runner that checks arguments and never crashes the loop, and a playground with four model slots (chat, embed, talk, vision).
- **P1, senses:**
  - Hearing: microphone, the frozen wake word, speech detection, Whisper on the GPU (about 0.2 s for a short command), and a hold-to-talk hotkey.
  - Seeing: screen change detection, Windows OCR, and a vision model that describes the screen when asked.
  - Presence: camera snapshots every 7 s say whether someone is at the desk.
- **P2, turn clock:** a 160 ms clock decides keep listening, end of turn, barge-in, backchannel, or ignore, with a 0.9 s hard cap and Smart Turn judging whether a sentence is finished. Voice ID exists but is switched off (see below).
- **P3, the thinker:** the brain (Qwen3.5-4B) with a tool loop and its safety rules: argument fixing, no repeated failed calls, 4 rounds and 4 calls per message, a yes/no question before anything destructive, no sending after reading untrusted content, and guards against claiming things were done that were not.
- **Memory bridge:** turns, tool calls, speech, perception and errors are archived on their own streams, and each turn gets your personal model. The deeper search runs only when you refer to the past.
- **Embedder:** Qwen3-Embedding-0.6B (1024-d), thresholds re-measured for it.
- **Skills (new this stretch):** tools are now drop-in folders, 11 built-in: memory (recall, remember, forget, correct), system (time, days_until, volume, system_stats with a `part` of all, cpu, gpu, ram or disk), self (what_can_you_do, read_own_docs), math (calculate). A broken built-in stops start-up; a broken add-on is skipped and listed. A manifest can also list `refuse_if` patterns on the user's words, so a skill refuses a request that is not its job (volume refuses microphone and device-switching wording). The old 44 tools exist only for old tests. GPU temperature is reported; the CPU temperature is not available on this PC.
- **Voice ID switch (new):** `voice_id_enabled`, off by default, until the gate is trained on your captured live recordings.

## The brutal test (full numbers in `Mark_I/Documentation/2026/10/07/brutal_test_report.md`)
- **Replayed your real usage:** 600 turns with the real brain: 0 crashes and no destructive action ran without a yes. Median reply 0.8 s, memory use flat.
- **Fuzzing, memory under load, self-checks:** all clean; 56 of 56 self-checks pass and 31 of 31 skill examples route to their own skill.
- **Bugs it found and I fixed:** "do you have X" answered as an error and made FRED report a fault (9 faults to 0); a bare recall once got described as "your screen"; a skill that exits at import could escape the loader.
- **Full 11-skill replay:** 600 real utterances, 0 crashes, no unconfirmed destructive action, 4 faults, median 1.45 s on a shared GPU. It exposed that the brain would change the real volume for microphone and device commands; a `refuse_if` guard now stops that.
- **Embedder:** EmbeddingGemma-2 was tried and does not load on our llama.cpp build, so Qwen3-Embedding-0.6B stays (70 of 70 skill examples rank first). Revisit when llama.cpp supports it, since its multimodal side fits your plans.
- **Honest gaps:**
  - Time, volume, stats and math now work, but your top requests (device switching, screen, reminders, lockdown) still have no skill; FRED says so instead of faking them.
  - Wake word: 78.6% of real "Hey FRED" caught, 37% false fires on real non-wake speech (frozen, not touched).
  - Voice ID on real live-mic clips would reject about 89% at the current threshold.
  - Presence flips more than 4 times a day on your data.
  - Vision 11 of 12; OCR 76% exact.
  - The brain is not deterministic, so conversation scores move between runs.

## What this means for real-life FRED
- **It can now:** hear you, know when you are done, think with tools, remember and recall, describe its own skills, and answer in text, all in one loop on the real microphone.
- **Not yet:** no voice replies (P4), no UI (P5), nothing proactive (P6), no nightly memory jobs (P7), no 8-hour soak (P8). Add-on skills for what you ask most (output and input device switching, screen questions, reminders, lockdown) are not designed yet.
