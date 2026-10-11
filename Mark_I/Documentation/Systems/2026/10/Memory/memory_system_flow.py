from PIL import Image, ImageDraw, ImageFont
import sys
OUT = sys.argv[1]
F = "C:/Windows/Fonts/"
def font(n, s): return ImageFont.truetype(F + n, s)
BODY, BOLD, MONO, TITLE, BAND = font("segoeui.ttf", 27), font("segoeuib.ttf", 31), font("consola.ttf", 24), font("segoeuib.ttf", 66), font("segoeuib.ttf", 40)
SMALL = font("segoeui.ttf", 25)
W, M = 4600, 50
BANDX, BANDW = M, 300
AX = BANDX + BANDW + 40
AW = W - M - AX
COL = {
 "in": ("#1F5FD1", "#DCE8FF"), "bus": ("#7B3FC4", "#EBDDFB"), "live": ("#1E9E4A", "#D9F4E1"), "scr": ("#0E8F8F", "#D3F1F1"),
 "arc": ("#E07B00", "#FFE9CC"), "db": ("#C62828", "#FFDCDC"), "job": ("#B58900", "#FFF3C4"), "rec": ("#D81B84", "#FFDDEE"),
 "map": ("#555B66", "#E9ECF1"), "ex": ("#222222", "#FFFFFF")}


def wrap(txt, f, w):
    out = []
    for para in txt.split("\n"):
        line = ""
        for word in para.split(" "):
            t = (line + " " + word).strip()
            if f.getlength(t) <= w:
                line = t
            else:
                if line:
                    out.append(line)
                while f.getlength(word) > w:
                    k = len(word)
                    while f.getlength(word[:k]) > w:
                        k -= 1
                    out.append(word[:k])
                    word = word[k:]
                line = word
        out.append(line)
    return out


def measure(b, w):
    h = 22
    h += len(wrap(b[0], BOLD, w - 40)) * 40 + 6
    if b[1]:
        h += len(wrap(b[1], BODY, w - 40)) * 36 + 8
    if len(b) > 2 and b[2]:
        h += len(wrap(b[2], MONO, w - 40)) * 32 + 8
    return h + 18


def draw_box(d, x, y, w, h, b, c):
    d.rounded_rectangle([x, y, x + w, y + h], 22, fill=c[1], outline=c[0], width=5)
    cy = y + 20
    for l in wrap(b[0], BOLD, w - 40):
        d.text((x + 20, cy), l, font=BOLD, fill=c[0])
        cy += 40
    cy += 6
    if b[1]:
        for l in wrap(b[1], BODY, w - 40):
            d.text((x + 20, cy), l, font=BODY, fill="#1A1A1A")
            cy += 36
        cy += 8
    if len(b) > 2 and b[2]:
        for l in wrap(b[2], MONO, w - 40):
            d.text((x + 20, cy), l, font=MONO, fill="#333333")
            cy += 32


def arrow_r(d, x, y, c, gap=46):
    d.line([x + 4, y, x + gap - 12, y], fill=c, width=7)
    d.polygon([(x + gap - 4, y), (x + gap - 22, y - 13), (x + gap - 22, y + 13)], fill=c)


def arrow_down(d, x, y0, y1, c, label=None):
    d.line([x, y0, x, y1 - 14], fill=c, width=9)
    d.polygon([(x, y1), (x - 20, y1 - 28), (x + 20, y1 - 28)], fill=c)
    if label:
        d.text((x + 34, (y0 + y1) / 2 - 16), label, font=SMALL, fill="#222222")


BANDS = [
 ("1  INPUTS", "in", "What FRED senses: the ears, the eyes, and the clock", [[
   ("Microphone", "The ears. Lavalier mic opened through WASAPI at 16 kHz; audio flows in 80 ms chunks.", "audio/mic.py"),
   ("Wake word 'Hey Fred'", "A frozen model listens all day. A wake counts only if 300 ms of real speech came just before it (false wakes were firing on silence).", "audio/wakeword.py"),
   ("Hotkey", "Hold Left Ctrl + Left Alt to talk: starts the capture itself, no voice detector needed.", "audio/hotkey.py"),
   ("Camera", "The eyes. A webcam snapshot every 7 s answers one question: is Vatsal here? (presence)", "senses/  camera index 0"),
   ("Screen reader", "Reads the foreground window's text about once a second, only when it changed. The text stays in RAM; it is never logged.", "proactive/screenread.py"),
   ("Self-starting services", "News at 06:00 and 18:00, the deadline watch and the scheduler: things FRED says without being asked.", "news/  proactive/")]]),
 ("2  EVENT BUS", "bus", "One shared notice board: parts announce what they did, they never call each other", [[
   ("Event bus", "Every step above and below emits a small event (source, kind, data). Anything can subscribe. This keeps the parts independent.", "events.py"),
   ("Event log = NOISE, not memory", "Every event is appended to a day file and kept in a 10-minute RAM ring. It holds VAD flickers, wake scores, clock states. Debugging only.", "FRED_Private\\events\\YYYY-MM-DD.jsonl"),
   ("Usage store = tuning data", "Saved clips and decisions (what the clock heard, what Whisper heard, wake clips) to tune thresholds. Git-ignored: it holds your real voice.", "Project_FRED\\Data\\{clock,stt,wakeword}\\YYYY\\MM\\DD\\*.wav + *.jsonl")]]),
 ("3  LIVE TURN", "live", "One conversation turn, from sound to speech", [[
   ("VAD (voice detector)", "Silero, 32 ms frames, threshold 0.35: is someone speaking? Replaced webrtc, which fired on hum and noise.", "audio/vad.py"),
   ("Turn controller", "Decides if it is for FRED (wake, hotkey, 5 s follow-up), checks voice ID, finds the end of the turn. Logs one clock.decision per utterance.", "clock/turn_controller.py"),
   ("STT (ears to text)", "Whisper large-v3-turbo on the GPU, with hint words (names + today's news names). Ghost or hint-only text is dropped.", "audio/stt.py"),
   ("Brain", "Qwen3.5-4B with tools. Sees: persona + recalled memory + time + the last 8 exchanges. One loaded copy also does vision.", "engine/loop.py"),
   ("Tools / skills", "Calculator, news, screen_recall, memory tools, camera and screen look-ups. Chosen by meaning, not keywords.", "skills/"),
   ("TTS (voice out)", "Kokoro speaks the reply. If you interrupt (barge-in) it is cut at once. Its own text is remembered to filter echo.", "audio/tts.py")]]),
 ("4  SCREEN MEMORY", "scr", "What was on the screen becomes a low-weight memory (never a screenshot)", [[
   ("Reader, 1 per second", "Foreground window only. Tile diff skips the OCR when nothing changed; re-read every 10 s and at once when the window changes.", "proactive/screenread.py"),
   ("Exclusion check", "BEFORE anything is stored: bank/payment pages, card numbers, account/IFSC/UPI ids, password managers, private windows, the vault, lock screen, FRED's own windows. A hit leaves no trace.", "proactive/screenmem.py  excluded()"),
   ("Visit builder", "A visit = a window held 10 s or more. Near-duplicates (same app + title, similar text) are merged, not stored again. Text capped at 1000 characters.", "screen_mem_dwell_s, screen_mem_chars"),
   ("Store + embed", "Caption + text become an event of type 'screen', importance 0.1. The memory index embeds it with Qwen3-Embedding (1024 numbers). Runs in one worker thread.", "store.add_event('screen', ...)"),
   ("Image vector (GPU, low priority)", "A small image model turns the window into one vector saved with the visit. Only the vector is kept: no pixels anywhere.", "senses/imgembed.py"),
   ("Pause switch", "Voice command, config flag or a file flag stops this whole lane. The scam watch is exempt and keeps reading.", "proactive/gate.py")], [
   ("Deadline finder (plain code)", "Looks for a cue word (due, deadline, submit by) next to a date. Asks about each deadline ONCE, by voice and a toast; the answer is kept for good.", "FRED_Private\\deadlines.json"),
   ("Speak gate", "Waits while you talk or FRED talks; quiet hours 23:30-06:00 (to 01:00 if you are up) = toast only; 1 spoken offer per 10 min, 10 a day; a deadline under 12 h away overrides.", "proactive/gate.py"),
   ("Reminders", "On yes: 18:00 the evening before and 06:00 on the day (12:00 on weekends), and a 'deadline' memory event (importance 0.6).", "scheduler + memory event"),
   ("Scam watch", "Reads every window, banking and private ones included, whenever it is on. Warns if a page looks like a scam. Unaffected by the pause switch.", "proactive/scamwatch.py")]]),
 ("5  ARCHIVE (layer 1)", "arc", "The shoebox: the untouched record of everything, never edited or deleted", [[
   ("Feed", "Listens to the bus and writes one archive record for each conversation turn, tool call, transcript, presence change and error. Noise (VAD, wake scores, clock states, screen changes) is NOT archived.", "memory_bridge/feed.py"),
   ("Archive files, one per day per stream", "Append-only JSON lines, India day. Streams: conversation, tool_call, speech, perception, error, system. A torn last line is repaired on the next write.", "Mark_I\\Initial\\Testing_Memory\\archive\\YYYY\\MM\\DD\\<stream>.jsonl"),
   ("Blobs + self-check", "Files are stored once by content hash. verify() compares line counts and reports anything lost, extra or torn.", "archive.py, schema_archive.sql")]]),
 ("6  DATABASE (layer 2)", "db", "The filing cabinet: organised facts with history, searchable by meaning and by exact words", [[
   ("events", "Notable things that happened (a conversation fact, a 'screen' visit 0.1, a news item 0.2, a deadline 0.6). A write gate keeps clutter out.", "memory.sqlite : events"),
   ("entities + entity_states", "People, projects, places. Details have valid_from / valid_to: a changed fact closes the old row and opens a new one. Nothing is overwritten.", "memory.sqlite : entities, entity_states"),
   ("relationships + tags", "A dated map of connections (works on, lives in) and who/what each event is about. Same-name spellings resolved by alias.", "relationships, event_entities"),
   ("Search index", "Qwen3-Embedding-0.6B vectors (1024-d, stored per model), FAISS cache for meaning, FTS5 for exact words, merged by rank fusion.", "embeddings, FTS5, FAISS cache"),
   ("Sources + logs", "Every fact keeps where it came from. Every model decision and refusal is kept in decision_log (private DB, never the repo).", "sources, decision_log, review_queue, proposals"),
   ("Side tables", "screen_visits (app, title, times, image vector), daily_logs (the diary), yearly_alerts, model_items (what FRED learned about you).", "screen_visits, daily_logs, ...")]]),
 ("7  SCHEDULED JOBS", "job", "The night editor and the analysts: they read, never overwrite", [[
   ("Nightly consolidation", "One model call per day: diary entry, key events, fact updates, new relationships. Contradictions go to the review list, not into memory.", "consolidate.py"),
   ("Weekly analyst", "A stronger model reads the week with 14 memory tools and records inferences with evidence and alternatives.", "weekly.py"),
   ("Personal model", "A short list of things learned about you. A code gate (weeks of evidence, 2+ events) decides what is allowed in.", "personal_model.py"),
   ("Monthly / yearly review", "Named chapters of your life and turning points; a yearly note anchored to a private date.", "period.py"),
   ("Self-analysis, alerts, hygiene", "Quarterly deep read; yearly alerts (birthdays, festivals); cold-marking old items; backups with checksums.", "selfanalysis.py, alerts.py, backup.py")]]),
 ("8  RECALL", "rec", "How a memory comes back when you ask", [[
   ("Question", "'What was I looking at at 3 pm?' / 'When did I start the voice project?'", "brain or screen_recall tool"),
   ("Hybrid search", "Meaning (vectors) and exact words (FTS5) together; tagged events and saved facts are merged in.", "semantic.py"),
   ("Judge", "The model reads the top 20 and drops the useless ones; if nothing is left it searches again wider, then asks you.", "recall.py  judged_search"),
   ("Prompt block", "The kept memories go into the brain's system prompt for that turn; FRED answers from them and names the day.", "engine/prompt.py"),
   ("Ask queue", "Any doubt (same person? contradiction?) waits and is asked as an either/or when a chat winds down.", "askqueue.py")]]),
]
ARROWS = ["events", "heard / seen / said", "text + captions", "every turn, call, transcript", "records become events", "the night editor reads them", "answers fed back"]

MAP = ("WHERE THINGS LIVE (folder map)", [
 ("Mark_I\\Core\\", "FRED's code. Runs live from here. Never holds personal data."),
 ("Mark_I\\memory\\", "The memory CODE (archive.py, store.py, consolidate.py ...). Never data."),
 ("Mark_I\\Initial\\Testing_Memory\\", "TESTING memory data for now: memory.sqlite + archive\\YYYY\\MM\\DD\\<stream>.jsonl. Git-ignored. (Switch: config memory_testing; the real root is FRED_Private\\memory.)"),
 ("C:\\Users\\Dhiraj Vatsal\\FRED_Private\\", "Private state outside the repo: events\\ (noise log), news\\YYYY\\MM\\DD.json (digests), deadlines.json, news_offered.json, voice_refs.json (your voice), screen_paused.flag."),
 ("Project_FRED\\Data\\", "Usage clips + jsonl for tuning (clock, stt, wakeword). Git-ignored."),
 ("Project_FRED\\Old\\", "Everything from before Mark I (v1, tests). Not used."),
])
EXH = "EXAMPLE TRACE: you stay 10 s on an invented page 'Chemistry Assignment 4 - due 14 Oct' in Chrome, 11 Oct 15:02"
STEPS = [
 ("0 s", "in", "Screen reader OCRs the foreground window (chrome.exe). Text lives in RAM only.", "proactive/screenread.py"),
 ("1-9 s", "scr", "Tile diff: nothing changed, so no new OCR (re-read every 10 s). The dwell timer counts up.", "dwell timer in screenmem.py"),
 ("10 s", "scr", "Dwell >= 10 s: a visit is created. Exclusion check passes (not bank, card, password, private, vault, lock screen, FRED).", "excluded() returns None"),
 ("10 s", "scr", "Caption 'chrome.exe: Chemistry Assignment 4 - first lines' + text up to 1000 characters.", "caption + text"),
 ("~11 s", "db", "Worker thread: store.add_event('screen', 'Screen 11 Oct 15:02 chrome.exe: ...', importance 0.1). Written to the database: table events, plus its vector in embeddings and a row in FTS5.", "Mark_I\\Initial\\Testing_Memory\\memory.sqlite  ->  events, embeddings, FTS5"),
 ("~12 s", "db", "Image vector (GPU, background priority) saved with the visit. Only 1 vector, no pixels.", "memory.sqlite  ->  screen_visits.img"),
 ("10 s", "scr", "In parallel, the deadline finder sees 'due 14 Oct' and asks once through the speak gate (voice + toast). Answer saved; on yes: reminders 18:00 13 Oct and 06:00 14 Oct, memory event 'deadline' 0.6.", "FRED_Private\\deadlines.json"),
 ("every event", "bus", "The same moments also leave noise events (screen read counts, gate decision) in the event log. Counts only: never the screen text.", "FRED_Private\\events\\2026-10-11.jsonl"),
 ("night", "job", "Consolidation reads the day's events, screen ones at low weight, and writes the diary line and fact updates.", "memory.sqlite  ->  daily_logs, entity_states"),
 ("later", "rec", "'What was I looking at around 3 pm?' -> screen_recall tool -> search events + screen_visits -> a short answer with app, title, time.", "skills/extra/vision/screen_recall"),
 ("never", "ex", "NEVER written: a screenshot, the screen text in any log or bus event, anything from an excluded window.", "-"),
]


def lay_out():
    y = 330
    meta = []
    for bi, (name, ck, sub, rws) in enumerate(BANDS):
        top = y
        y += 26
        rhs = []
        for r in rws:
            w = (AW - 60 - 46 * (len(r) - 1)) // len(r)
            rhs.append((w, max(measure(b, w) for b in r)))
        for i, (w, h) in enumerate(rhs):
            y += h + (30 if i < len(rhs) - 1 else 0)
        y += 26
        need = 30 + len(wrap(name, BAND, BANDW - 40)) * 52 + 10 + len(wrap(sub, SMALL, BANDW - 40)) * 31 + 30
        y = max(y, top + need)
        meta.append((top, y, rhs))
        y += 90
    return meta, y


meta, y_end = lay_out()
mapw = W - 2 * M
maph = 90 + sum(len(wrap(a + "  " + b, BODY, mapw - 60)) * 36 + 14 for a, b in MAP[1]) + 20
cols = 4
ew = (W - 2 * M - 46 * (cols - 1)) // cols
rowsN = (len(STEPS) + cols - 1) // cols
eh = [max(measure((STEPS[j][0], STEPS[j][2], STEPS[j][3]), ew) for j in range(i * cols, min(len(STEPS), (i + 1) * cols))) for i in range(rowsN)]
H = int(y_end + maph + 300 + sum(eh) + 40 * rowsN + 200)
img = Image.new("RGB", (W, H), "#FAFAFC")
d = ImageDraw.Draw(img)
d.text((M, 40), "Mark I memory and system flow", font=TITLE, fill="#111111")
d.text((M, 125), "How a sound, a glance at the screen or a spoken line becomes a memory, and how it comes back. One plain-words line per step, then the file or table involved.", font=BODY, fill="#333333")
lx = M
for k, label in [("in", "inputs"), ("bus", "event bus"), ("live", "live turn"), ("scr", "screen memory"), ("arc", "archive"), ("db", "database"), ("job", "night jobs"), ("rec", "recall")]:
    c = COL[k]
    d.rounded_rectangle([lx, 190, lx + 44, 226], 8, fill=c[1], outline=c[0], width=4)
    d.text((lx + 56, 190), label, font=BODY, fill="#222222")
    lx += 56 + int(BODY.getlength(label)) + 60
d.text((M, 245), "Mono lines = where it lives (file, folder or table).  Arrows show the direction data moves.  Test memory is currently in Initial\\Testing_Memory.", font=SMALL, fill="#444444")
d.text((M, 285), "Generated 2026-10-11 from the code and docs; the screen lane was built today and has not run live yet.", font=SMALL, fill="#B00020")

for bi, (name, ck, sub, rws) in enumerate(BANDS):
    top, bot, rhs = meta[bi]
    c = COL[ck]
    d.rounded_rectangle([BANDX, top, W - M, bot], 30, fill="#FFFFFF", outline=c[0], width=6)
    d.rounded_rectangle([BANDX, top, BANDX + BANDW, bot], 30, fill=c[0])
    ty = top + 30
    for l in wrap(name, BAND, BANDW - 40):
        d.text((BANDX + 22, ty), l, font=BAND, fill="white")
        ty += 52
    ty += 10
    for l in wrap(sub, SMALL, BANDW - 40):
        d.text((BANDX + 22, ty), l, font=SMALL, fill="#FFFFFF")
        ty += 31
    y = top + 26
    for r, (w, h) in zip(rws, rhs):
        x = AX
        for i, b in enumerate(r):
            draw_box(d, x, y, w, h, b, c)
            x += w
            if i < len(r) - 1:
                arrow_r(d, x, y + h // 2, c[0])
                x += 46
        y += h + 30
    if bi < len(BANDS) - 1:
        arrow_down(d, AX + 220, bot + 6, meta[bi + 1][0] - 6, "#555555", ARROWS[bi])
d.text((AX + 240, meta[-1][1] + 20), "Recall results flow back up into band 3 (the brain's prompt) and band 4 (screen_recall).", font=SMALL, fill="#D81B84")

y = meta[-1][1] + 90
d.rounded_rectangle([M, y, W - M, y + maph], 28, fill=COL["map"][1], outline=COL["map"][0], width=6)
d.text((M + 30, y + 22), MAP[0], font=BAND, fill=COL["map"][0])
cy = y + 90
CB = font("consolab.ttf", 26)
for a, b in MAP[1]:
    d.text((M + 30, cy), a, font=CB, fill="#222222")
    off = int(CB.getlength(a)) + 40
    lines = wrap(b, BODY, mapw - 60 - off)
    for i, l in enumerate(lines):
        d.text((M + 30 + off, cy + i * 36), l, font=BODY, fill="#222222")
    cy += max(len(lines), 1) * 36 + 14
y = y + maph + 60
d.text((M, y), EXH, font=BAND, fill="#111111")
y += 80
for i in range(rowsN):
    for j in range(cols):
        k = i * cols + j
        if k >= len(STEPS):
            break
        t, ck, txt, path = STEPS[k]
        x = M + j * (ew + 46)
        draw_box(d, x, y, ew, eh[i], (t, txt, path), COL[ck])
        if j < cols - 1 and k < len(STEPS) - 1:
            arrow_r(d, x + ew, y + eh[i] // 2, "#222222")
    y += eh[i] + 40
img = img.crop((0, 0, W, y + 20))
img.save(OUT, optimize=True)
print(img.size)
