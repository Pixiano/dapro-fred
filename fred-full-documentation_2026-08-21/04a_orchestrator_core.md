# FRED Orchestrator Core — Tool-Calling / Routing Machinery

Scope: the central dispatch/routing engine that turns a text turn into a reply. Covers `Core/orchestrator/orchestrator.py`, `dispatcher.py`, `intent.py`, `tool_router.py`, `vault_router.py`, `vault_intent.py`, `canned_replies.py`, `tool_call_log.py`, `scheduler.py`, `personality/system_prompt.py`, `state/conversation_state.py`, `state/lockdown_state.py`, `state/lockdown_log.py`, and the sensitive-content gate in `utils/sensitive.py`. Presence/sleep-mode and proactive checks are out of scope (other docs cover them); tool *implementations* (what `system_tools.py`, `machine_tools.py`, etc. actually do) are out of scope too — this document is about how a turn gets routed to them and back.

All file paths below are relative to `Core/` unless given in full.

---

## 1. The big picture: five layers between voice/text input and a reply

FRED's orchestrator (`orchestrator/orchestrator.py`, class `FREDOrchestrator`) exists to solve one problem: small local LLMs (4B-8B class models, see the LLM tier docs) are unreliable at picking the right tool out of a big menu, and even more unreliable at deciding whether a turn needs a tool at all. Every layer in this file exists to keep the number of *decisions* asked of the model as small as possible, and to make deterministic code answer the ones that don't need a model at all.

In order, a text turn passes through:

1. **Dispatcher** (`dispatcher.py`) — pure regex, zero LLM calls. Handles unambiguous commands ("what time is it", "mute", "open Spotify").
2. **Canned replies** (`canned_replies.py`) — pure string-match, zero LLM calls. Handles "thanks", "hello", "bye" etc.
3. **Intent classification** (`intent.py`) — decides CHAT vs TOOLS, and if TOOLS, which category subset of tools to expose. Cue-word regex first, semantic embedding rescue second, LLM classifier as last resort.
4. **Tool-calling loop** (`orchestrator._generate_with_tools`) — the LLM actually generates, requests tool calls, they execute, results feed back, repeat up to `MAX_TOOL_ROUNDS = 4`.
5. **Confirmation gate** (`orchestrator._run_or_confirm` / `_request_confirmation` / `_handle_pending_confirmation`) — destructive tools never execute inside the loop; they halt the whole batch and wait for an explicit yes/no on the *next* turn.

Layers 1-2 are the fast, zero-LLM paths. Layer 3 is the thing that makes layer 4 safe to trust with a small model. Layer 5 is orthogonal to all of them — it can fire from the dispatcher path (layer 1) or the tool loop (layer 4).

---

## 2. Entry points: `process()` and `process_stream()`

Both live on `FREDOrchestrator` in `orchestrator.py`.

### `process(user_input: str) -> str`

```python
def process(self, user_input: str) -> str:
    self.state.add_message("user", user_input)
    self._turn_utterance = user_input
    self.last_turn_id = tool_call_log.new_turn_id()

    canned = None if self.pending_action else canned_replies.match(user_input)

    if self.pending_action:
        assistant_reply = self._handle_pending_confirmation(user_input)
    elif canned:
        assistant_reply = canned
    else:
        dispatch = self.dispatcher.match(user_input)
        if dispatch:
            assistant_reply = self._run_or_confirm(dispatch["tool"], dispatch["arguments"])
        else:
            assistant_reply = self._process_with_llm(user_input)

    self.state.add_message("assistant", assistant_reply)
    self.memory.store("user", user_input)
    self.memory.store("assistant", assistant_reply)
    return assistant_reply
```

Routing priority, every turn:

1. If `self.pending_action` is set (a destructive tool is awaiting yes/no), **this turn is treated as the answer, not a new request** — everything else is skipped. `canned_replies.match` is deliberately not even called in this branch (a "no" that also happens to be a canned trigger must still be parsed as the confirmation answer).
2. Otherwise, try `canned_replies.match()` — instant, whole-utterance match, no model.
3. Otherwise, try `self.dispatcher.match()` — regex, no model. A hit routes through `_run_or_confirm`, which itself checks for destructiveness.
4. Otherwise, fall through to `_process_with_llm()` — the full memory+vault retrieval+LLM+tool-loop pipeline.

### `process_stream(user_input: str)` — generator

Same routing tree, but streams text as it's produced **only on the plain-conversation path**. Everything else (pending confirmation, dispatcher hit, or a turn that needs tools) must complete before any text is known, so those paths `yield` exactly once with the whole reply. The reasoning: a tool result can't be narrated before the tool has run, so streaming a tool-eligible turn is meaningless — `process_stream` calls `self._classify_turn()` up front (bypassing the tool loop's own classification) purely to decide whether to take the non-streaming `_process_with_llm()` shortcut or the streaming `llm.generate_stream()` path. Memory/state writes happen identically either way, via a local `finish(reply)` closure, so conversation history never differs based on which path a given turn took.

### Per-turn scratch state

`FREDOrchestrator.__init__` sets several instance (and class-level-default) attributes that exist purely to carry state *within* one call to `process()`／`process_stream()` — the orchestrator processes one turn at a time (the pill UI serialises calls via its own lock), so these are safe without threading utterance/turn-id through every function signature:

- `self._turn_utterance` / `self.last_turn_id` — set at the top of the turn, read by the tool-execution and logging paths so a tool call can be tied back to what was actually said.
- `self._turn_local_only` — latched by `_build_messages()` when vault retrieval pulls sensitive content (see §11), read by every LLM call this turn to force the local-only model tier.
- `self._last_tools_offered` / `self._last_routing_reason` — set by `_generate_with_tools`, read by `_execute_tool_call` so the tool-call log records what the model was *offered*, not just what it picked.
- `self.pending_action` / `self.pending_chain` — the confirmation-gate and end-of-day-sequence state (§6, §7).
- `self._classified_turn`, `self._carry_tools`, `self._carry_left` — the "carry-forward" memoisation for `_classify_turn` (§5.4).

These are declared as **class-level defaults** as well as set in `__init__`, specifically so `tests/test_compound_tool_calls.py` can build a bare orchestrator via `__new__` (skipping `__init__` entirely, so no LLM/scheduler boots) and still exercise the tool loop.

---

## 3. Layer 1 — the Dispatcher (`orchestrator/dispatcher.py`)

Called FRED's "brainstem" in its own docstring. A list of `(compiled regex, handler)` pairs tried in order; the **first** matching pattern whose handler returns non-`None` wins. Order matters — more specific patterns are listed first specifically to avoid a general pattern swallowing a specific one.

`Dispatcher.match(user_input)` returns `{"tool": name, "arguments": {...}}` or `None`. Crucially, **a handler may itself decline** a match it's regex-eligible for by returning `None`, and matching continues to the remaining rules — this is how `_route_web_search` can back out of "search it on the web" (a pronoun with no antecedent the dispatcher can resolve) and let it fall through to the LLM path with full conversation history.

Roughly 30 rules are registered, covering: repeat-last, "open X" (three sub-cases: pronoun follow-up / vault file / generic app-or-website), get-time, calculate (both "what is X" and bare "12 times 8" forms), create file/folder, weather, web search, mute/unmute, lockdown engage/disengage, volume/brightness (absolute and relative), screenshot, reminders/timers (four phrasings), file watch, list/cancel scheduled, end-of-day, kill-process, close-window, restart-fred.

Notable design details, each backed by a documented real failure in the code comments:

- **`_route_open_website` vs `_route_open_vault_file` vs file-suffix detection.** A bare `<word>.<word>` pattern is ambiguous between a website and a local filename. `_FILE_SUFFIXES` is a hardcoded set of ~40 extensions (`pdf`, `docx`, `mp3`, `py`, `exe`, ...) checked before defaulting to `https://`. This exists because "Open dossier.pdf" (right after FRED had found `dossier.pdf` on the Desktop) opened `https://dossier.pdf` in a browser instead of the actual file — confirmed live 2026-08-02.
- **Vault-file resolution sits between the single-token website rule and the generic `launch_application` catch-all**, and only fires if `tools.vault_files.resolve_vault_file(target)` finds a real match (declining, i.e. returning `None`, otherwise) — because "open up active priorities for me" doesn't match the strict single-token website regex (it has spaces/filler) and previously fell through to `launch_application`, which tried to launch an app literally named `"up active priorities for me."`
- **`_route_kill_process` / `_route_close_window` are deterministic on purpose.** These sit ahead of the LLM tool loop specifically so the confirmation gate *always* fires for them — the alternative (trusting a small model to correctly call `kill_process`/`close_window`, or worse, just *claim* it did in plain text) is exactly the failure class `_unsupported_claim` in the orchestrator exists to catch after the fact. Routing them here removes the need for that safety net in the common case.
- **`_route_create_folder` / `_route_create_text_file` decline (`_decline_if_complex`) if the target contains complexity cues** (`write`, `contains`, `saying`, `today's date`, "and then", >8 words). Confirmed bug 2026-08-12: a long dictated sentence describing a file's content, date, and location all got captured verbatim as one garbage filename by the greedy regex. Declining sends it to the LLM path instead, which can actually resolve a date and fill a `content` argument.
- **`_route_web_search` declines on a leading pronoun** (`it`, `that`, `this`, ...) — "Search it on the web. It has been released." dispatched with `query="it on the web..."` before this guard existed, because the dispatcher runs before any conversation history is in view. It also declines on **local-search cues** (`desktop`, `folder`, `vault`, `files`, ...) — "Search my desktop for dossier.pdf" was misrouted to `web_search` and read back irrelevant results; those phrasings need `search_files`/`find_file_smart`, which only live in the LLM tool path.
- **`_route_launch_application` declines on a compound connector** (`and`/`then`) — "open LM studio and go to cerebras.com" must not be passed whole as one literal app name.
- End-of-day's regex is listed **before** the kill-process rule, because `kill|terminate|end` would otherwise swallow "end of day" as "kill a process called 'of day'".

---

## 4. Layer 2 — Canned replies (`orchestrator/canned_replies.py`)

Ten fixed categories (`thanks`, `greeting`, `farewell`, `acknowledgment`, `apology`, `compliment`, `presence_check`, `cancel`, `confirm_result`, `checkin`), each a tuple of trigger phrases and a pool of 4-8 interchangeable reply sentences. `match(user_input)` normalises the input (via `_normalize`, which reuses `intent.normalise()`'s leading-vocative strip, then strips punctuation and any remaining "fred") and does an **exact** lookup against a flat dict built once at import time (`_LOOKUP`). It is deliberately **whole-utterance matching, not cue matching** — "Thanks, but can you also open Chrome" must not get canned-replied, since there's a real request riding along, and this module has no way to detect that a cue only partially covers the turn (unlike `intent.py`'s deliberately over-inclusive per-word cues).

`is_canned(user_input)` is a cheap boolean pre-check the UI layer uses to decide whether to play a filler phrase before the reply — since a canned reply is already instant, playing a ~1s filler in front of it would only add delay.

`process()` checks `canned_replies.match()` **only when `self.pending_action` is falsy** — a confirmation "yes"/"no" must never be intercepted by the canned-reply table.

---

## 5. Layer 3 — Intent classification (`orchestrator/intent.py`)

### 5.1 The documented incident that motivated this whole file

Quoting the file's own header comment: with ~40 tools passed on every turn and `tool_choice="auto"`, the model has to pick one option out of forty — and nothing in that list means "just reply", so a greeting competes against forty concrete actions and loses. Confirmed: **"Hello Fred, how are you doing?" selected `open_website` and opened google.com.**

Two layers fix this:

- **CHAT vs TOOLS** — conversation never sees tool definitions at all, so a misfire there is structurally impossible, not merely unlikely.
- **CATEGORY SUBSET** — an action turn sees only the matched category's tools, typically 2-6 instead of 40. Choosing among `set_volume`/`get_volume`/`mute`/`media_control` is a task a 4B model does reliably; choosing among forty is not.

### 5.2 `classify(text, llm=None, router=None) -> (needs_tools, tool_names, reason)`

The `reason` string is returned (not just logged) specifically so a misroute is debuggable after the fact — the caller prints it every turn (`print(f"[intent] tools ({reason})")` / `chat ({reason})`).

Decision order inside `classify()`:

1. **Empty text** → `(False, [], "empty")`.
2. **`match_categories(text)`** — every `CATEGORY_CUES` entry whose regex (word-boundary for alphanumeric cues, literal substring for symbol cues like `.com`) matches the normalised text. Categories are **unioned**, not exclusive — "turn the volume down and open Spotify" matches both `audio` and `apps`.
3. **Social short-circuit** — if `looks_social(text)` (matches the `_SOCIAL` regex: greetings, small talk, bare yes/no, "how are you", etc.) **and** `"selfdoc"` is not among the matched categories, return `(False, [], "social/meta phrasing")` immediately. The `selfdoc` exception exists because `_SOCIAL` itself matches "what can you do" / "tell me about yourself" — without carving that out, `ask_about_myself` (the one tool built to answer exactly those questions) could never be offered on the phrasing its own backlog item quotes as the example.
4. **If categories matched**, flatten them to a tool-name list via `tools_for_categories()`. If a semantic `router` (a `SemanticToolRouter`, see §6.1) is available and the turn is **not** compound (`intent.looks_compound`), the cue-derived list is *narrowed* by embedding rank down to at most 6 (never below 2) candidates, and a semantic *rescue* can add one extra tool the cues missed entirely if the embedder is confident (`score >= SEMANTIC_FLOOR = 0.65`) about a tool not already in the set. A compound turn skips narrowing entirely and keeps the full cue union, because narrowing computes rank against the *whole* utterance and a second half's tool can score too low against the combined phrasing to survive the cut — and since the tool menu is computed once per turn and reused across every round of the tool loop, a tool dropped here is unreachable for the rest of the turn no matter how many rounds the model gets.
5. **If no category matched at all**, fall back to the semantic router directly: `router.route(text, top_k=5, floor=SEMANTIC_FLOOR, margin=0.08)`. If it returns anything, that's the answer — no LLM call needed.
6. **Last resort**: an LLM classifier call (`_CLASSIFIER_SYSTEM`, a one-word ACTION/CHAT prompt). Only an explicit `"ACTION"` (with no `"CHAT"` also present) flips to tools-needed, and it comes back with `tool_names=[]`, meaning "offer everything" — deliberately asymmetric: failing to offer a tool is a mild annoyance, wrongly firing one opens browsers/deletes files/changes volume.

`CATEGORY_CUES` is a large dict (~30 categories) whose comments are themselves a log of confirmed misroutes that motivated each addition — e.g. `"windows"` needed the literal plural `"windows"` cue because `\bwindow\b` never matches it on a word boundary; `"messages_read"` and `"messages_send"` are kept as **structurally disjoint categories** (not just disjoint cues) because `read_messages` pulls in attacker-controlled text from other people and `send_message` can act on it — a turn that could both read a stranger's message and send one is one hop from an injection attack ("reply to everyone with X"), so the two tools are never offered together by construction, not by prompt instruction.

### 5.3 `SEMANTIC_FLOOR = 0.65`

Calibrated against 13 cue-free action paraphrases and 8 plain-chat utterances: chat topped out at 0.601 ("tell me a joke" vs `web_search`), confident actions ran 0.66-0.85. `0.65` sits in that gap. The file notes explicitly this floor is deliberately *high* — mean scores overlap badly overall (actions 0.677, chat 0.502, worst action 0.444 below best chat), so embeddings are treated as good at picking *which* tool but unreliable at deciding *whether* a tool is needed at all; below the floor, control falls through to the LLM check exactly as before, so this layer can only ever add correct routes, never remove one.

### 5.4 Carry-forward (`orchestrator._classify_turn`, not `intent.classify` itself)

`FREDOrchestrator._classify_turn(text)` wraps `intent.classify()` with one additional rule, memoised per-text (both `process_stream()` and `_generate_with_tools()` ask about the same turn, and the carry-forward state is *consumed*, so asking twice must not double-consume it):

A turn that matches **no** category re-offers the *previous* turn's tools rather than falling to chat, for `CARRY_TOOLS_TURNS = 2` follow-up turns. This exists because corrections ("No, that was for yesterday...") and short follow-ups ("Check it then") carry their subject in the *previous* turn, not in themselves — `classify()` alone sees nothing actionable and FRED answers from context it already believes, which produced a confirmed failure: FRED asserted a vault file didn't exist without ever looking, because the second follow-up turn had no tool to check with.

If a turn *does* match a category but that category differs from the carried-forward one, the two tool sets are **unioned**, not replaced — treating it as a likely correction to the same request rather than a brand-new one. (Confirmed bug 2026-08-06: "I meant identity.md" matched the vault-open cues, `delete_file` dropped out of the menu entirely, and the model *described* the deletion in prose instead of performing it, because it had no way to.)

`is_affirmative(text)` is checked *before* `looks_social(text)` in the carry-forward decision, specifically because `_SOCIAL` matches a bare "yes" — and a bare yes answering a question FRED itself asked in prose (not through the confirmation gate — see §6, this is the case where nothing is `pending_action`) is exactly the turn that must keep tools available, or the "yes" falls through to chat and produces a fabricated confirmation.

`_prime_carry(tool_names)` is a separate entry point called by `proactive_checks.py` right after it *speaks* a proactive question (e.g. "you had Geography due today, did you finish it?") — primed before the question is even answered, because an unrecorded "yeah I did it" is never a false claim FRED itself makes (so `_unsupported_claim`, below, can never catch it after the fact); it's Vatsal's own true answer that would otherwise just never get saved.

---

## 6. The confirmation gate for destructive tools

### 6.1 The destructive tool list

Destructiveness is a **per-tool boolean flag set at registration time** in `_register_tools()` (`self.tools.register(..., destructive=True)`), not a hardcoded name list checked elsewhere — `ToolRegistry.is_destructive(name)` (in `tools/registry.py`) is the single source of truth queried by both call paths below. Grepping the registrations, the destructive set is:

- `close_window`
- `kill_process`
- `call_phone`
- `send_message`
- `set_contact_tier`
- `find_otp`
- `delete_file`
- `power_action`
- `restart_fred`
- `delete_agenda_item`

(This matches the task brief's list plus `find_otp`, which is destructive because it reads live SMS content and is gated behind "only after explicitly asking the user first and getting a yes".)

### 6.2 How the halt actually works

There are exactly two places a tool call can originate — the dispatcher fast path and the LLM tool-calling loop — and both check destructiveness before executing anything:

**Dispatcher path** — `_run_or_confirm(tool_name, arguments)`:
```python
def _run_or_confirm(self, tool_name, arguments):
    if self.tools.is_destructive(tool_name):
        return self._request_confirmation(tool_name, arguments)
    self._announce_tool(tool_name)
    ...  # execute immediately
```

**Tool-loop path** — inside `_generate_with_tools`'s per-round loop, *before any call in the batch executes*:
```python
for call in tool_calls:
    function = call.get("function", {})
    name = function.get("name")
    if self.tools.is_destructive(name):
        arguments = json.loads(function.get("arguments") or "{}")
        return self._request_confirmation(name, arguments)
```

This is "FRED halts the whole batch when it sees one": the loop iterates the tool calls the model requested *this round* looking for a destructive one, and the moment it finds one it returns immediately from `_generate_with_tools` — **any other (safe) tool call in that same batch is simply never executed**, not queued, not partially run. The comment in the code is explicit about why: "keep the turn simple to reason about." The confirmed action resumes on the *next* `process()` call, via `_handle_pending_confirmation`.

### 6.3 `_request_confirmation(tool_name, arguments) -> str`

Sets `self.pending_action = {"tool": tool_name, "arguments": arguments}` and returns a spoken confirmation question. Two tools get special-cased resolution *before* the question is asked, so the yes/no is answering something concrete rather than a raw argument echo:

- **`kill_process`** resolves the substring match (`machine_tools.matching_processes(target)`) up front and names every actual process+PID that would die — because `kill_process` substring-matches by design ("code" matches every process with "code" anywhere in its name), and a confirmation that only echoes `kill_process (name_or_pid=code)` gives no way to notice that before it happens. If nothing matches, `pending_action` is cleared immediately and the turn ends with "nothing to kill" — no confirmation loop over a no-op.
- **`call_phone`** resolves a contact name to an actual number (`phone_tools.resolve_target`) up front — so "Calling Mom — confirm?" names the actual resolved target, not a lookup the user can't see the result of, and a name that fails to resolve dies at the prompt instead of after a "yes".

Every other destructive tool gets the generic form: `"This can't be undone — about to run 'TOOL' (k=v, k=v). Confirm? (yes/no)"`.

### 6.4 `_handle_pending_confirmation(user_input) -> str`

Called from `process()` whenever `self.pending_action` is set. Clears `pending_action` immediately (so a crash mid-execution doesn't leave it stuck armed), then:

- If a `pending_chain` exists (end-of-day sequence, §6.5) and the answer is one of `_ABORT_WORDS` (`stop`, `cancel`, `abort`, "never mind", "forget it", `quit`), the *entire remaining chain* is cleared — not just this one step.
- Otherwise, the yes/no is parsed via **`intent.is_affirmative(user_input)`**, not a naive string-equality set. This is a deliberate, documented fix: the old code used `user_input.strip().lower() in {"yes", ...}`, an exact match a spoken confirmation almost never survives (Whisper punctuates "Yes." → "yes." with a trailing period that broke the set-membership check) — confirmed live 2026-08-15 on a `call_phone` confirmation, where FRED answered a clear "Yes." with "Cancelled — didn't run it." A bare typed `"y"` is special-cased alongside `is_affirmative` too (nobody says it aloud, but it's natural typed into the HUD console).
- On affirmative: the tool actually executes (`self.tools.execute(action["tool"], **action["arguments"])`), or, for the special non-registered `"_end_of_day_sequence"` pseudo-tool, `_run_end_of_day(**arguments)` is called instead (see §6.5). Both the log entry and the reply text include the result of `_arm_next_step()` — the next queued confirmation in `pending_chain`, if any.
- On non-affirmative: nothing runs; the reply is `"Left that one open."` if a chain is still pending, else `"Cancelled — didn't run it."`

Every branch — confirmed-executed, confirmed-cancelled — logs through `tool_call_log.log_tool_call(..., path="confirmed_destructive")` and `event_log.log(...)`.

### 6.5 The end-of-day sequence (`end_of_day`, `_run_end_of_day`, `pending_chain`)

`end_of_day()` (reachable via dispatcher regex on phrases like "wind down", "call it a day", "goodnight") gathers every open window title, generates today's session summary, and asks **one single upfront confirmation** for the whole sequence — not one confirmation per window. This was a deliberate rewrite (2026-08-18) away from a previous design that asked yes/no once *per window* via `pending_chain` walked one step per turn: that design was fragile because one missed or misheard answer stalled the rest of the wind-down.

The current design: `pending_action` is set to the pseudo-tool `"_end_of_day_sequence"` with `{"titles": [...]}`. On confirmation, `_run_end_of_day(titles)` schedules a *background* sequence via APScheduler jobs (`_close_window_and_announce` per window, `_END_OF_DAY_CLOSE_INTERVAL = 3` seconds apart, then `_shutdown_and_announce` calling `power_action(action="shutdown")`) — each step announces itself through `notifier.notify()` as it fires, and `power_action`'s own 5-second cancellable delay remains the actual last word before the machine shuts down, so the destructive step still keeps its own independent guard even though the wind-down itself is no longer gated turn-by-turn.

`pending_chain` (a list structure, still present in the code) is now used only as generic machinery for "queue up N confirmations, walk them one per turn" — it's not driving end-of-day's per-window confirmations any more, but the same mechanism remains available for anything that needs it, since the yes/no parsing, tool logging, and cancelled-path handling all already live in `_handle_pending_confirmation`.

`_close_window_and_announce` / `_shutdown_and_announce` are module-level standalone functions (not bound methods) specifically because they run on APScheduler's own background thread, not the turn thread.

---

## 7. The tool-calling loop (`orchestrator._generate_with_tools`)

Entered from `_process_with_llm()` after `_build_messages()` has assembled the prompt (§11). This is the core "ask the LLM, run what it asks for, ask again with results" loop.

### 7.1 Chat bypass

```python
last_user = <most recent user message in `messages`>
needs_tools, tool_names, reason = self._classify_turn(last_user)
```

If `needs_tools` is `False`, the model generates a plain reply with **zero tool definitions in the prompt at all** — this is the mechanism that makes the intent-gate incident (§5.1) impossible to repeat: the model is never shown a menu to misfire against.

That chat reply is then checked by `_claims_completed_action(reply)` — because a turn that ran no tool by construction can never truthfully claim to have done something, and confirmed live 2026-08-06: "Deleted personal/identity.md, sir." was said in answer to a bare "Yes", with the file still on disk, because the model read its own earlier "Shall I delete...?" from context and reported the deletion as done. If a false completion claim is detected, the turn is **rerun once** with the *full* (unfiltered, ~40-tool) menu (`widened = True`) — this is the only branch that ever shows every tool definition at once, deliberately confined to a path that has *already* produced a falsehood, so the small-model misfire problem stays contained.

### 7.2 Ambiguity chip

If `needs_tools`, before dispatching, `intent.close_candidates(last_user, tool_names, self._tool_router())` checks whether the top two ranked tools are within `CLOSE_CANDIDATE_MARGIN = 0.03` of each other. If so, `_announce_ambiguity(top, alt)` fires the pill's disambiguation UI hook (`on_ambiguous_choice`) — purely informational, FRED still picks and acts on the top candidate; this just makes a near-tie visible/correctable rather than silent.

### 7.3 The round loop, `MAX_TOOL_ROUNDS = 4`

`tool_definitions = self.tools.get_tool_definitions(only=tool_names)` — only the classified category's tools are ever sent as JSON schema to the model.

Each round:

1. Call `llm.generate_with_tools(messages, tools=tool_definitions, local_only=self._turn_local_only)`.
2. If the structured `tool_calls` field is empty, try `_parse_text_tool_calls(content)` — a regex-based fallback for models (Nemotron/Hermes-style templates, or Gemma 4's `<|tool_call>call:NAME{args}<tool_call|>` syntax) that emit tool calls as plain text instead of using llama.cpp's structured field. Only emits a parsed call if the name matches a really-registered tool.
3. If still no calls: this is either a genuine final answer, leaked tool-call syntax (`_looks_like_leaked_tool_syntax` — regenerate once with no tools if this is round 0), or an unsupported completion claim (`_unsupported_claim`, §7.4 below — widen the menu once and retry).
4. If there **are** tool calls: check every one for destructiveness first (§6.2) — a hit halts the whole batch immediately. Otherwise, run each tool via `_execute_tool_call()`, append `{"role": "tool", ...}` result messages, and either:
   - Return immediately if every tool called this round is in `SELF_NARRATING_TOOLS` **and** the turn doesn't look compound (`intent.looks_compound`) — skipping the second LLM pass entirely because the tool's own return string is already a complete spoken sentence.
   - Otherwise loop again, asking the model with the results now in context — on a compound turn, a synthetic user message restates the original request ("That was part of this request: \"...\". If any part has not been done yet, call the tool for it now.") to stop the model from summarizing what it just did and silently dropping the second half.

If the round budget is exhausted with the model still requesting tools, the loop bails out and answers with whatever's actually been accumulated (`all_results`) rather than looping forever.

### 7.4 The fabrication guards — `_unsupported_claim` and `_claims_completed_action`

`_claims_completed_action(content)` — true when a reply's text matches `_ACTION_DONE` (a regex of past-tense action verbs: `deleted`, `created`, `scheduled`, `engaged`, `set`, etc — deliberately excludes "checked"/"looked", since honestly reporting having consulted context isn't a false claim), is non-empty, doesn't end in `?` (a question, e.g. "Shall I delete it, sir?", is not a claim), and isn't negated (`not`/`never`/`won't`/`can't`/... — an honest refusal isn't a claim either).

`_unsupported_claim(content, all_results)` is the *stricter* superset check, added after a second confirmed failure (2026-08-17, 14:16-14:17): asked to log the day, FRED called `create_folder` on a folder that had existed since 2026-08-03, then said "File created: daily/2026-08/2026-08-17.md with the session log" — no write tool ran, the file's mtime never moved. The old guard (`not all_results`) was fooled because *some unrelated tool* had run. The new check requires: (a) `_claims_completed_action` is true, (b) if nothing ran at all, that's automatically an unsupported claim, (c) if something ran, the reply must name a concrete file artifact (`_NAMED_ARTIFACT` regex — requires a real extension like `.md`/`.txt`/`.py`; bare words are too common in ordinary prose) that does **not** appear (by basename, matching a tool's absolute path against the vault-relative name in the reply) anywhere in the actual tool results. Only WRITE-shaped claim verbs qualify (`created|wrote|written|saved|appended|updated|deleted|removed|moved|renamed`) — a reply quoting `ask_about_myself`'s documentation excerpts (which are themselves full of words like "added"/"set") must not be mistaken for a claim to have written something.

If widening the tool menu once still produces only talk, the loop gives up and says so honestly: `"I haven't actually done that, sir — nothing ran. Say it again and name the file, and I'll run it."`

### 7.5 `EXACT_READBACK_TOOLS` vs `SELF_NARRATING_TOOLS`

Two separate, deliberately non-overlapping sets governing when the tool loop skips its own follow-up LLM phrasing pass in favor of the tool's raw return string:

- **`SELF_NARRATING_TOOLS`** — e.g. `get_current_time`, `schedule_reminder`, `add_task`, `whats_on_screen`. Skips the follow-up pass on a **simple** (non-compound) turn only. `whats_on_screen` is explicitly in this set because its own staleness hedge ("...which is probably stale: <description>") was getting silently dropped by a rephrasing pass that presented a 6-hour-old cached description as confidently current (caught 2026-08-09).
- **`EXACT_READBACK_TOOLS`** — a strictly narrower set (`add_agenda_item`, `list_agenda_items`, `update_agenda_item`, `list_scheduled`, `cancel_scheduled`). Stricter: the raw tool result is preferred over the model's own words **even across a compound turn's extra rounds** (`exact_readback_only`, AND-narrowed across every round — stays `True` only if *every* tool called the whole turn is in this set). Built for "3 questions in Geography and 1 in physics, due in 3 days": once both `add_agenda_item` calls have happened across two rounds, the turn would otherwise end with the model paraphrasing its own two tool results in one sentence — usually fine, except "usually fine" on a due **date** is the exact failure this whole feature exists to prevent. The comment is explicit that this is deliberately a separate set, not a flag on `SELF_NARRATING_TOOLS`, because `schedule_reminder`+`list_scheduled` together are ALSO all-self-narrating on a compound turn but a pinned regression test (`test_compound_tool_calls.py`) expects the model's own natural-language synthesis there ("Set for 6pm. You already had one other reminder.") — raw concatenation would make that case worse.

### 7.6 `SENSITIVE_TOOLS` — the tool-side half of the local-only gate

```python
SENSITIVE_TOOLS = {"workout_split", "todays_workout", "schedule_workouts"}
```

`_execute_tool_call()` checks `if SENSITIVE_LOCAL_ONLY and name in SENSITIVE_TOOLS: self._turn_local_only = True` **before** the tool runs. This is necessary because a tool that reads `personal/` puts sensitive content straight into the tool-result message, which the loop then feeds back to the LLM for phrasing — a path the vault-retrieval-side sensitivity check in `_build_messages` (§11) does not cover at all, since no vault retrieval is involved. Latching here, before execution, closes that gap for the follow-up round.

---

## 8. Semantic tool router (`orchestrator/tool_router.py`)

`SemanticToolRouter`, built lazily on first tool-eligible turn via `FREDOrchestrator._tool_router()` — lazy specifically because building it embeds all ~40 tool descriptions (measured ~4.8s), and there's no reason to pay that cost at startup if a session turns out to be all conversation. It reuses the memory manager's embedder (`self.memory._generate_embedding`, Qwen3-Embedding, already resident and not touched by the idle unloader) rather than loading a second model copy.

`build()`: for each registered tool, concatenates its name, registered description, and any hand-written colloquial examples from `TOOL_EXAMPLES` (a dict of ~3 example phrasings per tool, covering only a subset of tools — the ones judged to need paraphrase coverage the description alone doesn't provide) into one string, embeds it once. One vector per tool (not one per example) to keep this at 40 embeds rather than ~160.

`rank(text)` embeds the query (`is_query=True` — the asymmetric instruction convention shared with `memory_manager.py` and `vault_router.py`, where documents and queries are embedded with different instruction prefixes) and returns every tool sorted by cosine similarity, best first.

`route(text, top_k=5, floor=0.0, margin=0.06)` returns `(names, best_score)`: the top match plus anything within `margin` of it, capped at `top_k`. Below `floor`, returns `([], best)` — the signal used by `intent.classify()` to conclude "this is conversation, not action" when no category cue matched at all.

Fails open everywhere: any exception during build or embed degrades to "no opinion" and the caller (`intent.py`) falls back to pure cue matching — this is explicitly framed as "an accuracy improvement, never a dependency."

---

## 9. Vault semantic retrieval (`orchestrator/vault_router.py`)

`VaultRouter`, built lazily via `FREDOrchestrator._vault_router()` (same reuse-the-embedder pattern as the tool router — "zero extra VRAM"). Retrieves nearest chunks from every vault file **except** the three hardcoded-always files (`persona.md`/`profile.md`/`rules.md` — see §12) plus `active-priorities.md`, which is *also* hardcoded (see `VAULT_HARDCODED_FILES` in §12) — so 60 of ~63 `.md`/`.pdf` files at time of writing are reached only through this retrieval path.

### 9.1 Chunking (`_chunk_file`, `_chunk_pdf`, `_chunk_plain_text`)

- **Markdown** (`.md`, the common case): `_chunk_file` strips frontmatter, extracts the H1 title, then calls `utils.vault_md.split_sections(body)` to split by `##` headings. Chunked by **section**, not whole-file, because a vault file like `profile.md` or `machine.md` covers several unrelated ideas ("VRAM budget" and "Paths" have nothing to do with each other) — whole-file embedding would blur them into one vector that matches everything a little and nothing well. Section boundaries were checked against real files before committing to `##`/`---` as the convention.
- **PDF**: `_chunk_pdf` chunks **per page** (via `pypdf.PdfReader`), because a PDF has no `##` headings and the embedder runs at `n_ctx=4096` — a long document without page boundaries would be silently truncated at embed time. Returns `[]` and warns (never raises) on a malformed or image-only PDF, or if `pypdf` isn't installed, since the whole vault index is built lazily on the first turn of a session and must not take the session down with it.
- **Plain text** (`.txt`, no `.md` headings at all): `_chunk_plain_text` groups blank-line-separated paragraph blocks up to `max_chars=2000`, never splitting mid-block. Added 2026-08-15 specifically for two self-documentation `.txt` files with zero `#`/`##` lines that were otherwise embedding as one giant 300+ line whole-file chunk.

### 9.2 Content-hash caching (`chunks.json`)

Cached to `VAULT_INDEX_DIR / "chunks.json"` (which lives at `<VAULT_DIR>/vectors/`, travelling with the vault itself rather than the FRED repo, so a rebuilt-from-scratch clone of `Project_FRED` doesn't force a full re-embed). Keyed by a SHA-256 content hash **per file** (`_file_hash`), not per chunk — a file whose hash is unchanged reuses its cached chunk vectors wholesale; only changed files get re-chunked and re-embedded. Comment states a cold build measured at 19.7s, a fully-cached build at 0.04s — this cost is paid once per changed file, not once per FRED launch. Files dropped from the vault (present in the cache but no longer on disk) are pruned from the saved cache too.

### 9.3 Centering — the technique in detail

This is the most technically load-bearing part of `vault_router.py` and the file's largest comment block (documented, not guessed at).

**The problem.** Embedding spaces develop "hubs": a handful of vectors sit near the centroid of the whole corpus distribution and therefore score highly against *almost any* query by raw cosine similarity, carrying no actual discriminative information. This index had a textbook case: over 16 calibration queries (8 genuinely vault-relevant, 8 pure chat), the chunk `projects/fred.md — What it does` was the #1 hit for **9 of them** — including three cases where it was simply wrong, outranking `reference/machine.md` for "what are my machine specs" and `active-priorities.md` for "what are my current priorities".

**Why a threshold can't fix it.** This was checked before reaching for centering. Top-1 score, gap-to-6th, and gap-to-mean *all* overlap between relevant and chat queries: relevant top-1 scores ran 0.578-0.712, chat queries ran 0.388-0.649. Any floor that rejects "tell me a fun fact" also rejects a genuine "who is [person]" query. The task-description's cited numbers (0.533-0.736 relevant vs 0.340-0.661 chat) describe this same overlapping-distribution finding. **The problem isn't where the cutoff sits — it's that one vector is close to everything**, so no single scalar threshold on the raw similarity distribution can separate the two populations.

**The fix.** Subtract the corpus mean vector from every chunk vector at build time, and subtract that *same* mean from the query vector at retrieval time:

```python
def _mean_vector(vectors):
    dim = len(vectors[0])
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(dim)]

def _center(entries):
    mean = _mean_vector([v for _l, _t, v in entries])
    return [(label, text, [x - m for x, m in zip(vec, mean)]) for label, text, vec in entries]
```

This removes the shared component that made every vector look similar to every query, at essentially zero cost (one subtraction per chunk at build, one per query at retrieval). Measured on the same 16 calibration queries: relevant recall@6 stayed 7/7 (unchanged — centering didn't break what already worked), but `"machine specs"` top-1 correctly moved from `fred.md` to `reference/machine.md`, `"priorities"` top-1 moved from `fred.md` to `personal/goals.md — Priority order`, and `fred.md`'s spurious #1 rank on plain chat dropped from 6/6 to 4/6 occurrences.

**Consequence noted explicitly in the code:** centered scores are **not** raw cosine similarities any more — they're lower and *can go negative*, so `VAULT_RETRIEVAL_FLOOR` (currently `-1.0` in `settings.py`, effectively disabled — see §9.4) must be interpreted on that centered scale, not as a conventional 0-1 similarity.

The query is centered the same way at retrieval time using the *stored* corpus mean (`self._mean`, saved alongside `self._entries` at build time), so query and chunks always live in the same shifted space:
```python
if self._mean is not None:
    q_vector = [x - m for x, m in zip(q_vector, self._mean)]
```

### 9.4 Retrieval floor is effectively disabled — unrestricted read access

`VAULT_RETRIEVAL_TOP_K = 6` and `VAULT_RETRIEVAL_FLOOR = -1.0` together mean **every non-blank turn retrieves the top 6 nearest chunks, regardless of actual relevance** — `-1.0` is below the theoretical minimum of a cosine-similarity-derived score, so nothing is ever filtered out by the floor. The file's own header states this is deliberate: "the user asked for unrestricted read access, so the top-K nearest chunks come back on every turn regardless of score." `vault_intent.py`'s `should_check_vault()` cue-gate function still exists and is importable, but `retrieve()` no longer calls it — the module is left intentionally intact so the gate "can be put back... if the added noise ever proves too costly," but as shipped, retrieval has no cue gate and effectively no relevance floor.

This unconditional-6-chunks behavior is also exactly what created the sensitive-content leak this whole system had to be hardened against (see §11) — with a floor of `-1.0`, `personal/` and `people/` chunks reach the prompt on ANY turn where they happen to rank in the top 6, entirely independent of whether the question was actually about them.

### 9.5 A second consumer: `tools/self_docs.py`

`VaultRouter.__init__` accepts optional `files_fn`/`cache_path` parameters specifically because `tools/self_docs.py` (added 2026-08-15, backing `ask_about_myself`) reuses the identical class over a *different* corpus (FRED's own project documentation) rather than duplicating the chunking/caching/centering machinery — "a second retriever class would have been a copy of this one with two constants swapped."

---

## 10. Vault intent cue gate (`orchestrator/vault_intent.py`)

`should_check_vault(text)` is the vault-retrieval equivalent of `intent.py`'s category cue gate — **currently unused by `vault_router.retrieve()`** (see §9.4), left importable for reactivation. Its own header documents the same calibration finding restated in the task brief: "tell me a joke" scored 0.661 against the vault, outscoring the genuine "when are my board exams" match at 0.533 against its own correct chunk — proof that raw cosine similarity alone can't separate "asking about vault content" from "plain chat that happens to use similar words," and a global floor can't fix that because it's the same value applied to both populations. `VAULT_CUES` is a large, deliberately over-inclusive tuple of substrings grouped by vault subdirectory (`jobs/`, `projects/`, `personal/`, `reference/`, `daily/`, `people/`, plus generic recall phrasing like "remember"/"what's my").

---

## 11. Sensitive-content gating: `SENSITIVE_LOCAL_ONLY` and `utils/sensitive.py`

### 11.1 The rule and why it exists

`rules.md` (in the vault) states: *"Never send personal/ or people/ anywhere. No hosted model, no API, no paste, no export, no repo. They hold precise identifying details about a minor, health information, and other people's information."* `utils/sensitive.py`'s header notes that **until this module existed, nothing enforced that at runtime** — FRED's LLM cascade (`llm/llm_client.py`) tries cloud providers (Groq, then Cerebras) *before* any local model on every single turn, so the moment vault retrieval started returning `personal/` chunks, their contents would have been POSTed to a third party. Indexing `personal/` at all and enforcing this gate landed in the same change deliberately — the module's own comment: "one without the other is the bug."

### 11.2 `utils/sensitive.py`

- `SENSITIVE_DIRS = frozenset({"personal", "people"})` — matched case-insensitively against every path component (so `personal/fitness.md` and `FRED/People/sara.md` both match).
- `SENSITIVE_FLAG = "sensitive: true"` — an explicit frontmatter flag honoured wherever it appears, so a sensitive file living *outside* the two directories above is still caught (e.g. `personal/fitness.md` itself carries this flag).
- `is_sensitive_path(path)`, `is_sensitive_text(text)`, and `any_sensitive(chunks)` — the last is the function actually called from the orchestrator; it tolerates several chunk shapes (dict with `source`/`path`/`file` + `text`/`content` keys, plain strings, or objects with `.source`/`.text` attributes) and treats an unrecognised shape as "not sensitive on path, but still scanned for the frontmatter flag" — degrading to a text-only check rather than silently passing.
- Deliberately conservative throughout: anything that even *looks* sensitive returns `True`, because a false positive costs one slower local-model turn while a false negative is unrecoverable — once vault content is sent to a cloud API, it's sent.

### 11.3 `SENSITIVE_LOCAL_ONLY` — currently `False`

`config/settings.py` line 621: `SENSITIVE_LOCAL_ONLY = False`, with a comment referencing that it was deliberately disabled and the enforcement machinery ("`LLMClient.local_only`, `sensitive.py`, `SENSITIVE_TOOLS`") is left "fully wired... flip this back to `True` to re-arm it in one line." (This matches the standing memory note that `personal/` currently goes to cloud APIs by explicit user choice, and one flag re-arms local-only enforcement.)

### 11.4 The per-turn pin: `self._turn_local_only`

Two independent code paths can set `self._turn_local_only = True` for the remainder of a turn — both gated by `SENSITIVE_LOCAL_ONLY`, so with the flag currently `False` neither actually fires today, but the mechanism is fully wired:

1. **Retrieval side** — in `_build_messages()` (§12.4), immediately after vault retrieval returns hits:
   ```python
   self._turn_local_only = SENSITIVE_LOCAL_ONLY and sensitive.any_sensitive(
       [{"source": label, "text": text} for label, text, _ in hits]
   )
   ```
   This is documented as closing a confirmed 2026-08-04 leak: with `VAULT_RETRIEVAL_FLOOR = -1.0` forcing six chunks back on *every* turn regardless of relevance, `personal/`/`people/` excerpts were reaching the prompt routinely and being POSTed to Groq — "the exact thing rules.md forbids... and nothing was enforcing it."
2. **Tool-execution side** — `_execute_tool_call()` (§7.6), for any tool in `SENSITIVE_TOOLS` (`workout_split`, `todays_workout`, `schedule_workouts`), set *before* the tool runs so a follow-up round in the same turn can't slip the result to the cloud cascade.

`_turn_local_only` is explicitly reset to `False` at the **top** of `_build_messages()`, never at the bottom — the comment explains this direction matters: leaving it latched across turns would be the "safe" failure direction (an unnecessary extra local-only turn), but leaving it *unset* from a previous turn's true value would silently pin every subsequent turn to local-only forever with no way back. Every LLM call in the orchestrator (`llm.generate`, `llm.generate_with_tools`, `llm.generate_stream`) passes `local_only=self._turn_local_only`.

---

## 12. System prompt assembly

### 12.1 `personality/system_prompt.py` — hardcoded vault files

FRED's identity now lives in the vault, not in this file — this module is described in its own header as "a loader, not a source of truth." `VAULT_HARDCODED_FILES` (defined in `config/settings.py`) currently equals:

```python
VAULT_HARDCODED_FILES = ("persona.md", "profile.md", "rules.md", "active-priorities.md")
```

These four files are read **directly, always, on every `FREDOrchestrator` init** — no vector retrieval, no chance of a bad embedding match dropping identity or a rule on some turn. `persona.md` = who FRED is, `profile.md` = who Vatsal is, `rules.md` = hard behavioral rules, and `active-priorities.md` was added as a fourth hardcoded file (see the retrieval-floor comment history in §9.3 — `active-priorities.md` used to sit at rank 5 in retrieval and never reach the prompt for "what are my priorities" until `VAULT_RETRIEVAL_TOP_K` was raised to 6; it was later also hardcoded outright). Everything *else* in the vault (`jobs/`, `projects/`, `knowledge/`, `daily/`, `reference/`, `personal/`, `people/`) is reached only through `VaultRouter` retrieval (§9) — those files change often or are only relevant to some turns, unlike identity/rules which must never depend on a retrieval match succeeding.

`_load_vault_prompt()` strips frontmatter from each hardcoded file (`utils.vault_md.strip_frontmatter`) and joins them with `\n\n---\n\n`. `SYSTEM_PROMPT` is built **once, at import time** — vault edits to these four files take effect on the next FRED restart, not mid-session (traded deliberately for simplicity, since these files are expected to change rarely).

`_FALLBACK_SYSTEM_PROMPT` is a hardcoded functional fallback (not just an inert placeholder) used if the vault directory is unreachable — this is the literal text that was FRED's *sole* system prompt before the vault-based identity system existed, kept runnable so a machine without the vault mounted still gets a coherent (if generic) FRED persona rather than an empty system prompt.

### 12.2 Lockdown addendum

`LOCKDOWN_SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n---\n\n" + _LOCKDOWN_ADDENDUM` — **appended**, not swapped in, specifically so lockdown mode is "still FRED, just missing most of what it can do" — voice and personality don't shift, only the willingness to act. Two module-level asserts pin this invariant: `SYSTEM_PROMPT in LOCKDOWN_SYSTEM_PROMPT` and `len(LOCKDOWN_SYSTEM_PROMPT) > len(SYSTEM_PROMPT)`.

### 12.3 One system message, not several

`_build_messages()`'s own comment documents a real breaking bug: earlier versions built four separate `{"role": "system", ...}` entries (persona, screen context, vault knowledge, memory). Gemma4's chat template tolerated multiple system messages silently; Qwen3.5's native template *enforces* exactly one leading system message and raises `"System message must be at the beginning"` the instant a second one appears — which surfaced as **every** real reply silently falling through to the generic "cognitive malfunction" fallback string, because `generate()` catches the exception internally rather than propagating it. All sections (`system_sections`, a list of strings) are now joined into one final system message with `"\n\n".join(...)`.

### 12.4 What goes into `_build_messages()`, in order

1. `SYSTEM_PROMPT` (or `LOCKDOWN_SYSTEM_PROMPT` if `lockdown_state.is_locked()`).
2. **Today's date/time**, rebuilt fresh every turn (never cached), explicitly instructing the model to never date a written/logged/scheduled entry from a date it read *in* a vault file — confirmed bug 2026-08-05: asked to log that day's event, the model wrote the wrong (earlier) date into a `people/` file, sourced from whatever date it had just read, and that wrong date is now persisted where it will be believed.
3. **Ambient screen context** — `_screen_context()`, a single Win32 `GetWindowText(GetForegroundWindow())` call plus the local time, deliberately *not* a screenshot or vision-model call (cheap: one syscall, answers most of what "what am I doing" needs). Suppressed for FRED's own pill window or the desktop itself.
4. **Recent-interruption context** (`notifier.last_proactive()`) — if FRED recently spoke unprompted (a fired reminder, a proactive check), this tells the model so a bare "what was that?" / "how long till then?" follow-up has something to resolve against. Deliberately kept *out* of the stored conversation transcript itself — a bracketed system-style prefix there would be both something FRED never actually said and a format the model might copy aloud if it were in history.
5. **Vault knowledge** — `VaultRouter.retrieve(_retrieval_query(user_input, recent_messages))` (§9), with:
   - **Sensitivity latching** (§11.4) computed before formatting.
   - **Per-excerpt provenance tagging** via `utils/confidence.py` (`confidence.classify(text)` → `confidence.name(level)`), not a single turn-level minimum — because a turn-level floor would hedge a fact Vatsal stated outright just because some unrelated 6th chunk (forced into the prompt by `VAULT_RETRIEVAL_FLOOR = -1.0`) happens to be a weak guess.
   - **Table flattening before truncation** — `utils.vault_md.flatten_tables(text)` converts markdown tables to `"row — Column: value"` lines *before* the `VAULT_CHUNK_INJECT_CHARS` truncation, so a value can never be silently read out of the wrong column if a table gets cut mid-row.
   - **The anti-fabrication instruction**, quoted in full in the code as non-optional framing, motivated by a confirmed 2026-08-01 incident: asked to "review my fitness progress," FRED invented an entire fabricated report — a weight, BMI, body-fat percentage, and a full bloodwork panel that matched nothing in `personal/fitness.md` (which records different real figures and no bloodwork at all). The instruction explicitly tells the model the excerpts are the ONLY valid source for facts about Vatsal, that truncated/cut-off excerpts are *missing* information rather than an invitation to complete them, and gives explicit table-semantics rules (a Target/Goal is not-yet-reached, a Baseline is where he started, only Current is true now, a blank cell means genuinely unknown — never substitute from another column). It also explains the confidence tags: `stated`/`confirmed` may be said plainly; `derived` should be attributed to his notes; `inferred`/`speculative` were never said by him and must be flagged as inference, not fact.
6. **Long-term memory** — `self.memory.retrieve_relevant(query=user_input, top_k=5)` (the `MemoryManager`, documented elsewhere; not vault content).
7. Recent conversation history (`recent_messages`, up to the last 10).
8. The final user message.

`_retrieval_query(user_input, recent_messages)` — a short follow-up (`FOLLOW_UP_MAX_WORDS = 6` or fewer words) has the *previous* user turn prepended to the retrieval query, because a short follow-up carries no subject of its own and retrieving on it alone matches the vault on filler words, drops the entry the question is actually about, and the model then answers from nothing and invents content — confirmed against the live index 2026-08-04.

---

## 13. Reminders / timers / file-watches (`orchestrator/scheduler.py`)

`ReminderScheduler` wraps `apscheduler.schedulers.background.BackgroundScheduler` with two jobstores:

- **`"default"`** — `SQLAlchemyJobStore(url=f"sqlite:///{SCHEDULER_DB_PATH}")`, where `SCHEDULER_DB_PATH = DATA_DIR / "reminders.sqlite"`. One-off reminders (`schedule_reminder`), timers (`set_timer`), and recurring reminders (`schedule_recurring`) all persist here — **they survive a FRED restart or a full PC reboot.**
- **`"memory"`** — `MemoryJobStore()`, in-memory only. File watches (`schedule_file_watch`) and periodic proactive checks (`add_periodic`, used by `proactive_checks.py`) live here and do **not** survive a restart. The reason given: a file-watch polls a *bound method*, which isn't safely picklable into a persistent SQLite store, and "a 'watch for this file' request is more reasonably session-scoped anyway."

### 13.1 Missed-reminder catch-up

`misfire_grace_time` for one-shot reminders/timers is `_REMINDER_MISFIRE_GRACE_SECONDS = 3 hours` (not `None`/unlimited as it used to be) — the old unlimited value meant a reminder due while FRED happened to be off for a week would fire the instant it next started, presented as if current. Now, `_catch_up_missed_reminders()` runs **before** `self._scheduler.start()` (order matters — once APScheduler itself starts, it silently skips overdue jobs past their grace period with no error and no announcement; running the scan-and-remove manually first makes the skip an explicit, announced event instead). It reads `self._default_jobstore.get_all_jobs()` **directly** (not `self._scheduler.get_jobs()`, which before `start()` only sees jobs added this process, not anything persisted from a previous run — the jobstore object itself has no such restriction), collects every `reminder_*`/`timer_*` job overdue by more than the grace period, removes them, and speaks one batched `notify()`: `"While I was off: missed reminder \"X\", was due N hours ago; ..."`.

### 13.2 Natural-language time parsing (`parse_when`)

Handles, in priority order: relative offsets (`"in 20 minutes"`, `_RELATIVE_RE`), ISO dates (`"2026-08-05 17:55"`, `_ISO_DATE_RE` — checked **before** the generic clock regex specifically because the unanchored `_CLOCK_RE` used to greedily match "20" out of "2026" as an hour, confirmed bug session_2026-08-03), named weekdays (`"next Friday"`, rolled to the closest future occurrence), `"midnight"`/`"noon"`/`"midday"`, and a general clock-time regex (`_CLOCK_RE`) handling optional am/pm.

Two deliberate ambiguity-resolution rules, since speech (unlike a clock) is inherently ambiguous:

- **A time already past today rolls to tomorrow**, rather than firing instantly or erroring — "remind me at 7am" said at 9am means tomorrow morning.
- **A bare hour with no am/pm resolves to whichever of H or H+12 is next**, not a strict 24-hour reading — "at 7" said at 6am means 7am; said at 9am it means 7pm. (Treating it as strict 24-hour would make an evening "at 7" mean 7am *tomorrow*, almost never the intent.) `"tonight"`/`"evening"`/`"at night"` and `"morning"` are also checked as implied-meridiem words for a bare hour.

`schedule_reminder(message, minutes=None, when=None)` — exactly one of `when` (absolute) or `minutes` (relative offset) is expected; `when` wins if both are given. `minutes` is the sole *required-adjacent* parameter in the tool schema registered in `orchestrator.py` (`"required": ["message"]` only) — deliberately, because marking both fields required caused the model to invent a `minutes` value alongside every absolute clock time it was given.

### 13.3 Recurring reminders (`schedule_recurring` / `parse_recurrence`)

Cron-triggered (`day_of_week`, `hour`, `minute`), with `coalesce=True` so a machine off for several days fires **one** catch-up reminder on restart rather than one per missed occurrence, and `misfire_grace_time=3600` (1 hour) rather than the one-shot tools' 3-hour grace. `job_id` can be passed explicitly (with `replace_existing=True`) so a caller like `tools/workout_plan.py` can re-register the same recurring reminder in place rather than stacking duplicates. `parse_recurrence` deliberately reuses `parse_when()` for the clock-time half rather than re-implementing the am/pm and evening-word logic a second time — the comment notes two independent copies of that subtle logic would inevitably drift out of sync and produce a recurring reminder that fires an hour off from the one-shot phrasing of the same request.

### 13.4 Listing and cancellation

`list_scheduled()` reads live from `self._scheduler.get_jobs()` across both jobstores — it can never be stale, unlike a tool that reads a cached description. It skips any job id starting with `"proactive_"` (internal steps of the end-of-day sequence, or periodic proactive checks — not something to read back as a user-facing pending reminder). `cancel_scheduled(identifier)` matches by exact job id, by substring of the job's stored message/path, or `"all"` to clear everything.

### 13.5 `_next_job_id(prefix)` — why timestamps, not just a counter

```python
def _next_job_id(self, prefix):
    self._job_counter += 1
    stamp = int(time.time())
    return f"{prefix}_{stamp}_{self._job_counter}"
```
A counter-only scheme collided: reminders persist to SQLite but `_job_counter` restarts at zero every process launch, so the first reminder of a new session could try to reuse `"reminder_1"` from a previous run and APScheduler would reject it ("Job identifier conflicts with an existing job") — observed as a timer silently refusing to set while an old reminder from a prior session was still pending.

---

## 14. `tool_call_log.py` — purpose

Appends one JSON line per **executed** tool call to `DATA_DIR / "tool_call_log.jsonl"`, tagged with `path` (`"dispatcher"`, `"tool_loop"`, or `"confirmed_destructive"`), the utterance, the tools the model was actually *offered* (`tools_offered`) versus the one it *picked* (`tool_called`), the routing `reason` string from `intent.classify`, arguments, and a truncated result preview. `result_error` is auto-derived by scanning the result text against `_ERROR_MARKERS` (a substring regex checked against real error phrasing across `tools/*.py` — `error`, `couldn't`, `can't`, `failed`, `not found`, `doesn't exist`, `no such`, `unable`, `invalid`, `malformed` — since tool error phrasing isn't consistent across the codebase and a prefix-only check missed real failures like `"Path not found: ..."`).

The module's own header is explicit that this is **data collection only** — "deliberately NOT a training pipeline... nothing reads this file yet." The stated intent is a future router (an embedding example bank, or similar) that could learn from real routing outcomes instead of the hand-written `TOOL_EXAMPLES` table in `tool_router.py`.

`new_turn_id()` mints one UUID-derived id per user turn, shared across every tool call within that turn and joinable later to `log_turn_feedback(turn_id, interrupted=True, ...)` — a signal logged separately, after the fact, from the UI layer when the user interrupts FRED mid-reply (a "weak negative" outcome label, per the module's stated labeling scheme: tool ran cleanly → positive; tool returned an error string → negative; user cut off the reply → weak negative — all inferred automatically, no manual labeling pass or second model involved).

---

## 15. Lockdown kill-switch (`state/lockdown_state.py`, `state/lockdown_log.py`)

### 15.1 Engage / disengage mechanism

Two tools, registered in `orchestrator._register_tools()`:
- `lockdown_engage` (`system_tools.lockdown_engage`) — **no PIN required to engage.** Reachable via dispatcher regex too (`"lockdown"`, `"engage lockdown"`, `"lock down"`).
- `lockdown_disengage` (`system_tools.lockdown_disengage`) — **requires a PIN spoken together with the trigger phrase**, e.g. "unlock fred 1111". The dispatcher regex `^(?:unlock fred|lift lockdown|stand down)\s+(?P<pin>\d+)$` captures it directly; the PIN itself is validated inside `system_tools.lockdown_disengage()` (out of this document's scope — tool implementations). The design note in `dispatcher.py`: a native Windows popup for PIN entry was tried and repeatedly failed on Windows' foreground-focus rules, so the PIN travels in the same spoken utterance instead — "engaging stays easy, the friction is reserved for getting back out."

### 15.2 State persistence (`state/lockdown_state.py`)

The lock flag is a module-level boolean (`_locked`), loaded once at import from `DATA_DIR / "lockdown_state.json"` and persisted via the same atomic write-then-replace pattern used elsewhere in the codebase (write to `.json.tmp`, then `Path.replace()`). Persisted specifically so a restart — including a full PC reboot — doesn't silently drop back to unlocked. **Missing or corrupt state file always fails OPEN (unlocked), never locked** — the module comment is explicit: "a state file that didn't survive a crash should never be the reason someone's shut out." `set_locked(value)` updates the module global and immediately persists; `is_locked()` is a plain in-memory read.

### 15.3 What lockdown actually blocks

`ToolRegistry.execute()` (in `tools/registry.py`, out of this document's direct scope but referenced here since it's the enforcement point) refuses every tool call except `lockdown_disengage`/`lockdown_engage` themselves while `lockdown_state.is_locked()` is true. **Conversation still works** — `_build_messages()` swaps `SYSTEM_PROMPT` for `LOCKDOWN_SYSTEM_PROMPT` (§12.2), whose appended addendum instructs the model: if a request needs a tool, lookup, or any action, give a short calm acknowledgment that it isn't available while locked and stop — don't explain the whole situation, and answer ordinary conversation completely normally otherwise.

### 15.4 Event log (`state/lockdown_log.py`)

A separate, append-only `DATA_DIR / "lockdown_log.jsonl"` records `{"ts", "kind", "detail"}` rows for engage/disengage events and anything blocked while locked (e.g. `log_event("blocked", detail="get_weather")`). Same fail-soft shape as every other logger in this codebase: a write failure prints and moves on, never raises, "because a logging problem must never break the caller."

---

## 16. Conversation state (`state/conversation_state.py`)

`ConversationState` is deliberately the simplest object in this whole subsystem: an in-memory `list` of `{"role", "content", "timestamp"}` dicts, no persistence, no database. `add_message(role, content)` silently no-ops on blank/whitespace-only content (canned/filler UI text is never added, which is why `_repeat_last()` can search backwards for "the most recent real assistant message" without any further filtering). `get_recent_messages(limit=10)` is a plain slice, `get_all_messages()` returns a defensive copy, `clear()` resets the session, `export_session()` is a debug/analytics dump. Long-term persistence of conversation content happens elsewhere (`MemoryManager`, out of scope for this document) — this class is purely the current session's short-term rolling window, rebuilt fresh every process launch.

---

## 17. Tool registration mechanics (summary, not exhaustive)

`_register_tools()` in `orchestrator.py` is ~1,600 lines calling `self.tools.register(name=..., function=..., description=..., parameters={JSON schema}, destructive=<bool, default False>)` roughly 80 times, wiring every tool implementation module (`tools/system_tools.py`, `machine_tools.py`, `web_tools.py`, `assist_tools.py`, `git_tools.py`, `phone_tools.py`, `whatsapp_tools.py`, `smart_search.py`, `session_summary.py`, `vision_tools.py`, `daily_tasks.py`, `agenda.py`, `vault_files.py`, `workout_plan.py`, `file_index.py`, `self_docs.py`, `otp_tools.py`, `haismart_tools.py`, `sleep_mode_tools.py`, `audio/device_info.py`) into the single `ToolRegistry` instance (`self.tools`, `tools/registry.py`). A handful of tools are registered against **bound orchestrator methods** rather than plain module functions specifically because they need orchestrator-level state the tool module itself shouldn't own:

- `find_file_smart` → `self._find_file_smart` (needs `self.llm` to reason through the folder tree).
- `read_file` → `self._read_file` (needs `self.llm` to summarize long files — see `_READ_FILE_SUMMARY_FLOOR = 250` chars, below which the raw content is already shorter than a summarization round-trip would be worth).
- `ask_about_myself` → `self._ask_about_myself` (needs `self.memory._generate_embedding`, reusing the same embedder rather than loading a second copy).
- `schedule_reminder`, `schedule_file_watch`, `list_scheduled`, `cancel_scheduled` → bound methods on `self.scheduler` directly (the `ReminderScheduler` instance, §13).

`TOOL_LABELS` (a flat dict, ~70 entries) maps each tool name to a present-tense human-readable phrase ("Opening website", "Ending process") for the pill UI's tool-fire confirmation chip, fired via `_announce_tool()` — deliberately phrased as *what FRED is doing*, not the raw function name, "since this is read by a human at a glance, not parsed."

Destructive-tool registration is the one place this file structurally enforces the confirmation gate (§6) — `destructive=True` is the *only* signal `ToolRegistry.is_destructive()` consults, so adding a new irreversible tool later requires nothing more than setting that one kwarg at registration time for the whole confirmation machinery to apply automatically.

---

## Gaps / things not confidently verified from source alone

- `ToolRegistry` itself (`tools/registry.py`) — `is_destructive()`, `execute()`, `get_tool_definitions()`, and the lockdown-refusal check were referenced and their call-site behavior is documented here, but the file's own implementation was not read in full (out of the stated scope: "tool implementations themselves" are excluded). Anyone rebuilding this needs to also read that file directly.
- `llm/llm_client.py` — `LLMClient.generate`, `generate_with_tools`, `generate_stream`, `local_only`, the Groq→Cerebras→local cascade order, and `_strip_tool_call_debris` are referenced extensively (they're central to §7 and §11) but not read in full; this document describes only their observable contract from the orchestrator's call sites.
- `utils/confidence.py` (`classify`/`name`) and `utils/vault_md.py` (`strip_frontmatter`, `extract_h1_title`, `split_sections`, `flatten_tables`) are referenced by name and by their documented effect but not read in full.
- `orchestrator/proactive_checks.py` is referenced (registration call, `_prime_carry` callback) but is explicitly out of scope per the task brief (covered by another doc).
- The full list of ~80 tool registrations in `_register_tools()` was read in its entirety for the destructive-tool set and the bound-method wrappers, but this document does not enumerate every one of the ~80 JSON parameter schemas — see `orchestrator.py` lines 789-2395 directly for any tool's exact schema.
