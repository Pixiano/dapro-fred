# Core/tools/git_tools.py
#
# Suggestion #1 from the 2026-08-01 feedback session, read-only scope
# (confirmed with Vatsal — write access, i.e. commit/push, is a
# separate, bigger risk category and deliberately not built here).
#
# Every function below is a status/log/diff-summary READ. Nothing here
# ever mutates repo state — no add, commit, push, checkout, or reset.
# subprocess is called with a fixed argument list (never shell=True,
# never a string built from user input), so repo_path only ever
# selects the working directory a fixed command runs in — it can't be
# used to inject an arbitrary git subcommand.

import os
import subprocess
from pathlib import Path

from tools.assist_tools import resolve_user_path
from config.settings import BASE_DIR

# BASE_DIR is Project_FRED/Core; the repo root (where .git lives) is
# one level up. Used when repo_path is blank, since "what's changed"
# almost always means this project.
_DEFAULT_REPO = BASE_DIR.parent


def _resolve_repo(repo_path: str) -> Path:
    """
    Resolve the repo to operate on, falling back to the FRED project
    when the given path isn't a git repo.

    Small models invent this argument rather than omitting it —
    confirmed: "show me my recent GitHub commits" produced
    repo_path="Projects\\Claude", which resolve_user_path anchored under
    Documents/FRED into a path that doesn't exist, and the turn died on
    "isn't a git repository" instead of answering about the project
    actually in front of him. A guessed path that isn't a repo is worth
    strictly less than the sensible default, so it loses to it.
    """
    if not repo_path:
        return _DEFAULT_REPO

    candidate = Path(os.path.expandvars(repo_path)).expanduser()
    if not candidate.is_absolute():
        candidate = resolve_user_path(repo_path)

    if _is_git_repo(candidate):
        return candidate

    return _DEFAULT_REPO


def _run_git(repo: Path, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=15,
    )


def _is_git_repo(repo: Path) -> bool:
    if not repo.exists():
        return False
    result = _run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_status(repo_path: str = "") -> str:
    """
    Current branch plus a speech-safe count/summary of staged,
    modified, and untracked files. Never the raw `git status` porcelain
    output — same reasoning as search_files: a small model tends to
    parrot raw structured text rather than summarise it.
    """

    repo = _resolve_repo(repo_path)

    if not _is_git_repo(repo):
        return f"{repo} isn't a git repository."

    branch = _run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    porcelain = _run_git(repo, ["status", "--porcelain"]).stdout

    staged, modified, untracked = [], [], []
    for line in porcelain.splitlines():
        if not line:
            continue
        code, name = line[:2], line[3:]
        if code[0] not in (" ", "?"):
            staged.append(name)
        elif code[1] == "M":
            modified.append(name)
        elif code == "??":
            untracked.append(name)

    if not (staged or modified or untracked):
        return f"On branch {branch}, working tree clean."

    parts = [f"On branch {branch}:"]
    if staged:
        parts.append(f"{len(staged)} staged ({', '.join(staged[:5])})")
    if modified:
        parts.append(f"{len(modified)} modified ({', '.join(modified[:5])})")
    if untracked:
        parts.append(f"{len(untracked)} untracked ({', '.join(untracked[:5])})")

    return " ".join(parts)


def git_log(repo_path: str = "", count: int = 5) -> str:
    """Recent commits, most recent first — subject and relative time only."""

    repo = _resolve_repo(repo_path)

    if not _is_git_repo(repo):
        return f"{repo} isn't a git repository."

    count = max(1, min(int(count), 20))
    result = _run_git(
        repo, ["log", f"-{count}", "--pretty=format:%s|%ar"]
    )

    if result.returncode != 0:
        return f"Couldn't read history: {result.stderr.strip() or 'no commits yet'}"

    lines = [l for l in result.stdout.splitlines() if l]
    if not lines:
        return "No commits yet."

    entries = []
    for line in lines:
        subject, _, when = line.partition("|")
        entries.append(f"{subject} ({when})" if when else subject)

    return f"Last {len(entries)} commit(s): " + "; ".join(entries)


def git_diff_summary(repo_path: str = "") -> str:
    """
    What's changed but not committed — file count and total lines
    added/removed, not the actual diff body (which is exactly the kind
    of thing that should never be read aloud verbatim).
    """

    repo = _resolve_repo(repo_path)

    if not _is_git_repo(repo):
        return f"{repo} isn't a git repository."

    unstaged = _run_git(repo, ["diff", "--stat"]).stdout
    staged = _run_git(repo, ["diff", "--cached", "--stat"]).stdout

    def _summarize(stat_output: str) -> str:
        lines = [l for l in stat_output.splitlines() if l]
        if not lines:
            return ""
        # Last line is the "N files changed, X insertions(+), Y deletions(-)"
        # summary git itself already produces — no need to re-derive it.
        return lines[-1].strip()

    unstaged_summary = _summarize(unstaged)
    staged_summary = _summarize(staged)

    if not unstaged_summary and not staged_summary:
        return "No uncommitted changes."

    parts = []
    if staged_summary:
        parts.append(f"Staged: {staged_summary}")
    if unstaged_summary:
        parts.append(f"Unstaged: {unstaged_summary}")

    return ". ".join(parts)
