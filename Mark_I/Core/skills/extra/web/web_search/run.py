"""web_search: look something up on the web and hand back a SHORT digest, never a page dump. Add-on skill (skills/extra).

Sockets
  in : run(query, kind="auto")   kind: "auto" | "news" (news sources, last week or so) | "web"
       ambient(text) -> str | None   called by the engine before the model answers (engine/loop.py `_ambient`): returns a digest when the user's words ask about a named person, thing or
                                      current event the 4B model cannot know, otherwise None. Only the extracted subject leaves the PC, never the sentence; nothing runs for personal, command or chat wording.
  out: text for the MODEL (not spoken as is, not `say`): at most 3 results, one trimmed snippet each, plus a two-sentence encyclopedia summary when a Wikipedia page is among them,
       headed "Web results for ...; answer in one or two spoken sentences, do not read this list out". Flags: reads_untrusted (after reading, nothing may be sent out this turn).
Quality (the "no dumb results, try again" rule): a result must share at least 0.6 of the query's informative words (title + snippet), come from a non-blocked domain (YouTube is out by the
  user's call; link farms and social posts are dropped) and domains are capped at two. If fewer than two results pass, the query is rewritten and tried again, up to three attempts:
  as asked -> filler words and question phrasing removed -> subject plus "wikipedia" (or "news" for current events). If nothing passes it says so plainly; it never returns the rejects.
Backend: ddgs (DuckDuckGo, no key). Tests replace `SEARCH` and `WIKI` with fakes and never touch the network.
Self-check: python skills/extra/web/web_search/check.py
"""
import re
import threading
import time
from urllib.parse import urlparse

from tools.registry import ToolError

BLOCK = {"youtube.com", "youtu.be", "m.youtube.com", "pinterest.com", "pinterest.co.uk", "facebook.com", "instagram.com", "tiktok.com", "quora.com", "x.com", "twitter.com", "linkedin.com",
         "answers.com", "ask.com", "dictionary.com", "zhihu.com", "baidu.com", "grokipedia.com"}
FILLER = set("a an the is are was were be been of for to in on at by with about please can could would you tell me what whats who whos when where why how does do did has have tell said say "
             "latest recent new news search look up find google any some there their its it this that and or so just really actually kind sort show give know i want need hey hi hello okay ok fred thanks yeah yes last year years ago week month today tonight now right current currently still ever".split())
RECENT = re.compile(r"\b(latest|news|today|tonight|yesterday|right now|currently|score|scores|won|wins|winner|results?|release date|released|price of|net worth|died|dead|alive|trending|"
                    r"this (week|month|year)|last night|live)\b", re.I)
PERSONAL = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine|we|our|ours|you|your)\b", re.I)
ASK = re.compile(r"^\W*(?:(?:so|hey|okay|ok|and|also|btw|fred)[\s,]+)*(?:(?:who|what)(?:'s|\s+is|\s+was|\s+are|\s+were)|tell me about|what do you know about|how old is|how tall is|when (?:is|was|did|does|will)|"
                 r"where (?:is|was|are)|is|are|did|does|has|have|do you know)\b\s*(.*)$", re.I)
SUBJECT_TAIL = re.compile(r"\b(please|right now|these days|nowadays|again)\b.*$|\?+$", re.I)

LISTY = re.compile(r"^(list of|filmography|discography)|\b(filmography|discography|awards and nominations|google news)\b|\bnews of\b", re.I)         # index pages: never the answer to "who is X"
REGION = "in-en"       # NOT "wt-wt": ddgs then asks a nonexistent wt.wikipedia.org and fails
SEARCH = None          # tests: fn(query, kind, n) -> list of {"title","url","snippet"}
WIKI = None            # tests: fn(title) -> str   (summary of an English Wikipedia page)
WIKISEARCH = None      # tests: fn(query) -> str | None   (best matching page title)
_CACHE: dict = {}      # (query, kind) -> (time, rows): the same question twice in ten minutes costs one search


# ---- backends ----------------------------------------------------------------------------------------------------------------------------
def _search(query: str, kind: str, n: int = 10) -> list:
    if SEARCH:
        return SEARCH(query, kind, n)
    from ddgs import DDGS
    key = (query, kind)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    err = None
    for backend in ("bing,yahoo,duckduckgo,brave", "auto"):                          # the scraped engines answer or refuse at random: a fast set first, then ddgs's own choice
        try:
            if kind == "news":
                rows = DDGS(timeout=6).news(query, region=REGION, safesearch="moderate", timelimit="w", max_results=n, backend=backend)
                out = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("body", ""), "date": r.get("date", "")} for r in rows]
            else:
                rows = DDGS(timeout=6).text(query, region=REGION, safesearch="moderate", max_results=n, backend=backend)
                out = [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]
        except Exception as ex:
            if "No results" not in str(ex):
                err = ex
            continue
        if out:
            _CACHE[key] = (time.monotonic(), out)
            return out
    if err is not None:
        raise ToolError("unavailable", "I can't reach the web right now.", f"ddgs search failed: {err!r}")
    return []                                                                        # nothing found is an answer (the retry ladder tries the next wording), not a failure


def _wiki_title(query: str) -> str | None:
    if WIKISEARCH:
        return WIKISEARCH(query)
    import httpx
    try:
        r = httpx.get("https://en.wikipedia.org/w/api.php", params={"action": "opensearch", "search": query, "limit": 1, "format": "json"}, timeout=5, headers={"User-Agent": "FRED/1.0 (personal assistant)"})
        titles = r.json()[1] if r.status_code == 200 else []
        return titles[0].replace(" ", "_") if titles else None
    except Exception:
        return None


def _wiki(title: str) -> str:
    if WIKI:
        return WIKI(title)
    import httpx
    try:
        r = httpx.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + title, timeout=5, headers={"User-Agent": "FRED/1.0 (personal assistant)"}, follow_redirects=True)
        return r.json().get("extract", "") if r.status_code == 200 else ""
    except Exception:
        return ""


# ---- quality ---------------------------------------------------------------------------------------------------------------------------
INTENT = set("age old born birth date height tall price cost release released worth net salary population".split())     # what is asked ABOUT the subject: searched for, but a page need not repeat it


def keywords(query: str) -> list:
    return [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in FILLER and len(w) > 1]


def terms(query: str) -> list:
    """The subject words a result must mention (the question words and the intent words are left out)."""
    return [w for w in keywords(query) if w not in INTENT]


def domain(url: str) -> str:
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def blocked(url: str) -> bool:
    d = domain(url)
    return any(d == b or d.endswith("." + b) for b in BLOCK)


def relevance(query: str, row: dict) -> float:
    t = terms(query)
    if not t:
        return 0.0
    hay = (row.get("title", "") + " " + row.get("snippet", "")).lower()
    return sum(1 for w in t if w in hay) / len(t)


def good(query: str, rows: list, keep: int = 3) -> list:
    out, per = [], {}
    for r in sorted(rows, key=lambda r: -relevance(query, r)):
        d = domain(r.get("url", ""))
        if not d or blocked(r["url"]) or LISTY.search(r.get("title", "")) or relevance(query, r) < 0.6 or per.get(d, 0) >= 2 or not r.get("snippet", "").strip():
            continue
        per[d] = per.get(d, 0) + 1
        out.append(r)
        if len(out) >= keep:
            break
    return out


def rewrites(query: str, kind: str) -> list:
    core = " ".join(keywords(query)) or query
    return [query, core, core + (" news" if kind == "news" else " wikipedia")]


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def _two_sentences(text: str, n: int = 320) -> str:
    parts = re.split(r"(?<=[a-z0-9)\"'][.!?])\s+(?=[A-Z])", re.sub(r"\s+", " ", text).strip())
    return _clip(" ".join(parts[:2]), n)


def digest(query: str, kind: str = "auto") -> str | None:
    """The whole pipeline: search, filter, retry, enrich. None when nothing trustworthy was found."""
    k = "news" if kind == "news" or (kind == "auto" and RECENT.search(query)) else "web"
    box = {}
    if k == "web":                                                                   # an encyclopedia page for the subject, fetched while the search runs
        def enc():
            t = _wiki_title(" ".join(terms(query)) or query)
            if t and relevance(query, {"title": t.replace("_", " "), "snippet": ""}) >= 0.5:
                box["wiki"] = _two_sentences(_wiki(t))
        th = threading.Thread(target=enc, daemon=True)
        th.start()
    best = []
    for q in rewrites(query, k):
        rows = good(query, _search(q, k))
        if len(rows) > len(best):
            best = rows
        if len(best) >= 2:
            break
    if not best and k == "news":                                                    # nothing recent: ordinary web results still beat nothing
        best = good(query, _search(query, "web"))
    if k == "web":
        th.join(6)
    summary = box.get("wiki", "")
    wiki = next((r for r in best if "wikipedia.org/wiki/" in r["url"] and "en.wikipedia" in r["url"]), None)
    if not summary and wiki:
        summary = _two_sentences(_wiki(wiki["url"].rsplit("/wiki/", 1)[1].split("#")[0]))
    if not best and not summary:
        return None
    lines = []
    if summary:
        lines.append(f"Summary (Wikipedia): {summary}")
        best = [r for r in best if "en.wikipedia.org/wiki/" not in r["url"]]         # its snippet would only repeat the summary
    for i, r in enumerate(best, 1):
        when = f" ({r['date'][:10]})" if r.get("date") else ""
        lines.append(f"{i}. {_clip(r['title'], 80)}{when}: {_clip(r['snippet'], 180)} [{domain(r['url'])}]")
    age = _age(" ".join([summary] + [r["snippet"] for r in best])) if "age" in keywords(query) else ""
    if age:
        lines.insert(0, age)
    head = f"Web results for '{_clip(query, 80)}' (from the internet, may be wrong or stale; answer in one or two spoken sentences, never read this list out):"
    return head + "\n" + "\n".join(lines)


BORN = re.compile(r"\bborn\s+(?:on\s+)?(\d{1,2}\s+[A-Z][a-z]+\s+\d{4}|[A-Z][a-z]+\s+\d{1,2},\s+\d{4})")


def _age(text: str, today=None) -> str:
    """'Age (computed ...)' from a birth date found in the text: the small model gets date arithmetic wrong (said 58 for 60), so it is done here."""
    import datetime
    m = BORN.search(text)
    if not m:
        return ""
    for fmt in ("%d %B %Y", "%B %d, %Y"):
        try:
            b = datetime.datetime.strptime(re.sub(r"\s+", " ", m.group(1)), fmt).date()
        except ValueError:
            continue
        t = today or datetime.date.today()
        years = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
        return f"Age (computed by FRED, correct): {years} years old (born {b.day} {b.strftime('%B %Y')}); today is {t.day} {t.strftime('%B %Y')}."
    return ""


# ---- the skill and the ambient hook ----------------------------------------------------------------------------------------------------
def run(query: str, kind: str = "auto") -> str:
    q = (query or "").strip()
    if len(terms(q)) == 0:
        raise ToolError("bad_args", "What should I look up?", "empty query")
    d = digest(q, kind)
    if d is None:
        return f"I searched for '{_clip(q, 60)}' and found nothing reliable. Say so plainly; do not guess."
    return d


def subject(text: str) -> str | None:
    """The thing to look up if these words ask about a named person/thing or a current event, else None."""
    t = (text or "").strip()
    if len(t) < 8 or len(t) > 160 or PERSONAL.search(re.sub(r"\b(tell|show|give|let) me\b", "", t, flags=re.I)):
        return None
    m = ASK.match(t)
    body = SUBJECT_TAIL.sub("", (m.group(1) if m else t)).strip(" ,.")
    words = re.findall(r"[A-Za-z0-9']+", body)
    named = any(w[0].isupper() and len(w) > 2 for w in words[(0 if m else 1):]) or bool(re.search(r"\b(19|20)\d\d\b", body))      # a name or a year (the first word of a plain sentence is capitalised anyway)
    extra = " age" if re.match(r"^\W*how old\b", t, re.I) else (" height" if re.match(r"^\W*how tall\b", t, re.I) else "")        # the question's own intent travels with the subject
    if RECENT.search(t) and (m or named or len(terms(t)) >= 2):
        return " ".join(re.findall(r"[A-Za-z0-9']+", body or t)) + extra
    if m and named and len(terms(body)) >= 1:
        return " ".join(re.findall(r"[A-Za-z0-9']+", body)) + extra
    return None


def ambient(text: str) -> str | None:
    s = subject(text)
    return digest(s) if s else None
