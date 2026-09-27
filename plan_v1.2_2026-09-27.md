# v1.2 Plan — Gmail, Phone Messages, Calls, File Indexing
*(supersedes "next-up" ordering in `roadmap_pre-finetune_2026-08-26.md` for this cycle — that doc's freeze/fine-tune track and everything else below is explicitly ON HOLD, see bottom)*

**Scope set 2026-09-27, by Vatsal.** Primary focus: perfect four already-live
tool surfaces — Gmail, phone messages, calls, file indexing. Secondary
(pick up only after primary is solid): credentials vault, AI-chat-history
import. Everything else — the tool-calling freeze/fine-tune, the memory
overhaul, perception/proactivity work, the rest of the v1.1 backlog — is
on hold, not cancelled.

---

## 1. Gmail

> **Update 2026-09-27 — this section's "status" below is SUPERSEDED.**
> Built: real Gmail API (`Core/tools/gmail_api.py`) with IMAP as a
> read-only fallback; the 5-minute rescans replaced by History API deltas
> (1 min) + an hourly missed-reply check; new hardcoded tools — today's
> unread, Gmail-syntax search, read, attachment download (confirmed),
> drafts + confirmed send, confirmed signature change. Reading never marks
> mail read; only a sent reply does. Links are always redacted. See
> `DONE.md` 2026-09-27.
> **Decided later the same day:** new Primary/Updates mail is labelled
> `FRED/VVIP` / `FRED/Basic` / `FRED/Useless` (labels only, never archived);
> FRED doesn't touch Social/Promotions/Forums; hand overrides in the vault.

**Status (as of the morning of 2026-09-27): solid, working, running on a temporary transport.**
`Core/tools/gmail_imap.py` (389 lines) is fully wired live. Four
functions: `check_missed_replies()`, `check_email_deadlines()`,
`read_recent_primary()` (the on-demand `check_email` tool, routed via
`intent.py`'s `email_read`), `check_email_tiers()`. All poll on a shared
5-minute cadence (`GMAIL_CHECK_MINUTES`) inside `proactive_checks.py`,
no-op until `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` are set (never seen by
Claude). 9 mocked unit tests exist. Scoped to the Primary category only,
last 3/7/14 days depending on feature.

**How it works:** `imaplib.IMAP4_SSL` to `imap.gmail.com` with a Google
App Password. Primary-tab scoping uses Gmail's `X-GM-RAW` extension
(`category:primary`) since plain IMAP has no concept of inbox tabs.
Missed-reply detection cross-references inbox Message-IDs against
`[Gmail]/Sent Mail`'s `In-Reply-To`/`References` headers. Deadline
detection regex-scans stripped bodies for date-like phrases. Tier
classifier: `List-Unsubscribe` header → useless (never surfaced);
"Vatsal emailed them first" (one `SEARCH TO:<addr>` in Sent Mail) → vvip
(bypasses the naturalness gate, `urgent=True`); else → basic. Dedup is a
tail-capped (500) JSON seen-set of Message-IDs per feature, written
atomically.

**Why built this way:** the real Gmail API is blocked — Vatsal hit a GCP
project-deletion limit on 2026-08-28 (~1 month lockout; unverified
whether it's cleared as of today). IMAP + App Password is the stopgap
with equivalent `gmail.readonly` scope. Primary-only and 5-minute cadence
are deliberate noise/rate-limit calls, not defaults. `_emailed_first` is
a knowingly cheap approximation of "real VIP" — one IMAP search, not a
full Sent-history reconstruction.

**Improvement possibilities:**
- **Confirm the GCP lockout status**, then build `gmail_api.py` as a
  drop-in OAuth replacement — the module's own docstring says the rest
  of the pipeline (dedup, proactive wiring) doesn't need to change.
- Deadline regex is naive: no year-rollover handling, no timezone
  awareness, will false-positive on "today"/"tomorrow" in signatures or
  quoted replies.
- `_emailed_first` re-runs a live IMAP search per newly-seen sender per
  poll — fine now, won't scale if inbox volume grows.
- No calendar integration — deadline surfacing has nowhere to actually
  write a reminder/event (calendar-aware nudge was explicitly held out
  of scope in the prior roadmap doc).
- Draft-for-approval (`gmail.compose`) and the noise-triage digest are
  designed in the prior roadmap doc but unbuilt — still read-only.
- Each poll opens/closes a fresh IMAP connection per feature (up to 4
  logins per 5-minute cycle) — could share one connection per cycle.

**Verdict:** a well-built stopgap, not half-built. Highest-value next
step: check the GCP limit, then swap IMAP → real OAuth API without
touching the pipeline.

---

## 2. Phone messages (WhatsApp + SMS/OTP)

**Status: half-built, but the built half is solid.** There's no general
"read my texts" tool — coverage exists for two narrow cases:
- **WhatsApp** (`whatsapp_tools.py`) — full read + send + four-tier
  classification (useless/basic/trusted/vip), polled every 2 minutes
  (`VIP_MESSAGE_CHECK_MINUTES`) via `check_vip_messages`.
- **SMS** — deliberately scoped to exactly one function,
  `otp_tools.find_otp()`: last 5 minutes only, OTP-shaped codes only. No
  general SMS inbox browsing exists.

**How it works:** everything routes over `adb` (USB or wireless) to a
paired Android phone, with multi-phone naming and an mDNS-based wireless
address cache so port churn doesn't orphan per-phone state. WhatsApp
reads pull `dumpsys notification --noredact` (works even phone-locked)
and regex-parse `MessagingStyle` bundles; sending drives the real UI via
`uiautomator` taps (needs phone unlocked), verified through three layers
(chat identity, compose-box content, landed-bubble text) before
confirming a send. SMS/OTP queries `content://sms/inbox` directly,
filtered to the phone's own clock minus 5 minutes, regex-matched for a
keyword-adjacent 4-8 char code. A per-phone tier file (shared with the
calls watcher, see below) gates both what's surfaced and who can be
messaged back.

**Why built this way:** no official WhatsApp Business API (needs a
separate number) and no unofficial client (ban risk) — driving the real
phone's UI/notifications instead means the account can't get banned. SMS
is isolated into its own tiny, time-boxed, keyword-gated function on
purpose — SMS is every account's recovery channel, so the blast radius of
"what can read SMS" is kept as small as possible. Tiering exists because
every WhatsApp message read is attacker-controlled text entering an
agent with tools — dropping "useless" senders shrinks prompt-injection
surface, not just notification noise.

**Improvement possibilities:**
- **The single adb link is the actual failure mode in daily use** — USB
  unauthorized-prompt, Android disabling wireless debugging on reboot,
  ambiguous multi-phone resolution. `device_status()` already has
  hard-won error messages for these; the underlying fragility isn't
  fixable, only worth monitoring/surfacing better.
- WhatsApp send is UI-automation (coordinate taps), not an API — brittle
  to WhatsApp UI updates, no fallback if node text/content-desc
  attributes shift.
- **No general SMS reading exists, by design.** If "phone messages"
  means SMS broadly (not just OTP), that's the real gap — needs its own
  scope/tiering decision mirroring WhatsApp's, not a quick extension of
  `otp_tools.py`.
- Calls' VIP tiering piggybacks on WhatsApp's tier file rather than a
  unified per-contact trust model — works today, but any future channel
  faces the same "which tier file" question.
- Nameless callers/senders (unsaved or replaced number) can never be
  flagged VIP — both watchers require a resolved contact name.

**Verdict:** WhatsApp and OTP are production-quality, not scaffolding.
Highest-value next step: **decide whether general SMS reading is
actually wanted** (it's the one channel deliberately left out) before
investing further in what already exists — that decision shapes whether
this item is "done" or "needs a new subsystem."

---

## 3. Calls

**Status: fully working.** `phone_tools.py` covers dial (`call_phone`),
hang up, call-log reading (`get_call_log`), and a proactive VIP-call
watcher (`check_recent_calls`) polling every 2 minutes
(`CALL_LOG_CHECK_MINUTES`).

**How it works:** same `adb` bridge as messages. Dial via
`am start -a android.intent.action.CALL`; log reads via
`content query --uri content://call_log/calls`. The watcher tracks a
single max-timestamp watermark (`call_log_seen.json`), diffs new rows
against it, resolves caller names through the same contacts vault
`call_phone` uses, and filters to VIP tier before surfacing anything.

**Why built this way:** same rationale as messages — real device access
over adb, no ban-risk cloud dependency, VIP-gated to avoid noise.

**Improvement possibilities:** shares two of messages' gaps directly —
(a) same single-adb-link fragility as the failure mode, (b) same
nameless-caller blind spot (a VIP calling from an unsaved/replaced
number goes unflagged), (c) same implicit coupling to WhatsApp's tier
file rather than a unified contact-trust model.

**Verdict:** solid, no known functional gaps beyond what messages
already shares. Lowest-effort item in this plan — likely just needs the
contact-trust-model fix (see synthesis below) rather than standalone
work.

---

## 4. File indexing

**Status: solid, functional, already wired end-to-end.** Not the "no
maintained index yet" state the old v1.1 backlog item assumed — a real
SQLite index exists (`Core/tools/file_index.py`,
`files(path, name, mtime, size)`), distinct from `machine_tools`'s older
live `os.walk`-per-query search. `smart_search.find_file_smart` is a
third mode: an agentic folder-by-folder LLM walk for natural-language
descriptions ("find my health logs"), backed by its own `found_cache`
layer.

**How it works:** `reindex_drive()` does one full `_walk_pruned` pass
(shared skip-list with `machine_tools` — AppData/node_modules/.git/venv/
etc.) and writes to SQLite. `search_index()` is a plain `LIKE %query%`
lookup — instant, but frozen at last reindex. `add_entry`/`remove_entry`
hook into `create_text_file`/`create_folder`/`move_file`/`rename_file`/
`delete_file`, so day-to-day file ops through FRED keep the index
incrementally correct without a full rewalk. Reachable end-to-end:
registered as real orchestrator tools, with dispatcher cues ("reindex",
"file index") added 2026-08-17.

**Why built this way:** deliberately never auto-refreshes — no startup
scan, no background scheduler — specifically to avoid re-inventing the
live walk "with extra steps." The incremental hooks cover the common
case (files touched through FRED itself); only external changes
(something added via File Explorer, not through FRED) go stale until an
explicit "reindex my drive."

**Improvement possibilities:**
- **Staleness for out-of-band changes** is the one real ceiling — an
  external delete/move/rename leaves a ghost or missing entry until a
  manual reindex. A `watchdog`-based background updater would close
  this; worth doing since a stale file index gives *wrong* answers, not
  just outdated noise.
- `search_index`'s `LIKE %x%` has no fuzzy/typo tolerance — acceptable
  since `smart_search` already covers the semantic/fuzzy case; only
  matters for a typo'd exact filename.
- Filenames only, no content search — genuinely out of scope unless
  requested.

**Verdict:** finished, coherent design, not half-built. Highest-value
next step: a lightweight filesystem watcher for the external-change
staleness gap — otherwise this item is close to done as-is.

---

## 5. Cross-cutting fix worth doing once, not four times

Three of the four areas above (calls, WhatsApp, and implicitly any
future SMS work) resolve VIP/trust status by reading WhatsApp's own tier
file as a proxy for "who matters." That's an implicit coupling, not a
real per-contact trust model. Fixing this once — a single shared
contact-trust source that calls, WhatsApp, and (if built) SMS all read
— is smaller than patching each channel's "unnamed caller" and
"whichever tier file" gaps separately, and removes a whole class of
future bugs when a fifth channel shows up.

---

## 6. Suggested order inside v1.2

1. **File indexing** — closest to done; add the filesystem watcher if/when
   staleness actually causes a wrong answer, otherwise leave as-is.
2. **Calls** — no standalone work needed beyond the shared trust-model fix.
3. **Gmail** — check the GCP lockout, swap IMAP → OAuth if clear; fix the
   deadline-regex edge cases regardless.
4. **Phone messages** — the one item needing a real decision (general SMS
   reading: yes/no, and at what scope) before further investment; WhatsApp
   itself needs no urgent work.
5. **Shared contact-trust model** — do once, after deciding SMS's scope,
   since SMS would be the third consumer of it.

---

## 7. Secondary (after the above is solid)

- **Credentials vault** — prerequisite named in the v1.1 backlog for any
  *new* account integration; the four tools above already work without
  it (Gmail uses an env-var App Password, phone tools use adb, no vault
  needed yet). Only becomes urgent once a fifth integration (Spotify,
  YouTube, Drive, etc.) is actually started.
- **AI chat history import** ("AI_Chats" memory project) — the largest
  single item in the v1.1 backlog (multi-platform export formats,
  cross-platform dedup, sensitive-data handling). Explicitly secondary
  to this plan; not started.

---

## 8. On hold (not cancelled)

Everything else previously in flight stays parked for this cycle:
- Tool-calling freeze + LoRA fine-tune track (`roadmap_pre-finetune_2026-08-26.md`).
- Memory & meta-understanding overhaul (`FRED_Memory_Overhaul.md`) — design-only, Phase 1 not started.
- Perception-layer loose ends: voice enrollment/`voice_id.py` wiring, VAD real-audio verification, empty-room YOLO negative control.
- Proactivity naturalness principles 2/3/5/6 (task-boundary interrupts, composite interruptibility, suppress-during-busy, observation-style phrasing).
- Gmail features 3-5 (noise-triage digest, draft-for-approval, calendar-aware nudge) — held out of scope even within Gmail's own item above.
- Phone screen-recording tool (unscoped personal tool).
- Remaining v1.1 backlog items 1, 3-6, 8-13 (dispatcher self-learning, extreme-of-extreme cloud tier, Dynamic-Island UI, voice cloning, grammar-constrained STT, Eon/BRAIN/COPY/IAWY integration, cross-device access, calculator tool, system-info tool, self-documentation access, YouTube upload).
