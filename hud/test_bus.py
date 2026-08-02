"""Exercise server.read_bus()'s staleness + stomp-tolerance rules against a
temp bus dir, so the real ~/voice-line/ is never touched."""
import os, sys, time, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server

tmp = Path(tempfile.mkdtemp(prefix="voiceline_"))
server.BUS_DIR = tmp

def write(name, text, age=0.0):
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(p, (t, t))

def clear():
    for p in tmp.iterdir():
        p.unlink()

results = []
def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n      got={got!r} want={want!r}")

# 1. nothing there at all
clear()
check("empty bus dir -> idle", server.read_bus(), ("idle", 0.0))

# 2. fresh state file is honoured
clear(); write("state", "thinking")
check("fresh state=thinking -> thinking", server.read_bus()[0], "thinking")

# 3. STALE state file is ignored and decays to idle
clear(); write("state", "thinking", age=server.STALE_SECONDS + 2)
check("stale state=thinking -> idle", server.read_bus(), ("idle", 0.0))

# 4. STOMP TOLERANCE: fresh waveform outranks a disagreeing state file
clear(); write("state", "thinking"); write("waveform", "0.62")
st, lvl = server.read_bus()
check("fresh waveform beats state=thinking", st, "speaking")
check("  ...and carries its level", round(lvl, 2), 0.62)

# 5. stomp does NOT fire on a stale waveform
clear(); write("state", "listening"); write("waveform", "0.62", age=3.0)
check("stale waveform -> state file wins", server.read_bus()[0], "listening")

# 6. stomp does NOT fire on a fresh but silent waveform
clear(); write("state", "listening"); write("waveform", "0.001")
check("fresh but silent waveform -> no stomp", server.read_bus()[0], "listening")

# 7. garbage in the state file is not trusted
clear(); write("state", "rm -rf /")
check("garbage state -> idle", server.read_bus()[0], "idle")

# 8. multi-sample waveform takes the peak of the tail
clear(); write("waveform", "0.01, 0.02, 0.91, 0.04")
check("multi-sample waveform -> peak", round(server.read_bus()[1], 2), 0.91)

# 9. out-of-range values are clamped
clear(); write("waveform", "9.5")
check("level clamped to 1.0", server.read_bus()[1], 1.0)

# 10. a directory where a file is expected must not raise
clear(); (tmp / "state").mkdir()
check("state is a dir -> idle, no crash", server.read_bus()[0], "idle")
(tmp / "state").rmdir()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
