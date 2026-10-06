# Review of FRED's own logs (read-only, 2026-10-06)

Source: `Old/Core/data/logs/sessions/` (64 daily files, 2026-07-31 to 2026-10-02, 31,484 records), `Old/Core/data/tool_call_log.jsonl` (835 tool calls in 743 turns) and `logs/crash.log`. Nothing in FRED was changed. No user text is quoted here. "Heuristic" marks numbers that come from my own pattern matching, not from a field in the log.

## Tool calling
| Finding | Evidence |
|---|---|
| **Failing calls are retried with identical arguments.** | 31 identical repeat calls in one turn. `lockdown_disengage` 5 times in a row, all errors (twice); `read_file` 4 times, all errors; `lockdown_engage` 3 times, all errors. |
| **`lockdown_disengage` crashes on a number.** | 12 of 32 calls: `'int' object has no attribute 'strip'`. The PIN/phrase argument arrives as an int; it needs `str()` at the entry. |
| **Several tools mostly fail.** | `launch_application` 19 of 25 (the app name keeps trailing words or punctuation, "X now.", or a whole sentence); `find_file_smart` 12 of 12 (searches descriptions, one path with an unexpanded `%username%`); `read_file` 16 of 30 (guessed paths); `lockdown_engage` 5 of 35 (`SetForegroundWindow` denied); `whats_on_screen` 9 of 49 (vision unavailable); `list_directory` 4 of 18. |
| **A full-disk search crashed FRED.** | `crash.log` shows `Fatal Python error: Aborted` inside `os.walk` from `launch_application` (resolving from search roots). |
| **Camera returns black frames and the model describes them.** | 4 `look_through_camera` calls: "the image is completely black"; nothing retried or reported as a camera fault. |
| **Multi-part requests get one tool.** | Heuristic: 57 turns with a connective and 7+ words; 41 answered with a single tool call, 16 with two or more. |
| **The router narrows the tool list, then chains need tools it did not offer.** | 11 of 54 multi-call turns used a later tool that was not in the first offered list. Routing reasons are cue-based ("cues schedule -> 6 tools"), plus "classifier said CHAT; re-offering last turn's tool" (33). |
| **Almost everything is one call.** | 689 of 743 turns have one call; 30 two, 12 three, 10 four, 2 five. |
| **Ambiguity prompts.** | 287 `ambiguous_choice` events (screen check 27, opening 23, reminder 21, lockdown disengage 18, recurring reminder 14). |
| **Interruptions.** | 284 of 3,739 spoken replies were interrupted (7.6%); 293 feedback rows, all marked interrupted. |

## Non-tool errors (8,582 `error` records, 74 distinct signatures)
- `proactive_presence`: CUDA "operation not permitted when stream is capturing", 4,302 times (2026-09-11 to 09-29; 214 errors on 09-28 alone). Face/presence inference collides with CUDA graph capture on another thread.
- `headphone_watch`: device missing (about 530), `'NoneType' ... 'bbox'` (112), COM not initialised (17), bool not JSON serializable (9).
- Cloud: no API key configured (894), Cerebras 402 payment (615) and 429 rate limit (about 340), Groq 413/429 (about 130). The cloud path was effectively dead for most of the period.
- Looks like unit-test output written into the real session log: `boom` (293), `cloud must not be called` (509), `camera exploded` (117). Tests should write elsewhere or these inflate every error count.
- Gmail DNS failures (210 on 2026-09-28), PortAudio stream errors (about 37), permission denied writing `proactive_state` (12, a write race), health check: Backup tier model files missing (21 times).
- 320 model loads (Standard 171 at 10.4 s, Reflect 61 at 22.5 s, Deep 54, Vision 34), about 66 minutes of loading in total.

## Fix list (proposed, nothing done; priority = harm x ease)
1. Coerce tool arguments to the declared types at the dispatcher (fixes the `int.strip` crash for free; check every other tool for the same).
2. In the tool loop: refuse an identical call that already failed this turn, stop after 2 failures in a row on one tool and tell the user why (the memory engine already has this guard).
3. Replace the unbounded `os.walk` in `launch_application` with a bounded search (known app index, depth and time limit), and normalise app names before lookup.
4. `find_file_smart` and `read_file`: expand environment variables, search by name, return "not found, did you mean ..." with nearby matches.
5. Offer tools by chain, not only by cue: when the request has two parts, offer both categories (or let the model ask for a tool group).
6. Camera: detect a black frame, retry once, then say the camera gave nothing.
7. Presence/CUDA: serialise GPU use between presence and the other models (or catch and back off); this is 50% of all logged errors.
8. Keep test output out of the real logs; log device-missing errors once per state change, not per poll.
9. Model loading: keep Standard resident or batch reflections, to cut the 66 minutes of loading.
