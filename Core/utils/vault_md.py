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
_FRONTMATTER_BLOCK = re.compile(r"^---\n(.*?)\n---\n+", re.DOTALL)
_FRONTMATTER_LINE = re.compile(r"^([\w-]+):\s*(.*)$")
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_SPLIT = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# Table detection for flatten_tables(). A row is any line delimited by
# pipes; the separator is the |---|---| line that must follow a header.
_TABLE_ROW = re.compile(r"^\|.*\|$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")

# Cells that mean "no value recorded" rather than a real datum. Both
# dash forms appear in the vault (en dash is what the editor inserts).
_EMPTY_CELLS = {"", "-", "—", "–", "n/a", "N/A", "TBD"}


def _split_row(line: str) -> list:
    """Cells of one markdown table row, outer pipes discarded."""
    return line.strip().strip("|").split("|")


def flatten_tables(text: str) -> str:
    """
    Rewrite markdown tables as one explicit "Row — Column: value" line
    per row, so a value can't be read out of the wrong column.

    Confirmed necessary 2026-08-02. personal/fitness.md has:

        | Measure                | Baseline | Current | Target |
        | Shoulder circumference | 45"      | —       | 49"    |

    Asked "what is my CURRENT shoulder circumference", FRED answered
    49 inches — the Target. Current is a dash, i.e. genuinely unknown.
    A prose instruction telling the model that columns are meaningful
    and a dash means unknown was tried first and did NOT fix it: an 8B
    model simply doesn't track cell-to-header alignment reliably across
    a row whose cells are mostly short strings. Removing the positional
    parse entirely is deterministic where prompting was not, and it
    turns the row above into:

        Shoulder circumference — Baseline: 45"; Current: (not recorded);
        Target: 49"

    Empty/dash cells become an explicit "(not recorded)" rather than
    being dropped, since silently omitting Current is what let the
    model reach for a neighbouring column in the first place.
    """
    lines = text.split("\n")
    out = []
    i = 0

    while i < len(lines):
        row = _TABLE_ROW.match(lines[i].strip())
        separator = (
            i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1].strip())
        )

        if not (row and separator):
            out.append(lines[i])
            i += 1
            continue

        headers = [c.strip() for c in _split_row(lines[i])]
        i += 2  # skip the header and its |---|---| separator

        while i < len(lines) and _TABLE_ROW.match(lines[i].strip()):
            cells = [c.strip() for c in _split_row(lines[i])]
            label = cells[0] if cells else ""
            parts = []
            for header, cell in zip(headers[1:], cells[1:]):
                value = cell if cell and cell not in _EMPTY_CELLS else "(not recorded)"
                parts.append(f"{header}: {value}")
            out.append(f"{label} — " + "; ".join(parts) if parts else label)
            i += 1

    return "\n".join(out)


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def parse_frontmatter(text: str) -> dict:
    """
    Flat key: value frontmatter fields as a dict of strings — not a
    real YAML parser, but vault frontmatter is always flat scalars
    (type, status, updated, and now optionally deadline for
    orchestrator/proactive_checks.py), so a line split is enough and
    avoids a PyYAML dependency for this one narrow use.
    """
    match = _FRONTMATTER_BLOCK.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        line_match = _FRONTMATTER_LINE.match(line.strip())
        if line_match:
            fields[line_match.group(1)] = line_match.group(2).strip()
    return fields


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
