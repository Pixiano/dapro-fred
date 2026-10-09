"""Address the user as "sir" in about 85% of what FRED says once config.sir_rate is set to 0.85 (default 0.85, 0 = off), decided by code per line, never by the model (a 4B told to do it drifts). Applied at the output edge only (engine/live.py:
the reply that is spoken and shown, interim lines, talker lines), never to what is remembered in history, or the model would start adding it itself and overshoot.

Sockets
  in : sirify(text, rng=random.random) -> str      config.sir_rate (default 0.85, 0 = off)
  out: the same text with ", sir" before the final punctuation ("Done." -> "Done, sir."; "Apply it? Yes or no?" -> "Apply it? Yes or no, sir?"), or appended when there is none;
       unchanged for an empty line, a line that already says sir, or a roll above the rate. The 2-3 word fillers (audio/tts.FILLERS) never pass through here, so they stay bare.
Self-check: python engine/sir.py
"""
import random
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

_SIR = re.compile(r"\bsir\b", re.I)
_WAKE = re.compile(r"^\s*(?:(?:hey|hi|ok|okay|yo)[\s,]+)?fred\b[\s,.!?:-]*", re.I)
_END = re.compile(r"^(.*?)([\s.!?…\"')\]]*)$", re.S)


def sirify(text: str, rng=random.random) -> str:
    if not text or not text.strip() or _SIR.search(text) or rng() >= config.get().sir_rate:
        return text
    body, tail = _END.match(text.rstrip()).groups()
    if not body:
        return text
    return f"{body.rstrip(',;:')}, sir{tail}{text[len(text.rstrip()):]}"


def strip_wake(text: str) -> str:
    """'Hey Fred, how are you?' -> 'How are you?'. A bare 'Hey Fred.' stays whole (it is the whole message)."""
    rest = _WAKE.sub("", text, count=1).strip()
    return rest[:1].upper() + rest[1:] if rest else text


def is_echo(text: str, spoken: list[str]) -> bool:
    """True when the mic heard FRED's own recent words back through the speakers (substring or near match of a line it just said)."""
    words = lambda s: re.findall(r"[a-z0-9']+", s.lower().replace(".", ""))
    t = words(text)
    return len(t) >= 3 and any(sum(w in set(words(s)) for w in t) / len(t) >= 0.8 for s in spoken)      # 3+ words, 80% of them FRED's own (a bare 'yes' to 'yes or no?' is an answer)


def _selfcheck():
    assert strip_wake("Hey Fred, how are you?") == "How are you?" and strip_wake("Fred what's the date") == "What's the date"
    assert strip_wake("Hey Fred.") == "Hey Fred." and strip_wake("Alfred is here") == "Alfred is here"
    said = ["It's 6:46 AM on Friday, 9 October 2026, sir.", "I don't have information about that, sir."]
    assert is_echo("It's 6.46 a.m. on Friday.", said) and is_echo("I don't have information about that sir.", said)
    assert not is_echo("What's the date?", said) and not is_echo("", said) and not is_echo("yes", ["Yes or no, sir?"])
    config.reload()
    config.get().sir_rate = 0.85
    always = lambda: 0.0
    never = lambda: 0.99
    assert sirify("Done.", always) == "Done, sir."
    assert sirify("It's half past three.", always) == "It's half past three, sir."
    assert sirify("Apply it? Yes or no?", always) == "Apply it? Yes or no, sir?"
    assert sirify("Sure thing", always) == "Sure thing, sir"
    assert sirify('He said "go".', always) == 'He said "go, sir".'
    assert sirify("Line one.\nLine two.\n", always) == "Line one.\nLine two, sir.\n"
    assert sirify("Done, sir.", always) == "Done, sir." and sirify("Sir, it is done.", always) == "Sir, it is done."          # never twice
    assert sirify("", always) == "" and sirify("   ", always) == "   " and sirify("...", always) == "..."
    assert sirify("Done.", never) == "Done."
    config.get().sir_rate = 0.0
    assert sirify("Done.", always) == "Done."                                                                                   # off
    config.get().sir_rate = 0.85
    rng = random.Random(7)
    hits = sum("sir" in sirify("Your next meeting is at three.", rng.random) for _ in range(4000))
    assert 0.83 <= hits / 4000 <= 0.87, hits / 4000                                                                             # the rate, not the model's mood
    print("sir ok")


if __name__ == "__main__":
    _selfcheck()
