"""Hand-run gate: Windows OCR on complex invented screens (video site, search results, links, diagrams, code, tables, chat, low contrast, slide, terminal).

  python tests/live_ocr.py [--keep]
Renders each scene from tests/ocr_scenes.py with headless Edge at 1920x1080, reads it with senses/ocr.py, and reports per scene: phrases found exactly (letters and digits only,
case-insensitive), phrases found with the grounding tolerance (describe.found), time, and the missed phrases. Screens are invented; --keep leaves the PNGs in the temp dir.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from senses import ocr
from senses.describe import _alnum, found
from tests.ocr_scenes import SCENES

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def render(html: str, d: Path, name: str) -> np.ndarray:
    h, png = d / f"{name}.html", d / f"{name}.png"
    h.write_text(html, encoding="utf-8")
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1920,1200",
                    f"--user-data-dir={d / 'prof'}", f"--screenshot={png}", h.as_uri()], capture_output=True, timeout=60)
    img = cv2.imread(str(png))
    assert img is not None, f"render failed: {name}"
    return img[:1080, :1920, ::-1].copy()


def main():
    keep = "--keep" in sys.argv
    d = Path(tempfile.mkdtemp(prefix="fred_ocr_"))
    tot_e = tot_f = tot_n = 0
    print(f"{'scene':16} {'exact':>9} {'tolerant':>9} {'ms':>5}  missed (exact)")
    for name, html, phrases in SCENES:
        img = render(html, d, name)
        t0 = time.time()
        text = ocr.read(img)
        ms = int((time.time() - t0) * 1000)
        hay = _alnum(text)
        miss = [p for p in phrases if _alnum(p) not in hay]
        fuzzy_miss = [p for p in phrases if not found(p, text)]
        n = len(phrases)
        tot_e, tot_f, tot_n = tot_e + n - len(miss), tot_f + n - len(fuzzy_miss), tot_n + n
        print(f"{name:16} {n - len(miss):4d}/{n:<4d} {n - len(fuzzy_miss):4d}/{n:<4d} {ms:5d}  {miss[:4]}{' ...' if len(miss) > 4 else ''}")
    print(f"{'TOTAL':16} {tot_e:4d}/{tot_n:<4d} {tot_f:4d}/{tot_n:<4d}   ({tot_e / tot_n:.0%} exact, {tot_f / tot_n:.0%} tolerant)")
    if keep:
        print("pngs kept in", d)


if __name__ == "__main__":
    main()
