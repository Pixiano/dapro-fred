# FRED Tool Inventory — Core/tools/

Source of truth for this document: `Core/orchestrator/orchestrator.py`'s
`_register_tools()` method (the only place tools actually get wired to the
LLM) and `Core/tools/registry.py` (the `ToolRegistry` class that holds them).
Every tool name below is the literal string the LLM calls; every "implemented
in" is the literal function `orchestrator.py` points `self.tools.register()`
at. This was built by reading the full registration block (~1600 lines) plus
every file under `Core/tools/`, not by trusting `README.md`'s summary table —
discrepancies between the two are called out explicitly in the "README vs
reality" section at the end.

## 1. How tools get from a Python function to something the LLM can call

`Core/tools/registry.py` defines `ToolRegistry`, a small in-memory class:

- `register(name, function, description, parameters, destructive=False)` —
  stores `{function, description, parameters, destructive}` in
  `self.tools[name]`. `destructive` marks a tool the orchestrator must get an
  explicit "yes" for before running (see §8).
- `execute(tool_name, **kwargs)` — the single choke point every tool call
  passes through. Before calling the function it checks
  `state.lockdown_state.is_locked()`; if FRED is in lockdown mode, every tool
  except `lockdown_engage`/`lockdown_disengage` is refused with "FRED is in
  lockdown mode, sir — say 'unlock fred' to restore access," and the refusal
  is logged via `state.lockdown_log.log_event("blocked", detail=tool_name)`.
  This is why lockdown is enforced in exactly one place rather than a guard
  copy-pasted into every tool function.
- `get_tool_definitions(only=None)` — converts the registry into OpenAI-style
  function-call schemas (`{"type": "function", "function": {...}}`). The
  `only` parameter is how `orchestrator/intent.py`'s router shows a small
  local model a short menu (a handful of tools) instead of the full ~90-tool
  list on every turn — an empty/all-unknown `only` degrades to showing every
  tool rather than none, so a bad subset never disables tooling entirely.
- `list_tools()` — plain list of registered names, used by `describe_self`.
- `is_destructive(tool_name)` — used by the orchestrator's confirmation gate.

`Core/tools/__init__.py` is empty — `tools/` is a plain package with no
re-exports; every module is imported directly (`from tools import
machine_tools`, etc.) by `orchestrator.py`.

Registration itself happens once, in `Orchestrator._register_tools()`
(`Core/orchestrator/orchestrator.py`, roughly lines 789–2394), which is
called from the orchestrator's `__init__`. Some registered "functions" are
bare module-level functions (`machine_tools.close_window`); others are bound
methods on the orchestrator itself (`self._read_file`, `self._find_file_smart`,
`self.scheduler.schedule_reminder`) or lambdas (`lambda: system_tools
.describe_self(self.tools.list_tools())`) — used whenever a tool needs
orchestrator-level state (the loaded LLM handle, the scheduler, the live tool
list) that a plain module function in `tools/` has no access to.

## 2. Full inventory, by category

Counted directly from `_register_tools()`: **91 `self.tools.register()` calls**
(README.md says "~80 registered tools" — a stale/rounded count; see §10).
Categories below follow README's own grouping where it holds up, corrected
where the real registry disagrees.

### Info

| Tool name | Implemented in | Purpose | Notable params |
|---|---|---|---|
| `get_current_time` | `system_tools.get_current_time` | Local date/time, phrased for speech | `part`: "time"/"date"/"both" |
| `web_search` | `web_tools.web_search` | Live DuckDuckGo search (`ddgs` package), URLs stripped from results (bad for TTS) | `query`, `max_results` |
| `get_weather` | `web_tools.get_weather` | Current conditions or a forecast up to 2 days out, via wttr.in | `location`, `when` (today/tomorrow/weekday/range/weekend) |
| `calculate` | `assist_tools.calculate` | Exact arithmetic via a whitelisted `ast` evaluator (never `eval()`), phrased as a sentence | `expression` |
| `get_system_status` | `assist_tools.get_system_status` | Battery, CPU, RAM, disk free, uptime | — |
| `get_network_status` | `assist_tools.get_network_status` | Online/offline, SSID + signal, local IP | — |

### Apps

| Tool name | Implemented in | Purpose | Notable params |
|---|---|---|---|
| `open_website` | `system_tools.open_website` | Opens URL in default browser; bare host gets `https://` prepended | `url` |
| `launch_application` | `system_tools.launch_application` | Resolve a friendly app name (alias table → PATH → App Paths registry → Start Menu `.lnk` search → common install dirs) and launch it; self-learns new aliases to `Core/data/app_aliases.json` | `app_name` |
| `open_path` | `assist_tools.open_path` | Open an existing file/folder with its default program; falls back to checking Desktop/Downloads/Documents/Pictures for a bare filename | `path` |
| `open_vault_file` | `vault_files.open_vault_file` | Open a vault file by name/stem/title, no path needed | `name` |
| `open_last_found` | `assist_tools.open_last_found` | Open a result from the most recent `search_files`/`find_file_smart` call ("open it", "open the second one") — referent lives in `found_cache`, not conversation history | `which` (1-based) |

### Audio

| Tool name | Implemented in | Purpose |
|---|---|---|
| `get_volume` / `set_volume` / `adjust_volume` | `machine_tools` | System volume, via `pycaw` |
| `mute` / `is_muted` (internal, not itself a tool) | `machine_tools.mute` | Mutes FRED's own TTS output only — never touches system audio (see `audio/mute_state.py`) |
| `media_control` | `assist_tools.media_control` | Sends OS media keys (play/pause/next/prev/stop) via `ctypes.windll.user32.keybd_event` |
| `list_audio_devices` / `set_input_device` / `set_output_device` | `device_info.py` (not in this doc's file set) | Enumerate/switch mic and speaker by index |

### Display

| Tool name | Implemented in | Purpose |
|---|---|---|
| `get_brightness` / `set_brightness` / `adjust_brightness` | `machine_tools` (via `screen_brightness_control`) | Screen brightness |
| `take_screenshot` | `machine_tools.take_screenshot` | Saves a PNG to `~/Pictures/Screenshots` (via `mss`) — cannot describe the image, only saves the file |

### Vision

| Tool name | Implemented in | Purpose |
|---|---|---|
| `whats_on_screen` | `vision_tools.whats_on_screen` | Always attempts a fresh on-demand capture first (`watcher_manager.capture_now()`); if the main model is resident and cloud vision fails, it force-unloads the main model and retries local-only before falling back to the last cached description with an honest staleness hedge |
| `look_through_camera` | `vision_tools.look_through_camera` | **Corrected 2026-08-21** — this is now the DESK WEBCAM (`PRESENCE_CAMERA_INDEX`), the general/default camera tool for "what am I looking at" / "look through the camera" with no mention of the phone. It previously called `phone_tools.capture_camera_photo()` (the phone's camera) by mistake — that behavior is now `take_phone_photo` below. Captures one frame (open/read/release immediately, same pattern as `presence.poll_once()`), describes via `app.orchestrator.llm.describe_image()`, on-demand, no cache. |
| `take_phone_photo` | `vision_tools.take_phone_photo` | **New 2026-08-21**, split out of the `look_through_camera` fix above — captures the paired PHONE's camera view specifically (over adb, reuses `phone_tools.capture_camera_photo()`) and describes it. Only for explicit phone-camera requests ("take a pic from my phone"); a bare "look through the camera" means the webcam tool above. |

### Windows

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `list_windows` | `machine_tools.list_windows` | Titles of open windows (shell/system windows filtered via `_SHELL_WINDOWS`) | |
| `focus_window` / `minimize_window` / `maximize_window` | `machine_tools` | By partial title match via `pygetwindow` | |
| `close_window` | `machine_tools.close_window` | May discard unsaved work | **yes** |

### Files

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `create_text_file` | `system_tools.create_text_file` | Refuses a bare filename with no real destination rather than silently anchoring under Documents/FRED | |
| `create_folder` | `system_tools.create_folder` | Same destination requirement | |
| `append_to_file` | `assist_tools.append_to_file` | Quick-capture append, creates file if needed | |
| `list_directory` | `assist_tools.list_directory` | Folders first then files, with sizes; reports total folder/file counts even when truncated | |
| `read_file` | orchestrator's `self._read_file` wrapping `machine_tools.read_file` | Summarises files over 250 chars via the LLM (from the file's own wording, not free paraphrase) unless `raw=true` | |
| `move_file` / `rename_file` | `machine_tools` | Windows `Path.rename()` — explicitly checked against `FileExistsError` so a collision is a clean sentence, not a stack trace | |
| `delete_file` | `machine_tools.delete_file` | Irreversible; also calls `file_index.remove_entry()` to purge it from the index | **yes** |
| `search_files` | `machine_tools.search_files` | Deterministic substring filename search under a directory, pruning heavy dirs (`_SKIP_DIRS`); cached via `found_cache` | |
| `find_file_smart` | orchestrator's `self._find_file_smart` wrapping `smart_search.find_file_smart` | Agentic natural-language file search — see §7 | |
| `convert_file` | `system_tools.convert_file` | Shells out to `ffmpeg` (must be on PATH) | |
| `print_file` | `system_tools.print_file` | `os.startfile(path, "print")` — native shell print verb | |
| `reindex_drive` | `file_index.reindex_drive` | Rebuild the SQLite file index (slow, walk-based) | |
| `search_index` | `file_index.search_index` | Fast SQLite `LIKE` search against the last `reindex_drive` snapshot | |

### Processes

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `list_processes` | `machine_tools.list_processes` | Via `psutil`, optional name filter | |
| `kill_process` | `machine_tools.kill_process` | Kills every process matching name/PID substring (see `matching_processes`, used by the orchestrator's confirmation preview to show exact targets before the user says yes) | **yes** |

### Power

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `power_action` | `assist_tools.power_action` | lock/sleep/hibernate/restart/shutdown/cancel; shutdown/restart use a cancellable delay (`SHUTDOWN_DELAY = 10`s) | **yes** |
| `end_of_day` | orchestrator's `self.end_of_day` | Wind-down sequence: single upfront confirmation, then closes every open window a few seconds apart with its own announcement, recaps the day, offers shutdown. Rewritten 2026-08-18 from a per-window confirmation chain that stalled on one missed answer. | (confirmed once, not itself `destructive=True`) |
| `restart_fred` | `machine_tools.restart_fred` | Spawns a fresh detached `fred_popup.py --greet-now` process, waits for the current turn's speech to finish, then tears the old process down (`os._exit(0)`) | **yes** |

### Schedule

| Tool name | Implemented in | Purpose |
|---|---|---|
| `schedule_reminder` | `self.scheduler.schedule_reminder` | One-off, by clock time or minute offset |
| `schedule_recurring` | `self.scheduler.schedule_recurring` | Repeating (daily/weekly/weekday patterns) |
| `schedule_file_watch` | `self.scheduler.schedule_file_watch` | Notify when a path appears |
| `set_timer` | `self.scheduler.set_timer` | Countdown timer in minutes |
| `list_scheduled` / `cancel_scheduled` | `self.scheduler` | List/cancel by id, message substring, or "all" |

### Phone (see §3 for full detail)

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `call_phone` | `phone_tools.call_phone` | Dial by number or contact name via adb `CALL` intent | **yes** |
| `hang_up` | `phone_tools.hang_up` | `KEYCODE_ENDCALL` | |
| `get_call_log` | `phone_tools.get_call_log` | Recent calls / missed-only | |
| `set_alarm` | `phone_tools.set_alarm` | Sets a phone alarm via `SET_ALARM` intent with `SKIP_UI=true` | |
| `sync_contacts` | `phone_tools.sync_contacts` | Append-only pull of phone contacts, ranked by call frequency, into the vault | |
| `use_phone` | `phone_tools.use_phone` | Choose which configured phone (`FRED_PHONES` env var) commands target | |
| `find_otp` | `otp_tools.find_otp` | Gated OTP finder — see §4 | **yes** |
| `take_phone_photo` | (listed under Vision above) | — | |

Note: `capture_camera_photo` (in `phone_tools.py`) is not itself a registered
tool — it's an internal helper, now called by `vision_tools.take_phone_photo`
(not `look_through_camera` — that was corrected 2026-08-21 to be the desk
webcam, see Vision above).

### Messaging (WhatsApp — see §3 for full detail)

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `read_messages` | `whatsapp_tools.read_messages` | Recent notifications, `useless`-tier senders dropped; works with the phone locked | |
| `send_message` | `whatsapp_tools.send_message` | Drives the real WhatsApp UI via adb/uiautomator; trusted/VIP only; needs the screen unlocked | **yes** |
| `set_contact_tier` | `whatsapp_tools.set_contact_tier` | Move a sender between `useless`/`basic`/`trusted`/`vip` | **yes** |
| `list_contact_tiers` | `whatsapp_tools.list_contact_tiers` | Show current tier assignments and policy | |

### Tasks / Agenda

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `add_task` | `daily_tasks.add_task` | Same-day scratch task, appended to the vault's daily note | |
| `list_tasks` | `daily_tasks.list_tasks` | Today's tasks plus rolled-forward still-open ones from earlier days | |
| `complete_task` | `daily_tasks.complete_task` | Toggle done/not-done by substring match | |
| `add_agenda_item` | `agenda.add_item` | Homework/project/event with subject, detail, due date/time, count, prep lead time, next step | |
| `list_agenda_items` | `agenda.list_items` | Filter by when (today/tomorrow/week/overdue/all)/kind/subject | |
| `update_agenda_item` | `agenda.update_item` | Progress, done state, reschedule, note — where a proactive question's answer actually gets recorded | |
| `delete_agenda_item` | `agenda.delete_item` | Remove a wrongly-logged item outright; refuses on ambiguous match rather than guessing | **yes** |

### Workout

| Tool name | Implemented in | Purpose |
|---|---|---|
| `workout_split` | `workout_plan.describe_split` | Parses `personal/workout_split_June.pdf` (via `pypdf`) into a weekly split |
| `todays_workout` | `workout_plan.today_workout` | Today's focus muscle group, or "rest day" |
| `schedule_workouts` | `lambda **kw: workout_plan.schedule_workouts(self.scheduler, **kw)` | Registers one recurring reminder per training day (default 16:55), replacing stale ones when the split changes |

### Git (read-only)

| Tool name | Implemented in | Purpose |
|---|---|---|
| `git_status` | `git_tools.git_status` | Branch + staged/modified/untracked summary |
| `git_log` | `git_tools.git_log` | Recent commit subjects + relative time |
| `git_diff_summary` | `git_tools.git_diff_summary` | File/line-count summary, never the raw diff body |

No git tool ever mutates repo state — confirmed deliberately: "write access
(commit/push) is a separate, bigger risk category and deliberately not
built here." Defaults to the FRED project itself (`BASE_DIR.parent`) when
`repo_path` is blank or a small model invents a bad path.

### Recap / Recall

| Tool name | Implemented in | Purpose |
|---|---|---|
| `summarise_today` | orchestrator's `self._summarise_today` → `session_summary.preview_session_summary` | Builds and previews (never writes) today's recap from session event logs |
| `save_today_summary` | orchestrator's `self._save_today_summary` → `session_summary.save_session_summary` | Only called after explicit user confirmation of the preview; writes into today's auto-created vault session block |
| `recall_recent_conversation` | `session_summary.recall_recent_conversation` | Verbatim recent transcript from today's session log (not embeddings/summary) — survives a FRED restart |
| `repeat_last` | orchestrator's `self._repeat_last` | Pure state lookup of the last real (non-filler) assistant message |

### Self-docs

| Tool name | Implemented in | Purpose |
|---|---|---|
| `describe_self` | `lambda: system_tools.describe_self(self.tools.list_tools())` | "What tools/model" from LIVE runtime state (tool count, `DEFAULT_TIER`/`MODEL_TIERS`) |
| `ask_about_myself` | orchestrator's `self._ask_about_myself` → `self_docs.ask_about_myself` | "What can you do / how does X work / why was it built this way" from indexed project docs — see §10 |

### Vault

Vault semantic retrieval itself is not a registered tool (it's injected into
every turn's context by the orchestrator, not called on demand) — the only
vault-specific *tool* is `open_vault_file` (listed under Apps above).

### Lockdown

| Tool name | Implemented in | Purpose |
|---|---|---|
| `lockdown_engage` | `system_tools.lockdown_engage` | Bare trigger, no PIN. Refuses every other tool (see `ToolRegistry.execute`) while engaged; conversation still works. Also unloads LLM/Whisper/Kokoro in the background (`_stand_down_models`) once the current turn finishes speaking. |
| `lockdown_disengage` | `system_tools.lockdown_disengage` | Must be said with the PIN (hardcoded `_LOCKDOWN_PIN = "1111"`, explicitly marked `ponytail: plain demo-grade constant... swap for something less trivial before this protects anything that actually matters`) |

### Haismart AC control (not in README's category table at all — see §5, §10)

| Tool name | Implemented in | Purpose | Destructive |
|---|---|---|---|
| `get_ac_status` | `haismart_tools.get_ac_status` | Power/mode/target temp/fan/room temp | |
| `set_ac_power` | `haismart_tools.set_ac_power` | On/off | |
| `set_ac_temperature` | `haismart_tools.set_ac_temperature` | 16–30°C | |
| `set_ac_mode` | `haismart_tools.set_ac_mode` | auto/cool/dry/heat/fan_only | |
| `set_ac_fan_speed` | `haismart_tools.set_ac_fan_speed` | high/medium/low/auto | |

None of these five are `destructive=True` in the registry, despite
controlling physical hardware — worth flagging as a possible gap, not
something this doc should silently "fix" by re-describing it as gated.

### Sleep mode / presence / reflection

| Tool name | Implemented in | Purpose |
|---|---|---|
| `cancel_sleep_mode` | `sleep_mode_tools.cancel_sleep_mode` | Force-exit sleep mode on explicit command — see §9 |
| `get_active_hours_summary` | `presence_tools.describe_active_hours` | **New 2026-08-22.** "When am I usually active" — per-hour presence-poll ratio over the trailing N days (default 7), turned into a spoken hour-range sentence. See `06_proactive_and_memory.md` §2.10a. |
| `review_pending_reflection` | `reflection.review_pending` | **New 2026-08-22.** Opens the oldest un-reviewed self-observation draft staged by the sleep-mode deep reflection pass and marks it reviewed. Only meant to be called right after FRED has offered a review and the user said yes — see `05_presence_and_sleep_mode.md` §4.3. |

## 3. phone_tools.py in full

**File header framing:** "Remote call access: FRED dials from the PC, the
phone places the call." Control and audio are deliberately split — this
module only ever sends an Android `CALL` intent over `adb`; call audio stays
on the phone (speakerphone or headset). A Bluetooth HFP route was considered
and rejected 2026-08-14: it would need Windows' Phone Link to take the
hands-free role and flips the default mic to an 8kHz endpoint — the exact
endpoint the wake word listens on.

**Setup (manual, one-time):** enable USB debugging, plug in, accept the RSA
prompt. For wireless: `adb tcpip 5555` then `adb connect <ip>:5555`, with
`FRED_PHONE_ADB=<ip>:5555` for automatic reconnects.

**Multi-phone addressing.** `FRED_PHONES` env var
(`"personal=SERIAL1,work=SERIAL2"`) names phones by their stable adb serial
rather than by address, because a wireless session's address (`ip:port`) is
unstable — the port changes every time wireless debugging restarts, and
Android disables wireless debugging on every reboot. `_discover(serial)`
resolves a serial to a currently-reachable address in priority order: (1)
already attached under that serial, (2) an open wireless session (queried via
`getprop ro.serialno` on each attached address — checked *before* mDNS,
because a live connection can outlast its own mDNS announcement), (3) a
throttled (`_WIRELESS_REFRESH_INTERVAL = 300s`) cached wireless address
opportunistically refreshed while USB is live, (4) a fresh mDNS scan
(`adb mdns services`), (5) the legacy fixed `FRED_PHONE_ADB` address.
`_resolve()` on top of that: if exactly one device is attached, use it
unconditionally (ignoring any remembered `use_phone()` selection — "wired to
A or B, whatever" is the real usage pattern); otherwise falls through to
`FRED_PHONES` disambiguation; refuses (empty string) rather than guessing
when several devices are attached and there's no way to tell them apart.
`WIRED_ONLY` is a module flag (currently `False`, enabled 2026-08-17) that
when `True` ignores any open wireless session entirely.

**Calling.** `call_phone(number)` resolves either a raw number
(`_clean_number` — digit-only regex validation, 5–20 digits, rebuilt from
scratch out of matched digits so nothing a caller passes can smuggle a shell
argument) or a contact name (`resolve_target` → `find_contact`, exact match →
substring → `difflib` fuzzy match at 0.7 cutoff, refusing to guess on an
ambiguous hit) into a `tel:` intent. `hang_up()` sends `KEYCODE_ENDCALL`.

**Alarms.** `set_alarm(hour, minute, label)` sends `SET_ALARM` with
`--ez android.intent.extra.alarm.SKIP_UI true` — without this the intent just
opens the clock app's new-alarm screen and waits for a human tap, useless
from voice. The label is stripped of `"`, `\`, `` ` ``, `$` (`_UNSAFE_LABEL`
regex) and wrapped in literal double quotes before going into the `adb shell`
argument, because `adb shell` joins its whole argv into one string re-run
through the *device's own shell* — a multi-word label without quoting gets
mis-tokenized into two separate `am start` arguments (confirmed live
2026-08-20), and an unstripped `"` in a quoted label would break out of that
shell string entirely.

**Call log.** `get_call_log(limit, missed_only)` names via `_read_contacts()`
first, the call log's own (usually blank) provider `name` field only as
fallback — the same lookup `call_phone` dials from, so the name spoken to
dial someone and the name shown for their call are guaranteed to agree.
`check_recent_calls()` is the proactive-watcher half: new VIP-tier calls
since a persisted high-water mark (`CALL_SEEN_PATH =
DATA_DIR/call_log_seen.json`), reusing WhatsApp's tier data
(`whatsapp_tools._read_tiers`/`tier_of`) rather than a second call-specific
tier file — "a person's trust level isn't a property of the channel they're
reaching you through." First-ever run seeds the watermark silently (no
retroactive announcement), mirroring `check_vip_messages`.

**CONTACTS_PATH — the append-only design.** `CONTACTS_PATH = VAULT_DIR /
"people" / "contacts.md"`. Two deliberate exceptions to the vault's general
read-only-to-FRED rule (both explicit Vatsal calls, 2026-08-15):

1. `sync_contacts` writes this one file, and only ever appends: a name
   already on file is never removed by a sync, and a number only changes
   when the phone actively disagrees with it (a real correction). Deletions
   never propagate automatically — see the `## removed` tombstone section
   below.
2. `contacts.md` is in `VAULT_EXCLUDED_FILES` so the vault router never
   embeds or injects it into a cloud-bound prompt — a 50-name phone book has
   no business riding along with vault retrieval.

The merge logic (`_merge`) in detail, each rule backed by a confirmed
2026-08-16 real bug:

- **Identity is the phone NUMBER, not the display name** (`_match_key` — last
  10 digits, a deliberate `ponytail`-flagged simplification correct for
  Indian mobiles, would collide on two international numbers sharing a
  10-digit tail). Keying on name meant a hand-renamed contact came back under
  the phone's own spelling as a duplicate on the very next sync.
- **A number already on file under any name is skipped entirely** — the
  label Vatsal chose wins permanently over whatever the phone calls it.
- **Duplicate names within one incoming batch are not a "correction"** — a
  contact with both a mobile and landline arrives twice under one
  `display_name`; first occurrence (rank-ordered by call count) wins rather
  than the second silently overwriting the more-called number.
- **A name mapping to several DIFFERENT numbers in one batch is ambiguous**,
  not authoritative — confirmed bug: phone B had two contacts both named
  "Mom," and a naive sync replaced the real Mom's number (already dialled
  successfully) with a stranger's.
- **Tombstones** (`## removed` section, written by `_write_contacts`):
  append-only protects hand *edits* but not hand *deletions* — the file
  can't otherwise tell "Vatsal deleted this" from "never seen," so every
  sync would resurrect everything trimmed. Measured on the real file: a sync
  without tombstones would have re-added 33 of 34 just-curated-away entries.

**Camera.** `capture_camera_photo()` deliberately does NOT use the standard
`IMAGE_CAPTURE` → `MediaStore` → pull flow — confirmed live 2026-08-20 that it
silently drops the frame on Android 15 scoped storage (`am start` has no
calling Activity to receive the result or honor an output URI). Instead it
screenshots the live camera viewfinder directly before any shutter press
(screen resolution, not sensor resolution — irrelevant, since the only
consumer is the vision pipeline). `KEYCODE_HOME` afterward (not a
package-specific force-stop) keeps it portable across different phones'
stock camera apps.

## 4. otp_tools.py — the gated OTP finder

One tool, `find_otp(service_hint="")`, deliberately isolated in its own file
rather than folded into `phone_tools.py`, because "reading SMS is more
sensitive than anything else the phone integration does (SMS is the
recovery/verification channel for nearly every account)."

**What gates it:**

1. **`destructive=True`** in the registry — same confirmation-before-run gate
   as `call_phone`/`send_message`. The intended UX (built elsewhere, not in
   this file): FRED notices something OTP-shaped on screen, asks "should I
   try to find the OTP, sir?", and only calls `find_otp()` on an explicit
   yes. The tool's own registered description explicitly warns the model:
   never call this on a bare "read my texts" request, which this tool does
   not do.
2. **Hard 5-minute recency window** (`_MAX_AGE_MS = 5 * 60 * 1000`),
   non-configurable — not exposed as a parameter on purpose: "a caller asking
   for a wider window is asking for the wrong tool." Measured against the
   **phone's own clock** (`_device_now_ms()`, via `adb shell date +%s`), not
   the PC's, so PC clock drift can't silently widen/shrink the window.
3. **OTP-shape heuristic** (`_extract_code`): a 4–8 char alphanumeric token
   containing at least one digit, sitting within 25 characters
   (`_PROXIMITY`) of an OTP-ish keyword (`otp`, `one-time pass/code`,
   `verification code`, `passcode`, `security code`, `login code`, `auth
   code` — a broad cross-service list, since the issuing service is never
   known in advance). A code with no nearby keyword is never guessed at —
   "too risky... could be an amount, a UPI ref, an account tail."

Confirmed live against a real device (`O3PRIS25DB005413`, 2026-08-20):
`adb shell content query --uri content://sms/inbox` runs with no extra `pm
grant` needed, since `content query` runs as the shell user, which already
holds `READ_SMS` on that device.

## 5. haismart_tools.py / haismart_setup.py — AC control, current real state

**What it is.** Control of a Haier AC ("Haismart" / Haier U+ / uHome / SE-Asia
branding) entirely over the LAN — `TCP :56800`, AES-encrypted "uSS"/HRDP
framing — with no cloud round-trip per command. The protocol client itself
(`Core/tools/haismart/vendor/`) is **vendored third-party code, not
hand-written** — this doc's scope explicitly excludes reading it in full; the
two files reviewed (`haismart_tools.py`, `haismart_setup.py`) are FRED's own
glue on top of it.

**One-time setup (`haismart_setup.py`, run by hand, not registered as a
tool):**

```
cd Core && python tools/haismart_setup.py
```

Steps, in order: (1) prompt for the Haier account email/phone, password
(hidden via `getpass`, never a CLI arg), and the **account's registration**
country dialling code (explicitly NOT where the AC is installed); (2) sign
in once via `HaierCloud.login` (using the `requests` library injected as the
cloud transport, run in a worker thread since `requests` is sync and the
login/device-list calls are async — chosen specifically to avoid adding
`httpx` as a second HTTP dependency); (3) list appliances and fetch each
one's `localKey` over the cloud MQTT gateway (`GatewayClient.get_localkeys`)
— the one credential a LAN-only client needs and can get no other way; (4)
UDP-broadcast on `:7083` (`hrdp.discover`) to find each appliance's current
LAN IP, then open a real TCP connection and confirm the fetched key actually
decrypts a live status read (`_confirm_local`) — so success is never reported
on a key that silently fails; (5) write `device_id` + `local_key` + `host` +
`type_id` to `Core/data/haismart_devices.json` (gitignored, same handling as
`phone_tokens.json`).

**Known recent real bugs, confirmed by the git log and the code itself:**

- *"Validate haismart_setup.py's region prompt is a dialling code, not a
  country name"* (recent commit). The script's region prompt now explicitly
  rejects non-digit input with `region.isdigit()`, because entering the
  country NAME ("india") instead of its dialling code ("91") was **silently
  accepted by both this script and the server at login** — `zoneInfo` just
  scopes every subsequent call (including the device list) to an
  unrecognized region, which looks *exactly* like "this account has no
  appliances" for an account that has one. This was confirmed live 2026-08-20
  as a real false negative.
- *"Print list_devices_v2's raw response, not just 'no appliances'"* (most
  recent commit). Confirmed live 2026-08-20: **even after fixing the region
  bug**, `client.list_devices_v2()` still returned an empty device list for a
  real account with a real, actively-controlled AC. The script now, on an
  empty result, calls `client.get(client.domains.uhome, DEVICE_LIST_PATH_V2)`
  directly (a public method the vendored class already exposes — not an edit
  to the vendored file) to print the server's raw `retCode`/`retInfo`/`data`
  exactly as sent, then also tries the other two device-list paths the
  vendored library exposes (`list_user_devices`, `list_devices`) and prints
  their raw responses too — the vendored library's own docstrings mark those
  two paths' response shape as "to confirm on first call," so rather than
  guess a parser that might also silently show zero, the script prints raw
  JSON for a human to read.
- **Net effect: this integration's device-discovery path is currently in a
  known-unreliable state.** The setup script as of this reading explicitly
  says, on that failure path: *"This account has no appliances via
  list_devices_v2, and the two fallback paths' response shape isn't parsed
  yet... this needs a human to look at that JSON and confirm whether the AC
  is actually listed under a different key/shape before this script can
  proceed automatically."* Whether a *fresh* run (post both fixes) actually
  reaches the AC and writes `haismart_devices.json` successfully was **not
  confirmed by anything in the code itself** — the module's own header says
  plainly that the setup "wasn't run as part of building this file... see the
  printed output for whether it actually reached your AC." Do not assume this
  integration currently works end-to-end; treat `get_ac_status`/`set_ac_*` as
  functional against the vendored protocol client but contingent on
  `haismart_devices.json` actually existing and holding a confirmed-live key.

**Runtime behavior once set up (`haismart_tools.py`):** `_pick_device` — with
exactly one AC on file (the only case that currently exists, 2026-08-20),
`device` name is accepted as a parameter for forward-compat but ignored.
`_resolve_host` tries the cached IP first (a ~1.5s direct `hrdp.query`
liveness check) before paying for a fresh ~3s UDP broadcast
(`_DISCOVER_TIMEOUT = 3.0`), because full discovery on every voice command
would be too slow. Writes (`_write_field` / `set_ac_*`) are read-modify-write:
they read the AC's current full status first (`grsetdac_baseline_from_status`)
so changing one field (e.g. `targetTemperature`) never clobbers the AC's other
current settings; `counter=1` on every call, matching the upstream Home
Assistant integration this was modelled on, because the counter is a
per-session sequence, not a persisted one. Temperature wire encoding is
`celsius - 16` (the vendored encoder's own convention).

## 6. http_shortcuts_setup.py — what it provisions

Not a chat tool (not registered in the orchestrator at all) — a one-time
provisioning script (`Core/venv/Scripts/python.exe
Core/tools/http_shortcuts_setup.py`) that builds an **HTTP Shortcuts**
(Waboodoo, Android app `ch.rmy.android.http_shortcuts`) import file wired to
`Core/web/phone_api.py`'s `POST /command` endpoint, and pushes it onto the
paired phone. It generates four shortcuts: "Ask FRED" (free-text prompt
variable), "What's on my screen?", "Find my OTP" (exact phrase agreed with
`otp_tools.find_otp`'s intent-routing), and "Set an alarm" (prompts for a
time variable).

**How the file physically reaches the phone:** HTTP Shortcuts has no reliable
"import this local file" adb intent — its only file-shaped intent filters
need either a `content://` URI with a permission grant this setup has no
provider for, or a documented deep link
(`https://http-shortcuts.rmy.ch/import?url=<URL>`) that makes the app fetch
the export JSON itself over HTTP. The script serves the JSON from a
127.0.0.1-only `ThreadingHTTPServer` and uses `adb reverse` to tunnel a port
on the phone's own loopback to that PC-local port — no LAN traffic, no
Windows Firewall inbound rule needed (which would otherwise require admin
elevation this shell doesn't have). The shortcuts themselves, once imported,
target the PC's real LAN IP (not the loopback tunnel, which only exists
during this script's run) since they need to work independently afterward,
wired or wireless. Import confirmation is automated via a `uiautomator` UI
dump + tap-detection for the dialog's "OK" button (recent fix: the URL field
auto-focuses and pops the keyboard on open, which used to cause a mis-tap on
the URL text field instead of the actual button — now dismissed with
`KEYCODE_BACK` first). Schema constants (`version=91`,
`compatibilityVersion=90`) are hardcoded to HTTP Shortcuts 4.7.0's
`ImportExport.kt` values, confirmed against the phone's installed version —
a future app update would need these bumped to match.

## 7. found_cache.py / file_index.py / smart_search.py — fuzzy file finding

This is the "Deep" tier system `config/settings.py` references for
`find_file_smart`.

- **`found_cache.py`** — a small persistent JSON cache
  (`DATA_DIR/found_files.json`) keyed on `(directory, query)`, so a repeated
  `search_files`/`find_file_smart` call skips the walk entirely. Every cached
  path is re-verified with `Path.exists()` before being trusted (a full
  `os.path.exists()` per path, cheap versus a directory walk), and a
  *partially* stale hit (any one path gone) discards the whole entry rather
  than returning a trimmed result — "some of these moved" is treated as a
  signal the whole entry may be stale. A reserved key
  (`__last_results__`/`_LAST_KEY`) tracks the most recent search's paths
  specifically so a follow-up "open it" has something concrete to act on
  (search results are deliberately spoken without paths, per the no-file-
  paths-aloud persona rule).
- **`file_index.py`** — a maintained SQLite index
  (`DATA_DIR/file_index.db`, table `files(path, name, mtime, size)`,
  indexed on `name`) standing in for a live `os.walk` on every search.
  `reindex_drive(directory)` does the slow walk once (reusing
  `machine_tools._walk_pruned`, the same heavy-directory skip list as
  `search_files`) and writes a full snapshot; deliberately **never
  auto-refreshes** — only rebuilds on an explicit "reindex my drive."
  `add_entry`/`remove_entry` are best-effort incremental updates called
  right after `create_text_file`/`create_folder`/`delete_file` succeed, so a
  just-created path is findable via `search_index` before the next full
  reindex.
- **`smart_search.py`** — the actual "Deep"/agentic search:
  `find_file_smart(description, directory, llm)` walks the folder tree the
  way a person would, one LLM decision at a time (`ENTER: <subfolder>` /
  `FOUND: <file>` / `NONE`), capped at `MAX_STEPS = 4`. Deliberately its own
  small reasoning loop rather than a trip through the orchestrator's normal
  one-shot tool-calling round, because the folder-navigation reasoning has to
  happen across several sequential steps *before* any final answer is
  produced. **Runs on the RESIDENT model tier, not Deep** — despite the
  header comment's own historical name for this system — because
  `llm_client._get_model` keeps only one tier resident at a time (VRAM
  constraint), so requesting Deep here would evict whatever's loaded, run,
  and then force a reload for the very next turn; a per-step multiple-choice
  decision doesn't need the stronger tier. `_STEP_MAX_TOKENS = 512` — not
  because the answer is long, but because Standard runs with thinking
  enabled and a tight cap gets consumed entirely inside `<think>`, leaving no
  budget for the actual `ENTER:`/`FOUND:` line.

## 8. Destructive / confirmation-required tools

Cross-referencing every `destructive=True` in `orchestrator.py`'s
`_register_tools()` against the module files read for this doc — **10 tools
total**, one more than README.md's own list of 9 (see §10):

| Tool | Implemented in |
|---|---|
| `close_window` | `machine_tools.close_window` |
| `kill_process` | `machine_tools.kill_process` |
| `delete_file` | `machine_tools.delete_file` |
| `power_action` | `assist_tools.power_action` |
| `restart_fred` | `machine_tools.restart_fred` |
| `call_phone` | `phone_tools.call_phone` |
| `delete_agenda_item` | `agenda.delete_item` |
| `send_message` | `whatsapp_tools.send_message` |
| `set_contact_tier` | `whatsapp_tools.set_contact_tier` |
| `find_otp` | `otp_tools.find_otp` — **not mentioned in README's destructive list at all** |

The gate itself lives in the orchestrator's tool-calling loop (not in
`ToolRegistry`, and not in these functions) — every function in this list
"always just does the thing when called" per `machine_tools.py`'s own header
comment; the confirmation prompt is built and shown one layer up, before
`execute()` is ever reached. `call_phone` specifically resolves the contact
name to a real number (`resolve_target`) *before* the confirmation is shown,
so what the user is asked to confirm is the number that actually gets
dialled. `kill_process`'s confirmation preview uses the separate
`matching_processes()` helper so the actual target list (not just the raw
argument) is shown before anything is killed. FRED halts an entire batch of
tool calls the moment it sees one destructive call in it, so a confirmation
can't be used to smuggle a second, unconfirmed action alongside it.

## 9. sleep_mode_tools.py

One tool, `cancel_sleep_mode()`. The state machine itself
(`orchestrator/sleep_mode.py`) is out of this document's scope — covered
elsewhere — but the tool-level surface this file adds is exactly one thing:
a third, explicit way out of sleep mode, for when the other two (presence
returning, a hotkey press) don't fire — e.g. Vatsal is in frame but the
camera missed a match. Calls `sleep_mode.is_sleeping()` /
`sleep_mode.wake("cancel_command")`.

## 10. self_docs.py / session_summary.py — how FRED answers questions about itself

Two distinct, complementary systems, both registered:

- **`describe_self`** (`system_tools.describe_self`) answers "what tools do
  you have / what model are you running" from **live runtime state**: the
  actual registered tool count and a 6-name sample (from
  `self.tools.list_tools()`, passed in as a closure), and
  `config.settings.DEFAULT_TIER`/`MODEL_TIERS`. This can never drift from
  reality because it reads the running registry directly.
- **`ask_about_myself`** (`self_docs.ask_about_myself`) answers "what can you
  do / how does X work / why was it built this way" from FRED's **own project
  documentation**, indexed via a dedicated `VaultRouter` instance
  (`orchestrator/vault_router.py`, reused rather than a second retrieval
  implementation — "it already does chunking, per-file content-hash cache
  invalidation, corpus centering and top-K, all tuned against real measured
  failures"). Corpus is `config.settings.DOCS_FILES`:
  `README.md`, `SETUP.md`, `PHONE.md`, `MVP Plan (v1.0 - v1.1).txt`, `Phases
  11 - 20 (JARVIS Roadmap).txt` — read from `DOCS_DIR = BASE_DIR.parent`
  (the project root), indexed at `DOCS_INDEX_PATH`
  (`Core/data/indexes/docs_chunks.json`). This is explicitly **not** the
  vault (`VAULT_DIR`, Vatsal's personal memory, outside the repo, with its
  own always-on retriever) — the two corpora are kept structurally separate.
  `ask_about_myself` returns quoted excerpts (capped at `_EXCERPT_CHARS =
  900` per hit) labelled with their source file, not a synthesized answer —
  "the failure this exists to fix is FRED inventing capabilities, and a
  quoted section with its filename is what makes an answer checkable." The
  system prompt wrapped around the excerpts explicitly instructs the model
  to say "the docs don't cover it" rather than fill a gap. Missing files
  degrade silently (`iter_doc_files` skips non-existent entries) rather than
  breaking the whole tool.

`session_summary.py` is a different, separate system — daily activity
recap, not self-description:

- `collect_today`/`transcript` read directly from the session **event logs**
  (`utils/event_log.py`'s `SESSION_DIR`), never from the model's own memory
  of the conversation, specifically so a summary survives FRED being
  restarted mid-day and reflects sessions that have since ended.
- `summarise_today` → registered as `summarise_today`, which calls
  `preview_session_summary` — **propose-only**: builds and shows the recap
  text plus exactly where it would be saved, writes nothing. This follows
  the *stricter* of two possible vault-write conventions on purpose, even
  though `rules.md` technically allows `daily/` to be session-editable,
  because the same file also names real people and projects.
- `save_today_summary` → `save_session_summary`, reached only after explicit
  user confirmation of the preview, logs into **today's** auto-created vault
  session block (`start_daily_session`, called once per calendar day from
  `fred_popup.py`'s startup path, idempotent via an HTML-comment marker
  `<!-- fred-session:{day} -->`) rather than appending a new scattered
  top-level heading per save.
- `recall_recent_conversation` → registered directly, no propose/confirm
  step (read-only) — verbatim recent lines (`Vatsal: ...` / `FRED: ...` /
  `[tool: ...]`), fillers dropped, from the same event log.

## 11. README.md vs. reality — discrepancies found

Comparing README.md's "What FRED can do" table (lines 41–69) against the
actual `_register_tools()` contents:

- **Tool count.** README says "~80 registered tools." The actual count is
  **91** `self.tools.register()` calls in `orchestrator.py`. Rounded/stale,
  not wrong in kind.
- **Haismart AC control has no category row at all** in README's table —
  five real tools (`get_ac_status`, `set_ac_power`, `set_ac_temperature`,
  `set_ac_mode`, `set_ac_fan_speed`) are unlisted. Likely added after the
  table was last updated.
- **Destructive tools list is missing `find_otp`.** README names 9
  (`close_window`, `kill_process`, `delete_file`, `power_action`,
  `restart_fred`, `call_phone`, `delete_agenda_item`, `send_message`,
  `set_contact_tier`); the registry has a 10th, `find_otp`, also
  `destructive=True`.
- **Phone category in README omits `set_alarm`, `get_call_log`, `use_phone`,
  and camera capture** (only "call by name or number, hang up, sync
  contacts" are named) — all four are real registered tools.
- **File-index tools (`reindex_drive`, `search_index`) and `convert_file`/
  `print_file` aren't called out** in README's "Files" row, which only lists
  "create, append, read, list, search (incl. fuzzy `find_file_smart`), move,
  rename, delete."
- **`open_last_found`** isn't mentioned under Apps/Files despite being a
  registered tool.

None of these are functional bugs — they're README narration lagging behind
the actual registry, consistent with a project whose `_register_tools()`
block has clearly grown incrementally (phone integration, WhatsApp, Haismart
AC, and self-docs were all added within the 2026-08 session history visible
in the file's own comments) faster than its top-level README summary was
revisited.

## 12. Files read for this document

`Core/tools/registry.py`, `Core/tools/__init__.py` (empty), `agenda.py`,
`assist_tools.py`, `daily_tasks.py`, `file_index.py`, `found_cache.py`,
`git_tools.py`, `haismart_setup.py`, `haismart_tools.py`,
`http_shortcuts_setup.py`, `machine_tools.py`, `otp_tools.py`,
`phone_tools.py`, `self_docs.py`, `session_summary.py`,
`sleep_mode_tools.py`, `smart_search.py`, `system_tools.py`,
`vault_files.py`, `vision_tools.py`, `web_tools.py`, `whatsapp_tools.py`,
`workout_plan.py`, plus `Core/orchestrator/orchestrator.py`'s
`_register_tools()` (lines ~789–2394) as the ground truth for names,
parameters, and the `destructive` flag, and `README.md` for the discrepancy
check in §11. `Core/tools/haismart/vendor/` was deliberately not read in
full per this task's scope (vendored third-party LAN protocol code) — only
skimmed via its usage in `haismart_tools.py`/`haismart_setup.py`.
