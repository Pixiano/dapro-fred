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


def summarise_today(day: str = None, llm=None) -> str:
    """
    A spoken-length recap of the day. With an `llm` handle it writes
    real prose from the day's actual requests; without one it falls back
    to counts, which is still true and still useful.
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
                "that isn't in the list."
            ),
        },
        {"role": "user", "content": f"Requests today:\n{asked}\n\nTools used: {tools_text}"},
    ]

    try:
        return llm.generate(prompt)
    except Exception as e:
        return f"Couldn't summarise today: {e}"


def _daily_note_path(day: str = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    month = day[:7]
    return VAULT_DIR / "daily" / month / f"{day}.md"


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


def save_session_summary(day: str = None, llm=None, summary: str = "") -> str:
    """
    Append the summary to the vault's daily note. Only ever called after
    an explicit confirmation — see preview_session_summary.
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    path = _daily_note_path(day)
    text = summary or summarise_today(day, llm=llm)

    stamp = datetime.now().strftime("%H:%M")
    block = f"\n\n## FRED session recap — {stamp}\n\n{text}\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
            return f"Appended today's recap to {path.name}."

        header = (
            f"---\ntype: log\nstatus: active\nupdated: {day}\n---\n\n"
            f"# {datetime.strptime(day, '%Y-%m-%d').strftime('%A, %B %d, %Y')}\n"
        )
        path.write_text(header + block, encoding="utf-8")
        return f"Created {path.name} with today's recap."
    except OSError as e:
        return f"Couldn't write the daily note: {e}"
