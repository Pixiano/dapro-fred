"""Screen memory: what was on the screen becomes low-weight memory events (type "screen"), so "what was I looking at at 3 pm" can be answered. No model of its own.

One visit = a window dwell of >= screen_mem_dwell_s. Everything FRED sees is kept (up to screen_mem_chars of OCR text) EXCEPT, checked BEFORE anything is stored or embedded:
bank / payment pages and details (card numbers of 12-19 digits, account / IFSC / UPI ids), password managers and pages that ask for a password / OTP, private-browsing windows, the
vault, the lock screen and FRED's own windows. An excluded visit leaves no trace (no event, no vector, no image). The pause switch (proactive/gate.py) stops it; the scam watch is unrelated.
A visit with the same app + title and token overlap >= screen_mem_dup of a recent one is merged into it (seen count and end time), not stored again.

Sockets
  in : ScreenMemory(reader, screen, memory, image=None, foreground=None, exe_of=None, clock=time.time, vault=None, tick_s=2.0).start() / stop()
         reader = proactive.screenread.Reader (add(fn): called with changed foreground text);  screen = ScreenSense (crop for the image vector);  memory = manager.Memory (or None: off)
         image = senses.imgembed.ImageEmbedder or None (Phase B);  foreground() -> (hwnd, title, rect);  exe_of(hwnd) -> exe name;  vault = vault folder name/path to exclude (default config.vault_dir)
       excluded(app, title, text, vault="") -> reason | None       the exclusion rules (pure)
       search(memory, index, query="", start=None, end=None, k=5, image=None) -> [{id, ts, app, title, caption, seen, score}]     used by skills/extra/vision/screen_recall
  out: memory event type "screen", importance 0.1, ts = visit start: "Screen 11 Oct 15:02 chrome.exe: Title - first lines\\n<text up to 1000 chars>" (the memory index embeds the text itself:
       Phase A, the existing Qwen3 embedder); side table screen_visits(event_id, app, title, ts, ts_end, seen, img_model, img) in the memory database; img = unit float32 vector (Phase B), no pixels anywhere.
       The screen text never goes to the bus or the logs (counts only); stats() has them.
       Embedding and storing run in one worker thread (the reader is never blocked); the image vector runs through gpu.run at BACKGROUND priority.
Self-check: python proactive/screenmem.py (invented texts, fake memory embedder, fake image model)
"""
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from proactive import gate

SCHEMA = ("CREATE TABLE IF NOT EXISTS screen_visits (event_id INTEGER PRIMARY KEY REFERENCES events(id), app TEXT, title TEXT, ts TEXT, ts_end TEXT, seen INTEGER DEFAULT 1, img_model TEXT, img BLOB);"
          "CREATE INDEX IF NOT EXISTS screen_visits_ts ON screen_visits(ts);")

_APP = re.compile(r"keepass|1password|bitwarden|lastpass|dashlane|enpass|nordpass|roboform|keeper|proton ?pass|passwordsafe|authy|authenticator", re.I)
_LOCK = re.compile(r"^(lockapp|logonui|consent|credentialuibroker|securityhealthsystray|windowsterminalsecure)(\.exe)?$|\b(windows security|windows hello|lock screen|sign-in options)\b", re.I)
_OWN = re.compile(r"^(fred|fred hud|fred[ -](pill|popup|ask))\b", re.I)
_PRIVATE = re.compile(r"in ?private|incognito|private browsing|private window|password", re.I)
_BANK = re.compile(r"\b(net ?banking|internet banking|mobile banking|online banking|banking|bank|upi|bhim|paytm|phonepe|gpay|google pay|paypal|payments?|checkout|razorpay|stripe|"
                   r"credit card|debit card|hdfc|icici|sbi|axis bank|kotak|chase|wells ?fargo|coinbase|binance|wallet)\b", re.I)
_CARD = re.compile(r"(?<![\d])(?:\d[ -]?){12,19}(?![\d])")
_TEXT = [("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
         ("upi id", re.compile(r"\b[\w.\-]{2,}@(?:ok[a-z]+|ybl|ibl|axl|upi|paytm|apl|sbi|icici|hdfcbank|axisbank)\b", re.I)),
         ("account", re.compile(r"\b(?:a/c|acct?|account)\.?\s*(?:no\.?|number|#)?\s*[:#-]?\s*\d{9,18}\b", re.I)),
         ("secret field", re.compile(r"\b(password|passcode|passphrase|otp|one[- ]time (?:password|code)|cvv|cvc|card number|security code|upi pin|net ?banking)\b", re.I))]


def excluded(app: str, title: str, text: str = "", vault: str = "") -> str | None:
    """The reason a window must not be remembered, or None. Pure; the reason names the rule, never the content."""
    a, t = (app or "").strip(), (title or "").strip()
    if not a and not t:
        return "lock screen"                                    # no window at all: the secure desktop or the lock screen
    if _LOCK.search(a) or _LOCK.search(t):
        return "lock screen"
    if _OWN.search(t) or a.lower() in ("fred.exe", "fred_hud.exe"):
        return "FRED"
    if _APP.search(a) or _APP.search(t):
        return "password manager"
    if _PRIVATE.search(t):
        return "private or password window"
    if _BANK.search(t) or _BANK.search(a):
        return "bank or payment"
    v = Path(vault or "").name.lower() if vault else ""
    if v and (v in t.lower() or v in (text or "").lower()):
        return "vault"
    if _CARD.search(text or ""):
        return "card number"
    for name, rx in _TEXT:
        if rx.search(text or ""):
            return name
    return None


def caption(app: str, title: str, text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 2][:3]
    return f"{app}: {title}" + (" - " + " | ".join(lines) if lines else "")


def _tokens(s: str) -> set:
    return set(re.findall(r"\w+", s.lower()))


def _same(a: str, b: str, thr: float) -> bool:
    x, y = _tokens(a), _tokens(b)
    return (not x and not y) or len(x & y) / max(1, len(x | y)) >= thr


def win_exe(hwnd) -> str:
    try:
        import psutil
        import win32process
        return psutil.Process(win32process.GetWindowThreadProcessId(hwnd)[1]).name()
    except Exception:
        return ""


def _table(memory):
    st = memory.store
    with st.lock:
        st.db.executescript(SCHEMA)


class ScreenMemory:
    def __init__(self, reader, screen, memory, image=None, foreground=None, exe_of=None, clock=time.time, vault=None, tick_s: float = 2.0):
        self.reader, self.screen, self.mem, self.image, self.clock, self.tick_s = reader, screen, memory, image, clock, tick_s
        from proactive.screenread import win_foreground
        self.fg, self.exe_of = foreground or win_foreground, exe_of or win_exe
        self.vault = config.get().vault_dir if vault is None else vault
        self._v, self._lock = None, threading.Lock()
        self._q, self._thread, self._stop = queue.Queue(), None, threading.Event()
        self.n = {"stored": 0, "merged": 0, "excluded": 0, "errors": 0, "images": 0}
        if memory is not None:
            _table(memory)
        reader.add(self.on_text)                                # not always=True: the pause switch stops it

    def stats(self) -> dict:
        return dict(self.n)

    def _new(self, hwnd, title, text, now):
        app = self.exe_of(hwnd)
        bad = excluded(app, title, text, self.vault)
        if bad:
            self.n["excluded"] += 1
        return {"key": (hwnd, title), "app": app, "title": title, "text": text, "start": now, "seen": now, "stored": False, "bad": bad}

    def on_text(self, text, info):
        now = self.clock()
        with self._lock:
            key, v = (info["hwnd"], info["title"]), self._v
            if v and v["key"] == key:
                v["text"], v["seen"] = text, now
                v["bad"] = v["bad"] or excluded(v["app"], v["title"], text, self.vault)          # a page that turns into a card form is dropped
            else:
                self._close()
                self._v = self._new(info["hwnd"], info["title"], text, now)

    def _close(self):
        v = self._v
        if v and not v["bad"] and not v["stored"] and v["seen"] - v["start"] >= config.get().screen_mem_dwell_s:
            self._enqueue(v, None)
        self._v = None

    def _enqueue(self, v, rgb):
        v["stored"] = True
        self._q.put((dict(v), rgb))

    def tick(self):
        """Every tick_s: extend the current visit while its window is still in front; store it once it has lasted long enough; drop it when paused or gone."""
        if self.mem is None or not config.get().screen_mem_enabled:
            return
        with self._lock:
            v = self._v
            if gate.paused():
                self._v = None                                  # nothing from a paused period is kept
                return
            if not v:
                return
            hwnd, title, rect = self.fg()
            now = self.clock()
            if (hwnd, title) != v["key"]:
                self._close()
                return
            v["seen"] = now
            if v["bad"] or v["stored"] or now - v["start"] < config.get().screen_mem_dwell_s:
                return
            rgb = None
            if self.image is not None and config.get().screen_img_enabled:
                try:
                    rgb = self.screen.crop(tuple(rect), pad=0)  # in memory only; embedded and dropped
                except Exception:
                    rgb = None
            self._enqueue(v, rgb)

    def _save(self, v, rgb):
        c, st = config.get(), self.mem.store
        text = v["text"][:c.screen_mem_chars]
        ts = datetime.fromtimestamp(v["start"], config.IST).isoformat(timespec="seconds")
        end = datetime.fromtimestamp(v["seen"], config.IST).isoformat(timespec="seconds")
        with st.lock:
            rows = st.db.execute("SELECT s.event_id, e.content FROM screen_visits s JOIN events e ON e.id=s.event_id WHERE s.app=? AND s.title=? AND s.ts>=? ORDER BY s.ts DESC LIMIT 5",
                                 (v["app"], v["title"], datetime.fromtimestamp(v["start"] - 6 * 3600, config.IST).isoformat(timespec="seconds"))).fetchall()
        for eid, content in rows:
            if _same(content.split("\n", 1)[-1], text, c.screen_mem_dup):
                with st.lock:
                    st.db.execute("UPDATE screen_visits SET seen=seen+1, ts_end=? WHERE event_id=?", (end, eid))
                    st.db.commit()
                self.n["merged"] += 1
                return
        cap = caption(v["app"], v["title"], text)
        eid = st.add_event("screen", f"Screen {datetime.fromtimestamp(v['start'], config.IST):%d %b %H:%M} {cap}\n{text}", importance=0.1, ts=ts)
        with st.lock:
            st.db.execute("INSERT OR REPLACE INTO screen_visits(event_id, app, title, ts, ts_end, seen) VALUES(?,?,?,?,?,1)", (eid, v["app"], v["title"], ts, end))
            st.db.commit()
        self.n["stored"] += 1
        if rgb is not None and self.image is not None:
            import gpu
            try:
                vec = np.asarray(gpu.run("screenmem", gpu.BACKGROUND, lambda: self.image.embed_image(rgb), timeout=120), np.float32)
                with st.lock:
                    st.db.execute("UPDATE screen_visits SET img_model=?, img=? WHERE event_id=?", (self.image.model, vec.tobytes(), eid))
                    st.db.commit()
                self.n["images"] += 1
            except Exception as ex:                             # the visit stays text-only
                log.session("screenmem_image_error", error=type(ex).__name__)

    def _work(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self._save(*item)
            except Exception as ex:
                self.n["errors"] += 1
                log.session("screenmem_error", error=type(ex).__name__)
            self._q.task_done()

    def _run(self):
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
                if self.image is not None:
                    self.image.idle_check()
            except Exception as ex:
                log.session("screenmem_tick_error", error=type(ex).__name__)

    def start(self):
        if self._thread or self.mem is None:
            return
        self._stop.clear()
        threading.Thread(target=self._work, daemon=True, name="screenmem-save").start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="screenmem")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)
        self._thread and self._thread.join(3)
        self._thread = None

    def flush(self):
        """Wait for queued saves (tests, shutdown)."""
        self._q.join()


def search(memory, index, query: str = "", start: str | None = None, end: str | None = None, k: int = 5, image=None) -> list:
    """Screen visits, newest first when there is no query; with a query ranked by text similarity (the memory's embedder) fused with image similarity (reciprocal rank).
    start / end are ISO times (India); end exclusive. ponytail: scores every candidate in Python (newest 20000); an index is worth it beyond that."""
    st = memory.store
    sql = "SELECT s.event_id, s.app, s.title, s.ts, s.ts_end, s.seen, s.img_model, s.img, e.content FROM screen_visits s JOIN events e ON e.id=s.event_id WHERE 1=1"
    args = []
    if start:
        sql, args = sql + " AND s.ts>=?", args + [start]
    if end:
        sql, args = sql + " AND s.ts<?", args + [end]
    with st.lock:
        rows = st.db.execute(sql + " ORDER BY s.ts DESC LIMIT 20000", args).fetchall()
    if not rows:
        return []
    out = lambda r, sc: {"id": r[0], "app": r[1], "title": r[2], "ts": r[3], "seen": r[5], "score": sc, "caption": r[8].split("\n", 1)[0].split(" ", 4)[-1]}
    if not query.strip():
        return [out(r, 0.0) for r in rows[:k]][::-1] if start else [out(r, 0.0) for r in rows[:k]]
    q = index._unit(index.embed(query, True))
    with st.lock:
        vecs = dict(st.db.execute(f"SELECT event_id, vec FROM embeddings WHERE model=? AND event_id IN ({','.join('?' * len(rows))})", [index.model] + [r[0] for r in rows]).fetchall()) if len(rows) < 900 else \
            {e: v for e, v in st.db.execute("SELECT event_id, vec FROM embeddings WHERE model=?", (index.model,))}
    words = _tokens(query)
    text = {r[0]: float(np.frombuffer(vecs[r[0]], np.float32) @ q) + (0.15 if words and words <= _tokens(r[8]) else 0.0) for r in rows if r[0] in vecs}
    ranks = [sorted(text, key=text.get, reverse=True)]
    if image is not None:
        try:
            qi = np.asarray(image.embed_text(query), np.float32)
            sims = {r[0]: float(np.frombuffer(r[7], np.float32) @ qi) for r in rows if r[7] and r[6] == image.model}
            ranks.append(sorted(sims, key=sims.get, reverse=True))
        except Exception as ex:
            log.session("screenmem_image_search_error", error=type(ex).__name__)
    fused = {}
    for rk in ranks:
        for i, eid in enumerate(rk):
            fused[eid] = fused.get(eid, 0) + 1 / (60 + i)
    by = {r[0]: r for r in rows}
    return [out(by[e], s) for e, s in sorted(fused.items(), key=lambda kv: -kv[1])[:k]]


def _selfcheck():
    import tempfile
    from types import SimpleNamespace
    from memory_bridge.link import memory_modules, open_memory
    from models.fake import FakeEmbedder
    config.set_data_dir(tempfile.mkdtemp())
    gate.set_paused(False)
    # --- exclusions (invented content) ---
    ok = lambda app, title, text="": excluded(app, title, text, "")
    assert ok("chrome.exe", "Physics notes - Docs", "Newton's laws, chapter 4") is None
    assert ok("code.exe", "main.py - project", "def run():\n    return 1") is None
    assert ok("chrome.exe", "Cat videos", "12 comments 2024 views 1500000") is None            # numbers are not cards
    assert ok("chrome.exe", "Notes", "Card 4111 1111 1111 1111 exp 12/29") == "card number"
    assert ok("chrome.exe", "Notes", "card 4111-1111-1111-1111") == "card number" and ok("chrome.exe", "Notes", "id 123456789012") == "card number"
    assert ok("chrome.exe", "Notes", "IFSC code ABCD0123456") == "ifsc" and ok("chrome.exe", "Notes", "pay me at test.user@okaxis") == "upi id"
    assert ok("chrome.exe", "Notes", "Account number: 123456789012") in ("account", "card number")
    assert ok("chrome.exe", "Sample Bank - Net Banking login") == "bank or payment" and ok("chrome.exe", "Checkout - Cart") == "bank or payment"
    assert ok("KeePassXC.exe", "Database") == "password manager" and ok("Bitwarden.exe", "Vault") == "password manager"
    assert ok("chrome.exe", "Sign in - enter your password") == "private or password window" and ok("firefox.exe", "News - Private Browsing") == "private or password window"
    assert ok("chrome.exe", "Login", "Enter your password and OTP") == "secret field"
    assert ok("LockApp.exe", "Windows Default Lock Screen") == "lock screen" and ok("", "") == "lock screen" and ok("LogonUI.exe", "x") == "lock screen"
    assert ok("pythonw.exe", "FRED") == "FRED" and ok("msedge.exe", "FRED HUD") == "FRED"
    assert excluded("notepad.exe", "Secret Vault Notes", "", r"C:\invented\SecretVault") is None and excluded("notepad.exe", "secretvault - notes", "", r"C:\invented\SecretVault") == "vault"
    assert excluded("notepad.exe", "a", "files in C:\\invented\\secretvault\\x", r"C:\invented\SecretVault") == "vault"
    assert caption("code.exe", "main.py", "def run():\n\n  x = 1\nthird line\nfourth") == "code.exe: main.py - def run(): | x = 1 | third line"
    # --- the service: fake clock, windows, reader, embedder, image model ---
    mods = memory_modules()
    mem = open_memory(tempfile.mkdtemp())
    fe = FakeEmbedder()
    index = mods["semantic"].SemanticIndex(mem.store, lambda t, q=False: fe.embed(t, q), "fake")
    index.attach()
    now = [1_800_000_000.0]
    win = {"v": (7, "Physics notes - Docs", (0, 0, 640, 480))}
    exes = {7: "chrome.exe", 8: "KeePassXC.exe", 9: "code.exe", 10: "chrome.exe"}

    class Img:
        model = "fake-img"
        loaded = 0
        def embed_image(self, rgb):
            self.loaded += 1
            v = np.zeros(8, np.float32)
            v[int(rgb.mean()) % 8] = 1.0
            return v
        def embed_text(self, q):
            v = np.zeros(8, np.float32)
            v[3 if "red" in q else 0] = 1.0
            return v
        def idle_check(self):
            pass

    frame = SimpleNamespace(crop=lambda rect, pad=0: np.full((10, 10, 3), 3 if win["v"][0] == 9 else 100, np.uint8))
    callbacks = []
    reader = SimpleNamespace(add=lambda fn, always=False: callbacks.append(fn))
    img = Img()
    sm = ScreenMemory(reader, frame, mem, img, foreground=lambda: win["v"], exe_of=lambda h: exes[h], clock=lambda: now[0], vault="")
    seen = lambda text, hwnd=None, title=None: sm.on_text(text, {"hwnd": hwnd or win["v"][0], "title": title or win["v"][1]})

    def go(sec, n=1):
        for _ in range(n):
            now[0] += sec
            sm.tick()
        sm.flush_sync()

    sm.flush_sync = lambda: [sm._save(*sm._q.get_nowait()) or sm._q.task_done() for _ in range(sm._q.qsize())]       # no thread in the check: save synchronously
    count = lambda: mem.store.db.execute("SELECT COUNT(*) FROM screen_visits").fetchone()[0]
    seen("Newton's laws\nforce equals mass times acceleration\nchapter four review")
    go(4, 2)
    assert count() == 0                                                                  # 8 s: not a visit yet
    go(4)
    assert count() == 1 and sm.n["stored"] == 1                                          # 12 s dwell: stored once
    go(30)
    assert count() == 1
    ev = mem.store.db.execute("SELECT type, importance, content FROM events WHERE type='screen'").fetchone()
    assert ev[0] == "screen" and ev[1] == 0.1 and "[chrome.exe]" not in ev[2] and ev[2].startswith("Screen ") and "chrome.exe: Physics notes - Docs - Newton's laws | force equals" in ev[2], ev[2]
    assert mem.store.db.execute("SELECT img FROM screen_visits").fetchone()[0] is not None and sm.n["images"] == 1       # the image vector sits beside the visit
    # near-duplicate: the user leaves and comes back, text nearly the same -> merged
    win["v"] = (9, "main.py - project", (0, 0, 640, 480))
    seen("def run():\n    return 1")
    go(12, 2)
    assert count() == 2
    win["v"] = (7, "Physics notes - Docs", (0, 0, 640, 480))
    seen("Newton's laws\nforce equals mass times acceleration\nchapter four review notes")
    go(12, 2)
    assert count() == 2 and sm.n["merged"] == 1 and mem.store.db.execute("SELECT seen FROM screen_visits WHERE app='chrome.exe'").fetchone()[0] == 2
    win["v"] = (7, "Physics notes - Docs", (0, 0, 640, 480))
    now[0] += 5
    seen("Completely different content about chemistry bonds and ions")                 # same window, new text, same visit already merged -> no change until it is a new visit
    go(12, 2)
    # exclusions: nothing stored, nothing embedded
    before = (count(), mem.store.db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
    for hwnd, title, text in [(8, "Database", "entries: mail, forum"), (10, "Checkout - Cart", "total 40"), (10, "Notes", "my card 4111 1111 1111 1111"),
                              (10, "Windows Default Lock Screen", "10:42"), (10, "FRED", "hello"), (10, "Sign in", "Enter your password")]:
        win["v"] = (hwnd, title, (0, 0, 640, 480))
        seen(text)
        go(12, 3)
    assert (count(), mem.store.db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]) == before and sm.n["excluded"] == 6, sm.n
    # a page that turns into a card form after it started is dropped
    win["v"] = (10, "Travel plans", (0, 0, 640, 480))
    seen("Flights to Goa in March")
    go(5)
    seen("Flights to Goa in March\n4111 1111 1111 1111")
    go(12, 2)
    assert count() == before[0]
    # pause switch: nothing from a paused period is kept
    gate.set_paused(True)
    win["v"] = (10, "Trip budget", (0, 0, 640, 480))
    seen("Hotel 3 nights, train tickets, meals")
    go(12, 3)
    assert count() == before[0]
    gate.set_paused(False)
    go(12, 3)
    assert count() == before[0]                                                          # the dropped visit does not come back after resume
    seen("Hotel 3 nights, train tickets, meals")
    go(12, 2)
    assert count() == before[0] + 1
    # leaving a window before the next tick still stores a long-enough visit; a short one is not stored
    win["v"] = (10, "Quick look", (0, 0, 640, 480))
    seen("Short visit text here")
    go(3)
    win["v"] = (9, "other.py", (0, 0, 640, 480))
    seen("x = 2")
    go(1)
    assert count() == before[0] + 1
    # text caps at 1000 chars
    win["v"] = (10, "Long page", (0, 0, 640, 480))
    seen("word " * 400)
    go(12, 2)
    assert len(mem.store.db.execute("SELECT content FROM events WHERE content LIKE '%Long page%'").fetchone()[0].split("\n", 1)[1]) == 1000
    # search: time window, text, image
    all_ = search(mem, index)
    assert len(all_) == 4 and all_[0]["title"] == "Long page"
    t0 = datetime.fromtimestamp(1_800_000_000.0, config.IST)
    assert search(mem, index, start=(t0.replace(year=2000)).isoformat(), end=(t0.replace(year=2001)).isoformat()) == []
    hit = search(mem, index, "force mass acceleration", k=3)
    assert hit[0]["title"] == "Physics notes - Docs" and hit[0]["seen"] == 2 and "Newton" in hit[0]["caption"], hit
    ih = search(mem, index, "something red", k=3, image=img)
    assert ih, ih
    assert search(mem, index, "zebra", k=2) is not None
    # tick without memory is a no-op, text never reaches the logs
    assert ScreenMemory(reader, frame, None).stats()["stored"] == 0
    mem.close()
    print("screenmem ok")


if __name__ == "__main__":
    _selfcheck()
