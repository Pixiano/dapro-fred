"""30 invented edit cases for the note-editing tests (invented people, places and numbers only). Each: id, cat, name, text, instr, exp (expected file text, or None = must ask or refuse)."""

PROFILE = """---
type: person
name: Tavi Brennock
aliases: [Tav, T.B.]
created: 2031-01-04
updated: 2031-02-18
tags: [friend, climbing]
---
# Tavi Brennock

## Basics
- Lives in Quillham, Norland
- Works as a surveyor at Grayfen Mapping
- Phone: 555-0142
- Email: tavi@example.invalid
- Birthday: 12 June

## Family
- Partner: Orla Pendry
- Sister: Wenna Brennock (lives in Dunmarrow)
- Dog: Biscuit, a brown terrier

## Interests
- Bouldering at the Quillham Slab gym
- Collecting old trail maps
- Brewing small-batch cider

## History
- 2030-07: Moved to Quillham from Dunmarrow
- 2030-11: Started at Grayfen Mapping
- 2031-01: Ran the Norland half marathon

## Notes
- Prefers texts over calls
- Allergic to hazelnuts
- Brennock family reunion is every August in Dunmarrow
- Tavi's cider recipe uses Kessel apples only
"""

LOG = """---
type: notes-log
owner: Rilla Voss
---
# Notes Log

## 2031-01
- 2031-01-03: Dentist appointment moved to the 9th
- 2031-01-10: Bought new running shoes (Fenwick Trail 3)
- 2031-01-17: Call with Hobb about the garden shed
- 2031-01-28: Renewed library card

## 2031-02
- 2031-02-02: Started the sourdough starter, named it Gus
- 2031-02-09: Dinner at Marrow & Fig with Pell
- 2031-02-14: Quiet day, finished the Ashgrove novel
- 2031-02-21: Car service booked for March 4

## 2031-03
- 2031-03-01: Spring cleaning of the loft
- 2031-03-04: Car service done, tyres fine
- 2031-03-11: Pell's birthday gift ordered

## Ideas
- Try a pottery class
- Learn the basics of tide tables
- Build a bookshelf for the hallway

## Open questions
- Which broadband plan is cheaper after the promo ends?
- Is the loft insulation thick enough?
"""

CONTACTS = """---
type: contacts
updated: 2031-02-20
---
# Contacts

## Friends
- Pell Ardwick - 555-0171 - pell@example.invalid
- Hobb Tarrant - 555-0188
- Sam Okoro - 555-0123 - lives in Dunmarrow
- Sam Whitlow - 555-0155 - works at the bakery

## Family
- Mum - 555-0100
- Brother Dax - 555-0102
- Aunt Fenna - 555-0109 - Quillham

## Services
- Dr. Lindqvist (GP) - 555-0200
- Harbor Plumbing - 555-0231 - ask for Keld
- Grayfen Electric - 555-0240

## Work
- Isolde Marsh - manager - isolde.marsh@example.invalid
- Corvin Dale - IT help - ext 4412
- Reception - ext 4400

## Duplicates to clean
- Zora Pryce - 555-0301
- Zora Pryce - zora@example.invalid - moved to Dunmarrow
"""

PREFS = """---
type: preferences
owner: Rilla Voss
---
# Preferences

## Food
- Vegetarian, eats fish on occasion
- Dislikes coriander
- Favourite cuisine: Ashgrove-style stews
- Coffee: flat white, no sugar
- Drinks tea in the evening only

## Communication
- Prefers texts over calls
- No messages before 8am
- Calls are fine on Sundays

## Home
- Thermostat at 19 degrees in winter
- Lights dim after 9pm
- Wakes at 6:30 on weekdays
- Sleeps with the window open

## Music
- Likes folk and mellow electronic
- Favourite artist: The Lantern Hours
- Dislikes loud ads

## Shopping
- Prefers local shops when possible
- Buys books second-hand
- Allergic to wool
"""

PROJECT = """---
type: project
name: Greenhouse Build
status: active
started: 2031-01-15
---
# Greenhouse Build

## Summary
A 3m x 4m greenhouse behind the shed, built mostly from reclaimed window frames.

## Facts
- Location: back garden, south-facing
- Budget: 800 credits
- Frame material: reclaimed timber, treated
- Glazing: 14 old window panes
- Foundation: compacted gravel base

## Tasks
- [x] Measure the site
- [x] Collect window frames
- [ ] Level the ground
- [ ] Lay the gravel base
- [ ] Build the timber frame
- [ ] Fit the glazing
- [ ] Install shelves

## Contacts
- Hobb Tarrant - timber supplier
- Keld at Harbor Plumbing - water butt connection

## Log
- 2031-01-15: Project started
- 2031-01-22: 9 window frames collected
- 2031-02-05: Site measured, 3m x 4m confirmed
- 2031-02-19: Hobb quoted 120 credits for timber
"""

def rep(s, a, b, n=1):
    assert s.count(a) == n, (a, s.count(a))
    return s.replace(a, b)

C = []
def add(id, cat, name, text, instr, exp):
    C.append(dict(id=id, cat=cat, name=name, text=text, instr=instr, exp=exp))

# ---- plain text
T1 = """Team sync notes

Attendees: Mira, Joss, Penn, Alba
Date: week of 4 March

Agenda
1. Launch checklist
2. Budget review
3. The review meeting is on Thursday at 10:00 in Room 2

Action items
- Mira sends the checklist
- Joss books the room
- Penn drafts the budget summary
- Alba reviews the vendor contract

Next sync: the following Monday.
"""
add("t1", "text", "change weekday", T1, "Move the review meeting from Thursday to Friday.", rep(T1, "on Thursday at", "on Friday at"))
T2 = """Shopping list

- oat milk
- eggs
- sourdough loaf
- tomatoes
- basil
- lemons
- olive oil
- dark chocolate
"""
add("t2", "text", "remove line", T2, "Remove eggs from the list.", rep(T2, "- eggs\n", ""))
T3 = """Dear Mr. Harlow,

Thank you for the quick repair of the fence last week. The new panels look
excellent and the gate now closes properly.

Please send the invoice to the address on file. We will pay within ten days.

Kind regards,
Ines Calder
"""
add("t3", "text", "append P.S.", T3, "Add a line at the very end of the letter: P.S. The gate latch is still a bit stiff.", T3 + "P.S. The gate latch is still a bit stiff.\n")
T4 = """Flatbread

Ingredients
- 300g flour
- 200ml warm water
- 1 tsp salt
- 2 tbsp olive oil

Method
1. Mix everything into a dough.
2. Rest the dough for 30 minutes.
3. Roll into thin rounds.
4. Cook in a hot dry pan for 2 minutes per side.
5. Brush with oil and serve warm.
"""
add("t4", "text", "change number", T4, "Resting time should be 45 minutes, not 30.", rep(T4, "30 minutes", "45 minutes"))
T5 = """Trip Plan: Coast Weekend

Friday
- Train at 17:40
- Check in at the harbour guesthouse

Saturday
- Cliff walk, about 12 km
- Lunch at the fish shack
- Evening: board games

Sunday
- Late breakfast
- Train home at 15:10
"""
add("t5", "text", "rename title", T5, "Rename the title to 'Trip Plan: Harbour Weekend'.", rep(T5, "Coast Weekend", "Harbour Weekend"))

# ---- code
K1 = '''import math


def circle_area(r):
    """Area of a circle with radius r."""
    return math.pi * r * r


def circle_perimeter(r):
    return 2 * math.pi * r


def describe(r):
    area = circle_area(r)
    per = circle_perimeter(r)
    return f"r={r} area={area:.2f} perimeter={per:.2f}"


if __name__ == "__main__":
    print(describe(3))
'''
add("k1", "code", "rename function", K1, "Rename circle_perimeter to circle_circumference everywhere.", rep(rep(K1, "circle_perimeter", "circle_circumference", 2), "x", "x", K1.count("x")) if False else K1.replace("circle_perimeter", "circle_circumference"))
K2 = '''MAX_RETRIES = 3
TIMEOUT_S = 10
BACKOFF = 1.5


def fetch(client, url):
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            return client.get(url, timeout=TIMEOUT_S)
        except TimeoutError:
            if attempt == MAX_RETRIES - 1:
                raise
            delay *= BACKOFF
    return None
'''
add("k2", "code", "change constant", K2, "Set MAX_RETRIES to 5 and TIMEOUT_S to 20.", rep(rep(K2, "MAX_RETRIES = 3", "MAX_RETRIES = 5"), "TIMEOUT_S = 10", "TIMEOUT_S = 20"))
K3 = '''function total(items) {
  let sum = 0;
  for (let i = 0; i <= items.length; i++) {
    sum += items[i].price;
  }
  return sum;
}

function names(items) {
  return items.map((it) => it.name);
}

module.exports = { total, names };
'''
add("k3", "code", "fix off-by-one", K3, "Fix the off-by-one bug in the loop in total().", rep(K3, "i <= items.length", "i < items.length"))
K4 = '''def load(path):
    with open(path) as f:
        data = f.read()
    print("DEBUG raw data:", data)
    lines = data.splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def count(path):
    return len(load(path))
'''
add("k4", "code", "remove debug print", K4, "Remove the debug print statement.", rep(K4, '    print("DEBUG raw data:", data)\n', ""))
K5 = '''class Counter:
    def __init__(self):
        self.n = 0

    def inc(self):
        self.n += 1

    def reset(self):
        self.n = 0


c = Counter()
c.inc()
c.inc()
print(c.n)
'''
add("k5", "code", "add method", K5, "Add a method dec() to Counter that decrements n by 1, placed after inc().", rep(K5, "        self.n += 1\n", "        self.n += 1\n\n    def dec(self):\n        self.n -= 1\n"))

# ---- config
G1 = '''{
  "name": "demo-service",
  "version": "1.4.2",
  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "tls": false
  },
  "logging": {
    "level": "info",
    "file": "service.log"
  },
  "features": ["search", "export"]
}
'''
add("g1", "config", "json port", G1, "Change the server port to 9090.", rep(G1, "8080", "9090"))
G2 = '''service: demo
replicas: 2
env:
  - name: MODE
    value: prod
  - name: REGION
    value: north-1
ports:
  - 80
  - 443
resources:
  cpu: 500m
  memory: 256Mi
'''
add("g2", "config", "yaml add list item", G2, "Add port 8443 to the ports list.", rep(G2, "  - 443\n", "  - 443\n  - 8443\n"))
G3 = '''[general]
name = demo
debug = false

[database]
host = localhost
port = 5432
user = app
pool_size = 5

[cache]
enabled = true
ttl = 300
'''
add("g3", "config", "ini value", G3, "Set the database pool_size to 10 and turn debug on.", rep(rep(G3, "pool_size = 5", "pool_size = 10"), "debug = false", "debug = true"))
G4 = '''[package]
name = "demo-tool"
version = "0.3.1"
edition = "2021"

[dependencies]
serde = "1.0"
clap = "4.2"

[profile.release]
opt-level = 3
lto = true
'''
add("g4", "config", "toml version bump", G4, "Bump the package version to 0.4.0.", rep(G4, '0.3.1', '0.4.0'))
G5 = '''{
  "theme": "dark",
  "fontSize": 14,
  "autosave": true,
  "recentFiles": ["a.txt", "b.txt"],
  "legacyMode": true,
  "language": "en"
}
'''
add("g5", "config", "json remove key", G5, "Remove the legacyMode setting.", rep(G5, '  "legacyMode": true,\n', ""))

# ---- ambiguous / impossible (non-memory)
A1 = """Phone list

Alex Rowan: 555-0111
Alex Mercer: 555-0122
Bryn Hale: 555-0133
Cora Finch: 555-0144
"""
add("a1", "ambig", "two Alex", A1, "Change Alex's phone number to 555-0999.", None)
A2 = """Room bookings

Mon: Room 2, 09:00
Tue: Room 3, 14:00
Wed: Room 1, 11:00
Thu: free
Fri: Room 3, 10:00
"""
add("a2", "ambig", "target missing", A2, "Change the Room 4 booking on Monday afternoon to Room 5.", None)
A3 = """Project Overview

This document describes the lighthouse restoration schedule.
Phase one covers the lamp room.
Phase two covers the stairs.
"""
add("a3", "ambig", "contradictory", A3, "Make the title all uppercase, but keep the title exactly as it currently is.", None)
A4 = """Account settings

username: pelham
display_name: Pelham R.
timezone: Europe/Norland
notifications: on
"""
add("a4", "ambig", "needs missing info", A4, "Set the account email to the user's work email address.", None)
A5 = '''def area(w, h):
    return w * h


def perimeter(w, h):
    return 2 * (w + h)
'''
add("a5", "ambig", "missing function", A5, "Rename the function volume to capacity.", None)

# ---- MEMORY edits
add("m1", "memory", "update city", PROFILE, "Tavi moved: now lives in Harrowgate, Norland instead of Quillham (only the current residence; history stays as is).", rep(PROFILE, "- Lives in Quillham, Norland", "- Lives in Harrowgate, Norland"))
add("m2", "memory", "add dated bullet in section", LOG, "Add a new entry to the 2031-02 section: 2031-02-26: Replaced the bike tyre.", rep(LOG, "- 2031-02-21: Car service booked for March 4\n", "- 2031-02-21: Car service booked for March 4\n- 2031-02-26: Replaced the bike tyre\n"))
add("m3", "memory", "mark task done", PROJECT, "Mark 'Level the ground' as done and add to the Log: 2031-02-24: Ground levelled.", rep(rep(PROJECT, "- [ ] Level the ground", "- [x] Level the ground"), "- 2031-02-19: Hobb quoted 120 credits for timber\n", "- 2031-02-19: Hobb quoted 120 credits for timber\n- 2031-02-24: Ground levelled\n"))
m4_exp = rep(rep(CONTACTS, "- Zora Pryce - 555-0301\n- Zora Pryce - zora@example.invalid - moved to Dunmarrow\n", "- Zora Pryce - 555-0301 - zora@example.invalid - moved to Dunmarrow\n"), "x", "x", 0) if False else CONTACTS.replace("- Zora Pryce - 555-0301\n- Zora Pryce - zora@example.invalid - moved to Dunmarrow\n", "- Zora Pryce - 555-0301 - zora@example.invalid - moved to Dunmarrow\n")
add("m4", "memory", "merge duplicates", CONTACTS, "Merge the two Zora Pryce entries into one line, keeping the phone, the email and the note about Dunmarrow.", m4_exp)
add("m5", "memory", "remove wrong fact", PREFS, "The 'Allergic to wool' line is wrong, remove it. Nothing else changes.", rep(PREFS, "- Allergic to wool\n", ""))
add("m6", "memory", "fix spelling everywhere", PROFILE, "The surname is spelled wrong: it should be 'Brennok' (not 'Brennock'). Fix it everywhere in the file, including the title and front-matter.", PROFILE.replace("Brennock", "Brennok"))
add("m7", "memory", "append to list", PREFS, "Add 'Likes jazz on Friday evenings' to the Music list. Don't touch other lines.", rep(PREFS, "- Dislikes loud ads\n", "- Dislikes loud ads\n- Likes jazz on Friday evenings\n"))
add("m8", "memory", "rename heading", PROJECT, "Rename the heading '## Facts' to '## Key facts'.", rep(PROJECT, "## Facts\n", "## Key facts\n"))
add("m9", "memory", "AMBIG two Sams", CONTACTS, "Update Sam's phone number to 555-0777.", None)
add("m10", "memory", "AMBIG fact missing", PROFILE, "Update Tavi's passport number to the new one, X9-4410.", None)
