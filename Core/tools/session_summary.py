# Core/tools/session_summary.py
#
# Suggestion #3 from the 2026-08-01 feedback session, steps 1 and 2
# only: summarise what today's sessions actually contained, and offer to
# append it to the vault's daily note. The process-closing and
# PC-shutdown steps are deliberately NOT here — those were deferred, and
# the shutdown itself keeps a confirmation gate regardless.
#
# The summary is built from the session event logs (utils/event_log.py),
# not from the model's memory of the conversation: the logs are what
# actually happened, including turns from sessions that have since been
# restarted, and they survive FRED being killed mid-day.
#
# Writing to the vault is PROPOSE-ONLY. rules.md requires daily/ edits
# to be shown before they are made... and in fact allows daily/ to be
# session-editable, but the same file names people and projects, so this
# follows the stricter convention: build the text, show it, and only
# write when explicitly told to. save_session_summary is the separate,
# explicit second step.

import json
from datetime import datetime
from pathlib import Path

from config.settings import VAULT_DIR
# Reused rather than rebuilt from LOG_DIR — event_log owns where session
# logs live, and a second copy of that path is exactly the drift
# rules.md warns about.
from utils.event_log import SESSION_DIR as SESSIONS_DIR

# Turn types worth summarising. Fillers are excluded — they carry no
# information and would triple the length.
_USER = "user_speech"
_FRED = "fred_speech"
_TOOL = "tool_call"


def _today_logs(day: str = None):
    """
    Every session log file for `day` (YYYY-MM-DD), oldest first.

    The glob is `session_{day}*.jsonl`, not `session_{day}_*.jsonl`: logs
    were consolidated to one file per day (event_log.py), and the trailing
    underscore only matched the pre-consolidation per-launch names. It
    silently matched nothing from then on, so summarise_today answered
    "Nothing logged today yet, sir." every single day regardless of how
    much had happened. No error, no empty file — just a glob that stopped
    matching. Keeping the `*` covers any unmerged legacy file too.
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob(f"session_{day}*.jsonl"))


def _read_events(paths):
    events = []
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return events


def collect_today(day: str = None) -> dict:
    """
    Raw material for a summary: what was asked, what tools ran, and how
    much of it there was. Returns counts plus the actual utterances, so
    a caller can either summarise with the LLM or render it directly.
    """
    events = _read_events(_today_logs(day))

    asks = [
        e.get("text", "").strip()
        for e in events
        if e.get("type") == _USER and e.get("text", "").strip()
    ]
    # Fillers are logged as fred_speech with filler=True.
    replies = [
        e for e in events
        if e.get("type") == _FRED and not e.get("filler")
    ]
    tools = [e.get("tool") for e in events if e.get("type") == _TOOL and e.get("tool")]

    tool_counts = {}
    for name in tools:
        tool_counts[name] = tool_counts.get(name, 0) + 1

    interrupted = sum(1 for r in replies if r.get("interrupted"))

    return {
        "day": day or datetime.now().strftime("%Y-%m-%d"),
        # One file per day now, so counting files would always say "1
        # session" however many times FRED was restarted. The start marker
        # is what actually counts a session.
        "sessions": sum(1 for e in events if e.get("note") == "session start") or 1,
        "asks": asks,
        "reply_count": len(replies),
        "interrupted": interrupted,
        "tools": tool_counts,
    }


def transcript(day: str = None, limit: int = 200) -> str:
    """
    The day's conversation in order — both sides, plus which tools ran
    between them. collect_today splits the turns into separate lists and
    throws the reply text away, which is fine for a recap but useless as
    context: "did you finish the journal?" and the answer to it only mean
    something adjacent to each other.

    Fillers are dropped (they carry no information) and the LAST `limit`
    turns are kept, not the first — a long day's context is its end.
    """
    lines = []
    for e in _read_events(_today_logs(day)):
        kind = e.get("type")
        text = e.get("text", "").strip()
        if kind == _USER and text:
            lines.append(f"Vatsal: {text}")
        elif kind == _FRED and text and not e.get("filler"):
            lines.append(f"FRED: {text}")
        elif kind == _TOOL and e.get("tool"):
            lines.append(f"[tool: {e['tool']}]")

    return "\n".join(lines[-limit:])


def recall_recent_conversation(count: int = 20) -> str:
    """
    What was actually said recently, verbatim — not a theme-grouped
    summary like summarise_today, and not semantic memory search (which
    handles a vague query like "what did we just talk about" poorly,
    since it has no strong content of its own to match against).

    Reads from today's session log via transcript(), not in-memory turn
    history — this is what makes it survive FRED being restarted mid-
    conversation, which the orchestrator's own short-term
    ConversationState (reset to empty on every launch) does not.
    """
    text = transcript(limit=count)
    return text or "Nothing logged in today's session yet, sir."


if __name__ == "__main__":
    # Self-check, not Core/tests/ (regression-only per its README — this
    # is new logic, not a pinned bug). Same SESSIONS_DIR-swap approach
    # test_session_summary_logs.py already uses for this module.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        today = datetime.now().strftime("%Y-%m-%d")
        (Path(tmp) / f"session_{today}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in [
                {"type": "user_speech", "text": "what's the weather tomorrow"},
                {"type": "fred_speech", "text": "Sunny, high of 30.", "filler": False},
                {"type": "fred_speech", "text": "thinking...", "filler": True},
            ]),
            encoding="utf-8",
        )

        globals()["SESSIONS_DIR"] = Path(tmp)
        out = recall_recent_conversation(count=20)
        assert "Vatsal: what's the weather tomorrow" in out, out
        assert "FRED: Sunny, high of 30." in out, out
        assert "thinking..." not in out, out  # fillers dropped

        globals()["SESSIONS_DIR"] = Path(tmp) / "empty_dir_that_does_not_exist"
        assert recall_recent_conversation() == "Nothing logged in today's session yet, sir."

    print("session_summary.recall_recent_conversation self-check: all passed")


def summarise_today(day: str = None, llm=None, existing_note: str = None) -> str:
    """
    A spoken-length recap of the day. With an `llm` handle it writes
    real prose from the day's actual requests; without one it falls back
    to counts, which is still true and still useful.

    existing_note: the target daily note's current content, if any —
    passed by save_session_summary (which is about to write into that
    same file) so the model can avoid re-describing what's already
    logged there instead of writing blind. Purely additive context;
    omit it and behavior is unchanged.
    """
    data = collect_today(day)

    if not data["asks"]:
        return "Nothing logged today yet, sir."

    top_tools = sorted(data["tools"].items(), key=lambda kv: -kv[1])[:5]
    tools_text = ", ".join(f"{name} ({count})" for name, count in top_tools) or "none"

    if llm is None:
        return (
            f"{len(data['asks'])} request(s) across {data['sessions']} session(s) "
            f"today. Tools used: {tools_text}."
        )

    asked = "\n".join(f"- {a}" for a in data["asks"][:40])
    prompt = [
        {
            "role": "system",
            "content": (
                "Summarise this list of things the user asked their "
                "assistant today. Three to five short bullet points, "
                "grouped by theme, describing what he was working on. "
                "No preamble, no closing offer. Do not invent anything "
                "that isn't in the list. Do not just restate tool names "
                "or counts — describe what he was actually doing. If "
                "EXISTING NOTE CONTENT is given below, don't repeat what "
                "it already covers — describe only what's new."
            ),
        },
        # Deliberately NOT including tools_text (the "X (n), Y (n)" tool
        # tally) here — confirmed 2026-08-21 that handing it to a local
        # model alongside the asks list invites it to just echo the tool
        # tally back verbatim as its "summary" instead of writing real
        # prose from the asks, exactly the dumb output this prompt is
        # supposed to avoid. tools_text is still used below in the
        # no-llm fallback, where a bare tally is the honest, intended
        # output rather than a degraded one.
        {
            "role": "user",
            "content": (
                f"Requests today:\n{asked}"
                + (f"\n\nExisting note content:\n{existing_note}" if existing_note else "")
            ),
        },
    ]

    try:
        # local_only=True — same as _judge_carryover in proactive_checks.py
        # (check_day_rollover), and for the same reason: this reads raw
        # conversation/session content, unattended, and that shouldn't
        # leave the device on its own.
        return llm.generate(prompt, local_only=True)
    except Exception as e:
        return f"Couldn't summarise today: {e}"


def _daily_note_path(day: str = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    month = day[:7]
    return VAULT_DIR / "daily" / month / f"{day}.md"


_AUTO_SESSION_HEADING = "## FRED session — "


def _auto_session_marker(day: str) -> str:
    return f"<!-- fred-session:{day} -->"


def start_daily_session(day: str = None) -> str:
    """
    Auto-create today's vault session block, once per calendar day.

    Called from fred_popup.py right after event_log.start_session() —
    same one-per-day-not-per-launch shape as that module's own session
    file (see event_log.py's docstring on why: a relaunch later the same
    day should resume, not fork a new record). The marker comment is
    what makes this idempotent without a separate state file: a second
    launch the same day finds it already in the note and does nothing.

    Returns a short line to fold into the startup greeting (empty string
    if today's session already existed — nothing new to announce).
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    path = _daily_note_path(day)
    marker = _auto_session_marker(day)

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        return ""

    stamp = datetime.now().strftime("%H:%M")
    block = (
        f"\n{marker}\n"
        f"{_AUTO_SESSION_HEADING}{stamp}\n\n"
        f"### What Got Done\n-\n\n"
        f"### What's Still In Progress\n-\n\n"
        f"### Decisions Made\n-\n\n"
        f"### Notes Touched\n-\n\n"
        f"### Profile Updates\n-\n"
    )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
        else:
            header = (
                f"---\ntype: log\nstatus: active\nupdated: {day}\n---\n\n"
                f"# {datetime.strptime(day, '%Y-%m-%d').strftime('%A, %B %d, %Y')}\n"
            )
            path.write_text(header + block, encoding="utf-8")
        return "Today's vault session is up, sir."
    except OSError as e:
        print(f"[session_summary] couldn't start today's vault session: {e}")
        return ""


def preview_session_summary(day: str = None, llm=None) -> str:
    """
    Build the summary and say exactly where it WOULD be written,
    without writing it. This is the half that runs by default —
    save_session_summary is a separate, explicit call.
    """
    summary = summarise_today(day, llm=llm)
    path = _daily_note_path(day)
    status = "appending to" if path.exists() else "creating"

    return (
        f"{summary}\n\n"
        f"(Ready to save this: {status} {path.name}. Say save it to confirm.)"
    )


def save_session_summary(day: str = None, llm=None, summary: str = "", auto: bool = False) -> str:
    """
    Log the summary into *today's* auto-created vault session block
    (start_daily_session) rather than appending a separate top-level
    block — one place per day for everything FRED logs, not a scattered
    "## FRED session recap" per save.

    auto: True when called unattended from consolidation.on_sleep_enter()
    (no spoken "save it" confirmation first, per Vatsal's 2026-08-22
    request) — tags the recap line with a short marker so it's clear
    later which entries were unattended vs. manually confirmed via the
    _save_today_summary tool (auto=False, its default).

    If `summary` isn't given, it's built here rather than by the caller
    — that lets this read the note's own EXISTING content first and
    hand it to summarise_today as context, so a summary written on a
    second sleep-mode cycle the same day doesn't blindly re-describe
    what an earlier cycle already logged.

    If today's session block doesn't exist yet for some reason (e.g.
    this is called from a context that skipped fred_popup.py's startup
    path), start_daily_session() creates it first so this never fails
    open into a stray top-level block.
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    path = _daily_note_path(day)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    text = summary or summarise_today(day, llm=llm, existing_note=existing)
    stamp = datetime.now().strftime("%H:%M")
    tag = " _(auto-logged by FRED)_" if auto else ""
    recap = f"\n**Recap — {stamp}:**{tag} {text}\n"
    marker = _auto_session_marker(day)

    try:
        if marker not in (path.read_text(encoding="utf-8") if path.exists() else ""):
            start_daily_session(day)

        content = path.read_text(encoding="utf-8")
        marker_start = content.find(marker)
        if marker_start == -1:
            # start_daily_session() just ensured it — shouldn't happen —
            # but fail open rather than lose the recap.
            with open(path, "a", encoding="utf-8") as f:
                f.write(recap)
            return f"Appended today's recap to {path.name} (session block not found)."

        marker_line_end = content.find("\n", marker_start) + 1
        heading_line_end = content.find("\n", marker_line_end) + 1
        new_content = content[:heading_line_end] + recap + content[heading_line_end:]
        path.write_text(new_content, encoding="utf-8")
        return f"Logged today's recap into the session block in {path.name}."
    except OSError as e:
        return f"Couldn't write the daily note: {e}"
