"""
End-to-end check of the publisher/reader pair: FRED's VoiceLineBus writing
into a temp bus dir, and hud/server.read_bus() reading it back out.

Both sides are pointed at a temp directory, so the real ~/voice-line/ is
never touched and a run can't disturb a live HUD.
"""
import sys, time, tempfile
from pathlib import Path

HUD = Path(__file__).resolve().parent
sys.path.insert(0, str(HUD))
sys.path.insert(0, str(HUD.parent / "Core"))

import server
from utils.voice_line import VoiceLineBus

tmp = Path(tempfile.mkdtemp(prefix="wiring_"))
server.BUS_DIR = tmp
bus = VoiceLineBus(bus_dir=tmp)

results = []
def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n      got={got!r} want={want!r}")

check("constructing the bus publishes idle", server.read_bus()[0], "idle")

# --- the four states FRED actually emits, round-tripped -----------------
for state in ("listening", "thinking", "speaking", "idle"):
    bus.set_state(state)
    check(f"set_state({state!r}) round-trips", server.read_bus()[0], state)

# --- the pill's "working" has no HUD equivalent and must not be dropped -
bus.set_state("working")
check("working aliases to thinking", server.read_bus()[0], "thinking")

# --- garbage from a future call site must not corrupt the bus ----------
bus.set_state("banana")
check("unknown state ignored, last good kept", server.read_bus()[0], "thinking")

# --- WAVEFORM: only published while speaking ---------------------------
# This is the important one. set_level is fed from the MICROPHONE during
# listening; if that reached the bus, the reader's stomp rule would call
# it speaking while the user is the one talking.
bus.set_state("listening")
bus._last_wave = 0.0
bus.set_level(0.9)
check("mic level during listening does NOT leak", server.read_bus()[0], "listening")
check("  ...and writes no waveform at all", (tmp / "waveform").exists(), False)

bus.set_state("speaking")
bus._last_wave = 0.0
bus.set_level(0.77)
st, lvl = server.read_bus()
check("level during speaking is published", st, "speaking")
check("  ...with the right amplitude", round(lvl, 2), 0.77)

# --- throttling ---------------------------------------------------------
bus._last_wave = 0.0
bus.set_level(0.10)
first = (tmp / "waveform").read_text()
bus.set_level(0.95)                      # immediately after: should be dropped
check("rapid second level is throttled", (tmp / "waveform").read_text(), first)
time.sleep(bus_interval := 0.12)
bus.set_level(0.95)
check("level lands once the interval passes",
      round(float((tmp / "waveform").read_text()), 2), 0.95)

# --- out-of-range and junk amplitudes ----------------------------------
bus._last_wave = 0.0
bus.set_level(4.2)
check("level clamped high", round(server.read_bus()[1], 2), 1.0)
bus._last_wave = 0.0
bus.set_level(None)
check("junk level ignored, previous kept", round(server.read_bus()[1], 2), 1.0)

# --- HEARTBEAT: a long turn must not go stale --------------------------
# The reader drops a state file older than 5s. A 20s "thinking" turn only
# writes the word once, so without the heartbeat thread re-stamping it the
# HUD would show idle while FRED is still working.
bus.set_state("thinking")
age_before = time.time() - (tmp / "state").stat().st_mtime
time.sleep(server.STALE_SECONDS + 1.0)
still = server.read_bus()[0]
age_after = time.time() - (tmp / "state").stat().st_mtime
check(f"state survives past the {server.STALE_SECONDS}s staleness cutoff", still, "thinking")
check("  ...because the heartbeat re-stamped it",
      age_after < server.STALE_SECONDS, True)

# --- ALERT holds long enough to actually be seen -----------------------
bus.set_state("thinking")
bus.alert(hold=1.5)
check("alert publishes immediately", server.read_bus()[0], "alert")
bus.set_state("idle")                    # FRED racing on, as it really does
check("alert is held through a following idle", server.read_bus()[0], "alert")
# Release can only happen on a heartbeat tick, so allow for one.
time.sleep(1.5 + 1.2)
check("alert releases to the state FRED moved to",
      server.read_bus()[0], "idle")

# --- close() leaves the bus calm ---------------------------------------
bus.set_state("speaking")
bus.close()
check("close() publishes idle", server.read_bus()[0], "idle")

# --- a dead producer decays on its own ---------------------------------
(tmp / "state").write_text("thinking", encoding="utf-8")
import os
old = time.time() - (server.STALE_SECONDS + 2)
os.utime(tmp / "state", (old, old))
check("stale state from a dead FRED -> idle", server.read_bus()[0], "idle")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
