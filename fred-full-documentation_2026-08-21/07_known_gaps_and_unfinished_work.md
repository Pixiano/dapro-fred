# Known Gaps, Unfinished Work, and Open Issues

This file is an honest inventory of what is explicitly **not** built, **partially** built, **broken**, or **deferred** as of 2026-08-22 (originally written 2026-08-21, updated the next day for a real batch of new work — see the presence/sleep-mode section below, now largely resolved). Sources: `README.md`'s "Known limits" section, `MVP Plan (v1.0 - v1.1).txt` (the authoritative roadmap — read it directly for full reasoning, not duplicated here), `fred-presence-sleep-mode-plan_2026-08-18.md` (plan vs. built delta), `known_issues_2026-08-01.md` (a resolved-bug log kept for history, cross-checked against current code), and direct code inspection.

## Presence / sleep-mode: built vs. planned — updated 2026-08-22, largely resolved

The plan doc (`fred-presence-sleep-mode-plan_2026-08-18.md`) scoped four pieces: (1) presence detection, (2) presence-gated reminders, (3) a sleep-mode state machine with vault consolidation, (4) a cancel command. As of this update, **all four are built** — see `05_presence_and_sleep_mode.md` for full detail, including sleep-mode consolidation (§4.2) and a deep sleep-time reflection pass (§4.3) that goes beyond the original plan's scope entirely.

**Still NOT built** (confirmed absent by direct code inspection):
- Pausing the pill UI / wake-word listener while sleeping.
- Unprompted spoken task/agenda recap on wake (consolidation's recap covers day-summary + vault-gaps, not tasks/agenda).
- The "hold and re-check every 60-90 min, cap at 2 hours" reminder-gating behavior the plan proposed — sleep mode still just drops a gated nudge outright rather than queuing it.

**Match thresholds — HIGH has been tuned once, LOW has not.** `PRESENCE_MATCH_THRESHOLD_LOW = 0.30` is still an unmeasured starting guess. `PRESENCE_MATCH_THRESHOLD_HIGH` was raised 0.45 → 0.58 on 2026-08-21, a direct, justified response to a real false-positive report ("the notifications are appearing like every false match from the face recog"), paired with a new symmetrical `PRESENCE_PRESENT_DEBOUNCE` (2 consecutive present polls, mirroring the existing 3-poll absent debounce) so a single high-confidence-but-wrong frame can no longer flip presence, exit sleep mode, fire the wake greeting, or pollute the embedding pool. **Treat this as "tuned once against a real complaint," not "calibrated" — still not a systematic measurement against real enrolled-vs-live-frame similarity distributions.**

**Embedding storage is now a three-tier system** (base/hard/dynamic, replacing the old flat 50-embedding cap with no eviction) — see `05_presence_and_sleep_mode.md` §3.1. `PRESENCE_MAX_EMBEDDINGS` no longer exists as a setting.

**New known gap: "Save it" after a spoken summary offer mis-routes.** Confirmed via log investigation, not yet fixed. Saying "Save it" (or similar) right after FRED offers to save a session/day summary currently routes to `create_text_file` instead of the actual `save_today_summary` propose→save flow, producing an essentially empty daily note rather than the previewed summary content. Root cause not yet isolated to a specific line — flagged here as a reproduced, known bug for whoever picks it up next.

## Haismart AC / appliance control — known broken/unstable

Per the recent commit history (`Print list_devices_v2's raw response, not just "no appliances"`, `Validate haismart_setup.py's region prompt is a dialling code, not a country name`), this vendored LAN-protocol integration (`Core/tools/haismart_tools.py`, `Core/tools/haismart_setup.py`, `Core/tools/haismart/vendor/`) has had real, recent bugs around device discovery — a "no appliances found" failure mode that the most recent commit made more diagnosable (print the raw API response) rather than fixed outright. Treat this integration as **not confirmed reliable** — see `04b_tool_inventory.md` for what the current code actually does, and re-verify device discovery against a real account/region before depending on it.

## Cloud dependency is real and load-bearing, not cosmetic

As of the 2026-08-03 cloud cascade, most conversation and tool-calling depends on Cerebras being up. The local fallback is real, untouched code — not vaporware — but it is the *tertiary* path, reached only after cloud fails. `CEREBRAS_API_KEY` is currently hardcoded to `None` in settings.py because **the Cerebras account ran out of credits** (every call returning HTTP 402, 463 times in one day before caught) — meaning as shipped right now, FRED is running **local-only** despite all the cloud-cascade machinery being wired and working. Restoring `os.environ.get("CEREBRAS_API_KEY")` re-enables it once billing is resolved — a one-line revert, not a code change. A rebuild should not assume the cloud path is "the normal path in practice" without checking whether a key is actually live.

Related, and also currently forced off for GPU-load reasons rather than a real fix: `SCREEN_WATCHER_ENABLED = False` (screen watcher disabled 2026-08-19 "to rule it out as the source of a reported periodic GPU spike... the actual measured cadence in the logs didn't match the report... but disabling costs nothing and confirms either way" — i.e., this was a diagnostic measure, not a confirmed root-cause fix, and may need to be flipped back once the real spike source is found).

## Tier routing is disabled by design, not by accident

`TIER_ROUTING_ENABLED = False`. Three of the four local tier names (`Standard`, `Deep`, `Extreme`) currently resolve to the *same* Qwen3.5-4B checkpoint (see `02_llm_and_model_tiers.md`), a deliberate temporary simplification per Vatsal's direct instruction ("one tier only, the 4B for everything"), 2026-08-20. The real per-tier model paths are commented out immediately above, not deleted, for an easy revert. **A rebuild that wants real tier differentiation (a fast model for trivial turns, a bigger model for hard ones) needs to both restore those model paths and flip `TIER_ROUTING_ENABLED` on** — and per its own comment, the plan is not to silently re-enable this heuristic but to build a proper "offer the bigger model, ask before switching" UX first, so a tier/latency change is never a surprise mid-conversation.

## Memory system limitations (stated in README, verify against `memory_manager.py`)

- No delete/category system: every conversation turn is stored whole and unfiltered within a turn. FAISS `IndexFlatL2` (as used here) has no delete operation — removing a wrong memory requires a full index rebuild, not a targeted removal.
- Real category-based memory organization is scoped as Phase 19 in the MVP roadmap and, per README, not yet landed — verify current state against `Core/memory/memory_manager.py` directly (see `06_proactive_and_memory.md`) before assuming otherwise.

## Tool-selection accuracy is bounded by model size, not fully solved

The intent-routing/cue-word/semantic-rescue system (`04a_orchestrator_core.md`) is a deterministic shield for the *common* cases, not a guarantee. README states plainly: "Genuine action requests still depend on the model picking correctly within a routed subset, and that accuracy falls with model size. The router shields the common cases deterministically; it can't shield everything." This is a structural, acknowledged limitation of running ~80 tools against a small (4B-class) local model, not a bug to be "fixed" away entirely.

## Explicitly deferred / out of scope by direct decision (not bugs)

- **Live coding capability** — dropped 2026-08-01 per direct conflict with `persona.md`'s stated scope ("FRED is explicitly not a coding assistant"). Revisit only years out, per that decision.
- **End-of-session shutdown ritual** (auto-summarize → append to vault → close processes → unload models → confirm-gated OS shutdown) — proposed 2026-08-01, steps 1-4 judged automatable, step 5 (actual power-off) explicitly flagged as needing a confirm gate "no matter how automated the rest of the sequence is." Deferred, not built, as of this doc.
- **Double-clap gesture trigger** for presence-adjacent features — considered during presence-system planning, dropped outright (it's an audio signal, not vision, and wasn't worth the added scope).
- **Dynamic tier selection / "offer the bigger model" UX** — see Tier Routing section above.
- **Voice cloning (FTS fine-tune of Kokoro's voice)** and **account integrations (Spotify/YouTube/etc.) behind a credentials vault** — both explicitly v1.1-scope per `MVP Plan (v1.0 - v1.1).txt`, not started.
- **A LoRA fine-tune of the tool-calling model itself** on logged real usage — scoped in `Fine-Tune MVP Plan (2026-08-09).md` / `Fine-Tune Plan Draft (2026-08-12).md`, both planning-only, nothing trained. Explicitly noted in README as a *different* "fine-tune" project from the TTS voice-clone one — don't conflate the two if referenced elsewhere.
- **v2+ ideas** (self-improvement, a home-network "JARVIS mode" with allow-listed actuators, FRED placing/answering real phone calls) — scoped in depth on 2026-08-16 specifically to record blockers, explicitly not scheduled.

## Resolved historical bugs worth knowing about (do not reintroduce)

`known_issues_2026-08-01.md` documents six bugs and their real (log-confirmed) root causes, all fixed as of that session — useful as a list of specific failure modes a rebuild could silently reproduce if the same shortcuts are taken:
1. Deterministic dispatcher fast-paths (e.g. "search ___" phrasing) can silently steal a pronoun-led query away from the LLM's actual conversation history — fixed by declining the fast-path on pronoun-led queries.
2. A tool returning raw data (e.g. 50 absolute file paths) gets read aloud verbatim by a small model instead of summarized — fix is either rewrite the tool to return an already-spoken-safe sentence, or make sure it isn't wrongly exempted from the LLM's follow-up phrasing pass.
3. TTS loudness inconsistency between filler phrases and full replies — root cause was missing peak normalization in Kokoro synthesis, not a preroll/stream timing issue (three earlier guesses on that front were all wrong).
4. Literal single-pass file search vs. what "find my X" actually needs — solved with a two-tier design (a persistent found-cache for repeats, a genuine multi-step agentic search on the Deep tier for the rest) — see `found_cache.py`/`smart_search.py` in `04b_tool_inventory.md`.
5. Overly broad single-word cue matching (bare `"copy"`) in the category router firing on unrelated phrases containing that substring — fix was narrowing the cue list, a reminder that `CATEGORY_CUES` entries need to be specific enough not to false-positive on ordinary words.
6. App-launch resolution failing on apps outside a hardcoded path table (or with STT-added trailing punctuation mangling the exe name) — fixed by punctuation-stripping plus layering in the Windows "App Paths" registry key and Start Menu shortcut search rather than relying on one hardcoded table.

## Things a rebuild should explicitly re-verify, not assume

- Every numeric constant in `Core/config/settings.py` is dated and reasoned, but many are marked "temporary," "unverified," or "starting guess" — do not treat any of them as settled without re-reading that file's live comment for the constant in question.
- README.md and SETUP.md drift from the code (see `01_environment_and_setup.md`'s "Known drift" note) — always prefer `Core/config/settings.py` and the actual source over prose docs for exact values.
- The presence/sleep-mode subsystem (`05_presence_and_sleep_mode.md`) is still the freshest, most actively-changing part of the codebase — it shipped 2026-08-21 and had a real false-positive fix, a three-tier embedding redesign, consolidation, and a deep reflection job all land within the following day. Treat everything in it as more likely to change or be found wrong on first real-world use than any other subsystem, and re-read the source before trusting any specific number in this doc set over it.
- The `"Reflect"` model tier (gpt-oss-20b, `02_llm_and_model_tiers.md`) has an unverified VRAM footprint on this machine — flagged directly in `settings.py`'s own comment (a reported 26-35GB figure that doesn't fit this card, not re-measured live for lack of free VRAM at the time). Don't trust either number until someone runs `LLMClient().ensure_loaded("Reflect")` with everything else closed and reads `nvidia-smi`.
