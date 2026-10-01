"""results.json + latency.json -> markdown tables (printed, and written to docs/.../embedder_4b_vs_06b_tables.md fragment).
The verdict/caveat prose in the final doc is written by hand around these tables."""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
R = json.load(open(HERE / "results.json"))
LAT = json.load(open(HERE / "latency.json")) if (HERE / "latency.json").exists() else {}
EV = R["evals"]
M4, M4b, M06 = "4B_ctx4096", "4B_ctx1024", "06B_ctx4096"
NAMES = {"tools": "A tool routing", "memory": "B memory recall", "vault": "C vault retrieval", "general": "D general (authored)", "scifact": "D2 SciFact slice"}


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}"


def get(ev, tag, var, key="en"):
    d = EV[ev][tag].get(var)
    if not isinstance(d, dict):
        return None
    return d[key] if key and key in d else d


def row(ev, tag, var):
    m = get(ev, tag, var)
    if m is None:
        return None
    return m


def variants(ev):
    """(label, tag, variant key) rows shown in the headline, per eval."""
    v = [("4B ctx4096 (FRED today)", M4, "plain"), ("4B ctx1024", M4b, "plain"), ("0.6B Q8 plain (FRED config)", M06, "plain"),
         ("0.6B + A (job instr + centering + BM25 RRF" + (" + title" if ev == "scifact" else "") + ")", M06, "A:all"),
         ("0.6B + A, instr+centering only", M06, "A:job+center"),
         ("0.6B + C (top20 -> Qwen3-Reranker-0.6B)", M06, "C:top20(plain)->rerank06"),
         ("0.6B + A + C (A top20 -> reranker)", M06, "C:top20(A)->rerank06"),
         ("0.6B top20 -> 4B rerank (upper bound)", M06, "R4:top20(plain)->4B")]
    if ev == "tools":
        v.insert(5, ("0.6B + F (frozen + trained head; source holdout)", M06, "F:head(source holdout)"))
    return v


def table(ev, cols=("r1", "r3", "r5", "mrr"), key="en"):
    out = ["| variant | n | R@1 | R@3 | R@5 | MRR |" + (" nDCG@10 |" if ev == "scifact" else ""), "|---|---|---|---|---|---|" + ("---|" if ev == "scifact" else "")]
    for label, tag, var in variants(ev):
        m = EV[ev][tag].get(var)
        if not isinstance(m, dict):
            continue
        mm = m.get(key, m) if key else m
        if var.startswith("F:") :
            mm = m  # head metrics are on their own test split
            label += f" [n={m['n']}; same-split plain 0.6B R@1 {pct(m['same_test_set_plain06']['r1'])}, 4B {pct(m['same_test_set_4B']['r1'])}]"
        out.append(f"| {label} | {mm['n']} | " + " | ".join(pct(mm[c]) for c in cols) + (f" | {pct(mm.get('ndcg10'))} |" if ev == "scifact" else " |"))
    return "\n".join(out)


def boot(ranks_a, ranks_b, k=1, n=3000, seed=1):
    a, b = np.array(ranks_a) <= k, np.array(ranks_b) <= k
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), (n, len(a)))
    d = (a[idx].mean(1) - b[idx].mean(1)) * 100
    return float(d.mean()), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def best_fix_gap():
    """For each eval: best fix variant (by R@1, then MRR, English only) vs 4B, in points, with bootstrap CI on R@1."""
    lines = ["| eval | n (EN) | 4B R@1 / R@5 | best 0.6B variant | its R@1 / R@5 | gap R@1 (pts) [95% CI] | gap R@5 (pts) | within 3 pts? |", "|---|---|---|---|---|---|---|---|"]
    out = {}
    for ev in ("tools", "memory", "vault", "general", "scifact"):
        cands = {}
        for var in ("plain", "A:all", "A:job+center", "A:bm25rrf(fred cfg)", "A:title", "C:top20(plain)->rerank06", "C:top20(A)->rerank06", "F:head(source holdout)"):
            m = EV[ev][M06].get(var)
            if isinstance(m, dict) and "en" in m and not var.startswith("F:"):
                cands[var] = m
        if ev == "tools" and isinstance(EV[ev][M06].get("F:head(source holdout)"), dict):
            pass  # F reported separately (different test split)
        best = max(cands, key=lambda v: (cands[v]["en"]["r1"], cands[v]["en"]["mrr"]))
        b4, bb = EV[ev][M4]["plain"], EV[ev][M06][best]
        if ev == "vault":
            b4 = dict(b4, ranks=PRIV[f"vault|{M4}|plain"]); bb = dict(bb, ranks=PRIV[f"vault|{M06}|{best}"])
        en = [i for i, c in enumerate(_cats(ev)) if c != "hinglish"]
        ra = [b4["ranks"][i] for i in en] if "ranks" in b4 else None
        g1 = 100 * (bb["en"]["r1"] - b4["en"]["r1"]); g5 = 100 * (bb["en"]["r5"] - b4["en"]["r5"])
        ci = ""
        if ra is not None and "ranks" in bb:
            rb = [bb["ranks"][i] for i in en]
            _, lo, hi = boot(rb, ra)
            ci = f" [{lo:+.1f}, {hi:+.1f}]"
        out[ev] = (best, g1, g5)
        lines.append(f"| {NAMES[ev]} | {b4['en']['n']} | {pct(b4['en']['r1'])} / {pct(b4['en']['r5'])} | {best} | {pct(bb['en']['r1'])} / {pct(bb['en']['r5'])} | {g1:+.1f}{ci} | {g5:+.1f} | {'yes' if g1 >= -3 else 'NO'} |")
    return "\n".join(lines), out


_CATS = {}
PRIV = json.load(open("C:/Users/Dhiraj Vatsal/.claude/jobs/e9eb367b/tmp/vault_ranks_private.json"))


def _cats(ev):
    if not _CATS:
        import sys
        sys.path.insert(0, str(HERE))
        import bench_embedders as b
        for k, e in b.all_evals(with_vault=False).items():
            _CATS[k] = e.cats
        _CATS["vault"] = ["x"] * R["meta"]["sizes"]["vault"]["queries"]
    return _CATS[ev]


def bycat(ev, tags_vars):
    cats = sorted(EV[ev][M4]["plain"]["by_cat"])
    out = ["| category (n) | " + " | ".join(l for l, _, _ in tags_vars) + " |", "|---|" + "---|" * len(tags_vars)]
    for c in cats:
        n = EV[ev][M4]["plain"]["by_cat"][c]["n"]
        cells = []
        for _, tag, var in tags_vars:
            m = EV[ev][tag].get(var)
            cells.append("-" if not isinstance(m, dict) or c not in m.get("by_cat", {}) else f"{pct(m['by_cat'][c]['r1'])} / {pct(m['by_cat'][c]['r5'])}")
        out.append(f"| {c} ({n}) | " + " | ".join(cells) + " |")
    return "\n".join(out)


def sens(ev):
    out = ["| instruction | centering | 4B R@1 | 0.6B R@1 | gap (pts) | 4B R@5 | 0.6B R@5 |", "|---|---|---|---|---|---|---|"]
    for qv in ("fred", "none", "job"):
        for c in (0, 1):
            k = f"sens:{qv}|center={c}"
            a, b = EV[ev][M4][k]["en"], EV[ev][M06][k]["en"]
            tag = " (FRED)" if (qv == "fred" and c == (1 if ev in ("vault",) else 0)) else ""
            out.append(f"| {qv}{tag} | {'on' if c else 'off'} | {pct(a['r1'])} | {pct(b['r1'])} | {100 * (b['r1'] - a['r1']):+.1f} | {pct(a['r5'])} | {pct(b['r5'])} |")
    return "\n".join(out)


def main():
    parts = []
    parts.append("### Gap check: best 0.6B variant vs the 4B, English-only items\n")
    t, best = best_fix_gap()
    parts.append(t)
    for ev in ("tools", "memory", "vault", "general", "scifact"):
        parts.append(f"\n#### {NAMES[ev]} ({R['meta']['sizes'][ev]['queries']} queries, corpus {R['meta']['sizes'][ev]['docs']}); English-only items, % \n")
        parts.append(table(ev))
    parts.append("\n### Per-category (R@1 / R@5, all items incl. Hinglish where present)\n")
    for ev in ("tools", "memory", "general"):
        parts.append(f"\n{NAMES[ev]}\n")
        parts.append(bycat(ev, [("4B", M4, "plain"), ("0.6B", M06, "plain"), ("0.6B+A", M06, "A:all"), ("0.6B+C", M06, "C:top20(plain)->rerank06")]))
    parts.append("\n### Sensitivity (E): instruction prefix x centering, English-only\n")
    for ev in ("tools", "memory", "vault", "general", "scifact"):
        parts.append(f"\n{NAMES[ev]}\n")
        parts.append(sens(ev))
    if LAT:
        parts.append("\n### VRAM / latency\n")
        parts.append("| config | load s | VRAM delta MiB | short query median / p95 ms | 300-tok chunk median / p95 ms | re-index chunks/s | 1000 chunks min |\n|---|---|---|---|---|---|---|")
        for tag, d in LAT.items():
            parts.append(f"| {tag} | {d['load_s']} | {d['vram_delta_mib_after_use']} | {d['short_query']['median_ms']} / {d['short_query']['p95_ms']} | {d['chunk_300tok']['median_ms']} / {d['chunk_300tok']['p95_ms']} | {d['reindex_chunks_per_s']} | {d['reindex_1000_chunks_min']} |")
    txt = "\n".join(parts)
    (HERE / "tables.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
