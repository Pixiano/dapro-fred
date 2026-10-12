"""The system prompt: who FRED is (persona/profile/rules read from outside the repo) plus the spoken-reply rules.

Sockets
  in : build_system(persona_dir=None) -> str
       persona files: persona.md, profile.md, rules.md, active-priorities.md, read from `settings.vault_dir` if set, else `<data_dir>/persona/`; YAML front matter is stripped.
       Their content is personal data: it is read at start, never copied into the repo, logged or quoted in tests (tests use a temp dir with invented files).
  out: one string. Missing/empty persona files -> the built-in fallback persona, so FRED always runs coherently. The memory recall block (3b) is added per turn by the engine, not here.
Self-check: python engine/prompt.py
"""
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

FILES = ("persona.md", "profile.md", "rules.md", "active-priorities.md")

FALLBACK = """You are F.R.E.D., a calm, capable, slightly witty voice assistant on Vatsal's computer. Concise and natural, never corporate, never a cartoon.
Never invent capabilities. Never claim an action succeeded unless a tool result in this conversation says so; if a tool failed, say so plainly. Be transparent about limits."""

SPOKEN = """Your reply is spoken aloud: plain sentences, no markdown, no lists, no emojis; under about 25 words for simple things; a question that needs an explanation gets three or four short sentences.
For general knowledge, explanations, comparisons and opinions answer directly from what you know, and when asked to choose or recommend, pick one and say why. Never mention your tools or what they cannot do unless asked what you can do.
Use a tool only when the request needs one; a chat question gets a chat answer. For any arithmetic use the calculator, never work it out yourself.
If a tool fails or is unavailable, say what failed in one sentence; do not retry the same call."""


def _strip_front_matter(t: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", t, count=1, flags=re.DOTALL).strip()


def build_system(persona_dir=None) -> str:
    d = Path(persona_dir) if persona_dir else (Path(config.get().vault_dir) if config.get().vault_dir else config.data_dir() / "persona")
    parts = []
    for name in FILES:
        try:
            t = _strip_front_matter((d / name).read_text(encoding="utf-8"))
        except OSError:
            continue
        if t:
            parts.append(t)
    return "\n\n---\n\n".join(parts or [FALLBACK]) + "\n\n---\n\n" + SPOKEN


def _selfcheck():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        assert FALLBACK in build_system(t) and SPOKEN in build_system(t)                      # nothing there: fallback
        (Path(t) / "persona.md").write_text("---\ntitle: x\n---\nINVENTED PERSONA LINE", encoding="utf-8")
        (Path(t) / "rules.md").write_text("INVENTED RULE", encoding="utf-8")
        (Path(t) / "profile.md").write_text("   ", encoding="utf-8")
        s = build_system(t)
        assert s.startswith("INVENTED PERSONA LINE") and "INVENTED RULE" in s and "title: x" not in s and FALLBACK not in s and s.endswith(SPOKEN)
    print("prompt ok")


if __name__ == "__main__":
    _selfcheck()
