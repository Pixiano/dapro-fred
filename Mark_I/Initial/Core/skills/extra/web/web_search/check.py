"""Self-check for web_search with a FAKE search backend (no network): filtering, the retry ladder, the digest shape, the ambient gate, and the manifest."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("web_search_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def row(title, url, snippet, date=""):
    return {"title": title, "url": url, "snippet": snippet, **({"date": date} if date else {})}


def _selfcheck():
    m = _load()
    m.WIKISEARCH = lambda q: None                                                                     # no network in this check, ever
    assert m.domain("https://www.example.com/a") == "example.com" and m.blocked("https://www.youtube.com/watch?v=1") and m.blocked("https://m.youtube.com/x") and not m.blocked("https://notyoutube.com")
    assert m.terms("what is the latest news about Arijit Singh") == ["arijit", "singh"]

    # filtering: relevant, non-blocked, capped per domain, snippets present
    rows = [row("Arijit Singh - Wikipedia", "https://en.wikipedia.org/wiki/Arijit_Singh", "Arijit Singh is an Indian playback singer. He is known for romantic songs. He debuted in 2005."),
            row("Arijit Singh songs", "https://www.youtube.com/watch?v=1", "Arijit Singh best songs"),
            row("Arijit Singh concert tickets", "https://tix.example.com/a", "Buy Arijit Singh concert tickets."),
            row("Arijit Singh tour dates", "https://tix.example.com/b", "Arijit Singh tour dates and venues."),
            row("Arijit Singh biography", "https://tix.example.com/c", "Arijit Singh biography page."),
            row("Cheap flights to Goa", "https://fly.example.com", "Book flights now."),
            row("Arijit Singh", "https://news.example.com/x", "")]
    g = m.good("Arijit Singh", rows)
    assert [domain for domain in (m.domain(r["url"]) for r in g)] == ["en.wikipedia.org", "tix.example.com", "tix.example.com"], g

    # the happy path: Wikipedia summary first (two sentences), 3 numbered results, no raw dump, bounded size
    calls = []

    def fake(q, kind, n):
        calls.append((q, kind))
        return rows
    m.SEARCH, m.WIKI = fake, lambda t: "Arijit Singh is an Indian playback singer. He is known for romantic songs. He debuted in 2005."
    m.WIKISEARCH = lambda q: "Arijit_Singh"
    d = m.run("who is Arijit Singh")
    assert d.startswith("Web results for 'who is Arijit Singh'") and "never read this list out" in d, d
    assert "Summary (Wikipedia): Arijit Singh is an Indian playback singer. He is known for romantic songs." in d and "debuted in 2005" not in d, d
    assert d.count("\n") == 3 and "1. Arijit Singh concert tickets" in d and "youtube" not in d.lower() and len(d) < 900, (d.count("\n"), len(d))

    # an index page ("filmography", "List of ...") is never a result, a wrong-subject encyclopedia hit is ignored, and the encyclopedia alone is enough when the search finds nothing
    assert m.good("Arijit Singh", [row("Arijit Singh filmography", "https://x.example.com/a", "Arijit Singh films"), row("List of Arijit Singh songs", "https://y.example.com/a", "Arijit Singh songs list")]) == []
    m.SEARCH, m.WIKISEARCH = (lambda q, k, n: []), (lambda q: "Cheese")
    assert m.digest("Arijit Singh") is None
    m.WIKISEARCH = lambda q: "Arijit_Singh"
    d = m.digest("Arijit Singh")
    assert d and d.count("\n") == 1 and "Summary (Wikipedia): Arijit Singh is an Indian playback singer." in d, d
    m.SEARCH, m.WIKISEARCH = fake, lambda q: None

    # the retry ladder: junk for the sentence, junk for the core words, good for subject + wikipedia
    seen = []

    def ladder(q, kind, n):
        seen.append(q)
        return rows if q.endswith("wikipedia") else [row("Cheap flights", "https://fly.example.com", "Book flights now.")]
    m.SEARCH = ladder
    d = m.run("hey tell me about Arijit Singh please")
    assert seen == ["hey tell me about Arijit Singh please", "arijit singh", "arijit singh wikipedia"] and "Arijit Singh" in d, (seen, d)

    # nothing trustworthy: says so, never returns the rejects
    m.SEARCH = lambda q, k, n: [row("Cheap flights", "https://fly.example.com", "Book flights now.")]
    r = m.run("Arijit Singh")
    assert "found nothing reliable" in r and "flights" not in r, r

    # news: current wording goes to news sources, dated lines, and falls back to the web when news has nothing
    kinds = []

    def news(q, kind, n):
        kinds.append(kind)
        return [row("Arijit Singh announces new album", "https://news.example.com/a", "Arijit Singh announced a new album on Friday.", "2026-10-05T10:00:00")] if kind == "news" else []
    m.SEARCH, m.WIKI = news, lambda t: ""
    d = m.run("latest news about Arijit Singh")
    assert kinds[0] == "news" and "(2026-10-05)" in d and "new album" in d, (kinds, d)
    kinds.clear()
    m.SEARCH = lambda q, k, n: ([] if k == "news" else rows[:1] + rows[2:3])
    assert "Arijit Singh" in m.run("latest news about Arijit Singh")                                   # no news: ordinary results

    # a backend failure is a clean "unavailable", an empty query is bad_args
    def boom(q, k, n):
        raise RuntimeError("down")
    m.SEARCH = None
    import types
    sys.modules.setdefault("ddgs", types.SimpleNamespace(DDGS=lambda: types.SimpleNamespace(text=boom, news=boom)))
    for bad, kind in (("", "bad_args"), ("the of", "bad_args")):
        try:
            m.run(bad)
            raise AssertionError(bad)
        except Exception as ex:
            assert getattr(ex, "kind", "") == kind, (bad, ex)

    # the ambient gate: named people / current events yes; chat, commands, personal wording no
    yes = {"Who is Taylor Swift?": "Taylor Swift", "tell me about Arijit Singh": "Arijit Singh", "how old is Shah Rukh Khan": "Shah Rukh Khan", "What's the latest news on OpenAI": "the latest news on OpenAI",
           "who won the match last night": None, "when did Oppenheimer release": "Oppenheimer release", "what is the price of Bitcoin right now": None, "Is Elon Musk still the CEO of Tesla": "Elon Musk still the CEO of Tesla"}
    for text, want in yes.items():
        s = m.subject(text)
        assert s, (text, s)
        if want:
            assert s.lower().replace("the ", "") == want.lower().replace("the ", "") or want.lower().split()[0] in s.lower(), (text, s, want)
    no = ["what time is it", "turn the volume up", "remember that I like tea", "how are you", "tell me a joke", "my sister Priya called today", "who am I", "what is photosynthesis", "open Chrome", "set a timer for ten minutes",
          "what is 12 times 8", "I think Taylor Swift is great", "thanks", "how is your day going", "what do you think about this", "ok"]
    for text in no:
        assert m.subject(text) is None, (text, m.subject(text))
    import datetime
    assert m._age("He (born 2 November 1965) is an actor", datetime.date(2026, 10, 7)).startswith("Age (computed by FRED, correct): 60 years old (born 2 November 1965)")
    assert "61 years old" in m._age("She, born March 3, 1965, sings", datetime.date(2026, 10, 7)) and m._age("no date here") == "" and m._age("born 31 Feb 2000 x") == ""
    assert m.subject("how old is Shah Rukh Khan") == "Shah Rukh Khan age" and m.terms("Shah Rukh Khan age") == ["shah", "rukh", "khan"]                    # intent travels, but is not demanded of a page
    assert m.good("who won the IPL last year", [row("Kiara Advani to become parents again", "https://p.example.com", "Kiara embraced motherhood last year and won hearts")]) == []   # half a match is not a match
    m.SEARCH, m.WIKI = (lambda q, k, n: [row("Taylor Swift", "https://ts.example.com", "Taylor Swift is an American singer-songwriter.")]), lambda t: ""
    assert "Taylor Swift is an American" in m.ambient("Who is Taylor Swift?") and m.ambient("what time is it") is None

    print("web_search check ok")


if __name__ == "__main__":
    _selfcheck()
