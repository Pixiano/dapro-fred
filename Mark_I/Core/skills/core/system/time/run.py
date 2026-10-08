"""time: the local clock as one spoken sentence. Read-only, no arguments.

Sockets
  in : run()
  out: "It's 4:05 PM on Tuesday, 7 October 2026." (spoken as is, so the model never paraphrases a date)
"""
from datetime import datetime


def run() -> str:
    n = datetime.now()
    return f"It's {n.strftime('%I:%M %p').lstrip('0')} on {n.strftime('%A')}, {n.day} {n.strftime('%B %Y')}."
