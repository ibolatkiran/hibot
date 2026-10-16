"""Side-by-side comparison of two scored runs (results_<tag>.json from score.py).

    python tools/eval/compare.py --a baseline_20261016 --b round1_20261016 [--out knowledge/eval/compare_baseline_vs_round1.md]

Both results must have been scored with the same (axis-aware) score.py so that axes are derived from legacy labels for old reports.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "knowledge" / "eval"


def pct(v: Optional[float]) -> str:
    return "n/a" if v is None else "%.0f%%" % (100 * v)


def load(tag: str) -> dict:
    return json.loads((EVAL_DIR / ("results_%s.json" % tag)).read_text(encoding="utf-8"))


def row(label: str, a: Any, b: Any, fmt=pct) -> str:
    return "| %s | %s | %s |" % (label, fmt(a), fmt(b))


def num(v: Any) -> str:
    return "n/a" if v is None else str(v)


def render(ra: dict, rb: dict, ta: str, tb: str) -> str:
    oa, ob = ra["overall"], rb["overall"]
    L: List[str] = ["# HiBot baseline vs round 1 (test split, %d tickets, live ArGPT + EU prod)" % ob["n"], "",
                    "Left = `%s`, right = `%s`. Both scored by the same `score.py`: axes of the baseline are DERIVED from its legacy label "
                    "(`label_axes_rules.md`), so its resolution_action / both-axes numbers are pessimistic by construction (a legacy `config` "
                    "cannot say `data_fix`); root_cause_type is the fair axis. Silver labels, reliable = high+medium confidence (n=%d)." % (
                        ta, tb, ob["n_reliable_labels"]), "",
                    "## Headline", "", "| metric | baseline | round 1 |", "|---|---|---|",
                    row("**confidently wrong, legacy label** (conf >= 0.7, reliable)", oa["confidently_wrong_rate_reliable"], ob["confidently_wrong_rate_reliable"]),
                    row("**confidently wrong, root_cause_type**", oa["cw_rct_rate_reliable"], ob["cw_rct_rate_reliable"]),
                    row("classification exact (legacy), reliable", oa["class_exact_acc_reliable"], ob["class_exact_acc_reliable"]),
                    row("classification lenient (legacy), reliable", oa["class_lenient_acc_reliable"], ob["class_lenient_acc_reliable"]),
                    row("root_cause_type accuracy, reliable", oa["rct_acc_reliable"], ob["rct_acc_reliable"]),
                    row("resolution_action accuracy, reliable", oa["ra_acc_reliable"], ob["ra_acc_reliable"]),
                    row("both axes correct, reliable", oa["both_acc_reliable"], ob["both_acc_reliable"]),
                    row("root_cause_type accuracy, all labels", oa["rct_acc_all"], ob["rct_acc_all"]),
                    row("honest unknown (unknown, conf < 0.5)", oa["honest_unknown_rate"], ob["honest_unknown_rate"]),
                    row("expected_findings hit rate", oa["findings_hit_rate"], ob["findings_hit_rate"]),
                    row("work-order resolution", oa["wo_resolution_acc"], ob["wo_resolution_acc"]),
                    row("tenant resolution", oa["tenant_resolution_acc"], ob["tenant_resolution_acc"]),
                    row("card match rate", oa["card_match_rate"], ob["card_match_rate"]),
                    row("avg elapsed s", oa["avg_elapsed_s"], ob["avg_elapsed_s"], num),
                    row("avg queries prepared", oa["avg_queries_total"], ob["avg_queries_total"], num),
                    row("avg queries executed", oa["avg_queries_executed"], ob["avg_queries_executed"], num),
                    row("avg LLM calls", oa["avg_llm_calls"], ob["avg_llm_calls"], num), "",
                    "## Calibration (reliable labels): accuracy per confidence bucket", "",
                    "| bucket | legacy label: baseline (n) | legacy label: round 1 (n) | root_cause_type: baseline (n) | root_cause_type: round 1 (n) |",
                    "|---|---|---|---|---|"]
    for i, b in enumerate(rb["calibration"]):
        a = ra["calibration"][i]
        ar, br = ra["calibration_rct"][i], rb["calibration_rct"][i]
        L.append("| %s | %s (%d) | %s (%d) | %s (%d) | %s (%d) |" % (b["bucket"], pct(a["accuracy"]), a["n"], pct(b["accuracy"]), b["n"],
                                                               pct(ar["accuracy"]), ar["n"], pct(br["accuracy"]), br["n"]))
    L += ["", "## Per stratum (reliable labels)", "",
          "| stratum | n | exact: base -> r1 | lenient: base -> r1 | root cause: base -> r1 | both axes: base -> r1 | conf. wrong legacy: base -> r1 | conf. wrong rct: base -> r1 | findings: base -> r1 | avg q: base -> r1 |",
          "|---|---:|---|---|---|---|---|---|---|---|"]
    for k in sorted(set(ra["by_stratum"]) | set(rb["by_stratum"])):
        a, b = ra["by_stratum"].get(k), rb["by_stratum"].get(k)
        if not a or not b:
            continue
        L.append("| %s | %d | %s -> %s | %s -> %s | %s -> %s | %s -> %s | %d -> %d | %d -> %d | %s -> %s | %s -> %s |" % (
            k, b["n"], pct(a["class_exact_acc_reliable"]), pct(b["class_exact_acc_reliable"]),
            pct(a["class_lenient_acc_reliable"]), pct(b["class_lenient_acc_reliable"]), pct(a["rct_acc_reliable"]), pct(b["rct_acc_reliable"]),
            pct(a["both_acc_reliable"]), pct(b["both_acc_reliable"]), a["confidently_wrong_n_reliable"], b["confidently_wrong_n_reliable"],
            a["cw_rct_n_reliable"], b["cw_rct_n_reliable"], pct(a["findings_hit_rate"]), pct(b["findings_hit_rate"]),
            num(a["avg_queries_total"]), num(b["avg_queries_total"])))
    L += ["", "## Per ticket changes (root_cause_type; reliable labels only, differences only)", "",
          "| key | stratum | expected rct | baseline (conf) | round 1 (conf, raw) |", "|---|---|---|---|---|"]
    ta_by = {t["key"]: t for t in ra["tickets"]}
    for t in rb["tickets"]:
        a = ta_by.get(t["key"])
        if not a or not t["reliable"] or (a["got_rct"] == t["got_rct"] and abs(a["confidence"] - t["confidence"]) < 0.005):
            continue
        L.append("| %s | %s | %s | %s %.2f%s | %s %.2f (%s)%s |" % (
            t["key"], t["stratum"], t["expected_rct"], a["got_rct"], a["confidence"], " **CW**" if a["cw_rct"] else "",
            t["got_rct"], t["confidence"], "-" if t.get("confidence_raw") is None else "%.2f" % t["confidence_raw"], " **CW**" if t["cw_rct"] else ""))
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="baseline tag")
    ap.add_argument("--b", required=True, help="new tag")
    ap.add_argument("--out", default=str(EVAL_DIR / "compare_baseline_vs_round1.md"))
    a = ap.parse_args(argv)
    Path(a.out).write_text(render(load(a.a), load(a.b), a.a, a.b), encoding="utf-8")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
