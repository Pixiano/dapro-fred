# News: the daily news digest (design, 2026-10-11, not built)

## What Vatsal asked for
A task FRED does every day: find and read the news. Eight stories, in fixed slots:
1. 1-3 AI, whole world: new models, benchmarks, launches, controversies, Hugging Face, GitHub; at least one is a new feature of a product such as Claude.
2. 4-5 India, major ones only: politics, deaths, crimes, laws (for example a mass killing, a law affecting food prices).
3. 1 Boards: CBSE and NCERT (for example exam dates released).
4. 1-2 World: wars and big discussions (Iran, Russia and similar).
5. 1 anything general and interesting.

## Pipeline (one script, one job per day)
1. **Fetch** (stdlib `urllib` + `xml.etree`, no new dependency; `feedparser` is not installed). Feeds checked reachable on 2026-10-11:
   - India: The Hindu national, Indian Express India, NDTV top stories.
   - AI: Hugging Face blog, Hugging Face daily papers (JSON API), Hacker News front page (filtered to AI words), The Verge AI. GitHub trending is an HTML page (no feed): parsed only if wanted. Anthropic's news RSS URL returned 404: use the page or the web_search tool for lab announcements.
   - Boards: CBSE notices page (HTML, no feed) and the NCERT home page: fetch the page, diff against yesterday, and keep new notice titles.
   - World: BBC World, Al Jazeera.
2. **Pick per slot:** drop duplicates (same story from several feeds), score each item by embedding similarity to a short per-slot topic description (the embedder FRED already has), with a recency bonus; take the slot's quota. Fixed quotas make the mix match the list above whatever the day's volume.
3. **Summarise:** the local model gets only the headline and the feed snippet and writes one or two spoken lines per story. It has no tools: feed text is untrusted and must never reach anything that can act.
4. **Store:** `FRED_Private/news/YYYY/MM/DD.json` (items, links, summaries). Outside the repo, outside the personal memory.
5. **Run:** the existing job scheduler, once a day shortly before waking time (night-job slot, so the GPU is free).
6. **Deliver:** on first presence of the day (camera sees Vatsal) FRED offers: "Good morning, sir. I have eight stories. Want them?" The HUD shows the list. A `news` tool replays the stored digest at any time ("read me the news", "more on number three", "just the AI ones").

## Open (defaults used until Vatsal says otherwise)
- Ready time 06:30, spoken offer on first presence, one to two lines per story.
- Do the stories go into FRED's memory? Default no (it is a feed, not what Vatsal said).
- Which GitHub signal: trending repos, or releases of named repos? Default: skip until asked.

## Update (same day): APIs allowed, and the newsletter source
- Vatsal allows any method, not only RSS: APIs are fine.
- **Primary AI source: the "AI Snacks" email from "Humans at Magica"** (sender `humans@mail.galaxy.ai`, subject lines like "GPT-6 gets a glow-up"). Read once on 2026-10-11: 8 issues between 09-30 and 10-10, so near-daily on weekdays, not every day. Each issue: a short intro, a sponsor block ("Magica is the ONLY autonomous AI super agent...") to strip, then 5-8 items each with an emoji headline and 2-3 bullets (OpenAI revenue, lab firings, AI-powered hackers hitting banks, Anthropic cyber mission...). Cheap to parse: split on the emoji-headline lines, drop everything before the first item and any "sponsored"/"Join millions"/"Magica" block, and the unsubscribe footer.
  - Read through the existing Gmail transport (`tools/gmail`, read-only); the issue text is untrusted, summarised by the tool-less model only.
  - Rule: use the newest issue from the last 2 days; if none, the AI slot falls back to the feeds/APIs below. Never read the same issue twice (remember its message id in the day file).
- **API options added** (no key unless stated): Hugging Face daily papers JSON (`huggingface.co/api/daily_papers`, reachable), Hacker News via Algolia search API (AI keywords, points threshold), GitHub search API for newly created high-star AI repos and the releases of a short list of repos (unauthenticated limit is 60 requests/hour, plenty), GDELT for world and India headlines. A paid news API (NewsAPI, GNews) only if the free sources prove thin; needs a key from Vatsal.

## Decisions (Vatsal, 2026-10-11) and what was built
- Name and place: no "Pulse", no separate division; it is `news`, built inside Core (`Core/news/`, tool `Core/skills/extra/news/news/`).
- Editions at 06:00 and 18:00 (`config.news_hours`), twelve hours apart so a day covers 8+ topics twice. A missed slot is built at the next start (only the newest slot, never both).
- Every story is stored twice: a short spoken two-liner (one sentence for boards) and a detailed paragraph, with the source name and the link. The tool speaks the short ones; "more on number three" returns the paragraph, source and link.
- The news also goes into the memory (one event per story, type "news", importance 0.2 against the default 0.5, source named), so FRED remembers it with less weight; the paragraph stays in the day file `FRED_Private/news/YYYY/MM/DD.json`.
- GitHub: left out ("let git be"); only Hugging Face blog and daily papers, Hacker News, The Verge and the AI Snacks email feed the AI slots.
- Built and tested: `news/sources.py` (feeds, APIs, email parser), `news/digest.py` (quotas ai 3, india 2, boards 1, world 2, general 1; embedding pick and de-duplication; tool-less summariser; store; memory events), `news/service.py` (the clock and the spoken offer on first presence: "Good morning, sir. The 06:00 news is ready, 8 stories. Shall I read it?"), the `news` tool, wiring in `engine/live.py`.
- First real build (2026-10-11, 29 s, 9 stories, source names kept): AI 3 (all from AI Snacks), India 2 (Manipur violence, Ganga erosion), boards 1 (CBSE forms for private candidates), world 2, general 1. Typed checks: "What's the news today?", "Any CBSE news?" (called news and web_search), "Tell me more about number four": all worked.
- Open: the three AI slots all came from the newsletter because it carries a score bonus; if you want the mix with Hugging Face papers or Hacker News, lower `0.1` bonus in `digest.pick`. The yes after the spoken offer relies on the model calling the tool from the history; not yet tried by voice.

## Decision (Vatsal, 2026-10-11): an unread morning edition
If the 06:00 digest was not read, the 18:00 one is read first and FRED then asks whether he also wants the 06:00 one. Built: each edition's read state is kept in the day file (`read`), the tool marks an edition read when it reads it, `digest.earlier_unread()` finds the unread earlier edition, the spoken offer says so ("The 06:00 one is still unread: I'll read this first, then ask if you want that too"), and the tool tells the model to ask after reading and to call `news` with `edition: "earlier"` on a yes.
