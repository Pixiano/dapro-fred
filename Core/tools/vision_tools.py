# Core/tools/vision_tools.py
#
# The on-demand half of the screen watcher (see vision/screen_watcher.py
# for the background half). Deliberately just a read of whatever the
# watcher last cached — this tool never takes a screenshot itself or
# waits on the Vision model, so it's instant regardless of whether the
# watcher happens to be running right now.
#
# On-demand rather than injected into every turn's context on purpose:
# unlike vault knowledge (which the orchestrator does inject every turn,
# see orchestrator.py's VAULT_CHUNK_INJECT_CHARS tradeoff), screen
# content is only relevant to a small fraction of turns, and paying its
# token cost on every single one wasn't asked for.

from vision import screen_context


def whats_on_screen() -> str:
    """What the background screen watcher last saw, or an honest 'don't
    know' if it hasn't run yet or the result is too old to trust."""
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
