"""Deterministic generator of INVENTED memory-recall data (no real/personal content).
300 facts + 1500 distractors + 300 paraphrased questions (29 near-duplicate pairs inside).
Writes data/memory_eval.json."""
import collections
import json
import random
from pathlib import Path

R = random.Random(1234)
NAMES = ("Aarav Ananya Kabir Meera Rohan Ishita Vikram Tanvi Sameer Nisha Arjun Pooja Dev Kavya Rahul Sneha Yash Tara Neel Diya "
         "Karan Mitali Omkar Rhea Harsh Zoya Aditya Bhavna Chirag Divya Lakshay Preeti Jatin Anika Farhan Simran Tushar Ojas "
         "Radhika Manav Zubin Esha Parth Naina Ritvik Sakshi Devansh Palak Ishaan Vedika Rudra Amaya Kunal Trisha Nakul Alisha "
         "Raghav Sanya Mohit Gauri").split()
NA, NB = NAMES[:30], NAMES[30:60]
MONTHS = "January February March April May June July August September October November December".split()
DAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()
ITEMS = ["steel kettle", "laptop stand", "yoga mat", "desk lamp", "backpack", "water bottle", "trekking shoes", "air fryer", "hair dryer",
         "mechanical keyboard", "sling bag", "rain jacket", "table fan", "mixer grinder", "bluetooth speaker", "study chair", "wall clock",
         "iron box", "pressure cooker", "electric toothbrush", "cycling helmet", "bedside table", "umbrella", "wireless mouse", "tripod",
         "curtain set", "ceiling light", "cast iron pan", "thermos flask", "gym gloves", "reading lamp", "shoe rack", "power bank",
         "door mat", "ukulele", "paint set", "badminton racket", "pillow set", "wok", "scooter cover"]
IA, IB = ITEMS[0::2], ITEMS[1::2]
PLACES = ["Goa", "Manali", "Udaipur", "Coorg", "Rishikesh", "Hampi", "Pondicherry", "Darjeeling", "Munnar", "Jaipur", "Kochi", "Shillong",
          "Gokarna", "Leh", "Varanasi", "Ooty", "Mysuru", "Alleppey", "Auli", "Kasol", "Lonavala", "Pushkar", "Mahabaleshwar", "Spiti",
          "Tawang", "Kutch", "Khajuraho", "Bhimtal", "Orchha", "Wayanad", "Hubli", "Nainital", "Kanatal", "Chikmagalur", "Dalhousie",
          "Mount Abu", "Kodaikanal", "Pachmarhi", "Mussoorie", "Almora"]
PA, PB = PLACES[0::2], PLACES[1::2]
FOODS = ["peanuts", "mushrooms", "shrimp", "cauliflower", "brinjal", "coriander", "pineapple", "walnuts", "okra", "cheese", "olives",
         "bitter gourd", "soy sauce", "mangoes", "kiwi", "sesame", "pumpkin", "raw onion", "avocado", "oysters"]
TOPICS = ["budget review", "website redesign", "hiring plan", "vendor contract", "quarterly report", "app launch", "training schedule",
          "office move", "client demo", "logo options", "data migration", "event catering", "travel policy", "security audit", "pricing sheet"]
SUBJ = ["chemistry", "statistics", "microeconomics", "thermodynamics", "linguistics", "organic synthesis", "marketing", "discrete maths",
        "geography", "algorithms", "psychology", "optics", "accounting", "robotics", "sociology", "genetics", "ethics", "fluid mechanics",
        "philosophy", "astronomy", "macroeconomics", "databases", "calculus", "history of art", "networks", "pharmacology", "ecology",
        "graphics", "compilers", "statics"]
SA, SB = SUBJ[0::2], SUBJ[1::2]
DISHES = ["paneer tikka", "mushroom risotto", "dal makhani", "lemon rice", "aloo paratha", "chole", "pav bhaji", "banana bread",
          "veg biryani", "upma", "masala dosa", "kadhi", "fried rice", "poha", "rajma", "khichdi", "pasta arrabbiata", "gulab jamun",
          "sambar", "vada pav", "egg curry", "lasagna", "palak paneer", "tomato soup", "idli", "bhindi masala", "kheer", "pancakes",
          "hummus", "pulao"]
DA_, DB_ = DISHES[0::2], DISHES[1::2]
SUPPS = ["vitamin D", "magnesium", "iron", "omega 3", "zinc", "vitamin B12", "calcium", "probiotic", "vitamin C", "folic acid", "biotin",
         "potassium", "collagen", "melatonin", "ashwagandha", "multivitamin", "vitamin K", "selenium", "coq10", "lysine", "taurine",
         "creatine", "glutamine", "chromium"]
SPA, SPB = SUPPS[0::2], SUPPS[1::2]
VEH = ["Activa", "Royal Enfield", "i20", "Swift", "Splendor", "Jupiter", "Bullet", "Creta", "Nexon", "Pulsar", "Baleno", "Dio", "City",
       "Duke", "Alto", "Fascino", "Ertiga", "Access", "Gixxer", "Venue", "Brezza", "Glamour", "Ciaz", "Fortuner"]
VA, VB = VEH[0::2], VEH[1::2]
SOC = ["Green Meadows", "Lake View Heights", "Sunrise Enclave", "Palm Grove", "Silver Oaks", "Maple Residency", "Orchid Park",
       "Hill Crest", "River Bend", "Sky Towers"]
STREETS = ["MG Road", "Station Road", "Temple Street", "Nehru Lane", "Park Avenue", "Canal Road", "Market Cross", "College Road"]
ACT = ["swimming", "guitar", "pottery", "salsa", "chess coaching", "karate", "French lessons", "boxing", "cycling club", "painting",
       "ceramics", "yoga", "judo", "tabla", "kathak", "squash", "archery", "robotics club", "scuba", "baking"]
BEV = ["coffee", "green tea", "cola", "masala chai", "energy drinks", "black tea", "iced latte", "hot chocolate", "cold coffee", "soda",
       "filter coffee", "lemon soda", "matcha", "mocha", "bubble tea", "kombucha", "espresso", "chai latte", "root beer", "ginger ale"]
EVENTS = ["housewarming", "farewell lunch", "anniversary dinner", "game night", "book club", "cricket match", "pottery workshop",
          "family puja", "reunion", "movie night"]
HFOOD = ["karela", "baingan", "lauki", "mushroom", "tinda", "arbi", "gobhi", "kaddu", "mooli", "turai", "pyaaz ke pakode", "sevai"]
HFOOD2 = ["rajma chawal", "chole bhature", "pizza", "momos", "aloo tikki", "biryani", "dosa", "jalebi", "kachori", "pani puri",
          "samosa", "dhokla"]
CITIES = ["Indore", "Lucknow", "Nagpur", "Bhopal", "Surat", "Patna", "Kanpur", "Raipur", "Jodhpur", "Ranchi", "Agra", "Vadodara"]
REASONS = ["a bike repair", "college books", "a phone screen", "a train ticket", "hostel rent", "a laptop charger", "a wedding gift",
           "medical tests", "a concert pass", "a security deposit"]


def pick(pool, i):
    return pool[i % len(pool)]


def tm():
    return f"{R.choice([9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7])}:{R.choice(['00', '15', '30', '45'])} {R.choice(['am', 'pm'])}"


def num(a, b):
    return R.randint(a, b)


def digits():
    return "".join(str(R.randint(0, 9)) for _ in range(10))


def build(kind, K):
    """(fact, [question phrasings], category) for template `kind` and key K."""
    if kind == "bday":
        d, m = num(1, 28), R.choice(MONTHS)
        return (f"{K}'s birthday is on {d} {m}, I should send a gift before that.",
                [f"When should I wish {K} happy birthday?", f"Which date do I need to remember for {K}'s special day?"], "temporal")
    if kind == "flat":
        f, n = num(2, 18), num(101, 1809)
        return (f"{K} just moved into flat {n} on floor {f} of {R.choice(SOC)}.",
                [f"What's {K}'s new apartment number?", f"Where is {K} staying now, which flat?"], "numeric")
    if kind == "meet":
        w, t = R.choice(DAYS), tm()
        return (f"I have a meeting with {K} about the {R.choice(TOPICS)} on {w} at {t}.",
                [f"What time am I supposed to catch up with {K} this week?", f"When is the sit-down with {K}?"], "temporal")
    if kind == "allergy":
        fd = R.choice(FOODS)
        return (f"{K} can't eat {fd}, it makes her sick.",
                [f"What should I keep out of the menu when {K} comes over?", f"Is there anything {K} reacts badly to?"], "plain")
    if kind == "buy":
        p = num(3, 90) * 50
        shop = R.choice(['Croma', 'a local shop', 'Amazon', 'Decathlon', 'the weekly market'])
        return (f"I bought the {K} for {p} rupees from {shop}.",
                [f"How much did the {K} cost me?", f"What price did I pay for the {K}?"], "numeric")
    if kind == "trip":
        m = R.choice(MONTHS)
        return (f"We're planning a trip to {K} in {m} with {R.choice(NAMES)}.",
                [f"Who am I travelling with to {K}?", f"Which friend is joining the {K} getaway?"], "plain")
    if kind == "service":
        km = num(4, 40) * 500
        return (f"The {K} is due for servicing at {km} kilometres.",
                [f"At what odometer reading does the {K} need its next service?", f"When do I take the {K} to the garage?"], "numeric")
    if kind == "neg_event":
        w, ev = R.choice(DAYS), R.choice(EVENTS)
        return (f"{K} said she won't be coming to the {ev} on {w}, something came up.",
                [f"Is {K} going to make it to the {ev}?", f"Will {K} be at the {ev}?"], "negation")
    if kind == "neg_bev":
        t = R.choice(["6 pm", "5 pm", "7 pm", "4 pm"])
        return (f"I've stopped having {K} after {t} because it ruins my sleep.",
                [f"Can I have {K} late in the evening?", f"What's my rule about {K} at night?"], "negation")
    if kind == "loan":
        a, r = num(2, 60) * 100, R.choice(REASONS)
        return (f"I lent {K} {a} rupees for {r}.",
                [f"How much does {K} owe me?", f"What amount is pending from {K}?"], "numeric")
    if kind == "doc":
        return (f"Dr {K}'s clinic is on {R.choice(STREETS)} and opens at {tm()}.",
                [f"Where do I go to see Dr {K}?", f"What's the address of Dr {K}'s practice?"], "plain")
    if kind == "locker":
        c = num(1000, 9999)
        return (f"The locker code at the {K} gym is {c}.",
                [f"What's the combination for my locker at {K}?", f"How do I open my gym locker in {K}?"], "numeric")
    if kind == "supp":
        mg, meal = num(1, 20) * 25, R.choice(["breakfast", "lunch", "dinner"])
        return (f"I take {K} {mg} mg after {meal} every day.",
                [f"What's my daily {K} dose?", f"When do I take {K} and how much?"], "numeric")
    if kind == "due":
        d, m = num(1, 28), R.choice(MONTHS)
        return (f"The {K} assignment is due on {d} {m}.",
                [f"By when do I have to hand in the {K} work?", f"What's the {K} submission deadline?"], "temporal")
    if kind == "recipe":
        n = num(2, 9)
        return (f"For {K} I use {n} spoons of ghee and a pinch of hing.",
                [f"How much ghee goes into {K}?", f"What are my quantities for {K} fat?"], "numeric")
    if kind == "pet":
        mo = R.choice([3, 4, 6, 12])
        return (f"{K}'s dog needs vaccinations every {mo} months.",
                [f"How often is {K}'s puppy due for shots?", f"What's the vaccine interval for {K}'s dog?"], "numeric")
    if kind == "switch":
        m, y = R.choice(MONTHS), R.choice([2023, 2024, 2025])
        return (f"I started {K} classes in {m} {y}.",
                [f"When did I begin {K}?", f"Since what month have I been doing {K}?"], "temporal")
    if kind == "h_wed":
        m, c = R.choice(MONTHS), R.choice(CITIES)
        return (f"{K} ki shaadi {m} mein hai, {c} mein hogi.",
                [f"{K} ki wedding kahan hai?", f"{K} ki shaadi kis sheher mein hai aur kab?"], "hinglish")
    if kind == "h_food":
        f1, f2 = R.choice(HFOOD), R.choice(HFOOD2)
        return (f"{K} ko {f1} bilkul pasand nahi hai, par {f2} bahut pasand hai.",
                [f"{K} kya khana nahi khati?", f"{K} ke liye kya banana chahiye, uski favourite cheez?"], "hinglish")
    if kind == "h_money":
        a = num(1, 40) * 100
        return (f"Kal {K} ko {a} rupaye dene hain, yaad dilana.",
                [f"{K} ko kitna paisa lautana hai?", f"Kal kisko payment karni hai aur kitni, {K} ko?"], "hinglish")
    if kind == "h_phone":
        d = digits()
        return (f"{K} ka phone number {d[:5]} {d[5:]} hai.",
                [f"{K} ka number kya hai?", f"{K} ko call karna hai, uska contact batao."], "hinglish")
    if kind == "h_plan":
        p, t = R.choice(PLACES), R.choice(["5", "6", "7"])
        return (f"Sunday ko {K} ke saath {p} jaane ka plan hai, subah {t} baje nikalenge.",
                [f"{K} ke saath outing kab hai aur kitne baje nikalna hai?", f"Sunday ka plan kya hai {K} ke saath, kahan jana hai?"], "hinglish")
    raise KeyError(kind)


SURN = ('Mehta Kulkarni Bansal Iyer Dutta Naidu Chopra Menon Rastogi Bhatt Sethi Joshi Verma Pillai Ghosh Kapoor Desai Nair Saxena Rao '
        'Banerjee Malhotra Thakur Shetty Agarwal Mishra Reddy Khanna Sinha').split()


# kind -> (key pool for facts, disjoint key pool for distractors)
SINGLE = {
    "bday": (NA, NB), "flat": (NA, NB), "meet": (NA, NB), "allergy": (NA, NB), "buy": (IA, IB), "trip": (PA, PB),
    "service": (VA, VB), "neg_event": (NA, NB), "neg_bev": (BEV[0::2], BEV[1::2]), "loan": (NA, NB), "doc": (NA, NB),
    "locker": (PA, PB), "supp": (SPA, SPB), "due": (SA, SB), "recipe": (DA_, DB_), "pet": (NA, NB), "switch": (ACT[0::2], ACT[1::2]),
    "h_wed": (NA, NB), "h_food": (NA, NB), "h_money": (NA, NB), "h_phone": (NA, NB), "h_plan": (NA, NB),
}
CHAT = ["Sure, I'll set that up for you.", "Okay, noted.", "Haan theek hai, kar deta hoon.", "That sounds like a good plan.",
        "Let me know if you want me to change anything.", "Kya haal hai aaj?", "I'm not sure about that one, could you say it again?",
        "Done. Anything else?", "Arre wah, mast idea hai.", "Thanks, that helps a lot.", "It's been a long day, honestly.",
        "Mausam aaj kaafi accha hai.", "Remind me later about this.", "Good morning! Ready to start?", "Chalo phir kal baat karte hain.",
        "Can you explain that in simpler words?", "I think I'll try the other option.", "Bas thoda sa aur time chahiye.",
        "Let's keep it for the weekend.", "That was surprisingly easy."]


def main():
    facts, qs, dist = [], [], []

    def add(kind, K):
        f, q, cat = build(kind, K)
        facts.append({"id": len(facts), "text": f, "kind": kind})
        qs.append({"fact": len(facts) - 1, "q": R.choice(q), "cat": cat, "kind": kind})

    # 22 templates x 11 = 242 facts; 29 near-dup pairs = 58 -> 300
    for kind, (pa, _pb) in SINGLE.items():
        for i in range(11):
            add(kind, pick(pa, i))
    # near-duplicate pairs: same person and template, only the day/place/time differ; question names the distinguishing day
    for i in range(29):
        n = NAMES[30 + i] + ' ' + SURN[i]
        (w1, w2), (p1, p2) = R.sample(DAYS, 2), R.sample(PLACES, 2)
        for w, p, t in ((w1, p1, tm()), (w2, p2, tm())):
            facts.append({"id": len(facts), "text": f"I'm meeting {n} at {p} on {w} at {t}.", "kind": "neardup"})
            qs.append({"fact": len(facts) - 1,
                       "q": R.choice([f"Where am I seeing {n} on {w}?", f"What's the venue for my {w} catch-up with {n}?"]),
                       "cat": "neardup", "kind": "neardup"})
    # distractors: same templates with disjoint keys (a question's key never has a second answer) + other-template facts for fact names + chit-chat
    seen = {f["text"] for f in facts}
    kinds = list(SINGLE)
    while len(dist) < 1000:
        kind = R.choice(kinds)
        f = build(kind, R.choice(SINGLE[kind][1]))[0]
        if f not in seen:
            seen.add(f)
            dist.append(f)
    na_kinds = [k for k in kinds if SINGLE[k][0] is NA]
    used = {(f["kind"], k) for f in facts for k in NA if k in f["text"]}
    tries = 0
    while len(dist) < 1300 and tries < 20000:
        tries += 1
        kind, K = R.choice(na_kinds), R.choice(NA)
        if (kind, K) in used:
            continue
        f = build(kind, K)[0]
        if f not in seen:
            seen.add(f)
            dist.append(f)
    while len(dist) < 1500:
        dist.append(R.choice(CHAT) + (" " + R.choice(CHAT) if R.random() < 0.5 else ""))
    R.shuffle(dist)
    out = {"facts": facts, "questions": qs, "distractors": dist}
    Path(__file__).parent.joinpath("data", "memory_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(facts), len(qs), len(dist), collections.Counter(q["cat"] for q in qs))


if __name__ == "__main__":
    main()
