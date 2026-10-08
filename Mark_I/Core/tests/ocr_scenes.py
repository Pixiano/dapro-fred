"""Invented complex screens for the OCR / vision reading test: each scene is (name, html, phrases) where `phrases` are the texts that really are on the page.

Nothing here is real browsing or personal content: the sites, channels, people and numbers are made up. tests/live_ocr.py renders these with headless Edge at 1920x1080
(native size, device scale 1) and scores how many phrases each reader (Windows OCR, the vision describer) gets back.
"""

BASE = "body{margin:0;width:1920px;height:1080px;overflow:hidden;font-family:Segoe UI,Arial,sans-serif}"


def page(css: str, body: str) -> str:
    return f"<!doctype html><meta charset=utf-8><style>{BASE}{css}</style>{body}"


SCENES = []

# 1 YouTube-style watch page, dark theme
SCENES.append(("youtube_watch", page("""
body{background:#0f0f0f;color:#fff}.v{position:absolute;left:60px;top:70px;width:1180px;height:664px;background:linear-gradient(135deg,#1e3c72,#2a5298 55%,#c33764)}
h1{position:absolute;left:60px;top:750px;font-size:24px;margin:0;width:1180px;font-weight:600}.ch{position:absolute;left:60px;top:810px;font-size:16px}.ch small{color:#aaa;font-size:13px}
.btn{position:absolute;top:812px;background:#272727;border-radius:18px;padding:8px 16px;font-size:14px}.desc{position:absolute;left:60px;top:880px;width:1180px;background:#272727;border-radius:12px;padding:14px;font-size:14px}
.rec{position:absolute;left:1280px;width:580px;font-size:14px}.rec .t{width:168px;height:94px;background:#333;float:left;margin-right:10px}.rec b{font-size:15px;display:block}.rec span{color:#aaa;font-size:12px}
.cm{position:absolute;left:60px;top:990px;font-size:13px;color:#ddd}""",
 """<div class=v></div><h1>How to Build a Home Server in 2026 | Full Walkthrough</h1>
<div class=ch>Tech Workshop <small>1.2M subscribers</small></div><div class=btn style="left:520px">Subscribe</div><div class=btn style="left:640px">Like 12K</div><div class=btn style="left:740px">Share</div>
<div class=desc><b>482,113 views  3 weeks ago</b><br>0:00 Intro &nbsp; 3:42 Choosing the case &nbsp; 11:05 Installing the drives &nbsp; 19:48 Network setup &nbsp; 27:30 Final benchmarks<br>Links: https://workshop.example.com/server-guide</div>
<div class=cm>@mira_codes: The part at 19:48 saved me hours, thank you!</div>
<div class=rec style="top:70px"><div class=t></div><b>I Tried Every Budget NAS So You Don't Have To</b><span>Disk Doctor<br>218K views  1 month ago  21:07</span></div>
<div class=rec style="top:180px"><div class=t></div><b>Why Your Wi-Fi Is Slow (And The 5 Minute Fix)</b><span>Network Notes<br>1.4M views  8 months ago  9:31</span></div>
<div class=rec style="top:290px"><div class=t></div><b>Docker Compose Crash Course for Beginners</b><span>Tech Workshop<br>96K views  2 weeks ago  34:12</span></div>
<div class=rec style="top:400px"><div class=t></div><b>Silent PC Build: Fanless 65W Tower</b><span>Quiet Builds<br>57K views  5 days ago  15:40</span></div>""",),
 ["How to Build a Home Server in 2026", "Full Walkthrough", "Tech Workshop", "1.2M subscribers", "Subscribe", "Like 12K", "482,113 views", "3 weeks ago",
  "Choosing the case", "Installing the drives", "Network setup", "Final benchmarks", "https://workshop.example.com/server-guide", "The part at 19:48 saved me hours",
  "I Tried Every Budget NAS", "Disk Doctor", "218K views", "Why Your Wi-Fi Is Slow", "1.4M views", "Docker Compose Crash Course", "Silent PC Build", "Fanless 65W Tower"]))

# 2 YouTube-style home grid, text drawn ON the thumbnails
_cards = [("SERVER RACK TOUR", "#c0392b", "I Built a 42U Rack at Home", "Lab Notes", "2.1M views  2 days ago"),
          ("5 LINUX TIPS", "#16a085", "Five Linux Terminal Tricks You Need", "Shell Daily", "310K views  1 week ago"),
          ("PYTHON IN 10", "#2980b9", "Learn Python in 10 Minutes", "Code Quick", "5.6M views  1 year ago"),
          ("GPU FIGHT!", "#8e44ad", "Used GPUs: Worth It In 2026?", "Bench Mark", "889K views  3 days ago"),
          ("DRONE FOOTAGE", "#d35400", "Sunrise Over The Alps 4K", "Sky Frames", "74K views  6 hours ago"),
          ("KITCHEN HACKS", "#27ae60", "Meal Prep For The Whole Week", "Cook Along", "1.1M views  4 months ago")]
_grid = "".join(f"<div class=c><div class=th style='background:{c}'>{t}</div><b>{ti}</b><span>{ch}<br>{v}</span></div>" for t, c, ti, ch, v in _cards)
SCENES.append(("youtube_home", page("""
body{background:#fff;color:#0f0f0f}.top{height:56px;border-bottom:1px solid #ddd;font-size:20px;padding:14px 24px;box-sizing:border-box}.g{padding:30px 40px;display:flex;flex-wrap:wrap;gap:30px}
.c{width:560px}.th{height:315px;border-radius:12px;color:#fff;font:900 54px Impact,Arial;display:flex;align-items:center;justify-content:center;text-shadow:3px 3px 0 #000}
.c b{display:block;font-size:17px;margin-top:10px}.c span{font-size:14px;color:#606060}""", f"<div class=top>YouTube &nbsp; Home &nbsp; Shorts &nbsp; Subscriptions</div><div class=g>{_grid}</div>"),
 ["SERVER RACK TOUR", "I Built a 42U Rack at Home", "Lab Notes", "2.1M views", "FIVE LINUX TIPS".replace("FIVE", "5"), "Five Linux Terminal Tricks You Need", "Shell Daily", "PYTHON IN 10",
  "Learn Python in 10 Minutes", "5.6M views", "GPU FIGHT", "Used GPUs: Worth It In 2026", "DRONE FOOTAGE", "Sunrise Over The Alps 4K", "KITCHEN HACKS", "Meal Prep For The Whole Week"]))

# 3 Search results with links
SCENES.append(("search_results", page("""
body{background:#fff;font-family:Arial}.r{margin:0 0 26px 170px;width:650px}.r .u{font-size:14px;color:#202124}.r .u i{color:#5f6368;font-style:normal}.r a{display:block;font-size:20px;color:#1a0dab;text-decoration:underline;margin:2px 0}.r p{margin:0;font-size:14px;color:#4d5156;line-height:1.5}
.bar{margin:30px 170px 20px;font-size:14px;color:#70757a}.tiny{margin:10px 170px;font-size:11px;color:#70757a}""", """
<div class=bar>About 4,310,000 results (0.42 seconds)</div>
<div class=r><div class=u>https://docs.example.org <i>&rsaquo; guide &rsaquo; setup</i></div><a>Installation Guide | Example Docs</a><p>Step-by-step instructions to install the toolkit on Windows, macOS and Linux. Requires Python 3.11 or newer.</p></div>
<div class=r><div class=u>https://github.com <i>&rsaquo; example-org &rsaquo; toolkit</i></div><a>example-org/toolkit: A fast command line toolkit</a><p>14.2k stars &middot; 1.1k forks &middot; Latest release v2.8.1 published on 12 Sep 2026. MIT licence.</p></div>
<div class=r><div class=u>https://forum.example.net <i>&rsaquo; t &rsaquo; error-code-0x80070005</i></div><a>Fix: Access is denied (0x80070005) when installing</a><p>Run the installer as administrator and disable the controlled folder access policy, then retry the setup.</p></div>
<div class=r><div class=u>https://en.wikipedia.org <i>&rsaquo; wiki &rsaquo; Home_server</i></div><a>Home server - Wikipedia</a><p>A home server is a computer that provides services to other devices on a home network, such as file storage, media streaming and backups.</p></div>
<div class=tiny>Privacy &middot; Terms &middot; Settings &middot; Send feedback &middot; Report inappropriate predictions</div>""",),
 ["About 4,310,000 results", "https://docs.example.org", "Installation Guide | Example Docs", "Requires Python 3.11 or newer", "example-org/toolkit", "14.2k stars", "Latest release v2.8.1",
  "Fix: Access is denied (0x80070005)", "controlled folder access policy", "Home server - Wikipedia", "file storage, media streaming and backups", "Report inappropriate predictions"]))

# 4 Article with inline links, long URLs and small footnotes
SCENES.append(("links_article", page("""
body{background:#fafafa;color:#222;font-family:Georgia,serif}.a{margin:60px 360px;width:1200px;font-size:20px;line-height:1.6}a{color:#0645ad;text-decoration:underline}.fn{font:12px Arial;color:#555;margin-top:30px}h2{font:700 34px Arial}""", """
<div class=a><h2>Your Order Has Shipped</h2><p>Hello Sam, your package <b>#48213-77</b> left our warehouse today. Track it here: <a>https://track.example-shop.com/p?id=48213&amp;ref=newsletter</a> or visit
<a>your account page</a> to change the delivery address. Estimated delivery: <b>Thursday, 2 October 2026</b>. Need help? Email <a>support@example-shop.com</a> or call 1-800-555-0142.</p>
<p>Unsubscribe: <a>https://example-shop.com/unsub?u=a8f3c2d1</a> &middot; <a>View in browser</a> &middot; <a>Privacy policy</a></p>
<div class=fn>[1] Delivery windows are estimates. [2] Prices include VAT where applicable. &copy; 2026 Example Shop Ltd, 12 Market Street, Springfield. Company no. 01234567.</div></div>""",),
 ["Your Order Has Shipped", "#48213-77", "https://track.example-shop.com/p?id=48213&ref=newsletter", "your account page", "Thursday, 2 October 2026", "support@example-shop.com", "1-800-555-0142",
  "https://example-shop.com/unsub?u=a8f3c2d1", "View in browser", "Privacy policy", "Delivery windows are estimates", "Example Shop Ltd", "Company no. 01234567"]))

# 5 Flow diagram (SVG): labelled boxes and arrows
SCENES.append(("diagram_flow", page("body{background:#fff}svg text{font-family:Segoe UI,Arial}", """
<svg width=1920 height=1080><defs><marker id=a markerWidth=10 markerHeight=10 refX=9 refY=3 orient=auto><path d="M0,0 L9,3 L0,6 z" fill="#444"/></marker></defs>
<g stroke="#444" stroke-width=2 fill="none" marker-end="url(#a)"><path d="M330,300 L520,300"/><path d="M740,300 L930,300"/><path d="M1150,300 L1340,300"/><path d="M630,340 L630,520"/><path d="M1240,340 L1240,520"/><path d="M740,600 L1130,600"/></g>
<g font-size=22 text-anchor=middle><rect x=110 y=250 width=220 height=100 rx=10 fill="#2c3e50"/><text x=220 y=307 fill="#fff">User Request</text>
<rect x=520 y=250 width=220 height=100 rx=10 fill="#2980b9"/><text x=630 y=307 fill="#fff">Load Balancer</text>
<rect x=930 y=250 width=220 height=100 rx=10 fill="#27ae60"/><text x=1040 y=307 fill="#fff">API Server</text>
<rect x=1340 y=250 width=220 height=100 rx=10 fill="#8e44ad"/><text x=1450 y=307 fill="#fff">Auth Service</text>
<rect x=520 y=520 width=220 height=100 rx=10 fill="#e67e22"/><text x=630 y=577 fill="#fff">Redis Cache</text>
<rect x=1130 y=520 width=220 height=100 rx=10 fill="#c0392b"/><text x=1240 y=577 fill="#fff">PostgreSQL</text></g>
<g font-size=15 fill="#333" text-anchor=middle><text x=425 y=285>HTTPS :443</text><text x=835 y=285>round robin</text><text x=1245 y=285>JWT check</text><text x=700 y=440>cache miss</text><text x=935 y=590>SQL 5432</text>
<text transform="translate(1290 430) rotate(-90)">read replica</text></g>
<text x=110 y=760 font-size=14 fill="#666">Legend: blue = edge, green = app tier, red = data tier. Latency budget 120 ms p95.</text></svg>""",),
 ["User Request", "Load Balancer", "API Server", "Auth Service", "Redis Cache", "PostgreSQL", "HTTPS :443", "round robin", "JWT check", "cache miss", "SQL 5432",
  "read replica", "Legend: blue = edge, green = app tier, red = data tier", "Latency budget 120 ms p95"]))

# 6 Architecture diagram with tiny labels
SCENES.append(("diagram_arch", page("body{background:#f4f6f8}svg text{font-family:Consolas,monospace}", """
<svg width=1920 height=1080><rect x=80 y=80 width=1000 height=500 rx=14 fill="none" stroke="#7f8c8d" stroke-width=2 stroke-dasharray="10 6"/>
<text x=100 y=110 font-size=16 fill="#7f8c8d">VPC 10.0.0.0/16 - eu-west-1</text>
<rect x=120 y=160 width=260 height=130 fill="#dff0ff" stroke="#2980b9" stroke-width="2"/><text x=140 y=200 font-size=18>web-01</text><text x=140 y=228 font-size=12>10.0.1.11 nginx 1.25</text><text x=140 y=250 font-size=12>cpu 34% mem 61%</text>
<rect x=440 y=160 width=260 height=130 fill="#dff0ff" stroke="#2980b9" stroke-width="2"/><text x=460 y=200 font-size=18>web-02</text><text x=460 y=228 font-size=12>10.0.1.12 nginx 1.25</text><text x=460 y=250 font-size=12>cpu 29% mem 58%</text>
<rect x=760 y=160 width=260 height=130 fill="#e8f8e8" stroke="#27ae60" stroke-width="2"/><text x=780 y=200 font-size=18>worker-01</text><text x=780 y=228 font-size=12>10.0.2.21 celery 5.3</text><text x=780 y=250 font-size=12>queue depth 1,284</text>
<rect x=280 y=390 width=300 height=140 fill="#fdecea" stroke="#c0392b" stroke-width="2"/><text x=300 y=430 font-size=18>db-primary</text><text x=300 y=458 font-size=12>10.0.3.31 postgres 16.2</text><text x=300 y=480 font-size=12>iops 3,100 lag 0.2s</text>
<g stroke="#555" stroke-width=2><line x1=250 y1=290 x2=380 y2=390 /><line x1=570 y1=290 x2=480 y2=390 /><line x1=890 y1=290 x2=560 y2=420 /></g>
<text x=1140 y=130 font-size=13>Alerts (3)</text><text x=1140 y=156 font-size=12 fill="#c0392b">CRIT disk /var 92% on db-primary</text><text x=1140 y=178 font-size=12 fill="#e67e22">WARN queue depth above 1,000</text><text x=1140 y=200 font-size=12 fill="#7f8c8d">INFO cert expires in 18 days</text></svg>""",),
 ["VPC 10.0.0.0/16", "eu-west-1", "web-01", "10.0.1.11 nginx 1.25", "cpu 34% mem 61%", "web-02", "10.0.1.12 nginx 1.25", "worker-01", "10.0.2.21 celery 5.3", "queue depth 1,284",
  "db-primary", "10.0.3.31 postgres 16.2", "iops 3,100 lag 0.2s", "Alerts (3)", "CRIT disk /var 92% on db-primary", "WARN queue depth above 1,000", "INFO cert expires in 18 days"]))

# 7 Code editor, dark, syntax colours
SCENES.append(("code_dark", page("""
body{background:#1e1e1e;color:#d4d4d4;font:15px Consolas,monospace}.tabs{background:#252526;height:36px;padding:8px 16px;box-sizing:border-box;font:13px Segoe UI}.ln{position:absolute;left:20px;top:50px;color:#858585;line-height:22px;text-align:right;width:36px}
pre{position:absolute;left:80px;top:50px;margin:0;line-height:22px;font:15px/22px Consolas,monospace}.k{color:#569cd6}.s{color:#ce9178}.c{color:#6a9955}.f{color:#dcdcaa}.n{color:#b5cea8}.st{position:absolute;bottom:0;left:0;right:0;height:24px;background:#007acc;color:#fff;font:12px Segoe UI;padding:4px 12px;box-sizing:border-box}""", """
<div class=tabs>retry_policy.py &nbsp;&nbsp; test_retry.py &nbsp;&nbsp; settings.json</div><div class=ln>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>11<br>12<br>13<br>14<br>15</div>
<pre><span class=k>import</span> time, random
<span class=k>from</span> typing <span class=k>import</span> Callable, TypeVar

MAX_ATTEMPTS = <span class=n>5</span>
BASE_DELAY_S = <span class=n>0.25</span>

<span class=k>def</span> <span class=f>retry_with_backoff</span>(fn: Callable[[], T], *, attempts: <span class=k>int</span> = MAX_ATTEMPTS) -&gt; T:
    <span class=c># exponential backoff with full jitter</span>
    <span class=k>for</span> attempt <span class=k>in</span> <span class=f>range</span>(attempts):
        <span class=k>try</span>:
            <span class=k>return</span> fn()
        <span class=k>except</span> ConnectionError <span class=k>as</span> exc:
            delay = random.uniform(<span class=n>0</span>, BASE_DELAY_S * <span class=n>2</span> ** attempt)
            <span class=f>print</span>(<span class=s>f"attempt {attempt + 1} failed: {exc!r}, sleeping {delay:.2f}s"</span>)
    <span class=k>raise</span> RuntimeError(<span class=s>"all retries exhausted"</span>)</pre>
<div class=st>main &nbsp; 0 errors 2 warnings &nbsp;&nbsp; Ln 9, Col 18 &nbsp; UTF-8 &nbsp; Python 3.11.4</div>""",),
 ["retry_policy.py", "test_retry.py", "settings.json", "import time, random", "from typing import Callable, TypeVar", "MAX_ATTEMPTS = 5", "BASE_DELAY_S = 0.25",
  "def retry_with_backoff", "attempts: int = MAX_ATTEMPTS", "exponential backoff with full jitter", "for attempt in range(attempts)", "except ConnectionError as exc",
  "random.uniform(0, BASE_DELAY_S * 2 ** attempt)", "all retries exhausted", "0 errors 2 warnings", "Python 3.11.4"]))

# 8 Dense table with numbers
_rows = [("INV-1041", "Acme Logistics", "2026-09-03", "1,240.50", "EUR", "Paid"), ("INV-1042", "Northwind Foods", "2026-09-05", "-86.20", "EUR", "Credit"),
         ("INV-1043", "Globex Corp", "2026-09-08", "12,009.99", "USD", "Overdue"), ("INV-1044", "Initech", "2026-09-11", "430.00", "GBP", "Pending"),
         ("INV-1045", "Umbrella Ltd", "2026-09-14", "7,775.10", "EUR", "Paid"), ("INV-1046", "Stark Supply", "2026-09-18", "-1,050.00", "USD", "Refund"),
         ("INV-1047", "Wayne Freight", "2026-09-21", "98.76", "EUR", "Pending"), ("INV-1048", "Hooli Cloud", "2026-09-25", "15,300.00", "USD", "Paid")]
_tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in _rows)
SCENES.append(("table_dense", page("""
body{background:#fff;font:14px Calibri,Arial}table{border-collapse:collapse;margin:60px 100px;width:1500px}td,th{border:1px solid #bbb;padding:7px 12px;text-align:left}th{background:#e8eef7}td:nth-child(4){text-align:right;font-variant-numeric:tabular-nums}""",
 f"<table><tr><th>Invoice</th><th>Customer</th><th>Date</th><th>Amount</th><th>Cur</th><th>Status</th></tr>{_tr}</table>"),
 ["INV-1041", "Acme Logistics", "1,240.50", "INV-1042", "Northwind Foods", "-86.20", "INV-1043", "Globex Corp", "12,009.99", "Overdue", "INV-1044", "430.00", "GBP",
  "INV-1045", "Umbrella Ltd", "7,775.10", "Stark Supply", "-1,050.00", "Refund", "Wayne Freight", "98.76", "INV-1048", "Hooli Cloud", "15,300.00"]))

# 9 Chat app, dark, timestamps and small names
SCENES.append(("chat_dark", page("""
body{background:#1a1d21;color:#d1d2d3;font:15px Segoe UI}.s{position:absolute;left:0;top:0;bottom:0;width:260px;background:#19171d;padding:20px;box-sizing:border-box;font-size:15px;line-height:2}
.m{position:absolute;left:300px;width:1100px}.m b{color:#fff}.m small{color:#8b8d90;font-size:12px;margin-left:8px}.m p{margin:3px 0 0}""", """
<div class=s><b>Workspace: Northside</b><br># general<br># design-review<br># incidents<br># random<br><br>Direct messages<br>Priya Nair<br>Tom&aacute;s Silva</div>
<div class=m style="top:60px"><b>Priya Nair</b><small>10:42 AM</small><p>Deploy of v2.8.1 is done, error rate back under 0.5%.</p></div>
<div class=m style="top:140px"><b>Tom&aacute;s Silva</b><small>10:44 AM</small><p>Great. Can someone check the dashboard at https://grafana.example.net/d/api-latency before standup?</p></div>
<div class=m style="top:220px"><b>Lee Chen</b><small>10:51 AM</small><p>p95 is 118 ms, inside the 120 ms budget. Ticket OPS-3391 can be closed.</p></div>
<div class=m style="top:300px"><b>Priya Nair</b><small>10:52 AM</small><p>Closing it now. Thanks both! Lunch at 12:30?</p></div>""",),
 ["Workspace: Northside", "# design-review", "# incidents", "Priya Nair", "10:42 AM", "Deploy of v2.8.1 is done", "error rate back under 0.5%", "https://grafana.example.net/d/api-latency",
  "before standup", "Lee Chen", "p95 is 118 ms", "within the 120 ms budget".replace("within", "inside"), "OPS-3391", "Lunch at 12:30"]))

# 10 Low contrast and small print
SCENES.append(("low_contrast", page("""
body{background:#e9e9e9;color:#9a9a9a;font-family:Arial}.a{position:absolute;left:200px;top:140px;font-size:28px;color:#8f8f8f}.b{position:absolute;left:200px;top:220px;font-size:16px;color:#a5a5a5;width:900px}
.c{position:absolute;left:200px;top:400px;font-size:11px;color:#8a8a8a;width:700px}.d{position:absolute;left:1000px;top:600px;font-size:60px;color:#dcdcdc;font-weight:900}""", """
<div class=a>Account settings saved</div><div class=b>Your preferences were updated on 27 September 2026 at 14:05. Changes may take up to 10 minutes to appear on all devices.</div>
<div class=c>Terms apply. Offer valid until 31/10/2026 while stocks last. Reference ID: 7F3A-92C1-0B44. Not available in all regions. See store for details.</div><div class=d>DRAFT</div>""",),
 ["Account settings saved", "27 September 2026 at 14:05", "up to 10 minutes", "all devices", "Offer valid until 31/10/2026", "while stocks last", "Reference ID: 7F3A-92C1-0B44", "Not available in all regions", "DRAFT"]))

# 11 Slide with bullet text and a bar chart
SCENES.append(("slide_chart", page("""
body{background:#fff}h1{position:absolute;left:100px;top:60px;font:700 64px Arial;color:#1f3a5f;margin:0}ul{position:absolute;left:110px;top:200px;font:32px Arial;line-height:1.7;color:#333;width:760px}
svg{position:absolute;left:960px;top:180px}""", """
<h1>Q3 Results Overview</h1><ul><li>Revenue up 18% year on year</li><li>Churn down to 2.1%</li><li>Launched in 4 new markets</li><li>Hiring plan: 35 engineers</li></ul>
<svg width=860 height=700 font-family="Arial"><g font-size=22 fill=#333><text x=20 y=40>Revenue by month (EUR k)</text></g>
<g fill=#2c7be5><rect x=60 y=420 width=120 height=200 /><rect x=260 y=330 width=120 height=290 /><rect x=460 y=220 width=120 height=400 /></g>
<g font-size=20 text-anchor=middle fill=#222><text x=120 y=410>412</text><text x=320 y=320>598</text><text x=520 y=210>861</text><text x=120 y=655>July</text><text x=320 y=655>August</text><text x=520 y=655>September</text></g>
<line x1=40 y1=620 x2=720 y2=620 stroke=#333 /></svg>""",),
 ["Q3 Results Overview", "Revenue up 18% year on year", "Churn down to 2.1%", "Launched in 4 new markets", "Hiring plan: 35 engineers", "Revenue by month (EUR k)", "412", "598", "861", "July", "August", "September"]))

# 12 Terminal, green on black, 0/O and path ambiguity
SCENES.append(("terminal", page("""
body{background:#0c0c0c;color:#16c60c;font:16px Consolas,monospace;padding:30px;box-sizing:border-box;white-space:pre;line-height:1.35}.w{color:#f9f1a5}.e{color:#e74856}""", """PS C:\\Users\\demo\\project&gt; git status
On branch feature/oauth-login
Your branch is ahead of 'origin/feature/oauth-login' by 3 commits.
<span class=w>modified:   src/auth/token_store.py</span>
<span class=w>modified:   tests/test_token_store.py</span>
PS C:\\Users\\demo\\project&gt; python -m pytest -q
<span class=e>FAILED tests/test_token_store.py::test_expiry_boundary - AssertionError: 3600 != 3599</span>
1 failed, 41 passed in 2.07s
PS C:\\Users\\demo\\project&gt; ping 10.0.0.1
Reply from 10.0.0.1: bytes=32 time=1ms TTL=64
PS C:\\Users\\demo\\project&gt; _""",),
 ["PS C:\\Users\\demo\\project", "git status", "On branch feature/oauth-login", "ahead of 'origin/feature/oauth-login' by 3 commits", "src/auth/token_store.py",
  "tests/test_token_store.py", "python -m pytest -q", "FAILED tests/test_token_store.py::test_expiry_boundary", "AssertionError: 3600 != 3599", "1 failed, 41 passed in 2.07s",
  "ping 10.0.0.1", "Reply from 10.0.0.1: bytes=32 time=1ms TTL=64"]))
