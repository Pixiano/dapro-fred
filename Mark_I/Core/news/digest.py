"""The daily news digest: build an edition at 06:00 and 18:00, store it, give the memory a low-weight copy of each story.

Sockets
  in : build(chat, embed=None, memory=None, now=None, fetch=None, article=None) -> edition dict
         chat   = models.client.Chat (tool-less: feed text is untrusted); embed(texts) -> vectors (the Embedder), or None = recency order only
         memory = manager.Memory or None: each story becomes an event type "news" with importance 0.2 (the stored paragraph stays in the day file)
       due(now=None) -> "06:00" | "18:00" | None      the latest slot hour of today that has no edition yet
       latest(now=None) -> edition | None             today's newest edition, else yesterday's
       config.news_hours ("6,18"), day files at <data_dir>/news/YYYY/MM/DD.json (India time)
  out: edition = {"at": "06:00", "built": iso, "items": [{n, slot, title, short, detail, source, url}]}; slots and quotas in QUOTA
Each story keeps a short spoken two-liner AND a detailed paragraph (source named, link kept): the tool speaks the short one and fetches the detail on request.
Self-check: python news/digest.py
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from news import sources

QUOTA = {"ai": 3, "india": 2, "boards": 1, "world": 2, "general": 1}
TOPIC = {"ai": "major AI news: new models, benchmarks, product launches and new features, research breakthroughs, controversies",
         "india": "major Indian news: national politics, deaths of well-known people, serious crimes, new laws and policy affecting prices",
         "boards": "CBSE and NCERT board exams: dates, syllabus, results, rules",
         "world": "wars and major international conflicts, diplomacy and big political discussions",
         "general": "a surprising or interesting story of general interest from around the world"}
SCHEMA = {"type": "object", "required": ["short", "detail"], "properties": {"short": {"type": "string"}, "detail": {"type": "string"}}, "additionalProperties": False}
SAY = ("You condense one news item for a voice assistant. Use ONLY the text given, add no facts and no opinion. Reply as JSON: "
       "'short' = {short}, plain spoken words; 'detail' = one detailed paragraph (4 to 6 sentences) with the who, what, when, numbers and why it matters, as far as the text says.")


def _dir() -> Path:
    return config.data_dir() / "news"


def _day(now: datetime) -> Path:
    return _dir() / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}.json"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"editions": []}


def hours() -> list:
    return sorted(int(h) for h in str(config.get().news_hours).replace(" ", "").split(",") if h)


def due(now=None):
    now = now or datetime.now(config.IST)
    have = {e["at"] for e in _load(_day(now))["editions"]}
    past = [h for h in hours() if h <= now.hour]
    return f"{past[-1]:02d}:00" if past and f"{past[-1]:02d}:00" not in have else None            # only the newest slot: a missed 06:00 is not rebuilt at 18:30


def latest(now=None):
    now = now or datetime.now(config.IST)
    for d in (now, now.replace(day=max(1, now.day - 1))):
        ed = _load(_day(d))["editions"]
        if ed:
            return ed[-1]
    return None


def _unit(m):
    m = np.asarray(m, dtype=np.float32)
    return m / (np.linalg.norm(m, axis=-1, keepdims=True) + 1e-9)


def pick(items: list, embed=None, seen=(), now=None) -> list:
    now = now or time.time()
    items = [i for i in items if i["title"] not in seen]
    if embed and items:
        v = _unit(embed([f"{i['title']}. {i['snippet'][:200]}" for i in items]))
        t = dict(zip(TOPIC, _unit(embed(list(TOPIC.values()), True))))
    out = []
    for slot, n in QUOTA.items():
        cand = [(k, i) for k, i in enumerate(items) if i["slot"] == slot and (slot != "boards" or sources.BOARDS.search(i["title"] + " " + i["snippet"]))]
        scored = sorted(((float(v[k] @ t[slot]) if embed else 0.0) + 0.1 * max(0.0, 1 - (now - i["ts"]) / 86400) + (0.1 if "Snacks" in i["source"] else 0.0), k, i) for k, i in cand)[::-1]
        got = []
        for s, k, i in scored:
            if len(got) == n:
                break
            if embed and any(float(v[k] @ v[kk]) > 0.85 for kk, _ in got):         # the same story from another feed
                continue
            got.append((k, dict(i, slot=slot)))
        out += [i for _, i in got]
    return out


def _article(url: str) -> str:
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=sources.UA), timeout=12).read(400_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside)\b.*?</\1>", " ", raw)
    return sources._clean(raw)[:3500]


def summarise(chat, item: dict, article=_article) -> dict:
    text = item["snippet"] if item["source"].startswith(("AI Snacks", "Hugging Face papers")) or not item["url"] else (article(item["url"]) or item["snippet"])
    short = "one short sentence" if item["slot"] == "boards" else "two short sentences"
    try:
        r = chat.complete([{"role": "system", "content": SAY.format(short=short)},
                           {"role": "user", "content": f"Title: {item['title']}\nSource: {item['source']}\nText: {text[:3500] or item['title']}"}], schema=SCHEMA, think=False).json()
        short_s, detail = r["short"].strip(), r["detail"].strip()
    except Exception as ex:
        log.session("news_summary_failed", title=item["title"][:60], error=type(ex).__name__)
        short_s, detail = item["title"], item["snippet"] or item["title"]
    return {"slot": item["slot"], "title": item["title"], "short": short_s, "detail": detail, "source": item["source"], "url": item["url"]}


def build(chat, embed=None, memory=None, now=None, fetch=None, article=_article) -> dict:
    now = now or datetime.now(config.IST)
    day = _load(_day(now))
    seen = {t for d in (now, now.replace(day=max(1, now.day - 1))) for t in _load(_day(d)).get("seen", [])}
    items = pick(sources.collect(fetch), embed, seen, now.timestamp())
    slots = [h for h in hours() if h <= now.hour]
    ed = {"at": f"{slots[-1] if slots else now.hour:02d}:00", "built": now.isoformat(), "items": []}
    for n, it in enumerate(items, 1):
        ed["items"].append(dict(summarise(chat, it, article), n=n))
    day["editions"].append(ed)
    day["seen"] = sorted(set(day.get("seen", [])) | {i["title"] for i in items})
    p = _day(now)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    if memory is not None:
        for it in ed["items"]:
            memory.store.add_event("news", f"News {now:%d %b} {ed['at']} [{it['slot']}]: {it['short']} (source: {it['source']})", importance=0.2, ts=now.isoformat())
    log.session("news_built", at=ed["at"], items=len(ed["items"]))
    return ed


def _selfcheck():
    import tempfile
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as d:
        import os
        os.environ["FRED_DATA"] = d
        config._data = None
        config.reload()
        now = datetime(2026, 10, 11, 18, 5, tzinfo=config.IST)
        assert due(now) == "18:00" and due(now.replace(hour=5)) is None and hours() == [6, 18]
        t0 = now.timestamp()
        items = [{"slot": s, "title": f"{s[:2]} story {k}", "snippet": f"{s[:2]} text", "source": "Src", "url": "", "ts": t0 - k * 60}
                 for s in QUOTA for k in range(4)] + [{"slot": "boards", "title": "Unrelated", "snippet": "none", "source": "Src", "url": "", "ts": t0}]
        got = pick(items, None, {"ai story 0"}, t0)
        by = {s: [i["title"] for i in got if i["slot"] == s] for s in QUOTA}
        assert [len(by[s]) for s in QUOTA] == [3, 2, 0, 2, 1], by          # quotas; boards has no CBSE title -> none
        assert "ai story 0" not in by["ai"] and "Unrelated" not in by["boards"]
        boards_items = pick([dict(items[8], title="CBSE sets dates")], None, (), t0)
        assert [i["title"] for i in boards_items] == ["CBSE sets dates"]
        chat = SimpleNamespace(complete=lambda m, **kw: SimpleNamespace(json=lambda: {"short": "Two lines.", "detail": "A long paragraph."}))
        s = summarise(chat, items[0], lambda u: "")
        assert s["short"] == "Two lines." and s["detail"] == "A long paragraph." and s["source"] == "Src"
        bad = SimpleNamespace(complete=lambda m, **kw: (_ for _ in ()).throw(ValueError("x")))
        assert summarise(bad, items[0], lambda u: "")["short"] == "ai story 0"          # a failing model never loses the story
        events = []
        mem = SimpleNamespace(store=SimpleNamespace(add_event=lambda *a, **k: events.append((a, k))))
        orig = sources.collect
        sources.collect = lambda fetch=None: items
        ed = build(chat, None, mem, now)
        sources.collect = orig
        assert ed["at"] == "18:00" and len(ed["items"]) == 8 and ed["items"][0]["n"] == 1 and due(now) is None
        assert latest(now)["items"][0]["detail"] == "A long paragraph." and len(events) == 8 and events[0][1]["importance"] == 0.2
        assert "source: Src" in events[0][0][1]
    print("news digest ok")


if __name__ == "__main__":
    _selfcheck()
