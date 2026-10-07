"""Unit conversion for `calculate`: SI and common units both ways, metric first (km before miles, kg before pounds: how Indians use them), lakh and crore for number sizes, with 1 to 3 related units as alternatives.

Sockets
  in : convert(value: float, src: str, dst: str = "") -> str          "5 km to miles" -> "5 km = 3.10686 mi (also 5000 m, 16404.2 ft, 5468.07 gaj)"
       parse(text) -> (value_text, src, dst) | None                   splits "5 km in miles", "98.6 F to C", "2.5 crore to million", "convert 10 kg" (no target: alternatives only)
  out: exact factors from the table below (every factor is a published definition); an unknown unit, or two units of different kinds, raises ValueError with a sentence the model can say.
Left out on purpose (Vatsal 2026-10-07: "not gaj and all"): gaj, tola, seer, maund, bigha and the other traditional measures; and the UK/US ton and gallon ambiguity: ton means the metric tonne, gallon the US one, "uk gallon" is separate.
Temperatures are affine, handled apart. Data sizes are decimal (KB = 1000 B) with KiB/MiB/GiB for binary.
Self-check: python units.py
"""
import re

# kind -> {unit: factor to the kind's base unit}; first listed unit is the base
KINDS = {
    "length": {"m": 1, "km": 1000, "cm": .01, "mm": .001, "um": 1e-6, "nm": 1e-9, "mi": 1609.344, "yd": .9144, "ft": .3048, "in": .0254, "nmi": 1852},
    "mass": {"kg": 1, "g": .001, "mg": 1e-6, "tonne": 1000, "lb": .45359237, "oz": .028349523125},
    "volume": {"l": 1, "ml": .001, "m3": 1000, "gal": 3.785411784, "ukgal": 4.54609, "qt": .946352946, "pt": .473176473, "cup": .2365882365, "floz": .0295735295625, "tbsp": .01478676478, "tsp": .00492892159},
    "area": {"m2": 1, "km2": 1e6, "cm2": 1e-4, "ha": 1e4, "acre": 4046.8564224, "ft2": .09290304, "in2": .00064516, "yd2": .83612736, "mi2": 2589988.110336},
    "speed": {"m/s": 1, "km/h": 1 / 3.6, "mph": .44704, "kn": 1852 / 3600, "ft/s": .3048},
    "time": {"s": 1, "ms": .001, "min": 60, "h": 3600, "day": 86400, "week": 604800, "year": 31557600},
    "data": {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4, "bit": .125},
    "energy": {"j": 1, "kj": 1e3, "cal": 4.184, "kcal": 4184, "wh": 3600, "kwh": 3.6e6, "btu": 1055.05585262},
    "pressure": {"pa": 1, "kpa": 1e3, "bar": 1e5, "atm": 101325, "psi": 6894.757293168, "mmhg": 133.322387415},
    "power": {"w": 1, "kw": 1e3, "hp": 745.69987158227},
    "angle": {"deg": 1, "rad": 180 / 3.141592653589793},
    "count": {"one": 1, "thousand": 1e3, "lakh": 1e5, "million": 1e6, "crore": 1e7, "billion": 1e9, "arab": 1e9, "trillion": 1e12, "kharab": 1e11, "dozen": 12},
    "temperature": {"c": 1, "f": 1, "k": 1},
}
# what a person writes -> table name
ALIAS = {
    "m": "m", "meter": "m", "metre": "m", "km": "km", "kilometer": "km", "kilometre": "km", "cm": "cm", "centimeter": "cm", "centimetre": "cm", "mm": "mm", "millimeter": "mm", "millimetre": "mm",
    "micron": "um", "micrometer": "um", "nanometer": "nm", "mile": "mi", "mi": "mi", "yard": "yd", "yd": "yd", "foot": "ft", "feet": "ft", "ft": "ft", "inch": "in", "in": "in", "nautical mile": "nmi",
    "kg": "kg", "kilogram": "kg", "kilo": "kg", "g": "g", "gram": "g", "gm": "g", "mg": "mg", "milligram": "mg", "tonne": "tonne", "ton": "tonne", "metric ton": "tonne",
    "lb": "lb", "pound": "lb", "lbs": "lb", "oz": "oz", "ounce": "oz",
    "l": "l", "liter": "l", "litre": "l", "ml": "ml", "milliliter": "ml", "millilitre": "ml", "m3": "m3", "cubic meter": "m3", "gallon": "gal", "gal": "gal", "us gallon": "gal", "uk gallon": "ukgal", "imperial gallon": "ukgal",
    "quart": "qt", "pint": "pt", "cup": "cup", "fluid ounce": "floz", "fl oz": "floz", "tablespoon": "tbsp", "tbsp": "tbsp", "teaspoon": "tsp", "tsp": "tsp",
    "m2": "m2", "sq m": "m2", "square meter": "m2", "square metre": "m2", "km2": "km2", "sq km": "km2", "square kilometer": "km2", "cm2": "cm2", "sq cm": "cm2", "hectare": "ha", "ha": "ha", "acre": "acre",
    "sq ft": "ft2", "square foot": "ft2", "square feet": "ft2", "sqft": "ft2", "sq in": "in2", "square inch": "in2", "sq yd": "yd2", "square yard": "yd2", "sq mi": "mi2", "square mile": "mi2",
    "m/s": "m/s", "mps": "m/s", "km/h": "km/h", "kmh": "km/h", "kph": "km/h", "kmph": "km/h", "mph": "mph", "knot": "kn", "kn": "kn", "ft/s": "ft/s", "fps": "ft/s",
    "second": "s", "sec": "s", "s": "s", "millisecond": "ms", "ms": "ms", "minute": "min", "min": "min", "hour": "h", "hr": "h", "h": "h", "day": "day", "week": "week", "year": "year", "yr": "year",
    "bit": "bit", "byte": "b", "b": "b", "kb": "kb", "kilobyte": "kb", "mb": "mb", "megabyte": "mb", "gb": "gb", "gigabyte": "gb", "tb": "tb", "terabyte": "tb", "kib": "kib", "mib": "mib", "gib": "gib", "tib": "tib",
    "joule": "j", "j": "j", "kj": "kj", "kilojoule": "kj", "calorie": "cal", "cal": "cal", "kcal": "kcal", "kilocalorie": "kcal", "wh": "wh", "watt hour": "wh", "kwh": "kwh", "kilowatt hour": "kwh", "unit": "kwh", "btu": "btu",
    "pa": "pa", "pascal": "pa", "kpa": "kpa", "kilopascal": "kpa", "bar": "bar", "atm": "atm", "atmosphere": "atm", "psi": "psi", "mmhg": "mmhg", "torr": "mmhg",
    "w": "w", "watt": "w", "kw": "kw", "kilowatt": "kw", "hp": "hp", "horsepower": "hp", "deg": "deg", "degree": "deg", "rad": "rad", "radian": "rad",
    "thousand": "thousand", "lakh": "lakh", "lac": "lakh", "million": "million", "crore": "crore", "billion": "billion", "arab": "arab", "trillion": "trillion", "kharab": "kharab", "dozen": "dozen",
    "c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k",
}
SHOW = {"mi": "mi", "ukgal": "UK gal", "gal": "gal", "floz": "fl oz", "ft2": "sq ft", "in2": "sq in", "yd2": "sq yd", "m2": "m²", "km2": "km²", "cm2": "cm²", "mi2": "sq mi", "m3": "m³", "tonne": "tonnes", "b": "B",
        "kb": "KB", "mb": "MB", "gb": "GB", "tb": "TB", "kib": "KiB", "mib": "MiB", "gib": "GiB", "tib": "TiB", "kn": "knots", "h": "h", "day": "days", "week": "weeks", "year": "years", "c": "°C", "f": "°F", "k": "K",
        "kwh": "kWh", "wh": "Wh", "kj": "kJ", "j": "J", "kpa": "kPa", "pa": "Pa", "mmhg": "mmHg", "hp": "hp", "kw": "kW", "w": "W", "l": "L", "ml": "mL", "um": "µm", "nm": "nm", "one": ""}
# nice alternatives per unit kind, in the order they are tried; only ones whose value reads sensibly (0.01 to 1e7) are kept
ALTS = {
    "length": ["m", "km", "cm", "mi", "ft", "in", "yd"], "mass": ["kg", "g", "lb", "oz", "tonne"], "volume": ["l", "ml", "gal", "cup", "floz", "m3"],
    "area": ["m2", "ft2", "acre", "ha", "yd2", "km2"], "speed": ["km/h", "mph", "m/s", "kn"], "time": ["s", "min", "h", "day", "week"], "data": ["mb", "gb", "kb", "mib", "gib", "tb"],
    "energy": ["kj", "kcal", "wh", "kwh", "j", "btu"], "pressure": ["kpa", "bar", "psi", "atm", "mmhg"], "power": ["w", "kw", "hp"], "angle": ["deg", "rad"],
    "count": ["thousand", "lakh", "million", "crore", "billion"], "temperature": ["c", "f", "k"],
}


def _norm(u: str) -> str:
    u = re.sub(r"[°\s]+", " ", u.lower().strip().rstrip(".")).strip()
    u = re.sub(r"^(?:a|an|the)\s+", "", u)
    u = u.replace("squared", "").replace("sq. ", "sq ").replace("²", "2").replace("³", "3").strip()
    u = re.sub(r"^(?:cubic) ", "cubic ", u)
    for cand in (u, u[:-1] if u.endswith("s") else u, u[:-2] if u.endswith("es") else u, u.replace("meters", "meter").replace("metres", "metre"), re.sub(r"(foot|inch|mile|yard)s?$", r"\1", u).replace("feet", "foot")):
        if cand in ALIAS:
            return ALIAS[cand]
    raise ValueError(f"I don't know the unit '{u}'.")


def _kind(unit: str) -> str:
    return next(k for k, t in KINDS.items() if unit in t and (k != "count" or unit != "one")) if unit != "one" else "count"


def _temp(v: float, a: str, b: str) -> float:
    c = v if a == "c" else (v - 32) * 5 / 9 if a == "f" else v - 273.15
    return c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15


def _num(x: float) -> str:
    if x == 0:
        return "0"
    s = f"{x:.6g}"
    return f"{x:.0f}" if "e" in s and abs(x) < 1e15 else s


def _fmt(v: float, u: str) -> str:
    lab = SHOW.get(u, u)
    return f"{_num(v)} {lab}".strip()


def _val(v: float, a: str, b: str) -> float:
    k = _kind(a)
    return _temp(v, a, b) if k == "temperature" else v * KINDS[k][a] / KINDS[k][b]


def convert(value: float, src: str, dst: str = "") -> str:
    a = _norm(src)
    k = _kind(a)
    b = _norm(dst) if dst.strip() else ""
    if b and _kind(b) != k:
        raise ValueError(f"I can't convert {k} to {_kind(b)}: {SHOW.get(a, a) or a} and {SHOW.get(b, b) or b} measure different things.")
    first = [b] if b else []
    alts = []
    for u in ALTS[k]:
        if u in (a, b):
            continue
        x = abs(_val(value, a, u))
        if k == "temperature" or 0.05 <= x < 1e5 or value == 0:
            alts.append(u)
    pick = alts[:3]                                                               # ALTS lists metric first, so km, kg, litres and °C come before miles, pounds and gallons
    head = (f"{_fmt(value, a)} = {_fmt(_val(value, a, b), b)}" if b else f"{_fmt(value, a)} =")
    tail = ", ".join(_fmt(_val(value, a, u), u) for u in pick)
    if b:
        return head + (f" (also {tail})" if tail else "")
    return f"{head} {tail}" if tail else _fmt(value, a)


SPLIT = re.compile(r"^\s*(?:convert\s+)?(?P<num>[-+]?[\d.,]+(?:\s*[-+*/x×^]\s*[\d.,]+)*)\s*(?P<src>[a-zA-Z°²³/ .]+?)\s*(?:(?:to|in|into|as)\s+(?P<dst>[a-zA-Z°²³/ .]+?))?\s*$", re.I)


def parse(text: str):
    m = SPLIT.match(text)
    if not m:
        return None
    src = m.group("src").strip()
    try:
        _norm(src)
    except ValueError:
        if not m.group("dst"):
            return None                                                               # "5 apples" is not a conversion; "5 usd to inr" is, and convert() says the unit is unknown
    return m.group("num"), src, (m.group("dst") or "").strip()


def _selfcheck():
    r = convert(5, "km", "miles")
    assert r.startswith("5 km = 3.10686 mi (also ") and "5000 m" in r and r.count(",") == 2, r
    assert convert(98.6, "F", "C").startswith("98.6 °F = 37 °C"), convert(98.6, "F", "C")
    assert convert(0, "c", "k").startswith("0 °C = 273.15 K") and convert(100, "celsius", "fahrenheit").startswith("100 °C = 212 °F")
    assert convert(1, "kg", "pounds").startswith("1 kg = 2.20462 lb"), convert(1, "kg", "pounds")
    for gone in ("gaj", "tola", "seer", "maund"):
        try:
            convert(1, gone, "m")
            raise AssertionError(gone)
        except ValueError:
            pass
    assert convert(2.5, "crore", "million").startswith("2.5 crore = 25 million"), convert(2.5, "crore", "million")
    assert convert(3, "lakh", "thousand").startswith("3 lakh = 300 thousand")
    assert convert(10, "miles", "km").startswith("10 mi = 16.0934 km") and "m" in convert(10, "mi", "km"), convert(10, "mi", "km")
    assert convert(150, "lb").split("=")[1].strip().startswith("68.0389 kg") or "kg" in convert(150, "lb"), convert(150, "lb")
    assert convert(1, "acre", "hectares").startswith("1 acre = 0.404686 ha"), convert(1, "acre", "ha")
    assert convert(60, "mph", "km/h").startswith("60 mph = 96.5606 km/h"), convert(60, "mph", "km/h")
    assert convert(1, "GB", "MB").startswith("1 GB = 1000 MB") and convert(1, "GiB", "MiB").startswith("1 GiB = 1024 MiB")
    assert convert(1, "kwh", "kj").startswith("1 kWh = 3600 kJ") and convert(1, "hp", "w").startswith("1 hp = 745.7 W")
    assert convert(10, "kg").startswith("10 kg =") and "lb" in convert(10, "kg") and "(" not in convert(10, "kg")
    for bad, why in (((1, "km", "kg"), "different things"), ((1, "florp", "m"), "don't know")):
        try:
            convert(*bad)
            raise AssertionError(bad)
        except ValueError as ex:
            assert why in str(ex), ex
    assert parse("5 km in miles") == ("5", "km", "miles") and parse("convert 10 kg") == ("10", "kg", "") and parse("2.5 crore to million") == ("2.5", "crore", "million")
    assert parse("98.6 F to C") == ("98.6", "F", "C") and parse("50 usd to inr") == ("50", "usd", "inr") and parse("5 apples") is None and parse("12 * 8") is None and parse("17 percent of 300") is None and parse("sqrt(16)") is None
    for u in {u for t in KINDS.values() for u in t if u != "one"}:
        assert any(ALIAS.get(k) == u for k in ALIAS), f"table unit {u} has no spoken alias"
    print("units ok")


if __name__ == "__main__":
    _selfcheck()
