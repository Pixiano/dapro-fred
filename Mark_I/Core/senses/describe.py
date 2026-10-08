"""Screen description through the playground's vision slot, with the grounding rule that keeps invented text from ever being spoken.

A vision model can describe a screen well and still invent text that is not there. Rule (from the L0 design): any text the describer QUOTES, and any number it states,
must appear in the OCR output of the same crop. With OCR off (the current state) nothing can be verified, so the result says so (`verified=False`, `unverified` lists the
quoted spans) and the engine must not present them as exact.

Sockets
  in : Describer(chat=None, bus=None).describe(image, question=None, ocr_text=None) -> Description
         image = RGB ndarray | PNG/JPEG bytes | path;  question = what the user asked (answered from the screen, not a generic blurb);
         ocr_text = OCR output of the same crop (senses/ocr.read) or None when OCR is off;  think = let the model reason first (off by default: about 3 s vs 9 s; reading is fast, arithmetic over what was read belongs to the brain);  chat = Chat-shaped (default: vision slot, settings.describe_model)
       ground(text, ocr_text) -> (clean_text, dropped)     the pure rule
  out: Description(text, verified, unverified, dropped, ms); bus event screen.described {ms, verified, n_quotes} (never the text or pixels)
       ocr_text given: spans not found in it are replaced by "(unverified text)" in `text` and listed in `dropped`; verified=True
       ocr_text None:  `text` is untouched, verified=False, `unverified` = every quoted span and number-like token (the caller decides how to hedge)
The vision slot must be loaded with the image projector (Playground().load(model, slot="vision", vision=True)); the launcher owns that, describe() only calls it.
Self-check: python senses/describe.py (scripted model).  Real model on the 12-screenshot set: tests/live_describe.py
"""
import difflib
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from models.client import Chat, image_part

GENERIC = ("Describe what is on this screen: the application and the general activity, e.g. 'a code editor with a Python file open'. "
           "If there is an error message, code, a specific number or other text the user is likely looking at, quote it exactly instead of summarising it. "
           "Do not guess about anything that is not clearly visible. If nothing is wrong or notable, say so plainly.")
ASK = ("Look at this screenshot and answer the question directly and specifically, quoting exact text and numbers where they matter. "
       "If the screen does not show the answer, say so. Do not invent anything that is not visible. Answer in at most two short sentences.\n\nQuestion: {q}")

_QUOTE = re.compile(r"[\"“‘`]([^\"”’`\n]{2,200})[\"”’`]|(?<!\w)'([^'\n]{3,200})'(?!\w)")
_NUMBER = re.compile(r"(?<![\w.])(?:\d+(?:[,.:]\d+)+|\d{3,})(?!\w)")
_UNVERIFIED = "(unverified text)"


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def found(span: str, ocr_text: str, cover: float = 0.85) -> bool:
    """Is `span` in the OCR text? OCR reads quotes, spaces, underscores and punctuation differently from the source, so compare letters and digits only;
    spans of 8+ characters also pass when at least `cover` of them is matched in order by blocks of 3+ characters (an OCR slip, not an invention)."""
    a, h = _alnum(span), _alnum(ocr_text)
    if not a or a in h:
        return True
    if len(a) < 8:
        return False
    blocks = difflib.SequenceMatcher(None, a, h, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 3) / len(a) >= cover


@dataclass
class Description:
    text: str
    verified: bool = False
    unverified: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    ms: int = 0


def spans(text: str) -> list:
    """Quoted spans and multi-digit numbers/times the description asserts."""
    out = [(m.group(1) or m.group(2)).strip() for m in _QUOTE.finditer(text)]
    out += [m.group(0) for m in _NUMBER.finditer(text) if not any(m.group(0) in q for q in out)]
    return out


def ground(text: str, ocr_text: str) -> tuple:
    """Replace every quoted span not present in the OCR text; numbers outside quotes that OCR lacks are only listed. -> (clean_text, dropped)"""
    dropped, clean = [], text

    def fix(m):
        span = (m.group(1) or m.group(2)).strip()
        if found(span, ocr_text):
            return m.group(0)
        dropped.append(span)
        return _UNVERIFIED
    clean = _QUOTE.sub(fix, clean)
    dropped += [n for n in (m.group(0) for m in _NUMBER.finditer(clean)) if not found(n, ocr_text) and n not in dropped]
    return clean, dropped


def _to_part(image) -> dict:
    if isinstance(image, np.ndarray):
        import cv2
        ok, buf = cv2.imencode(".png", image[:, :, ::-1])
        if not ok:
            raise ValueError("png encode failed")
        return image_part(buf.tobytes(), "image/png")
    return image_part(image)   # bytes or path


class Describer:
    def __init__(self, chat=None, bus: ev.EventBus | None = None):
        c = config.get()
        self.chat = chat or Chat(c.describe_model, slot="vision", max_tokens=c.describe_max_tokens, temperature=0.2)
        self.bus = bus or ev.bus

    def describe(self, image, question: str | None = None, ocr_text: str | None = None, think: bool = False) -> Description:
        prompt = ASK.format(q=question) if question else GENERIC
        t0 = time.monotonic()
        r = self.chat.complete([{"role": "user", "content": [{"type": "text", "text": prompt}, _to_part(image)]}], think=think)
        text = r.text.strip()
        if ocr_text is None:
            d = Description(text, False, spans(text))
        else:
            clean, dropped = ground(text, ocr_text)
            d = Description(clean, True, [], dropped)
        d.ms = int((time.monotonic() - t0) * 1000)
        self.bus.emit("screen", "described", ms=d.ms, verified=d.verified, n_quotes=len(d.unverified) + len(d.dropped))
        return d


def _selfcheck():
    from models.fake import FakeChat
    ocr = "Traceback (most recent call last):\n  File app.py, line 12\nKeyError: 'user_id'\nExit code 1  at 14:32"
    t = 'It shows a Python crash: "KeyError: \'user_id\'" at 14:32, and also a toast saying "Disk is full" with code 98765.'
    clean, dropped = ground(t, ocr)
    assert "KeyError" in clean and "Disk is full" not in clean and _UNVERIFIED in clean       # an invented quote is replaced
    assert "Disk is full" in dropped and "98765" in dropped and "14:32" not in dropped         # an invented number is listed, a real one passes
    assert ground("The build log says 'exit code 1'.", ocr)[1] == []                           # single-quoted, case-insensitive match
    assert ground("It failed with \"KeyError: 'user_id'\".", "Traceback\nKeyError :\n' user id'\nline 41")[1] == []   # OCR spacing / underscore differences are not inventions
    assert ground("Error says \"Connection refused by remote host\".", "Connection refused by remote host")[1] == []
    assert ground("Error says \"Connection reset by peer\".", "Connection refused by remote host")[1] == ["Connection reset by peer"]   # a different message is

    assert ground("Nothing notable on screen.", ocr) == ("Nothing notable on screen.", [])
    assert spans("It says \"hello world\" and costs 4,280 or 99 euros") == ["hello world", "4,280"]   # 2-digit numbers are not asserted facts
    b, got = ev.EventBus(), []
    b.subscribe(got.append, sources={"screen"})
    chat = FakeChat([t, t, "A desktop with no windows open."])
    d = Describer(chat, b)
    img = np.zeros((40, 60, 3), dtype=np.uint8)
    r = d.describe(img, question="What is wrong?")
    msg = chat.calls[0]["messages"][0]["content"]
    assert msg[0]["text"].endswith("Question: What is wrong?") and msg[1]["type"] == "image_url" and msg[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert chat.calls[0]["think"] is False
    assert not r.verified and r.text == t and "Disk is full" in r.unverified                    # OCR off: untouched but flagged
    r = d.describe(img, ocr_text=ocr)
    assert r.verified and "Disk is full" not in r.text and r.dropped
    assert chat.calls[1]["messages"][0]["content"][0]["text"].startswith("Describe what is on this screen")
    r = d.describe(b"\x89PNG\r\n\x1a\n")
    assert r.unverified == [] and [e.kind for e in got] == ["described"] * 3 and "text" not in got[0].data
    print("describe ok")


if __name__ == "__main__":
    _selfcheck()
