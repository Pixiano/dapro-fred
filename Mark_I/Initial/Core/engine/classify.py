"""Offer the model a short menu, never the whole one: cue categories + an embedding ranker (ported from the old intent.py / tool_router.py).

Sockets
  in : classify(text, registry, router=None) -> Decision(needs_tools, names, reason)
       Router(registry, embedder).rank(text) -> [(tool, score)] best first;  .route(text, top_k, floor, margin) -> (names, best_score)
       text helpers: normalise, looks_social, is_affirmative, is_negative, is_info_question, looks_compound
  out: Decision.names = the tools to offer; needs_tools False = answer in conversation (no menu at all, so a misfire is impossible, not merely unlikely)
Routing metadata comes from the tools themselves (tools/registry.ToolSpec): `group` is the category, `cues` the regex fragments that make the group a candidate, `examples`
the phrasings embedded for the semantic ranking (the registered description is embedded too). Nothing is listed here per tool.
Order (as in the old FRED, whose cue/threshold lessons are kept): empty -> chat; social/meta phrasing -> chat; cue categories -> their tools, narrowed by the embedder to the
best 2-6 (never below two; a confident embedding hit the cues missed is rescued in; a COMPOUND request keeps the full cue union so no half is lost); no cue -> embedding alone
above `semantic_floor`; otherwise chat (failing to offer a tool is mild, wrongly firing one is not symmetric).
Self-check: python engine/classify.py (fake embedder)
"""
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

# ---- text helpers (verbatim behaviour from the old intent.py) -----------------------------------------------------------------------
_PREFIX = re.compile(r"^(?:\s*(?:hey|hi|hello|yo|ok|okay|so|um+|uh+|please|fred+(?:ie)?|good\s+(?:morning|afternoon|evening|night))\b[\s,.!]*)+", re.IGNORECASE)
_SOCIAL = re.compile(
    r"^(?:how(?:'s| is| are|s)?\s+(?:you|it going|things|your day|everything)\b.*|what(?:'s| is)?\s*up\b.*"
    r"|(?:i'm |im )?(?:good|fine|great|ok|okay|alright)\b.*|(?:thanks?|thank you|cheers|ta|nice|cool|awesome|great|perfect|lovely|sweet)\b.*"
    r"|(?:bye|goodbye|good\s?night|see ya|see you|later)\b.*|(?:who|what)\s+(?:are|r)\s+you\b.*|what(?:'s| is)?\s+your\s+name\b.*"
    r"|what\s+can\s+you\s+do\b.*|tell me about (?:yourself|you)\b.*|(?:are you|you) (?:there|awake|alive|online|ready|listening)\b.*"
    r"|(?:nothing|never ?mind|forget it|no)\b\.?$|(?:yes|yeah|yep|no|nope|sure|maybe)\b\.?$)$", re.IGNORECASE)
_ECHO = re.compile(r"""^(?:just\s+|please\s+)*(?:say|repeat|reply\s+with|respond\s+with)\s+["'‘“].+["'’”](?:\s+and\s+(?:nothing|only|just)\b.*|\s+only)?[\s.!]*$""", re.IGNORECASE | re.VERBOSE)
_INFO_QUESTION = re.compile(r"^(?:who|what|which|when|where|why|how)\b", re.IGNORECASE)
_AFFIRMATIVE = re.compile(r"^(?:(?:yes|yeah|yep|yup|sure|ok|okay|correct|right|affirmative|do it|go ahead|go on|proceed|confirm(?:ed)?|please(?: do)?|that's\s+right|sounds\s+good|and|now)\b[\s,.!]*)+$", re.IGNORECASE)   # "yeah go ahead" counts; anything with a "no" or a question does not
_NEGATIVE = re.compile(r"^(?:no|nope|nah|negative|cancel|don't|do not|stop|never ?mind|forget it|abort|not now)\b[\s,.!]*$", re.IGNORECASE)
_QUESTION_WORD = r"(?:what|when|where|who|which|how|why|is there|do i|did i|can you|could you)"
_COMPOUND_RE = re.compile(r"\b(?:and|also|plus)\b\s+" + _QUESTION_WORD, re.IGNORECASE)
_MULTI_DAY_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
_MULTI_COUNT_RE = re.compile(r"\b(?:two|three|four|five|both|several|multiple)\s+(?:reminders?|timers?|tasks?|alarms?)\b", re.IGNORECASE)
_NUMBER_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
_MULTI_ITEM_RE = re.compile(rf"\b{_NUMBER_WORD}\b.*?\band\b.*?\b{_NUMBER_WORD}\b", re.IGNORECASE)
_ACTION_VERB = (r"(?:open|launch|start|play|close|stop|set|turn|add|remind|schedule|tell|show|check|create|make|send|search|find|mute|unmute|pause|skip|take|save|delete|move|rename|read|list|cancel|go)")
_COMPOUND_ACTION_RE = re.compile(r"\b(?:and|then|also|plus|after that)\b\s+(?:please\s+|proceed\s+to\s+)?" + _ACTION_VERB + r"\b", re.IGNORECASE)


def normalise(text: str) -> str:
    return _PREFIX.sub("", (text or "").strip().lower()).strip(" ,.!?")


def is_info_question(text: str) -> bool:
    return bool(_INFO_QUESTION.match(normalise(text)))


def looks_social(text: str) -> bool:
    s = normalise(text)
    return True if not s else bool(_SOCIAL.match(s) or _ECHO.match(s))


def is_affirmative(text: str) -> bool:
    """A turn that is nothing but agreement: social in isolation, an ANSWER in context (the caller knows which)."""
    return bool(_AFFIRMATIVE.match((text or "").strip()))


def is_negative(text: str) -> bool:
    return bool(_NEGATIVE.match((text or "").strip()))


_DERIVED = re.compile(r"\b(?:until|till|left|since|ago|how many|how long|difference|between|before|after|from now|countdown)\b", re.IGNORECASE)


def needs_reasoning(text: str) -> bool:
    """The answer is derived from a tool result, not the result itself ("how many days until X" needs the date AND arithmetic): the model must see it."""
    return bool(_DERIVED.search(text or ""))


def looks_compound(text: str) -> bool:
    t = text or ""
    return bool(_COMPOUND_RE.search(t) or _COMPOUND_ACTION_RE.search(t) or _MULTI_DAY_RE.search(t) or _MULTI_COUNT_RE.search(t) or _MULTI_ITEM_RE.search(t))


# ---- semantic router ---------------------------------------------------------------------------------------------------------------
class Router:
    """Embeds every tool once (description + examples), then ranks an utterance against them. Cosine similarity is arithmetic: same input, same answer."""

    def __init__(self, registry, embedder):
        self.registry, self.embedder = registry, embedder
        self._vecs: dict[str, list[list[float]]] = {}

    def _build(self):
        names = [n for n in self.registry.names() if n not in self._vecs]
        for n in names:
            s = self.registry.spec(n)
            texts = [t for t in (s.description, *s.examples) if t]
            self._vecs[n] = [self._unit(v) for v in self.embedder.embed(texts)] if texts else []

    @staticmethod
    def _unit(v):
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def rank(self, text: str) -> list:
        self._build()
        q = self._unit(self.embedder.embed([text], is_query=True)[0])
        out = [(n, max((sum(a * b for a, b in zip(q, v)) for v in vs), default=-1.0)) for n, vs in self._vecs.items()]
        return sorted(out, key=lambda x: -x[1])

    def route(self, text: str, top_k: int = 5, floor: float | None = None, margin: float | None = None):
        """(names, best_score). `floor`: nothing below it counts; `margin`: only names within it of the best are kept."""
        r = self.rank(text)
        if not r:
            return [], 0.0
        best = r[0][1]
        fl = config.get().semantic_floor if floor is None else floor
        if best < fl:
            return [], best
        return [n for n, s in r[:top_k] if margin is None or best - s <= margin], best


# ---- classifier --------------------------------------------------------------------------------------------------------------------
@dataclass
class Decision:
    needs_tools: bool
    names: list = field(default_factory=list)
    reason: str = ""


def _group_cues(registry) -> dict:
    out = {}
    for g, names in registry.groups().items():
        frags = [c for n in names for c in registry.spec(n).cues]
        if frags:
            out[g] = re.compile(r"\b(?:" + "|".join(frags) + r")\b", re.IGNORECASE)
    return out


def match_groups(text: str, registry) -> list:
    stripped = re.sub(r"\b(?:time|space)\s+complexity\b", " ", normalise(text), flags=re.I)         # an idiom that contains a cue word without meaning it
    return [g for g, rx in _group_cues(registry).items() if rx.search(stripped)]


def classify(text: str, registry, router: Router | None = None) -> Decision:
    if not (text or "").strip():
        return Decision(False, [], "empty")
    groups = match_groups(text, registry)
    if looks_social(text) and "selfdoc" not in groups and not (router is not None and router.route(text, top_k=1, floor=0.9)[0]):     # "what can you do" is social-shaped but is also a skill's own example
        return Decision(False, [], "social/meta phrasing")
    if groups:
        names = [n for g in groups for n in registry.groups()[g]]
        names = list(dict.fromkeys(names))
        if router is not None and not looks_compound(text):
            ranked = [n for n, _ in router.rank(text)]
            narrowed = [n for n in ranked if n in names][: max(2, min(6, len(names)))]
            top, best = router.route(text, top_k=1)
            rescued = [top[0]] if top and top[0] not in names else []
            cand = rescued + (narrowed or names)
            if len(cand) < len(names) or rescued:
                return Decision(True, cand, f"cues {'+'.join(groups)} {'narrowed+rescued' if rescued else 'narrowed'} by embedding {len(names)}->{len(cand)} (sim {best:.2f})")
        return Decision(True, names, f"cues {'+'.join(groups)} -> {len(names)} tools")
    if router is not None:
        names, best = router.route(text, top_k=5, margin=0.08)
        if names and len(names) < 3:                                                      # never a one-tool menu from embeddings alone: "forget that I like X" sits next to "remember that I like X"
            names = router.route(text, top_k=3)[0]
        if names:
            return Decision(True, names, f"embedding {best:.2f} -> {len(names)} tools")
    return Decision(False, [], "no cue, no embedding hit: chat")


def _selfcheck():
    from models.fake import FakeEmbedder
    from tools.registry import Registry, ToolSpec
    r = Registry()
    r.register(ToolSpec("get_time", lambda: "x", description="current time and date", group="clock", cues=(r"what(?:'s| is) the time", r"what time", r"the date"), examples=("what time is it",)))
    r.register(ToolSpec("set_volume", lambda: "x", description="set the speaker volume", group="audio", cues=(r"volume", r"louder", r"quieter"), examples=("turn it up", "make it quieter")))
    r.register(ToolSpec("mute", lambda: "x", description="mute or unmute the sound", group="audio", cues=(r"mute",), examples=("silence it",)))
    r.register(ToolSpec("launch", lambda: "x", description="open an application", group="apps", cues=(r"open", r"launch"), examples=("open spotify",)))
    rt = Router(r, FakeEmbedder())
    assert classify("", r, rt).reason == "empty"
    assert not classify("Hey Fred, how are you?", r, rt).needs_tools                     # social
    assert not classify("thanks", r, rt).needs_tools
    d = classify("what time is it", r, rt)
    assert d.needs_tools and d.names == ["get_time"], d
    d = classify("turn the volume down", r, rt)
    assert d.needs_tools and set(d.names) <= {"set_volume", "mute"} and "set_volume" in d.names, d
    d = classify("open spotify and turn the volume down", r, rt)                        # compound: the full cue union, nothing narrowed away
    assert set(d.names) == {"set_volume", "mute", "launch"}, d
    assert not classify("time complexity of binary search", r, rt).needs_tools          # idiom, not a clock question
    assert not classify("tell me a joke about cats", r, rt).needs_tools                 # no cue, embedding below the floor
    assert is_affirmative("yeah go ahead") and is_affirmative("sure") and not is_affirmative("yes... actually no, don't") and not is_affirmative("no wait yes") and not is_affirmative("wait what file is that")
    assert is_affirmative("Yes please.") and not is_affirmative("yes open it") and is_negative("no") and not is_negative("nobody")
    assert looks_compound("set the volume to 50 and open chrome") and looks_compound("set two reminders") and not looks_compound("what time is it")
    assert needs_reasoning("how many days until christmas") and not needs_reasoning("what time is it")
    assert is_info_question("who wrote Emma") and not is_info_question("delete it")
    assert rt.route("tell me a joke about cats")[0] == [] and rt.rank("what time is it")[0][0] == "get_time"
    print("classify ok")


if __name__ == "__main__":
    _selfcheck()
