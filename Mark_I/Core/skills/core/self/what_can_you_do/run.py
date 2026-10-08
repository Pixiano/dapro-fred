"""what_can_you_do: FRED reads its own skills as text. No embedder: it walks the registry the loader built from the skill folders, so it can never describe a skill that is not installed.

Sockets
  in : run(topic="")   "" -> groups with their skill names; a group name -> its skills, one line each; a skill name -> description, arguments, examples, not-for;
                       other words -> up to 5 skills whose name, description or examples contain the most of those words
  out: plain text, never longer than MAX_CHARS. Words that match no skill are a normal answer ("I don't have a skill for X" plus the group list), not an error.
        With many skills it stays short by drilling down: groups first, then a group, then one skill; what was cut is said ("N more: ask about <group>")
  bind(ctx): needs ctx["registry"] (the loader sets it)
Scaling: 50 skills in ~10 groups is one short index line per group; a group of 20 shows the first lines plus "N more"; search finds a skill without listing anything else.
"""
import re

from skills import Unavailable

MAX_CHARS = 1200
_ctx = {}


def bind(ctx):
    if "registry" not in ctx:
        raise Unavailable("no registry")
    _ctx["c"] = ctx


def _reg():
    return _ctx["c"]["registry"]


def _first(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def _cut(lines: list, hint: str) -> str:
    out, used = [], 0
    for i, ln in enumerate(lines):
        if used + len(ln) + 1 > MAX_CHARS:
            out.append(f"({len(lines) - i} more. {hint})")
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def _detail(spec) -> str:
    props = spec.schema.get("properties", {})
    req = set(spec.schema.get("required", []))
    args = "; ".join(f"{k}{'*' if k in req else ''}: {v.get('description', v.get('type', ''))}" for k, v in props.items()) or "none"
    parts = [f"{spec.name} ({spec.group}): {spec.description}", f"Arguments (* needed): {args}", "Examples: " + "; ".join(spec.examples[:4])]
    if spec.not_for:
        parts.append("Not for: " + "; ".join(spec.not_for))
    if spec.destructive:
        parts.append("It asks for confirmation first.")
    return _cut(parts, "")


def run(topic: str = "") -> str:
    reg, t = _reg(), topic.strip().lower()
    groups = reg.groups()
    if not t:
        return _cut([f"{g} ({len(n)}): {', '.join(n)}" for g, n in sorted(groups.items())], "Ask about a group or a skill by name.")
    if t in groups:
        return _cut([f"{n}: {_first(reg.spec(n).description)}" for n in groups[t]], f"Ask about a skill by name.")
    if reg.spec(t):
        return _detail(reg.spec(t))
    words = [w for w in re.findall(r"[a-z0-9]+", t) if (len(w) > 2 or w.isdigit()) and w not in {"the", "you", "can", "your", "skill", "skills", "tool", "tools", "what", "does", "how", "about", "for", "and"}]
    scored = []
    for n in reg.names():
        s = reg.spec(n)
        have = set(re.findall(r"[a-z0-9]+", " ".join([n.replace("_", " "), s.group, s.description, *s.examples]).lower()))          # whole words, so "7" does not match "17"
        hit = sum(w in have for w in words)
        if hit:
            scored.append((hit, n))
    if not scored:
        # "do you have X?" with the answer no is a successful answer, not a failure (an error here made the engine stop the tool and report a fault)
        return _cut([f"I don't have a skill for {topic}. What I do have, by group:"] + [f"{g} ({len(n)}): {', '.join(n)}" for g, n in sorted(groups.items())], "")
    scored.sort(key=lambda x: (-x[0], x[1]))
    return _cut([f"{n}: {_first(reg.spec(n).description)}" for _, n in scored[:5]], "Ask about a skill by name.")
