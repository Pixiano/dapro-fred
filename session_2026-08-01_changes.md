# FRED — Session Changes & Open Items (2026-08-01)

Everything built and fixed in this session, followed by what's still open. Companion to `known_issues_2026-08-01.md` (the triage doc that started it) — this one is the record of what actually happened.

**Nothing here is committed to git.** 10 files modified, 4 new modules, +409/-22 lines.

---

## Part 1 — What was done

### Round 1: the original triage list

Fixed against the six bugs, two structural gaps, and three suggestions from the feedback session.

| # | Item | Outcome |
|---|---|---|
| 1 | Web search loses context | Fixed — root cause was **not** the LLM |
| 2 | Reads raw file paths aloud | Fixed — `search_files` now returns speech-safe summaries |
| 3 | Voice too low | Two attempts, **both reverted** (see below) |
| 4 | File search not semantic | Fixed — both cheap and real tiers built |
| 5 | Clipboard misfires | Fixed — confirmed via 3 real log hits |
| 6 | App launching unreliable | Fixed — confirmed via log evidence |
| A | Live coding | **Dropped** — conflicts with persona.md's stated scope |
| B | No proactivity | Built — three checks |
| S1 | Git integration | Built — read-only |
| S2 | Found-things index | Built |
| S3 | Shutdown ritual | **Deferred** by choice |

**Detail on the ones where the real cause differed from the guess:**

- **#1 Web search** — not a context problem at all. `Dispatcher._route_web_search` intercepted "search ___" phrasing *before* the LLM ran, so conversation history was never in play. `"Search it on the web. It has been released."` dispatched with `query="it on the web..."` verbatim. Fix: pronoun-led queries decline the deterministic route and fall through to the LLM tool path. `Dispatcher.match()` now supports a handler returning `None` to decline.
- **#5 Clipboard** — three real transcript hits, all `"project copy"` (a project name) matching the bare cue `"copy"`. Those phrases had *no other* category cue, so clipboard was the only tool menu offered. Cues narrowed to `clipboard`/`paste`/`pasted` plus demonstrative pairs (`copy that`, `copy this`).
- **#6 App launching** — every logged Spotify failure had a trailing period from STT: `"Spotify."` → exe name `"Spotify..exe"`. Fix: strip trailing punctuation/filler. Also added `%APPDATA%` to search roots (Spotify's actual install location, previously missing) and a Start Menu `.lnk` search layer.

**New modules:** `tools/found_cache.py`, `tools/git_tools.py`, `tools/smart_search.py`, `orchestrator/proactive_checks.py`.

---

### Round 2: bugs found from your session logs

You reported "many errors, only app launching works." Reading the logs found five real bugs — four of which I would not have found by guessing.

**1. `NameError: THINKING_MARKER` — the big one.**
A leftover reference in `llm_client._strip_thinking()` from before this session, when tier prompt markers changed from one global variable to a per-tier dict. Fired on **every** `Standard`-tier tool-calling turn, got swallowed by the fallback handler, and surfaced as the generic *"I'm experiencing a cognitive malfunction, boss."* Weather appeared to work only because it routes through the Dispatcher and never touches the LLM. This one bug explains most of what looked like unrelated failures.

**2. Fabrication of personal health data — the serious one.**
Asked to review fitness progress, FRED invented 68.5 kg, BMI 19.2, 12.8% body fat, and a complete bloodwork panel. `personal/fitness.md` actually says 73.5 kg / 178 cm and contains **no bloodwork values at all**.

Two causes, both fixed:
- Vault chunks were truncated at 420 chars, so the biometrics *table* arrived cut mid-row — which reads to a model like a form to complete. Raised to 1200 (clears every table in `personal/`).
- Nothing told the model the vault was the only admissible source for personal facts. Now stated explicitly, including that a truncated excerpt means missing data, never an invitation to fill in the rest.

Verified after the fix — real values quoted, no invented BMI/body fat/bloodwork, and "find my health logs" now honestly reports not finding it instead of inventing a sleep/mood/energy report.

**3. `"Search my desktop for dossier.pdf"` → web search.** Same Dispatcher class as #1. Local-machine cues (desktop, downloads, folder, drive, vault, …) now decline the web route.

**4. Invented file/repo paths.** `find_file_smart` received `C:\Users\%username%\Desktop` (literal, unexpanded) and `git_log` received a guessed relative repo path — both hard-failed the turn. Both now expand env vars and fall back to a sensible default rather than dying on a guessed path.

**5. Empty spoken replies.** Three turns logged `"text": ""` with `spoken: true` — FRED said nothing at all. With thinking-on Qwen3-8B, when reasoning overruns `max_tokens` the block never closes, and `_strip_thinking` correctly returns `""` rather than speak raw chain-of-thought — but nothing substituted a reply, so it reached TTS as silence. `generate()` now returns an honest line instead.

---

### The audio issue — two attempts, both reverted

Worth recording so none of it gets retried:

1. **Peak-normalizing every chunk** to a fixed target. Made it *worse*. Kokoro's output level was never the problem, so this only amplified the already-audible part after the ramp — widening the gap it was meant to close. (Your diagnosis was correct.)
2. **Replacing the silent preroll with a quiet 80 Hz tone**, on the theory that a Bluetooth receiver's gain ramp only triggers on real signal energy. No improvement.

Both removed; `tts_kokoro.py` is back to its original behaviour with a comment recording both dead ends. The remaining quiet-then-step-up on Bluetooth is most likely the receiver's own hardware gain ramp — outside this process's control, and unreached by anything at the sample level. Matches your read that it's "like the model loading in."

---

## Part 2 — Open items

### 1. Target vs. current confusion — the one you named

After the fabrication fix, FRED listed **shoulder 49"** and **arm 14"** under "Biometrics" as though they were current measurements. `fitness.md` has those in the **Target** column; the current values are blank (`—`).

This is a **misread, not invention** — the numbers are real and from the file, just the wrong column. Much smaller than the original problem, but still a wrong statement about his own body, and the same class of error: presenting something as fact that the file doesn't actually assert.

**Why it happens:** the vault table is markdown with `Baseline | Current | Target` columns, flattened into a prompt as plain text. Column alignment is visually obvious to a human and near-invisible to a small model reading a linearised table, especially when the current cell is an em-dash placeholder.

**Plausible fixes, cheapest first:**
- **Extend the anti-fabrication instruction** to name this case directly: never state a measurement without its column label; if a value is under "Target" say "target", if the current cell is `—` say it isn't recorded. Cheap, no code, same lever that already worked once.
- **Pre-flatten tables during chunking** (`vault_md.split_sections`) into explicit `Measure: current=X, target=Y` lines, so column meaning survives linearisation instead of depending on the model tracking a pipe-table. More robust, more work, touches the shared chunker.
- **Fill the blanks in the vault** — a `—` current cell is genuinely ambiguous. Not a code fix, and your call, not mine.

**Recommendation:** try the instruction first. It's one paragraph, and the same approach already fixed the larger fabrication.

### 2. Not yet verified by you
The Round 2 fixes have been verified by me in isolation, but not in live voice use: fitness/health questions, "search my desktop for X", "show me my recent commits", and whether empty replies are actually gone.

### 3. Deliberately deferred
- **Shutdown ritual** (S3) — steps 1–4 are orchestration of existing pieces; step 5 (OS shutdown) keeps a confirm gate regardless of automation.
- **Live coding** (A) — dropped as conflicting with persona.md; revisit in years, not months, once local model code quality is trustworthy.

### 4. Nothing is committed
10 modified files, 4 new modules, +409/-22. Worth splitting into at least two commits (Round 1 features vs. Round 2 bug fixes) if you want a clean history.

---

## Method note

The pattern that mattered this session: **four of five Round 2 bugs were found by reading `Core/data/logs/sessions/*.jsonl`, not by reasoning about the code.** Every Round 1 hypothesis that I checked against a real log turned out to have a different root cause than the plausible guess — web search, clipboard, app launching, and volume all did. The logs record actual tool-call arguments and results, which is exactly the evidence that separates "this is probably why" from "this is why."
