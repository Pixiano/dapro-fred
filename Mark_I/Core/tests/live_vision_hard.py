"""Live hard vision gate: 20 invented screens, each with a question that needs reading AND reasoning (a bug, a lookalike domain, a double-booked afternoon, a total that does not add up,
a dark-pattern banner, the unsecured network...). Everything on the pages is made up. Pages are rendered with headless Edge at 1920x1080 into a temp folder and never stored.

  python tests/live_vision_hard.py [model-id ...]       default: config.describe_model and unsloth/qwen3.5-4b, one after the other (one run at a time; the vision slot must be free)
Each model is loaded into the VISION slot with its projector; the answer comes through the real Describer with OCR grounding, thinking off. Pass = every `all` pattern found
and no `bad` pattern in the reply (negated mentions are dropped first). Also prints whether the OCR text alone already holds the answer (reading vs reasoning split).
"""
import re
import sys
import tempfile
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from models.client import Chat, Playground
from senses import ocr
from senses.describe import Describer
from tests.live_ocr import render

CSS = "body{margin:0;width:1920px;height:1080px;overflow:hidden;font-family:Segoe UI,Arial,sans-serif;background:#f3f4f6;color:#111}.c{position:absolute;left:160px;top:90px;width:1600px}h1{font-size:40px}"


def page(body: str, css: str = "") -> str:
    return f"<!doctype html><meta charset=utf-8><style>{CSS}{css}</style>{body}"


def table(rows, head=None, size=30):
    h = "".join(f"<th style='text-align:left;padding:10px 24px;background:#e5e7eb'>{c}</th>" for c in head) if head else ""
    b = "".join("<tr>" + "".join(f"<td style='padding:10px 24px;border-bottom:1px solid #ddd'>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table style='font-size:{size}px;border-collapse:collapse;margin-top:24px'><tr>{h}</tr>{b}</table>"


# (name, html, question, all-patterns, bad-pattern or None)
S = []
S.append(("code_bug", page("<div class=c><h1>billing.py</h1><pre style='font-size:30px;background:#1e1e1e;color:#d4d4d4;padding:30px'>1  def total_price(items):\n2      total = 0\n3      for i in range(1, len(items)):\n4          total += items[i].price\n5      return total\n6\n7  print(total_price(cart))   # cart has 4 items</pre></div>"),
          "This function is meant to add up every item in the cart but the result is too low. Which line is the problem and why?", [r"(line )?3\b|range\(1", r"first|skip|index 0|zero|0"], None))
S.append(("phish_lookalike", page("<div style='position:absolute;left:0;top:0;width:1920px;height:70px;background:#fff;border-bottom:1px solid #ccc;font-size:26px;line-height:70px;padding-left:40px'>&#128274; https://secure-paypa1-login.co/signin</div><div class=c style='top:200px;width:700px;margin-left:420px;background:#fff;padding:50px;font-size:30px'><h1 style='color:#003087'>PayPal</h1>Email<br><input style='width:600px;height:44px'><br><br>Password<br><input style='width:600px;height:44px'><br><br><button style='font-size:28px;padding:12px 40px;background:#0070ba;color:#fff;border:0'>Log In</button><p style='color:#c00'>Your account has been limited. Confirm your details within 24 hours.</p></div>"),
          "Is it safe to type my password into this page?", [r"\b(not|no|don'?t|unsafe|suspicious|phish|fake|scam|lookalike|paypa1|careful)\b"], r"\b(yes|safe to|is legit|is legitimate|looks legit)\b"))
S.append(("phish_bank", page("<div style='position:absolute;left:0;top:0;width:1920px;height:70px;background:#fff;border-bottom:1px solid #ccc;font-size:26px;line-height:70px;padding-left:40px'>https://netbank-secure.verify-account.xyz/auth</div><div class=c style='top:160px'><h1 style='color:#b00020'>URGENT: Account suspended</h1><p style='font-size:34px'>Verify your card number, PIN and OTP within 2 hours or your account will be closed permanently.</p><p style='font-size:30px'>Card number <input style='width:500px;height:40px'> PIN <input style='width:200px;height:40px'> OTP <input style='width:200px;height:40px'></p></div>"),
          "Does anything about this page look suspicious? Name the signs.", [r"phish|scam|suspicious|fake|not (a )?(legit|real|genuine|safe)", r"pin|otp|urgent|\.xyz|domain|2 hours|verify"], None))
S.append(("sheet_sum", page("<div class=c><h1>Q3 expenses</h1>" + table([["Hosting", "1,250"], ["Licences", "3,400"], ["Travel", "780"], ["Training", "2,150"], ["Misc", "960"]], ["Item", "Amount (USD)"], 34) + "</div>"),
          "What is the total of the Amount column?", [r"8,?540"], None))
S.append(("calendar_clash", page("<div class=c><h1>Thursday</h1><div style='position:relative;height:700px;font-size:28px'><div style='position:absolute;top:60px;left:0;width:700px;height:220px;background:#a7f3d0;padding:10px'>Design review<br>14:00 - 15:00</div><div style='position:absolute;top:170px;left:360px;width:700px;height:220px;background:#fecaca;padding:10px'>Dentist<br>14:30 - 15:30</div><div style='position:absolute;top:480px;left:0;width:700px;height:110px;background:#bfdbfe;padding:10px'>Team lunch<br>16:30 - 17:00</div></div></div>"),
          "Do I have a scheduling conflict this afternoon? Between which events?", [r"design review", r"dentist"], r"no conflict|don'?t have a conflict|no overlap"))
S.append(("traceback", page("<div class=c><pre style='font-size:26px;background:#0c0c0c;color:#ddd;padding:30px'>Traceback (most recent call last):\n  File \"app/main.py\", line 41, in handle\n    return invoice(order)\n  File \"app/billing.py\", line 88, in invoice\n    uid = order['user_id']\nKeyError: 'user_id'\n\n127.0.0.1 - - [07/Oct/2026 09:14:02] \"POST /checkout HTTP/1.1\" 500 -</pre></div>"),
          "What exception was raised, in which file and on which line?", [r"keyerror", r"billing\.py", r"\b88\b"], None))
S.append(("bar_low", page("<div class=c><h1>Monthly sales (thousands)</h1><div style='position:relative;height:620px;border-left:3px solid #333;border-bottom:3px solid #333;margin-top:30px;font-size:26px'>" + "".join(f"<div style='position:absolute;bottom:0;left:{40 + i * 230}px;width:150px;height:{v * 9}px;background:#3b82f6'><div style='margin-top:-34px'>{v}</div></div><div style='position:absolute;bottom:-44px;left:{70 + i * 230}px'>{m}</div>" for i, (m, v) in enumerate([("Jan", 52), ("Feb", 47), ("Mar", 12), ("Apr", 58), ("May", 63), ("Jun", 61)])) + "</div></div>"),
          "Which month had the lowest sales and roughly how much?", [r"mar", r"\b12\b"], None))
S.append(("pricing", page("<div class=c><h1>Pick a plan</h1>" + table([["Starter", "$12 per user / month", "1-5 users"], ["Team", "$9 per user / month", "minimum 10 users"], ["Business", "$15 per user / month", "unlimited users"]], ["Plan", "Price", "Limits"], 34) + "</div>"),
          "Which plan has the lowest price per user, and what is it?", [r"team", r"\$?9\b"], None))
S.append(("cookie_pattern", page("<div class=c><h1>Daily Recipes</h1><p style='font-size:28px'>Welcome back! Scroll for today's dishes.</p></div><div style='position:absolute;left:0;bottom:0;width:1920px;height:260px;background:#fff;border-top:3px solid #ccc;padding:30px 160px;font-size:26px'>We value your privacy. We and 143 partners use cookies to personalise ads.<br><br><button style='font-size:34px;padding:18px 80px;background:#16a34a;color:#fff;border:0'>Accept all</button> <span style='font-size:14px;color:#aaa;margin-left:40px'>manage options</span></div>"),
          "I want to decline the cookies. What do I click?", [r"manage"], None))
S.append(("form_errors", page("<div class=c style='width:900px;margin-left:300px;font-size:30px'><h1>Create account</h1>Email<br><input value='sam@example.com' style='width:800px;height:44px;border:3px solid #c00'><div style='color:#c00;font-size:24px'>This email is already registered</div><br>Password<br><input value='abc12345' type=text style='width:800px;height:44px;border:3px solid #c00'><div style='color:#c00;font-size:24px'>Password must be at least 12 characters</div><br><button disabled style='font-size:28px;padding:12px 60px'>Sign up</button></div>"),
          "Why can't I sign up? List every problem.", [r"already", r"\b12\b|characters|longer|too short"], None))
S.append(("chat_deadline", page("<div class=c style='width:900px;margin-left:300px;font-size:30px'><div style='background:#fff;padding:16px;margin:12px'><b>Priya</b> 10:02<br>Report is due Friday right?</div><div style='background:#dcf8c6;padding:16px;margin:12px;margin-left:200px'><b>Arun</b> 10:05<br>No, I checked with Meera. It moved up to Thursday 5 pm.</div><div style='background:#fff;padding:16px;margin:12px'><b>Priya</b> 10:06<br>Ugh. Ok, I'll finish the charts tonight.</div></div>"),
          "When is the report actually due?", [r"thursday", r"5 ?(pm|p\.m\.|:00)|17"], r"friday"))
S.append(("invoice_mismatch", page("<div class=c style='font-size:30px'><h1>Invoice #2291</h1>" + table([["Design work", "620"], ["Hosting", "480"], ["Support", "380"]], ["Description", "Amount"], 34) + "<p style='font-size:36px;margin-top:30px'><b>Total due: 1,840</b></p></div>"),
          "Does the total due match the line items?", [r"\b(no|not|doesn'?t|mismatch|incorrect|wrong|differs?)\b", r"1,?480"], None))
S.append(("privacy_toggles", page("<div class=c style='width:1000px;margin-left:300px;font-size:34px'><h1>Privacy</h1>" + table([["Location", "Off"], ["Camera", "On"], ["Microphone", "On"], ["Contacts", "Off"]], None, 36) + "</div>"),
          "Which privacy settings are currently turned on?", [r"camera", r"microphone"], r"location (is |are )?(turned )?on|contacts (is |are )?(turned )?on"))
S.append(("cpu_hog", page("<div class=c style='font-size:24px'><h1 style='font-size:32px'>Processes</h1>" + table([["chrome.exe", "4.1%", "812 MB"], ["Code.exe", "6.3%", "540 MB"], ["render_worker.exe", "87.4%", "2,100 MB"], ["explorer.exe", "0.8%", "120 MB"], ["spoolsv.exe", "0.1%", "14 MB"]], ["Name", "CPU", "Memory"], 24) + "</div>"),
          "Which process is using the most CPU?", [r"render_worker"], None))
S.append(("diff", page("<div class=c><pre style='font-size:30px;background:#fff;padding:30px'> def fetch(url):\n-    resp = http.get(url, timeout=30)\n+    resp = http.get(url, timeout=3)\n     return resp.json()</pre></div>"),
          "What value changed in this diff, and to what?", [r"\b30\b", r"\b3\b"], None))
S.append(("email_spoof", page("<div class=c style='background:#fff;padding:40px;font-size:28px'><b>From:</b> Accounts &lt;billing@micros0ft-support.net&gt;<br><b>Subject:</b> Your invoice is attached<br><hr>Dear customer, please review the attached invoice.<br><br><a style='color:#06c'>https://www.microsoft.com/invoice/view</a> <span style='font-size:18px;color:#888'>(links to http://bit.ly/x9fK2)</span><br><br>Open invoice.pdf.exe</div>"),
          "Is there anything off about this email?", [r"micros0ft|bit\.ly|\.exe|spoof|phish|suspicious|fake|mismatch"], None))
S.append(("card_anomaly", page("<div class=c style='font-size:28px'><h1>Recent card activity</h1>" + table([["03 Oct", "Corner Cafe", "4.50"], ["03 Oct", "Metro Pass", "30.00"], ["04 Oct", "FOREIGN TXN LAGOS NG", "4,999.00"], ["05 Oct", "Grocer", "62.10"], ["06 Oct", "Streaming", "9.99"]], ["Date", "Merchant", "Amount"], 30) + "</div>"),
          "Which transaction looks unusual?", [r"lagos|foreign|4,?999"], None))
S.append(("wifi_open", page("<div class=c style='width:900px;margin-left:300px;font-size:32px'><h1>Wi-Fi networks</h1>" + table([["HomeNet_5G", "Secured (WPA3)"], ["CafeGuest", "Open"], ["Neighbour_2G", "Secured (WPA2)"], ["Library", "Secured (WPA2)"]], None, 32) + "</div>"),
          "Which network is not secured?", [r"cafeguest|cafe guest"], None))
S.append(("boarding", page("<div class=c style='background:#fff;padding:50px;font-size:36px;width:1100px;margin-left:200px'><b>BOARDING PASS</b><br>Flight AB 417 &nbsp; Seat 14C<br>From DEL to BOM<br><br>Gate <b>C12</b> &nbsp; Boarding <b>18:25</b> &nbsp; Departs 19:05</div>"),
          "Which gate, and when does boarding start?", [r"c ?12", r"18:25|6:25"], None))
S.append(("recipe_scale", page("<div class=c style='width:900px;margin-left:300px;font-size:34px;background:#fff;padding:40px'><h1>Pancakes (serves 4)</h1>2 cups flour<br>2 eggs<br>1 cup milk<br>1 tsp baking powder</div>"),
          "I'm cooking for 6 people. How much flour do I need?", [r"\b3\b|three"], None))


def passes(reply: str, all_: list, bad: str | None) -> bool:
    r = reply.lower()
    pos = re.sub(r"\b(no|nor|nothing|not|without|zero|free of)\b[^.,;\n]{0,40}", " ", r)
    return all(re.search(p, r) for p in all_) and not (bad and re.search(bad, pos))


def main(models):
    url = config.get().playground_url
    pg = Playground(url)
    assert pg.healthy() and pg.status()["slots"]["vision"] is None, "playground down or the vision slot is busy"
    tmp = Path(tempfile.mkdtemp())
    imgs = []
    for name, html, q, a, b in S:
        rgb = render(html, tmp, name)
        imgs.append((name, rgb, ocr.read(rgb)))
    print(f"rendered {len(imgs)} screens")
    table_ = {}
    for model in models:
        pg.load(model, slot="vision", ctx=8192, vision=True)
        d = Describer(Chat(model, slot="vision", url=url, max_tokens=config.get().describe_max_tokens, temperature=0.2), ev.EventBus())
        res, ok = [], 0
        try:
            for (name, html, q, a, b), (_, rgb, text) in zip(S, imgs):
                t = time.time()
                r = d.describe(rgb, question=q, ocr_text=text)
                good = passes(r.text, a, b)
                ok += good
                res.append(good)
                print(f"[{model.split('/')[-1][:14]}] {'PASS' if good else 'FAIL'} {name:16} {int((time.time() - t) * 1000):5d} ms  ocr-has-it: {'yes' if passes(text, a, None) else 'no '}  " + ("" if good else f"reply: {r.text[:170]!r}"), flush=True)
        finally:
            pg.unload("vision")
        table_[model] = (ok, res)
        print(f"== {model}: {ok}/{len(S)}")
    if len(models) == 2:
        (m1, (_, r1)), (m2, (_, r2)) = table_.items()
        print("only", m1.split("/")[-1], ":", [S[i][0] for i in range(len(S)) if r1[i] and not r2[i]])
        print("only", m2.split("/")[-1], ":", [S[i][0] for i in range(len(S)) if r2[i] and not r1[i]])
        print("both fail:", [S[i][0] for i in range(len(S)) if not r1[i] and not r2[i]])


if __name__ == "__main__":
    main(sys.argv[1:] or [config.get().describe_model, "unsloth/qwen3.5-4b"])
