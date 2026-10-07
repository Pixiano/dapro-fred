"""Self-check for weather: fake Open-Meteo responses, no network."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config

HERE = Path(__file__).resolve().parent


def _selfcheck():
    spec = importlib.util.spec_from_file_location("weather_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    seen = []

    def fake(url):
        seen.append(url)
        if "geocoding" in url:
            return {"results": [{"name": "Testville", "admin1": "Westshire", "country": "Exampleland", "latitude": 10.5, "longitude": 20.25}]} if "Testville" in url else {}
        assert "latitude=10.5" in url and "longitude=20.25" in url
        return {"current": {"temperature_2m": 31.6, "apparent_temperature": 35.2, "weather_code": 2, "wind_speed_10m": 12.4, "relative_humidity_2m": 48},
                "daily": {"time": [f"2026-10-{8 + i:02d}" for i in range(7)], "weather_code": [2, 63, 0, 0, 3, 95, 1], "temperature_2m_max": [33, 29, 30, 31, 30, 28, 32],
                          "temperature_2m_min": [22, 21, 20, 21, 22, 20, 21], "precipitation_probability_max": [10, 80, 0, 5, 20, 90, 0]}}
    m.FETCH = fake
    r = m.run(city="Testville")
    assert r == ("Testville, Westshire, Exampleland. Now: 32°C, feels like 35°C, partly cloudy, wind 12 km/h, humidity 48%. "
                 "Today: partly cloudy, high 33°C, low 22°C, 10% chance of rain"), r
    assert "Testville" in seen[0] and "question" not in seen[0]
    t = m.run(city="Testville", when="tomorrow")
    assert t.endswith("Tomorrow: rain, high 29°C, low 21°C, 80% chance of rain") and "Now" not in t, t
    wk = m.run(city="Testville", when="week")
    assert wk.count(";") == 6 and "thunderstorm" in wk and "2026-10-10" in wk, wk
    assert m.run(city="Nowhere") == "I couldn't find a place called Nowhere."
    try:
        m.run()
        raise AssertionError
    except Exception as ex:
        assert ex.kind == "bad_args" and ex.speakable == "Which city?"
    config.get().weather_city = "Testville"                                # a default city is used when none is named
    assert m.run().startswith("Testville,")
    config.reload()

    def down(url):
        raise OSError("no net")
    m.FETCH = None
    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: down(a[0])
    try:
        m.run(city="Testville")
        raise AssertionError
    except Exception as ex:
        assert ex.kind == "unavailable"
    finally:
        urllib.request.urlopen = orig
    print("weather check ok")


if __name__ == "__main__":
    _selfcheck()
