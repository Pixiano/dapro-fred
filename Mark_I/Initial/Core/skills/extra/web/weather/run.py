"""weather: current conditions and the next days for a city, from Open-Meteo (free, no key). Add-on skill (skills/extra).

Sockets
  in : run(city="", when="now")      city: any place name; empty -> config.weather_city, else ToolError bad_args "Which city?"
                                     when: now | today | tomorrow | week
       FETCH(url) -> dict            tests replace it; only the place name and its coordinates are sent, never the user's sentence
  out: one plain line per fact for the model to word: temperature (feels like), sky, wind, rain chance, and the day's high and low; "I couldn't find a place called X." for an unknown city;
       ToolError unavailable when the service is down.
"""
import json
import urllib.parse
import urllib.request

import config
from tools.registry import ToolError

FETCH = None
SKY = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
       65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def _get(url: str) -> dict:
    if FETCH:
        return FETCH(url)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)
    except Exception as ex:
        raise ToolError("unavailable", "I can't reach the weather service right now.", f"weather fetch failed: {type(ex).__name__}")


def _sky(code) -> str:
    return SKY.get(code, "mixed conditions")


def run(city: str = "", when: str = "now") -> str:
    city = city.strip() or getattr(config.get(), "weather_city", "")
    if not city:
        raise ToolError("bad_args", "Which city?", "no city given and no default")
    g = _get("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": city, "count": 1}))
    if not g.get("results"):
        return f"I couldn't find a place called {city}."
    p = g["results"][0]
    q = {"latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 7, "wind_speed_unit": "kmh",
         "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
         "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}
    w = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    place = ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x)
    c, d = w["current"], w["daily"]
    day = lambda i, label: (f"{label}: {_sky(d['weather_code'][i])}, high {round(d['temperature_2m_max'][i])}°C, low {round(d['temperature_2m_min'][i])}°C, "
                            f"{d['precipitation_probability_max'][i]}% chance of rain")
    when = (when or "now").lower()
    if when == "tomorrow":
        return f"{place}. " + day(1, "Tomorrow")
    if when == "week":
        names = ["Today", "Tomorrow"] + [d["time"][i] for i in range(2, 7)]
        return f"{place}. " + "; ".join(day(i, names[i]) for i in range(7))
    now = f"{place}. Now: {round(c['temperature_2m'])}°C, feels like {round(c['apparent_temperature'])}°C, {_sky(c['weather_code'])}, wind {round(c['wind_speed_10m'])} km/h, humidity {round(c['relative_humidity_2m'])}%."
    return now + " " + day(0, "Today")
