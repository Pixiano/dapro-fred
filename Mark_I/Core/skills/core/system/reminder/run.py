"""reminder: set, list or cancel reminders and timers on the scheduler (proactive/scheduler.py). One skill with an action, like volume.

Sockets
  in : run(action, text="", minutes=None, at="", days_ahead=0, match="")
         set    needs text, or minutes for a plain timer; and minutes OR at ("HH:MM" 24 h, plus days_ahead 0 = today, 1 = tomorrow; the 4B does not know the date, so code builds it); a time in the past or none given is bad_args with a question
         list   the next few, soonest first      cancel   needs match (words from the text, or "all")
       bind(ctx): needs ctx["scheduler"]; Unavailable without it
  out: one spoken sentence ("Okay, I'll remind you at 4:30 PM today to check the oven."). Firing is spoken by engine/live.py from the bus event.
The brain gives a clock time and how many days ahead; "next Friday" is asked about, not guessed.
Self-check: python skills/core/system/reminder/check.py
"""
from datetime import datetime, timedelta

from skills import Unavailable
from tools.registry import ToolError

_s = None


def bind(ctx):
    global _s
    if ctx.get("scheduler") is None:
        raise Unavailable("no scheduler")
    _s = ctx["scheduler"]


def when(ts: float, now: float) -> str:
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    t = d.strftime("%I:%M %p").lstrip("0")
    days = (d.date() - n.date()).days
    return f"{t} today" if days == 0 else f"{t} tomorrow" if days == 1 else f"{t} on {d.strftime('%A')} {d.day} {d.strftime('%B')}"


def run(action: str, text: str = "", minutes: float | None = None, at: str = "", days_ahead: int = 0, match: str = "") -> str:
    now = _s.now()
    if action == "list":
        its = _s.items()
        return "You have nothing scheduled." if not its else "Coming up: " + "; ".join(f"{i['text'] or 'timer'} at {when(i['at'], now)}" for i in its[:4]) + "."
    if action == "cancel":
        if not match.strip():
            raise ToolError("bad_args", "Which reminder should I cancel?", "missing: match")
        n = _s.cancel(match)
        return f"Cancelled {n} reminder{'s' if n != 1 else ''}." if n else "I don't have a reminder like that."
    if action != "set":
        raise ToolError("bad_args", "Do you want to set, list or cancel a reminder?", f"action {action!r}")
    if minutes:
        ts = now + float(minutes) * 60
    elif at.strip():
        try:
            h, mi = datetime.strptime(at.strip(), "%H:%M").timetuple()[3:5]
            ts = (datetime.fromtimestamp(now) + timedelta(days=int(days_ahead))).replace(hour=h, minute=mi, second=0, microsecond=0).timestamp()
        except ValueError:
            raise ToolError("bad_args", "What time should I use?", f"bad at: {at!r}")
    else:
        raise ToolError("bad_args", "When should I remind you?", "missing: minutes or at")
    if not text.strip() and not minutes:
        raise ToolError("bad_args", "What should I remind you about?", "missing: text")
    try:
        _s.add(ts, text, "reminder" if text.strip() else "timer")
    except ValueError:
        raise ToolError("bad_args", "That time has already passed. What time should I use?", "past")
    return f"Okay, I'll remind you at {when(ts, now)} to {text.strip()}." if text.strip() else f"Okay, timer set for {when(ts, now)}."
