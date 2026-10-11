"""Builds calendar_india.json (public days for alerts.py). Developer tool, run when the file runs out of years:
    pip install holidays   (only needed to run this script, not by the memory system)
    python gen_calendar.py [first_year] [last_year]      (default 2026 2035; the holidays package covers 2001-2035)
Movable festivals come from the holidays package (Hindu/Islamic/Christian dates), plus WEB_CHECKED below: dates for festivals the package lacks
(Navratri's first day, Raksha Bandhan, Ganesh Chaturthi, and gaps in the package), taken from web searches on 2026-10-05 only where several sources
agreed. Dhanteras and Bhai Dooj are left out (rules of thumb off by a day in some years, and the searches contradicted each other); so are Onam and
other tithi-based days. Years without a checked date simply show nothing for that festival.
Every January alerts.run_alerts_pending recomputes the movable dates of the new year through a socket (alerts.recompute_year), so this file
only has to be right for the near term; regenerate it when it runs out of years.
Nothing personal belongs in this file: personal dates live in the private memory database (alerts.py).
"""
import json
import sys
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


# (iso date, title, important, note): web-checked 2026-10-05; Navratri/Rakhi/Ganesh agreed across sources for 2026-2030
WEB_CHECKED = (
    [[d, "Navratri begins", 1, "Nine nights up to Dussehra; web-checked, can differ by a day by region."] for d in
     ("2026-10-11", "2027-09-30", "2028-09-19", "2029-10-08", "2030-09-28")]
    + [[d, "Raksha Bandhan", 0, "Web-checked; can differ by a day by region."] for d in ("2026-08-28", "2027-08-17", "2028-08-06", "2029-07-26", "2030-07-15")]
    + [[d, "Ganesh Chaturthi", 0, "Web-checked; ten days of celebration follow."] for d in ("2026-09-14", "2027-09-03", "2028-08-23", "2029-08-12", "2030-08-01")]
    + [["2026-02-15", "Maha Shivratri", 0, "Web-checked (missing from the package); some sources discussed the 16th."],
       ["2029-04-22", "Ram Navami", 0, "One web source; verify when the year comes."]])


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
        have = {(r[0][:4], r[1]) for r in rows}
        rows += [r for r in WEB_CHECKED if r[0][:4] == str(y) and (str(y), r[1]) not in have]
        movable[str(y)] = sorted(rows)
    return {"generated_for": [first, last], "fixed": [list(f) for f in FIXED], "movable": movable}


if __name__ == "__main__":
    a, b = (int(x) for x in sys.argv[1:3]) if len(sys.argv) > 2 else (2026, 2035)
    Path(__file__).with_name("calendar_india.json").write_text(json.dumps(build(a, b), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("calendar_india.json written for", a, "to", b)
