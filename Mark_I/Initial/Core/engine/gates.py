"""Gates between the model and the machine: reply guards (ported from the old orchestrator), the confirmation gate, read/send separation.

Sockets
  in : claims_completed_action(text, asked) / unsupported_claim(text, results, asked) / reflects_failure(text)   reply guards; `results` = this turn's tool result strings
       Confirmer: .ask(name, args, prompt) stores one pending destructive call;  .resolve(user_text) -> ("run", name, args) | ("cancel",) | None
       untrusted_then_send(spec, read_untrusted) -> True when a `sends` tool is called in a turn that already read untrusted content (web page, mail, file): refused
  out: plain booleans / tuples; no state except Confirmer's single pending slot (it expires after `ttl_s` and on any other kind of reply)
Guards (all deterministic):
  - false completion: the reply says an action is done ("deleted", "set", "engaged"...) but no tool ran, or the file it names appears in no tool result
  - leaked tool syntax: models/toolparse.looks_leaked
  - refusal with tools offered: the reply says "I can't ..." although the menu had tools for the request and none was called -> asked again once, naming the tools
  - failure not reflected: a tool failed this turn and the reply neither admits it nor asks anything
Self-check: python engine/gates.py
"""
import re
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import classify

_ACTION_DONE = re.compile(r"\b(?:deleted|removed|erased|created|moved|renamed|updated|added|saved|written|wrote|scheduled|cancell?ed|muted|unmuted|launched|installed|uninstalled|engaged|lifted|set)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|never|won't|will not|can't|cannot|unable)\b", re.IGNORECASE)
_WRITE_CLAIM = re.compile(r"\b(?:created|wrote|written|saved|appended|updated|deleted|removed|moved|renamed)\b", re.IGNORECASE)
_NAMED_ARTIFACT = re.compile(r"[\w\-.]+\.(?:md|txt|json|jsonl|py|csv|pdf|png|jpg|docx?|xlsx?)\b")           # an extension is required: bare words are not evidence of a claim
_FAILURE_WORDS = re.compile(r"\b(?:couldn't|could not|can't|cannot|didn't|did not|failed|unable|not|no|error|sorry|problem|isn't|wasn't|won't|unavailable|denied|too long)\b", re.IGNORECASE)


def claims_completed_action(text: str, asked: str = "") -> bool:
    """A reply that asserts it finished something. A question is not a claim, nor an answer to a wh-question, nor an honest refusal."""
    t = (text or "").strip()
    if not t or t.endswith("?") or classify.is_info_question(asked) or _NEGATION.search(t):
        return False
    return bool(_ACTION_DONE.search(t))


def unsupported_claim(text: str, results, asked: str = "") -> bool:
    """The reply claims an action the tools that actually ran cannot account for: nothing ran, or it names a file that appears in no result (basename match)."""
    if not claims_completed_action(text, asked):
        return False
    if not results:
        return True
    if not _WRITE_CLAIM.search(text):                       # only a write claim is checked against the artifacts (tools that return documentation say "added", "set"...)
        return False
    blob = " ".join(str(r) for r in results).lower()
    return any((a.replace("\\", "/").split("/")[-1].lower() not in blob) for a in _NAMED_ARTIFACT.findall(text))


def reflects_failure(text: str) -> bool:
    """True if a reply that follows a failed tool says something is wrong (or asks a question) rather than carrying on as if it worked."""
    t = (text or "").strip()
    return bool(_FAILURE_WORDS.search(t)) or t.endswith("?")


_REFUSAL = re.compile(r"\b(?:i (?:can't|cannot|can not|am unable|am not able|'m unable|'m not able|don't have|do not have|have no)|unable to|not able to|no (?:tool|way to|access to))\b", re.IGNORECASE)


def looks_like_refusal(text: str) -> bool:
    """The reply says it cannot do something. Only meaningful when tools WERE offered for the request and none was called: then it is a refusal of something the menu can do."""
    return bool(_REFUSAL.search(text or ""))


def untrusted_then_send(spec, read_untrusted: bool) -> bool:
    """Read/send separation: after reading untrusted content in this turn, a tool that sends something out is refused (prompt-injection guard)."""
    return bool(spec is not None and spec.sends and read_untrusted)


class Confirmer:
    """One pending destructive call. The next user turn answers it: yes -> run, no -> cancel, anything else -> dropped (an unanswered question never executes later)."""

    def __init__(self, ttl_s: float = 60.0):
        self.ttl_s, self._p = ttl_s, None

    def ask(self, name: str, args: dict, prompt: str) -> str:
        self._p = (name, dict(args), time.monotonic())
        return prompt

    @property
    def pending(self):
        if self._p and time.monotonic() - self._p[2] > self.ttl_s:
            self._p = None
        return self._p

    def resolve(self, user_text: str):
        p, self._p = self.pending, None
        if not p:
            return None
        if classify.is_affirmative(user_text):
            return ("run", p[0], p[1])
        if classify.is_negative(user_text):
            return ("cancel",)
        return None


def _selfcheck():
    from tools.registry import ToolSpec
    assert claims_completed_action("Deleted notes.md, sir.") and not claims_completed_action("Shall I delete it?")
    assert not claims_completed_action("I can't delete that.") and not claims_completed_action("Jane Austen wrote it.", "who wrote Emma")
    assert unsupported_claim("Lockdown engaged.", []) and not unsupported_claim("Lockdown engaged.", ["lockdown on"])
    assert unsupported_claim("File created: daily/a.md", ["folder made: daily"])                  # something ran, but not the thing claimed
    assert not unsupported_claim("Saved a.md", ["wrote C:\\x\\a.md"])
    assert not unsupported_claim("It is set to 40.", ["volume 40"])                                  # a non-write claim with evidence passes
    assert reflects_failure("I couldn't open it.") and reflects_failure("Do you want me to retry?") and not reflects_failure("Done, sir. Volume is at 30.")
    assert untrusted_then_send(ToolSpec("m", lambda: 0, sends=True), True) and not untrusted_then_send(ToolSpec("m", lambda: 0, sends=True), False)
    assert looks_like_refusal("I can't capture screenshots.") and looks_like_refusal("Sorry, I don't have access to that.") and not looks_like_refusal("Done, screenshot saved.")
    c = Confirmer(ttl_s=60)
    c.ask("delete_file", {"path": "a"}, "Delete a?")
    assert c.resolve("yes please") == ("run", "delete_file", {"path": "a"}) and c.resolve("yes") is None          # consumed
    c.ask("x", {}, "?")
    assert c.resolve("no") == ("cancel",)
    c.ask("x", {}, "?")
    assert c.resolve("what time is it") is None and c.pending is None
    c.ask("x", {}, "?")
    c.ttl_s = -1
    assert c.resolve("yes") is None                                                                    # expired
    print("gates ok")


if __name__ == "__main__":
    _selfcheck()
