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

# A snippet is a page's meta description — often a full paragraph, and
# occasionally an entire cookie-consent notice. Whole ones are wasted
# tokens at best and unlistenable at worst.
_SNIPPET_CHARS = 220


def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the live web (DuckDuckGo) and return a short, readable
    summary of the top results.

    URLs are deliberately NOT included. This result is spoken aloud, and
    a URL read by TTS is "h t t p s colon slash slash w w w dot youtube
    dot com slash watch question mark v equals C K Z v W h C q x 1 s" —
    confirmed in session_2026-08-01_14-24-11.jsonl, where a search
    result's full YouTube link was read out to Vatsal character by
    character. persona.md already forbids reading paths aloud for the
    same reason; a link is worse, being longer and more random.

    Nothing downstream needs the href either: FRED can't follow a link,
    and open_website takes a domain the user says out loud, not one
    scraped from a result. Titles and trimmed snippets are the whole
    useful payload.
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

        if len(snippet) > _SNIPPET_CHARS:
            # Cut at a word boundary so TTS never reads half a word.
            snippet = snippet[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "..."

        lines.append(f"- {title}: {snippet}" if snippet else f"- {title}")

    return f"Top results for '{query}':\n" + "\n".join(lines)


# =========================================================
# WEATHER
# =========================================================

# "" / "now" / "today" -> 0 (current conditions, unchanged path below).
# Anything else recognized -> a wttr.in forecast day offset. wttr.in's
# free tier only returns 3 days (today/+1/+2), so that's the entire
# map — no historical weather, no further-out forecast, on purpose.
_FORECAST_DAYS = {
    "": 0, "now": 0, "today": 0,
    "tomorrow": 1,
    "day after tomorrow": 2, "overmorrow": 2,
}


def get_weather(location: str = "", when: str = "") -> str:
    """
    Get weather for a location (or the caller's auto-detected location
    if left blank), via wttr.in.

    `when` blank/"now"/"today" -> current conditions (below, unchanged).
    "tomorrow" / "day after tomorrow" -> a short forecast via wttr.in's
    JSON endpoint. Anything else (further out, or any past reference)
    gets a plain "can't do that" reply instead of a guess — wttr.in's
    free tier has no historical data and only forecasts 3 days out.

    Requests condition, temperature and resolved location as three
    separate fields (%C|%t|%l) rather than wttr.in's default one-line
    report — the default glues an emoji straight onto the temperature
    ("Mumbai, Maharashtra, IN: 🌦️ +28°C"), which is a location label plus
    a symbol, not a sentence. Building the sentence here means an emoji
    TTS can't reliably render never appears, and phrasing three fields is
    still zero LLM calls.
    """

    offset = _FORECAST_DAYS.get((when or "").strip().lower())
    if offset is None:
        return (
            "I can only forecast about 3 days ahead, and I don't have "
            "historical weather data."
        )
    if offset != 0:
        return _get_forecast(location, offset)

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


def _get_forecast(location: str, offset: int) -> str:
    """
    offset 1 or 2 -> wttr.in's JSON endpoint, day index `offset` into
    its 3-day (today/+1/+2) forecast array. Midday (hourly block 4 of
    the ~8 three-hour blocks) stands in for "the day's condition" —
    good enough for a spoken one-liner, not a full breakdown.
    """

    url = f"https://wttr.in/{location}" if location else "https://wttr.in/"

    try:
        response = requests.get(
            url,
            params={"format": "j1"},
            headers={"User-Agent": "curl"},
            timeout=10,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        body = (e.response.text or "").strip().lower() if e.response is not None else ""
        if "not found" in body or "invalid" in body:
            return f"Couldn't find weather for '{location}'." if location else "Couldn't fetch weather."
        return f"Couldn't fetch weather: {e}"
    except requests.RequestException as e:
        return f"Couldn't fetch weather: {e}"

    try:
        data = response.json()
        day = data["weather"][offset]
        max_t, min_t = day["maxtempC"], day["mintempC"]
        condition = day["hourly"][4]["weatherDesc"][0]["value"]
        place = data["nearest_area"][0]["areaName"][0]["value"]
    except (ValueError, KeyError, IndexError, TypeError):
        return "Couldn't get the forecast — wttr.in didn't return what I expected."

    when_word = "Tomorrow" if offset == 1 else "The day after tomorrow"
    return (
        f"{when_word} in {place} looks like {condition[0].lower()}{condition[1:]}, "
        f"with a high of {max_t}°C and a low of {min_t}°C."
    )


if __name__ == "__main__":
    # Self-check for the forecast branch logic — not in Core/tests/
    # because that folder's own README restricts it to regression
    # tests pinning a bug that actually happened; this is new-feature
    # logic, not a pinned bug. Offline: mocks requests.get.
    from unittest.mock import patch

    _FAKE_J1 = {
        "nearest_area": [{"areaName": [{"value": "Mumbai"}]}],
        "weather": [
            {"maxtempC": "30", "mintempC": "25", "hourly": [{}] * 4 + [{"weatherDesc": [{"value": "Sunny"}]}]},
            {"maxtempC": "31", "mintempC": "26", "hourly": [{}] * 4 + [{"weatherDesc": [{"value": "Cloudy"}]}]},
            {"maxtempC": "29", "mintempC": "24", "hourly": [{}] * 4 + [{"weatherDesc": [{"value": "Rainy"}]}]},
        ],
    }

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return _FAKE_J1

    with patch("requests.get", return_value=_FakeResponse()):
        tomorrow = get_weather("Mumbai", "tomorrow")
        assert "Mumbai" in tomorrow and "cloudy" in tomorrow and "31" in tomorrow, tomorrow

        overmorrow = get_weather("Mumbai", "day after tomorrow")
        assert "rainy" in overmorrow and "29" in overmorrow, overmorrow

    out_of_range = get_weather("Mumbai", "next week")
    assert "3 days" in out_of_range and "historical" in out_of_range, out_of_range

    past = get_weather("Mumbai", "yesterday")
    assert "3 days" in past and "historical" in past, past

    print("web_tools weather forecast self-check: all passed")
