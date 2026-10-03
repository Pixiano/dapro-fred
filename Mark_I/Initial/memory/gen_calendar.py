"""Builds calendar_india.json (public days for alerts.py). Developer tool, run when the file runs out of years:
    pip install holidays   (only needed to run this script, not by the memory system)
    python gen_calendar.py [first_year] [last_year]      (default 2026 2035; the holidays package covers 2001-2035)
Movable festivals come from the holidays package (Hindu/Islamic/Christian dates); Navratri's first day, Dhanteras and Bhai Dooj are derived
from Dussehra and Diwali (9 days before, 2 days before, 2 days after), so they can be a day off in some regions or years: the note says so.
Not covered (no reliable source): Raksha Bandhan, Ganesh Chaturthi, Onam and other tithi-based days. Add them by hand per year if wanted.
Nothing personal belongs in this file: personal dates live in the private memory database (alerts.py).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# (month, day, title, kind, important, note): days that never move
FIXED = [
    (1, 1, "New Year's Day", "festival", 1, ""),
    (1, 4, "World Braille Day", "fun_fact", 0, "Louis Braille was born on this day in 1809."),
    (1, 12, "National Youth Day (India)", "fun_fact", 0, "Swami Vivekananda's birthday."),
    (1, 14, "Makar Sankranti / Uttarayan (the kite festival)", "festival", 1, "Kite flying; Pongal in the south. Occasionally falls on the 15th."),
    (1, 26, "Republic Day (India)", "festival", 1, "The Constitution came into force in 1950."),
    (2, 11, "International Day of Women and Girls in Science", "fun_fact", 0, ""),
    (2, 28, "National Science Day (India)", "fun_fact", 0, "Marks C. V. Raman's announcement of the Raman effect in 1928."),
    (2, 29, "Leap Day", "fun_fact", 0, "Only in leap years: a year divisible by 4, except century years not divisible by 400."),
    (3, 8, "International Women's Day", "fun_fact", 0, ""),
    (3, 14, "Pi Day", "fun_fact", 0, "3.14; also Albert Einstein's birthday."),
    (3, 22, "World Water Day", "fun_fact", 0, ""),
    (4, 1, "April Fools' Day", "fun_fact", 0, ""),
    (4, 12, "International Day of Human Space Flight", "fun_fact", 0, "Yuri Gagarin became the first person in space in 1961."),
    (4, 14, "Dr. B. R. Ambedkar Jayanti", "festival", 0, ""),
    (4, 22, "Earth Day", "fun_fact", 0, ""),
    (5, 4, "Star Wars Day", "fun_fact", 0, "May the Fourth be with you."),
    (5, 11, "National Technology Day (India)", "fun_fact", 0, "Marks the Pokhran tests of 1998 and the first Indian-built aircraft test flight."),
    (6, 5, "World Environment Day", "fun_fact", 0, ""),
    (6, 21, "International Yoga Day", "fun_fact", 0, "Also the longest day of the year in the north."),
    (6, 23, "Alan Turing's birthday", "fun_fact", 0, "Born in 1912; the father of theoretical computer science."),
    (7, 11, "World Population Day", "fun_fact", 0, ""),
    (7, 20, "Moon landing anniversary", "fun_fact", 0, "Apollo 11 landed in 1969."),
    (8, 12, "International Youth Day", "fun_fact", 0, ""),
    (8, 15, "Independence Day (India)", "festival", 1, ""),
    (8, 23, "National Space Day (India)", "fun_fact", 0, "Chandrayaan-3 landed near the Moon's south pole in 2023."),
    (9, 5, "Teachers' Day (India)", "fun_fact", 0, "Dr. Sarvepalli Radhakrishnan's birthday."),
    (9, 15, "Engineers' Day (India)", "fun_fact", 0, "M. Visvesvaraya's birthday."),
    (9, 21, "International Day of Peace", "fun_fact", 0, ""),
    (10, 2, "Gandhi Jayanti", "festival", 0, ""),
    (10, 15, "World Students' Day", "fun_fact", 0, "A. P. J. Abdul Kalam's birthday."),
    (10, 31, "Halloween", "fun_fact", 0, ""),
    (11, 10, "World Science Day for Peace and Development", "fun_fact", 0, ""),
    (11, 14, "Children's Day (India)", "fun_fact", 0, "Jawaharlal Nehru's birthday."),
    (12, 10, "Human Rights Day", "fun_fact", 0, ""),
    (12, 22, "National Mathematics Day (India)", "fun_fact", 0, "Srinivasa Ramanujan was born in 1887."),
    (12, 25, "Christmas", "festival", 1, ""),
    (12, 31, "New Year's Eve", "festival", 0, ""),
]
# substring of the holidays package name -> (our title, important)
MOVABLE = {"Holi": ("Holi", 1), "Shivaratri": ("Maha Shivratri", 0), "Ram Navami": ("Ram Navami", 0), "Mahavir": ("Mahavir Jayanti", 0), "Buddha": ("Buddha Purnima", 0),
           "Janmashtami": ("Janmashtami", 0), "Dussehra": ("Dussehra (Vijayadashami)", 1), "Diwali": ("Diwali", 1), "Guru Nanak": ("Guru Nanak Jayanti", 0),
           "Id-ul-Fitr": ("Eid al-Fitr", 0), "Eid al-Fitr": ("Eid al-Fitr", 0), "Id-ul-Zuha": ("Eid al-Adha", 0), "Eid al-Adha": ("Eid al-Adha", 0), "Good Friday": ("Good Friday", 0)}


def build(first, last):
    import holidays
    movable = {}
    for y in range(first, last + 1):
        rows = []
        for d, name in sorted(holidays.India(years=y).items()):
            for key, (title, imp) in MOVABLE.items():
                if key in name:
                    est = "estimated" in name.lower() or name.endswith("(estimated)") or key.startswith(("Id", "Eid"))
                    rows.append([d.isoformat(), title, imp, "Moon-sighting dependent: can be a day off." if est else ""])
                    break
        dates = {r[1]: date.fromisoformat(r[0]) for r in rows}
        if "Dussehra (Vijayadashami)" in dates:
            rows.append([(dates["Dussehra (Vijayadashami)"] - timedelta(days=9)).isoformat(), "Navratri begins", 1, "Nine nights up to Dussehra; the first day can differ by a day by region."])
        if "Diwali" in dates:
            for off, title in ((-2, "Dhanteras"), (2, "Bhai Dooj")):
                rows.append([(dates["Diwali"] + timedelta(days=off)).isoformat(), title, 0, "Counted from Diwali; can differ by a day by region."])
        movable[str(y)] = sorted(rows)
    return {"generated_for": [first, last], "fixed": [list(f) for f in FIXED], "movable": movable}


if __name__ == "__main__":
    a, b = (int(x) for x in sys.argv[1:3]) if len(sys.argv) > 2 else (2026, 2035)
    Path(__file__).with_name("calendar_india.json").write_text(json.dumps(build(a, b), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("calendar_india.json written for", a, "to", b)
