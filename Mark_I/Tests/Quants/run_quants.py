"""Quant comparison: the live quants (brain Q6_K, talker Q8_0, embedder Q8_0, served by the playground on :8090) against Q4_K_M builds on private llama-servers.

  python run_quants.py embed | talk | brain     one stage; writes results_<stage>.json next to this file (replies included, for reading side by side)
Run with FRED stopped: the Q4 server needs VRAM next to the playground's loaded slots. Nothing here touches the playground slots or FRED's data; all texts are invented.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[1] / "Core"
sys.path.insert(0, str(CORE))

import config
from models.client import Chat, Embedder

LLAMA = "C:/Users/Dhiraj Vatsal/llama.cpp/b11433/llama-server.exe"
FILES = {"brain": "Qwen3.5-4B-Q4_K_M.gguf", "talk": "MiniCPM5-2B-Q4_K_M.gguf", "embed": "Qwen3-Embedding-0.6B-q4_k_m.gguf"}
PORT = {"brain": 8201, "talk": 8202, "embed": 8203}


def vram() -> int:
    return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.split()[0])


class Server:
    def __init__(self, stage, ctx):
        self.stage, self.ctx, self.url = stage, ctx, f"http://127.0.0.1:{PORT[stage]}"

    def __enter__(self):
        self.v0 = vram()
        cmd = [LLAMA, "-m", str(HERE / "models" / FILES[self.stage]), "-c", str(self.ctx), "-ngl", "99", "--port", str(PORT[self.stage]), "--host", "127.0.0.1",
               "--jinja", "-fa", "on", "-np", "1", "--no-webui", "-lm", "dio"] + (["--embedding", "--pooling", "last"] if self.stage == "embed" else [])
        self.proc = subprocess.Popen(cmd, stdout=open(HERE / f"llama-{self.stage}.log", "w"), stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 240:
            try:
                if urllib.request.urlopen(self.url + "/health", timeout=2).status == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Q4 server did not start; see llama-%s.log" % self.stage)
        self.vram_mb = vram() - self.v0
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        self.proc.wait(30)


def live(model, slot, ctx=None, vision=False):
    """Only this slot loaded in the playground (everything else unloaded so the GPU is free for the Q4 run that follows)."""
    from models.client import Playground
    pg = Playground()
    pg.unload()
    pg.load(model, slot, ctx, vision)
    return pg


def save(stage, data):
    (HERE / f"results_{stage}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "detail"}, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- embedder
FACTS = ["Maya's laptop charger is kept in the second drawer of the study desk.", "The chemistry exam is on the 14th of November in room 204.",
         "Arjun prefers black coffee without sugar.", "The Wi-Fi password was changed last Sunday by Dad.", "Riya moved to Pune in March for her internship.",
         "The bicycle tyre needs air every two weeks.", "Grandma's birthday dinner is planned at the Italian place near the station.", "The physics project partner is Kabir.",
         "The dentist appointment was moved to Thursday at 5 pm.", "The library book on thermodynamics is due on the 20th.", "The sofa in the living room is dark green.",
         "Mom takes the 7:40 train on weekdays.", "The router is in the hallway cupboard.", "The cricket practice runs from 6 to 8 on Saturdays.",
         "The science fair registration closes next Friday.", "Neel's phone number ends in 4417.", "The plant on the balcony is watered every third day.",
         "The family trip to Jaipur is booked for December.", "Priya is allergic to peanuts.", "The printer ink cartridge model is 805XL.",
         "The school bus arrives at 7:15 near the park gate.", "Sana won the maths olympiad in grade nine.", "The guitar lessons are on Wednesday evenings.",
         "The warranty on the washing machine ends in June.", "Uncle Raj lives in Bengaluru near the airport road.", "The new keyboard is a Keychron with brown switches.",
         "The history assignment is about the Mughal empire.", "The gym membership renews on the first of every month.", "The cat's vet is Dr Mehta.", "The monitor was bought in August."]
QUERIES = [("where do I keep the charger", 0), ("when is the chemistry test and which room", 1), ("how does Arjun take his coffee", 2), ("who changed the wifi password", 3),
           ("where did Riya go for work", 4), ("who is my physics project partner", 7), ("when is the dentist", 8), ("which book is due soon", 9),
           ("what time does mom's train leave", 11), ("where is the router", 12), ("when is cricket", 13), ("what is Priya allergic to", 18),
           ("which printer cartridge do I need", 19), ("where do I catch the school bus", 20), ("when does the gym fee renew", 27)]
EXTRA = ["Seearch for borards dates online for grade 10 please", "what is on my screen", "read me the news", "GOt it", "remind me to call mom at six", "what was I looking at around 3 pm",
         "how much battery do I have", "open spotify", "turn the volume down", "forget what I told you about the trip", "yes please", "no thanks"]


def cos(a, b):
    import numpy as np
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed_run(e, reg, examples, texts):
    import numpy as np
    from skills.rank_check import check
    t = time.time()
    v = [x for i in range(0, len(texts), 32) for x in e.embed(texts[i:i + 32])]
    secs = round(time.time() - t, 1)
    bad = [b for b in check(reg, e) if b[0] != b[2]]
    f = np.asarray(e.embed(FACTS), np.float32)
    hit1 = hit3 = 0
    for q, g in QUERIES:
        qv = np.asarray(e.embed(q, is_query=True), np.float32)
        order = list(np.argsort(-(f @ qv / (np.linalg.norm(f, axis=1) * np.linalg.norm(qv)))))
        hit1 += order[0] == g
        hit3 += g in order[:3]
    return np.asarray(v, np.float32), {"dim": len(v[0]), "seconds_for_all_texts": secs, "skill_examples_ranked_own_first": f"{len(examples) - len(bad)}/{len(examples)}",
                                       "losers": [(b[0], b[1], b[2]) for b in bad][:8], "recall@1": f"{hit1}/{len(QUERIES)}", "recall@3": f"{hit3}/{len(QUERIES)}"}


def stage_embed():
    import numpy as np
    from skills.loader import load
    reg, _ = load(ctx={"recall": None})
    examples = [ex for n in reg.names() for ex in reg.spec(n).examples]
    texts = examples + EXTRA + FACTS + [q for q, _ in QUERIES]
    out = {"examples": len(examples)}
    live(config.get().embedder, "embed")
    a, out["Q8_0 (live)"] = embed_run(Embedder(), reg, examples, texts)
    from models.client import Playground
    Playground().unload()
    with Server("embed", 8192) as srv:
        out["q4_vram_mb"] = srv.vram_mb
        b, out["Q4_K_M"] = embed_run(Embedder(model="q4", url=srv.url), reg, examples, texts)
    cs = [cos(x, y) for x, y in zip(a, b)]
    out["mean_cosine_q8_vs_q4_same_text"], out["min_cosine_q8_vs_q4_same_text"] = round(float(np.mean(cs)), 4), round(float(np.min(cs)), 4)
    sa, sb = a @ a.T, b @ b.T
    out["top5_neighbour_overlap_q8_vs_q4"] = round(float(np.mean([len(set(np.argsort(-sa[i])[1:6]) & set(np.argsort(-sb[i])[1:6])) / 5 for i in range(len(texts))])), 3)
    out["note"] = "the memory index and judge_model.json are built on Q8 vectors: a different embedder (any quant) means re-embedding the memory and retraining the judge"
    save("embed", out)


# ---------------------------------------------------------------- shared chat helpers
KNOW = [("What is the capital of Japan?", "tokyo"), ("Who wrote Hamlet?", "shakespeare"), ("What is the chemical symbol for gold?", "au"), ("Which planet is the largest in our solar system?", "jupiter"),
        ("What does CPU stand for?", "central processing unit"), ("How many days are in a leap year?", "366"), ("At what Celsius temperature does water boil at sea level?", "100"),
        ("What is the powerhouse of the cell?", "mitochondri")]
CHAT = ["Hello.", "How are you?", "Got it.", "Thanks a lot.", "Tell me something interesting about octopuses.", "Differentiate between accuracy and error in AI.",
        "What is the better metric if you had to choose one, accuracy or F1?", "Explain what a neural network is, simply.", "I am tired but I have to finish my homework. Any advice?",
        "Give me a one line summary of how photosynthesis works.", "Should I learn Python or C first? Pick one.", "What can you do?"]


def talk_to(chat, prompts, system, **kw):
    from engine import prompt
    out = []
    for p in prompts:
        t = time.time()
        r = chat.complete([{"role": "system", "content": system}, {"role": "user", "content": p}], think=False, **kw)
        dt = time.time() - t
        out.append({"q": p, "a": r.text, "words": len(r.text.split()), "finish": r.finish_reason, "tok": (r.usage or {}).get("completion_tokens"), "s": round(dt, 2)})
    return out


def summarize(rows, know=None):
    n = len(rows)
    s = {"avg_words": round(sum(r["words"] for r in rows) / n, 1), "empty": sum(1 for r in rows if not r["a"].strip()), "cut_by_length": sum(1 for r in rows if r["finish"] == "length"),
         "tool_markup_in_text": sum(1 for r in rows if "<tool_call" in r["a"] or "tool_call" in r["a"].lower()), "mentions_no_tools_or_cant": sum(1 for r in rows if any(x in r["a"].lower() for x in ("my tools", "available tools", "i can't choose", "i cannot choose")))}
    toks = [(r["tok"], r["s"]) for r in rows if r["tok"] and r["s"] > 0.3]
    s["tok_per_s"] = round(sum(t for t, _ in toks) / sum(d for _, d in toks), 1) if toks else None
    if know:
        s["facts_correct"] = f"{sum(1 for r, (_, k) in zip(rows, know) if k in r['a'].lower())}/{len(know)}"
    return s


def stage_talk():
    from engine import prompt
    system = prompt.build_system()
    out = {}
    live(config.get().talker, "talk")
    rows = talk_to(Chat(model=config.get().talker, slot="talk", temperature=0.2, max_tokens=config.get().talk_max_tokens), [q for q, _ in KNOW] + CHAT, system)
    out["Q8_0 (live)"] = {"facts": summarize(rows[:len(KNOW)], KNOW), "chat": summarize(rows[len(KNOW):]), "detail": rows}
    from models.client import Playground
    Playground().unload()
    with Server("talk", 8192) as srv:
        out["q4_vram_mb"] = srv.vram_mb
        rows = talk_to(Chat(model="q4", url=srv.url, temperature=0.2, max_tokens=config.get().talk_max_tokens), [q for q, _ in KNOW] + CHAT, system)
        out["Q4_K_M"] = {"facts": summarize(rows[:len(KNOW)], KNOW), "chat": summarize(rows[len(KNOW):]), "detail": rows}
    save("talk", out)


def brain_run(chat, reg, owned, tops, system, qa_chat):
    hit = inlist = 0
    miss = []
    t0 = time.time()
    for (n, ex), names in zip(owned, tops):
        if n not in names:
            continue
        inlist += 1
        try:
            r = chat.complete([{"role": "system", "content": system}, {"role": "user", "content": ex}], tools=reg.schemas(names), think=False)
            got = r.tool_calls[0].name if r.tool_calls else None
        except Exception as ex_:
            got = f"error:{type(ex_).__name__}"
        if got == n:
            hit += 1
        else:
            miss.append((n, ex, got))
    secs = round(time.time() - t0, 1)
    rows = talk_to(qa_chat, [q for q, _ in KNOW] + CHAT, system)
    return {"tool_call_correct": f"{hit}/{inlist} (cases whose skill the router put in its top 5)", "tool_seconds": secs, "first_misses": miss[:10],
            "facts": summarize(rows[:len(KNOW)], KNOW), "chat": summarize(rows[len(KNOW):]), "detail": rows}


def stage_brain():
    from engine import prompt
    from skills.loader import load
    from engine.classify import Router
    from models.client import Playground
    system = prompt.build_system()
    reg, _ = load(ctx={"recall": None})
    owned = [(n, ex) for n in reg.names() for ex in reg.spec(n).examples]
    live(config.get().embedder, "embed")
    rt = Router(reg, Embedder())
    tops = [[m for m, _ in rt.rank(ex)[:5]] for _, ex in owned]            # the live router (Q8 embedder) narrows the tools for both brains alike
    out = {"tool_cases": len(owned)}
    live(config.get().brain, "chat", None, False)
    out["Q6_K (live)"] = brain_run(Chat(model=config.get().brain, slot="chat", temperature=0.0, max_tokens=300), reg, owned, tops, system,
                                    Chat(model=config.get().brain, slot="chat", temperature=0.2, max_tokens=config.get().brain_max_tokens))
    Playground().unload()
    with Server("brain", 16000) as srv:
        out["q4_vram_mb"] = srv.vram_mb
        out["Q4_K_M"] = brain_run(Chat(model="q4", url=srv.url, temperature=0.0, max_tokens=300), reg, owned, tops, system,
                                   Chat(model="q4", url=srv.url, temperature=0.2, max_tokens=config.get().brain_max_tokens))
    save("brain", out)


if __name__ == "__main__":
    {"embed": stage_embed, "talk": stage_talk, "brain": stage_brain}[sys.argv[1]]()
