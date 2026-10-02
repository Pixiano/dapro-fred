"""Large tool-routing test: ~370 invented English utterances + ~70 negatives, DEV/TEST 50/50 stratified by category.

    tools_large.py selftest          # metric-code self-test (no models)
    tools_large.py build             # parse data/tools_large_pos*.txt + _neg.txt, dedup, split -> data/tools_large_utterances.json
    tools_large.py embed 06B_ctx4096 | 4B_ctx4096   # one process per model, may run in parallel (GPU only)
    tools_large.py dev               # DEV only: pick the config-3 wording x centering (numpy)
    tools_large.py final             # TEST-half metrics for the three frozen configs -> tools_large.json (numpy)

Configs: 1 = 4B, FRED generic query instruction, no centering (today). 2 = 0.6B Q8_0, same. 3 = 0.6B, tool-specific
instruction + centering (wording/centering frozen on DEV). Nothing under Core/ is edited; Core/data is never read.
"""
import io, contextlib, json, math, random, re, sys
from collections import Counter
from pathlib import Path

import numpy as np

import bench_embedders as B

HERE, SEED = B.HERE, B.SEED
DATA = HERE / "data"
UTT = DATA / "tools_large_utterances.json"
WORDS = {
    "W1": "Given a user request to a voice assistant, retrieve the description of the tool that should handle it",
    "W2": "Given a short spoken command to a PC voice assistant, retrieve the tool whose description matches the action the user wants carried out",
    "W3": "Match the user's spoken request to the single assistant tool that can carry it out",
    "W4": "Given a short spoken request, retrieve the tool description that names the operation being asked for, both the verb and the thing it acts on",
}
GROUPS = {
    "system/info": "get_current_time get_system_status get_network_status list_processes kill_process power_action end_of_day restart_fred take_screenshot get_clipboard set_clipboard calculate get_weather",
    "audio/display": "get_volume set_volume adjust_volume mute list_audio_devices set_input_device set_output_device adjust_brightness get_brightness set_brightness media_control",
    "windows/apps": "launch_application open_website list_windows focus_window minimize_window maximize_window close_window open_path open_last_found",
    "files": "create_text_file create_folder search_files find_file_smart move_file rename_file read_file delete_file append_to_file list_directory convert_file print_file reindex_drive search_index schedule_file_watch",
    "email": "check_email search_email read_email download_attachment draft_email send_draft set_email_signature",
    "messaging/phone": "send_message set_contact_tier list_contact_tiers read_messages find_otp call_phone hang_up get_call_log set_alarm sync_contacts use_phone take_phone_photo",
    "reminders/tasks/agenda": "schedule_reminder list_scheduled cancel_scheduled set_timer schedule_recurring add_task list_tasks complete_task add_agenda_item list_agenda_items update_agenda_item delete_agenda_item workout_split todays_workout schedule_workouts",
    "ac": "get_ac_status set_ac_power set_ac_temperature set_ac_mode set_ac_fan_speed",
    "vision": "whats_on_screen look_through_camera",
    "dev": "git_status git_log git_diff_summary",
    "self/vault/lockdown": "open_vault_file summarise_today save_today_summary preview_missing_map_entries add_missing_map_entries recall_recent_conversation review_pending_reflection describe_self ask_about_myself repeat_last lockdown_engage lockdown_disengage confirm_lockdown_lift cancel_sleep_mode get_active_hours_summary",
    "web/agents": "web_search delegate_research delegate_general",
}
GROUP_OF = {t: g for g, ts in GROUPS.items() for t in ts.split()}
STYLE = {"n": "plain", "q": "question", "t": "typo/ASR slip", "c": "confusable/short", "a": "argument-heavy"}


def nt(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def doc_texts():
    tr, *_ = B.core_modules()
    tools = json.load(open(DATA / "tools.json", encoding="utf-8"))
    names = list(tools)
    return names, tools, [f"{n.replace('_', ' ')}. {tools[n]} " + " ".join(tr.TOOL_EXAMPLES.get(n, [])) for n in names], tr.TOOL_EXAMPLES


def build():
    names, tools, docs, examples = doc_texts()
    banned = {nt(e) for v in examples.values() for e in v}
    for f in (ROOT_CASES := B.ROOT / "benchmarking" / "cases").glob("*.jsonl"):
        for l in open(f, encoding="utf-8"):
            try:
                banned.add(nt(json.loads(l).get("text", "")))
            except Exception:
                pass
    rows, seen, dropped = [], set(), []
    for fn in ("tools_large_pos.txt", "tools_large_pos2.txt"):
        for l in open(DATA / fn, encoding="utf-8"):
            if not l.strip():
                continue
            tl, style, utt = l.rstrip("\n").split("|", 2)
            tl = tl.split(",")
            assert all(t in names for t in tl), tl
            k = nt(utt)
            if k in banned or k in seen:
                dropped.append(utt); continue
            seen.add(k)
            rows.append({"q": utt, "gold": tl, "style": style, "cat": GROUP_OF[tl[0]]})
    rng = random.Random(SEED)
    for cat in sorted({r["cat"] for r in rows}):  # stratified 50/50 by category (odd one out alternates)
        idx = [i for i, r in enumerate(rows) if r["cat"] == cat]
        rng.shuffle(idx)
        half = len(idx) // 2 + (len(idx) % 2 and rng.random() < 0.5)
        for j, i in enumerate(idx):
            rows[i]["split"] = "dev" if j < half else "test"
    neg = [l.strip() for l in open(DATA / "tools_large_neg.txt", encoding="utf-8") if l.strip()]
    rng.shuffle(neg)
    negs = [{"q": q, "split": "dev" if j < len(neg) // 2 else "test"} for j, q in enumerate(neg)]
    for i, r in enumerate(rows):
        r["id"] = i
    D = {"seed": SEED, "pos": rows, "neg": negs, "dropped_verbatim": dropped,
         "tools_without_utterance": [t for t in names if not any(t in r["gold"] for r in rows)]}
    return D, names, tools, docs


def side(D, split):
    return [r for r in D["pos"] if r["split"] == split], [r["q"] for r in D["neg"] if r["split"] == split]


def qt(q, w):
    return B.FRED_INSTR.format(query=q) if w == "fred" else f"Instruct: {WORDS[w]}\nQuery: {q}"


def phase_embed(tag):
    D, names, tools, docs = build()
    qs = [r["q"] for r in D["pos"]] + [r["q"] for r in D["neg"]]
    ws = ["fred"] if tag.startswith("4B") else ["fred"] + list(WORDS)
    B.embed_all(tag, docs + [qt(q, w) for q in qs for w in ws])


def scores(tag, docs, qs, w, center):
    c = B.Cache(tag)
    D = np.stack([c.d[B.h(t)] for t in docs])
    Q = np.stack([c.d[B.h(qt(q, w))] for q in qs])
    return B.dense(Q, D, center)


def gold_idx(rows, names):
    return [{names.index(t) for t in r["gold"]} for r in rows]


def msum(ranks):
    a = np.asarray(ranks)
    return {"n": int(len(a)), "r1": float((a <= 1).mean()), "r3": float((a <= 3).mean()), "r5": float((a <= 5).mean()), "mrr": float((1 / a).mean())}


# ----------------------------------------------------------------------------- stats
def boot_ci(x, n=4000, seed=11):
    x = np.asarray(x, float)
    idx = np.random.default_rng(seed).integers(0, len(x), (n, len(x)))
    m = x[idx].mean(1)
    return [round(float(np.percentile(m, 2.5)) * 100, 1), round(float(np.percentile(m, 97.5)) * 100, 1)]


def boot_diff(a, b, n=4000, seed=7):
    a, b = np.asarray(a, float), np.asarray(b, float)
    idx = np.random.default_rng(seed).integers(0, len(a), (n, len(a)))
    d = (a[idx].mean(1) - b[idx].mean(1)) * 100
    return [round(float((a - b).mean() * 100), 1), round(float(np.percentile(d, 2.5)), 1), round(float(np.percentile(d, 97.5)), 1)]


def mcnemar(ok_a, ok_b):
    """exact two-sided binomial test on the discordant pairs."""
    ok_a, ok_b = np.asarray(ok_a, bool), np.asarray(ok_b, bool)
    b, c = int((ok_a & ~ok_b).sum()), int((~ok_a & ok_b).sum())
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n)
    return {"A_right_B_wrong": b, "B_right_A_wrong": c, "exact_p": round(p, 4)}


def selftest():
    B.selftest()
    S = np.array([[0.9, 0.9, 0.1], [0.2, 0.5, 0.5]])
    assert B.rank_of_best(S, [{0}, {2}]) == [2, 2]  # tie with a non-gold doc counts against the gold item
    assert msum([1, 2, 6, 3]) == {"n": 4, "r1": 0.25, "r3": 0.75, "r5": 0.75, "mrr": (1 + .5 + 1 / 6 + 1 / 3) / 4}
    assert mcnemar([1, 1, 1, 1, 1, 0], [0, 0, 0, 0, 0, 0]) == {"A_right_B_wrong": 5, "B_right_A_wrong": 0, "exact_p": 0.0625}
    assert mcnemar([1, 0], [0, 1])["exact_p"] == 1.0 and mcnemar([1, 0], [1, 0])["exact_p"] == 1.0
    assert mcnemar([1] * 10 + [0] * 2, [0] * 10 + [1] * 2)["exact_p"] == round(2 * sum(math.comb(12, i) for i in range(3)) / 4096, 4)
    x = np.array([1, 0, 1, 1, 0, 1.0])
    assert boot_diff(x, x) == [0.0, 0.0, 0.0]
    ci = boot_ci(np.ones(20)); assert ci == [100.0, 100.0]
    lo, hi = boot_ci(np.array([1.0] * 30 + [0.0] * 30)); assert lo < 50 < hi
    # end-to-end reach helper
    assert reach({"path": "dispatcher", "tool": "a", "menu": ["a"], "needs": True, "full": False}, {"a"})
    assert not reach({"path": "cue", "tool": None, "menu": ["x"], "needs": True, "full": False}, {"a"})
    assert reach({"path": "cue", "tool": None, "menu": [], "needs": True, "full": True}, {"a"}) and not reach({"path": "cue", "tool": None, "menu": [], "needs": True, "full": True}, {"a"}, strict=True)
    print("tools_large selftest OK")


# ----------------------------------------------------------------------------- pipeline (FRED's dispatcher -> cues -> embedding menu)
def pipeline(tag, docs, descr, w, center, texts, floor=None):
    tr, intent, dispatcher, _ = B.core_modules()
    disp = dispatcher.Dispatcher()
    c = B.Cache(tag)
    m = np.stack([c.d[B.h(t)] for t in docs]).mean(0) if center else 0
    old = intent.SEMANTIC_FLOOR
    if floor is not None:
        intent.SEMANTIC_FLOOR = floor

    def embed(text, is_query=False):
        return (c.d[B.h(qt(text, w) if is_query else text)] - m).tolist()
    router = tr.SemanticToolRouter(embed, descr)
    with contextlib.redirect_stdout(io.StringIO()):
        router.build()
    recs = []
    try:
        for q in texts:
            hit = disp.match(q)
            if hit:
                recs.append({"path": "dispatcher", "tool": hit["tool"], "menu": [hit["tool"]], "needs": True, "full": False}); continue
            cues = intent.match_categories(q)
            with contextlib.redirect_stdout(io.StringIO()):
                needs, menu, _ = intent.classify(q, llm=None, router=router)
            recs.append({"path": "cue" if cues else "embedding-only", "tool": None, "menu": list(menu), "needs": bool(needs), "full": bool(needs and not menu)})
    finally:
        intent.SEMANTIC_FLOOR = old
    return recs


def reach(rec, gold_names, strict=False):
    """FRED can reach a gold tool: dispatcher fired it, or it is in the menu; (lenient) 'offer everything' also counts, as in the earlier report."""
    if rec["path"] == "dispatcher":
        return rec["tool"] in gold_names
    return bool(set(rec["menu"]) & gold_names) or (rec["full"] and not strict)


# ----------------------------------------------------------------------------- DEV: tune config 3 only
def phase_dev():
    D, names, tools, docs = build()
    dev, _ = side(D, "dev")
    qs, gold = [r["q"] for r in dev], gold_idx(dev, names)
    out = {}
    for w in WORDS:
        for cen in (False, True):
            m = msum(B.rank_of_best(scores("06B_ctx4096", docs, qs, w, cen), gold))
            out[f"{w}|center={int(cen)}"] = m
            print(w, "center", int(cen), {k: round(v, 3) for k, v in m.items()}, flush=True)
    base = {}
    for tag in ("4B_ctx4096", "06B_ctx4096"):
        base[tag] = msum(B.rank_of_best(scores(tag, docs, qs, "fred", False), gold))
    best = max(out, key=lambda k: (out[k]["mrr"], out[k]["r1"]))
    w, cen = best.split("|")
    json.dump({"dev_n": len(dev), "grid_0.6B": out, "baselines_dev": base, "frozen": {"wording": w, "center": cen == "center=1"}, "words": WORDS},
              open(HERE / "tools_large_dev.json", "w"), indent=1)
    print("frozen:", best, "dev n =", len(dev))


# ----------------------------------------------------------------------------- TEST
def phase_final():
    D, names, tools, docs = build()
    fz = json.load(open(HERE / "tools_large_dev.json"))["frozen"]
    W, CEN = fz["wording"], fz["center"]
    test, negs = side(D, "test")
    dev, negs_dev = side(D, "dev")
    qs, gold = [r["q"] for r in test], gold_idx(test, names)
    gnames = [{names[g] for g in s} for s in gold]
    cfgs = {"1_4B_fred_today": ("4B_ctx4096", "fred", False), "2_0.6B_fred_cfg": ("06B_ctx4096", "fred", False),
            "3_0.6B_tool_instr+center": ("06B_ctx4096", W, CEN)}
    res = {"meta": {"frozen_wording": W, "wording_text": WORDS[W], "frozen_center": CEN, "n_test_pos": len(test), "n_test_neg": len(negs),
                    "n_dev_pos": len(dev), "n_dev_neg": len(negs_dev), "n_total_pos": len(D["pos"]), "n_total_neg": len(D["neg"]),
                    "dropped_verbatim": len(D["dropped_verbatim"]), "tools_without_utterance": D["tools_without_utterance"],
                    "tools_in_test": len({t for r in test for t in r["gold"]}), "semantic_floor": 0.65}, "configs": {}}
    ranks, ok_pipe, ok_pipe_strict, top1, S_all = {}, {}, {}, {}, {}
    cats = [r["cat"] for r in test]; styles = [r["style"] for r in test]
    for name, (tag, w, cen) in cfgs.items():
        S = scores(tag, docs, qs, w, cen); Sn = scores(tag, docs, negs, w, cen); S_all[name] = S
        r = np.array(B.rank_of_best(S, gold)); ranks[name] = r
        d = msum(r)
        d["ci95_pct"] = {"r1": boot_ci(r <= 1), "r3": boot_ci(r <= 3), "r5": boot_ci(r <= 5), "mrr": boot_ci(1 / r)}
        # per category / style
        d["by_category"] = {c: {"n": int(sum(x == c for x in cats)), "r1": round(float((r[[x == c for x in cats]] <= 1).mean()), 3),
                                "r5": round(float((r[[x == c for x in cats]] <= 5).mean()), 3)} for c in sorted(set(cats))}
        d["by_style"] = {STYLE[s]: {"n": int(sum(x == s for x in styles)), "r1": round(float((r[[x == s for x in styles]] <= 1).mean()), 3)} for s in sorted(set(styles))}
        conf, wrong = Counter(), Counter()
        for i in range(len(qs)):
            if r[i] > 1:
                top = names[int(np.argmax(S[i]))]
                conf[f"{test[i]['gold'][0]} -> {top}"] += 1; wrong[top] += 1
        d["top_confusions"] = conf.most_common(10); d["most_often_predicted_when_wrong"] = wrong.most_common(8)
        # score scale and the 0.65 floor, directly on the embedder (any path)
        t1p, t1n = S.max(1), Sn.max(1); top1[name] = (t1p, t1n)
        d["scores"] = {"top1_median_pos": round(float(np.median(t1p)), 3), "top1_median_neg": round(float(np.median(t1n)), 3),
                       "share_pos_top1_ge_floor": round(float((t1p >= 0.65).mean()), 3), "share_neg_top1_ge_floor": round(float((t1n >= 0.65).mean()), 3),
                       "auroc_pos_vs_neg_top1": round(float(np.mean([(p > n_) + 0.5 * (p == n_) for p in t1p for n_ in t1n])), 3)}
        # end to end through FRED's own code, floor 0.65 as shipped
        P = pipeline(tag, docs, tools, w, cen, qs); N = pipeline(tag, docs, tools, w, cen, negs)
        ok = np.array([reach(p, g) for p, g in zip(P, gnames)]); oks = np.array([reach(p, g, True) for p, g in zip(P, gnames)])
        ok_pipe[name], ok_pipe_strict[name] = ok, oks
        ft = np.array([n_["needs"] for n_ in N]); fe = np.array([n_["needs"] and n_["path"] == "embedding-only" for n_ in N])
        d["pipeline_floor_0.65"] = {
            "reach": float(ok.mean()), "reach_ci95_pct": boot_ci(ok), "strict_reach": float(oks.mean()),
            "by_path": {p: {"n": sum(x["path"] == p for x in P), "reach": round(float(np.mean([o for o, x in zip(ok, P) if x["path"] == p])), 3) if any(x["path"] == p for x in P) else None}
                        for p in ("dispatcher", "cue", "embedding-only")},
            "false_trigger_any_path": float(ft.mean()), "false_trigger_ci95_pct": boot_ci(ft),
            "false_trigger_embedding_only": float(fe.mean()), "false_trigger_embedding_only_ci95_pct": boot_ci(fe),
            "false_trigger_by_cause": {p: int(sum(n_["needs"] and n_["path"] == p for n_ in N)) for p in ("dispatcher", "cue", "embedding-only")}}
        # secondary: floor re-calibrated on DEV negatives only (95th pct of dev-negative top-1)
        fl = float(np.percentile(scores(tag, docs, negs_dev, w, cen).max(1), 95))
        P2 = pipeline(tag, docs, tools, w, cen, qs, floor=fl); N2 = pipeline(tag, docs, tools, w, cen, negs, floor=fl)
        o2 = np.array([reach(p, g) for p, g in zip(P2, gnames)])
        d["pipeline_dev_calibrated_floor"] = {"floor": round(fl, 3), "reach": float(o2.mean()), "reach_ci95_pct": boot_ci(o2),
                                              "false_trigger_embedding_only": float(np.mean([n_["needs"] and n_["path"] == "embedding-only" for n_ in N2])),
                                              "false_trigger_any_path": float(np.mean([n_["needs"] for n_ in N2]))}
        res["configs"][name] = d
    # paired comparisons of 2 and 3 against 1 (and 3 vs 2)
    res["paired"] = {}
    for a, b in (("2_0.6B_fred_cfg", "1_4B_fred_today"), ("3_0.6B_tool_instr+center", "1_4B_fred_today"), ("3_0.6B_tool_instr+center", "2_0.6B_fred_cfg")):
        f = {"R@1": lambda r: r <= 1, "R@3": lambda r: r <= 3, "R@5": lambda r: r <= 5, "MRR": lambda r: 1 / r}
        d = {f"{k} gap pts [mean, lo, hi]": boot_diff(fn(ranks[a]), fn(ranks[b])) for k, fn in f.items()}
        d["top1 paired (A=first, B=second)"] = mcnemar(ranks[a] <= 1, ranks[b] <= 1)
        d["top5 paired"] = mcnemar(ranks[a] <= 5, ranks[b] <= 5)
        d["pipeline reach gap pts [mean, lo, hi]"] = boot_diff(ok_pipe[a], ok_pipe[b])
        d["pipeline reach paired"] = mcnemar(ok_pipe[a], ok_pipe[b])
        d["false_trigger_embedding_only gap pts"] = round(res["configs"][a]["pipeline_floor_0.65"]["false_trigger_embedding_only"] * 100 - res["configs"][b]["pipeline_floor_0.65"]["false_trigger_embedding_only"] * 100, 1)
        res["paired"][f"{a} vs {b}"] = d
    json.dump(res, open(HERE / "tools_large.json", "w"), indent=1)
    print(json.dumps({"meta": res["meta"]}, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "selftest":
        selftest()
    elif cmd == "build":
        D, names, tools, docs = build()
        json.dump({"note": "invented utterances only; gold = any-of acceptable tools", **D}, open(UTT, "w", encoding="utf-8"), indent=1)
        print({"pos": len(D["pos"]), "neg": len(D["neg"]), "dropped": D["dropped_verbatim"], "missing_tools": D["tools_without_utterance"],
               "dev_pos": sum(r["split"] == "dev" for r in D["pos"]), "test_pos": sum(r["split"] == "test" for r in D["pos"]),
               "cats": Counter((r["cat"], r["split"]) for r in D["pos"])})
    elif cmd == "embed":
        phase_embed(sys.argv[2])
    elif cmd == "dev":
        phase_dev()
    elif cmd == "final":
        phase_final()
