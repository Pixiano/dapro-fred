# FRED — Known Issues & Roadmap (2026-08-01)

Captured from a single feedback session after switching `Standard` to Qwen3-8B. Six concrete bugs, two structural gaps, three feature proposals.

**Status as of this session: every item resolved** — six bugs fixed and verified against real session-log reproductions (not guesses), Bug #4's cheap and real tiers both built, Suggestion #1 (Git, read-only) and Observation B (proactivity, all three checks) built, Suggestion #3 (shutdown ritual) deferred by choice, Observation A (live coding) dropped — it directly conflicted with persona.md's own "not a coding assistant" scope. Root causes marked "hypothesis" below are the original triage guesses; several turned out to have a different, log-confirmed root cause once actually investigated (noted inline).

---

## Bugs

### 1. Web search loses context across turns — RESOLVED

**Actual root cause (log-confirmed, different from the hypothesis below):** not an LLM context problem at all — `Dispatcher._route_web_search` in `Core/orchestrator/dispatcher.py` intercepted "search ___" phrasing deterministically, before the LLM (and its conversation history) ever ran. "Search it on the web. It has been released." dispatched with `query="it on the web..."` verbatim, reproduced directly in `session_2026-08-01_14-24-11.jsonl`.

**Fix:** `_route_web_search` now declines (returns `None`) when the captured query is pronoun-led ("it", "that", "this", etc.), and `Dispatcher.match()` falls through to the next rule instead of stopping — which for a pronoun-led search means falling all the way through to the LLM tool path, which has the last 10 messages in context. Self-contained searches ("search for the weather in Paris") still dispatch instantly.

<details><summary>Original hypothesis (superseded)</summary>
**Symptom:** Discuss something, then say "just web search" on the next turn — it searches the wrong thing.

**Hypothesis:** The tool-call step only ever passes the current utterance as the `query` argument to `web_search()`. Nothing carries "what we were just discussing" into that argument's construction — `Core/tools/web_tools.py` itself is a thin, working DuckDuckGo wrapper; the bug is upstream of it, in how the query string gets built.

**Plausible fix:** When the model is offered `web_search`, include the last 1-2 turns of conversation directly in the tool-calling context (already partially true via `recent_messages`, so worth checking whether the issue is really "no context" or "the model doesn't reliably use the context it has"). A cheaper fix: post-process — if the query argument is very short/pronoun-heavy ("that", "it", "this"), splice in the previous turn's topic before calling the tool.

**Effort:** Small, once root cause is confirmed. Needs reproduction first (capture the actual tool-call arguments FRED generates across two real turns).
</details>

---

### 2. Reads raw file paths / tool output aloud verbatim — RESOLVED
**Actual root cause:** not `SELF_NARRATING_TOOLS` (search_files/read_file/list_directory were never in that set) — `search_files` in `Core/tools/machine_tools.py` returned up to 50 raw absolute paths newline-joined, which a small model tends to parrot rather than summarize even with the follow-up phrasing pass. **Fix:** `search_files` now returns a speech-safe summary (count + filename + parent folder name, no full paths), matching the existing convention `move_file`/`rename_file` already used.

<details><summary>Original text</summary>
**Symptom:** Some tool results get spoken as raw structured text ("dash, filename, open paren, C colon backslash...") instead of a natural sentence.

**Hypothesis:** Same mechanism as the `calculate` bug fixed earlier this session — `SELF_NARRATING_TOOLS` in `Core/orchestrator/orchestrator.py` skips the follow-up LLM phrasing pass for any tool in that set, on the assumption its raw output is "already a complete spoken sentence." Whichever tool is causing this (likely `list_processes`, `search_files`, `read_file`, or similar) is probably in that set incorrectly, or its own return-string formatting was never actually written to be spoken.

**Plausible fix:** Audit `SELF_NARRATING_TOOLS` against every tool actually in it — for each, listen to what it currently returns and decide: (a) rewrite the tool to return a real spoken sentence (like `calculate` was rewritten to do), or (b) remove it from the set so the follow-up LLM pass phrases it naturally, same fix already applied once for `calculate`.

**Effort:** Small-medium. Mechanical once the offending tool(s) are identified — needs a real turn's tool-call log to name them precisely.
</details>

---

### 3. Voice still low sometimes — RESOLVED
**Actual root cause:** not a mis-tuned preroll/stream setting (three prior guesses on that front were all correct to abandon) — Kokoro simply doesn't normalize loudness across utterances, so short filler phrases synthesize at a genuinely lower peak amplitude than full reply sentences. No gain control existed anywhere in the pipeline. **Fix:** every chunk (cached or freshly synthesized) is now peak-normalized to a consistent target in `Core/audio/tts_kokoro.py`'s `emit()`, regardless of source — filler and reply are equally loud from the first sample instead of "ramping up."

---

### 4. File search is one deterministic pass, not semantic/agentic — RESOLVED (both tiers)
**Cheap tier:** built — `Core/tools/found_cache.py`, a persistent (directory, query) → resolved-paths cache, checked before `search_files` re-walks a directory. Positive results only (a miss has no invalidation trigger, so caching "not found" risked permanent false negatives); a hit is re-verified with `Path.exists()` on every read so a moved/deleted file doesn't return stale.

**Real tier:** built — `Core/tools/smart_search.py`'s `find_file_smart`, a genuine multi-step agentic search: reasons through the folder tree on the `Deep` tier (list contents → ENTER a subfolder or declare FOUND → repeat, capped at 6 steps), for when the filename itself isn't known ("find my health logs"). Registered as its own tool alongside `search_files`, and shares the same found-cache.

<details><summary>Original text</summary>
**Symptom:** Asking FRED to find a file (e.g. "find my health logs") does a single literal filesystem match rather than reasoning across directory contents the way an agent would (list dirs, semantically match candidates, narrow down).

**Root cause (structural, not a bug):** `search_files` almost certainly does one `os.walk`/glob pass with substring matching. Real agentic search — list, reason, narrow, possibly recurse — is a genuinely bigger capability than what's built, not a small fix.

**Plausible fix, two tiers:**
- **Cheap, immediate value:** pair with Suggestion #2 below (a persistent "already found this" index) — most real searches are repeats ("where's my health log" gets asked more than once), so caching resolved paths removes the need for smart search on the common case.
- **Real fix, bigger effort:** a multi-step tool where the model can call `list_directory` repeatedly and reason over results before committing to a file, rather than one shot. Needs a capable-enough model to reason well across multiple tool calls — likely wants `Deep`, not `Standard`, given the reasoning load.

**Effort:** Cheap tier: small. Real tier: medium-large, new tool-calling pattern.
</details>

---

### 5. Clipboard tool fires instead of web search / file read — RESOLVED
**Confirmed root cause (log evidence, not just a match to the "on track" pattern):** three real transcript hits in `session_2026-08-01_14-24-11.jsonl`, all triggered by "project copy" — a project name — matching the bare cue word `"copy"` in `CATEGORY_CUES["clipboard"]`. None of the three phrases had any other category cue, so clipboard was the *only* category offered. **Fix:** narrowed the clipboard cues in `Core/orchestrator/intent.py` to `"clipboard"`, `"paste"`, `"pasted"`, and copy/paste paired with a demonstrative ("copy that", "copy this") — dropped bare `"copy"`/`"copied"`. Verified against all three real trigger phrases plus genuine clipboard requests.

---

### 6. App launching slow/unreliable — RESOLVED
**Confirmed root cause (log evidence):** every logged Spotify failure had a trailing period from STT transcription — "Spotify." became exe name "Spotify..exe" (nothing already ending in ".exe" gets one appended, and "Spotify." didn't match the alias table). **Fix:** `launch_application` now strips trailing punctuation/filler words ("now", "please") before resolution. Also added `%APPDATA%` (Roaming) to `_SEARCH_ROOTS` — confirmed as Spotify's actual install location, missing before — and a Start Menu `.lnk` shortcut search as a resolution layer for anything installed somewhere neither PATH nor the App Paths registry knows about.

<details><summary>Original text</summary>
**Symptom:** LM Studio worked on the third try; Spotify doesn't work at all as of testing.

**Hypothesis:** `launch_application` most likely uses a hardcoded name→path table rather than resolving through Windows' own app-discovery mechanisms. This would explain both symptoms at once: works reliably only for apps whose install path happens to be in the table and matches exactly; fails or needs retries for anything installed somewhere non-standard — which is exactly how Spotify and many modern apps install (per-user AppData path, or UWP via `shell:AppsFolder`, not `Program Files`).

**Plausible fix:** Layer the lookup instead of hardcoding one path per app:
1. Try the Windows "App Paths" registry key (`HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths`) — this is how `start spotify` often resolves from a terminal.
2. Try Start Menu shortcut search (`.lnk` files under both per-user and all-users Start Menu folders) — resolves by matching the shown name, not a fixed path.
3. Fall back to a `shell:AppsFolder` lookup for UWP-packaged apps (Spotify's Store version, if applicable).
4. Only fall back to a hardcoded table as the last resort for known-tricky cases.

**Effort:** Medium. Real Windows API work (registry read, shortcut resolution), but each layer is independent and can be added incrementally.
</details>

---

## Structural gaps (not bugs — capability FRED doesn't have yet)

### A. No live coding capability — DROPPED (for now)
Decided with Vatsal: this directly conflicts with persona.md's own stated scope — *"Not a coding assistant... Claude Code covers that ground; FRED covers the rest."* Not built. Revisit long-term, years out, once local model code-editing quality is actually trustworthy — not a near-term item.

### B. No real proactivity — RESOLVED (three checks built)
Built in `Core/orchestrator/proactive_checks.py`, wired into `ReminderScheduler` at orchestrator startup (`register()` called from `orchestrator.py.__init__`), each firing through the existing `notifier.py` plumbing at most once per stretch:
1. **Vault staleness** — `active-priorities.md`'s own `updated:` frontmatter date, flagged past `PROACTIVE_STALE_DAYS` (7).
2. **Long session** — real Windows idle-time (`GetLastInputInfo`), flagged after `PROACTIVE_LONG_SESSION_HOURS` (3) of continuous use with no break of at least `PROACTIVE_BREAK_IDLE_MINUTES` (15).
3. **Deadline proximity** — an optional `deadline: YYYY-MM-DD` frontmatter field on any vault file, flagged within `PROACTIVE_DEADLINE_WARN_DAYS` (7). No vault file uses this field yet — this is the read path for whenever a real deadline gets added, not speculative date-parsing of prose.

All three tested against synthetic and real vault data; dedup logic verified to fire once and not repeat.

---

## Feature suggestions

### 1. Git integration — RESOLVED (read-only, as scoped)
Built in `Core/tools/git_tools.py`: `git_status`, `git_log`, `git_diff_summary` — all read-only (status/log/diff-stat), nothing that mutates repo state. Fixed argument-list subprocess calls only, never shell=True. Tested against this actual repo. Write access (commit/push) deliberately not built — different risk category, not requested.

### 2. A persistent "already found this" index — RESOLVED
Built in `Core/tools/found_cache.py`, wired into `search_files`. See Bug #4 above.

### 3. End-of-session shutdown ritual — DEFERRED (not this pass)
Proposed sequence:
1. Summarize everything done/learned this session.
2. Append that summary to the vault (with permission if needed — matches the existing propose-before-write vault convention).
3. Close running processes — ask per-process, except a pre-approved auto-close list (e.g. Spotify).
4. Unload all models, shut FRED itself down.
5. Optionally shut down the PC — or a countdown fallback if a direct shutdown isn't clean.

**Steps 1-4 are reasonable to automate** as far as the user wants.

**Step 5 is flagged, not rejected:** actually powering off the machine is a hard-to-reverse, whole-system action. Per how this assistant is meant to operate, that specific step should always require explicit confirmation in the moment — never a fire-and-forget timer with no final check, regardless of how automated the rest of the sequence is. Everything up to "shut FRED down" can be as hands-off as desired; the OS shutdown itself keeps a confirm gate no matter what.

**Effort:** Medium overall — mostly orchestration of existing pieces (summarization is just an LLM call, vault write already has a pattern, process listing/closing tools already exist) plus the new confirm-gated shutdown step.

---

## Suggested order, if picking where to start

Cheapest to actually diagnose first, since each needs a specific reproduction rather than more guessing:
1. Bug #5 (clipboard misroute) and Bug #2 (raw path reading) — mechanical once the exact triggering phrase/tool is named.
2. Bug #3 (volume) — needs specifics, not another guess.
3. Bug #1 (web search context) and Bug #6 (app launching) — real fixes, but scoped and independent.
4. Bug #4 (semantic file search) and Suggestion #2 (found-things index) — worth doing together, since the cache softens the bigger problem immediately while real agentic search (if ever built) is a bigger, separate effort.
5. Structural gaps (A, B) and Suggestion #3 (shutdown ritual) — each is a real design conversation before any code, not a quick fix.
