"""Address the user as "sir" in about 85% of what FRED says once config.sir_rate is set to 0.85 (default 0.0 = dormant until the P8 tuning pass), decided by code per line, never by the model (a 4B told to do it drifts). Applied at the output edge only (engine/live.py:
the reply that is spoken and shown, interim lines, talker lines), never to what is remembered in history, or the model would start adding it itself and overshoot.

Sockets
  in : sirify(text, rng=random.random) -> str      config.sir_rate (default 0.0 = off; the target is 0.85)
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
_END = re.compile(r"^(.*?)([\s.!?…\"')\]]*)$", re.S)


def sirify(text: str, rng=random.random) -> str:
    if not text or not text.strip() or _SIR.search(text) or rng() >= config.get().sir_rate:
        return text
    body, tail = _END.match(text.rstrip()).groups()
    if not body:
        return text
    return f"{body.rstrip(',;:')}, sir{tail}{text[len(text.rstrip()):]}"


def _selfcheck():
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
