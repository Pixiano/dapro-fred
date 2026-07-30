# Core/tools/web_tools.py
#
# F.R.E.D.'s only sanctioned exception to "fully local" — Phase 13
# ("Reaching Outward") exists specifically to give FRED live
# information his training data and local models can't have. The
# LLM/embeddings stay 100% local; these two tools reach the network
# on purpose, only when explicitly invoked.

import requests

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


# =========================================================
# WEB SEARCH
# =========================================================

def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the live web (DuckDuckGo) and return a short, readable
    summary of the top results.
    """

    if not DDGS:
        return "Web search unavailable: duckduckgo-search module not installed."

    try:
        results = list(DDGS().text(query, max_results=max_results))
    except Exception as e:
        return f"Web search failed: {e}"

    if not results:
        return f"No web results found for '{query}'."

    lines = []
    for r in results:
        title = r.get("title", "").strip()
        snippet = r.get("body", "").strip()
        url = r.get("href", "").strip()
        lines.append(f"- {title}: {snippet} ({url})")

    return "\n".join(lines)


# =========================================================
# WEATHER
# =========================================================

def get_weather(location: str = "") -> str:
    """
    Get current weather for a location (or the caller's
    auto-detected location if left blank), via wttr.in.

    Requests condition, temperature and resolved location as three
    separate fields (%C|%t|%l) rather than wttr.in's default one-line
    report — the default glues an emoji straight onto the temperature
    ("Mumbai, Maharashtra, IN: 🌦️ +28°C"), which is a location label plus
    a symbol, not a sentence. Building the sentence here means an emoji
    TTS can't reliably render never appears, and phrasing three fields is
    still zero LLM calls.
    """

    url = f"https://wttr.in/{location}" if location else "https://wttr.in/"

    try:
        response = requests.get(
            url,
            params={"format": "%C|%t|%l"},
            headers={"User-Agent": "curl"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        # An unresolvable location isn't a 200 with an error message in
        # the body — wttr.in answers it with an actual 500, so
        # raise_for_status() throws before the "location not found" text
        # in the body is ever read, and every bad location used to surface
        # as a raw "500 Server Error" string instead of a clean message.
        body = (e.response.text or "").strip().lower() if e.response is not None else ""
        if "not found" in body or "invalid" in body:
            return f"Couldn't find weather for '{location}'." if location else "Couldn't fetch weather."
        return f"Couldn't fetch weather: {e}"
    except requests.RequestException as e:
        return f"Couldn't fetch weather: {e}"

    text = response.text.strip()
    parts = text.split("|")

    if len(parts) != 3:
        return f"Couldn't find weather for '{location}'." if location else "Couldn't fetch weather."

    condition, temp, resolved = (p.strip() for p in parts)
    temp = temp.lstrip("+")

    # Lowercased for "with light rain showers" — mid-sentence, not a label.
    return f"It's {temp} in {resolved} right now, with {condition[0].lower()}{condition[1:]}."
