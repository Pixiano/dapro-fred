# Core/tools/workout_plan.py
#
# Reads the training split out of the vault's workout PDF and turns it
# into recurring reminders, one per training day, labelled with the
# muscles that day actually targets.
#
# Why parse the PDF rather than hardcode the split: it is the artefact
# Vatsal maintains (personal/workout_split_June.pdf — the "June" in the
# name says it gets revised), and personal/fitness.md explicitly records
# that he "plans and logs actual workout structure, not casual
# exercise". Hardcoding "Monday is legs" would be correct today and
# quietly wrong the first time he rewrites the split, with nothing to
# catch the drift. Reading the file means re-running this after an edit
# picks the change up.
#
# SENSITIVE: this reads personal/, which rules.md forbids sending to any
# hosted model. Everything here is plain regex over local text — no LLM
# call at any point — so the content never leaves the machine. Keep it
# that way; the moment this needs a model it needs the local-only path
# (see utils/sensitive.py).

import re

from config.settings import VAULT_DIR

# pypdf emits the schedule table one CELL per line, not one row per line
# — verified against the real personal/workout_split_June.pdf, which
# extracts as:
#
#     MON\n TUE\n WED\n THU\n FRI\n SAT\n SUN\n
#     Legs\n Chest\n Back\n REST\n Shoulders\n Arms + Core\n REST\n
#
# So the parse is: find the seven consecutive day-name lines, then take
# the seven lines that follow as the focus cells. Do NOT "simplify" this
# to a single-line regex over "MON TUE WED..." — that is what the visual
# layout of the PDF looks like and it matches nothing in the extracted
# text. A cell may itself contain spaces ("Arms + Core"), which the
# one-cell-per-line shape handles for free.
_DAY_SEQUENCE = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_CRON_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Rest days are not reminded about — a 4:55pm "Workout - REST" alert
# every Thursday is noise, and the PDF is explicit that Thursday and
# Sunday are "Light walk or mobility only. No training."
_REST = {"rest", "off", "recovery", ""}

DEFAULT_PDF = "personal/workout_split_June.pdf"

# 4:55pm, five minutes before the 5pm session Vatsal stated he trains at
# `[stated 2026-08-04]` — a reminder at 5:00 arrives as the session
# starts, which is too late to act on.
DEFAULT_HOUR = 16
DEFAULT_MINUTE = 55


def _pdf_text(rel_path: str = DEFAULT_PDF) -> str:
    from pypdf import PdfReader

    path = VAULT_DIR / rel_path
    if not path.is_file():
        raise FileNotFoundError(f"No workout PDF at {rel_path}")

    reader = PdfReader(str(path))
    # Page 1 alone carries the schedule table; reading every page would
    # pull in the per-day detail sections, which repeat the day names.
    return reader.pages[0].extract_text() or ""


def parse_split(text: str) -> dict:
    """
    {"mon": "Legs", "tue": "Chest", ...} for TRAINING days only — rest
    days are dropped, not recorded as "REST".

    Returns {} if the schedule table isn't found, rather than guessing:
    a wrong split would schedule five confidently mislabelled reminders.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]

    # Find the run of seven consecutive day-name lines. Searching for the
    # whole run rather than a lone "MON" matters: the per-day sections
    # further down the document each start with their own day name
    # ("MON — DAY 1"), so the first "MON" is not necessarily the table.
    start = None
    for i in range(len(lines) - 13):
        window = [line.lower().rstrip(".") for line in lines[i:i + 7]]
        if window == list(_DAY_SEQUENCE):
            start = i
            break

    if start is None:
        return {}

    cells = lines[start + 7:start + 14]
    if len(cells) != 7:
        return {}

    split = {}
    for day, focus in zip(_CRON_DAYS, cells):
        cleaned = focus.strip()
        if cleaned.lower() in _REST:
            continue
        split[day] = cleaned

    return split


def get_split(rel_path: str = DEFAULT_PDF) -> dict:
    return parse_split(_pdf_text(rel_path))


def describe_split(rel_path: str = DEFAULT_PDF) -> str:
    """Spoken summary of the training week — for "what's my split?"."""
    try:
        split = get_split(rel_path)
    except Exception as e:
        return f"Couldn't read the workout plan: {e}"

    if not split:
        return "I couldn't find the schedule table in the workout plan."

    spoken = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
              "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
              "sun": "Sunday"}
    lines = [f"{spoken[day]}: {focus}" for day, focus in split.items()]
    rest = [spoken[d] for d in _CRON_DAYS if d not in split]
    summary = ", ".join(lines)
    if rest:
        summary += f". Rest on {' and '.join(rest)}"
    return summary + "."


def today_workout(rel_path: str = DEFAULT_PDF) -> str:
    """What today's session is — for "what am I training today?"."""
    from datetime import datetime

    try:
        split = get_split(rel_path)
    except Exception as e:
        return f"Couldn't read the workout plan: {e}"

    today = _CRON_DAYS[datetime.now().weekday()]
    focus = split.get(today)
    if not focus:
        return "Today's a rest day — light walk or mobility only."
    return f"Today is {focus}."


def schedule_workouts(
    scheduler,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    rel_path: str = DEFAULT_PDF,
) -> str:
    """
    Register one recurring reminder per training day, labelled
    "Workout - <focus>".

    Job ids are stable and derived from the weekday, so re-running this
    after the PDF changes REPLACES each day's reminder rather than
    stacking a second one (schedule_recurring passes
    replace_existing=True). A day that becomes a rest day has its
    reminder cancelled rather than left firing.
    """
    try:
        split = get_split(rel_path)
    except Exception as e:
        return f"Couldn't read the workout plan: {e}"

    if not split:
        return "I couldn't find the schedule table in the workout plan."

    scheduled = []
    for day, focus in split.items():
        scheduler.schedule_recurring(
            message=f"Workout - {focus}",
            days=day,
            hour=hour,
            minute=minute,
            job_id=f"workout_{day}",
        )
        scheduled.append(f"{day.title()} {focus}")

    # A day dropped from the split (or now a rest day) must lose its
    # reminder, or the old one keeps firing forever with a stale label.
    for day in _CRON_DAYS:
        if day not in split:
            scheduler.cancel_job_id(f"workout_{day}")

    clock = f"{hour % 12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"
    return (
        f"Workout reminders set for {clock} on {len(scheduled)} training days: "
        + ", ".join(scheduled) + "."
    )
