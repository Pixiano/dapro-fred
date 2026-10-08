"""remember: add a user-stated item to the personal model (locked: no inference ever changes it). Confirmed first (the manifest flag is `destructive` only because that is what triggers the yes/no).

Sockets
  in : run(text, category="preference");  preview(args) -> the words shown in the question: "Just to confirm: remember that <text>. Yes or no?"
  out: "Noted, I'll remember that."; empty text is bad_args
  bind(ctx): needs ctx["recall"]
"""
from skills import Unavailable
from tools.registry import ToolError

CATS = {"priority", "preference", "working_style", "project", "routine", "decision", "relationship"}
_rc = None


def bind(ctx):
    global _rc
    if "recall" not in ctx:
        raise Unavailable("memory is off")
    _rc = ctx["recall"]


def preview(args: dict) -> str:
    return f"that {str(args.get('text', '')).strip()[:120]}"


def run(text: str, category: str = "preference") -> str:
    if not text.strip():
        raise ToolError("bad_args", "What should I remember?", "empty text")
    _rc.model.add(category if category in CATS else "preference", text.strip())
    return "Noted, I'll remember that."
