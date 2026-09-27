# 2026-09-27 — work log

- **v1.2 scope set** (`plan_v1.2_2026-09-27.md`): Gmail, phone messages,
  calls, file indexing first; credentials vault + AI-chat import
  secondary; everything else on hold.
- **Nightly file reindex**: `Core/scripts/nightly_reindex.py` +
  Windows task `FRED_NightlyReindex` (03:00) rebuilds the file index
  over all of VatsalDaPro — closes the "changed outside FRED" staleness.
- **Gmail moved to the real API** (`Core/tools/gmail_api.py`, OAuth
  client + token in `VatsalDaPro\Keys`, never in a repo). IMAP stays
  as a read-only fallback (`gmail_imap.search`).
- **Fixed: IMAP polling was marking mail read.** `FETCH (RFC822)` sets
  `\Seen`; since 2026-08-28 every polled Primary email got marked read.
  Now `BODY.PEEK[]` on a read-only `EXAMINE`.
- **SUPERSEDED: the 5-minute Gmail rescans** (`GMAIL_CHECK_MINUTES`, three
  jobs re-reading 7-14 days of mail). Replaced by a History API delta
  every minute (only what arrived since last time) + an hourly
  missed-reply check. An API outage now announces itself once instead
  of going silent.
- **New Gmail tools, all hardcoded, no LLM**: today's unread
  (`check_email`), Gmail-syntax `search_email`, `read_email`,
  `download_attachment` (confirm first, sanitised filenames),
  `draft_email` / `send_draft` (confirm shows the draft as it is in
  Gmail), `set_email_signature` (confirm first). Only a sent reply marks
  anything read. Every link in email text becomes
  `[REDACTED link to <domain>]` — FRED never opens links.
- **Gmail tier labels, tuned on 5 days of real mail**: every new
  Primary/Updates email gets `FRED/VVIP` / `FRED/Basic` / `FRED/Useless`
  (labels only, never archived or marked read). FRED no longer touches
  Social/Promotions/Forums at all. Fixes from the real-mail run: no-reply
  senders are never VVIP; missed replies count VVIP senders only; "due
  to" and bare "today" aren't deadlines. Hand overrides live in the
  vault (`people/email-tiers.md`). API calls now back off on Gmail's
  burst rate limit (hit live).
- **Push notifications not built**: Gmail's push needs Pub/Sub, and with
  no public endpoint FRED would still have to pull from that queue — no
  faster than the History delta, plus a topic, IAM grant, extra scope and
  a weekly watch renewal.
- **Documentation System Mark 2**: one folder per day, `docs/YYYY/MM/DD/`
  (Vatsal's call). Root `DONE.md` frozen as the Mark 1 log; today's entry
  moved here. Rules in `DOCUMENTATION_SYSTEM.md`.
- **Benchmark suite built** (`benchmarking/`): 211 hand-written cases in
  5 files + the 119-row real holdout + wake-word held-out sets; synthetic
  screenshot and Kokoro speech fixtures (`make_fixtures.py`); one runner
  (`run.py`) with a GPU-only VRAM guard, full isolation (tools recorded,
  never executed; memory/logs redirected), verbose per-case logging.
  Every case declares what it needs, so the same suite scores all five
  candidates; missing capabilities count as fails.
- **Current-FRED baseline**: routing 83% (real) / 80% (adversarial),
  safety gates 100%, STT 2.3% WER English but 74-82% Hinglish, wake word
  33% recall + ~40 false wakes/hour. LLM and vision stages blocked on
  VRAM (4.2 GB free, ~7.7 GB needed). Full table + 10 findings:
  `benchmark_baseline.md`.
- **Candidate 2 research** (`research_candidate2_multimodal.md`, Sonnet
  subagent + main-session addendum): Realtime-Venus-Omni is MiniCPM-o 4.5
  (Qwen3-8B + SigLIP2 + Whisper-medium) with only the LLM post-trained;
  MiniCPM5-2B would need every adapter trained from scratch (hidden size
  2048 matches no existing omni model). Milestone plan added.
- **Routing failure triage** (Haiku subagent): of 35 real routing
  failures, most are cue gaps / embedder misses; 3 dispatcher misfires;
  6-7 holdout rows are yes/no replies that need the previous turn (now
  skipped by the runner).
- **Researcher + General agents** (`Core/orchestrator/agents.py`, tools
  `delegate_research` / `delegate_general`): roles (prompt + hard tool
  allowlist) on the already-loaded model -- zero extra VRAM, zero agents
  unless FRED delegates, dismissed after each job. Researcher: read-only
  `web_search`, 4 rounds max, off-allowlist tools refused in code.
  General: no tools at all. Per-role `tier` field is the hot-swap hook for
  future fine-tuned weights. 5 tests (`test_agents.py`), 637 total pass.
  Delegation routing 8/8 in the benchmark; live agent runs blocked on VRAM
  like the rest of the LLM stage.
- **LLM + vision baseline run** (FRED stopped at Vatsal's request to free
  VRAM): tools end to end 77.8%, injection 85.7%, conversation 76.2%,
  delegation decisions 62.5%, memory 75%, vision 12/12. Two harness bugs
  found and fixed mid-run. Added to `benchmark_baseline.md`.
- **Realtime-Venus HF survey**: official Omni/Audio are BF16 ~18.7 GB (no
  official quant); only community quant is an abliterated NVFP4 build
  (proven realtime on an RTX 5060 Ti). Decision: test the official model
  with our own on-the-fly quantization. Nothing downloaded yet.
- **Handoff written**: `handoff_realtime_venus.md` (plan, rules, facts,
  VRAM options, next steps).
- **Committed and pushed** `8528f54` (benchmark, agents, daily docs),
  `17fcc4e` (pending August work: wake-word AGC fix, plans, notes),
  `5d1a7d2` (README rewritten for the current all-local FRED). Three
  older notes with real share passwords stay local and are now
  gitignored, as are six runtime state files in `Core/data/`.
