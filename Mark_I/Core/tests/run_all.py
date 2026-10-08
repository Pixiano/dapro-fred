"""Run every module's self-check (`python <file>.py`) and report; exit 1 if any fails. No GPU, no playground needed.

  python tests/run_all.py
A module is any Core .py file (outside tests/ and hidden dirs such as .venv) that defines `_selfcheck`, plus every tests/test_*.py. Output goes to a temp FRED_DATA, never the real logs.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]


def main() -> int:
    mods = sorted(p for p in CORE.rglob("*.py") if "tests" not in p.parts and not any(x.startswith(".") for x in p.relative_to(CORE).parts) and "def _selfcheck" in p.read_text(encoding="utf-8"))
    mods += sorted((CORE / "tests").glob("test_*.py"))                     # cross-module scenario suites
    failed = 0
    with tempfile.TemporaryDirectory() as t:
        env = {**os.environ, "FRED_DATA": t}
        for m in mods:
            r = subprocess.run([sys.executable, str(m)], capture_output=True, text=True, env=env, cwd=CORE, timeout=300)
            ok = r.returncode == 0
            failed += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {m.relative_to(CORE)}" + ("" if ok else f"\n{r.stdout}{r.stderr}"))
    print(f"{len(mods) - failed}/{len(mods)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
