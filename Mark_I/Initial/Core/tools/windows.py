"""Tools: list and focus the open desktop windows.

Sockets
  in : register(registry) adds list_windows, focus_window
  out: titles as text; focus matches by case-insensitive title substring (the best match is the shortest title that contains it); failures ToolError(not_found | unavailable)
Closing/minimising windows (destructive for unsaved work) comes with the confirmation-gated group in 3c.
Self-check: python tools/windows.py (pure matching logic; the real desktop is touched by tests/live_tools.py)
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolError, ToolSpec


def pick(titles, query: str):
    """Best window for a spoken name: all titles containing the query, shortest first (so 'chrome' beats a long page title that merely mentions it)."""
    q = query.strip().lower()
    hits = sorted((t for t in titles if q and q in t.lower()), key=len)
    return hits[0] if hits else None


def _windows() -> list:
    try:
        import win32gui
        out = []
        win32gui.EnumWindows(lambda h, _: out.append((h, win32gui.GetWindowText(h))) if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip() else None, None)
        return out
    except Exception as ex:
        raise ToolError("unavailable", "I can't see the windows right now.", f"EnumWindows: {ex!r}")


def list_windows() -> str:
    return "\n".join(t for _, t in _windows()) or "(no windows)"


def focus_window(title: str) -> str:
    import win32con
    import win32gui
    ws = _windows()
    best = pick([t for _, t in ws], title)
    if best is None:
        raise ToolError("not_found", f"I can't find a window called {title}.", f"no window title contains {title!r}")
    h = next(h for h, t in ws if t == best)
    try:
        if win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(h)
    except Exception as ex:                                                        # Windows refuses foreground steals sometimes; say so, never pretend
        raise ToolError("failed", f"Windows wouldn't let me switch to {best[:40]}.", f"SetForegroundWindow: {ex!r}")
    return f"Switched to {best[:60]}."


def register(reg):
    cues = (r"windows?", r"switch to", r"bring up", r"go to (?:my )?(?:chrome|browser|editor|terminal|code)", r"what(?:'s| have i got| do i have) open")
    reg.register(ToolSpec("list_windows", list_windows, description="List the titles of the open desktop windows.", group="windows", cues=cues,
                          examples=("what have I got open", "show my open windows")))
    reg.register(ToolSpec("focus_window", focus_window, {"type": "object", "required": ["title"], "properties": {"title": {"type": "string", "description": "part of the window title"}}},
                          "Bring an open window to the front by (part of) its title.", group="windows", say=True, cues=cues, examples=("switch to chrome", "bring up my editor")))


def _selfcheck():
    ts = ["Inbox - Gmail - Google Chrome", "Google Chrome", "notes.md - Visual Studio Code", "Settings"]
    assert pick(ts, "chrome") == "Google Chrome" and pick(ts, "CODE") == "notes.md - Visual Studio Code" and pick(ts, "spotify") is None and pick(ts, " ") is None
    from tools.registry import Registry
    r = Registry()
    register(r)
    assert r.call("focus_window", {}).error_kind == "bad_args"
    print("windows ok")


if __name__ == "__main__":
    _selfcheck()
