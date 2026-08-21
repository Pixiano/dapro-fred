# 06 — Proactive Checks, Memory, Agenda & Tasks

Scope of this file: `Core/memory/memory_manager.py`, `Core/orchestrator/proactive_checks.py`
(excluding deep presence/sleep-mode internals — see `05_presence_and_sleep_mode.md`),
`Core/utils/notifier.py`, `Core/tools/agenda.py`, `Core/tools/daily_tasks.py`,
`Core/tools/session_summary.py`, and the `PROACTIVE CHECKS` / `MEMORY SETTINGS`
sections of `Core/config/settings.py`.

---

## 1. Memory architecture (`Core/memory/memory_manager.py`)

`MemoryManager` is FRED's long-term semantic memory: every user/assistant turn gets
stored to disk and embedded into a FAISS vector index for later semantic recall. It is
distinct from `Core/state/conversation_state.py`'s `ConversationState`, which is
short-term, in-process, and reset to empty on every launch (see below).

### 1.1 Storage model — what actually happens per turn

On every completed orchestrator turn (`orchestrator.py`, both the streaming and
non-streaming code paths, right after the assistant reply is finalized):

```python
self.memory.store("user", user_input)
self.memory.store("assistant", assistant_reply)
```

`MemoryManager.store(role, content)`:

1. Skips silently if `content.strip()` is empty.
2. Embeds `content` via `_generate_embedding` (no query-instruction prefix — see §1.4 on
   the query/document asymmetry), then L2-normalizes the vector (`_normalize`).
3. Appends `{"role": role, "content": content.strip(), "timestamp": datetime.now().isoformat()}`
   to an in-RAM list `self.memories`, **and** appends the same JSON line to
   `self.memory_file` (`MEMORY_DIR/{username}.jsonl`), opened in append mode per call —
   no batching, one disk write per store().
4. Adds the normalized vector to the FAISS index (`self.index.add(...)`) and
   immediately calls `faiss.write_index(...)` to persist the whole index to disk again
   — also on every single store(), not batched.

**Verified: there is no delete, no edit, no category/tagging system.** Every turn (user
or assistant) is stored whole and unfiltered — nothing here does topic filtering, PII
scrubbing, or importance scoring at write time. A turn that later turns out to be
sensitive or wrong sits in the JSONL and FAISS index forever unless something deletes
the files by hand. Confirm this yourself in the file: `store()` has no `id`, no
delete path, and no read-modify-write of existing entries anywhere in the class.

Files/paths (from `config/settings.py`):
- `MEMORY_DIR = DATA_DIR / "memory"` → `Core/data/memory/{username}.jsonl`
- `INDEX_DIR = DATA_DIR / "indexes"` → `Core/data/indexes/{username}.faiss`

Both paths are **absolute**, built from `settings.py`'s `DATA_DIR`, deliberately not
relative to the process's working directory. The file's own comment explains why this
matters: they used to be bare relative `Path("memory_data")` / `Path("memory_indexes")`,
which resolved differently depending on whether FRED was launched from `Core/` (CLI) or
the repo root (GUI) — two completely separate, invisible-to-each-other memory stores
existed simultaneously, so the GUI popup looked like it had zero history while 254
entries sat untouched in the CLI's store. Both legacy stores are preserved under
`data/memory_archive/` (not read by current code — a historical artifact, do not treat
it as live).

### 1.2 FAISS index specifics

- **Index type: `IndexFlatIP`** (inner product), not `IndexFlatL2`. This was a
  confirmed-and-fixed bug (tracked in the vault's `active-priorities.md` as one of four
  open issues as of 2026-07-29): the index used to be `IndexFlatL2` on **raw,
  unnormalized** vectors, so "distance" was literal Euclidean distance, not cosine
  similarity — text-length-driven magnitude differences skewed rankings away from
  actual semantic relevance. The fix: `_normalize()` unit-length every vector before it
  touches FAISS, and the index type changed to `IndexFlatIP`, so inner product on unit
  vectors **is** cosine similarity.
- No approximate index (no IVF/HNSW) — flat, brute-force exact search. Fine at the
  personal-assistant scale this runs at; rebuild it as flat if you rebuild this system,
  don't reach for an ANN index unless memory volume is actually a bottleneck.
- **Index/memory-count desync bug (also tracked in `active-priorities.md`), fixed):**
  `_load_or_create_index()` used to catch *any* exception from a corrupt/mismatched
  index file and silently substitute an empty index, while `self.memories` still loaded
  every JSONL entry. The next `store()` call then wrote to position 0 of the *new* empty
  index while `self.memories` already had N entries — so FAISS index position 0 pointed
  at `memories[N]`, not `memories[0]`, and every retrieval afterward returned
  neighboring-but-wrong entries. There was no rebuild path, so once this happened the
  desync was permanent until someone manually deleted the index file.
  - **Fix, present in current code:** `_rebuild_index()` re-embeds every entry in
    `self.memories` from scratch and rebuilds the FAISS index. It's invoked
    automatically the instant `retrieve_relevant()` notices `self.index.ntotal !=
    len(self.memories)`, and also directly from `_load_or_create_index()` on any load
    failure (dimension mismatch or count mismatch) — so an empty-but-wrong index can no
    longer be left in place silently. This self-healing check runs on **every**
    `retrieve_relevant()` call, not just at startup.

### 1.3 Semantic recall (`retrieve_relevant`)

```python
def retrieve_relevant(self, query: str, top_k: int = 5) -> list:
```

- Returns `[]` immediately if there are no memories at all.
- Runs the desync self-heal check described above before searching.
- Embeds `query` **with** the query-instruction prefix (`is_query=True`), normalizes it,
  and does `self.index.search(query_vector, min(top_k, len(self.memories)))`.
- Returns the raw memory dicts (`{role, content, timestamp}`) at the returned FAISS
  indices — no snippet extraction, no re-ranking, no score threshold filtering. Whatever
  FAISS ranks top-k is returned as-is; a low-similarity match is still returned if there
  simply aren't `top_k` better ones.

Called from `orchestrator.py` in both `_process_with_llm` (non-streaming) and the
streaming path, always with `top_k=5` **hard-coded at the call site**, not imported from
`config.settings.MEMORY_TOP_K` (which is also `5` — the values currently agree, but if
you change `MEMORY_TOP_K` in settings.py, note it does **not** automatically flow to
these call sites; you'd need to update `orchestrator.py`'s two call sites too). Grep
`orchestrator.py` for `retrieve_relevant` before assuming the constant is wired through.

### 1.4 Embedding model — what, why, and its history

`EMBEDDING_MODEL_PATH` (settings.py):
```python
EMBEDDING_MODEL_PATH = MODELS_DIR / "Qwen" / "Qwen3-Embedding-4B-GGUF" / "Qwen3-Embedding-4B-Q4_K_M.gguf"
```

This one embedding model is shared by **three** systems: `MemoryManager` here, tool
routing (`tool_router.py`'s `rank()`), and vault retrieval (`vault_router.py`'s
`retrieve()`) — all three call `Llama.create_embedding()` off model instances built from
the same GGUF file, and all three use the identical asymmetric query/document
instruction convention described below.

**Swap history, documented directly in settings.py's comment block:**

1. **Originally Qwen3-Embedding-0.6B-f16** (MTEB ~64). Rejected/upgraded on
   2026-08-03 because it could not reliably surface `active-priorities.md`'s real
   content for a literal query like "what are my priorities" — measured centered-cosine
   scores of -0.002 to -0.141 (near-noise, sometimes negative), i.e. it was failing at
   the one job memory retrieval exists to do.
2. **Upgraded to Qwen3-Embedding-4B** (MTEB ~69) rather than jumping straight to 8B:
   this model runs **synchronously, on CPU-adjacent inference, up to 3x per single
   conversational turn** (memory retrieval + tool routing + vault routing), so raw
   inference latency matters as much as ranking quality — 4B was the point judged to
   balance both.
3. **Quantization: Q8_0 chosen over f16 first** — near-lossless for embedding tasks at
   roughly half the size/compute (4.28GB vs 8.05GB).
4. **Then Q8_0 → Q4_K_M, same day (2026-08-03):** side-by-side tested against the real
   vault. On `goals.md`'s "Priority order" section across 3 calibration queries: Q4_K_M
   scored 0.338 / 0.412 / 0.326 vs Q8_0's 0.324 / 0.429 / 0.313 — negligible quality
   difference, same #1/#2 ranking every time — for half the disk/VRAM footprint (2.5GB
   vs 4.28GB). Q4_K_M is what ships.
5. **GPU offload was added alongside the 0.6B→4B upgrade** (same `GPU_LAYERS` constant
   the chat-tier LLMs use, `-1` = all layers). At 4B, CPU-only inference made every
   turn's up-to-3 embedding calls noticeably slower; VRAM contention against the chat
   models hasn't been a problem since this model is comparatively small next to them.

**`n_ctx=4096`, set explicitly** rather than left at llama-cpp-python's 512 default —
documented root cause in `memory_manager.py`: this model is invoked up to 3x per turn
across three call sites sharing one `Llama` instance, and rapid repeated calls (e.g.
fast hotkey presses) produced native access violations inside `llama_cpp`'s `decode()`
(logged to `data/logs/crash.log`), consistent with the 512-token context silently being
exceeded. 4096 sits comfortably above any single embedded text in this system (tool
descriptions, vault chunks, memory entries), and the model itself trained on 32768
context, so there's no accuracy tradeoff from raising it.

**Any embedding-model swap invalidates every cached vector.** FAISS memory indexes
self-heal automatically on a dimension mismatch (via `_load_or_create_index`'s rebuild
path, §1.2). `vault_router.py`'s `chunks.json` cache does **not** self-heal — it trusts
a content-hash match regardless of which model actually produced the stored vector, so
after any embedding-model swap that cache file must be **deleted by hand**, or it will
silently mix vectors from two different models in the same index. (Full vault retrieval
mechanics are documented in `04a_orchestrator_core.md` — this is flagged here only
because it's a direct consequence of the shared embedding model.)

### 1.5 Query vs. document asymmetry

`_QUERY_INSTRUCTION`:
```python
"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: {query}"
```

This prefix is applied **only** to the text being searched *with* (the query), never to
the text being searched (stored memory content, tool descriptions, vault chunks) — this
mirrors Qwen3-Embedding's own documented usage convention, and it is generic across all
three call sites (memory, tool routing, vault routing) rather than separately tuned per
caller, on the reasoning that one well-tested "find the passage that answers this
query" instruction covers all three well enough. Added 2026-08-03 alongside the 4B
upgrade after raw, unprefixed queries measurably hurt ranking — the file's comment cites
a concrete failure where an explicitly-archived, dead project outranked the real active
priorities for the query "what are my current priorities" without this prefix.

`memory_manager.py`'s `_generate_embedding(text, is_query=False)` takes the flag
directly; `retrieve_relevant()` is the only caller that passes `is_query=True`, on the
query text. `store()` always embeds with `is_query=False` (documents never get the
instruction prefix).

### 1.6 Startup sequence and dimension probing

`MemoryManager.__init__`:
1. Resolve `memory_dir`/`index_dir`, create if missing.
2. Fail loudly (`FileNotFoundError`) if `EMBEDDING_MODEL_PATH` doesn't exist — no silent
   fallback to a different model.
3. Construct the `Llama` embedding model (`n_ctx=4096`, `n_gpu_layers=GPU_LAYERS`,
   `verbose=False`).
4. Probe embedding dimensionality by actually embedding the string `"dimension probe"`
   and taking `len(...)` — not hardcoded, so a future model swap with a different output
   dimension doesn't require touching this file.
5. **Load `self.memories` from disk BEFORE loading/building the index** — ordering is
   deliberate: the index's rebuild path (`_load_or_create_index`) depends on
   `self.memories` already existing when a rebuild is triggered.
6. Load or build the FAISS index (`_load_or_create_index`, §1.2).

### 1.7 Short-term memory (contrast, not part of this module)

`SHORT_TERM_MEMORY_LIMIT = 10` is declared in `settings.py`'s MEMORY SETTINGS block but
**is not actually imported or referenced anywhere in the codebase** (grep confirms
zero non-definition matches). The short-term conversational window that this constant
apparently documents is instead a hardcoded literal: `orchestrator.py` calls
`self.state.get_recent_messages(limit=10)` at both call sites (streaming and
non-streaming), and `ConversationState.get_recent_messages(self, limit: int = 10)`
(`Core/state/conversation_state.py`) has its own independent default of `10`. The
numbers currently agree by coincidence, not by wiring — if you rebuild this, either wire
`get_recent_messages`'s default/call sites to the settings constant, or delete the
unused constant; don't assume changing `SHORT_TERM_MEMORY_LIMIT` does anything today.

`ConversationState` itself is short-term, in-RAM, per-process conversation history —
reset to empty on every FRED launch, entirely separate from `MemoryManager`'s persistent
long-term store. `session_summary.py`'s `recall_recent_conversation()` (§6) is the
mechanism that survives a restart, by reading the day's session log file instead of
in-memory state.

---

## 2. Proactive checks (`Core/orchestrator/proactive_checks.py`)

### 2.1 Framing

Built from "Observation B" in a 2026-08-01 feedback session: the plumbing for
unprompted speech already existed (`utils/notifier.py`, the popup UI's
`_speak_proactive`) but nothing had ever decided *when* to use it outside of
scheduled reminders. Every check function in this module is registered as a periodic
job via `register(scheduler, llm=None, on_agenda_ask=None)`, called once at orchestrator
startup.

**Every check funnels through this module's own `notify()`** (a thin wrapper that
shadows the real `utils.notifier.notify`, imported as `_real_notify`):

```python
def notify(*args, **kwargs):
    if sleep_mode.is_sleeping():
        return
    _real_notify(*args, **kwargs)
```

This centralizes the sleep-mode gate in one place instead of every check function
independently calling `sleep_mode.is_sleeping()`. A gated nudge during sleep mode is
simply dropped — no queue, no replay when the user returns — matching the same "fire
once or not at all" precedent scheduled reminders already followed. (Sleep mode itself
is documented in full in `05_presence_and_sleep_mode.md`; this file only needs to know
that `notify()` no-ops while asleep.)

### 2.2 Dedup state file

`PROACTIVE_STATE_PATH = DATA_DIR / "proactive_state.json"`. `_load_state()` /
`_save_state()` read/write this as one JSON blob, atomically (`_save_state` writes to a
`.json.tmp` sibling then `.replace()`s it into place — avoids a half-written state file
if the process dies mid-write). Each check function `setdefault`s its own named
sub-dict inside this blob (`"stale"`, `"long_session"`, `"deadlines"`,
`"task_deadlines"`, `"agenda_deadlines"`, `"agenda_event_prep"`,
`"agenda_event_upcoming"`, `"agenda_carryover"`, `"rollover_day"`) — one shared file,
namespaced per-check, not one file per check.

The header comment states the governing rule directly (from `persona.md`): a second
reminder about the same thing is acceptable, a third is nagging — so **every** check
here dedups against this state file rather than re-firing on every 15-minute tick.
Notably, `check_vip_messages` and `check_recent_calls` (§2.10) deliberately do **not**
use this mechanism — they have their own finer-grained watermarks.

### 2.3 Check 1 — Vault staleness (`check_vault_staleness`)

- **Reads:** `VAULT_DIR / "active-priorities.md"`'s frontmatter `updated:` field only —
  explicitly **not** a per-bullet parse of the file's prose. The file's own comment
  states the reasoning: the file is hand-curated prose with no per-item machine-readable
  timestamp (checked before building this), so a whole-file "when was this last
  touched" signal is the only honest granularity available — it cannot tell you *which*
  priority is stale, only that the file as a whole hasn't been edited. (§4 below
  expands on why this file-level date was chosen over any smarter parsing.)
- **Trigger:** `days_since(updated) >= PROACTIVE_STALE_DAYS` (7).
- **Dedup:** keyed on the `updated` string value itself, not a boolean flag — so if the
  file gets a fresh edit and then goes stale *again* later, it notifies again rather
  than being silenced forever by one old "already notified" flag.
- **Interval:** `PROACTIVE_CHECK_INTERVAL_MINUTES` (15).

### 2.4 Check 2 — Long session / no break (`check_long_session`)

- Uses real **OS-level** idle time via `GetLastInputInfo`/`GetTickCount` (Win32, ctypes
  structure `_LASTINPUTINFO`) — system-wide keyboard/mouse idle time, not FRED
  conversational activity. The comment is explicit about why: "no one has spoken to FRED
  in an hour" says nothing about whether the user actually stepped away from the machine.
- **State machine per stretch:**
  - If currently idle ≥ `PROACTIVE_BREAK_IDLE_MINUTES` (15 min) → that idle gap *counts
    as* a real break: resets `last_break` to now and clears the `notified` flag, so the
    next long stretch can notify again.
  - Otherwise, if no `last_break` recorded yet (first check since launch), starts
    counting from now rather than assuming a long session was already underway before
    FRED started.
  - If `now - last_break >= PROACTIVE_LONG_SESSION_HOURS` (3 hours) and not already
    notified this stretch, fires the notification once.
- **Interval:** 15 min.

### 2.5 Check 3 — Deadline proximity, frontmatter-based (`check_deadlines`)

- Reads an **optional** `deadline: YYYY-MM-DD` frontmatter field on **any** vault file
  (`VAULT_DIR.rglob("*.md")`). As of the file's writing (2026-08-01), the comment notes
  no vault file actually used this field yet — this is documented as the read path
  waiting for that field to exist, not speculative prose-date parsing.
- **Trigger:** `0 <= days_until(deadline) <= PROACTIVE_DEADLINE_WARN_DAYS` (7).
- **Dedup key:** `f"{path.name}|{deadline_str}"` — a changed deadline re-notifies.
- Speaks using the file's `type:` frontmatter as a label (defaults to `"deadline"`).
- **Interval:** 15 min.

### 2.6 Check 4 — Task deadlines from daily notes (`check_task_deadlines`)

- Distinguished explicitly from Check 3: real deadlines in practice live in **daily
  note task lines** ("due Thursday in school"-style text), not in frontmatter — the
  comment cites a confirmed incident (2026-08-04) where a real chemistry-journal
  deadline and a physics one went unmentioned entirely until the user pushed back twice
  asking what was pending, because nothing was watching daily-note task text.
- Calls `daily_tasks.open_due_tasks(within_days=PROACTIVE_TASK_DUE_DAYS)` (§4).
- **Dedup key:** per task text + due date (`f"{text}|{deadline:%Y-%m-%d}"`) — a re-dated
  task warns again, an unchanged one doesn't nag every 15 minutes.
- Phrasing varies: "N day(s) overdue" / "due today" / "due tomorrow" / "due in N day(s)".
- **Interval:** 15 min.

### 2.7 Checks 6–8 — Agenda-based checks

Three separate checks read from `tools/agenda.py` (§5), each with a distinct purpose —
the file's own comments are careful to distinguish "statement" checks (fire-and-forget)
from "question" checks (expect a spoken answer that must land somewhere real):

- **`check_agenda_deadlines`** ("statement" — homework/project items due soon or
  overdue): calls `agenda.due_within(PROACTIVE_TASK_DUE_DAYS)`. Dedup key bakes in the
  item's `when` value, not the "days until" phrasing, so an item that stays overdue day
  after day does **not** re-nag here — that re-asking behavior is deliberately split out
  into `check_agenda_carryover` instead, because that one is a question expecting an
  answer and this one is not.
- **`check_agenda_event_prep`** ("statement" — an event's prep window has just opened):
  calls `agenda.events_needing_prep()`. Fires once: "`{event}` starts at `{time}`, sir —
  time to start getting ready." Dedup key includes `when`, so a rescheduled event's prep
  warning fires again for the new time.
- **`check_agenda_events_upcoming(on_agenda_ask=None)`** ("question" — an event
  starting within `_EVENT_UPCOMING_HOURS` = 24h): calls
  `agenda.events_upcoming(within_hours=24)`. Asks once: "`{event}` at `{time}`, sir — are
  you prepped for it?" **This is a question**, unlike every check above it — the reply
  needs to land on `agenda.update_item`, not just conversation memory, so after
  speaking, this check calls `on_agenda_ask(["update_agenda_item"])` if a callback was
  provided, priming the orchestrator's tool-carry-forward mechanism for the next turn.
- **`check_agenda_carryover(on_agenda_ask=None)`** ("question", re-asked **daily**):
  calls `agenda.carryover_candidates()` — still-open homework/project items due today or
  earlier. Dedup key deliberately **includes today's date**
  (`f"{kind}|{subject}|{when}|{today}"`), the opposite convention from
  `check_agenda_deadlines` above — this check is meant to keep checking in on an
  outstanding item every single day it remains open ("was X due — did you finish it, or
  find a workaround?"), not warn once and go quiet. Same `on_agenda_ask` priming as
  the upcoming-event check, same reason: the reply is the actual record update.

All four: `PROACTIVE_CHECK_INTERVAL_MINUTES` (15).

### 2.8 Check 5 — Overnight day rollover (`check_day_rollover`)

Documented in full in §4 below (shared with `daily_tasks.py`'s carryover mechanism).

### 2.9 VIP WhatsApp and missed-call checks

- **`check_vip_messages`**: lazily imports `tools.whatsapp_tools.check_vip_messages`
  (`fetch`), calls it, and `notify()`s the returned summary if non-empty. **Deliberately
  does not use `_load_state()`** — the comment explains `whatsapp_tools` keeps its own
  seen-set keyed on each message's own epoch timestamp, a finer granularity than "once
  per stretch," which is the correct granularity here: two distinct VIP messages an hour
  apart are two separate things worth announcing, whereas the same message must never be
  announced twice. Any exception is logged via `event_log.log_error` and swallowed —
  a phone that's asleep or off the network is treated as the normal case, not an error,
  so it must never crash the scheduler.
- **`check_recent_calls`**: same shape, backed by `tools.phone_tools.check_recent_calls`,
  which keeps its own watermark (highest call `date` seen). Same non-raising contract.
- **Intervals:** both run far more often than the 15-minute default —
  `VIP_MESSAGE_CHECK_MINUTES = 2` and `CALL_LOG_CHECK_MINUTES = 2`. Rationale from
  `settings.py`: "someone important just messaged/called you" loses its value if it
  surfaces a quarter-hour late; the underlying check is a cheap `adb` round-trip that
  costs nothing extra when no phone is attached, so there's no reason to poll it any
  less often. `CALL_LOG_CHECK_MINUTES`'s comment additionally notes that its watermark's
  useful case — "who called while FRED was off" — only works if the *first* check after
  startup lands soon after startup; at the 15-minute default that window would be wide
  enough for "just now" to mean nearly 15 minutes late.

### 2.10 Presence check (brief — full detail in `05_presence_and_sleep_mode.md`)

`check_presence()` polls the camera via `input/presence.poll_once()` every
`PRESENCE_POLL_SECONDS / 60` minutes (`PRESENCE_POLL_SECONDS = 15`, fed to
`scheduler.add_periodic` as an exact fractional-minute float — `15/60 == 0.25` exactly,
no rounding error). It records whether `sleep_mode.is_sleeping()` was true *before*
calling `sleep_mode.on_presence_poll(present)` (since that call is what would flip
sleeping state for this very poll), and speaks a randomly-chosen "welcome back" greeting
from `_PRESENCE_GREETINGS` only on a **real, debounced** wake (i.e. `present and
was_sleeping`) — never on a single-poll blip. The debounce threshold (3 consecutive
absent polls) and the rest of sleep-mode's state machine live in `orchestrator/sleep_mode.py`
and are covered in `05_presence_and_sleep_mode.md`. Camera/vision-model failures are
caught and logged, never allowed to crash the scheduler.

### 2.11 Constants table (from `config/settings.py`, `PROACTIVE CHECKS` section)

| Constant | Value | Documented rationale |
|---|---|---|
| `PROACTIVE_CHECK_INTERVAL_MINUTES` | 15 | Default cadence for most checks — cheap operations (frontmatter reads, one Win32 API call), no reason to poll faster. |
| `VIP_MESSAGE_CHECK_MINUTES` | 2 | VIP WhatsApp needs to be near-immediate; the underlying `adb` check is cheap even when no phone is attached. |
| `CALL_LOG_CHECK_MINUTES` | 2 | Same cheap-poll reasoning as VIP messages; additionally needed so the post-startup "who called while FRED was off" watermark check lands soon after launch, not up to 15 minutes late. |
| `PROACTIVE_STALE_DAYS` | 7 | Days since `active-priorities.md`'s `updated:` frontmatter before it's flagged stale. |
| `PROACTIVE_BREAK_IDLE_MINUTES` | 15 | Idle gap (Win32 idle time) that counts as "he took a break," resetting the long-session clock. |
| `PROACTIVE_LONG_SESSION_HOURS` | 3 | Continuous machine use with no qualifying break before the break nudge fires. |
| `PROACTIVE_DEADLINE_WARN_DAYS` | 7 | Window for a vault file's frontmatter `deadline:` field to count as "upcoming." |
| `PROACTIVE_TASK_DUE_DAYS` | 2 | Window for a daily-note "due <day>" task or agenda homework/project item to be raised — kept short (vs. the 7-day deadline window) because warning a full week ahead of something not yet actionable trains the user to ignore it. |
| `ROLLOVER_IDLE_HOURS` | 2 | Idle time before FRED treats the day as "over" for day-rollover purposes — long enough that a normal lunch/errand break doesn't trigger a mid-day rollover. |
| `PROACTIVE_STATE_PATH` | `DATA_DIR / "proactive_state.json"` | Single shared dedup-state file, namespaced per-check (see §2.2). |
| `MEMORY_TOP_K` | 5 | Default semantic-recall breadth (see caveat in §1.3 — call sites currently hardcode `5` rather than importing this). |
| `SHORT_TERM_MEMORY_LIMIT` | 10 | Declared but unused (see §1.7) — the actual short-term window is a hardcoded `10` at each call site and in `ConversationState`'s own default. |
| `EMBEDDING_MODEL_PATH` | `Qwen3-Embedding-4B-Q4_K_M.gguf` | See §1.4 for the full 0.6B→4B, Q8_0→Q4_K_M swap history. |

---

## 3. `utils/notifier.py` — the notify/dedup mechanism

Built for "Phase 15 — He Speaks First": proactive notifications must actually interrupt
— a Windows toast (visual) plus spoken TTS (voice), not a silent `print` buried in a log.

`notify(message: str, title: str = "F.R.E.D.")` does three things every call, in order,
each independently try/excepted so one failing doesn't block the others:

1. `print(f"\n[F.R.E.D.] {message}\n")` — console.
2. A `winotify.Notification` toast (`app_id="F.R.E.D."`, `duration="short"`).
3. Spoken via `_speak()`, which routes to whichever voice was injected via
   `set_voice(speak_callable)` (the GUI's Kokoro TTS instance), falling back to a lazily
   constructed SAPI `TTSManager` (`audio/tts.py`) if `set_voice` was never called — e.g.
   the CLI, which never starts Kokoro. This exists specifically so a reminder doesn't
   interrupt in a jarringly different voice than the one you'd been talking to.

**Context-carry mechanism (`_last_proactive` / `last_proactive()`):** every `notify()`
call records `{"kind": title, "message": message, "at": time.time()}` into a module-level
`_last_proactive`, retrievable via `last_proactive(within_seconds=PROACTIVE_CONTEXT_SECONDS)`
(default 600s / 10 minutes) which returns `None` if the most recent proactive utterance
is older than the window. This is kept **deliberately separate from the conversation
transcript** — the comment explains the transcript records *what* was said (attributed
to FRED as if it spoke it normally), while this side-channel records that it was
*unprompted* and *what kind*, so `_build_messages` (in `orchestrator.py`) can render it
as short-lived context for a natural follow-up like "what was that?" or "how long till
then?" without ever putting a machine-looking tag like `[Reminder]` into the transcript
itself (which would risk the model learning to imitate that tag aloud).

**`set_recorder(record_callable)`:** routes what FRED says proactively back into
`ConversationState` so a spoken reply to a proactive question isn't answered from
amnesia. The docstring documents the confirmed bug this fixes (2026-08-09): a proactive
check asked "are you prepped for [movie just logged]?", the user replied "No, not yet.",
and FRED replied "I won't log the movie" — it interpreted the negative as declining to
*log* something, because `notify()` never told `ConversationState` a question had been
asked at all. `_build_messages` only ever sees `self.state.get_recent_messages()`, and
proactive speech happens entirely outside `process()`/`process_stream()` — the only two
places that ever wrote to conversation state before this fix. Fixed once, at the single
choke point every proactive check speaks through, rather than patching each individual
check that happens to ask a question. Pass `None` (default) to record nothing — used by
the CLI or any caller with no conversation state to keep in sync.

---

## 4. Day rollover (`ROLLOVER_IDLE_HOURS`, implemented across `proactive_checks.py` + `daily_tasks.py`)

**Trigger condition** (`check_day_rollover`, in `proactive_checks.py`):
```python
if _idle_seconds() < ROLLOVER_IDLE_HOURS * 3600:
    return
today = datetime.now().strftime(_DATE_FMT)
state = _load_state()
if state.get("rollover_day") == today:
    return
```
Two conditions must both hold: the machine must currently be idle (Win32 idle time) for
at least `ROLLOVER_IDLE_HOURS` (2) hours **continuously right now**, and the wall-clock
date must have actually turned over since the last recorded rollover. This is why a
two-hour idle gap *inside* the same calendar day does nothing — there's no new day to
roll into yet, regardless of how long the idle gap was. It only fires once per calendar
date transition (keyed by `state["rollover_day"]`), and specifically it fires
*retroactively* whenever the machine finally goes idle for 2+ hours after midnight has
already passed (e.g. overnight sleep) — not at exactly midnight.

**What it does, step by step:**
1. `daily_tasks.carryover_candidates(today)` — still-open task lines whose *origin day*
   predates `today` (§5.2 below explains `origin` tracking).
2. `daily_tasks.ensure_day_note(today)` — creates today's daily note (header only) even
   if there's nothing to carry over. The comment is explicit about why: "there is a log
   for today" is the point of this call by itself — an empty note still gets appended to
   later by `add_task` and the session recap.
3. **LLM-judged filtering** (`_judge_carryover`): rather than blindly copying every
   still-open task forward forever, candidates are passed to the **Deep tier** LLM
   (Qwen3-14B, pinned `local_only=True`) alongside `_recent_transcript(today)` — the
   previous day's *and* today's raw conversation logs concatenated (both, because a
   rollover firing at 01:00 sits on the far side of midnight from the evening it's
   actually summarizing). The model is asked which numbered tasks are still worth
   carrying, told to drop anything the log shows was finished, explicitly dropped, or
   tied to a date that's passed and can't be redone, and to keep anything it's unsure
   about. `local_only=True` is deliberate: this reads the vault's task text *and* the
   day's raw conversation — exactly the kind of material that shouldn't leave the
   machine for a filtering job a local model handles fine.
   - **Safety property:** the LLM's answer is parsed as comma-separated numbers via
     regex (`re.findall(r"\d+", answer)`) and matched back against the actual candidate
     list by index — anything that doesn't map to a real candidate index is silently
     dropped, so a hallucinated task text can never be injected into the note.
   - **Fail-open on judgement failure:** if the LLM call raises, or the answer is empty
     / unparseable, **everything carries** rather than nothing — the code comment states
     the reasoning directly: losing a task silently is worse than carrying one that was
     already dead.
4. Each surviving candidate text is re-appended via `daily_tasks.add_task(text,
   day=today)`.
5. `state["rollover_day"] = today` is saved, so the rollover doesn't re-run again today.

**Where the actual carry-forward data comes from** (`daily_tasks.py`,
`carryover_candidates`): built on top of `_all_tasks(day)`, which merges every daily
note in the current month directory (`VAULT_DIR/daily/<month>/*.md`) up to and including
`day`, tracking `{task_text: (done, origin_day)}` with **later days winning on
conflicting status** — so completing a task today that was originally logged three days
ago still correctly marks it done everywhere it's read. `carryover_candidates` filters
this merged view down to tasks that are `not done` and whose `origin != day` (i.e.
genuinely came from an earlier day, so running rollover twice can never duplicate a
line — anything already logged under `day` itself is excluded).

---

## 5. `tools/agenda.py` — homework/project/event tracking

### 5.1 Purpose and how it differs from `daily_tasks.py`

Built 2026-08-09 specifically because unreliable deadline tracking was named as the one
gap that would make the user actually rely on FRED daily. Backed by **one persistent
file**, `VAULT_DIR/agenda.md` — explicitly **not** sharded by day the way
`daily_tasks.py` is. The file's own comment states the distinction plainly: the entire
point of an agenda item is that it needs to be found again *days later, by subject* —
"what's left for Geography" — not by which day it happened to be logged. `daily_tasks.py`
is a same-day disposable scratch list; `agenda.py` is a durable, subject-searchable
record with structured fields (due dates, progress counts, prep windows) that
`daily_tasks.py`'s free-text checkbox lines don't have.

Originally named `school_tasks.py`, and the third kind was originally implicit as
"school-only" — renamed the same day, before shipping a second real item, when a movie
someone wanted logged had to go through a tool literally called `add_school_item`. The
`"event"` kind (homework/project/**event**) was always meant to also cover personal
plans (a movie, meeting friends), not just school deadlines; only the naming had
quietly narrowed to school.

**Design principle stated directly in the header comment:** entity extraction
(subject/count/due/kind) is the LLM's job, done via the tool call's own structured
arguments — that's what tool-calling already is, and far more reliable than asking a
small local model to freehand-parse a whole sentence. This module's job is turning
those already-extracted arguments into one deterministic file line and back — it never
guesses at prose itself.

### 5.2 Storage format

`VAULT_DIR/agenda.md`, frontmatter `type: log`, `status: active`, one `## Items`
section. Each item is a single markdown checkbox line, pipe-delimited fields:
```
- [ ] kind | subject | detail | when YYYY-MM-DD[ HH:MM] | progress D/T | prep N | next: ... | note: ...
```
`kind` ∈ `{"homework", "project", "event"}`. `when`/`progress`/`prep`/`next`/`note` are
optional trailing fields, order-independent, parsed by prefix match
(`_parse_line`/`_serialize`). A malformed or foreign line is silently skipped on read,
never crashes the whole file load (`_parse_line` returns `None`, filtered out) — same
fail-open convention `daily_tasks.py` uses for its own line parsing.

Items are re-sorted on every save (`_save_items`): open items first (soonest due), then
done ones — so the file itself reads like an actual to-do list rather than an append-only
log.

### 5.3 Date parsing (`parse_due_date`, `_resolve_when`, `_named_month_date`)

Two existing parsers were checked and rejected before writing a third: `daily_tasks.parse_due`
requires the literal word "due" embedded in a sentence (this module gets a dedicated
`due` argument, not a sentence to search), and `orchestrator.scheduler.parse_when`
always resolves to an exact minute (built for reminders, which need one; an agenda due
date usually doesn't have a clock time at all). `agenda.py`'s own `parse_due_date`
covers the bare-date case neither owns ("in 3 days" with no time component), and
`_resolve_when` defers to `scheduler.parse_when` only when a `time` argument is *also*
given, reusing its weekday/tomorrow/ISO handling rather than duplicating it.

Handles: `today`, `tomorrow`, `next week`, `in N day(s)/week(s)`, bare weekday names
(next occurrence, wrapping via `% 7`), ISO `YYYY-MM-DD`, and **named-month dates in both
day-month and month-day order** (`_named_month_date`) — added because school notices
read dates as "13 August 2026," not ISO; day-month is the primary order tried first
(matches CBSE/Indian-convention date writing), month-day is checked second in case a
transcribed or copy-pasted date arrives that way instead. A year-less named-month date
that would land in the past is rolled forward one year, same convention the bare-weekday
branch already uses.

### 5.4 Fuzzy item matching (`_find_candidates`)

Layered fallback, same shape and same 0.7 difflib cutoff `phone_tools.find_contact`
already established (exact → substring → difflib) — reused as precedent, not re-tuned.
Compares against `subject + " " + detail` **joined as one string**, not the two fields
separately. The file documents a confirmed live failure (2026-08-21): the LLM sometimes
echoes an item's *entire* subject+detail concatenated back as the `match` argument
(having read it from `list_items`'s own "subject — detail" display format earlier in the
same turn); comparing fields separately failed both because of punctuation/quote-style
drift between what got echoed and what's stored, and because a long combined echo
scores badly in length-sensitive fuzzy matching against either field alone even though
it's unmistakably the same item to a human reader. Joining the fields fixes both at once.

### 5.5 Public tool functions

- `add_item(kind, subject, detail="", count=None, due="", time="", prep_minutes=None, next_step="")`
  — one item per call by design (a turn naming two subjects gets asked again for the
  second one, per `intent.looks_compound`'s multi-item detection elsewhere in the
  codebase, rather than silently dropping it). Refuses an `event` with `prep_minutes`
  but no clock time — prep countdown needs a real start time.
- `list_items(when="", kind="", subject="")` — always reads fresh from the file, never
  answered from conversation memory. `when` ∈ `all/today/tomorrow/week/overdue`.
- `update_item(match, done=None, add_progress=None, set_progress=None, new_due="", new_time="", note="", next_step="")`
  — this is explicitly where a reply to a proactive question (§2.7's "question" checks)
  actually lands: "did you finish the geography questions" → the answer updates the
  real record here, never just acknowledged in speech and forgotten. Matches soonest-due
  among fuzzy candidates; mutates the dict in place (no separate write-back step needed
  since the matched dict is the same object living in the loaded list).
- `delete_item(match)` — added because `update_item`'s ambiguity handling (soonest-due
  wins) is fine for updates but wrong for deletion: a mismatch here is not a harmless
  no-op, it's gone, so **more than one match refuses outright** rather than guessing.
  Motivated by a real incident (2026-08-09) where a two-part school notice had been
  merged into one wrong item with no way to remove it short of hand-editing the vault.

### 5.6 Proactive query helpers (read-only, consumed by `proactive_checks.py`)

`open_items()`, `due_within(days, kind_filter=None)` (excludes events — they have their
own prep/upcoming checks), `events_needing_prep(now=None)`, `events_upcoming(within_hours=24)`,
`carryover_candidates(today=None)`. Each returns raw item dicts, never spoken text —
phrasing is deliberately left to the caller in `proactive_checks.py`, which knows which
dedup key and framing applies to each check.

---

## 6. `tools/daily_tasks.py` — same-day scratch tasks

### 6.1 Purpose

A same-day scratch task list backed by the vault's daily note
(`VAULT_DIR/daily/<month>/<day>.md`) — the same file and header format
`session_summary.py` writes its own recap into (§7). Built 2026-08-03 after confirming
FRED had been narrating task-saves that never actually happened: `active-priorities.md`
hadn't been touched since 2026-08-01, no `daily/2026-08` notes existed at all, yet FRED
had said "your goals for today are logged" more than once. This module gives "add to
today's tasks" and "mark X complete" something real to call.

**Deliberately has no confirmation gate**, unlike `session_summary.py`'s recap flow
(§7): that writes free-form prose to a file the rules treat carefully, so it previews
before saving. This module only ever appends one short checkbox line at a time to a
disposable same-day section — asking first would be friction on every single item for
something this low-stakes.

### 6.2 Due-date extraction from free text (`parse_due`, `_DUE_RE`)

Regex: `\bdue\s+(?:on\s+|by\s+)?(today|tomorrow|YYYY-MM-DD|<weekday>)\b`. Motivated by a
real logged line, `"Chemistry journal completion — due Thursday in school"`, that
nothing could parse — so a task with a real deadline was indistinguishable from one
without, and `proactive_checks.py`'s deadline check at the time only read the (unused)
frontmatter `deadline:` field. A bare weekday resolves to the **next occurrence
including today** (`% 7` offset) — "due Thursday" spoken on Thursday morning is due
*right now*, not in seven days; the alternative (always forward-looking) would silence
the warning on exactly the day it matters most.

### 6.3 File structure and merge-across-days behavior

Daily note header: `type: log`, `status: active`, `updated: <day>` frontmatter, `#
<Weekday, Month DD, YYYY>` heading, then a `## Tasks` section of checkbox lines.
`_split_tasks_section` / `_rewrite` mirror the exact same before/heading/items/after
contract `agenda.py`'s `_split_items`/`_write` use.

**`_all_tasks(day=None)`** is the shared core both `list_tasks` and `open_due_tasks`
build on: merges every daily note in the day's month directory up to and including
`day`, producing `{task_text: (done, origin_day)}`, **later days winning on conflicting
status**. Documented rationale for why this one function is shared rather than each
caller re-deriving "still open" independently: a proactive deadline warning using a
different notion of "still open" than the spoken task list would be a bug nobody would
notice for weeks. `origin_day` matters because a merged list that doesn't distinguish
today's tasks from carried-forward ones reads as "today's tasks" to both FRED and the
user — confirmed incident (2026-08-05): yesterday's task list was spoken as if it were
today's, before origin tracking existed.

`list_tasks(day=None)`: today's tasks plus anything still open from earlier in the
month, each line tagged with its origin day when it isn't today (`"(from <day>)"`).
Motivated by a second confirmed gap (2026-08-04): a chemistry-journal task logged
2026-08-03 was invisible the next day because the old code only ever looked at the
current day's note. Output is deliberately **not** bracket-tagged with status words
inside `[...]` — `audio/tts_kokoro.py`'s `clean_for_speech()` strips any `[bracket]`
content as machine noise (the same pass that swallows tool-call job IDs), which would
silently eat the done/open status too if it were bracket-formatted; plain words survive
that pass.

`complete_task(match, done=True, day=None)`: first-match, case-insensitive substring on
task text within the given day's own note only (not merged across days) — toggles the
checkbox and rewrites.

`open_due_tasks(day=None, within_days=2)`: `[(due_date, text)]` for still-open tasks
(from `_all_tasks`) whose `parse_due`-extracted deadline is within `within_days`,
**including already-overdue ones** — the comment states a missed deadline is more worth
surfacing than an upcoming one, not less. This is the function
`proactive_checks.check_task_deadlines` (§2.6) calls directly.

`ensure_day_note(day=None)` / `carryover_candidates(day=None)`: the two entry points
`proactive_checks.check_day_rollover` (§4) uses.

---

## 7. `tools/session_summary.py` — day/session recap

### 7.1 Source of truth: event logs, not model memory

The summary is built from `utils/event_log.py`'s session event logs
(`SESSIONS_DIR`, imported as `SESSIONS_DIR = event_log.SESSION_DIR` rather than
re-derived from `LOG_DIR` — explicitly to avoid a second copy of that path drifting from
the original, which the project's `rules.md` is cited as warning against directly), not
from the LLM's own memory of the conversation. Reasoning stated directly: the logs are
what actually happened, including turns from sessions that have since been restarted,
and they survive FRED being killed mid-day — the model's own context does not.

`_today_logs(day)` globs `session_{day}*.jsonl` (note the `*` immediately after the
date, no literal underscore required before it). The comment flags a confirmed silent
bug this specific glob shape fixes: logs were consolidated to one file per day, and an
earlier glob pattern (`session_{day}_*.jsonl`, requiring a trailing underscore) only
ever matched the old pre-consolidation per-launch filenames — after consolidation it
matched *nothing*, and `summarise_today` answered "Nothing logged today yet, sir." every
single day regardless of actual activity, with no error and no empty file to notice.
Keeping the bare `*` also still covers any unmerged legacy per-launch file.

### 7.2 `collect_today` / `transcript` / `recall_recent_conversation`

- `collect_today(day)`: counts + raw material — `asks` (user speech text), `replies`
  (non-filler assistant speech, `filler=True`-tagged entries excluded because they carry
  no information and would triple summary length), `tools` (tool-call counts by name),
  `interrupted` count, and a `sessions` count derived from `note == "session start"`
  markers rather than counting log files (one file per day now, so file-counting would
  always report "1 session" no matter how many times FRED was relaunched that day).
- `transcript(day, limit=200)`: full ordered both-sides conversation, **including which
  tools ran between turns** (`[tool: {name}]` lines) — `collect_today` throws reply text
  away entirely, which is fine for a themed recap but useless as raw context, since e.g.
  "did you finish the journal?" only means something next to its actual answer. Keeps
  the **last** `limit` turns (a long day's relevant context is its end, not its start).
  This is the function `_recent_transcript` in `proactive_checks.py` (§4) calls for
  day-rollover's carryover judgement.
- `recall_recent_conversation(count=20)`: verbatim recent conversation, not a
  theme-grouped summary and not semantic memory search — the comment notes semantic
  search handles a vague query like "what did we just talk about" poorly since it has no
  strong content of its own to match against. Reads from today's session log via
  `transcript()`, not in-memory `ConversationState`, specifically so it survives FRED
  being restarted mid-conversation (which resets `ConversationState` to empty on every
  launch).
- Has a `if __name__ == "__main__":` self-check (assert-based, not `Core/tests/`, since
  the file's own comment states `Core/tests/`'s README scopes it to regression-only
  pinned bugs and this is new logic) that writes a temp session log, swaps
  `SESSIONS_DIR` at the module-global level, and asserts filler lines are excluded and
  non-filler lines are present.

### 7.3 `summarise_today` — LLM or fallback

Returns `"Nothing logged today yet, sir."` if there are no `asks` at all. With an `llm`
handle, sends the day's asks (capped at 40) plus a top-5 tool-usage breakdown to a
system prompt instructing 3–5 short grouped bullet points, explicitly forbidding
inventing anything not in the list. Without an `llm`, falls back to a plain count
sentence (`"{N} request(s) across {M} session(s) today. Tools used: ..."`) — still true,
still useful, no LLM dependency required.

### 7.4 Propose-only write flow

Writing to the vault here is **propose-only**, per the same stricter convention the
header comment cites `rules.md` as applying to `daily/` files that name people and
projects (even though `rules.md` technically permits `daily/` to be session-editable):

- `preview_session_summary(day, llm)`: builds the summary text and states exactly where
  it *would* be written and whether that means creating vs. appending, **without
  writing anything** — this is the function that runs by default.
- `save_session_summary(day, llm, summary="")`: the separate, explicit second step,
  only ever called after explicit user confirmation.

### 7.5 Auto session block (`start_daily_session`) and idempotent recap insertion

`start_daily_session(day)`: auto-creates today's vault session block **once per
calendar day** (not once per FRED launch) — same precedent as `event_log.py`'s own
session-file consolidation, and for the same reason: relaunching FRED later the same day
should resume the existing record, not fork a new one. Idempotency is achieved without a
separate state file: an HTML comment marker (`<!-- fred-session:{day} -->`) is written
into the note, and a second call the same day finds the marker already present and
returns an empty string (nothing new to announce in the startup greeting). The block
includes fixed sub-headings: `### What Got Done`, `### What's Still In Progress`,
`### Decisions Made`, `### Notes Touched`, `### Profile Updates`, each starting with a
single empty `-` bullet.

`save_session_summary`: inserts a timestamped `**Recap — HH:MM:** ...` line directly
under the day's session-block heading (found via the same marker), rather than appending
a separate top-level `## FRED session recap` block — the goal being one place per day
for everything FRED logs, not a scattered pile of top-level blocks. If the marker is
somehow missing when this runs (e.g. called from a path that skipped the popup's normal
startup sequence which calls `start_daily_session` first), it calls
`start_daily_session` itself first; if the marker is *still* not found after that, it
fails open by appending the recap to the end of the file anyway rather than losing it
silently.

---

## 8. Vault cross-reference

Full vault retrieval mechanics (`vault_router.py`, chunking, `chunks.json` caching,
`VAULT_RETRIEVAL_TOP_K`, etc.) are documented in `04a_orchestrator_core.md` — not
duplicated here. The one place this file's scope intersects the vault directly is
`proactive_checks.check_vault_staleness` (§2.3) and `check_deadlines` (§2.5), both of
which read `active-priorities.md` (or, for deadlines, any vault file) by opening the
file directly and parsing its YAML-style frontmatter with `utils.vault_md.parse_frontmatter`
— **not** through `vault_router`'s semantic retrieval path at all. This is a deliberate,
narrow choice: semantic vault retrieval answers "what's relevant to this query," which
is the wrong tool for "has this specific file been edited recently" — that question
needs one exact field off one exact file, not a similarity search across chunked
content. Parsing the frontmatter's `updated:`/`deadline:` field directly is a whole-file,
structured signal that exists precisely because the alternative (date-parsing the file's
free-form prose bullets to guess which individual item is stale) was checked and
rejected as unreliable — see §2.3's fuller explanation.

---

## Notes on what could not be confirmed from source alone

- `MEMORY_TOP_K` and `SHORT_TERM_MEMORY_LIMIT` are declared in `settings.py` but not
  actually imported at their apparent call sites (§1.3, §1.7) — documented above as a
  discrepancy between the constant and the hardcoded literals currently in effect,
  rather than assumed to be wired through.
- The exact contents/format of `Core/data/proactive_state.json` and
  `Core/data/memory/*.jsonl` at runtime were not inspected (out of scope per the task's
  instructions not to read `Core/data/` contents) — only the write/read code paths that
  produce and consume them.
