"""Add expected_root_cause_type / expected_resolution_action / axes_rule to eval_set.json by the rules of
knowledge/eval/label_axes_rules.md (mechanical, idempotent).

    python tools/eval/add_axes.py [--eval-set PATH] [--windows PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "knowledge" / "eval" / "eval_set.json"
WINDOWS = ROOT / "knowledge" / "learning" / "ticket_windows.jsonl"

NO_FOOTPRINT_RE = re.compile(r"no change|no log rows|no tracked field change", re.I)


def load_transition_facts(path: Path) -> Dict[Tuple[str, str, str], bool]:
    """(ticket key, from, to) -> transition_allowed, from the local footprint file (empty when missing)."""
    facts: Dict[Tuple[str, str, str], bool] = {}
    if not path.exists():
        return facts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for wo in r.get("wos") or []:
            for c in (wo.get("window") or {}).get("status_changes") or []:
                if c.get("transition_allowed") is not None:
                    facts[(r["key"], str(c.get("from")), str(c.get("to")))] = bool(c["transition_allowed"])
    return facts


def transition_allowed(item: dict, facts: Dict[Tuple[str, str, str], bool]) -> Optional[bool]:
    for f in item.get("expected_findings") or []:
        if f.get("type") == "manual_fix_detected":
            v = facts.get((item["key"], str(f.get("from")), str(f.get("to"))))
            if v is not None:
                return v
    basis = item.get("label_basis") or ""
    if "transition not in StatusStep" in basis:
        return False
    if "step exists" in basis:
        return True
    return None


def axes_for(item: dict, facts: Dict[Tuple[str, str, str], bool]) -> Tuple[str, str, str]:
    """(root_cause_type, resolution_action, rule id)."""
    cl = item["expected_classification"]
    basis = "%s %s" % (item.get("label_basis") or "", item.get("note") or "")
    if cl == "master_data":
        return "master_data", "master_data_change", "R1"
    if cl == "access":
        return "access", "grant_access", "R2"
    if cl == "user_error":
        return "user_error", "explain_to_user", "R3"
    if cl == "code_defect":
        return "code_defect", "code_change", "R4"
    if cl == "integration":
        return ("integration", "escalate_other_system", "R5a") if re.search(r"sap|atlas", basis, re.I) else ("integration", "unknown", "R5b")
    if cl == "config":
        return "config", "config_change", "R6"
    if cl == "unknown":
        return "unknown", "unknown", "R7"
    if cl == "change_request":                       # round 2 rule R9 (label_axes_rules.md); only new tickets carry this class
        return "change_request", "route_to_backlog", "R9"
    if cl == "data_correction":
        manual = any(f.get("type") == "manual_fix_detected" for f in item.get("expected_findings") or [])
        ta = transition_allowed(item, facts) if manual else None
        if manual and ta is False:
            return "config", "data_fix", "R8a"
        if manual and ta is True:
            return "data_issue", "data_fix", "R8b"
        if NO_FOOTPRINT_RE.search(item.get("label_basis") or ""):
            return "unknown", "data_fix", "R8c"
        return "data_issue", "data_fix", "R8d"
    raise ValueError("unmapped expected_classification %r (%s)" % (cl, item["key"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-set", default=str(EVAL_SET))
    ap.add_argument("--windows", default=str(WINDOWS))
    a = ap.parse_args(argv)
    path = Path(a.eval_set)
    data = json.loads(path.read_text(encoding="utf-8"))
    facts = load_transition_facts(Path(a.windows))
    counts: Dict[str, int] = {}
    for it in data["tickets"]:
        rct, ra, rule = axes_for(it, facts)
        it["expected_root_cause_type"], it["expected_resolution_action"], it["axes_rule"] = rct, ra, rule
        counts[rule] = counts.get(rule, 0) + 1
    data.setdefault("meta", {})["axes_rules"] = "knowledge/eval/label_axes_rules.md"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("axes added to %d tickets; rule counts: %s" % (len(data["tickets"]), ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
