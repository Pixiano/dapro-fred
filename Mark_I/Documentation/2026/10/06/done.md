# 2026-10-06: ask queue, two-project answers, precedents, llama.cpp playground

## Ask queue (`askqueue.py`)
Every doubt that needs Vatsal (same-person questions, contradictions, merge/other proposals) waits in one queue and is asked as a short either/or question when a conversation winds down, each side described ("Nitu (came to tuition yesterday) and Nitya (lives in Pune): same one, or two different ones?"). One question per conversation end (longer chats trigger more often), at most 5 a day, each item at most twice, "ask me later" snoozes 3 days, the item closest to the topic goes first. An answer may carry a reason ("no, they live in different cities"). It closes the item everywhere and is written to the archive. Delivery (voice, pill popup with a text box, HUD) and the "conversation is ending" signal are integration work.

## Two projects fit one question (`recall.py`)
`judged_search` answers about every project the kept events belong to (`groups`) and offers a `follow_up` question. "fred" in the query only orders FRED's projects first, it never filters.

## Precedents (`precedents.py`)
What Vatsal decides is kept (situation without names/dates, options, choice, reason, domain, stakes, vector). FRED may settle a later case itself only when: at least 3 similar precedents (cosine >= 0.75, weight >= 2.0 with a one-year half-life) all chose the same, none opposed, the domain's autonomy level allows it, and the model's difference test finds nothing new in the case. Levels per domain: 0 ask, 1 suggest ("I'd say X, like your 3 earlier answers"), 2 settle and tell (digest line + `undo`); capped by stakes (same-person and merge proposals 2, contradictions 1, anything else 0). A level rises after 3 agreeing answers or confirmations and drops on one override (those precedents stop counting). Reasons are only Vatsal's own words or "none": a model-offered reason must be a verbatim part of what he said and carry real content. One-tap reason tags are offered only for a first-of-kind case or one that conflicts with an earlier precedent. `distill` turns a choice made 3+ times, with reasons given for at least 2, into one personal-model "decision" item. Default model: gpt-oss-20b at low reasoning (fallback Qwen3-8B).

## Measured (invented chats, real embedder and models through the playground)
- Real embedder: questions of one kind score 0.78-0.91 with each other, a different kind about 0.5, so the first cut-off 0.9 never matched; now 0.75 and the difference test carries the "what is new here" judgement.
- Full loop (6 answers, 3 suggestions confirmed, 3 settled by FRED, 1 planted nickname case): gpt-oss medium, gpt-oss low, Qwen3-8B and Gemma 4 12B all reached level 2, settled 3 of 3, and sent the planted case back as a question. Qwen3-8B invented a reason on 6 answers (all refused by the code); gpt-oss and Gemma none. Model time: gpt-oss low 58 s, medium 148 s, Qwen 151 s, Gemma 679 s (its difference test about 59 s a call). Gemma's distillation produced nothing.
- Bugs found by the runs and fixed: an answer with a reason was rejected as unclear; a bare "yeah same person" was accepted as a reason; the distilled pattern without reasons only repeated the data (now needs reasons); a stored reason kept the leading option word.
- Not tested: contradiction and merge-proposal domains with precedents, real history, delivery.

## Other domains (gpt-oss low)
- Merge proposals (domain `pair`): reached level 2, settled by FRED, planted nickname case asked.
- Contradictions: never left level 0 (13 answers): the wording varies too much to match, so FRED always asks. Safe, and precedents do not help there.
- Stale-fact proposals: first run settled a planted "allergy is out of date" case from earlier "school" answers (the difference test missed it). Fixed by giving each stale-fact field its own domain (`yesno:stale_fact:<field>`); rerun: the allergy case is asked, "school" reached level 2.
- Over-caution: the difference test sometimes flags a harmless case ("topic changed"), costing an extra question, never a wrong settlement.

## llama.cpp playground (`Mark_I/Initial/playground/`)
One OpenAI-compatible endpoint (http://localhost:8090/v1) and a web page in front of llama.cpp's `llama-server` (CUDA build b11433, outside the repo): chat with streaming and thinking pane, embeddings, load/unload, VRAM bar, register a model. Models load on demand and swap in one chat slot (plus one embedding slot), fully on the GPU. Standing rule: local models are run ONLY through it, one run at a time. Models load with `-lm dio` (direct IO): without it each llama-server held the whole model file in host RAM (13 GB for gpt-oss) and a run of three models exhausted 31 GB twice. Smoke test of Bonsai-27B, Gemma 4 12B and Qwen3.5-4B (text only): all load in 5-7 s and answer a schema-forced call correctly (63, 53 and 97 tok/s).

## Other fixes
`personal_model.py` self-check repaired (broken by the earlier duplicate-evidence guard); weekly digest footer lists doubts FRED settled itself.
