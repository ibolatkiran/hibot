"""Score an evaluation run against the silver labels in knowledge/eval/eval_set.json.

    python tools/eval/score.py --tag baseline [--split test|dev|all] [--run-dir DIR]
      -> knowledge/eval/results_<tag>.md + results_<tag>.json

Pure functions (score_ticket, aggregate, render_markdown) are unit tested in tests/test_eval.py; they use stdlib only.
Metric definitions are in knowledge/eval/README.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "knowledge" / "eval"

CLASSES = ["code_defect", "master_data", "access", "data_correction", "integration", "config", "user_error", "unknown"]
# Lenient equivalence (symmetric, NOT transitive): a status gap can be called a data fix or a missing configuration/setup row,
# and a missing product/tariff row is master data or configuration.  Anything else must match exactly.
LENIENT_PAIRS = {frozenset(p) for p in (("config", "data_correction"), ("config", "master_data"))}
CONF_WRONG = 0.7          # wrong with confidence >= this is a "confidently wrong" answer
HONEST_UNKNOWN_MAX = 0.5  # unknown with confidence < this is an "honest unknown"
BUCKETS = [(0.0, 0.4), (0.4, 0.7), (0.7, 1.01)]
RELIABLE = ("high", "medium")


# ------------------------------------------------------------------ helpers

def lenient_match(expected: str, got: str) -> bool:
    return expected == got or frozenset((expected, got)) in LENIENT_PAIRS


def _int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def report_text(rep: dict) -> str:
    parts = [str(rep.get("summary") or ""), str(rep.get("root_cause") or "")]
    for ev in rep.get("evidence") or []:
        if isinstance(ev, dict):
            parts.append(str(ev.get("finding") or ""))
    parts.extend(str(s) for s in rep.get("fix_steps") or [])
    return "\n".join(parts).lower()


def resolved_wos(rec: dict) -> List[dict]:
    pf = rec.get("preflight") or (rec.get("report") or {}).get("preflight") or {}
    return [r for r in pf.get("resolved") or [] if isinstance(r, dict)]


def _query_ids(rec: dict, executed_only: bool) -> set:
    out = set()
    for q in rec.get("queries") or []:
        if executed_only and not q.get("executed"):
            continue
        out.add(q.get("query_id"))
    return out


# ------------------------------------------------------------------ expected_findings checks

def check_finding(chk: dict, rec: dict) -> bool:
    rep = rec.get("report") or {}
    typ = chk.get("type")
    tw = rep.get("ticket_window_changes") or {}
    if typ == "wo_resolved":
        for r in resolved_wos(rec):
            if _int(r.get("WorkOrderId")) == _int(chk.get("work_order_id")) and \
                    (chk.get("tenant_id") is None or _int(r.get("TenantId")) == _int(chk.get("tenant_id"))):
                return True
        return False
    if typ == "manual_fix_detected":
        if not (tw.get("manual_fix") or {}).get("suspected"):
            return False
        return any(_int(c.get("old_status_id")) == _int(chk.get("from")) and _int(c.get("new_status_id")) == _int(chk.get("to"))
                   for c in tw.get("status_changes") or tw.get("changes") or [])
    if typ == "no_manual_fix":
        return not (tw.get("manual_fix") or {}).get("suspected")
    if typ == "transition_missing":
        ran = _query_ids(rec, True) & {"status_step_check", "wo_status_transition_check"}
        txt = report_text(rep)
        ids = all(re.search(r"(?<!\d)%s(?!\d)" % re.escape(str(chk.get(k))), txt) for k in ("from", "to") if chk.get(k) is not None)
        words = any(w in txt for w in ("statusstep", "status step", "transition", "geçiş", "gecis", "izin verilmiyor", "tanımlı değil"))
        return bool(ran) and ids and words
    if typ == "query_used":
        executed_only = rec.get("mode") == "live"
        return chk.get("query_id") in _query_ids(rec, executed_only)
    if typ == "mentions":
        txt = report_text(rep)
        return any(str(w).lower() in txt for w in chk.get("any") or [])
    if typ == "not_code_defect":
        return rep.get("classification") not in (None, "code_defect")
    return False


# ------------------------------------------------------------------ per ticket

def score_ticket(item: dict, rec: dict) -> dict:
    """item = eval_set entry, rec = run record (run_eval.py). A record with error/no report scores as a miss, not as skipped."""
    rep = rec.get("report") or {}
    got = rep.get("classification")
    conf = float(rep.get("confidence") or 0.0) if rep else 0.0
    exp = item["expected_classification"]
    reliable = item.get("label_confidence") in RELIABLE
    has_report = bool(rep)
    out: Dict[str, Any] = {
        "key": item["key"], "stratum": item["stratum"], "split": item["split"], "label_confidence": item.get("label_confidence"),
        "reliable": reliable, "expected": exp, "got": got, "confidence": conf, "error": rec.get("error"),
        "elapsed_s": rec.get("elapsed_s"), "llm_calls": rec.get("llm_calls"),
        "queries_total": len(rec.get("queries") or []), "queries_executed": sum(1 for q in rec.get("queries") or [] if q.get("executed")),
        "matched_card": rec.get("matched_card"),
    }
    out["exact"] = bool(has_report and got == exp)
    out["lenient"] = bool(has_report and got and lenient_match(exp, got))
    out["confidently_wrong"] = bool(has_report and got != exp and conf >= CONF_WRONG)
    out["honest_unknown"] = bool(has_report and got == "unknown" and conf < HONEST_UNKNOWN_MAX)
    # tenant / WO resolution (only when the label knows the answer)
    exp_wos = item.get("expected_work_order_ids") or []
    res = resolved_wos(rec)
    out["wo_applicable"] = bool(exp_wos)
    out["wo_ok"] = bool(exp_wos) and any(_int(r.get("WorkOrderId")) in {_int(x) for x in exp_wos} for r in res)
    out["tenant_applicable"] = item.get("expected_tenant_id") is not None
    out["tenant_ok"] = out["tenant_applicable"] and any(
        _int(r.get("TenantId")) == _int(item["expected_tenant_id"]) and _int(r.get("WorkOrderId")) in {_int(x) for x in exp_wos}
        for r in res)
    checks = item.get("expected_findings") or []
    results = [{"type": c.get("type"), "hit": bool(has_report and check_finding(c, rec))} for c in checks]
    out["findings"] = results
    out["findings_hit"] = sum(1 for r in results if r["hit"])
    out["findings_total"] = len(results)
    return out


# ------------------------------------------------------------------ aggregate

def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _avg(vals: Iterable[Any]) -> Optional[float]:
    v = [x for x in vals if isinstance(x, (int, float))]
    return round(sum(v) / len(v), 3) if v else None


def summarize(rows: List[dict]) -> dict:
    n = len(rows)
    rel = [r for r in rows if r["reliable"]]
    wo = [r for r in rows if r["wo_applicable"]]
    te = [r for r in rows if r["tenant_applicable"]]
    ft = sum(r["findings_total"] for r in rows)
    return {
        "n": n, "errors": sum(1 for r in rows if r.get("error")), "n_reliable_labels": len(rel),
        "wo_resolution_acc": _rate(sum(r["wo_ok"] for r in wo), len(wo)), "wo_n": len(wo),
        "tenant_resolution_acc": _rate(sum(r["tenant_ok"] for r in te), len(te)), "tenant_n": len(te),
        "class_exact_acc_all": _rate(sum(r["exact"] for r in rows), n),
        "class_lenient_acc_all": _rate(sum(r["lenient"] for r in rows), n),
        "class_exact_acc_reliable": _rate(sum(r["exact"] for r in rel), len(rel)),
        "class_lenient_acc_reliable": _rate(sum(r["lenient"] for r in rel), len(rel)),
        "findings_hit_rate": _rate(sum(r["findings_hit"] for r in rows), ft), "findings_total": ft,
        "honest_unknown_rate": _rate(sum(r["honest_unknown"] for r in rows), n),
        "confidently_wrong_rate_reliable": _rate(sum(r["confidently_wrong"] for r in rel), len(rel)),
        "confidently_wrong_n_reliable": sum(r["confidently_wrong"] for r in rel),
        "confidently_wrong_rate_all": _rate(sum(r["confidently_wrong"] for r in rows), n),
        "avg_elapsed_s": _avg(r.get("elapsed_s") for r in rows), "avg_queries_total": _avg(r["queries_total"] for r in rows),
        "avg_queries_executed": _avg(r["queries_executed"] for r in rows), "avg_llm_calls": _avg(r.get("llm_calls") for r in rows),
        "card_match_rate": _rate(sum(1 for r in rows if r.get("matched_card")), n),
    }


def calibration(rows: List[dict]) -> List[dict]:
    """Confidence buckets vs exact accuracy, on reliable labels only."""
    out = []
    rel = [r for r in rows if r["reliable"] and r["got"]]
    for lo, hi in BUCKETS:
        b = [r for r in rel if lo <= r["confidence"] < hi]
        out.append({"bucket": "%.1f-%.1f" % (lo, min(hi, 1.0)), "n": len(b), "accuracy": _rate(sum(r["exact"] for r in b), len(b)),
                    "mean_confidence": _avg(r["confidence"] for r in b)})
    return out


def aggregate(rows: List[dict]) -> dict:
    by_stratum: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_stratum[r["stratum"]].append(r)
    confusion: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["reliable"]:
            confusion[r["expected"]][str(r["got"])] += 1
    return {"overall": summarize(rows), "calibration": calibration(rows),
            "by_stratum": {k: summarize(v) for k, v in sorted(by_stratum.items())},
            "confusion_reliable": {k: dict(v) for k, v in sorted(confusion.items())}}


# ------------------------------------------------------------------ io + report

def load_eval_set(path: Optional[Path] = None) -> List[dict]:
    return json.loads((path or EVAL_DIR / "eval_set.json").read_text(encoding="utf-8"))["tickets"]


def load_records(path: Path) -> Dict[str, dict]:
    """results.jsonl: last record per key wins (a resumed run may append a retry)."""
    out: Dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["key"]] = r
    return out


def score_all(items: List[dict], records: Dict[str, dict], split: str = "test") -> dict:
    sel = [i for i in items if split == "all" or i["split"] == split]
    rows = [score_ticket(i, records[i["key"]]) for i in sel if i["key"] in records]
    missing = [i["key"] for i in sel if i["key"] not in records]
    res = aggregate(rows)
    res.update({"split": split, "missing_keys": missing, "tickets": rows})
    return res


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else "%.0f%%" % (100 * v)


def render_markdown(res: dict, tag: str) -> str:
    o = res["overall"]
    L = ["# HiBot evaluation results: %s" % tag, "",
         "Split: **%s**, tickets scored: **%d** (missing: %d, errors: %d). Labels are SILVER (heuristic): accuracy against low-confidence "
         "labels is reported but the headline numbers use `reliable` labels (high+medium: %d)." % (
             res["split"], o["n"], len(res["missing_keys"]), o["errors"], o["n_reliable_labels"]), "",
         "## Headline", "",
         "| metric | value |", "|---|---|",
         "| **confidently wrong** (wrong, conf >= %.1f, reliable labels) | **%s** (%d tickets) |" % (
             CONF_WRONG, _pct(o["confidently_wrong_rate_reliable"]), o["confidently_wrong_n_reliable"]),
         "| honest unknown (unknown, conf < %.1f), all tickets | %s |" % (HONEST_UNKNOWN_MAX, _pct(o["honest_unknown_rate"])),
         "| classification exact, reliable labels | %s |" % _pct(o["class_exact_acc_reliable"]),
         "| classification lenient, reliable labels | %s |" % _pct(o["class_lenient_acc_reliable"]),
         "| classification exact / lenient, all labels | %s / %s |" % (_pct(o["class_exact_acc_all"]), _pct(o["class_lenient_acc_all"])),
         "| work-order resolution (n=%d) | %s |" % (o["wo_n"], _pct(o["wo_resolution_acc"])),
         "| tenant resolution (n=%d) | %s |" % (o["tenant_n"], _pct(o["tenant_resolution_acc"])),
         "| expected_findings hit rate (%d checks) | %s |" % (o["findings_total"], _pct(o["findings_hit_rate"])),
         "| card match rate | %s |" % _pct(o["card_match_rate"]),
         "| avg elapsed s / queries (executed) / LLM calls | %s / %s (%s) / %s |" % (
             o["avg_elapsed_s"], o["avg_queries_total"], o["avg_queries_executed"], o["avg_llm_calls"]), "",
         "## Calibration (reliable labels)", "", "| confidence | n | accuracy | mean confidence |", "|---|---:|---:|---:|"]
    for b in res["calibration"]:
        L.append("| %s | %d | %s | %s |" % (b["bucket"], b["n"], _pct(b["accuracy"]), b["mean_confidence"] if b["mean_confidence"] is not None else "n/a"))
    L += ["", "## Per stratum", "",
          "| stratum | n | exact (rel.) | lenient (rel.) | tenant | findings | conf. wrong | honest unk. | avg s | avg q |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, s in res["by_stratum"].items():
        L.append("| %s | %d | %s | %s | %s | %s | %d | %s | %s | %s |" % (
            k, s["n"], _pct(s["class_exact_acc_reliable"]), _pct(s["class_lenient_acc_reliable"]), _pct(s["tenant_resolution_acc"]),
            _pct(s["findings_hit_rate"]), s["confidently_wrong_n_reliable"], _pct(s["honest_unknown_rate"]),
            s["avg_elapsed_s"], s["avg_queries_total"]))
    L += ["", "## Confusion (reliable labels; expected -> got)", ""]
    for e, g in res["confusion_reliable"].items():
        L.append("- %s: %s" % (e, ", ".join("%s=%d" % kv for kv in sorted(g.items()))))
    L += ["", "## Per ticket", "", "| key | stratum | label | expected | got | conf | findings | tenant | s | q |", "|---|---|---|---|---|---:|---:|---|---:|---:|"]
    for r in res["tickets"]:
        flag = " **CW**" if r["confidently_wrong"] and r["reliable"] else ""
        L.append("| %s | %s | %s | %s | %s%s | %.2f | %d/%d | %s | %s | %d |" % (
            r["key"], r["stratum"], r["label_confidence"], r["expected"], r["got"], flag, r["confidence"], r["findings_hit"],
            r["findings_total"], ("ok" if r["tenant_ok"] else "miss") if r["tenant_applicable"] else "-",
            r["elapsed_s"] if r["elapsed_s"] is not None else "-", r["queries_total"]))
    if res["missing_keys"]:
        L += ["", "Missing (not run): " + ", ".join(res["missing_keys"])]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--split", choices=["test", "dev", "all"], default="test")
    ap.add_argument("--run-dir", help="default: runs/eval_<tag>")
    ap.add_argument("--eval-set")
    ap.add_argument("--out-dir", help="default: knowledge/eval")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir) if a.run_dir else ROOT / "runs" / ("eval_" + a.tag)
    items = load_eval_set(Path(a.eval_set) if a.eval_set else None)
    res = score_all(items, load_records(run_dir / "results.jsonl"), a.split)
    out = Path(a.out_dir) if a.out_dir else EVAL_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / ("results_%s.json" % a.tag)).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / ("results_%s.md" % a.tag)).write_text(render_markdown(res, a.tag), encoding="utf-8")
    o = res["overall"]
    print("scored %d tickets | exact(rel) %s | lenient(rel) %s | confidently wrong %s | honest unknown %s -> %s" % (
        o["n"], _pct(o["class_exact_acc_reliable"]), _pct(o["class_lenient_acc_reliable"]),
        _pct(o["confidently_wrong_rate_reliable"]), _pct(o["honest_unknown_rate"]), out / ("results_%s.md" % a.tag)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
