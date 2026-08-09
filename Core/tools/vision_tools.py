# Core/tools/vision_tools.py
#
# The on-demand half of the screen watcher (see vision/screen_watcher.py
# for the background half). Usually just a read of whatever the watcher
# last cached — but a cache older than _FORCE_CAPTURE_AGE_SECONDS
# triggers a real on-demand capture (watcher_manager.capture_now())
# rather than handing back something that might be stale by hours, as
# the passive background watcher's own gating can leave it (see the
# Fine-Tune MVP Plan, Phase 17: LLM_IDLE_UNLOAD_SECONDS is a full hour,
# so under normal use the watcher rarely gets a window to run at all).
# That capture attempt is still fail-safe: if the main process has a
# model loaded (the common case, mid-turn), it's skipped and this falls
# back to the existing cache + honest staleness hedge, same as before.
#
# On-demand rather than injected into every turn's context on purpose:
# unlike vault knowledge (which the orchestrator does inject every turn,
# see orchestrator.py's VAULT_CHUNK_INJECT_CHARS tradeoff), screen
# content is only relevant to a small fraction of turns, and paying its
# token cost on every single one wasn't asked for.

from vision import screen_context

_FORCE_CAPTURE_AGE_SECONDS = 180  # 3 minutes


def whats_on_screen() -> str:
    """What the background screen watcher last saw. If that's more than
    three minutes old, tries a real on-demand capture first (see
    module docstring) — falls back to an honest 'don't know' / 'stale'
    if nothing's been captured yet, or a capture can't run safely right
    now."""
    description, age = screen_context.read()

    if age is None or age > _FORCE_CAPTURE_AGE_SECONDS:
        from ui.pill_app import get_current_app
        app = get_current_app()
        if app is not None:
            app.screen_watcher.capture_now()
            description, age = screen_context.read()

    if description is None:
        return (
            "I haven't looked at your screen recently — the watcher "
            "only runs after a few minutes away from the hotkey."
        )

    if not screen_context.is_fresh(age):
        return (
            f"The last thing I saw was {int(age // 60)} minute(s) ago, "
            f"which is probably stale: {description}"
        )

    return description
