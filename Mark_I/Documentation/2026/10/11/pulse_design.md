# Pulse: the daily news digest (design, 2026-10-11, not built)

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
4. **Store:** `FRED_Private/pulse/YYYY/MM/DD.json` (items, links, summaries). Outside the repo, outside the personal memory.
5. **Run:** the existing job scheduler, once a day shortly before waking time (night-job slot, so the GPU is free).
6. **Deliver:** on first presence of the day (camera sees Vatsal) FRED offers: "Good morning, sir. I have eight stories. Want them?" The HUD shows the list. A `news` tool replays the stored digest at any time ("read me the news", "more on number three", "just the AI ones").

## Open (defaults used until Vatsal says otherwise)
- Ready time 06:30, spoken offer on first presence, one to two lines per story.
- Do the stories go into FRED's memory? Default no (it is a feed, not what Vatsal said).
- Which GitHub signal: trending repos, or releases of named repos? Default: skip until asked.
