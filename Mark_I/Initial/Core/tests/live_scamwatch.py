"""Live scam-watch gate: real Windows OCR on rendered invented pages (the 20 screens of tests/live_vision_hard.py plus a genuine-domain login page); the three scam screens must warn,
everything else must stay quiet. `--toast` also shows one real Windows toast for the first scam page.   python tests/live_scamwatch.py [--toast]"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from proactive.scamwatch import assess
from senses import ocr
from tests.live_ocr import render
from tests.live_vision_hard import S, page

SCAM = {"phish_lookalike", "phish_bank", "email_spoof"}
REAL = ("real_paypal", page("<div style='position:absolute;left:0;top:0;width:1920px;height:70px;background:#fff;border-bottom:1px solid #ccc;font-size:26px;line-height:70px;padding-left:40px'>&#128274; https://www.paypal.com/signin</div><div class=c style='top:200px;width:700px;margin-left:420px;background:#fff;padding:50px;font-size:30px'><h1 style='color:#003087'>PayPal</h1>Email<br><input style='width:600px;height:44px'><br><br>Password<br><input style='width:600px;height:44px'><br><br><button style='font-size:28px;padding:12px 40px;background:#0070ba;color:#fff;border:0'>Log In</button><p style='color:#c00'>Your account has been limited. Confirm your details within 24 hours.</p></div>"))


def main():
    tmp, bad = Path(tempfile.mkdtemp()), 0
    first = None
    for name, html in [(n, h) for n, h, *_ in S] + [REAL]:
        rgb = render(html, tmp, name)
        v = assess(ocr.read(rgb) or "")
        want = name in SCAM
        ok = bool(v) == want
        bad += not ok
        first = first or (v if v else None)
        print(f"{'ok  ' if ok else 'FAIL'} {name:18} -> " + (f"{v.kind}/{v.level} {v.domain} {v.brand} {v.reasons}" if v else "quiet"))
    print(f"{bad} wrong of {len(S) + 1}")
    if "--toast" in sys.argv and first:
        from ui import toast
        title, text = first.message()
        print("toast shown:", toast.show(title, text))
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
