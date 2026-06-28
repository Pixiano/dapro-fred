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
    """

    url = f"https://wttr.in/{location}" if location else "https://wttr.in/"

    try:
        response = requests.get(
            url,
            params={"format": "3"},
            headers={"User-Agent": "curl"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        return f"Couldn't fetch weather: {e}"

    text = response.text.strip()

    if not text or "Unknown location" in text:
        return f"Couldn't find weather for '{location}'."

    return text
