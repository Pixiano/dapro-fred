"""News sources: every fetcher returns items {slot, title, snippet, source, url, ts, text?} and never raises (a dead source is skipped and logged).

Sockets
  in : collect(fetch=None) -> list of items      fetch(url) -> bytes replaces the network (tests)
       snacks_items(text) -> list of items       parse one "AI Snacks" newsletter email body (sender humans@mail.galaxy.ai, from Magica)
  out: items for the slots ai | india | boards | world | general
Slots: ai = Hugging Face blog + daily papers, Hacker News (AI words), The Verge AI, the AI Snacks email; india = The Hindu, Indian Express, NDTV;
       boards = CBSE/NCERT/board-exam items found in the Indian and education feeds; world = BBC World, Al Jazeera; general = BBC science.
Feed text is untrusted: it only ever reaches the tool-less summariser.
Self-check: python news/sources.py
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import log

UA = {"User-Agent": "Mozilla/5.0 (Mark I news)"}
RSS = [("india", "The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
       ("india", "Indian Express", "https://indianexpress.com/section/india/feed/"),
       ("india", "NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
       ("boards", "The Hindu", "https://www.thehindu.com/education/feeder/default.rss"),
       ("boards", "Indian Express", "https://indianexpress.com/section/education/feed/"),
       ("world", "BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
       ("world", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
       ("general", "BBC", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
       ("ai", "Hugging Face blog", "https://huggingface.co/blog/feed.xml"),
       ("ai", "The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml")]
BOARDS = re.compile(r"\b(CBSE|NCERT|board exams?|boards?|class (?:10|12|X|XII)|CUET|NEET|JEE)\b", re.I)
AI_WORDS = re.compile(r"\b(AI|LLM|GPT|Claude|Gemini|OpenAI|Anthropic|DeepSeek|Llama|model|benchmark|Hugging Face)\b", re.I)
SNACKS_FROM = "humans@mail.galaxy.ai"
_AD = re.compile(r"(magica|join millions|unsubscribe|sponsor|view in browser|forward this)", re.I)


def _get(url: str, fetch=None) -> bytes:
    if fetch:
        return fetch(url)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
        return r.read()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _ts(s: str) -> float:
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return time.time()


def rss_items(xml: bytes, slot: str, source: str) -> list:
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()
        title = _clean(g("title"))
        if title:
            out.append({"slot": slot, "title": title, "snippet": _clean(g("description"))[:600], "source": source, "url": g("link"), "ts": _ts(g("pubDate"))})
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):                         # Atom (Hugging Face)
        ns = "{http://www.w3.org/2005/Atom}"
        link = it.find(ns + "link")
        title = _clean(it.findtext(ns + "title") or "")
        if title:
            out.append({"slot": slot, "title": title, "snippet": _clean(it.findtext(ns + "summary") or "")[:600], "source": source,
                        "url": link.get("href", "") if link is not None else "", "ts": time.time()})
    return out[:25]


def snacks_items(text: str) -> list:
    """Headline lines start with an emoji; their bullets follow; the Magica sponsor block (before the first headline) and the footer are dropped."""
    items, cur = [], None
    for ln in (x.strip() for x in text.splitlines()):
        if not ln:
            continue
        head = ord(ln[0]) > 0x2000 and ln[0] not in "✅👉" and len(ln) < 140 and not ln.startswith(("*", "•"))
        if head and not _AD.search(ln):
            cur = {"slot": "ai", "title": re.sub(r"^[^\w\"'(]+", "", ln) or ln, "snippet": "", "source": "AI Snacks (Magica)", "url": "", "ts": time.time()}
            items.append(cur)
        elif cur is not None:
            if _AD.search(ln):
                cur = None
            else:
                cur["snippet"] = (cur["snippet"] + " " + ln.lstrip("*• ").strip())[:900]
    return [i for i in items if i["snippet"]]


def _gmail_snacks() -> list:
    from tools import gmail
    out = []
    for m in gmail.search(f"from:{SNACKS_FROM} newer_than:2d", 1):
        out += snacks_items(m["text"])
    return out


def _json(url: str, fetch=None):
    return json.loads(_get(url, fetch))


def _hf_papers(fetch=None) -> list:
    rows = sorted(_json("https://huggingface.co/api/daily_papers", fetch), key=lambda r: -(r.get("paper", {}).get("upvotes") or 0))[:4]
    return [{"slot": "ai", "title": r["paper"]["title"], "snippet": _clean(r["paper"].get("summary", ""))[:900], "source": "Hugging Face papers",
             "url": "https://huggingface.co/papers/" + r["paper"]["id"], "ts": time.time()} for r in rows if r.get("paper")]


def _hn(fetch=None) -> list:
    since = int(time.time() - 36 * 3600)
    q = urllib.parse.urlencode({"query": "AI model", "tags": "story", "numericFilters": f"points>100,created_at_i>{since}", "hitsPerPage": 8})
    return [{"slot": "ai", "title": h["title"], "snippet": "", "source": "Hacker News", "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "ts": float(h["created_at_i"])} for h in _json("https://hn.algolia.com/api/v1/search?" + q, fetch).get("hits", []) if AI_WORDS.search(h.get("title", ""))]


def collect(fetch=None) -> list:
    items = []
    jobs = [(f"{s}/{n}", lambda s=s, n=n, u=u: rss_items(_get(u, fetch), s, n)) for s, n, u in RSS]
    jobs += [("hf_papers", lambda: _hf_papers(fetch)), ("hn", lambda: _hn(fetch))]
    if not fetch:
        jobs.append(("snacks", _gmail_snacks))
    for name, job in jobs:
        try:
            items += job()
        except Exception as ex:
            log.session("news_source_failed", source=name, error=type(ex).__name__)
    extra = [dict(i, slot="boards") for i in items if i["slot"] in ("india", "world") and BOARDS.search(i["title"])]       # a board-exam item in a general feed is also a boards candidate
    return items + extra


def _selfcheck():
    xml = b"<rss><channel><item><title>CBSE announces dates</title><description>&lt;p&gt;Boards from 17 Feb&lt;/p&gt;</description><link>http://x/1</link><pubDate>Mon, 05 Oct 2026 06:00:00 GMT</pubDate></item></channel></rss>"
    r = rss_items(xml, "india", "T")
    assert r[0]["snippet"] == "Boards from 17 Feb" and r[0]["url"] == "http://x/1" and r[0]["slot"] == "india"
    body = "OpenAI misses.\n\nAI Snacks\n\n✅ Can run every model\n👉 Join millions on Magica\n\n📉 OpenAI Misses by $20B\n\n* Revenue is $50B.\n* Researchers went public.\n\n🔒 Anthropic cyber mission\n\n* A new unit.\n\nUnsubscribe here\n"
    s = snacks_items(body)
    assert [i["title"] for i in s] == ["OpenAI Misses by $20B", "Anthropic cyber mission"], s
    assert "Researchers went public" in s[0]["snippet"] and "Unsubscribe" not in s[1]["snippet"]
    pages = {"https://huggingface.co/api/daily_papers": json.dumps([{"paper": {"id": "1", "title": "P1", "summary": "S", "upvotes": 9}}]).encode()}
    got = collect(lambda u: pages[u] if u in pages else (_ for _ in ()).throw(OSError("offline")))
    assert [i["title"] for i in got] == ["P1"], got
    print("news sources ok")


if __name__ == "__main__":
    _selfcheck()
