# Core/orchestrator/proactive_checks.py
#
# Observation B from the 2026-08-01 feedback session: the plumbing for
# unprompted speech already existed (utils/notifier.py,
# pill_app._speak_proactive) but nothing ever decided to use it outside
# of reminders. Three checks, scoped with Vatsal directly rather than
# guessed:
#
#   1. Vault staleness    — active-priorities.md untouched too long
#   2. Long session        — continuous machine use with no break
#   3. Deadline proximity  — a vault file's `deadline:` frontmatter
#
# Each fires through notify() AT MOST ONCE per stretch — persona.md is
# explicit that a second reminder is acceptable, a third is nagging, so
# every check here dedups against orchestrator/proactive_checks.py's own
# small state file rather than firing again on every interval tick.

import ctypes
import json
import random
import re
from datetime import datetime, timedelta

from config.settings import (
    VAULT_DIR,
    ROLLOVER_IDLE_HOURS,
    PROACTIVE_CHECK_INTERVAL_MINUTES,
    PROACTIVE_STALE_DAYS,
    PROACTIVE_BREAK_IDLE_MINUTES,
    PROACTIVE_LONG_SESSION_HOURS,
    PROACTIVE_DEADLINE_WARN_DAYS,
    PROACTIVE_TASK_DUE_DAYS,
    PROACTIVE_STATE_PATH,
    PROACTIVE_INTERRUPT_STREAK,
    VIP_MESSAGE_CHECK_MINUTES,
    CALL_LOG_CHECK_MINUTES,
    GMAIL_CHECK_MINUTES,
    PRESENCE_POLL_SECONDS,
    HEADPHONE_POLL_SECONDS_ON_HEADPHONES,
    PROACTIVE_CAMERA_OBSTRUCTION_IDLE_SECONDS,
    PROACTIVE_CAMERA_OBSTRUCTION_POLL_SECONDS,
    PROACTIVE_CAMERA_OBSTRUCTION_STREAK,
    PROACTIVE_BEHIND_YOU_DEBOUNCE,
)
from input import presence
from orchestrator import focus_checkin, headphone_watch, reflection, sleep_mode
from tools import agenda, daily_tasks, session_summary
from utils import event_log
from utils.notifier import notify as _real_notify
from utils.vault_md import parse_frontmatter

# =========================================================
# NATURALNESS GATE — principles 2-5 from plan_perception_features_2026-08-25
# .md's "Proactivity naturalness principles" section (principle 1, backoff
# from last fire, already lives in focus_checkin.py's own fired_at anchor).
# Piggybacked on check_presence's existing PRESENCE_POLL_SECONDS cadence
# below rather than a new scheduled job — it already needs presence.is_
# present() every tick, this just rides along.
# =========================================================

# In-memory only, same "a restart is a real event" precedent every other
# streak counter in this codebase holds to (security_watch._stranger_streak,
# presence._present_streak).
_interruptible_streak = 0
_last_window_title = None  # None = not observed yet, first tick can't be "changed"
_last_media_playing = False
_task_boundary_this_tick = False


def _update_interruptibility():
    """Composite interruptibility + suppress-during-busy (principles 3
    and 5): present, and nothing else is actively playing audio —
    media_state.py already excludes FRED's own TTS output, so a True
    here means someone else's audio (music/video/a call) is live, not
    FRED talking over itself. Also tracks the task-boundary signal
    (principle 2): the foreground window changing, or media that WAS
    playing just stopping."""
    global _interruptible_streak, _last_window_title, _last_media_playing
    global _task_boundary_this_tick

    # Local imports, same convention check_vip_messages/check_recent_calls
    # already use in this file — pycaw (media_state) and win32gui aren't
    # needed by every process that imports this module (e.g. the CLI),
    # so don't pay for them at module load.
    from audio import media_state

    media_playing = media_state.is_media_playing()
    try:
        import win32gui
        title = (win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "").strip()
    except Exception:
        title = _last_window_title  # unreadable this tick -- don't manufacture a false "changed"

    media_just_stopped = _last_media_playing and not media_playing
    title_changed = _last_window_title is not None and title != _last_window_title
    _task_boundary_this_tick = media_just_stopped or title_changed

    _last_media_playing = media_playing
    _last_window_title = title

    good_moment = presence.is_present() and not media_playing
    _interruptible_streak = _interruptible_streak + 1 if good_moment else 0


def _ready_to_interrupt() -> bool:
    """Calm technology (principle 4): the composite signal must have
    held for PROACTIVE_INTERRUPT_STREAK consecutive polls before an
    ordinary nudge speaks — UNLESS a task boundary was just observed
    (principle 2), which is itself already a strong "good moment" signal
    and gets to skip the wait. Either way the composite signal must be
    good THIS tick — a boundary during a busy/absent moment still holds."""
    if _interruptible_streak == 0:
        return False
    return _task_boundary_this_tick or _interruptible_streak >= PROACTIVE_INTERRUPT_STREAK


def notify(*args, urgent=False, **kwargs):
    """Gate on sleep-mode and (unless urgent) the naturalness gate above:
    every proactive nudge in this file funnels through here (this
    module's own `notify` shadows utils.notifier.notify, imported above
    as _real_notify) rather than each check function checking
    sleep_mode.is_sleeping() individually. Neither gate queues or
    replays a skipped nudge — matches reminders' own precedent of "fire
    once or not at all", now extended from just sleep-mode to "is this
    actually a good moment."

    urgent=True bypasses the naturalness gate (not sleep-mode) for
    checks where timing quality matters less than not going stale —
    VIP messages, recent calls, headphone-switch status announcements.
    Ordinary "presence/work/focus" nudges (vault staleness, long
    session, deadlines, agenda carryover, focus check-ins, etc.) leave
    this False."""
    if sleep_mode.is_sleeping():
        return
    if not urgent and not _ready_to_interrupt():
        return
    _real_notify(*args, **kwargs)


_DATE_FMT = "%Y-%m-%d"

# How far ahead an event counts as "upcoming" for check_agenda_events_upcoming
# — matches the "due tomorrow" framing used everywhere else in this file,
# not a separate concept.
_EVENT_UPCOMING_HOURS = 24


# =========================================================
# STATE (dedup so a check fires at most once per stretch)
# =========================================================

def _load_state() -> dict:
    if not PROACTIVE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict):
    PROACTIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROACTIVE_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(PROACTIVE_STATE_PATH)


# =========================================================
# 1. VAULT STALENESS
# =========================================================

def check_vault_staleness():
    """
    Reads active-priorities.md's own `updated:` frontmatter date — not
    a per-item parse of its prose bullets. The file is hand-curated
    prose with no per-item machine-readable timestamp (checked before
    building this), so a whole-file signal is the honest granularity
    available, not "which project is stale."
    """
    path = VAULT_DIR / "active-priorities.md"
    if not path.exists():
        return

    try:
        fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        updated_str = fields.get("updated", "")
        updated = datetime.strptime(updated_str, _DATE_FMT)
    except (ValueError, OSError):
        return

    days = (datetime.now() - updated).days
    if days < PROACTIVE_STALE_DAYS:
        return

    state = _load_state()
    stale = state.setdefault("stale", {})

    # Dedup key is the `updated` value itself, not just "already
    # notified" — so a fresh edit that later goes stale again DOES
    # notify again, rather than being silenced forever by one old flag.
    if stale.get("active-priorities.md") == updated_str:
        return

    notify(
        f"Active priorities hasn't been touched in {days} days, sir.",
        title="Active Priorities",
    )
    stale["active-priorities.md"] = updated_str
    _save_state(state)


# =========================================================
# 2. LONG SESSION (no break)
# =========================================================

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input, system-wide —
    Windows' own idle-time API, not FRED-specific activity. Public
    (no leading underscore) since orchestrator/security_watch.py also
    needs this as a live "is input happening right now" check."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return millis / 1000.0


def check_long_session():
    """
    Flags continuous machine use with no break, using real OS-level
    idle time rather than FRED-conversation activity — the two are
    different things, and "no one has spoken to FRED in an hour" says
    nothing about whether Vatsal stepped away.
    """
    state = _load_state()
    session = state.setdefault("long_session", {})
    now = datetime.now()

    idle = idle_seconds()
    if idle >= PROACTIVE_BREAK_IDLE_MINUTES * 60:
        # A real break — reset the clock and the notified flag, so the
        # NEXT long stretch can notify again.
        session["last_break"] = now.isoformat()
        session.pop("notified", None)
        _save_state(state)
        return

    last_break_str = session.get("last_break")
    if last_break_str is None:
        # First check since launch — start counting from now rather
        # than assuming a long session was already underway.
        session["last_break"] = now.isoformat()
        _save_state(state)
        return

    try:
        last_break = datetime.fromisoformat(last_break_str)
    except ValueError:
        session["last_break"] = now.isoformat()
        _save_state(state)
        return

    if session.get("notified"):
        return

    if now - last_break >= timedelta(hours=PROACTIVE_LONG_SESSION_HOURS):
        notify(
            f"You've been at this {PROACTIVE_LONG_SESSION_HOURS} hours "
            f"straight with no break, sir.",
            title="Break?",
        )
        session["notified"] = True
        _save_state(state)


# =========================================================
# 3. DEADLINE PROXIMITY
# =========================================================

def check_deadlines():
    """
    Reads an optional `deadline: YYYY-MM-DD` frontmatter field on any
    vault file. No vault file uses this field yet — as of 2026-08-01,
    exam dates aren't recorded (see active-priorities.md's "Recently
    closed" — board exam dates were explicitly removed pending real
    dates). This is the read path for that field once it exists, not
    speculative parsing of dates out of prose.
    """
    if not VAULT_DIR.exists():
        return

    state = _load_state()
    notified = state.setdefault("deadlines", {})
    now = datetime.now()
    changed = False

    for path in VAULT_DIR.rglob("*.md"):
        try:
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue

        deadline_str = fields.get("deadline", "")
        if not deadline_str:
            continue

        try:
            deadline = datetime.strptime(deadline_str, _DATE_FMT)
        except ValueError:
            continue

        days_until = (deadline - now).days
        if not (0 <= days_until <= PROACTIVE_DEADLINE_WARN_DAYS):
            continue

        key = f"{path.name}|{deadline_str}"
        if notified.get(key):
            continue

        label = fields.get("type", "deadline")
        notify(
            f"{path.stem}, {label}, in {days_until} day(s), sir.",
            title="Deadline",
        )
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


# =========================================================
# 4. TASK DEADLINES (daily notes, not frontmatter)
# =========================================================

def check_task_deadlines():
    """
    Warns about a still-open task whose own text says when it's due.

    check_deadlines above reads a `deadline:` frontmatter field that no
    vault file has ever used. Real deadlines live in the daily notes'
    task lines instead — "Chemistry journal completion — due Thursday in
    school" (daily/2026-08/2026-08-03.md). Confirmed 2026-08-04: that
    task, and a physics one alongside it, went unmentioned until Vatsal
    pushed back twice asking what else was pending. Nothing was watching
    them.

    Dedup is per task per due date, so a task that gets re-dated warns
    again but an unchanged one doesn't nag every 15 minutes.
    """
    state = _load_state()
    notified = state.setdefault("task_deadlines", {})
    changed = False

    try:
        due = daily_tasks.open_due_tasks(within_days=PROACTIVE_TASK_DUE_DAYS)
    except Exception as e:
        print(f"[proactive] task deadline check failed: {e}")
        return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for deadline, text in due:
        key = f"{text}|{deadline:%Y-%m-%d}"
        if notified.get(key):
            continue

        days = (deadline - today).days
        if days < 0:
            when = f"{abs(days)} day(s) overdue"
        elif days == 0:
            when = "due today"
        elif days == 1:
            when = "due tomorrow"
        else:
            when = f"due in {days} day(s)"

        notify(f"{text} — {when}, sir.", title="Task deadline")
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


# =========================================================
# 6. AGENDA DEADLINES (homework/project — statement, not a question)
# =========================================================

def check_agenda_deadlines():
    """
    Warns about a still-open homework/project item due soon or overdue.

    Dedup key bakes in the item's `when` value, not the current
    "days until" phrasing — same precedent as check_task_deadlines
    above. An item that stays overdue day after day does not re-nag
    here; check_agenda_carryover below is the mechanism for asking
    about it again each new day, deliberately separate because that
    one is a question expecting an answer, not a statement.
    """
    state = _load_state()
    notified = state.setdefault("agenda_deadlines", {})
    changed = False

    try:
        due = agenda.due_within(PROACTIVE_TASK_DUE_DAYS)
    except Exception as e:
        print(f"[proactive] agenda deadline check failed: {e}")
        return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for item in due:
        key = f"{item['kind']}|{item['subject']}|{item['detail']}|{item['when'].isoformat()}"
        if notified.get(key):
            continue

        days = (item["when"].date() - today.date()).days
        if days < 0:
            when = f"{abs(days)} day(s) overdue"
        elif days == 0:
            when = "due today"
        elif days == 1:
            when = "due tomorrow"
        else:
            when = f"due in {days} day(s)"

        subject = item["subject"] + (f", {item['detail']}" if item["detail"] else "")
        notify(f"{subject} — {when}, sir.", title="Agenda deadline")
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


# =========================================================
# 7. AGENDA EVENTS — prep-time reached (statement) and upcoming (question)
# =========================================================

def check_agenda_event_prep():
    """One notification the moment an event's getting-ready window
    opens: "Sir, [event] starts at [time] — time to start getting
    ready." Dedup key includes `when`, so a rescheduled event's prep
    warning fires again for the new time."""
    state = _load_state()
    notified = state.setdefault("agenda_event_prep", {})
    changed = False

    try:
        due = agenda.events_needing_prep()
    except Exception as e:
        print(f"[proactive] event prep check failed: {e}")
        return

    for item in due:
        key = f"{item['subject']}|{item['when'].isoformat()}"
        if notified.get(key):
            continue

        clock = item["when"].strftime("%I:%M %p").lstrip("0")
        notify(f"{item['subject']} starts at {clock}, sir — time to start getting ready.", title="Getting ready")
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


def check_agenda_events_upcoming(on_agenda_ask=None):
    """
    An event starting within _EVENT_UPCOMING_HOURS gets asked about
    once: "you have X tomorrow — are you prepped for it?" A QUESTION,
    unlike every check above — the answer needs to land on
    update_agenda_item, not conversation memory, so on_agenda_ask primes
    the orchestrator's carry-forward the moment this actually speaks.
    """
    state = _load_state()
    notified = state.setdefault("agenda_event_upcoming", {})
    changed = False

    try:
        upcoming = agenda.events_upcoming(within_hours=_EVENT_UPCOMING_HOURS)
    except Exception as e:
        print(f"[proactive] event upcoming check failed: {e}")
        return

    for item in upcoming:
        key = f"{item['subject']}|{item['when'].isoformat()}"
        if notified.get(key):
            continue

        clock = item["when"].strftime("%I:%M %p").lstrip("0")
        detail = f", {item['detail']}" if item["detail"] else ""
        notify(
            f"{item['subject']}{detail} at {clock}, sir — are you prepped for it?",
            title="Upcoming",
        )
        notified[key] = True
        changed = True

        if on_agenda_ask is not None:
            try:
                on_agenda_ask(["update_agenda_item"])
            except Exception as e:
                print(f"[proactive] on_agenda_ask callback failed: {e}")

    if changed:
        _save_state(state)


# =========================================================
# 8. AGENDA CARRYOVER (homework/project — question, re-asked daily)
# =========================================================

def check_agenda_carryover(on_agenda_ask=None):
    """
    A still-open homework/project item due today or earlier gets asked
    about once PER DAY: "you had Geography due today — did you finish
    it, or find a workaround?" Unlike check_agenda_deadlines, the dedup
    key includes TODAY's date on purpose — this is meant to keep
    checking in on an item every day it remains outstanding, not warn
    once and go quiet. Same on_agenda_ask priming as the upcoming-event
    question above, same reason: the reply is the actual record update,
    not small talk to be forgotten once spoken.
    """
    state = _load_state()
    notified = state.setdefault("agenda_carryover", {})
    changed = False

    try:
        candidates = agenda.carryover_candidates()
    except Exception as e:
        print(f"[proactive] agenda carryover check failed: {e}")
        return

    today = datetime.now().strftime(_DATE_FMT)

    for item in candidates:
        days_overdue = (datetime.now().date() - item["when"].date()).days

        # Commitments re-ask every 3rd day of carryover (day 0, 3, 6...)
        # instead of daily -- Vatsal's own call 2026-08-28: a casual "I'll
        # email them back" is lower-stakes than homework/project and
        # shouldn't nag as often. Homework/project cadence is unchanged.
        if item["kind"] == "commitment" and days_overdue % 3 != 0:
            continue

        key = f"{item['kind']}|{item['subject']}|{item['when'].isoformat()}|{today}"
        if notified.get(key):
            continue

        subject = item["subject"] + (f", {item['detail']}" if item["detail"] else "")
        overdue = days_overdue > 0
        phrase = "was due" if overdue else "was due today"
        ask = (
            "did you get to it, or does it still need doing?"
            if item["kind"] == "commitment"
            else "did you finish it, or find a workaround?"
        )
        notify(
            f"Sir, {subject} {phrase} — {ask}",
            title="Check-in",
        )
        notified[key] = True
        changed = True

        if on_agenda_ask is not None:
            try:
                on_agenda_ask(["update_agenda_item"])
            except Exception as e:
                print(f"[proactive] on_agenda_ask callback failed: {e}")

    if changed:
        _save_state(state)


# =========================================================
# 5. OVERNIGHT DAY ROLLOVER
# =========================================================

def _recent_transcript(today: str) -> str:
    """
    What was actually said over the stretch being closed out: the
    previous day plus today's own turns, in order. Both, because a
    rollover that fires at 01:00 sits on the far side of midnight from
    the evening it is summarising, and a session that ran past midnight
    lands in today's log.
    """
    prev = (
        datetime.strptime(today, _DATE_FMT) - timedelta(days=1)
    ).strftime(_DATE_FMT)
    parts = [session_summary.transcript(d) for d in (prev, today)]
    return "\n".join(p for p in parts if p)


def _judge_carryover(candidates: list, llm, today: str) -> list:
    """
    Which of yesterday's open tasks are still worth carrying. Judged by
    the Deep tier (Qwen3-14B) and pinned local_only — this reads the
    vault's task text AND the day's raw conversation, which is exactly
    the material that shouldn't leave the machine for a filtering job a
    local model does fine.

    Anything the model can't be matched back to a real candidate line is
    dropped, so a hallucinated task can never enter the note. If the
    judgement fails outright, everything carries: losing a task silently
    is worse than carrying one that was already dead.
    """
    if llm is None:
        return candidates

    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(candidates))
    convo = _recent_transcript(today) or "(nothing logged)"
    messages = [
        {
            "role": "system",
            "content": (
                "You decide which unfinished tasks are still worth carrying "
                "into the next day. Use the conversation log: a task he said "
                "he finished, dropped, or that the log shows is no longer "
                "relevant should not carry. Drop tasks that are clearly "
                "obsolete or tied to a date that has passed and cannot be "
                "redone. When unsure, keep the task. Answer with the numbers "
                "to keep, comma-separated, and nothing else."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Conversation log:\n{convo}\n\n"
                f"Unfinished tasks:\n{listing}"
            ),
        },
    ]

    try:
        answer = llm.generate(messages, tier="Deep", local_only=True)
    except Exception as e:
        print(f"[proactive] carryover judgement failed, keeping all: {e}")
        return candidates

    kept = [
        candidates[int(n) - 1]
        for n in re.findall(r"\d+", answer or "")
        if 0 < int(n) <= len(candidates)
    ]
    # An empty or unparseable answer means "no judgement", not "drop
    # everything" — the model refusing to answer must not wipe the list.
    return kept or candidates


def check_day_rollover(llm=None):
    """
    Starts the new day's note with whatever is still outstanding.

    Fires when the machine has been idle for ROLLOVER_IDLE_HOURS — the
    overnight gap in practice. Keyed on the wall date, so a two-hour gap
    inside the same day does nothing: the date has to have turned over
    since the last rollover for there to be a new day to write.
    """
    if idle_seconds() < ROLLOVER_IDLE_HOURS * 3600:
        return

    today = datetime.now().strftime(_DATE_FMT)
    state = _load_state()
    if state.get("rollover_day") == today:
        return

    try:
        candidates = daily_tasks.carryover_candidates(today)
    except Exception as e:
        print(f"[proactive] rollover read failed: {e}")
        return

    # The note gets created even with nothing to carry — "there is a log
    # for today" is the point; an empty one still gets appended to by
    # add_task and the session recap later in the day.
    daily_tasks.ensure_day_note(today)
    for text in _judge_carryover(candidates, llm, today):
        daily_tasks.add_task(text, day=today)

    state["rollover_day"] = today
    _save_state(state)


# =========================================================
# 9. REFLECTION REVIEW REMINDER (declined-review nudge)
# =========================================================

def check_reflection_review_pending():
    """
    Re-offers reflection's staged self-observation review while it sits
    un-reviewed — the "he said no, so keep reminding" half of the
    review flow. consolidation.on_sleep_exit() already offers it once
    at the wake moment; this is the ongoing nudge for whenever he just
    never answers, or the offer lands mid-sleep-mode and gets missed.

    Dedup is once per calendar day (not once ever, not every interval
    tick) — same "second reminder is fine, a third is nagging" line
    every other check in this file holds to, applied per-day since
    "still not reviewed" can legitimately span many days.
    """
    if not reflection.has_pending_review():
        return

    state = _load_state()
    notified = state.setdefault("reflection_review_offered", {})
    today = datetime.now().strftime(_DATE_FMT)
    if notified.get(today):
        return

    notify(reflection.offer_review_text(), title="Review pending")
    notified[today] = True
    _save_state(state)


# =========================================================
# WIRING
# =========================================================

def check_vip_messages():
    """
    Speak up when a VIP messages, and stay silent otherwise.

    Deliberately does NOT dedup through _load_state() like the checks
    above: whatsapp_tools keeps its own seen-set keyed on the message's
    own epoch timestamp, which is finer-grained than "once per stretch"
    and is the right granularity here — two different VIP messages an
    hour apart are two separate things worth hearing, whereas one message
    must never be announced twice.

    Reading works with the phone locked (verified 2026-08-16), so this
    costs nothing but an adb round trip when nothing has arrived. Never
    raises into the scheduler: a phone that's asleep or off the network
    is the normal case, not an error.
    """
    try:
        from tools.whatsapp_tools import check_vip_messages as fetch
        summary = fetch()
    except Exception as e:
        event_log.log_error("proactive_vip_messages", e)
        return

    if summary:
        # urgent: a VIP message going stale while FRED waits for a "good
        # moment" defeats the point of a fast-poll check in the first
        # place — see this module's own comment on VIP_MESSAGE_CHECK_MINUTES.
        notify(summary, title="Message", urgent=True)


def check_recent_calls():
    """
    Speak up when a VIP-tier person called since the last check, and
    stay silent otherwise — "you missed a call from X" gated the exact
    same way the VIP WhatsApp check above is gated, off the exact same
    tier data (see phone_tools.check_recent_calls's docstring).

    Same reasoning as check_vip_messages above for skipping _load_state():
    phone_tools keeps its own watermark (the highest call `date` seen),
    which is the right granularity here too.

    Never raises into the scheduler: a phone that's asleep or off the
    network is the normal case, not an error.
    """
    try:
        from tools.phone_tools import check_recent_calls as fetch
        summary = fetch()
    except Exception as e:
        event_log.log_error("proactive_recent_calls", e)
        return

    if summary:
        notify(summary, title="Call", urgent=True)  # same reasoning as check_vip_messages above


def check_gmail_missed_replies():
    """Speak up when an inbox email has gone GMAIL_MISSED_REPLY_DAYS
    with no reply, stay silent otherwise. Same dedup-in-the-tool-not-
    _load_state() shape as check_vip_messages above — gmail_imap.py
    keeps its own Message-ID seen-set. Never raises into the scheduler:
    IMAP being briefly unreachable (or credentials never set up) is the
    normal case, not an error — see gmail_imap.py's own docstring."""
    try:
        from tools.gmail_imap import check_missed_replies as fetch
        summary = fetch()
    except Exception as e:
        event_log.log_error("proactive_gmail_missed_replies", e)
        return

    if summary:
        # Not urgent, deliberately -- Vatsal's own call 2026-08-28: unlike
        # a VIP text/call, a missed-reply nag can wait for a good moment
        # via the normal naturalness gate rather than interrupting anything.
        notify(summary, title="Email")


def check_gmail_deadlines():
    """Speak up when a recent email's body mentions a date-like phrase,
    stay silent otherwise. Same shape as check_gmail_missed_replies
    above, separate seen-set (gmail_imap.check_email_deadlines)."""
    try:
        from tools.gmail_imap import check_email_deadlines as fetch
        summary = fetch()
    except Exception as e:
        event_log.log_error("proactive_gmail_deadlines", e)
        return

    if summary:
        notify(summary, title="Email")  # not urgent -- same reasoning as check_gmail_missed_replies above


# Wake-awareness greeting, same sir-suffixed short-phrase-pool style as
# canned_replies.py's "presence_check" category — fires once, only when
# waking from a real debounced sleep-mode absence (see check_presence
# below), not on every single-poll blip.
_PRESENCE_GREETINGS = (
    "You there, sir?", "Welcome back, sir.", "Good to see you again, sir.",
    "Ah, there you are, sir.", "Back with us, sir?", "Good to have you back, sir.",
)


# "Someone's behind you" awareness alert — the opposite of security_
# watch.py's stranger-lockdown check, which only fires when Vatsal is
# NOT present. Any second face in frame while he IS present counts,
# known family or unrecognized alike — deliberately not reasoning about
# gaze/head-pose direction, Vatsal's own explicit choice 2026-08-28. The
# literal "Sir. " opener (a real sentence break, not just a comma) is
# his own ask too, for a startled-then-informed cadence rather than a
# single rushed line.
#
# TODO (deferred, Vatsal's own note): differentiate phrasing once the
# second face is a RECOGNIZED family member vs a stranger — for now
# these stay generic regardless of which.
_BEHIND_YOU_PHRASES = (
    "Sir. Someone's right behind you.",
    "Sir. You've got company behind you.",
    "Sir. Someone just stepped in behind you.",
    "Sir. Heads up — there's someone right there.",
)

# Consecutive qualifying check_presence polls, in-memory only — same
# "restart is a real event" streak-debounce shape as
# security_watch._stranger_streak, avoiding a single misclassified
# frame triggering this.
_behind_you_streak = 0
_behind_you_fired_this_episode = False


def _check_behind_you():
    """Bypasses this module's own notify() (naturalness gate + sleep-
    mode) entirely — goes straight to utils.notifier.notify, same
    precedent check_camera_obstruction sets below for the same reason:
    this is an awareness alert, waiting for a "good moment" or for sleep
    mode to lift defeats the point. Dedup is once per "episode" — the
    fired flag resets the instant the extra face leaves frame, so a new
    visitor later still gets a fresh alert."""
    global _behind_you_streak, _behind_you_fired_this_episode

    if not presence.is_present():
        _behind_you_streak = 0
        _behind_you_fired_this_episode = False
        return

    classification = presence.last_classification()
    someone_else = bool(classification.get("known_people")) or classification.get("unrecognized", False)

    if not someone_else:
        _behind_you_streak = 0
        _behind_you_fired_this_episode = False
        return

    _behind_you_streak += 1
    if _behind_you_streak < PROACTIVE_BEHIND_YOU_DEBOUNCE or _behind_you_fired_this_episode:
        return

    _real_notify(random.choice(_BEHIND_YOU_PHRASES), title="Heads up")
    _behind_you_fired_this_episode = True


def check_presence():
    """
    Polls the camera for who's in frame — see input/presence.py's
    poll_once(). Same reasoning as check_vip_messages above: never let a
    camera hiccup or a transient vision-model failure crash the
    scheduler, log it and move on.

    Greets only on a REAL sleep-mode wake — sleep_mode.py's own
    PRESENCE_ABSENT_DEBOUNCE (3 consecutive absent polls) is what
    distinguishes actual absence from a single missed poll (camera
    hiccup, reaching for something), and PRESENCE_PRESENT_DEBOUNCE (2
    consecutive present polls) does the same for the return trip — a
    single high-confidence-but-wrong frame must not fire the greeting.
    So the greeting fires only on the actual is_sleeping() True->False
    edge, which is why the choice of greeting text is just handed to
    on_presence_poll() — it's the one that knows whether that edge is
    being crossed this poll, and only uses it then.

    The greeting text is passed IN to on_presence_poll() rather than
    this function firing its own notify() after the fact — confirmed
    live 2026-08-22: a real wake fired consolidation's bundled-recap
    notify() (inside on_presence_poll(), via sleep_mode's own
    on_sleep_exit hook) and then this function's separate greeting
    notify() moments later, and only the first was ever heard. Both
    routed through pill_app._speak_proactive, which runs on its own
    thread and skips itself entirely if another proactive utterance
    already holds the turn lock — so the second notify() call didn't
    error, it just silently never spoke. See consolidation.on_sleep_exit's
    docstring: one bundled message, not two competing ones.
    """
    try:
        present = presence.poll_once()
    except Exception as e:
        event_log.log_error("proactive_presence", e)
        return

    try:
        _update_interruptibility()
    except Exception as e:
        event_log.log_error("proactive_interruptibility", e)

    try:
        _check_behind_you()
    except Exception as e:
        event_log.log_error("proactive_behind_you", e)

    sleep_mode.on_presence_poll(present, greeting=random.choice(_PRESENCE_GREETINGS))


# =========================================================
# 10. CAMERA OBSTRUCTION -- active input while camera reads absent
# =========================================================

_CAMERA_OBSTRUCTION_PHRASES = (
    "Camera looks blocked, sir — still there?",
    "Is the camera obstructed, sir?",
    "Can't see you, sir — camera blocked, or did you just look away?",
    "Sir, I think something's covering the camera — you still there?",
)

# Consecutive qualifying polls (sleeping + recent input), in-memory only
# — same "a restart is a real event" reasoning sleep_mode.py's own
# streak counters hold to, not persisted.
_camera_obstruction_streak = 0


def check_camera_obstruction():
    """
    Sleep mode is purely camera-driven, but a blocked/covered/misaimed
    camera looks identical to "stepped away" from that signal alone.
    Cross-check against real keyboard/mouse activity (idle_seconds(),
    the same OS-level GetLastInputInfo timestamp long_session/
    security_watch already use elsewhere in this file — no keystroke
    content, just "how long since the last input") to catch the case
    where FRED thinks nobody's home but someone is clearly still typing.
    Vatsal's own idea, 2026-08-23.

    Debounced (Vatsal's own follow-up call, same day): registered on its
    own PROACTIVE_CAMERA_OBSTRUCTION_POLL_SECONDS-interval job (5s, not
    the 15s presence-poll cadence) and requires
    PROACTIVE_CAMERA_OBSTRUCTION_STREAK (3) CONSECUTIVE qualifying polls
    before it actually speaks — a single glance-away-then-back must not
    trigger this. Also see input/presence.py's own 2026-08-23 fix: a
    face at a bad angle now always gets a real vision-model check before
    being written off as absent, which is the other half of the same
    complaint ("turning away or looking down even a little" was
    triggering this too fast).

    Deliberately bypasses this module's own notify() wrapper (which
    gates on sleep_mode.is_sleeping()) — that gate would silence the
    exact case this check exists to catch. Goes straight to
    utils.notifier.notify, same precedent consolidation.py's
    on_sleep_exit already sets for the same reason.

    Dedup: at most once per sleep stretch. The "asked" flag (and the
    streak) resets the moment sleep mode isn't active, so the next
    stretch can ask again — same "second reminder is fine" shape as
    every other check here, just keyed off sleep-mode edges instead of a
    value/date.
    """
    global _camera_obstruction_streak

    state = _load_state()
    obstruction = state.setdefault("camera_obstruction", {})

    if not sleep_mode.is_sleeping():
        _camera_obstruction_streak = 0
        if obstruction.get("asked"):
            obstruction["asked"] = False
            _save_state(state)
        return

    if obstruction.get("asked"):
        return

    if idle_seconds() >= PROACTIVE_CAMERA_OBSTRUCTION_IDLE_SECONDS:
        _camera_obstruction_streak = 0
        return

    _camera_obstruction_streak += 1
    if _camera_obstruction_streak < PROACTIVE_CAMERA_OBSTRUCTION_STREAK:
        return

    _real_notify(random.choice(_CAMERA_OBSTRUCTION_PHRASES), title="Camera")
    obstruction["asked"] = True
    _save_state(state)


def register(scheduler, llm=None, on_agenda_ask=None):
    """
    Call once at orchestrator startup — see orchestrator.py's __init__.

    on_agenda_ask: called with a tool-name list right after an agenda
    check SPEAKS a question expecting an answer (upcoming event,
    carryover) — see FREDOrchestrator._prime_carry. None (the CLI, or
    any caller that doesn't need the follow-up primed) just means those
    two checks fire without priming anything, same as before this
    parameter existed.
    """
    scheduler.add_periodic(
        lambda: check_day_rollover(llm), PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_day_rollover"
    )
    scheduler.add_periodic(
        check_vault_staleness, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_vault_staleness"
    )
    scheduler.add_periodic(
        check_long_session, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_long_session"
    )
    scheduler.add_periodic(
        check_deadlines, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_deadlines"
    )
    scheduler.add_periodic(
        check_task_deadlines, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_task_deadlines"
    )
    scheduler.add_periodic(
        check_agenda_deadlines, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_agenda_deadlines"
    )
    scheduler.add_periodic(
        check_agenda_event_prep, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_agenda_event_prep"
    )
    scheduler.add_periodic(
        lambda: check_agenda_events_upcoming(on_agenda_ask),
        PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_agenda_events_upcoming",
    )
    scheduler.add_periodic(
        lambda: check_agenda_carryover(on_agenda_ask),
        PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_agenda_carryover",
    )
    # Runs on its own, much shorter interval: every other check here is
    # about state that changes over hours, but "someone important just
    # messaged you" is worthless if it arrives twenty minutes late.
    scheduler.add_periodic(
        check_vip_messages, VIP_MESSAGE_CHECK_MINUTES, "proactive_vip_messages"
    )
    # Same short-interval reasoning as VIP messages above, for calls
    # instead of WhatsApp — see CALL_LOG_CHECK_MINUTES's comment.
    scheduler.add_periodic(
        check_recent_calls, CALL_LOG_CHECK_MINUTES, "proactive_recent_calls"
    )
    # Own slower cadence, see GMAIL_CHECK_MINUTES's comment — an IMAP
    # round trip is heavier than the adb checks above. No-ops until
    # scripts/setup_gmail_credentials.py has been run.
    scheduler.add_periodic(
        check_gmail_missed_replies, GMAIL_CHECK_MINUTES, "proactive_gmail_missed_replies"
    )
    scheduler.add_periodic(
        check_gmail_deadlines, GMAIL_CHECK_MINUTES, "proactive_gmail_deadlines"
    )
    # add_periodic takes minutes, PRESENCE_POLL_SECONDS is defined in
    # seconds (Vatsal's explicit "15 seconds" call) — dividing by 60
    # feeds add_periodic's IntervalTrigger a fractional-minute float
    # (15 / 60 == 0.25 exactly, no rounding), which APScheduler turns
    # straight into timedelta(seconds=15). No need for a seconds-native
    # variant of add_periodic just for this.
    scheduler.add_periodic(
        check_presence, PRESENCE_POLL_SECONDS / 60, "proactive_presence"
    )
    # Own faster cadence, not presence polling's 15s — the debounce
    # inside check_camera_obstruction needs PROACTIVE_CAMERA_OBSTRUCTION_STREAK
    # consecutive 5s ticks, so 3*5s=15s total before it speaks. Cheap
    # either way (no camera/LLM call, just an OS idle-time read and a
    # state-file check).
    scheduler.add_periodic(
        check_camera_obstruction,
        PROACTIVE_CAMERA_OBSTRUCTION_POLL_SECONDS / 60,
        "proactive_camera_obstruction",
    )
    # Headphone-detection -> audio-output switching — see
    # orchestrator/headphone_watch.py's own module docstring. Registered
    # at the FAST cadence (HEADPHONE_POLL_SECONDS_ON_HEADPHONES, 3s) —
    # Vatsal's own call 2026-08-25 — since APScheduler's add_periodic
    # has no per-job dynamic interval; check_and_switch() self-throttles
    # down to the slower on-speakers cadence internally instead (see its
    # own _last_check_ts). It gates on presence.is_present() as its
    # first check, so this is cheap on every tick nobody's there. A
    # no-op every tick until scripts/enroll_headphones.py has been run
    # once. Fires through this module's own notify() (passed in, not
    # imported back — same reason focus_checkin.check(notify) takes it
    # as a parameter below) so sleep-mode gating is automatic.
    scheduler.add_periodic(
        lambda: headphone_watch.check_and_switch(notify),
        HEADPHONE_POLL_SECONDS_ON_HEADPHONES / 60,
        "proactive_headphone_watch",
    )
    # Focus-awareness check-in — see orchestrator/focus_checkin.py. Fires
    # through this module's own notify() so sleep-mode gating is automatic
    # (no redundant separate gate — see that module's docstring for why
    # that's actually sufficient here).
    scheduler.add_periodic(
        lambda: focus_checkin.check(notify),
        PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_focus_checkin",
    )
    scheduler.add_periodic(
        check_reflection_review_pending,
        PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_reflection_review",
    )
