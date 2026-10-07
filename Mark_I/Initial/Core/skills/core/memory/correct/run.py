"""correct: fix a name in the graph ("Nitya, not Navya") or an item in the personal model (the user's wording replaces it, locked). Confirmed first.

Sockets
  in : run(wrong="", right="", item="", text="")   a name fix needs wrong+right; an item fix needs item+text; anything else is bad_args
       preview(args) -> "Navya to Nitya" / "that you <old item> to say: <new text>"
  out: "Got it, it's Nitya, not Navya." / "Corrected, thanks."; unknown name or item -> not_found
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
    if args.get("wrong") and args.get("right"):
        return f"{args['wrong']} to {args['right']}"
    try:
        return f"that you {find_item(_rc.model, args.get('item', ''))['text'][:80]} to say: {str(args.get('text', ''))[:100]}"
    except ToolError as ex:
        return f"nothing ({ex.speakable.lower()})"


def run(wrong: str = "", right: str = "", item: str = "", text: str = "") -> str:
    if wrong.strip() and right.strip():
        if not _rc.graph.correct("person", wrong, right):
            raise ToolError("not_found", f"I don't know anyone called {wrong}.", f"no entity named {wrong!r}")
        return f"Got it, it's {right}, not {wrong}."
    if item.strip() and text.strip():
        _rc.model.correct(find_item(_rc.model, item)["id"], text)
        return "Corrected, thanks."
    raise ToolError("bad_args", "What should I correct, and to what?", "give wrong+right for a name, or item+text for a remembered item")
