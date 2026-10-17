"""Evaluate the Claude brain (hibot/brains/claude_agent.py) on a stratified subset of the test split, and compare it
with an ArGPT run on EXACTLY the same tickets.

    python tools/eval/run_eval_claude.py run   --tag claude_haiku_20 [--n 20] [--model claude-haiku-5-5] [--max-usd 0.5]
    python tools/eval/run_eval_claude.py score --tag claude_haiku_20 --compare round2_20261017 [--compare round1_20261016 ...]

Another eval set (e.g. the second hold-out, all of its tickets): add `--eval-set knowledge/eval/holdout2.json --n 40` to BOTH run and score.
The chosen keys are then stored in runs/eval_<tag>/subset.json (the shared subset20.json is only used for the default eval set).

Records are written to runs/eval_<tag>/results.jsonl in the run_eval.py format, so tools/eval/score.py logic applies.
Limitation: the Claude brain has no deterministic ticket-window step, so `manual_fix_detected` checks only hit when the
model itself called the `ticket_window` tool.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

import score  # noqa: E402  (tools/eval/score.py)

SUBSET_FILE = ROOT / "knowledge" / "eval" / "subset20.json"
RANK = {"high": 0, "medium": 1, "low": 2}


def pick_subset(items, n):
    """Deterministic stratified half of the test split, preferring reliable labels."""
    test = [i for i in items if i["split"] == "test"]
    by = defaultdict(list)
    for i in test:
        by[i["stratum"]].append(i)
    quota = {s: max(1, round(len(v) * n / len(test))) for s, v in by.items()}
    while sum(quota.values()) > n:                       # trim the largest strata first
        s = max(quota, key=lambda k: (quota[k], k))
        quota[s] -= 1
    while sum(quota.values()) < n:
        s = max(quota, key=lambda k: (len(by[k]) - quota[k], k))
        quota[s] += 1
    out = []
    for s in sorted(by):
        ranked = sorted(by[s], key=lambda i: (RANK.get(i.get("label_confidence"), 3), i["key"]))
        out += [i["key"] for i in ranked[:quota[s]]]
    return sorted(out)


HEADER = re.compile(r"Kök neden türü:\*\*\s*`?([a-z_]+)`?.*?Yapılması gereken:\*\*\s*`?([a-z_]+)`?.*?Güven:\*\*\s*([0-9.]+)", re.S)


def _section(text, title):
    m = re.search(r"^##\s*%s[^\n]*\n(.*?)(?=^##\s|\Z)" % re.escape(title), text, re.M | re.S)
    return m.group(1).strip() if m else ""


def to_record(key, info, elapsed):
    from hibot.agent.axes import derive_classification
    text = info.get("report_text") or ""
    m = HEADER.search(text)
    rct, ra, conf = (m.group(1), m.group(2), float(m.group(3))) if m else ("unknown", "unknown", 0.0)
    report = {
        "root_cause_type": rct, "resolution_action": ra, "classification": derive_classification(rct, ra),
        "confidence": conf, "summary": _section(text, "Özet"), "root_cause": _section(text, "Mekanizma"),
        "evidence": [{"finding": _section(text, "Kanıtlar")}, {"finding": _section(text, "Kod konumları")}],
        "fix_steps": [_section(text, "Çözüm adımları")],
    }
    resolved, queries = [], []
    for t in info.get("trace") or []:
        if t["name"] in ("run_template", "sql"):
            qid = (t["input"] or {}).get("query_id") if t["name"] == "run_template" else "adhoc_sql"
            ok = t["ok"]
            try:
                res = json.loads(t["result"])
                ok = ok and bool(res.get("ok", True))
            except (ValueError, AttributeError):
                res = {}
            queries.append({"query_id": qid, "executed": ok})
            if qid == "wo_locate" and isinstance(res, dict):
                cols = res.get("columns") or []
                resolved += [dict(zip(cols, row)) for row in res.get("rows") or []]
        if t["name"] == "ticket_window":
            try:
                tw = json.loads(t["result"])
                if isinstance(tw, dict) and tw.get("ok", True):
                    report["ticket_window_changes"] = tw.get("window") or tw
            except ValueError:
                pass
    usage = info.get("usage") or {}
    return {"key": key, "mode": "live", "brain": "claude", "model": usage.get("model"), "report": report,
            "preflight": {"resolved": resolved}, "queries": queries, "elapsed_s": round(elapsed, 1),
            "llm_calls": usage.get("steps"), "usd": usage.get("usd"), "matched_card": None,
            "error": None if m else "final report header not found"}


def _subset_file(a):
    return SUBSET_FILE if not a.eval_set else ROOT / "runs" / ("eval_%s" % a.tag) / "subset.json"


def _load_items(a):
    return score.load_eval_set(Path(a.eval_set)) if a.eval_set else score.load_eval_set()


def cmd_run(a):
    from hibot.brains.claude_agent import investigate
    from hibot.envfile import load_env
    load_env()
    items = _load_items(a)
    keys = pick_subset(items, a.n)
    out_dir = ROOT / "runs" / ("eval_%s" % a.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    _subset_file(a).write_text(json.dumps({"n": len(keys), "keys": keys}, indent=1), encoding="utf-8")
    res_path = out_dir / "results.jsonl"
    done = set(score.load_records(res_path)) if res_path.exists() else set()
    total = 0.0
    for i, key in enumerate(keys, 1):
        if key in done:
            continue
        t0 = time.time()
        try:
            _, info = investigate(key, a.model, a.effort, a.max_usd, a.max_steps)
            rec = to_record(key, info, time.time() - t0)
        except Exception as e:                                      # noqa: BLE001 - one ticket must not stop the run
            rec = {"key": key, "mode": "live", "brain": "claude", "error": "%s: %s" % (type(e).__name__, str(e)[:300])}
        total += rec.get("usd") or 0.0
        with res_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        rep = rec.get("report") or {}
        print("[%2d/%d] %s -> %s/%s (%.2f) $%.3f  total $%.3f %s" % (
            i, len(keys), key, rep.get("root_cause_type"), rep.get("resolution_action"), rep.get("confidence") or 0,
            rec.get("usd") or 0, total, rec.get("error") or ""), flush=True)
        if total >= a.total_usd:
            print("total budget reached, stopping (resume with the same command)")
            break


def cmd_score(a):
    items = _load_items(a)
    keys = set(json.loads(_subset_file(a).read_text(encoding="utf-8"))["keys"])
    sub = [i for i in items if i["key"] in keys]
    tags = [a.tag] + list(a.compare or [])
    rows = []
    for tag in tags:
        recs = score.load_records(ROOT / "runs" / ("eval_%s" % tag) / "results.jsonl")
        res = score.score_all(sub, recs, "test")
        o = res["overall"]
        usd = sum((recs.get(k) or {}).get("usd") or 0 for k in keys)
        rows.append((tag, o, res, usd))
    metrics = [("n scored", "n", "{}"), ("reliable labels", "n_reliable_labels", "{}"),
               ("confidently wrong (legacy)", "confidently_wrong_rate_reliable", "%"),
               ("confidently wrong (root_cause_type)", "cw_rct_rate_reliable", "%"),
               ("exact / legacy", "class_exact_acc_reliable", "%"), ("lenient / legacy", "class_lenient_acc_reliable", "%"),
               ("root_cause_type acc", "rct_acc_reliable", "%"), ("resolution_action acc", "ra_acc_reliable", "%"),
               ("both axes", "both_acc_reliable", "%"), ("findings hit", "findings_hit_rate", "%"),
               ("WO resolution", "wo_resolution_acc", "%"), ("tenant resolution", "tenant_resolution_acc", "%"),
               ("honest unknown", "honest_unknown_rate", "%"), ("avg seconds", "avg_elapsed_s", "{}"),
               ("avg LLM calls", "avg_llm_calls", "{}")]
    L = ["# Claude vs ArGPT on the same %d test tickets" % len(keys), "",
         "Subset: %s (stratified part of the test split, reliable labels preferred). " % ("`%s`" % a.eval_set if a.eval_set else "`knowledge/eval/subset20.json`") +
         "Silver labels; headline metrics on reliable labels. Claude limitation: `manual_fix_detected` only hits when "
         "the model called `ticket_window` itself.", "",
         "| metric | " + " | ".join(t for t, *_ in rows) + " |", "|---|" + "---|" * len(rows)]
    for name, k, fmt in metrics:
        vals = []
        for _, o, _, _ in rows:
            v = o.get(k)
            vals.append("n/a" if v is None else ("%.0f%%" % (100 * v) if fmt == "%" else str(v)))
        L.append("| %s | %s |" % (name, " | ".join(vals)))
    L.append("| API cost (USD) | %s |" % " | ".join("%.3f" % u if u else "-" for *_, u in rows))
    L += ["", "## Per stratum, root_cause_type accuracy (all labels)", "",
          "| stratum | " + " | ".join(t for t, *_ in rows) + " |", "|---|" + "---|" * len(rows)]
    strata = sorted({s for _, _, r, _ in rows for s in r["by_stratum"]})
    for s in strata:
        L.append("| %s | %s |" % (s, " | ".join(
            "%.0f%% (n=%d)" % (100 * (r["by_stratum"].get(s) or {}).get("rct_acc_all", 0) or 0, (r["by_stratum"].get(s) or {}).get("n", 0))
            for _, _, r, _ in rows)))
    L += ["", "## Per ticket", "", "| key | stratum | expected rct | " + " | ".join(t for t, *_ in rows) + " |",
          "|---|---|---|" + "---|" * len(rows)]
    per = {t: {r["key"]: r for r in res["tickets"]} for t, _, res, _ in rows}
    for i in sorted(sub, key=lambda x: (x["stratum"], x["key"])):
        cells = []
        for t, *_ in rows:
            r = per[t].get(i["key"])
            cells.append("-" if not r else "%s%s %.2f" % (r["got_rct"], " ✓" if r["rct_ok"] else "", r["confidence"]))
        L.append("| %s | %s | %s | %s |" % (i["key"], i["stratum"], score.expected_axes(i)[0], " | ".join(cells)))
    out = ROOT / "knowledge" / "eval" / ("compare_%s.md" % a.tag)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:22]))
    print("->", out)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                           # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--model", default="claude-haiku-5-5")
    r.add_argument("--effort", default="medium")
    r.add_argument("--max-usd", type=float, default=0.5, help="per ticket")
    r.add_argument("--max-steps", type=int, default=30)
    r.add_argument("--total-usd", type=float, default=2.0, help="stop the whole run above this spend")
    r.add_argument("--eval-set", help="default: knowledge/eval/eval_set.json (stratified --n subset of its test split)")
    s = sp.add_parser("score")
    s.add_argument("--tag", required=True)
    s.add_argument("--compare", action="append")
    s.add_argument("--eval-set", help="same value as used for run")
    a = ap.parse_args()
    cmd_run(a) if a.cmd == "run" else cmd_score(a)


if __name__ == "__main__":
    main()
