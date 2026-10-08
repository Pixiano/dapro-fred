"""Scam watch: warns, unasked, when the screen shows a phishing or scam page (a lookalike login, an urgent "verify your card" page, a fake support alert).

A code-based check on the OCR text of the screen, not a model's opinion (a small vision model called a fake PayPal page safe; tests/live_vision_hard.py). Nothing leaves the machine; the
OCR text is judged in memory and only the domain and the reasons are logged or sent on the bus, never the page text or pixels.

Signals (all from the OCR text)
  domain   every host in the text: legitimate (a brand's own registrable domain) -> 0; imitating a brand (homoglyphs 0/o 1/l rn/m, the brand inside a foreign domain such as
           secure-paypal-login.com or paypal.com.verify-login.net, one typo away) -> 3; odd TLD (.xyz .top ...), punycode, raw IP, many hyphens -> +1..2
  page     asks for credentials (password, PIN, OTP, CVV, card number ...) and/or pressures (account suspended, within 24 hours, verify your identity ...), or tech-support scam text
Verdict  imitated brand + (credentials or pressure) -> high;  domain risk >= 2 + credentials + pressure -> medium;  unfamiliar site + credentials + pressure -> medium;  tech-support scam -> medium
         A genuine brand domain never warns, whatever the page says.

Sockets
  in : assess(text) -> Verdict | None                       pure; the rules above
       ScamWatch(bus, screen, ocr_read=None, notify=None, clock=None).start() / stop() / check_once(frame=None)
         bus event screen.settled (not duplicate) -> the latest frame is OCR'd on one worker thread (the screen thread is never blocked) and assessed
         ocr_read(rgb) -> str | None   (default senses.ocr.read);  notify(title, text) -> bool   (default ui.toast.show)
  out: bus event proactive.warn {scam: "phishing"|"scam", level, domain, reasons}; a Windows toast; log.session("scam_warning", domain, reasons)
       cooldown per domain / page kind (scamwatch_cooldown_s, 30 min) and at most scamwatch_max_per_hour warnings; config.scamwatch_enabled turns it off
       The L4 judge (P6) subscribes to proactive.warn to also say it aloud.
Self-check: python proactive/scamwatch.py (invented texts, fake OCR and toast); real OCR on rendered pages: tests/live_scamwatch.py
"""
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log

BRANDS = {
    "paypal": {"paypal.com", "paypalobjects.com"}, "google": {"google.com", "google.co.in", "gmail.com", "youtube.com"}, "microsoft": {"microsoft.com", "live.com", "outlook.com", "office.com", "microsoftonline.com"},
    "amazon": {"amazon.com", "amazon.in", "amazon.co.uk", "amazon.de", "amazonaws.com"}, "apple": {"apple.com", "icloud.com"}, "netflix": {"netflix.com"}, "facebook": {"facebook.com", "fb.com"},
    "instagram": {"instagram.com"}, "whatsapp": {"whatsapp.com", "whatsapp.net"}, "linkedin": {"linkedin.com"}, "dropbox": {"dropbox.com"}, "github": {"github.com", "github.io", "githubusercontent.com"},
    "hdfc": {"hdfcbank.com"}, "icici": {"icicibank.com", "icicidirect.com"}, "sbi": {"onlinesbi.sbi", "sbi.co.in"}, "axis": {"axisbank.com"}, "paytm": {"paytm.com"}, "phonepe": {"phonepe.com"},
    "flipkart": {"flipkart.com"}, "irctc": {"irctc.co.in"}, "chase": {"chase.com"}, "wellsfargo": {"wellsfargo.com"}, "coinbase": {"coinbase.com"}, "binance": {"binance.com"},
    "steam": {"steampowered.com", "steamcommunity.com"}, "discord": {"discord.com", "discord.gg", "discordapp.com"}, "spotify": {"spotify.com"}, "adobe": {"adobe.com"},
}
SLD = {"co.uk", "co.in", "com.au", "co.jp", "org.uk", "com.br", "co.za", "ac.in", "gov.in", "net.in"}
ODD_TLD = {"xyz", "top", "click", "zip", "tk", "ml", "ga", "cf", "gq", "icu", "buzz", "rest", "cyou", "monster", "country", "support", "work", "lol", "mov"}
SHORT = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rb.gy", "shorturl.at", "ow.ly"}
HOST = re.compile(r"(?<![\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}|(?:\d{1,3}\.){3}\d{1,3})(?=[/:?#\s)\]\"'>,]|$)", re.I)
CRED = re.compile(r"\b(password|passcode|pin|otp|cvv|cvc|card number|ssn|social security|one[- ]time (?:password|code)|security code|net ?banking|login id)\b", re.I)
PRESSURE = re.compile(r"(within \d+ ?(?:hours?|hrs?|minutes?|mins?)|account (?:has been |is |will be )?(?:suspended|limited|locked|closed|restricted|blocked)|verify your (?:account|identity|details|card|information)|"
                      r"confirm your (?:details|account|identity|information)|unusual (?:sign.?in|activity|login)|permanently (?:closed|deleted|blocked)|\burgent\b|act now|final notice)", re.I)
TECH = re.compile(r"(your (?:computer|pc|device|windows|mac) (?:is|has been|may be) (?:infected|compromised|blocked|at risk)|virus (?:detected|alert)|call (?:microsoft|apple|windows|tech) support|"
                  r"do not (?:restart|shut ?down|close this))", re.I)
LURE = re.compile(r"\.(?:pdf|docx?|xlsx?|jpe?g|png|txt)\.(?:exe|scr|bat|js|vbs|msi|cmd)\b|\b(?:attached|attachment|open|run|download)\b[^\n]{0,40}\.(?:exe|scr|bat|vbs|msi|cmd)\b", re.I)
GLYPH = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "$": "s", "!": "i", "@": "a"})


@dataclass
class Verdict:
    kind: str                 # phishing | scam
    level: str                # high | medium
    domain: str = ""
    brand: str = ""
    reasons: list = field(default_factory=list)

    def message(self) -> tuple:
        who = f" imitating {self.brand.title()}" if self.brand else ""
        if self.kind == "scam":
            return "Possible scam on screen", "This looks like a tech-support scam. Don't call the number or install anything."
        return f"Possible phishing page{who}", (f"{self.domain} is not the real site. " if self.domain else "") + "Don't enter passwords, PINs or card details here."


def registrable(host: str) -> str:
    p = host.lower().strip(".").split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in SLD:
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def _typo(a: str, b: str) -> bool:
    """One substitution, insertion or deletion apart (same length class, no repeats of a short word)."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = sorted((a, b), key=len)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def check_host(host: str):
    """(risk, brand, reasons) for one host; (0, "", []) for a genuine brand domain or an unremarkable one."""
    h = host.lower()
    reg = registrable(h)
    if any(reg in d for d in BRANDS.values()):
        return 0, "", []
    risk, brand, why = 0, "", []
    label = reg.split(".")[0] if not re.fullmatch(r"[\d.]+", h) else ""
    flat = label.translate(GLYPH).replace("rn", "m").replace("vv", "w")
    tokens = [t for t in re.split(r"[-.]", h.translate(GLYPH)) if t]
    for b, domains in BRANDS.items():
        legit_prefix = any(h.startswith(d.split(".")[0] + ".") and d in h for d in domains)
        if legit_prefix and reg not in domains:                                     # paypal.com.verify-login.net
            risk, brand, why = 3, b, [f"{b} is only a label inside another domain"]
            break
        if b in tokens or (len(b) >= 6 and b in flat) or any(_typo(t, b) for t in flat.split("-") if len(t) >= 6 and len(b) >= 6):
            risk, brand, why = 3, b, [f"the domain imitates {b}" + (" with look-alike characters" if label.translate(GLYPH) != label else "")]
            break
    tld = reg.split(".")[-1]
    if tld in ODD_TLD:
        risk += 1
        why.append(f"unusual ending .{tld}")
    if "xn--" in h:
        risk, why = risk + 2, why + ["punycode (look-alike letters)"]
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", h):
        risk, why = risk + 2, why + ["a raw IP address instead of a name"]
    if label.count("-") >= 2:
        risk, why = risk + 1, why + ["many hyphens in the name"]
    if reg in SHORT:
        risk, why = risk + 1, why + ["a link shortener"]
    return risk, brand, why


def _despace(text: str) -> str:
    """OCR often puts spaces inside a URL ("secure-paypa 1 -login.co"): add the same lines with the spaces of their URL part removed."""
    extra = []
    for line in text.split("\n"):
        low = line.lower()
        if "://" in low:
            extra.append(re.sub(r"\s+", "", line.split("://", 1)[1]))
        elif low.startswith("www."):
            extra.append(re.sub(r"\s+", "", line))
    return text + "\n" + "\n".join(extra)


def assess(text: str):
    if not text or len(text) < 25:
        return None
    text = _despace(text)
    lure = bool(LURE.search(text))
    cred, pressure, tech = bool(CRED.search(text)), bool(PRESSURE.search(text)) or lure, bool(TECH.search(text))
    best = (0, "", "", [])
    for m in HOST.finditer(text):
        risk, brand, why = check_host(m.group(1))
        if risk > best[0]:
            best = (risk, brand, m.group(1).lower(), why)
    risk, brand, host, why = best
    if risk >= 3 and brand and ((cred and pressure) or lure):                       # a lookalike brand needs credentials AND pressure, or a disguised executable
        return Verdict("phishing", "high", registrable(host) if "." in host else host, brand, why + (["asks for credentials"] if cred else []) + (["pressures you to act fast"] if PRESSURE.search(text) else []) + (["an executable disguised as a document"] if lure else []))
    if risk >= 2 and cred and pressure:
        return Verdict("phishing", "medium", registrable(host), brand, why + ["asks for credentials", "pressures you to act fast"])
    if risk >= 1 and cred and pressure:                                             # unknown brand, odd site: shown (toast, caption), never spoken
        return Verdict("phishing", "medium", registrable(host), brand, why + ["asks for credentials", "pressures you to act fast"])
    if tech and (re.search(r"\b\d[\d\- ()]{8,}\d\b", text)):
        return Verdict("scam", "medium", "", "", ["tech-support scam wording"])
    return None


class ScamWatch:
    def __init__(self, bus, screen, ocr_read=None, notify=None, clock=None):
        self.bus, self.screen = bus, screen
        self._ocr, self._notify, self._clock = ocr_read, notify, clock or time.time
        self._seen: dict = {}
        self._sent: list = []
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._unsub, self._thread, self._stop = None, None, threading.Event()

    def start(self):
        if not config.get().scamwatch_enabled or self._thread:
            return
        self._unsub = self.bus.subscribe(self._on_event, kinds=("settled",), sources=("screen",))
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scamwatch")
        self._thread.start()

    def stop(self):
        self._unsub and self._unsub()
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        self._thread and self._thread.join(2)
        self._thread = self._unsub = None

    def _on_event(self, ev):
        if ev.data.get("duplicate"):
            return
        try:
            self._q.put_nowait(1)                                       # one pending check at most: a burst of changes costs one OCR pass
        except queue.Full:
            pass

    def _loop(self):
        while not self._stop.is_set():
            if self._q.get() is None:
                return
            try:
                self.check_once()
            except Exception as ex:
                log.session("scamwatch_error", error=type(ex).__name__)

    def check_once(self, frame=None):
        frame = frame if frame is not None else self.screen.latest()
        if frame is None:
            return None
        if self._ocr is None:
            from senses import ocr
            self._ocr = ocr.read
        v = assess(self._ocr(frame) or "")
        return self._warn(v) if v else None

    def _warn(self, v: Verdict):
        c, now = config.get(), self._clock()
        key = v.domain or v.kind
        if now - self._seen.get(key, -1e12) < c.scamwatch_cooldown_s:
            return None
        self._sent = [t for t in self._sent if now - t < 3600]
        if len(self._sent) >= c.scamwatch_max_per_hour:
            return None
        self._seen[key] = now
        self._sent.append(now)
        title, text = v.message()
        log.session("scam_warning", domain=v.domain, scam=v.kind, level=v.level, reasons=v.reasons)
        self.bus.emit("proactive", "warn", scam=v.kind, level=v.level, domain=v.domain, brand=v.brand, reasons=v.reasons, title=title, text=text)
        notify = self._notify
        if notify is None:
            from ui import toast
            notify = toast.show
        notify(title, text)
        return v


def _selfcheck():
    import tempfile
    import events as ev
    config.set_data_dir(tempfile.mkdtemp())
    page = lambda url, body: f"{url}\n{body}\nWelcome back. This is an ordinary page with some more words on it so that it is long enough."
    pos = [
        (page("https://secure-paypa1-login.co/signin", "PayPal Email Password Log In Your account has been limited. Confirm your details within 24 hours."), "paypal", "high"),
        (page("https://netbank-secure.verify-account.xyz/auth", "URGENT: Account suspended. Verify your card number, PIN and OTP within 2 hours."), "", "medium"),
        (page("paypal.com.account-verify.top/login", "Enter your password to restore your account. Your account is locked."), "paypal", "high"),
        (page("http://micros0ft-support.net/pay", "Microsoft account password required. Verify your identity within 1 hour."), "microsoft", "high"),
        (page("https://hdfc-netbanking.info/login", "HDFC NetBanking login ID and password. Your account will be suspended, act now."), "hdfc", "high"),
        (page("http://185.44.12.9/bank", "Confirm your details: card number, CVV and PIN. Unusual activity: your account is blocked."), "", "medium"),
        (page("https://secure-paypa 1 -login.co/signin", "PayPal Email Password Log In Your account has been limited. Confirm your details within 24 hours."), "paypal", "high"),
        ("From: Accounts <billing@micros0ft-support.net>\nSubject: Your invoice is attached\nDear customer, please review the attached invoice.\nhttps://www.microsoft.com/invoice/view (links to http://bit.ly/x9fK2)\nOpen invoice.pdf.exe", "microsoft", "high"),
        (page("https://amaz0n-orders.shop/track", "Amazon sign in password. Verify your account within 24 hours, final notice."), "amazon", "high"),
    ]
    for t, brand, level in pos:
        v = assess(t)
        assert v and v.kind == "phishing" and v.brand == brand and v.level == level, (t[:60], v)
    s = assess("WARNING: Your computer is infected! Do not close this window. Call Microsoft support now: 1-800-555-0100 toll free. Virus detected on your device.")
    assert s and s.kind == "scam"
    neg = [
        page("https://www.paypal.com/signin", "PayPal Email Password Log In Your account has been limited. Confirm your details within 24 hours."),      # the real domain never warns
        page("https://accounts.google.com/signin", "Google sign in. Enter your password. Verify your identity."),
        page("https://news.example.org/story", "Security tip: never share your password or OTP. Phishing sites use domains like paypa1-login.co. Read more on how banks protect accounts."),
        page("https://github.com/login", "Sign in to GitHub. Username or email address, Password. Forgot password?"),
        page("https://shop.example.com/cart", "Your cart: 2 items. Total 1,840. Pay with card number, expiry and CVV. Free delivery within 3 days."),
        page("https://docs.python.org/3/library/re.html", "re - regular expression operations. This module provides regular expression matching operations. Password patterns."),
        "From: Sam <sam@gmail.com>\nSubject: Photos from Sunday\nHi, attached are the report.pdf and the trip photos, open them when you can. See you soon, talk later.",
        page("https://s3.amazonaws.com/bucket/file", "Files for you. Enter your password or PIN to open. URGENT notice."),               # real CDN / ccTLD / substring look-alikes the reviewer found
        page("https://purchase-orders.com/login", "Enter your password. URGENT: act now, final notice."),
        page("https://www.google.co.in/account", "Enter your password. URGENT: verify your account now."),
        page("https://shop.example.org/support", "Call toll free 1800 123 4567 for help with your order, any day of the week."),
        "tiny text",
        "",
    ]
    for t in neg:
        assert assess(t) is None, (t[:70], assess(t))
    assert check_host("paypal.com")[0] == 0 and check_host("www.paypal.com")[0] == 0 and check_host("paypal.co.uk")[0] >= 3
    assert check_host("rnicrosoft.com")[1] == "microsoft" and check_host("faceb00k-help.com")[1] == "facebook" and check_host("pa1pal.com")[1] == "paypal"

    bus, calls, now = ev.EventBus(), [], [1000.0]
    seen = []
    bus.subscribe(lambda e: seen.append(e), kinds=("warn",), sources=("proactive",))
    texts = {"v": pos[0][0]}
    w = ScamWatch(bus, type("S", (), {"latest": staticmethod(lambda: object())})(), ocr_read=lambda f: texts["v"], notify=lambda t, x: calls.append((t, x)) or True, clock=lambda: now[0])
    v = w.check_once()
    assert v and len(calls) == 1 and calls[0][0] == "Possible phishing page imitating Paypal" and "secure-paypa1-login.co is not the real site" in calls[0][1], calls
    assert seen[0].data["domain"] == "secure-paypa1-login.co" and "text" in seen[0].data and "Password" not in str(seen[0].data)                    # domain and reasons only, never page text
    assert w.check_once() is None and len(calls) == 1                                                                                           # cooldown
    now[0] += 1801
    assert w.check_once() and len(calls) == 2                                                                                                   # warns again after the cooldown
    texts["v"] = "https://www.paypal.com/signin Password. Verify your identity within 24 hours. Plenty of extra words here."
    assert w.check_once() is None
    for i in range(10):                                                                                                                        # the hourly cap
        texts["v"] = page(f"https://secure-paypa1-login{i}.co/x", "PayPal password. Your account has been limited, confirm your details within 24 hours.")
        w.check_once()
    assert len(calls) == config.get().scamwatch_max_per_hour
    # through the bus: a settled event reaches the worker; a duplicate does not
    texts["v"] = pos[1][0]
    w2 = ScamWatch(bus, type("S", (), {"latest": staticmethod(lambda: object())})(), ocr_read=lambda f: texts["v"], notify=lambda t, x: calls.append((t, x)) or True, clock=lambda: now[0] + 99999)
    w2.start()
    n = len(calls)
    bus.emit("screen", "settled", bbox=[0, 0, 1, 1], changes=1, ms=1, duplicate=True)
    time.sleep(0.3)
    assert len(calls) == n
    bus.emit("screen", "settled", bbox=[0, 0, 1, 1], changes=1, ms=1, duplicate=False)
    for _ in range(40):
        if len(calls) > n:
            break
        time.sleep(0.05)
    w2.stop()
    assert len(calls) == n + 1 and calls[-1][0] == "Possible phishing page"
    config.get().scamwatch_enabled = False
    w3 = ScamWatch(bus, None)
    w3.start()
    assert w3._thread is None                                                                                                                  # switched off in config
    config.reload()
    print("scamwatch ok")


if __name__ == "__main__":
    _selfcheck()
