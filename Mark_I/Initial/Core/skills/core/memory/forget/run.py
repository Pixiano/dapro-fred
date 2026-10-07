"""forget: hide one personal-model item. Matched by id or by words from its text; two matches ask which one, never a guess. Confirmed first.

Sockets
  in : run(item);  preview(args) -> "that you <the item's text>" (or why nothing matches)
  out: "Forgotten."; no match -> not_found; several -> bad_args naming them
  bind(ctx): needs ctx["recall"]
"""
from memory_bridge.items import find_item
from skills import Unavailable
from tools.registry import ToolError

_rc = None


def bind(ctx):
    global _rc
    if "recall" not in ctx:
        raise Unavailable("memory is off")
    _rc = ctx["recall"]


def preview(args: dict) -> str:
    try:
        return f"that you {find_item(_rc.model, args.get('item', ''))['text'][:100]}"
    except ToolError as ex:
        return f"nothing ({ex.speakable.lower()})"


def run(item: str) -> str:
    _rc.model.remove(find_item(_rc.model, item)["id"])
    return "Forgotten."
