# F.R.E.D. — Fine-Tune Plan Draft (2026-08-12)

Continues `Fine-Tune MVP Plan (2026-08-09).md`, specifically its section 6
decision ("freeze first, fine-tune once") and section 8's parked item (the
tool-call-log reader). Written from that document, `known_issues_2026-08-01.md`,
`session_2026-08-01_changes.md`, `session_2026-08-12_wakeword_changes.md`,
`Core/orchestrator/tool_call_log.py`, `Core/config/settings.py`, and a live
count against `Core/orchestrator/orchestrator.py`.

**Status: rough discussion draft. Pure planning, nothing built, several
sections explicitly undecided. Not a committed roadmap.**

---

## 1. Preconditions / blockers

| Item | Status | Blocking? |
|---|---|---|
| Tool-calling robustness fixes (ambiguous/unexecuted calls, missed vault lookups) | In progress, separate workstream | **Yes, hard blocker.** These fixes change what "clean" vs. "error" means in the tool-call log. Collecting eval data before they land bakes today's known bugs in as if they were the target behavior. |
| `Core/scripts/tool_call_report.py` (error-row dump reader) | In progress, separate workstream, not yet built | **Yes, hard blocker.** `tool_call_log.py` only writes rows today (confirmed — its own docstring: "nothing reads this file yet"). No reader means no eval set, full stop. |
| Wake-word stability (`session_2026-08-12_wakeword_changes.md`, "What's still open") | Live experiment, unvalidated 20-clip real-positive model, known-good revert path exists | **Not a blocker on the fine-tune pipeline itself — a parallel-safe but noisy neighbor.** Reasoning below. |

**Why wake-word isn't a hard blocker:** it's a separate small audio model
(openWakeWord) gating when a turn starts, not part of the LLM or the
tool-calling path being fine-tuned. Building the eval-set pipeline, the
LoRA spike, and the training scaffolding doesn't need it settled.

**Why it still matters for timing:** if the wake-word model is still
throwing false triggers while tool-call-log data is being collected for
the eval set, that pollutes the data — spurious turns with no real
utterance behind them, or turns cut short by a false wake. Recommendation:
don't gate the fine-tune *work* on wake-word settling, but do gate what
data counts as trustworthy training/eval material on it — either tag rows
by session/date so a bad wake-word window can be excluded after the fact,
or simply prefer data collected after Part 6's 20-clip model (or whatever
current state) is confirmed stable over a longer window than the ~36
minutes logged so far.

---

## 2. Data / eval-set strategy

**Source 1: `Core/data/tool_call_log.jsonl`.** Confirmed live as of
2026-08-12: 612 rows already accumulated, growing organically since
nothing gates it. Each row (from the module itself) carries `turn_id`,
`utterance`, `path` (`dispatcher` or `tool_loop`), `tools_offered`,
`routing_reason`, `tool_called`, `arguments`, `result_preview`, and
`result_error` (regex substring match over the actual error vocabulary in
`Core/tools/*.py`, not a clean signal — the module's own comment calls
these "weak-labeling"). A separate `log_turn_feedback()` call writes
`interrupted`/`note` rows joined by `turn_id`, arriving later from the UI
layer.

That maps directly to a weak 3-way label already described in the
module's docstring: clean run = positive, `result_error` = negative,
interrupted = weak negative. The forthcoming `tool_call_report.py` is
assumed (per task brief) to turn the error rows into a reviewable dump —
plan assumes that exists once the other workstream lands it, not verified
here.

**Source 2: raw session logs (`Core/data/logs/sessions/*.jsonl`).** 13
files exist today. Richer context (full turns, including `notify()`
proactive turns per the 2026-08-09 session), but NOT directly usable as
training targets without a cleaning pass — `session_2026-08-01_changes.md`
is a direct demonstration of why: real logged assistant turns from that
session included fabricated health data and a target/current column
misread. Using raw transcript turns as positive fine-tune examples without
a human filter would train the model to repeat exactly the failure modes
that session spent fixing. `tool_call_log.jsonl` rows are closer to
pre-labeled (weakly) and narrower in scope (one tool call, not a full
reply); raw session jsonl is closer to "needs a person to read it first."

**What "enough data" might look like — genuinely not decided.** 612 rows
exist today, but an unknown fraction predate the robustness fixes and
should probably be discarded or re-reviewed rather than trusted as-is (see
freeze point below). No sizing target is set here — LoRA-scale fine-tunes
for a narrow task can work with anywhere from a few hundred to a few
thousand clean examples depending on how much the base model already gets
right, but that range isn't a commitment, just a rough anchor to revisit
once the reader exists and the real post-fix row count is known.

---

## 3. What gets fine-tuned and how

**Base model:** `DEFAULT_TIER = "Standard"` in `Core/config/settings.py`
→ Qwen3-8B, Q4_K_M, thinking-on. This is the only tier `TIER_ROUTING_ENABLED
= False` ever actually routes to, and the tier the ~68 tool definitions
are tuned against (`settings.py`'s own comment: "these ~30 tool
definitions were tuned against the 9B" — stale on the exact count but
correct on which tier).

**Approach: LoRA/QLoRA, not a full fine-tune.** Reasoning by the same VRAM
logic `settings.py` already uses elsewhere (the Standard+Vision
can't-both-fit comment, the documented history of hard crashes on this
16310 MiB card): Standard alone already runs ~9.9GB resident for
inference. A full fine-tune of an 8B model needs optimizer state and
gradients on top of the weights — not close to fitting alongside anything
else on this card, and this machine is the only training environment (the
project's own "fully local, always" value, not just a cost choice). LoRA
freezes the base and trains a small adapter, which is the only approach
that's plausible here without new hardware. Not yet computed: the actual
VRAM footprint of a LoRA/QLoRA run at a real batch size and sequence
length on this specific card — that's a spike, not assumed to just work.

**A real pipeline unknown, not yet reasoned through elsewhere:** the
runtime model is a llama.cpp GGUF (`Qwen3-8B-Q4_K_M.gguf`). LoRA training
tooling (Unsloth, PEFT, etc.) typically wants the original safetensors
weights (fp16/bf16, or its own 4-bit bnb quantization), not a GGUF
directly — training happens against a different weight format than the
one FRED actually loads, and merging the trained adapter back into a
runtime GGUF is an extra conversion step, not automatic. Needs its own
small end-to-end spike (toy adapter, merge, reload in llama.cpp, confirm
it still loads and generates) before trusting the pipeline on a real run.

**Target — tool-call accuracy first, tone secondary/undecided.** The
2026-08-09 plan already decided to build an eval set "against a fixed
target," which given the data source (`tool_call_log.jsonl`) is naturally
tool-call accuracy: does the right utterance pick the right tool with the
right arguments. That's also the only thing currently being logged in a
form suitable for eval. Conversational tone/persona is a much softer,
harder-to-measure target, and there's a real question of whether it needs
fine-tuning at all — persona/profile/rules are already injected into the
prompt every turn from the vault, which is a cheaper lever than training
weights for something that may already be working through context. Not
ruled out, but not assumed in scope either — see open questions.

---

## 4. The freeze point

**Concretely, as of 2026-08-12:** `self.tools.register(` appears 68 times
in `_register_tools()` in `Core/orchestrator/orchestrator.py` — unchanged
from the 68 counted in the 2026-08-09 plan, so no tool-surface drift in
the interim.

**Today's in-flight additions (a separate workstream, per task brief: 3
new tools + a self-documentation tool) have NOT landed yet** — the count
is still 68, not 71+. Call: once they land, they should count as **inside**
the frozen baseline, not as post-freeze exceptions. These are already
decided, already-scoped additions (the self-doc tool is backlog #13,
explicitly motivated by `fred.md` itself going stale — see the
2026-08-09 plan's section 4), not new scope creep. Freezing right before a
known, already-committed addition lands just means immediately breaking
the freeze to add it back in, which defeats the point of freezing at all.

**Practical sequencing implication:** the actual freeze declaration should
happen *after* that workstream lands, not today. Anything proposed after
that point (the parked ffmpeg/print/drive-index tools from the
2026-08-09 plan's section 4, or anything new) waits for the next
fine-tune cycle, per the "train once" decision already made in that plan.

**Not part of the tool surface, so not part of this freeze question at
all:** wake-word (an audio trigger model, not an LLM tool), and the
tool-calling robustness fixes (behavior/prompting fixes to how existing
tools get selected and called — no new tool count, but see section 1,
they still gate when data collection can be trusted).

---

## 5. Rough phases/sequencing (draft ordering, open to revision)

1. Land the two hard blockers: tool-calling robustness fixes, and
   `tool_call_report.py`.
2. Let the in-flight tools + self-doc tool workstream land. Declare the
   freeze at that point, explicitly (a real decision/commit, not implied).
3. Start eval-set data collection treated as trustworthy from the freeze
   point forward. Rows collected before robustness fixes landed are
   suspect — discard or re-review, don't blindly fold them in.
4. Build the eval set: pull error rows via `tool_call_report.py`, triage
   each by hand (real bug vs. genuinely ambiguous vs. one-off STT/mishear
   noise — same categories `known_issues_2026-08-01.md` already surfaced
   by hand before any reader existed), pull a matching sample of clean
   rows, settle on a real target size once the post-freeze row count is
   known.
5. Spike the training mechanics in isolation, before committing to a real
   run: confirm a LoRA/QLoRA adapter actually trains within this card's
   VRAM budget at a realistic batch/sequence length, and confirm the
   train → merge → GGUF → llama.cpp-load round trip works end to end on a
   toy adapter.
6. Train once against the frozen tool surface and the built eval set, per
   the 2026-08-09 plan's "train once against a stable baseline" decision.
7. Validate both ways: score against the held-out eval slice, and do real
   live-use validation before trusting it — this codebase has hit the
   eval-vs-live gap more than once already (the AGC caveat in Part 4 of
   the wake-word session, and the 20-clip model beating its own eval
   numbers live in Part 6), so an eval-set win alone shouldn't be treated
   as sufficient.
8. Decide whether "train once" really holds long-term, or whether a
   periodic retrain cadence gets reopened once real usage keeps producing
   new tool-surface needs — not decided here, matches the existing
   parked item in the 2026-08-09 plan.

---

## 6. Open questions (genuinely undecided, need Vatsal's call)

- **Training framework/hyperparameters.** Not chosen. The vault has a
  standing, still-open item to review a fine-tuning guide for a
  Qwen3.5-4B-shaped setup — but `DEFAULT_TIER` moved from Qwen3.5-4B to
  Qwen3-8B on 2026-08-01, after that item was left open, so that pending
  review may now be pointed at the wrong model family and is worth
  re-checking against Qwen3-8B specifically rather than assumed to
  transfer as-is.
- **"Enough data" — no number set.** Depends on the post-freeze,
  post-robustness-fix row count, which doesn't exist yet.
- **Is raw session-log data in scope at all**, or is this fine-tune built
  entirely from `tool_call_log.jsonl` rows? Leaning toward
  tool-call-log-only for a first pass (cleaner, narrower, already
  weak-labeled), but not decided.
- **Is conversational tone in scope for this fine-tune**, or fully
  deferred to the existing vault-injected persona/profile/rules
  mechanism? Not decided.
- **How much manual review the eval set needs** — the weak-labeling
  scheme (clean/error/interrupted) is explicitly approximate per the
  source module's own comments; whether that's trusted as-is or needs a
  human pass before training is unresolved.
- **How strict to be about excluding wake-word-unstable-window data** —
  flagged in section 1 as a judgment call, not made here.
- **What happens to the 612 rows already logged** before any blocker
  landed — discard, keep for reference only, or re-review once the
  reader exists.
- **Scope beyond Standard** — whether this ever extends to Deep/Vision
  tiers, or stays a Standard-only effort. Nothing here assumes beyond
  Standard.

---

## 7. FREEZE DECLARED — 2026-08-15

**The tool surface is frozen at 80 registered tools.** This is the explicit
decision section 4 said had to be made rather than implied, and section 5's
step 2. Everything below is settled, not proposed.

**Both hard blockers from section 1 are cleared:**

- `Core/scripts/tool_call_report.py` exists and runs. It has since gained
  `--since YYYY-MM-DD` and a dated `EXCLUSIONS` list, both of which exist
  specifically to serve this freeze — see below.
- Tool-calling robustness: the all-time error table read as a broken
  file/path subsystem (`open_path` 4/4, `find_file_smart` 12/12) and is
  not one. Date-split, 66 total errors are 15 since 2026-08-13 and 0 on
  2026-08-15, with the high-rate rows dominated by bugs already fixed —
  `%VARS%` expansion (`smart_search.py:121`) and the abandoned v1 lockdown
  popup. What remains is the "genuinely ambiguous / one-off STT mishear"
  category this plan already anticipated, not a workstream.

**Inside the baseline** (Vatsal's call, 2026-08-15): `call_phone`,
`hang_up`, `sync_contacts`, and `ask_about_myself`. The phone tools were
built after this draft and are admitted deliberately rather than by
default; the self-doc tool is backlog #13 and was always the last item
before the freeze.

**Eval data counts from 2026-08-15 forward.** Rows before it are suspect
per section 5 step 3 — pass `--since 2026-08-15` when building the set.

**Known-bug rows are excluded by default and the exclusions are dated**,
so a row logged after a fix stays eligible and the filter cannot outlive
the bug it describes. Currently excluded:

- `"Cancelled by user"` up to 2026-08-15 — `_handle_pending_confirmation`
  compared against a set of bare words, so a spoken "Yes." (the form
  Whisper produces) cancelled the action instead of running it. Every
  such row is a confirmation Vatsal GAVE, recorded as a refusal. Training
  on them teaches exactly backwards.
- `lockdown_engage` foreground-window errors up to 2026-08-14 — a code
  path that no longer exists.

**Whisper fine-tuning is cancelled** (2026-08-15). It was raised while
looking at far-field transcription accuracy and is now explicitly out of
scope, not deferred. Reasons on record: the only transcripts available as
labels came from Whisper itself, so training on them is self-distillation
that amplifies its errors; the clips actually worth learning from are the
~99 captures where Whisper produced nothing, which are unlabelled by
definition and would need hand-transcription. The far-field accuracy work
stays where it landed instead — decode settings (beam 5, an
`initial_prompt` biased toward command vocabulary, no cross-utterance
conditioning) and, separately, wake-word retraining.

**Wake-word retraining is NOT part of this freeze** and never was — it is
a separate audio model, not a tool. It continues on its own track against
the live capture set (493 clips as of 2026-08-15, 75 clear positives /
312 clear negatives / 99 ambiguous). Mic and placement are staying as they
are, so that data stays valid and keeps accumulating against a fixed
acoustic condition.

**Next**, per section 5: build the eval set from post-freeze rows, then
spike the LoRA/QLoRA mechanics and the train -> merge -> GGUF ->
llama.cpp round trip before committing to a real run.
