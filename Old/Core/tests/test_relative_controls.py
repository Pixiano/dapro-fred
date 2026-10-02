# "Turn it up a bit" is how people actually talk to an assistant.
# Before these routes, the only volume setter took an absolute number,
# so a request that names no number had to be guessed at by the model.

from orchestrator.dispatcher import Dispatcher


def test_bare_direction():
    d = Dispatcher()
    result = d.match("turn it up")
    assert result["tool"] == "adjust_volume"
    assert result["arguments"] == {"direction": "up", "amount": "normal"}


def test_hedged_amount_is_smaller():
    d = Dispatcher()
    assert d.match("turn it down a bit")["arguments"] == {
        "direction": "down", "amount": "small",
    }
    assert d.match("turn it up a lot")["arguments"] == {
        "direction": "up", "amount": "large",
    }


def test_comparatives_carry_their_own_direction():
    d = Dispatcher()
    assert d.match("louder")["arguments"]["direction"] == "up"
    assert d.match("quieter")["arguments"]["direction"] == "down"


def test_brightness_has_the_same_shape():
    d = Dispatcher()
    result = d.match("brightness down a bit")
    assert result["tool"] == "adjust_brightness"
    assert result["arguments"] == {"direction": "down", "amount": "small"}

    assert d.match("dimmer")["tool"] == "adjust_brightness"


def test_absolute_volume_still_wins_when_a_number_is_given():
    d = Dispatcher()
    assert d.match("set volume to 30") == {
        "tool": "set_volume", "arguments": {"level": 30},
    }
