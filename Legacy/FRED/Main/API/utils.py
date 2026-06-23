# utils.py
from datetime import datetime

def get_current_time():
    """Return ISO-formatted current time."""
    return datetime.now().isoformat()

def get_current_time_info():
    """
    Return structured current time info for F.R.E.D.,
    including hour, minute, second, weekday, and greeting.
    """
    now = datetime.now()
    return {
        "timestamp": now.isoformat(),
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
        "weekday": now.strftime("%A")
    }
