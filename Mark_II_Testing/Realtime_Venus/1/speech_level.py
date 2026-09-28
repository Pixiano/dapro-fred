"""Talking vs not, relative to the room's own noise floor (shared by live_duplex and analyse_live).

A fixed dB threshold broke on 2026-09-28: the lavalier at 8x gain put a
quiet room at -26 dB median, above the old -40 dB "talking" line, so every
second counted as speech (no forced replies, barge-in on noise).
"""

from collections import deque


class NoiseFloor:
    """Floor = 10th percentile of the last `window` levels (dB). Talking = floor + TALK_DB."""

    TALK_DB = 8.0  # above the floor -> speech
    QUIET_DB = 4.0  # within this of the floor -> he has stopped

    def __init__(self, window=30, default=-50.0):
        self.levels, self.default = deque(maxlen=window), default

    def add(self, db):
        self.levels.append(db)

    @property
    def floor(self):
        if len(self.levels) < 5:
            return self.default
        s = sorted(self.levels)
        return s[len(s) // 10]

    def talking(self, db):
        return db > self.floor + self.TALK_DB

    def quiet(self, db):
        return db < self.floor + self.QUIET_DB


if __name__ == "__main__":  # self-check: steady hum, then speech, then hum again
    nf = NoiseFloor()
    for _ in range(20):
        nf.add(-26.0)
    assert not nf.talking(-25.0) and nf.quiet(-25.0), nf.floor
    assert nf.talking(-15.0) and not nf.quiet(-15.0)
    for _ in range(5):  # a few speech seconds don't drag the floor up
        nf.add(-14.0)
    assert abs(nf.floor - -26.0) < 0.1 and nf.quiet(-24.5)
    quiet_room = NoiseFloor()
    for _ in range(20):
        quiet_room.add(-60.0)
    assert quiet_room.talking(-45.0) and quiet_room.quiet(-58.0)
    print("NoiseFloor self-check OK")
