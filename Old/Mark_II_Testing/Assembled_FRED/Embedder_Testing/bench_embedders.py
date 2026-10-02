"""Head-to-head: FRED's embedder Qwen3-Embedding-4B (Q4_K_M) vs Qwen3-Embedding-0.6B (Q8_0).

    <Core venv python> bench_embedders.py selftest   # metric sanity check, no models
    <Core venv python> bench_embedders.py embed      # phase 1: embed every text each eval needs (GPU, cached on disk)
    <Core venv python> bench_embedders.py rerank     # phase 2: Qwen3-Reranker-0.6B scores for the 0.6B top-20 candidates
    <Core venv python> bench_embedders.py eval       # phase 3: all metrics -> results.json (numpy only)
    <Core venv python> bench_embedders.py latency    # VRAM / load time / latency / throughput per config
    <Core venv python> bench_embedders.py report     # docs/2026/10/01/embedder_4b_vs_06b.md

Nothing under Core/ is edited. Core modules that are pure (tool_router, intent, dispatcher, vault_md) are loaded
read-only so the routing numbers use FRED's own code. Vault text is only ever held in memory and in hash-keyed
caches under the job tmp dir; per-case vault data never enters the repo.
"""
import hashlib
import importlib.util
import json
import math
import os
import pickle
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORE = ROOT / "Core"
TMP = Path(os.environ.get("EMB_TMP", r"C:\Users\Dhiraj Vatsal\.claude\jobs\e9eb367b\tmp"))
CACHE = TMP / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
SEED = 20261001
RESULTS = HERE / "results.json"
DOC = ROOT / "docs" / "2026" / "10" / "01" / "embedder_4b_vs_06b.md"

EMB4B = Path(r"C:\Users\Dhiraj Vatsal\.lmstudio\models\Qwen\Qwen3-Embedding-4B-GGUF\Qwen3-Embedding-4B-Q4_K_M.gguf")
EMB06 = HERE / "models" / "Qwen3-Embedding-0.6B-Q8_0.gguf"
RERANK = HERE / "models" / "Qwen3-Reranker-0.6B.Q8_0.gguf"
VAULT_DIR = Path(r"C:\Users\Dhiraj Vatsal\VatsalDaPro\Projects\1_FRED_Memory\FRED")
CFG = {"4B_ctx4096": (EMB4B, 4096), "4B_ctx1024": (EMB4B, 1024), "06B_ctx4096": (EMB06, 4096)}
FRED_INSTR = "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: {query}"
JOB = {
    "tools": "Given a user request to a voice assistant, retrieve the description of the tool that should handle it",
    "memory": "Given a question, retrieve the earlier conversation turn that contains the answer",
    "vault": "Given a question about the user's life, goals or setup, retrieve the notes section that answers it",
    "general": "Given a web search query, retrieve relevant passages that answer the query",
    "scifact": "Given a scientific claim, retrieve abstracts that support or refute the claim",
}
TOPN = 20


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def h(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- core module loading (read-only)
def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def core_modules():
    sys.path.insert(0, str(CORE))
    tr = load_file("tool_router_ro", CORE / "orchestrator" / "tool_router.py")
    from orchestrator import intent, dispatcher  # noqa: pure regex modules
    vmd = load_file("vault_md_ro", CORE / "utils" / "vault_md.py")
    return tr, intent, dispatcher, vmd


# ----------------------------------------------------------------------------- metrics
def rank_of_best(S, gold):
    """1-based rank of the best-ranked gold doc per query. Ties are counted AGAINST the gold doc (pessimistic), so
    saturated reranker scores or equal RRF sums can never inflate a metric."""
    out = []
    n = S.shape[1]
    for i, g in enumerate(gold):
        g = list(g)
        best = S[i, g].max()
        ng = np.ones(n, bool); ng[g] = False
        out.append(int((S[i][ng] >= best).sum()) + 1)
    return out


def ndcg10(S, gold):
    vals = []
    n = S.shape[1]
    for i, g in enumerate(gold):
        ng = np.ones(n, bool); ng[list(g)] = False
        ranks = sorted(int((S[i][ng] >= S[i, d]).sum()) + sum(1 for e in g if e != d and S[i, e] > S[i, d]) + 1 for d in g)
        dcg = sum(1 / math.log2(r + 1) for r in ranks if r <= 10)
        idcg = sum(1 / math.log2(k + 2) for k in range(min(len(g), 10)))
        vals.append(dcg / idcg)
    return vals


def summarize(ranks, cats=None, nd=None):
    r = np.array(ranks)
    d = {"n": int(len(r)), "r1": float((r <= 1).mean()), "r3": float((r <= 3).mean()), "r5": float((r <= 5).mean()),
         "mrr": float((1.0 / r).mean())}
    if nd is not None:
        d["ndcg10"] = float(np.mean(nd))
    if cats is not None:
        d["by_cat"] = {}
        for c in sorted(set(cats)):
            m = np.array([x == c for x in cats])
            rc = r[m]
            d["by_cat"][c] = {"n": int(m.sum()), "r1": float((rc <= 1).mean()), "r3": float((rc <= 3).mean()),
                              "r5": float((rc <= 5).mean()), "mrr": float((1.0 / rc).mean())}
    return d


def norm(M):
    return M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-8)


def dense(Q, D, center):
    if center:
        m = D.mean(0)
        Q, D = Q - m, D - m
    return norm(Q) @ norm(D).T


STOP = set("a an the of to in on at for from by with and or but is are was were be been am do does did have has had i me my we our you your it its this that these those what which who whom how when where why can could should would will shall may might not no if then than as into about there their they he she him her his hers them so up out over just also any some more most very".split())


def tok(t):
    return [w for w in re.findall(r"\w+", t.lower()) if w not in STOP]


def bm25(queries, docs, k1=1.5, b=0.75):
    from scipy.sparse import csr_matrix
    vocab, rows, cols, vals = {}, [], [], []
    dl = []
    for i, d in enumerate(docs):
        toks = tok(d)
        dl.append(len(toks))
        cnt = {}
        for t in toks:
            cnt[t] = cnt.get(t, 0) + 1
        for t, c in cnt.items():
            j = vocab.setdefault(t, len(vocab))
            rows.append(i), cols.append(j), vals.append(c)
    n, V = len(docs), len(vocab)
    tf = csr_matrix((vals, (rows, cols)), shape=(n, V), dtype=np.float64)
    df = np.asarray((tf > 0).sum(0)).ravel()
    idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
    avg = np.mean(dl)
    tf = tf.tocoo()
    w = tf.data * (k1 + 1) / (tf.data + k1 * (1 - b + b * np.array(dl)[tf.row] / avg)) * idf[tf.col]
    W = csr_matrix((w, (tf.row, tf.col)), shape=(n, V))
    qr, qc = [], []
    for i, q in enumerate(queries):
        for t in set(tok(q)):
            if t in vocab:
                qr.append(i), qc.append(vocab[t])
    Qm = csr_matrix((np.ones(len(qr)), (qr, qc)), shape=(len(queries), V))
    return np.asarray((Qm @ W.T).todense())


def ranks_matrix(S):
    return np.argsort(np.argsort(-S, axis=1), axis=1) + 1


def rrf(*Ss, k=60):
    return sum(1.0 / (k + ranks_matrix(S)) for S in Ss)


def restrict(S_base, S_new, topn=TOPN):
    """Candidates = top-n of S_base; order them by S_new; everything else keeps S_base order below them."""
    out = S_base.astype(np.float64) - 1e6  # float64: in float32 a 1e6 offset collapses scores into ties
    idx = np.argsort(-S_base, axis=1)[:, :topn]
    for i in range(S_base.shape[0]):
        out[i, idx[i]] = S_new[i, idx[i]].astype(np.float64) + 1e6
    return out


def selftest():
    S = np.array([[0.9, 0.1, 0.5], [0.1, 0.2, 0.3], [0.5, 0.5, 0.4]])
    assert rank_of_best(S, [{0}, {0}, {2}]) == [1, 3, 3], rank_of_best(S, [{0}, {0}, {2}])
    assert rank_of_best(S, [{1, 2}, {0, 1}, {0}]) == [2, 2, 2]  # best of multiple gold; a tie with a non-gold doc counts against gold
    s = summarize([1, 2, 6, 3], ["a", "a", "b", "b"])
    assert (s["r1"], s["r3"], s["r5"]) == (0.25, 0.75, 0.75) and abs(s["mrr"] - (1 + .5 + 1 / 6 + 1 / 3) / 4) < 1e-9
    assert s["by_cat"]["a"]["r3"] == 1.0 and s["by_cat"]["b"]["r5"] == 0.5
    nd = ndcg10(np.array([[3.0, 2.0, 1.0]]), [{0, 1}])
    assert abs(nd[0] - 1.0) < 1e-9
    B = bm25(["apple pie", "zebra"], ["apple pie recipe", "banana bread", "zebra crossing apple"])
    assert B[0].argmax() == 0 and B[1].argmax() == 2
    R = rrf(np.array([[3., 2., 1.]]), np.array([[1., 2., 3.]]))
    assert abs(R[0, 1] - 2 / 62) < 1e-9 and abs(R[0, 0] - R[0, 2]) < 1e-12
    r = restrict(np.array([[3., 2., 1., 0.]]), np.array([[0., 5., 9., 100.]]), topn=3)
    assert list(np.argsort(-r[0])) == [2, 1, 0, 3]  # reranked inside top-3, doc 3 stays last
    f32 = restrict(np.array([[.71, .70, .69, .1]], dtype=np.float32), np.array([[.70, .71, .69, 0.]], dtype=np.float32), topn=3)
    assert len(set(f32[0].tolist())) == 4 and int(np.argmax(f32[0])) == 1  # no float32 ties
    D = np.array([[1., 0.], [0., 1.]]); Q = np.array([[2., 0.1]])
    assert dense(Q, D, False).argmax() == 0
    log("selftest OK")


# ----------------------------------------------------------------------------- datasets
class Ev:
    def __init__(self, name, queries, cats, gold, docs, center, job, extra=None):
        self.name, self.queries, self.cats, self.gold, self.docs = name, queries, cats, gold, docs
        self.center, self.job, self.extra = center, job, extra or {}


def qtext(q, qv, job):
    if qv == "fred":
        return FRED_INSTR.format(query=q)
    if qv == "job":
        return f"Instruct: {JOB[job]}\nQuery: {q}"
    return q


def tools_ev():
    tr, intent, disp, _ = core_modules()
    tools = json.load(open(HERE / "data" / "tools.json", encoding="utf-8"))
    names = list(tools)
    docs = [f"{n.replace('_', ' ')}. {tools[n]} " + " ".join(tr.TOOL_EXAMPLES.get(n, [])) for n in names]  # tool_router.build()
    queries, cats, gold, meta = [], [], [], []
    for l in open(ROOT / "benchmarking/cases/routing_hard.jsonl", encoding="utf-8"):
        k = json.loads(l)
        if k["tools"]:
            queries.append(k["text"]); cats.append(k["tag"]); gold.append({names.index(t) for t in k["tools"]})
            meta.append({"src": "routing_hard", "forbid": k.get("must_not_call", []), "allow": k.get("allow", []), "all": k.get("all", False),
                         "tools": k["tools"]})
    at = {"researcher": "delegate_research", "general": "delegate_general"}
    for l in open(ROOT / "benchmarking/cases/agents_memory.jsonl", encoding="utf-8"):
        k = json.loads(l)
        ea = k.get("expect_agent")
        if "delegation" in k.get("needs", []) and isinstance(ea, str):
            queries.append(k["text"]); cats.append("delegation"); gold.append({names.index(at[ea])})
            meta.append({"src": "agents_memory", "forbid": [], "allow": [], "all": False, "tools": [at[ea]]})
    for l in open(ROOT / "benchmarking/cases/conversation.jsonl", encoding="utf-8"):
        k = json.loads(l)
        if k.get("tools"):
            g = {names.index(t) for t in k["tools"] if t in names}
            if not g:
                continue
            queries.append(k["text"]); cats.append("conversation"); gold.append(g)
            meta.append({"src": "conversation", "forbid": [], "allow": [], "all": False, "tools": k["tools"]})
    ex = {re.sub(r"[^a-z0-9 ]", "", e.lower()).strip() for v in tr.TOOL_EXAMPLES.values() for e in v}
    novel = [re.sub(r"[^a-z0-9 ]", "", q.lower()).strip() not in ex for q in queries]
    return Ev("tools", queries, cats, gold, {"main": docs}, False, "tools",
              {"names": names, "meta": meta, "novel": novel, "descriptions": tools, "examples": tr.TOOL_EXAMPLES})


def memory_ev():
    d = json.load(open(HERE / "data" / "memory_eval.json", encoding="utf-8"))
    docs = [f["text"] for f in d["facts"]] + d["distractors"]
    return Ev("memory", [q["q"] for q in d["questions"]], [q["cat"] for q in d["questions"]], [{q["fact"]} for q in d["questions"]],
              {"main": docs}, False, "memory")


def general_ev():
    from general_pairs import PAIRS, FILLER
    rng = random.Random(SEED)
    queries, cats, gold, docs = [], [], [], []
    for cat, q, p in PAIRS:
        if cat == "L":
            fill = rng.sample(FILLER, 9)
            pos = rng.randint(0, 9)
            p = " ".join(fill[:pos] + [p] + fill[pos:])
            cat = "long"
        docs.append(p); queries.append(q); cats.append(cat); gold.append({len(docs) - 1})
    n_auth = len(docs)
    sf = load_scifact(rng)
    dis = sf["distractors"][:1000]
    return Ev("general", queries, cats, gold, {"main": docs + dis}, False, "general", {"n_authored": n_auth})


def load_scifact(rng=None):
    rng = rng or random.Random(SEED)
    d = TMP / "scifact"
    corpus = {}
    for l in open(d / "corpus.jsonl", encoding="utf-8"):
        r = json.loads(l); corpus[r["_id"]] = (r.get("title", ""), r["text"])
    qrels = {}
    for i, l in enumerate(open(d / "qrels" / "test.tsv", encoding="utf-8")):
        if i == 0:
            continue
        q, c, s = l.split()
        if int(s) > 0:
            qrels.setdefault(q, set()).add(c)
    queries = {}
    for l in open(d / "queries.jsonl", encoding="utf-8"):
        r = json.loads(l); queries[r["_id"]] = r["text"]
    qids = sorted(qrels, key=int)
    rel = sorted({c for q in qids for c in qrels[q]}, key=int)
    others = sorted(set(corpus) - set(rel), key=int)
    rng2 = random.Random(SEED)
    rng2.shuffle(others)
    keep = rel + others[:1500]
    keep.sort(key=int)
    idx = {c: i for i, c in enumerate(keep)}
    distract = [corpus[c][1][:1200] for c in others[1500:2500]]
    ev = Ev("scifact", [queries[q] for q in qids], ["scifact"] * len(qids), [{idx[c] for c in qrels[q]} for q in qids],
            {"main": [corpus[c][1] for c in keep], "title": [f"{corpus[c][0]}\n{corpus[c][1]}" for c in keep]}, False, "scifact")
    return {"ev": ev, "distractors": distract}


def scifact_ev():
    return load_scifact()["ev"]


# ---- vault (PRIVATE): everything below stays in memory; only aggregate numbers are ever written
GENERIC_HEADINGS = {"overview", "summary", "notes", "status", "goals", "plan", "history", "background", "details", "next", "steps", "current",
                    "todo", "context", "purpose", "about", "intro", "links", "contents", "description", "timeline", "tasks", "ideas", "log",
                    "reference", "references", "resources", "rules", "contact", "info", "list", "progress", "priorities", "projects", "setup"}


def vault_chunks(vmd):
    skip = {"persona.md", "profile.md", "rules.md", "active-priorities.md", "_TEMPLATE.md", "contacts.md"}
    out = []  # (rel, heading, body, embed_text, plain_text)
    if not VAULT_DIR.exists():
        return out
    for path in sorted(VAULT_DIR.rglob("*")):
        if path.suffix.lower() not in (".md", ".pdf") or not path.is_file():
            continue
        rel = str(path.relative_to(VAULT_DIR)).replace("\\", "/")
        if any(path.name.startswith(p) for p in ("whatsapp-tiers-",)) or path.name in skip or "vectors" in rel.split("/"):
            continue
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
                for n, pg in enumerate(PdfReader(str(path)).pages, 1):
                    t = (pg.extract_text() or "").strip()
                    if t:
                        out.append((rel, f"page {n}", t, f"{rel} — page {n}\n{t}", t))
            except Exception:
                pass
            continue
        raw = path.read_text(encoding="utf-8")
        body = vmd.strip_frontmatter(raw)
        title = vmd.extract_h1_title(body) or rel
        for heading, text in vmd.split_sections(body):
            et = f"{title} — {heading}\n{text}" if heading not in ("(whole file)", "(intro)") else f"{title}\n{text}"
            out.append((rel, heading, text, et, text))
    return out


def vault_ev(with_priorities=False):
    _, _, _, vmd = core_modules()
    ch = vault_chunks(vmd)
    pr = []
    if with_priorities:
        p = VAULT_DIR / "active-priorities.md"
        if p.exists():
            body = vmd.strip_frontmatter(p.read_text(encoding="utf-8"))
            title = vmd.extract_h1_title(body) or "active-priorities.md"
            for heading, text in vmd.split_sections(body):
                et = f"{title} — {heading}\n{text}" if heading not in ("(whole file)", "(intro)") else f"{title}\n{text}"
                pr.append(("active-priorities.md", heading, text, et, text))
    ch = ch + pr
    files = sorted({c[0] for c in ch})
    queries, cats, gold = [], [], []
    by_file = {}
    for i, c in enumerate(ch):
        by_file.setdefault(c[0], []).append(i)
    for f in files:
        if f == "active-priorities.md":
            continue
        stem = Path(f).stem
        if not re.fullmatch(r"[a-z][a-z\-]*", stem):  # skip dated / numeric names
            continue
        words = stem.replace("-", " ")
        queries.append(f"what do I have about {words}"); cats.append("file"); gold.append(set(by_file[f]))
    nfile = len(queries)
    for i, c in enumerate(ch):
        stem = Path(c[0]).stem
        hw = re.findall(r"[a-z]+", c[1].lower())
        if c[0] != "active-priorities.md" and hw and set(hw) <= GENERIC_HEADINGS and re.fullmatch(r"[a-z][a-z\-]*", stem):
            queries.append(f"{stem.replace('-', ' ')} {c[1].lower()}"); cats.append("heading"); gold.append({i})
    hist = []
    if with_priorities:
        pri = set(by_file.get("active-priorities.md", []))
        mach = set(by_file.get("reference/machine.md", []))
        for q in ("what are my priorities", "what are my current priorities"):
            hist.append((q, pri, "priorities"))
        if mach:
            hist.append(("what are my machine specs", mach, "machine"))
    return Ev("vault_hp" if with_priorities else "vault", queries, cats, gold,
              {"main": [c[3] for c in ch], "noprefix": [c[4] for c in ch]}, True, "vault",
              {"nfile": nfile, "nchunks": len(ch), "nfiles": len(files), "hist": hist})


def all_evals(with_vault=True):
    evs = {"tools": tools_ev(), "memory": memory_ev(), "general": general_ev(), "scifact": scifact_ev()}
    if with_vault:
        v = vault_ev(False)
        evs["vault"] = v
        evs["vault_hp"] = vault_ev(True)
    return evs


# ----------------------------------------------------------------------------- embedding cache / model
class Cache:
    def __init__(self, tag):
        self.p = CACHE / f"{tag}.pkl"
        self.d = pickle.load(open(self.p, "rb")) if self.p.exists() else {}

    def save(self):
        tmp = self.p.with_suffix(".tmp")
        pickle.dump(self.d, open(tmp, "wb"))
        tmp.replace(self.p)


def vram_used():
    o = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return int(o.strip().splitlines()[0])


def load_llama(path, n_ctx, embedding=True):
    sys.path.insert(0, str(CORE))
    from utils.gpu_bootstrap import ensure_cuda_dlls
    ensure_cuda_dlls()
    from llama_cpp import Llama
    before = vram_used()
    t = time.perf_counter()
    kw = dict(model_path=str(path), n_ctx=n_ctx, n_gpu_layers=-1, verbose=False)  # same args as MemoryManager
    if embedding:
        kw["embedding"] = True
    llm = Llama(**kw)
    dt = time.perf_counter() - t
    after = vram_used()
    if after - before < 0.5 * path.stat().st_size / 2**20:
        raise SystemExit(f"model does not appear to be on the GPU (VRAM delta {after - before} MiB) -- refusing to run on CPU")
    return llm, dt, after - before


def embed_all(tag, texts):
    path, ctx = CFG[tag]
    cache = Cache(tag)
    need = [t for t in dict.fromkeys(texts) if h(t) not in cache.d]
    log(tag, f"{len(need)} texts to embed ({len(set(texts))} unique)")
    if not need:
        return
    llm, dt, dv = load_llama(path, ctx)
    log(tag, f"loaded in {dt:.1f}s, +{dv} MiB VRAM")
    t0 = time.perf_counter()
    for i, t in enumerate(need):
        cache.d[h(t)] = np.asarray(llm.create_embedding(t)["data"][0]["embedding"], dtype=np.float32)
        if i % 500 == 499:
            cache.save(); log(tag, f"{i + 1}/{len(need)} ({(i + 1) / (time.perf_counter() - t0):.1f}/s)")
    cache.save()
    llm.close()
    log(tag, f"done {len(need)} in {time.perf_counter() - t0:.0f}s")


def texts_for(ev):
    T = list(ev.docs["main"])
    for k, v in ev.docs.items():
        if k != "main":
            T += v
    for qv in ("fred", "none", "job"):
        T += [qtext(q, qv, ev.job) for q in ev.queries]
    if ev.name == "tools":
        for v in ev.extra["examples"].values():
            T += [FRED_INSTR.format(query=e) for e in v]
        T += [FRED_INSTR.format(query=q) for q in ev.queries]
    if ev.name == "vault_hp":
        for q, _, _ in ev.extra["hist"]:
            for qv in ("fred", "none", "job"):
                T.append(qtext(q, qv, "vault"))
    return T


def phase_embed():
    evs = all_evals()
    T = []
    for ev in evs.values():
        T += texts_for(ev)
    T += ["dimension probe"]
    log("unique texts", len(set(T)))
    for tag in ("06B_ctx4096", "4B_ctx4096"):
        embed_all(tag, T)
    # ctx 1024 is identical by construction (llama-cpp-python truncates to n_batch=512 tokens, not n_ctx); verify on a sample
    rng = random.Random(SEED)
    sample = rng.sample(sorted(set(T)), 400)
    c4096 = Cache("4B_ctx4096")
    embed_all("4B_ctx1024", sample)
    c1024 = Cache("4B_ctx1024")
    diffs = [float(np.abs(c1024.d[h(t)] - c4096.d[h(t)]).max()) for t in sample]
    cos = [float(np.dot(norm(c1024.d[h(t)][None])[0], norm(c4096.d[h(t)][None])[0])) for t in sample]
    info = {"n": len(sample), "max_abs_diff": max(diffs), "min_cos": min(cos)}
    json.dump(info, open(HERE / "ctx1024_check.json", "w"))
    log("ctx1024 vs ctx4096 embedding check", info)


# ----------------------------------------------------------------------------- reranker
RR_SYS = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. '
          'Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
RR_END = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class Reranker:
    def __init__(self, cache_name="reranker"):
        self.llm, self.load_s, self.vram = load_llama(RERANK, 4096, embedding=False)
        self.yes = self.llm.tokenize(b"yes", add_bos=False)[0]
        self.no = self.llm.tokenize(b"no", add_bos=False)[0]
        self.cache = Cache(cache_name)

    def score(self, instr, q, doc):
        key = h(instr + "\x00" + q + "\x00" + doc)
        if key in self.cache.d:
            return self.cache.d[key]
        text = RR_SYS + f"<Instruct>: {instr}\n<Query>: {q}\n<Document>: {doc[:5000]}" + RR_END
        toks = self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)[:4000]
        self.llm.reset()
        self.llm.eval(toks)
        import llama_cpp
        ptr = llama_cpp.llama_get_logits_ith(self.llm._ctx.ctx, -1)  # last position (logits_all is off)
        lg = np.ctypeslib.as_array(ptr, shape=(self.llm.n_vocab(),))
        a, b = float(lg[self.no]), float(lg[self.yes])
        p = b - a  # log-odds of "yes"; probabilities saturate at 1.0 and would create ties
        self.cache.d[key] = p
        return p


def mats(ev, tag, qv="fred", center=None, dv="main"):
    c = Cache(tag)
    center = ev.center if center is None else center
    D = np.stack([c.d[h(t)] for t in ev.docs[dv]])
    Q = np.stack([c.d[h(qtext(q, qv, ev.job))] for q in ev.queries])
    return Q, D


def base_scores(ev, tag="06B_ctx4096", **kw):
    center = kw.pop("center", None)
    Q, D = mats(ev, tag, **kw)
    return dense(Q, D, ev.center if center is None else center)


def a_scores(ev, tag, center=None, job=True, hybrid=True, title=True):
    dv = "title" if (title and "title" in ev.docs) else "main"
    S = base_scores(ev, tag, qv="job" if job else "fred", dv=dv, center=True if center is None else center)
    if not hybrid:
        return S
    B = bm25(ev.queries, ev.docs["main"] if dv == "main" else ev.docs[dv])
    return rrf(S, B)


def rr_pairs(ev, S):
    idx = np.argsort(-S, axis=1)[:, :TOPN]
    return idx


def phase_rerank():
    # usage: rerank [eval names...]  -- one process per subset, each writes its own cache file (merged in phase_eval)
    only = sys.argv[2:] or None
    evs = all_evals()
    rr = Reranker("reranker_" + "_".join(only) if only else "reranker")
    log(f"reranker loaded {rr.load_s:.1f}s +{rr.vram} MiB")
    # sanity: an obvious pair must rank yes > no
    good = rr.score("Given a web search query, retrieve relevant passages that answer the query", "capital of France", "Paris is the capital of France.")
    bad = rr.score("Given a web search query, retrieve relevant passages that answer the query", "capital of France", "Bananas are rich in potassium.")
    log(f"reranker sanity log-odds(yes): relevant {good:.2f} vs irrelevant {bad:.2f}")
    assert good > 5 and bad < -5, "reranker does not score sensibly"
    json.dump({"relevant": good, "irrelevant": bad}, open(HERE / "reranker_sanity.json", "w"))
    t0, n = time.perf_counter(), 0
    for name, ev in evs.items():
        if name == "vault_hp" or (only and name not in only):
            continue
        cand = set()
        S1 = base_scores(ev)
        S2 = a_scores(ev, "06B_ctx4096")
        A1, A2 = rr_pairs(ev, S1), rr_pairs(ev, S2)
        docs = ev.docs["main"]
        for i, q in enumerate(ev.queries):
            for j in set(A1[i]) | set(A2[i]):
                rr.score(JOB[ev.job], q, docs[j]); n += 1
            if i % 50 == 49:
                rr.cache.save(); log(name, i + 1, f"{n / (time.perf_counter() - t0):.1f} pairs/s")
        rr.cache.save()
        log(name, "done")
    rr.llm.close()


# ----------------------------------------------------------------------------- evaluation
def metrics(ev, S, mask=None):
    gold, cats = ev.gold, ev.cats
    if mask is not None:
        S = S[mask]; gold = [g for g, m in zip(gold, mask) if m]; cats = [c for c, m in zip(cats, mask) if m]
    r = rank_of_best(S, gold)
    nd = ndcg10(S, gold) if ev.name in ("scifact",) else None
    d = summarize(r, cats, nd)
    en = [i for i, c in enumerate(cats) if c != "hinglish"]  # headline = English-only (standing instruction); Hinglish reported in by_cat
    d["en"] = summarize([r[i] for i in en], None, [nd[i] for i in en] if nd is not None else None)
    d["ranks"] = r
    return d


def phase_eval():
    evs = all_evals()
    res = {"meta": {"seed": SEED, "topn": TOPN, "sizes": {k: {"queries": len(e.queries), "docs": len(e.docs["main"])} for k, e in evs.items()}}, "evals": {}}
    rrc = Cache("reranker")
    for f in CACHE.glob("reranker_*.pkl"):
        rrc.d.update(pickle.load(open(f, "rb")))
    tags = ["4B_ctx4096", "4B_ctx1024", "06B_ctx4096"]
    ctx1024_same = True  # vectors are shared; see ctx1024_check.json
    for name, ev in evs.items():
        E = {}
        for tag in tags:
            t = "4B_ctx4096" if tag == "4B_ctx1024" else tag
            E[tag] = {"plain": metrics(ev, base_scores(ev, t))}
        # sensitivity (E): instruction x centering, on the plain model of each size
        for tag in ("4B_ctx4096", "06B_ctx4096"):
            for qv in ("fred", "none", "job"):
                for c in (False, True):
                    E[tag][f"sens:{qv}|center={int(c)}"] = metrics(ev, base_scores(ev, tag, qv=qv, center=c))
            # fix recipe components, applied to both models (A)
            E[tag]["bm25_only"] = metrics(ev, bm25(ev.queries, ev.docs["main"]))
            E[tag]["A:job+center"] = metrics(ev, a_scores(ev, tag, hybrid=False, title=False))
            E[tag]["A:bm25rrf(fred cfg)"] = metrics(ev, rrf(base_scores(ev, tag), bm25(ev.queries, ev.docs["main"])))
            E[tag]["A:title"] = metrics(ev, a_scores(ev, tag, job=False, hybrid=False, center=ev.center)) if "title" in ev.docs else None
            E[tag]["A:all"] = metrics(ev, a_scores(ev, tag))
            if "noprefix" in ev.docs:
                E[tag]["vault:no heading prefix"] = metrics(ev, base_scores(ev, tag, dv="noprefix"))
        # upper bound: 0.6B top-20 re-ranked by the 4B embedding (ordering inside the 20 only)
        S06, S4 = base_scores(ev, "06B_ctx4096"), base_scores(ev, "4B_ctx4096")
        E["06B_ctx4096"]["R4:top20(plain)->4B"] = metrics(ev, restrict(S06, S4))
        SA = a_scores(ev, "06B_ctx4096")
        E["06B_ctx4096"]["R4:top20(A)->4B"] = metrics(ev, restrict(SA, S4))
        E["06B_ctx4096"]["recall@20 plain"] = float(np.mean([r <= TOPN for r in rank_of_best(S06, ev.gold)]))
        E["06B_ctx4096"]["recall@20 A"] = float(np.mean([r <= TOPN for r in rank_of_best(SA, ev.gold)]))
        # C: embed -> Qwen3-Reranker-0.6B on top-20
        if name != "vault_hp":
            try:
                R1 = rr_scores_safe(ev, S06, rrc); E["06B_ctx4096"]["C:top20(plain)->rerank06"] = metrics(ev, restrict(S06, R1))
                R2 = rr_scores_safe(ev, SA, rrc); E["06B_ctx4096"]["C:top20(A)->rerank06"] = metrics(ev, restrict(SA, R2))
            except KeyError:
                E["06B_ctx4096"]["C:error"] = "reranker cache incomplete"
        if name == "tools":
            E.update(tools_extra(ev, tags))
            E["06B_ctx4096"]["F:head(source holdout)"] = head_eval(ev, split="source")
            E["06B_ctx4096"]["F:head(5-fold on routing_hard)"] = head_eval(ev, split="cv")
        if name == "vault_hp":
            E["historical"] = historical(ev)
        res["evals"][name] = E
        log(name, "done")
    res["ctx1024_check"] = json.load(open(HERE / "ctx1024_check.json"))
    out = res
    # vault per-case ranks stay out of the repo
    vault_private = {}
    for nm in ("vault", "vault_hp"):
        for tag, d in res["evals"][nm].items():
            if isinstance(d, dict):
                for k, m in d.items():
                    if isinstance(m, dict) and "ranks" in m:
                        vault_private[f"{nm}|{tag}|{k}"] = m.pop("ranks")
    json.dump(vault_private, open(TMP / "vault_ranks_private.json", "w"))
    json.dump(out, open(RESULTS, "w"), indent=1)
    log("wrote", RESULTS)


def rr_scores_safe(ev, S_cand, rrc, optional=False):
    out = np.full(S_cand.shape, -1e3)
    idx = rr_pairs(ev, S_cand)
    docs = ev.docs["main"]
    for i, q in enumerate(ev.queries):
        for j in idx[i]:
            k = h(JOB[ev.job] + "\x00" + q + "\x00" + docs[j])
            out[i, j] = rrc.d[k]
    return out


def tools_extra(ev, tags):
    """FRED pipeline reach (dispatcher -> cues -> embedding) using FRED's own intent.classify + SemanticToolRouter."""
    tr, intent, dispatcher, _ = core_modules()
    disp = dispatcher.Dispatcher()
    out = {"pipeline": {}}
    descr = ev.extra["descriptions"]
    for tag in tags:
        t = "4B_ctx4096" if tag == "4B_ctx1024" else tag
        c = Cache(t)

        def embed(text, is_query=False, c=c):
            return c.d[h(FRED_INSTR.format(query=text) if is_query else text)].tolist()
        router = tr.SemanticToolRouter(embed, descr)
        router.build()
        rows = []
        for q, meta in zip(ev.queries, ev.extra["meta"]):
            want, forb = set(meta["tools"]), set(meta["forbid"])
            hit = disp.match(q)
            cues = intent.match_categories(q)
            if hit:
                ok = hit["tool"] in want and hit["tool"] not in forb
                rows.append({"path": "dispatcher", "ok": ok, "strict": ok, "src": meta["src"]})
                continue
            needs, menu, _r = intent.classify(q, llm=None, router=router)
            full = needs and not menu
            ok = full or bool(want & set(menu))
            strict = bool(want & set(menu))
            rows.append({"path": "cue" if cues else "embedding-only", "ok": ok, "strict": strict, "full": full, "menu": len(menu), "src": meta["src"]})
        agg = {}
        for pth in ("dispatcher", "cue", "embedding-only"):
            rr = [r for r in rows if r["path"] == pth]
            agg[pth] = {"n": len(rr), "reach": float(np.mean([r["ok"] for r in rr])) if rr else None,
                        "strict_reach": float(np.mean([r["strict"] for r in rr])) if rr else None,
                        "full_menu_share": float(np.mean([r.get("full", False) for r in rr])) if rr else None}
        agg["all"] = {"n": len(rows), "reach": float(np.mean([r["ok"] for r in rows])), "strict_reach": float(np.mean([r["strict"] for r in rows]))}
        out["pipeline"][tag] = agg
    # embedding-only subset ranking metrics (cases FRED's cues + dispatcher do not catch) and held-out-wording subset
    paths = []
    for q in ev.queries:
        if disp.match(q):
            paths.append("dispatcher")
        else:
            paths.append("cue" if intent.match_categories(q) else "embedding-only")
    out["subsets"] = {}
    for tag in tags:
        t = "4B_ctx4096" if tag == "4B_ctx1024" else tag
        S = base_scores(ev, t)
        d = {}
        for nm, mask in (("embedding-only", [p == "embedding-only" for p in paths]), ("cue-matched", [p == "cue" for p in paths]),
                         ("dispatcher", [p == "dispatcher" for p in paths]), ("novel wording (not a TOOL_EXAMPLES phrase)", ev.extra["novel"])):
            if sum(mask):
                m = metrics(ev, S, np.array(mask)); m.pop("ranks"); d[nm] = m
        out["subsets"][tag] = d
    # score scale: FRED's SEMANTIC_FLOOR (0.65) was tuned on the 4B
    sc = {}
    for tag in ("4B_ctx4096", "06B_ctx4096"):
        S = base_scores(ev, tag)
        sc[tag] = {"top1_median": float(np.median(S.max(1))), "top1_p10": float(np.percentile(S.max(1), 10)), "share_top1_ge_0.65": float((S.max(1) >= 0.65).mean())}
    out["score_scale"] = sc
    return {"pipeline": out["pipeline"], "subsets": out["subsets"], "score_scale": out["score_scale"]}


def head_eval(ev, split):
    """F: frozen 0.6B + a linear head (110 x d, initialised at the tool-text vectors, L2-pulled back to them) trained on utterances.
    source holdout: train on TOOL_EXAMPLES phrases + conversation.jsonl tool turns; test on routing_hard + delegation (different source).
    cv: 5-fold over routing_hard (+ always the TOOL_EXAMPLES phrases)."""
    import torch
    torch.manual_seed(SEED)
    c = Cache("06B_ctx4096")
    names = ev.extra["names"]
    D = np.stack([c.d[h(t)] for t in ev.docs["main"]])
    P0 = torch.tensor(norm(D))
    ex_x, ex_y = [], []
    for n, v in ev.extra["examples"].items():
        for e in v:
            ex_x.append(c.d[h(FRED_INSTR.format(query=e))]); ex_y.append(names.index(n))
    Q = np.stack([c.d[h(FRED_INSTR.format(query=q))] for q in ev.queries])
    meta = ev.extra["meta"]
    src = np.array([m["src"] for m in meta])
    # one label per case = its first listed tool (training only); evaluation still uses the full gold set
    first = np.array([min(g) if len(g) == 1 else -1 for g in ev.gold])

    def fit(X, y, lam=2.0, epochs=300, lr=3e-3, scale=25.0):
        X = torch.tensor(norm(np.asarray(X))); y = torch.tensor(y)
        P = torch.nn.Parameter(P0.clone())
        opt = torch.optim.Adam([P], lr=lr)
        for _ in range(epochs):
            opt.zero_grad()
            logits = scale * X @ torch.nn.functional.normalize(P, dim=1).T
            loss = torch.nn.functional.cross_entropy(logits, y) + lam * ((P - P0) ** 2).sum(1).mean()
            loss.backward(); opt.step()
        return torch.nn.functional.normalize(P.detach(), dim=1).numpy()

    S_all = np.zeros((len(ev.queries), len(names)))
    if split == "source":
        tr_idx = [i for i in range(len(src)) if src[i] == "conversation" and first[i] >= 0]
        X = ex_x + [Q[i] for i in tr_idx]; y = ex_y + [int(first[i]) for i in tr_idx]
        P = fit(X, y)
        S_all = norm(Q) @ P.T
        test = np.array([s != "conversation" for s in src])
        n_train = len(X)
    else:
        idx = [i for i in range(len(src)) if src[i] == "routing_hard"]
        rng = random.Random(SEED); rng.shuffle(idx)
        folds = [idx[k::5] for k in range(5)]
        for k in range(5):
            trn = [i for i in idx if i not in folds[k] and first[i] >= 0]
            P = fit(ex_x + [Q[i] for i in trn], ex_y + [int(first[i]) for i in trn])
            for i in folds[k]:
                S_all[i] = norm(Q[i:i + 1]) @ P.T
        test = np.array([s == "routing_hard" for s in src])
        n_train = len(ex_x)
    m = metrics(ev, S_all, test)
    m["train_examples"] = n_train
    m["test_scope"] = "routing_hard + delegation" if split == "source" else "routing_hard (cross-validated)"
    base = metrics(ev, base_scores(ev, "06B_ctx4096"), test); base.pop("ranks")
    m["same_test_set_plain06"] = {k: base[k] for k in ("n", "r1", "r3", "r5", "mrr")}
    S4 = metrics(ev, base_scores(ev, "4B_ctx4096"), test); S4.pop("ranks")
    m["same_test_set_4B"] = {k: S4[k] for k in ("n", "r1", "r3", "r5", "mrr")}
    return m


def historical(ev):
    """Only ranks and scores; no text. Chunks of active-priorities.md are added to the corpus for this test."""
    out = {}
    for tag in ("4B_ctx4096", "06B_ctx4096"):
        c = Cache(tag)
        D = np.stack([c.d[h(t)] for t in ev.docs["main"]])
        d = {}
        for q, gold, nm in ev.extra["hist"]:
            for qv in ("fred", "none"):
                Qv = np.stack([c.d[h(qtext(q, qv, "vault"))]])
                for center in (True, False):
                    S = dense(Qv, D, center)[0]
                    g = sorted(gold)
                    best = max(g, key=lambda i: S[i])
                    rank = int((S > S[best]).sum()) + 1
                    d[f"{nm}|{qv}|center={int(center)}"] = {"best_chunk_rank": rank, "best_chunk_score": round(float(S[best]), 3),
                                                            "top1_score": round(float(S.max()), 3), "n_chunks": int(len(S)), "n_gold_chunks": len(g)}
        out[tag] = d
    return out


# ----------------------------------------------------------------------------- latency / VRAM
def phase_latency():
    rng = random.Random(SEED)
    d = json.load(open(HERE / "data" / "memory_eval.json", encoding="utf-8"))
    shorts = [q["q"] for q in d["questions"]]
    from general_pairs import FILLER
    out = {}
    for tag in ("4B_ctx4096", "4B_ctx1024", "06B_ctx4096"):
        path, ctx = CFG[tag]
        base = vram_used()
        llm, load_s, dv = load_llama(path, ctx)
        for t in shorts[:5]:
            llm.create_embedding(t)
        vram_after = vram_used()
        # 300-token chunks
        chunks = []
        while len(chunks) < 400:
            words = rng.sample(FILLER, 40)
            txt = ""
            for s in words:
                if len(llm.tokenize((txt + " " + s).encode())) > 300:
                    break
                txt = (txt + " " + s).strip()
            chunks.append(txt)
        ntok = statistics.mean(len(llm.tokenize(c.encode())) for c in chunks)
        for t in chunks[:3]:
            llm.create_embedding(t)

        def timeit(texts, n=200):
            ts = []
            for i in range(n):
                t0 = time.perf_counter(); llm.create_embedding(texts[i % len(texts)]); ts.append((time.perf_counter() - t0) * 1000)
            ts.sort()
            return {"median_ms": round(statistics.median(ts), 2), "p95_ms": round(ts[int(0.95 * len(ts)) - 1], 2), "runs": n}
        q = [FRED_INSTR.format(query=s) for s in shorts]
        short = timeit(q)
        long_ = timeit(chunks)
        t0 = time.perf_counter()
        for c in chunks:
            llm.create_embedding(c)
        dt = time.perf_counter() - t0
        out[tag] = {"vram_delta_mib_after_load": dv, "vram_delta_mib_after_use": vram_after - base, "load_s": round(load_s, 2),
                    "short_query": short, "chunk_300tok": long_, "avg_chunk_tokens": round(ntok, 1),
                    "reindex_400_chunks_s": round(dt, 1), "reindex_chunks_per_s": round(len(chunks) / dt, 2),
                    "reindex_1000_chunks_min": round(1000 / (len(chunks) / dt) / 60, 2)}
        llm.close()
        log(tag, out[tag])
        time.sleep(3)
    json.dump(out, open(HERE / "latency.json", "w"), indent=1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    {"selftest": selftest, "embed": phase_embed, "rerank": phase_rerank, "eval": phase_eval, "latency": phase_latency}[cmd]()
