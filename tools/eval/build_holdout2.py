"""Build knowledge/eval/holdout2.json: the second blind hold-out set (improvement round 3, D19).

Same silver-label procedure as tools/eval/build_eval_set.py (hand-curated LABELS table from the Jira resolution, comments and the DB footprint in
knowledge/learning/ticket_windows.jsonl), then the SAME mechanical axes rules (tools/eval/add_axes.py + knowledge/eval/label_axes_rules.md, R1-R9).
Selection rules (written before any tuning, no HiBot output was looked at):
  * Sirius EU tickets from knowledge/incidents/tickets_enriched.json, resolution Done;
  * NOT in knowledge/eval/eval_set.json and NOT mentioned in knowledge/skills/*.md, knowledge/cards/*.json, project/*.md, CLAUDE.md or knowledge/eval/*;
  * stratified like the existing set; high/medium label confidence preferred (3 low ones are marked needs_human_review).

    python tools/eval/build_holdout2.py            # writes holdout2.json (refuses to overwrite a different frozen file unless --force)
    python tools/eval/build_holdout2.py --verify   # checks the sha256 recorded in knowledge/eval/README.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.eval import add_axes                                    # noqa: E402
from tools.eval.build_eval_set import (M, NCD, Q, S_ACC, S_FOOT, S_FUNC, S_INT, S_MD, S_NOCHG, S_NOT, S_PERF,  # noqa: E402
                                       TICKETS, WINDOWS, _int)

OUT = ROOT / "knowledge" / "eval" / "holdout2.json"
README = ROOT / "knowledge" / "eval" / "README.md"

# key: (stratum, class, label_confidence, label_basis, note (own words, <=120), extra_findings)
LABELS = {
    # ---- status / data_correction, footprint (manual fix in the DB)
    "SM-1977792": (S_FOOT, "data_correction", "medium", "footprint: manual fix 2271->2284, transition not in StatusStep", "WO closed by mistake; asks to move it to Beko Approved; status forced in DB", []),
    "SM-1980996": (S_FOOT, "data_correction", "medium", "footprint: manual fix 2275->2284, transition not in StatusStep", "asks status Beko Approved for a closed WO; status forced in DB", []),
    "SM-2044656": (S_FOOT, "data_correction", "medium", "footprint: manual fix 651->638 (step exists), no history row", "asks to reject two WOs; status forced in DB", []),
    "SM-2103087": (S_FOOT, "data_correction", "medium", "footprint: manual fix 896->901, transition not in StatusStep", "asks to move an exchange-approved WO to rejected; status forced in DB", []),
    "SM-2139680": (S_FOOT, "data_correction", "medium", "footprint: manual fix 2232->704, transition not in StatusStep", "asks to put a WO back to New Application; status forced in DB", []),
    "SM-1969942": (S_FOOT, "data_correction", "medium", "footprint: manual fix 1123->848, transition not in StatusStep", "technician could not change a WO status; status forced in DB", []),
    # ---- status / data_correction, no change in the ticket window
    "SM-1802347": (S_NOCHG, "data_correction", "medium", "status change request, comment 'solved'; no change inside window", "asks status Financed for a WO", []),
    "SM-1871288": (S_NOCHG, "data_correction", "medium", "status change request, comment 'solved'; no change inside window", "asks status New for a WO", []),
    "SM-1943978": (S_NOCHG, "data_correction", "medium", "bulk status change request, comment 'solved'; no change inside window", "asks status Repair Completed for several WOs", []),
    "SM-1957195": (S_NOCHG, "data_correction", "medium", "status change request, comment 'changed'; no change inside window", "asks status New Application for several WOs", []),
    "SM-2039109": (S_NOCHG, "data_correction", "medium", "WOs in a wrong status, comment 'changed'; no change inside window", "asks to fix WOs that ended in Exchange FI Created", []),
    # ---- functional_defect
    "SM-1839332": (S_FUNC, "master_data", "medium", "comment: price group of the material not yet entered, so it is not visible", "newly defined material code cannot be selected in Sirius", [M("price", "material")]),
    "SM-1934122": (S_FUNC, "master_data", "medium", "comment: price definitions missing for these materials for this service shop", "two materials cannot be selected on a Moldova WO", [M("price", "material")]),
    "SM-1870003": (S_FUNC, "user_error", "medium", "comment: product history empty because the WO has no production year/month; feature filters those out", "repair history of a product not shown", [NCD, M("production", "history")]),
    "SM-2080016": (S_FUNC, "config", "medium", "comment: service shop sits in a WHP labour-tariff payment group; payment group has to change", "labour options for a Beko-defined appliance not offered", [M("payment group", "tariff", "labour")]),
    "SM-1881470": (S_FUNC, "user_error", "medium", "comment: Whirlpool serial format is XXYYMMSSSSSS; entered format differs", "serial number cannot be added on a service call", [NCD, M("serial")]),
    "SM-1979237": (S_FUNC, "user_error", "medium", "comment: serial entered in Sirius differs from the one on the product", "serial number cannot be added on a Moldova call", [NCD, M("serial")]),
    "SM-1886324": (S_FUNC, "master_data", "medium", "comment: no warranty period defined for the chosen warranty type; period added for the product", "wrong warranty type on a WC, cannot be resolved", [M("warranty")]),
    # ---- integration_failure
    "SM-1924609": (S_INT, "integration", "medium", "comment: material is in neither Manusoft nor Sirius; must be added in SAP", "part defined in SAP cannot be selected in Sirius", [M("sap")]),
    "SM-1960355": (S_INT, "integration", "medium", "comment: material must be defined in the right plant / sales area in SAP", "material order problem: part data in SAP, order fails", [M("sap", "material")]),
    "SM-1976131": (S_INT, "integration", "medium", "comment: SAP shows no stock at the storage location for the exchange material", "exchange not sent to SAP (four WOs stuck)", [M("sap", "stock", "exchange")]),
    "SM-1905442": (S_INT, "integration", "low", "comment: preventive action on Sirius and SAP side against duplicate SAP orders", "one spare order creates several SAP orders", [M("sap", "order")]),
    "SM-1917116": (S_INT, "user_error", "medium", "comment: process is to select exchange type first, then update status", "product exchange not transferred to SAP", [NCD, M("exchange")]),
    # ---- master_data_request
    "SM-2083959": (S_MD, "master_data", "high", "ticket asks to load an SKU; 'added'", "asks to add an SKU for South Africa", [M("sku", "product"), Q("product_sku_lookup")]),
    "SM-2103728": (S_MD, "master_data", "high", "ticket asks to register SKU and part list for a model; solved", "asks to register an SKU and part list", [M("sku", "part")]),
    "SM-2029368": (S_MD, "master_data", "high", "ticket asks to add an SKU for a WO in Slovenia; 'added'", "asks to add one SKU for Slovenia", [M("sku"), Q("product_sku_lookup")]),
    "SM-2121046": (S_MD, "master_data", "high", "comment: product data missing in the Sirius DB; added", "product cannot be reached on a WO in Azerbaijan", [M("sku", "product")]),
    "SM-2065664": (S_MD, "master_data", "medium", "comment: products had empty commercial-code values; corrected, labour list restored", "labour list missing on a Bosnia WO", [M("labour", "hierarchy", "commercial")]),
    "SM-1954815": (S_MD, "master_data", "high", "ticket asks to add product description and tenants for an SKU; solved", "asks to add description and tenant for an SKU", [M("sku", "tenant", "product")]),
    # ---- access_request
    "SM-1831956": (S_ACC, "access", "high", "comment: account was activated", "business partner cannot log in; account inactive", [M("user", "account", "login")]),
    "SM-1829822": (S_ACC, "access", "medium", "comment: user added with the registry-based login instead of the D user", "cannot log on CRM and Portal", [M("user", "log", "access")]),
    "SM-1850411": (S_ACC, "access", "medium", "comment: user must be registered via Employee menu; Okta authorization to follow", "asks for access to Sirius", [M("user", "okta", "access", "authoriz")]),
    "SM-1824360": (S_ACC, "access", "medium", "comment: Sirius Okta authorization already exists; request is Okta-side", "cannot log in to Sirius", [M("user", "okta", "access", "authoriz")]),
    # ---- notification_failure
    "SM-2042803": (S_NOT, "config", "medium", "comment: misconfiguration in the email job, fixed", "notification emails not sent for Ukraine", [M("email", "notification", "job")]),
    "SM-1931803": (S_NOT, "integration", "medium", "comment: SMS provider changed its route, then NPS SMS delivered", "NPS survey SMS not received by customers", [M("sms", "nps", "provider", "route")]),
    "SM-2105830": (S_NOT, "user_error", "medium", "comment: Sirius side shows nothing wrong; recipient domain / mail log to be checked", "notification mails not received though report is fine", [NCD, M("notification", "mail", "email")]),
    "SM-1932075": (S_NOT, "integration", "medium", "comment: counts consistent at the source; the Atlas side should be reviewed", "NPS survey data in Atlas looks wrong", [M("nps", "atlas")]),
    # ---- performance / report
    "SM-2071460": (S_PERF, "user_error", "medium", "comment: tests on the report found no issue; download works", "SAP integration failed-docs report downloads empty", [NCD, M("report")]),
    "SM-1833067": (S_PERF, "user_error", "medium", "comment: checks done, no problem found", "blank screen on portal log-on", [NCD]),
    "SM-2059766": (S_PERF, "unknown", "low", "support found no problem with the user; instability complaints continue", "technician app freezes for many technicians", [M("freeze", "unstable", "slow", "performance")]),
}


def build() -> dict:
    tickets = {t["key"]: t for t in json.loads(TICKETS.read_text(encoding="utf-8"))["tickets"]}
    wins = {}
    if WINDOWS.exists():
        for line in WINDOWS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                w = json.loads(line)
                wins[w["key"]] = w
    existing = {t["key"] for t in json.loads((ROOT / "knowledge" / "eval" / "eval_set.json").read_text(encoding="utf-8"))["tickets"]}
    dup = existing & set(LABELS)
    if dup:
        raise SystemExit("error: tickets already in eval_set.json: %s" % sorted(dup))
    items = []
    for key, (stratum, cls, conf, basis, note, extra) in LABELS.items():
        t = tickets[key]
        w = wins.get(key) if wins.get(key, {}).get("status") == "ok" else None
        wos = list((w or {}).get("wos", []))
        wo_ids = [o["work_order_id"] for o in wos] or [_int(x) for x in (t.get("entities") or {}).get("work_order_ids_strong") or []]
        wo_ids = [_int(x) for x in wo_ids if _int(x)]
        tenant = _int(wos[0]["tenant_id"]) if wos else None
        findings = []
        if wos:
            findings.append({"type": "wo_resolved", "work_order_id": wos[0]["work_order_id"], "tenant_id": tenant})
            for o in wos:
                sc = o["window"].get("status_changes") or []
                if sc and o["window"].get("manual_level") == "high":
                    c = sc[0]
                    findings.append({"type": "manual_fix_detected", "from": _int(c["from"]), "to": _int(c["to"])})
                    if c.get("transition_allowed") is False:
                        findings.append({"type": "transition_missing", "tenant_id": _int(o["tenant_id"]), "from": _int(c["from"]), "to": _int(c["to"])})
                    break
            if stratum == S_NOCHG and not any(f["type"] == "manual_fix_detected" for f in findings):
                findings.append({"type": "no_manual_fix"})
        for f in extra:                                       # query_used checks only where the ticket really carries the entity
            if f.get("type") == "query_used":
                ents = t.get("entities") or {}
                if f["query_id"] == "product_sku_lookup" and not ents.get("sku_codes"):
                    continue
            findings.append(f)
        findings = findings[:3]
        items.append({
            "key": key, "split": "test", "stratum": stratum, "region": "EU",
            "problem_type": t.get("problem_type"), "app": t.get("app_guess"),
            "expected_work_order_ids": wo_ids, "expected_tenant_id": tenant,
            "expected_classification": cls, "label_confidence": conf, "label_basis": basis,
            "expected_findings": findings, "needs_human_review": conf == "low", "note": note[:120],
        })
    meta = {"version": 1, "n": len(items), "label_kind": "silver (heuristic; not developer-verified)", "set": "holdout2",
            "classes": ["code_defect", "master_data", "access", "data_correction", "integration", "config", "user_error", "change_request", "unknown"]}
    return {"meta": meta, "tickets": items}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.verify:
        m = re.search(r"holdout2\.json sha256: `([0-9a-f]{64})`", README.read_text(encoding="utf-8"))
        if not m:
            print("no sha256 recorded in README")
            return 2
        ok = sha256(OUT) == m.group(1)
        print("holdout2.json sha256 %s" % ("MATCHES the README" if ok else "DIFFERS from the README (%s)" % sha256(OUT)))
        return 0 if ok else 1
    if OUT.exists() and not a.force:
        print("holdout2.json exists (frozen); use --verify, or --force to rebuild")
        return 1
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rc = add_axes.main(["--eval-set", str(OUT)])
    print("wrote %d tickets, sha256 %s" % (len(data["tickets"]), sha256(OUT)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
