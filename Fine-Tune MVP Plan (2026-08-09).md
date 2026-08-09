# F.R.E.D. — Fine-Tune MVP Plan (2026-08-09)

Sequel to `MVP Plan (v1.0 - v1.1).txt`. That document shipped its v1.0 cut of
Phases 16-20 by June 28; this one decides what ships *next*, with a specific
end goal: reach a state stable enough to fine-tune a model on, without every
subsequent feature addition causing regression. Written from a full read of
every prior roadmap doc, `known_issues_2026-08-01.md`, the vault's
`active-priorities.md` / `projects/fred.md`, and a live check of the current
codebase against all of it.

**Status: text/discussion only. Nothing in this document has been built.**

---

## 1. Ground truth vs. the vault's own record

The vault's `projects/fred.md` (last updated 2026-08-01) is stale on
specifics, confirmed by checking the running code directly:

| Claim in `fred.md` | Actual, checked 2026-08-09 |
|---|---|
| Default model: Qwen3.5-4B / Nemotron | `DEFAULT_TIER = "Standard"` → Qwen3-8B-Q4_K_M |
| ~40 tools | 68 registered tools |
| All 4 memory-layer bugs open (split-brain path, FAISS/JSONL desync, unnormalized L2, raw per-turn embedding) | **3 of 4 fixed** (absolute paths, desync, normalized IP). **1 still open**: raw per-turn embedding — every turn, including "hello" and tool-error strings, still gets embedded as long-term memory. No end-of-session distillation exists yet. |

Action item, independent of everything else below: update `fred.md` and
`active-priorities.md` to match reality. Small, but it's exactly the kind of
drift that compounds — and it's a live demonstration of why v1.1 backlog
item #13 (self-documentation access) is a real need, not a nice-to-have.

---

## 2. What's actually left from v1.0

Per `known_issues_2026-08-01.md`, six bugs and three feature suggestions
from that session are marked resolved. Checked against the original v1.0
phase list (`MVP Plan.txt`), still open or unverified:

- ~~**Phase 17 (Vision)**~~ — **checked live 2026-08-09, was broken, now
  fixed.** Two real bugs found and fixed:
  1. **False confidence on stale data.** `whats_on_screen()` correctly
     computes staleness (`is_fresh()`), but its own hedge sentence
     ("...which is probably stale: ...") wasn't in
     `SELF_NARRATING_TOOLS`, so the LLM's rephrase pass was dropping
     the hedge and presenting a 6-hour-old cached description (a car
     wallpaper from earlier testing) as a confident current answer —
     exactly the symptom Vatsal hit live. Fixed: added
     `whats_on_screen` to `SELF_NARRATING_TOOLS`
     (`orchestrator.py`), so the raw hedge now reaches the user
     verbatim.
  2. **Prompt self-censored on request.** `screen_watcher.py`'s
     describe prompt explicitly forbade verbatim text transcription —
     "what's this error" could never work by construction, since the
     actual error text was never allowed through. Removed that
     restriction on Vatsal's explicit instruction ("don't gatekeep
     anything, allow it formatting, text formatting") — prompt now
     asks for exact quotes of error/code/text, `describe_image`'s
     `max_tokens` bumped 200→500 so a real quote doesn't get cut.
  - **Root cause still open, not fixed, flagged not silently patched:**
    the cache is stale by *design* under normal use, not just bad
    luck. The watcher needs 5 min of no-hotkey idle to even attempt a
    capture, then skips the cycle entirely if the main conversation
    model is still resident — and that model's own idle-unload is a
    full hour (`LLM_IDLE_UNLOAD_SECONDS`). Anyone talking to FRED at
    least once an hour gives the watcher almost no window to ever
    capture. This is a real tradeoff (the VRAM math is real — Standard
    + Vision don't both fit on a 16GB card), not a bug to patch
    reflexively; needs its own decision (shrink the unload window?
    force-unload on watcher demand? accept it and lean on the
    now-honest staleness hedge?), not decided here.
- **Phase 16 (HUD live transcript)** — **checked 2026-08-09: does not
  exist, not a bug to fix.** `hud/server.py`'s `/state` feed has a
  `diagnostics` field, rendered by `index.html`'s `paintLog()` as the
  HUD's "log" panel — but that's tool calls / errors / system notes
  only (`_DIAG` dict), not speech. `server.py`'s own comment on why:
  "the log also carries bulky things (full transcripts, health
  checks) that would just be noise here." No `transcript` /
  `user_speech` / `fred_speech` text reaches the HUD anywhere. This
  was never built, not broken — a real feature decision needed (worth
  it? what would it even show — full text is a lot of HUD real
  estate), not a verify-and-fix like Vision was.
- **Phase 20 (wake-word / conversation window)** — never built as
  originally scoped. Instead, `pill_app.py` deliberately replaced
  wake-word entirely with hold-to-talk (LEFT Ctrl+Alt). Documented as a
  considered decision ("a better fit than a wake word on every axis that
  mattered"), not an oversight.
  **Reopened 2026-08-09**: given how central proactivity now is, this
  deserves its own real conversation — not decided in this document.
- **Phase 20 (grammar-constrained STT)** — moot as originally written. The
  plan was scoped against Vosk; the project has since moved to
  `faster-whisper large-v3-turbo`, a materially stronger model with a
  different failure profile. Needs re-scoping against current STT before
  it's actionable, not picked up as-is.
- **Phase 20 (dynamic tier selection)** — `_pick_tier` is still
  keyword/regex heuristics, unchanged since Phase 12.

---

## 3. What tonight (2026-08-09) added to the picture

Not in any prior plan, because none of this existed before tonight's
session:

- **The agenda system** — homework/projects/events, proactive carryover
  and prep nudges. Already in real daily use (logged during this same
  session).
- **A structural bug class, not just one bug**: proactive speech
  (`notify()`) never entered FRED's own conversation history, so a reply
  to his own unprompted question free-associated from stale context.
  Fixed generically (`notifier.set_recorder`), but this is the shape of
  bug that likely has undiscovered siblings — anywhere FRED speaks
  outside the normal turn loop is a candidate.
- **The tool-call outcome log has no reader yet.** `tool_call_log.py`
  writes a row per tool call (with a documented weak-labeling scheme:
  clean / errored / interrupted) but nothing consumes it. Requested
  tonight, explicitly parked — see open items below.
- **Router fixes were all reactive** — carry-forward, compound-turn
  detection, exact-readback, all discovered by reading real failed
  sessions, none anticipated in advance. No reason to expect today's
  usage has stopped producing new instances of this pattern.

---

## 4. New tools proposed

**Low-risk, from the existing v1.1 backlog** (flagged there as
architecture-free additions):
- ~~**Calculator tool** (backlog #10)~~ — **already built.** Verified
  2026-08-09: `assist_tools.calculate()`, registered in the tool loop
  (`orchestrator.py:1213`), plus a dispatcher fast-path for bare
  arithmetic. Live-checked word-form math, percentages, order of
  operations, and a code-injection string (rejected cleanly, ast
  whitelist not eval). Backlog was stale, not the code.
- ~~**System info tool** (backlog #11)~~ — **already built.** Verified
  2026-08-09: `assist_tools.get_system_status()` /
  `get_network_status()`, both registered (`orchestrator.py:1234`).
  Live-checked: battery/CPU/RAM/disk/uptime and online+SSID+signal+IP
  all correct.
- **ffmpeg / print / hard-drive index** (backlog #7) — checked
  2026-08-09: none of it exists (`grep` across `Core/` for ffmpeg,
  printing, or a maintained drive index came up empty; `search_files`
  in `machine_tools.py` is a live walk, not an index). Still genuinely
  open — three tools, same shape as `machine_tools.py`:
  - `ffmpeg` access / file conversion ("convert this to mp4")
  - a print command ("print this PDF")
  - a maintained hard-drive file index (vs. today's live-walk search)

**New, from tonight's own experience:**
- **Tool-call outcome reader** (the parked eval-set work) — beyond
  feeding a fine-tune, this alone answers "which tools misfire most in
  real usage" without manually reading session logs turn by turn.
- **Self-documentation access** (backlog #13) — concretely motivated now
  by `fred.md` itself being caught stale about its own model tier and
  tool count.

---

## 5. Things learned working with Vatsal tonight, as design constraints

1. **Real bugs get found by real use, not by description.** Every fix
   tonight traced back to a live session log or a specific complaint
   ("why is it named school," "FRED doesn't remember"), never a
   hypothetical. The tool-call reader is high-leverage precisely because
   it automates what's currently done by hand.
2. **Naming/scope drift gets caught fast, and should be caught earlier.**
   The school→agenda rename happened because "event" was scoped
   generally from the start but named narrowly. Worth asking "is this
   named for the general case or just the first example" *before*
   building, not after.
3. **Proactivity is the most-wanted feature and the hardest to test.**
   It can't be triggered on demand — only observed live, whenever a
   check happens to fire. That's a real tension for a fine-tuning
   target: the highest-value behavior is also the one with the thinnest
   eval coverage.

---

## 6. Decided MVP scope (2026-08-09)

Answered directly by Vatsal:

- **Freeze first, fine-tune once.** Stop adding tool surface once this
  MVP's list is hit; build the eval set against a fixed target; train
  once against a stable baseline rather than accepting recurring
  retraining as new tools land.
- **Memory-layer distillation (the 4th open bug) is explicitly OUT of
  this MVP** — its own separate effort afterward, given the open design
  questions already logged in `active-priorities.md` (distiller engine
  choice, what to do with ~500 existing raw records).
- **Vision (Phase 17) is IN** — verify it actually works end to end,
  fix if broken, don't just assume the roadmap's "done" claim.
- **Wake-word vs. hold-to-talk is REOPENED**, not decided here — flagged
  as its own real conversation, separate from this MVP, given how much
  weight proactivity is now carrying.

---

## 7. The pill textbox — decided feature spec

Discovered while scoping: `plan_29_6_26.txt` (June 29) already designed
almost exactly this feature, but as part of a full UI rewrite (Tkinter
pill → pywebview glass orb) that was never built. That plan's textbox
design (1-line compact / 4-line fullscreen, arrow-key history, submits to
orchestrator) is the reference point, not a fresh design.

**Decided scope**, answered directly by Vatsal:

- **Bolt onto the CURRENT native pill** (`Core/ui/pill/window.py`) —
  explicitly NOT the full pywebview rewrite. Smallest change: a text
  field added to the existing pill, everything else about it stays as-is.
- **Use cases** (all three, not mutually exclusive):
  - Voice isn't appropriate (late night, people around) — must be
    quiet-usable, no mic activation.
  - Typing/pasting something long or precise (a URL, exact phrasing) —
    needs to handle more than a one-liner well.
  - Fast correction path when voice mishears — needs to be reachable
    mid-conversation, not a separate mode to switch into.
- **Reply display: show a small reply line too** — not input-only. At
  least the last exchange visible as text near the input, since typing
  specifically implies audio may be unwanted.

**Not yet decided / needs follow-up before this is buildable:**
- Exact trigger to open/show the input (always visible vs. summoned by
  hotkey/click).
- Whether it reuses the exact same `process()`/`process_stream()` path
  voice turns use (almost certainly yes, but not confirmed against how
  the HUD's own broken `#cmd` box was wired, to avoid repeating whatever
  made that one fail in Chrome kiosk specifically — different context,
  worth a quick check before implementation, not before this document).

---

## 8. Open items, explicitly parked

- **Tool-call outcome log reader / eval-set system** — requested, parked
  by Vatsal to return to later this same session or a future one.
- **Wake-word vs. hold-to-talk** — reopened, needs its own conversation.
- **Memory-layer distillation** — confirmed out of this MVP, own effort
  after.
- **Vault housekeeping** — `fred.md` / `active-priorities.md` staleness,
  small fix, not yet scheduled.
