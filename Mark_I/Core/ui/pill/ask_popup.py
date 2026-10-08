"""The ask queue's question in the pill's text box: FRED shows one either/or question, Vatsal types an option letter or a plain word, the answer goes to memory/askqueue.answer.

Sockets
  in : AskPopup(popup, xy, answer, done=None).show(item)
         item = {"id", "text", "options": [{"key", "label"}]} as returned by askqueue.ask; popup = ui.pill.input_popup.TypeInputPopup; xy() -> (x, y) where the box goes
         answer(item_id, reply) -> {"ok", "choice"} | {"error": "unclear", "options"}   (askqueue.answer with the memory bound in)
  out: the box takes the keyboard when it appears (Vatsal's decision; if Windows refuses the focus the box is still up and clicking it types, see ui/pill/layered.force_foreground); on {"ok"} done(result) is called and the box closes; on "unclear" the box reopens once more with "Which one?" and the lettered options; a second unclear leaves the question for later
Self-check: python ui/pill/ask_popup.py (fake popup)
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _label(item, prefix=""):
    opts = "   ".join(f"{chr(97 + i)}) {o['label']}" for i, o in enumerate(item["options"]))
    return f"{prefix}{item['text']}\n{opts}"


class AskPopup:
    def __init__(self, popup, xy, answer, done=None):
        self.popup, self.xy, self.answer, self.done = popup, xy, answer, done

    def show(self, item, retry=True):
        def submit(reply):
            r = self.answer(item["id"], reply)
            if r.get("ok"):
                self.done and self.done(r)
            elif retry:
                self.show(item, retry=False)                         # one more try, with the options spelled out
        x, y = self.xy()
        self.popup.show(x, y, _label(item, "" if retry else "Which one? "), tall=True, on_submit=submit)


def _selfcheck():
    shown, results = [], []
    popup = type("P", (), {"show": lambda self, x, y, text, tall, on_submit: shown.append((text, tall, on_submit))})()
    item = {"id": "7", "text": "Is Rohan 'friend' the same as 'Ro'?", "options": [{"key": "yes", "label": "Same person"}, {"key": "no", "label": "Two people"}]}
    replies = iter([{"error": "unclear", "options": []}, {"ok": True, "choice": "yes"}])
    asked = []
    AskPopup(popup, lambda: (10, 20), lambda i, r: asked.append((i, r)) or next(replies), results.append).show(item)
    text, tall, submit = shown[-1]                                                                   # no activate=False any more: the box takes focus
    assert tall and "Is Rohan" in text and "a) Same person   b) Two people" in text
    submit("hmm")
    text, _, submit = shown[-1]
    assert text.startswith("Which one? ") and asked == [("7", "hmm")]                                # unclear: asked again, options spelled out
    submit("a")
    assert results == [{"ok": True, "choice": "yes"}] and asked[-1] == ("7", "a")
    shown.clear()
    AskPopup(popup, lambda: (0, 0), lambda i, r: {"error": "unclear", "options": []}).show(item)
    shown[-1][2]("x")
    shown[-1][2]("y")
    assert len(shown) == 2                                                                           # two tries, then it is left for later
    print("ask popup ok")


if __name__ == "__main__":
    _selfcheck()
