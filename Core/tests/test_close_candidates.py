# The pill disambiguation chip: FRED still picks and acts on the top
# tool candidate (never blocks), but shows both when the router found a
# genuine near-tie, so a wrong pick is visible instead of silent.

from orchestrator import intent


class FakeRouter:
    """Fixed ranking, so the margin logic can be tested without a real
    embedding model."""

    def __init__(self, ranking):
        self._ranking = ranking  # [(name, score), ...] already sorted

    def rank(self, text):
        return self._ranking


def test_close_pair_is_detected_within_margin():
    router = FakeRouter([
        ("set_volume", 0.81),
        ("set_brightness", 0.80),  # 0.01 apart — well within margin
        ("mute", 0.40),
    ])
    result = intent.close_candidates(
        "turn it up", ["set_volume", "set_brightness", "mute"], router
    )
    assert result == ("set_volume", "set_brightness")


def test_clear_winner_is_not_flagged_as_ambiguous():
    router = FakeRouter([
        ("kill_process", 0.90),
        ("close_window", 0.50),  # far apart — not a near-tie
    ])
    result = intent.close_candidates(
        "kill chrome", ["kill_process", "close_window"], router
    )
    assert result is None


def test_single_candidate_cannot_be_ambiguous():
    router = FakeRouter([("get_current_time", 0.95)])
    assert intent.close_candidates("what time is it", ["get_current_time"], router) is None


def test_no_router_returns_none_rather_than_erroring():
    assert intent.close_candidates("turn it up", ["set_volume", "set_brightness"], None) is None


def test_only_offered_tools_are_considered():
    """A close score against a tool that ISN'T actually in tool_names
    (e.g. filtered out upstream) must not count."""
    router = FakeRouter([
        ("get_weather", 0.85),   # not in tool_names below
        ("set_volume", 0.80),
        ("set_brightness", 0.40),
    ])
    result = intent.close_candidates(
        "turn it up", ["set_volume", "set_brightness"], router
    )
    assert result is None  # 0.80 vs 0.40 is not close
