# Core/tools/vision_tools.py
#
# The on-demand half of the screen watcher (see vision/screen_watcher.py
# for the background half). Every call tries a real on-demand capture
# first (watcher_manager.capture_now()) — this tool is explicitly "look
# now", not "read whatever the passive watcher happened to last see" (see
# the Fine-Tune MVP Plan, Phase 17: LLM_IDLE_UNLOAD_SECONDS is a full
# hour, so under normal use the passive watcher rarely gets a window to
# run at all, and its cache can otherwise sit stale for a very long time).
# That capture attempt is still fail-safe: if the main process has a
# model loaded (the common case, mid-turn) and cloud vision is also
# unavailable (rate-limited, offline), capture_now() reports it couldn't
# get a fresh one and this falls back to the existing cache with an
# honest "just tried and couldn't" hedge, distinct from an ordinary
# staleness hedge — a failed attempt right now reads differently to the
# user than "I haven't looked in a while."
#
# On-demand rather than injected into every turn's context on purpose:
# unlike vault knowledge (which the orchestrator does inject every turn,
# see orchestrator.py's VAULT_CHUNK_INJECT_CHARS tradeoff), screen
# content is only relevant to a small fraction of turns, and paying its
# token cost on every single one wasn't asked for.

from vision import screen_context


def whats_on_screen() -> str:
    """Always takes a fresh look at the screen first (see module
    docstring) — falls back to whatever's cached, with an honest hedge,
    only if nothing's ever been captured or a fresh capture genuinely
    couldn't run right now (no app, no safe local fallback, cloud
    unavailable)."""
    from ui.pill_app import get_current_app

    app = get_current_app()
    captured_fresh = False
    if app is not None:
        captured_fresh = app.screen_watcher.capture_now()

    description, age = screen_context.read()

    if description is None:
        return (
            "I haven't looked at your screen recently — the watcher "
            "only runs after a few minutes away from the hotkey."
        )

    if not captured_fresh and not screen_context.is_fresh(age):
        return (
            f"I tried to look just now but couldn't (vision's unavailable "
            f"right now). The last thing I saw was {int(age // 60)} "
            f"minute(s) ago, which is probably stale: {description}"
        )

    return description
