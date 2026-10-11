# Memory folder system, revamp design (2026-10-11)

Status: DESIGN ONLY. Nothing moved, nothing coded. Vatsal will build it.
Asked for: granularity year / month / date, a daily log inside each date folder, some things outside the time granularity and some inside, designed with logic and modularity.

## What exists today (so the revamp starts from facts)
- `memory_root()` (config.py) is `Mark_I\Initial\Testing_Memory` while `memory_testing` is on, else `FRED_Private\memory`. It holds `memory.sqlite` (the whole database), `archive\YYYY\MM\DD\<stream>.jsonl` (already year/month/day, streams conversation, tool_call, speech, perception, error, system, session_state) and `jobs_calls.jsonl`.
- The same year/month/day idea is repeated, differently, in other places: `FRED_Private\events\YYYY-MM-DD.jsonl` (flat), `FRED_Private\news\YYYY\MM\DD.json`, `Project_FRED\Data\{clock,stt,wakeword}\YYYY\MM\DD\` (clips), `FRED_Private\voice_data`, `logs`, `usage`.
- Everything derived (diary, events, facts, decisions, screen visits) lives only inside `memory.sqlite`, so there is no day folder you can open and read, copy, back up or delete on its own.
- Personal state that is not about a day (voice_refs, face_refs, schedule, lockdown, apps_index, yearly_alerts_seed, deadlines, news_offered) is scattered in the FRED_Private root.

## The principle that decides every case
Ask one question of a piece of data: **"is it a record of something that happened at a time, or is it the current state of something?"**
- Happened at a time, never changes afterwards -> goes in the **time tree**, in the folder of its day.
- Current state that changes in place, or a global index over everything -> goes **outside** the time tree, in its own module folder.
- Derived from the time tree and rebuildable (indexes, embeddings, caches) -> outside, and safe to delete.
Truth lives in files you can read; the SQLite database is the fast index and the home of state history, and it can be rebuilt from the day folders. (Today the database is the only copy of the derived data; the revamp makes the day folder the source of truth for the day's records.)

## The layout (root = `memory_root()`)
```
<root>\
  layout.json                       version of this layout + the lane registry (see "Modularity")
  README.md                         one page: the rules below
  time\                             THE LEDGER: sharded by year / month / date, India day (IST)
    2026\
      year.md                       yearly review (written once a year)
      01\ ... 10\
        month.md                    monthly review (written on the 1st for the month before)
        weeks\W41.md                weekly analysis, filed in the month in which the week ENDS
        11\                         one folder per day
          daily-log.md              the daily log: the readable diary of the day (written by the nightly editor, short on boring days)
          day.json                  manifest: counts per file, sha256 per file, sealed true/false, sealed_at, model/versions used
          raw\                      the shoebox, append-only, never edited
            conversation.jsonl  tool_call.jsonl  speech.jsonl  perception.jsonl  error.jsonl  system.jsonl  session_state.jsonl
          screen\
            visits.jsonl            one line per screen visit (app, title, times, caption, <=1000 chars of text, seen count)
          derived\                  what the nightly job produced from that day (replayable, never edits raw)
            events.jsonl            the day's memory events (type, importance, text, entity tags)
            decisions.jsonl         every model prompt/reply/override/refusal of that day's jobs (today the decision_log table)
            jobs_calls.jsonl        model-call log of the day's jobs (today jobs_calls.jsonl in the memory root)
          news\
            digest-0600.json  digest-1800.json      the editions built that day, with the read/offered flags
          amendments.jsonl          any later correction or late import for this day: appended here, the day's files stay untouched
  signals\                          BULK data, same year/month/date sharding, NOT expired: cleaned only by an explicit clean command
    2026\10\11\
      usage\<component>\            clips + usage.jsonl per component: clock, stt, wakeword, audio, tts, camera, presence, screen, engine (today Project_FRED\Data)
      events\noise.jsonl            the event-bus log (VAD flickers, wake scores, clock states): today FRED_Private\events
      logs\session.jsonl  tools.jsonl   live session and tool logs: today FRED_Private\logs (flat files)
  state\                            CURRENT STATE, changes in place, history kept in rows (outside the time tree)
    memory.sqlite                   entities, entity_states (valid_from/to), relationships, event_entities, model_items, proposals,
                                    review_queue, ask_state, yearly_alerts, precedents, screen_visits index, sources
    people\  places\               one readable .md per person / place, written next to the database rows (both kept in step; the database stays the source for queries)
    files_index.sqlite              the file-search index (a state index, not a day record)
  identity\                         WHO VATSAL IS TO FRED (private, never sharded by time)
    voice_refs.json (+ .bak)  face_refs.json  yearly_anchor.json  yearly_alerts_seed.json
    voice_data\index.jsonl         voice-ID training clips and their index (today FRED_Privateoice_data)
  settings\                         small switches and per-module memory of choices
    screen_paused.flag  deadlines.json  news_offered.json  schedule.json  reminders.json  agenda.json
    lockdown.json  lockdown_state.json  lockdown_vectors.jsonl  apps_index.json  app_index.json  apps_usage.json  judge_model.json
    persona\                        persona files
  safety\                           undo and discard copies, never auto-cleaned
    email_drafts\  vault_undo\  trash\
  index\                            DERIVED, rebuildable, safe to delete
    <embed-model-id>\               one folder per embedding model so models never mix
      faiss.index                   only the FAISS cache is a file; vectors and FTS5 stay in memory.sqlite today
  imports\                          raw originals of historical imports, by source (their content is projected into time\ with kind="historical")
  backups\                          backup.py output: manifest.json with SHA-256 per file, one folder per backup date
  tmp\                              scratch, always safe to empty
```
Read it as six kinds of folder (safety holds undo copies): **time** (permanent ledger), **signals** (same shape, never auto-expired), **state** (current truth), **identity + settings** (small, personal, never sharded), **index + backups + tmp** (derived or copies).

## What goes where (decision table)
| Data | Folder | Why |
|---|---|---|
| a conversation turn, tool call, transcript, presence change, error | time\Y\M\D\raw\ | it happened at a time and never changes |
| screen visit text | time\Y\M\D\screen\visits.jsonl | a record of a time (text only; no pixels ever) |
| the diary of a day | time\Y\M\D\daily-log.md | derived from that day, readable |
| memory events of a day | time\Y\M\D\derived\events.jsonl (+ the database rows) | derived from that day |
| model prompts/replies of the nightly job | time\Y\M\D\derived\decisions.jsonl | audit of that day's decisions |
| news digest of a day | time\Y\M\D\news\ | an edition belongs to a day |
| weekly / monthly / yearly review | time\Y\M\weeks, month.md, year.md | belongs to a period, not a day |
| voice clips, noise event log | signals\Y\M\D\ | same sharding, kept until you run the clean command (no automatic expiry) |
| who knows whom, where Vatsal lives now, what FRED learned about him | state\memory.sqlite | current state with valid_from/valid_to history, changes in place |
| voice and face references, yearly anchor, alert seed | identity\ | personal, not time-bound, never deleted by retention |
| deadlines answered, news offered, pause flag, schedule, reminders, agenda, lockdown, persona | settings\ | per-module small state |
| voice-ID training clips | identityoice_data\ | who Vatsal sounds like, not a day record |
| email drafts, vault undo copies, trash | safety\ | reversal copies, never auto-deleted |
| session and tool logs, per-component usage clips | signals\Y\M\D\logs and usage\ | debug and tuning data, kept until cleaned |
| embeddings, FAISS, FTS | index\<model>\ | derived: delete and rebuild |
| historical chat imports | imports\ then projected into time\ | originals are kept untouched |

## The daily log (what you find in the 11th folder)
- `daily-log.md`: the readable diary. Header with date, weekday, and counts (turns, tool calls, screen visits, news read); the nightly editor's diary text; key events; deadlines seen and answered; open questions queued for you. A busy day is longer, a quiet day is two lines.
- `day.json`: machine manifest so a day can be verified, sealed and copied on its own.
- Until the nightly job runs the folder already has `raw\` and `screen\`, so "today" is browsable live; `daily-log.md` appears after consolidation and `sealed` flips to true.
- **Sealing:** after consolidation a day is sealed (hashes in day.json). A sealed day is never edited; corrections and late imports go to `amendments.jsonl` in that day and the next consolidation reads them.

## Rules (the logic)
1. **One clock:** the day is the India day (IST), as the archive already does. A session that crosses midnight writes each record into the day of its own timestamp.
2. **Append-only in `time\`**: files are only appended, repaired on a torn last line, or sealed. No module deletes from `time\`.
3. **Nothing expires by itself.** `signals\` has no automatic deletion; it is the only tree a clean command may delete from, and `tmp\` is always safe to empty. The ledger is never deleted. Clips and noise stay OUT of `time\` because their bulk and their delete-on-request lifetime differ, so they get a sibling tree with the same shape.
4. **Rebuildability:** `index\` can always be rebuilt from `time\` + `state\`. `state\memory.sqlite` can be rebuilt as far as events go from `time\...\derived\events.jsonl` (entity states carry their own history and are backed up).
5. **Personal data never in the repo:** `memory_root()` stays outside git (as today: Testing_Memory is gitignored).
6. **Folder names are zero-padded** (2026, 10, 11) so plain sorting is chronological.

## Modularity: one place knows the paths
- A single module `layout.py` (socket) is the only code that builds paths: `day_dir(ts)`, `raw_file(ts, stream)`, `screen_file(ts)`, `derived_file(ts, name)`, `signals_dir(ts, kind)`, `period_file(kind, ts)`, `state_dir()`, `identity_file(name)`, `settings_file(name)`, `index_dir(model_id)`. Nothing else concatenates path strings.
- Each module declares a **lane** in `layout.json`: `{name, scope: day|period|state|identity|settings|index, path template, retention, writer, sealed_by}`. Adding a feature (for example a "health" log) means adding one lane, not touching the others.
- Every writer calls `layout`; readers ask `layout` plus a date range ("all raw conversation between two dates", "the daily logs of October"). Backup, verify and export work per lane.
- `layout.json` carries a `version`; a migration is a small idempotent script per version.

## Migration plan (when you build it)
1. Write `layout.py` and `layout.json`; point the current writers at it with the OLD paths first (no behaviour change, tests green).
2. Move `archive\YYYY\MM\DD\<stream>.jsonl` to `time\YYYY\MM\DD\raw\` (same files, one rename per day; `archive.verify()` before and after).
3. Add `day.json` and `daily-log.md` writers to the nightly job; add `screen\visits.jsonl` next to the database write.
4. Move news digests, then clips and noise (to `signals\`), then identity and settings files out of the FRED_Private root. Each step keeps a read fallback to the old path until the next version.
5. Export derived events and decisions to the day's `derived\` and make the database the index of them.
6. The clean command for `signals\` last (see "Clean mechanism").

## Clean mechanism (for signals\ only)
- One command, `clean(lane, before=None, older_than_days=None, component=None, dry_run=True)`; the default is a dry run that lists what would go and how many MB.
- It only touches `signals\`, `tmp\` and `safety	rash\`, never `time\`, `state\`, `identity\`. It refuses a path outside those, and writes what it removed (count, size, range) to `time\<today>aw\system.jsonl`.
- Runs only when you ask (voice or CLI); nothing schedules it. A day folder holding a clip you flagged (a `keep` marker file) is skipped.

## Audit changes (2026-10-11)
Checked against what the code writes today. Added: the other usage components, session and tool logs, voice_data, safety copies, reminders/agenda/lockdown/persona/app-usage settings, files_index, jobs_calls. Removed as speculative or duplicating the database: timeline.jsonl, vectors\screen.npy, people\ and places\ folders, vectors.sqlite and fts.sqlite in index.

## Decisions (Vatsal, 2026-10-11)
1. Clips and noise do NOT expire; a clean mechanism deletes on request (above).
2. Screen text is a file (`screenisits.jsonl`) and a database row.
3. Weekly files go in the month where the week ENDS.
4. People and places: readable files AND database rows.
5. `daily-log.md` is created at midnight with live counts and completed after the nightly job.
