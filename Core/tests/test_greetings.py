# The greeting is the one thing FRED says without being asked, so the
# house rules for it are worth pinning: every line addresses the user as
# "sir", and none is phrased as a question (a greeting that asks
# something then goes silent reads as though FRED is waiting for an
# answer it isn't recording).

import time

from audio import greetings


def _at(hour):
    return time.struct_time((2026, 8, 2, hour, 0, 0, 6, 214, -1))


def test_every_greeting_addresses_the_user_as_sir():
    missing = [g for g in greetings.ALL_GREETINGS if "sir" not in g.lower()]
    assert not missing, missing


def test_no_greeting_is_a_question():
    asking = [g for g in greetings.ALL_GREETINGS if "?" in g]
    assert not asking, asking


def test_time_bands_cover_the_whole_clock():
    bands = {greetings._band(h) for h in range(24)}
    assert bands == {"morning", "afternoon", "evening", "night"}
    for hour, want in ((0,"night"), (5,"morning"), (11,"morning"), (12,"afternoon"),
                       (16,"afternoon"), (17,"evening"), (21,"evening"), (22,"night")):
        assert greetings._band(hour) == want, (hour, greetings._band(hour))


def test_pick_can_return_the_matching_time_line_but_is_not_stuck_on_it():
    """The time-of-day line competes with the neutrals rather than always
    winning, so two restarts in one afternoon don't say the same thing."""
    drawn = {greetings.pick_greeting(_at(14)) for _ in range(300)}
    assert greetings.BY_TIME["afternoon"] in drawn
    assert len(drawn) > 1


def test_pick_never_offers_another_bands_line():
    drawn = {greetings.pick_greeting(_at(8)) for _ in range(300)}
    for band, line in greetings.BY_TIME.items():
        if band != "morning":
            assert line not in drawn, line


def test_pick_survives_a_broken_clock():
    """Falls back to a neutral rather than raising — the greeting is
    cosmetic and must never be able to fail start-up."""
    assert greetings.pick_greeting(object()) in greetings.NEUTRAL
