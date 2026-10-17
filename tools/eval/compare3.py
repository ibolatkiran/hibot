"""Three-column comparison of scored runs (baseline / round 1 / round 2) from results_<tag>.json written by score.py.

    python tools/eval/compare3.py --tags baseline_20261016 round1_20261016 round2_20261017 --names baseline "round 1" "round 2" \
        [--out knowledge/eval/compare_round1_vs_round2.md]

Axes of old (legacy-only) reports are derived by score.py (LEGACY_TO_AXES), so resolution_action / both-axes numbers of the baseline are pessimistic by construction.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "knowledge" / "eval"


def pct(v: Optional[float]) -> str:
    return "n/a" if v is None else "%.0f%%" % (100 * v)


def num(v: Any) -> str:
    return "n/a" if v is None else str(v)


def load(tag: str) -> dict:
    return json.loads((EVAL_DIR / ("results_%s.json" % tag)).read_text(encoding="utf-8"))


def row(label: str, key: str, rs: List[dict], fmt: Callable = pct) -> str:
    return "| %s | %s |" % (label, " | ".join(fmt(r["overall"][key]) for r in rs))


def render(rs: List[dict], names: List[str], title: str) -> str:
    o = rs[-1]["overall"]
    n = len(names)
    L = ["# %s" % title, "",
         "Columns: %s. All scored by the same (axis-aware) `score.py` on the `test` split (%d tickets, live ArGPT + EU prod). Axes of the baseline are DERIVED from its "
         "legacy label, so its resolution_action / both-axes numbers are pessimistic by construction; root_cause_type is the fair axis. Silver labels, reliable = high+medium "
         "(n=%d). 1 ticket = 3.3 points on the reliable set; LLM variance is a few tickets." % (", ".join(names), o["n"], o["n_reliable_labels"]), "",
         "## Headline", "", "| metric | %s |" % " | ".join(names), "|---|%s|" % "|".join(["---"] * n),
         row("**confidently wrong, legacy label** (conf >= 0.7, reliable)", "confidently_wrong_rate_reliable", rs),
         row("**confidently wrong, root_cause_type**", "cw_rct_rate_reliable", rs),
         row("classification exact (legacy), reliable", "class_exact_acc_reliable", rs),
         row("classification lenient (legacy), reliable", "class_lenient_acc_reliable", rs),
         row("root_cause_type accuracy, reliable", "rct_acc_reliable", rs),
         row("resolution_action accuracy, reliable", "ra_acc_reliable", rs),
         row("both axes correct, reliable", "both_acc_reliable", rs),
         row("root_cause_type accuracy, all labels", "rct_acc_all", rs),
         row("honest unknown (unknown, conf < 0.5)", "honest_unknown_rate", rs),
         row("expected_findings hit rate", "findings_hit_rate", rs),
         row("work-order resolution", "wo_resolution_acc", rs),
         row("tenant resolution", "tenant_resolution_acc", rs),
         row("card match rate", "card_match_rate", rs),
         row("avg elapsed s", "avg_elapsed_s", rs, num),
         row("avg queries prepared", "avg_queries_total", rs, num),
         row("avg queries executed", "avg_queries_executed", rs, num),
         row("avg LLM calls", "avg_llm_calls", rs, num), "",
         "## Calibration (reliable labels): accuracy (n) per confidence bucket", "",
         "| bucket | %s | %s |" % (" | ".join("legacy: " + x for x in names), " | ".join("root_cause_type: " + x for x in names)),
         "|---|%s|" % "|".join(["---"] * (2 * n))]
    for i, b in enumerate(rs[-1]["calibration"]):
        cells = ["%s (%d)" % (pct(r["calibration"][i]["accuracy"]), r["calibration"][i]["n"]) for r in rs]
        cells += ["%s (%d)" % (pct(r["calibration_rct"][i]["accuracy"]), r["calibration_rct"][i]["n"]) for r in rs]
        L.append("| %s | %s |" % (b["bucket"], " | ".join(cells)))
    L += ["", "## Per stratum (reliable labels; %s)" % " -> ".join(names), "",
          "| stratum | n | exact | root cause | both axes | conf. wrong legacy / rct | findings | avg q |", "|---|---:|---|---|---|---|---|---|"]

    def arrow(vals, f=pct):
        return " -> ".join(f(v) for v in vals)
    for k in sorted(set().union(*[set(r["by_stratum"]) for r in rs])):
        ss = [r["by_stratum"].get(k) for r in rs]
        if not all(ss):
            continue
        L.append("| %s | %d | %s | %s | %s | %s / %s | %s | %s |" % (
            k, ss[-1]["n"], arrow([s["class_exact_acc_reliable"] for s in ss]), arrow([s["rct_acc_reliable"] for s in ss]),
            arrow([s["both_acc_reliable"] for s in ss]), arrow([s["confidently_wrong_n_reliable"] for s in ss], str),
            arrow([s["cw_rct_n_reliable"] for s in ss], str), arrow([s["findings_hit_rate"] for s in ss]),
            arrow([s["avg_queries_total"] for s in ss], num)))
    L += ["", "## Per ticket (root_cause_type, reliable labels; only tickets where an answer or its correctness changed between the last two columns)", "",
          "| key | stratum | expected rct | %s |" % " | ".join(names), "|---|---|---|%s|" % "|".join(["---"] * n)]
    by = [{t["key"]: t for t in r["tickets"]} for r in rs]
    for t in rs[-1]["tickets"]:
        prev = by[-2].get(t["key"])
        if not t["reliable"] or not prev or (prev["got_rct"] == t["got_rct"] and abs(prev["confidence"] - t["confidence"]) < 0.005):
            continue
        cells = []
        for b in by:
            x = b.get(t["key"])
            cells.append("-" if not x else "%s %.2f%s%s" % (x["got_rct"], x["confidence"], " ok" if x["rct_ok"] else "", " **CW**" if x["cw_rct"] else ""))
        L.append("| %s | %s | %s | %s |" % (t["key"], t["stratum"], t["expected_rct"], " | ".join(cells)))
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tags", nargs="+", required=True)
    ap.add_argument("--names", nargs="+", required=True)
    ap.add_argument("--title", default="HiBot baseline vs round 1 vs round 2")
    ap.add_argument("--out", default=str(EVAL_DIR / "compare_round1_vs_round2.md"))
    a = ap.parse_args(argv)
    if len(a.tags) != len(a.names):
        ap.error("--tags and --names need the same length")
    Path(a.out).write_text(render([load(t) for t in a.tags], a.names, a.title), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
