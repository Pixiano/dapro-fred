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
# get a fresh one — at which point this tool takes the one deliberate
# extra step of unloading the main model itself and forcing a
# local-only retry (2026-08-12, Vatsal's explicit call: cloud was
# 429-ing essentially all day, and the existing local Vision fallback
# was structurally unreachable during any real conversation because
# the main model is always resident then). The main model isn't
# manually reloaded afterward — the orchestrator's next generate() call
# does that transparently via _get_model's normal lazy load, same as
# any other tier switch. Only if THAT also fails does this fall back to
# the cache with an honest "just tried and couldn't" hedge, distinct
# from an ordinary staleness hedge.
#
# On-demand rather than injected into every turn's context on purpose:
# unlike vault knowledge (which the orchestrator does inject every turn,
# see orchestrator.py's VAULT_CHUNK_INJECT_CHARS tradeoff), screen
# content is only relevant to a small fraction of turns, and paying its
# token cost on every single one wasn't asked for.

from vision import screen_context


def whats_on_screen(question: str = "") -> str:
    """Always takes a fresh look at the screen first (see module
    docstring) — falls back to whatever's cached, with an honest hedge,
    only if nothing's ever been captured or a fresh capture genuinely
    couldn't run right now (no app, no safe local fallback, cloud
    unavailable).

    question: what the user actually wants to know about the screen —
    e.g. "is my English correct in this paragraph", "what does this
    error say". Confirmed live 2026-08-13: with no way to pass this
    through, the vision model only ever got a generic "describe the
    app and activity" prompt, and the separate text-only turn had to
    answer the user's real question from that generic blurb alone —
    which is why it so often didn't actually answer what was asked.
    Optional, not required: omitted, this behaves exactly as before
    (screen_watcher._prompt_for falls back to the generic prompt)."""
    from ui.pill_app import get_current_app

    app = get_current_app()
    captured_fresh = False
    if app is not None:
        captured_fresh = app.screen_watcher.capture_now(question=question)

        if not captured_fresh:
            # Cloud failed (or was rate-limited) and the main model was
            # resident, so the passive local-fallback check inside that
            # first attempt skipped local entirely — the exact gap this
            # forces past. Only worth retrying if something was actually
            # loaded to free; if unload() drops nothing, local was
            # already tried on the first pass and already failed too.
            if app.orchestrator.llm.unload():
                captured_fresh = app.screen_watcher.capture_now(force_local=True, question=question)

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


def look_through_camera(question: str = "") -> str:
    """
    Captures whatever the desk webcam is pointed at right now and
    describes it — "what am I looking at", "read this for me", etc.

    Vatsal's explicit call 2026-08-21: this must use the local webcam
    (input/presence.py's PRESENCE_CAMERA_INDEX), not the paired phone's
    camera over ADB — the two are physically different cameras pointed
    at different things, and "look through the camera" meant the one on
    the desk. phone_tools.capture_camera_photo() is untouched and still
    used elsewhere for actual phone-camera requests.

    Capture pattern lifted from presence.py's poll_once() (open, grab
    one frame, release immediately) rather than imported — same reason
    focus_checkin.py's own _capture_frame() lifts it instead of
    importing: poll_once() also does face-matching and mutates
    presence_state.json, neither of which this needs.

    Purely on-demand, no cache, no background process: unlike the screen
    watcher (a separate OS process specifically because it polls
    continuously and must never collide with a live conversation turn's
    inference call — see screen_watcher.py's module docstring), this is
    a single capture+describe pair that only ever runs as part of
    handling this exact tool call, sequentially with the turn that
    triggered it, never concurrently with another inference call. That's
    the same safety property whats_on_screen()'s forced-local retry
    above already relies on when it calls straight into
    app.orchestrator.llm from a tool function — no separate process
    needed here for the same reason.
    """
    import base64

    import cv2

    from config.settings import PRESENCE_CAMERA_INDEX

    cap = cv2.VideoCapture(PRESENCE_CAMERA_INDEX)
    try:
        if not cap.isOpened():
            return "I couldn't open the webcam just now."
        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok:
        return "I couldn't grab a frame from the webcam just now."

    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return "Captured a webcam frame but couldn't encode it."

    from ui.pill_app import get_current_app

    app = get_current_app()
    if app is None:
        return "Camera captured, but there's no running app to describe it with."

    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    data_uri = f"data:image/jpeg;base64,{b64}"

    prompt = (
        f"Looking through this camera, answer this question as directly "
        f"and specifically as possible: {question}"
        if question else
        "Describe what this camera is pointed at right now — the general "
        "scene and any specific text, objects, or people that stand out."
    )

    try:
        return app.orchestrator.llm.describe_image(data_uri, prompt, max_tokens=300)
    except Exception as e:
        return f"I captured the camera view but couldn't describe it: {e}"
