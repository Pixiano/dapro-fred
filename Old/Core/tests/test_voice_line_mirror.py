# PillApp mirrors the pill window's state/level onto the ~/voice-line/
# file bus that the HUD reads. The mirroring is done by wrapping the two
# window setters once in _mirror_window_to_bus, rather than adding a
# publish call at each of the ~8 sites that change pill state — so what
# these tests pin is that the wrapper both publishes AND still drives the
# real window, and that a broken publisher can never take the pill down
# with it.
#
# VoiceLineBus itself (staleness heartbeat, stomp-safety, throttling,
# alert holds) is covered end-to-end against the real reader in
# hud/test_wiring.py. PillApp.__init__ needs real audio hardware, so
# these construct a bare instance via __new__ — same pattern as
# test_turn_dedup.py.

from ui.pill_app import PillApp


class FakeWindow:
    def __init__(self):
        self.states = []
        self.levels = []

    def set_state(self, state):
        self.states.append(state)

    def set_level(self, level):
        self.levels.append(level)


class FakeBus:
    def __init__(self):
        self.states = []
        self.levels = []

    def set_state(self, state):
        self.states.append(state)

    def set_level(self, level):
        self.levels.append(level)


class ExplodingBus:
    def set_state(self, state):
        raise RuntimeError("bus is on fire")

    def set_level(self, level):
        raise RuntimeError("bus is on fire")


def _wired(bus=None):
    app = PillApp.__new__(PillApp)
    app.window = FakeWindow()
    app.voice_line = bus or FakeBus()
    app._mirror_window_to_bus()
    return app


def test_state_reaches_both_the_window_and_the_bus():
    app = _wired()
    app.window.set_state("thinking")
    assert app.window.states == ["thinking"]
    assert app.voice_line.states == ["thinking"]


def test_level_reaches_both_the_window_and_the_bus():
    app = _wired()
    app.window.set_level(0.42)
    assert app.window.levels == [0.42]
    assert app.voice_line.levels == [0.42]


def test_every_state_in_a_whole_turn_is_mirrored_in_order():
    """The real sequence a turn walks, so a reordering or a dropped
    transition shows up here rather than as a HUD that sticks."""
    app = _wired()
    for state in ("listening", "thinking", "speaking", "idle"):
        app.window.set_state(state)
    assert app.voice_line.states == ["listening", "thinking", "speaking", "idle"]
    assert app.window.states == app.voice_line.states


def test_a_failing_bus_never_breaks_the_pill():
    """The pill is what the user is actually looking at. A publisher
    that throws must not stop the window updating — FRED has to behave
    identically whether or not anything is reading the bus."""
    app = _wired(bus=ExplodingBus())
    app.window.set_state("speaking")
    app.window.set_level(0.9)
    assert app.window.states == ["speaking"]
    assert app.window.levels == [0.9]


def test_wrapping_preserves_the_return_value():
    app = PillApp.__new__(PillApp)
    app.window = FakeWindow()
    app.window.set_state = lambda state: "sentinel"
    app.voice_line = FakeBus()
    app._mirror_window_to_bus()
    assert app.window.set_state("idle") == "sentinel"
