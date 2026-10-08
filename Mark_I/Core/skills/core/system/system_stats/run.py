"""system_stats: the computer's vital signs, one skill with a `part` (all, cpu, gpu, ram, disk). Read-only.

Sockets
  in : run(part="all", drive="")   drive only matters for disk: a letter ("C", "d:") picks one fixed drive, else all
  out: a spoken sentence per part, for example
         cpu   "CPU is at 12 percent across 12 threads (6 cores), running at 4.7 GHz. Temperature isn't available on this PC."
         gpu   "NVIDIA GeForce RTX 5060 Ti: 3 percent busy, 12.8 of 15.9 GB video memory used, 39 C, 7 W."
         ram   "Memory: 14.2 of 31.8 GB used (45 percent), 17.6 GB free. Page file: 2.1 of 8.0 GB."
         disk  "C: 412 GB free of 953 GB (57 percent used). D: ..."
         all   "CPU 12 percent. Memory 45 percent used. Graphics card 3 percent busy, 12.8 of 15.9 GB video memory, 39 C. Disk C 43 percent used. Battery 80 percent, charging."
       all leaves out a part it cannot read; a single part that cannot be read is ToolError(unavailable); a bad drive letter is bad_args naming the drives that exist.
Temperatures: GPU from nvidia-smi (ships with the driver). CPU from psutil's sensors where the OS has them; Windows does not expose the CPU temperature without a separate sensor tool
(checked on this PC: the ACPI thermal zone says "Not supported"), so the answer says so instead of guessing. To add it later: read a sensor tool's value in `_cpu_temp`, nothing else changes.
"""
import subprocess

import psutil

from tools.registry import ToolError

GB = 1024 ** 3
GPU_FIELDS = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"


def _cpu_temp():
    try:
        t = [x.current for rows in psutil.sensors_temperatures().values() for x in rows]
    except (AttributeError, OSError):
        return None                                                                  # Windows: psutil has no sensors_temperatures
    return max(t) if t else None


def cpu() -> str:
    s = f"CPU is at {psutil.cpu_percent(interval=0.3):.0f} percent across {psutil.cpu_count()} threads ({psutil.cpu_count(logical=False) or '?'} cores)"
    f = psutil.cpu_freq()
    s += f", running at {f.current / 1000:.1f} GHz." if f and f.current else "."
    t = _cpu_temp()
    return s + (f" Temperature {t:.0f} C." if t is not None else " Temperature isn't available on this PC.")


def gpu_parse(line: str) -> dict:
    v = [x.strip() for x in line.split(",")]
    if len(v) != 6:
        raise ToolError("failed", "I couldn't read the graphics card.", f"unexpected nvidia-smi line: {line!r}")
    num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None
    return {"name": v[0], "util": num(v[1]), "used": num(v[2]), "total": num(v[3]), "temp": num(v[4]), "power": num(v[5])}


def _gpu_read() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={GPU_FIELDS}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise ToolError("unavailable", "I can't reach the graphics card.", f"nvidia-smi: {ex!r}")
    if out.returncode or not out.stdout.strip():
        raise ToolError("unavailable", "I can't reach the graphics card.", f"nvidia-smi exit {out.returncode}: {out.stderr.strip()[:150]}")
    return gpu_parse(out.stdout.strip().splitlines()[0])


def _gpu_bits(g: dict) -> list:
    return ([f"{g['util']:.0f} percent busy"] if g["util"] is not None else []) + ([f"{g['used'] / 1024:.1f} of {g['total'] / 1024:.1f} GB video memory used"] if g["used"] is not None and g["total"] else []) \
        + ([f"{g['temp']:.0f} C"] if g["temp"] is not None else []) + ([f"{g['power']:.0f} W"] if g["power"] is not None else [])


def gpu() -> str:
    g = _gpu_read()
    return f"{g['name']}: " + ", ".join(_gpu_bits(g)) + "."


def ram() -> str:
    m, s = psutil.virtual_memory(), psutil.swap_memory()
    out = f"Memory: {m.used / GB:.1f} of {m.total / GB:.1f} GB used ({m.percent:.0f} percent), {m.available / GB:.1f} GB free."
    return out + (f" Page file: {s.used / GB:.1f} of {s.total / GB:.1f} GB." if s.total else "")


def _drives() -> dict:
    out = {}
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype:
            continue
        try:
            out[p.device[:1].upper()] = psutil.disk_usage(p.mountpoint)
        except OSError:
            pass                                                                     # a drive that is not ready (empty card reader) is not a drive
    return out


def disk(drive: str = "") -> str:
    d = _drives()
    letter = drive.strip().rstrip(":\\/")[:1].upper()
    if letter and letter not in d:
        raise ToolError("bad_args", f"I don't see a drive {letter}.", f"no drive {letter!r}; drives present: {', '.join(sorted(d))}")
    rows = [f"{k}: {u.free / GB:.0f} GB free of {u.total / GB:.0f} GB ({u.percent:.0f} percent used)." for k, u in sorted(d.items()) if not letter or k == letter]
    return " ".join(rows) or "I can't see any drives."


def overview() -> str:
    parts = [f"CPU {psutil.cpu_percent(interval=0.3):.0f} percent" + (f", {_cpu_temp():.0f} C" if _cpu_temp() is not None else ""), f"Memory {psutil.virtual_memory().percent:.0f} percent used"]
    try:
        parts.append("Graphics card " + ", ".join(_gpu_bits(_gpu_read())).replace(" video memory used", " video memory"))
    except ToolError:
        pass                                                                         # no card or no driver: the overview is still useful
    for k, u in sorted(_drives().items()):
        parts.append(f"Disk {k} {u.percent:.0f} percent used")
    b = psutil.sensors_battery()
    if b:
        parts.append(f"Battery {b.percent:.0f} percent{', charging' if b.power_plugged else ''}")
    return ". ".join(parts) + "."


def run(part: str = "all", drive: str = "") -> str:
    if part == "all":
        return overview()
    if part == "disk":
        return disk(drive)
    return {"cpu": cpu, "gpu": gpu, "ram": ram}[part]()
