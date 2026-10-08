Progress snapshot at about 40% (P0 to P3 built, the tools rebuilt as skills, brutal test done, 2026-10-07). The P0 to P1 detail is in `20_percent.md` and the P2 to P3 detail in `35_percent.md`; they are not repeated here.

## The idea
Old FRED was one big program where listening, looking, thinking and tools all knew about each other. The rebuild keeps every layer separate, each with a clear input and output and its own self-check, so one part can be replaced without touching the rest. This stretch changed how tools are built and then tried to break everything built so far.

## Skills: tools as drop-in folders
- **Format:** every tool is a folder with a manifest (name, description, arguments, at least 3 example phrases, what it is not for, safety flags), a `run.py` and a self-check. The loader finds them; nothing is registered by hand.
- **Failure handling:** a broken built-in skill stops start-up. A broken add-on is skipped and listed, so one bad skill cannot take FRED down.
- **11 built-in skills:**
  - Memory: recall, remember, forget, correct.
  - System: time, days until, volume, system stats (all, cpu, gpu, ram or disk).
  - Self: what can you do, read own docs.
  - Math: calculate.
- **Routing:** the choice of tool is made by meaning (the embedder compares your words to each skill's example phrases), not by cue words. All 31 example phrases route to their own skill.
- **Refusing the wrong job:** a manifest can list `refuse_if` patterns on your words, so a skill declines a request that is not its job. Volume refuses microphone and device-switching wording.
- **Old tools:** the old 44 tools now exist only for the old tests.
- **Temperatures:** the GPU temperature is reported. The CPU temperature is not available on this PC.

## The embedder
- **Qwen3-Embedding-0.6B (1024 dimensions)** replaced the earlier one, and its thresholds were measured again. 70 of 70 skill examples rank first.
- **EmbeddingGemma-2** was tried and does not load on our llama.cpp build, so it is not used. Revisit when llama.cpp supports it, since its multimodal side fits your plans.

## Voice ID switch
- **`voice_id_enabled`** is off by default until the gate is trained on your own captured live recordings.
- **Why:** on real live-mic clips the current threshold would reject about 89% of them.

## The brutal test (full numbers in `Mark_I/Documentation/2026/10/07/brutal_test_report.md`)
- **Replay of your real usage:** 600 turns with the real brain. 0 crashes, and no destructive action ran without a yes. Median reply 0.8 s, and memory use stayed flat.
- **Fuzzing, memory under load, self-checks:** all clean. 56 of 56 self-checks pass.
- **Full 11-skill replay:** 600 real utterances, 0 crashes, no unconfirmed destructive action, 4 faults, median 1.45 s on a shared GPU.
- **Bugs it found, now fixed:**
  - "Do you have X" was answered as an error and made FRED report a fault. Faults went from 9 to 0.
  - A bare recall once got described as "your screen".
  - A skill that exits at import could escape the loader.
  - The brain would change the real volume for microphone and device commands; the `refuse_if` guard now stops that.

## Open items
- Time, volume, stats and math work, but your top requests (device switching, screen questions, reminders, lockdown) still had no skill at this point. FRED says so instead of faking them.
- Wake word: 78.6% of real "Hey FRED" caught, with 37% false fires on real non-wake speech (frozen, not touched).
- Presence flips more than 4 times a day on your data.
- Vision 11 of 12; OCR 76% exact.
- The brain is not deterministic, so conversation scores move between runs.

## What this means for real-life FRED
- **It can now:** hear you, know when you are done, think with tools, remember and recall, describe its own skills, and answer in text, all in one loop on the real microphone.
- **Not yet:**
  - No voice replies (P4) and no UI (P5).
  - Nothing proactive (P6) and no nightly memory jobs (P7).
  - No 8-hour soak (P8).
  - Add-on skills for what you ask most are not designed yet.
- **Next:** voice out, the on-screen pill, and the add-on skills.
