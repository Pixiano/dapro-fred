# Core/utils/vault_md.py
#
# Small, shared markdown helpers for reading vault files. Used by both
# personality/system_prompt.py (loads persona/profile/rules directly) and
# orchestrator/vault_router.py (chunks and embeds everything else) — kept
# here once rather than duplicated, since a frontmatter-parsing regex
# living in two places is exactly the kind of drift the vault's own
# rules.md warns about ("a fact rediscovered twice should have been
# written down").

import re

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n+", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_SPLIT = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def extract_h1_title(text: str) -> str:
    """The file's own H1 heading, or '' if it doesn't have one — used as
    a provenance prefix so a retrieved chunk identifies itself."""
    match = _H1.search(text)
    return match.group(1).strip() if match else ""


def split_sections(text: str):
    """
    Split frontmatter-stripped body into (heading, body) pairs on H2 (##)
    boundaries — the boundary every sampled vault file actually uses
    consistently (checked against persona.md, profile.md, rules.md,
    board-exams.md, active-priorities.md, machine.md, jobs/_TEMPLATE.md
    before writing this).

    Content between the H1 title and the first H2 becomes one
    ("(intro)", ...) section rather than being dropped — several files
    put real content there (board-exams.md's opening blockquote, for
    instance). A file with no H2 headers at all yields a single
    ("(whole file)", ...) section covering everything after the H1.
    """
    body = _H1.sub("", text, count=1).strip()

    headers = list(_H2_SPLIT.finditer(body))
    if not headers:
        return [("(whole file)", body)] if body else []

    sections = []
    intro = body[: headers[0].start()].strip()
    if intro:
        sections.append(("(intro)", intro))

    for i, match in enumerate(headers):
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        heading = match.group(1).strip()
        section_body = body[start:end].strip()
        if section_body:
            sections.append((heading, section_body))

    return sections
