# Tool-layer regressions: STT noise in arguments, and small models
# inventing plausible-but-wrong path arguments. Both classes were
# confirmed from session logs, both silently killed whole turns.

from pathlib import Path

from tools import git_tools, system_tools


# ---------------------------------------------------------------
# launch_application — trailing STT punctuation
# ---------------------------------------------------------------

def test_trailing_punctuation_is_stripped_from_app_name():
    """
    Real transcripts: every logged Spotify launch failure had a
    trailing period from transcription ("Spotify."). Because the name
    didn't end in ".exe", the resolver appended one and looked for
    "Spotify..exe", which exists nowhere.
    """
    strip = system_tools._TRAILING_NOISE.sub
    assert strip("", "Spotify.".strip()).strip() == "Spotify"
    assert strip("", "Spotify now.".strip()).strip() == "Spotify"
    assert strip("", "LM Studio".strip()).strip() == "LM Studio"


# ---------------------------------------------------------------
# git tools — invented repo paths
# ---------------------------------------------------------------

def test_guessed_repo_path_falls_back_to_the_fred_repo():
    """
    Real transcript: "show me my recent GitHub commits" produced a
    guessed relative repo path, which resolved to a non-existent
    directory and killed the turn with "isn't a git repository"
    instead of answering about the project actually in front of him.
    """
    answer = git_tools.git_status(repo_path="Projects\\SomeGuessedRepo")
    assert "isn't a git repository" not in answer
    assert "On branch" in answer or "working tree clean" in answer


def test_unexpanded_env_var_path_does_not_kill_the_turn():
    answer = git_tools.git_status(repo_path="%USERPROFILE%\\definitely-not-here")
    assert "isn't a git repository" not in answer


def test_blank_repo_path_uses_the_fred_project():
    assert git_tools._resolve_repo("") == git_tools._DEFAULT_REPO


# ---------------------------------------------------------------
# search_files — speech safety
# ---------------------------------------------------------------

def test_search_files_does_not_return_raw_absolute_paths():
    """
    FRED's replies are spoken aloud; persona.md forbids reading paths
    out. The tool formats its own result rather than trusting the
    follow-up phrasing pass to strip them.
    """
    from tools.machine_tools import search_files

    result = search_files("settings", str(Path(__file__).resolve().parent.parent / "config"))
    assert "settings.py" in result
    assert ":\\" not in result  # no "C:\..." anywhere in spoken output
