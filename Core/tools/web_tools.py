# Core/tools/web_tools.py
#
# F.R.E.D.'s only sanctioned exception to "fully local" — Phase 13
# ("Reaching Outward") exists specifically to give FRED live
# information his training data and local models can't have. The
# LLM/embeddings stay 100% local; these two tools reach the network
# on purpose, only when explicitly invoked.

import re
from datetime import date, timedelta

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
# free tier only returns 3 days (today/+1/+2), so 2 is the real ceiling
# — no historical weather, no further-out forecast, on purpose.
_FORECAST_DAYS = {
    "": 0, "now": 0, "today": 0,
    "tomorrow": 1,
    "day after tomorrow": 2, "overmorrow": 2,
}
_MAX_OFFSET = 2

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_IN_DAYS_RE = re.compile(r"^in (\d+) days?$")
_RANGE_RE = re.compile(r"^(\w+)\s*(?:through|to|-|until)\s*(\w+)$")


def _weekday_offset(name: str) -> int | None:
    """Days from today to the next occurrence of weekday `name` (0-6), or None if not a weekday name."""
    if name not in _WEEKDAYS:
        return None
    return (_WEEKDAYS.index(name) - date.today().weekday()) % 7


def _single_offset(word: str) -> int | None:
    """One range endpoint: a literal ("today"/"tomorrow") or a weekday name."""
    if word in _FORECAST_DAYS:
        return _FORECAST_DAYS[word]
    return _weekday_offset(word)


def _resolve_when(when: str):
    """
    Normalized `when` -> a single day offset, a list of offsets (a
    range), or None if unrecognized/past/beyond `_MAX_OFFSET`.
    """

    if when in _FORECAST_DAYS:
        offset = _FORECAST_DAYS[when]
        return offset if offset <= _MAX_OFFSET else None

    if when in ("weekend", "this weekend", "the weekend"):
        offsets = sorted({_weekday_offset("saturday"), _weekday_offset("sunday")})
        return offsets if max(offsets) <= _MAX_OFFSET else None

    m = _IN_DAYS_RE.match(when)
    if m:
        offset = int(m.group(1))
        return offset if offset <= _MAX_OFFSET else None

    offset = _weekday_offset(when)
    if offset is not None:
        return offset if offset <= _MAX_OFFSET else None

    m = _RANGE_RE.match(when)
    if m:
        start, end = _single_offset(m.group(1)), _single_offset(m.group(2))
        if start is not None and end is not None:
            if end < start:
                end += 7
            offsets = list(range(start, end + 1))
            return offsets if max(offsets) <= _MAX_OFFSET else None

    return None


def _day_label(offset: int) -> str:
    if offset == 0:
        return "Today"
    if offset == 1:
        return "Tomorrow"
    return (date.today() + timedelta(days=offset)).strftime("%A")


def get_weather(location: str = "", when: str = "") -> str:
    """
    Get weather for a location (or the caller's auto-detected location
    if left blank), via wttr.in.

    `when` blank/"now"/"today" -> current conditions (below, unchanged).
    "tomorrow", "day after tomorrow"/"overmorrow", a weekday name, or
    "in N days" that falls within wttr.in's forecast window -> that
    day's forecast. A weekday range ("tuesday through thursday") or
    "weekend" fully inside the window -> each day's forecast joined
    into one sentence. Anything further out, or any past reference,
    gets a plain "can't do that" reply instead of a guess — wttr.in's
    free tier has no historical data and only forecasts a few days out.

    Requests condition, temperature and resolved location as three
    separate fields (%C|%t|%l) rather than wttr.in's default one-line
    report — the default glues an emoji straight onto the temperature
    ("Mumbai, Maharashtra, IN: 🌦️ +28°C"), which is a location label plus
    a symbol, not a sentence. Building the sentence here means an emoji
    TTS can't reliably render never appears, and phrasing three fields is
    still zero LLM calls.
    """

    resolved = _resolve_when((when or "").strip().lower())
    if resolved is None:
        return (
            f"I can only forecast up to {_MAX_OFFSET} days ahead, and I don't have "
            "historical weather data."
        )
    if isinstance(resolved, list):
        return " ".join(_get_forecast(location, o) for o in resolved)
    if resolved != 0:
        return _get_forecast(location, resolved)

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
    offset 0-2 -> wttr.in's JSON endpoint, day index `offset` into its
    3-day (today/+1/+2) forecast array. Midday (hourly block 4 of the
    ~8 three-hour blocks) stands in for "the day's condition" — good
    enough for a spoken one-liner, not a full breakdown.
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

    when_word = _day_label(offset)
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

        # "in 2 days" is the same offset as "day after tomorrow", different phrasing.
        in_2_days = get_weather("Mumbai", "in 2 days")
        assert "rainy" in in_2_days and "29" in in_2_days, in_2_days

        # A weekday name computed relative to "today" so this stays correct on any run date.
        today = date.today()
        in_range_day = (today + timedelta(days=2)).strftime("%A")
        by_weekday = get_weather("Mumbai", in_range_day)
        assert "rainy" in by_weekday and "29" in by_weekday, by_weekday

        # Range spanning today (offset 0) through tomorrow (offset 1), joined into one string.
        span = get_weather("Mumbai", "today through tomorrow")
        assert "sunny" in span and "30" in span and "cloudy" in span and "31" in span, span

    out_of_range = get_weather("Mumbai", "next week")
    assert f"{_MAX_OFFSET} days" in out_of_range and "historical" in out_of_range, out_of_range

    # 5 days out by weekday name is always beyond _MAX_OFFSET regardless of today's date.
    out_of_range_day = (date.today() + timedelta(days=5)).strftime("%A")
    far_weekday = get_weather("Mumbai", out_of_range_day)
    assert f"{_MAX_OFFSET} days" in far_weekday and "historical" in far_weekday, far_weekday

    past = get_weather("Mumbai", "yesterday")
    assert f"{_MAX_OFFSET} days" in past and "historical" in past, past

    print("web_tools weather forecast self-check: all passed")
