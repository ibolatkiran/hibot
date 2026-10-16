"""Build knowledge/eval/eval_set.json + labeling_notes.md from the hand-curated LABELS table below.

Silver labels only (no gold): derived from the DB footprint (knowledge/learning/ticket_windows.jsonl, git-ignored local file),
the Jira resolution, problem_type and scrubbed ticket text/comments in knowledge/incidents/tickets_enriched.json.
No names, no free ticket text: `note` is a short own-words paraphrase (<= 120 chars).

    python tools/eval/build_eval_set.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "knowledge" / "eval"
TICKETS = ROOT / "knowledge" / "incidents" / "tickets_enriched.json"
WINDOWS = ROOT / "knowledge" / "learning" / "ticket_windows.jsonl"

DEV = ["SM-2139009", "SM-2139002", "SM-2122480", "SM-2126600", "SM-1777386"]

S_FOOT, S_NOCHG = "status_footprint", "status_nochange"
S_FUNC, S_INT, S_MD, S_ACC, S_NOT, S_PERF = ("functional_defect", "integration_failure", "master_data_request",
                                            "access_request", "notification_failure", "performance_report")


def M(*w):
    return {"type": "mentions", "any": list(w)}


def Q(q):
    return {"type": "query_used", "query_id": q}


NCD = {"type": "not_code_defect"}

# key: (stratum, class, label_confidence, label_basis, note (own words, <=120), extra_findings)
LABELS = {
    # ---- dev split (already used while developing HiBot; do not treat as blind)
    "SM-2139009": (S_FOOT, "data_correction", "medium", "footprint: manual fix 329->314, transition not in StatusStep", "cancel blocked at 'no proof of money'; DB fix", []),
    "SM-2139002": (S_FOOT, "data_correction", "medium", "footprint: manual fix 329->314, transition not in StatusStep", "same cancel request as sibling ticket", []),
    "SM-2126600": (S_FOOT, "user_error", "high", "D17: T70 2271->2257 closed on purpose; devs forced it by hand", "asks status Transfer on a financed WO; blocked by design", []),
    "SM-2122480": (S_NOCHG, "integration", "low", "no change in window; comment only 'solved'; text claims Atlas mismatch", "status differs between Sirius and Atlas", [M("atlas")]),
    "SM-1777386": (S_FUNC, "code_defect", "low", "Jira Won't Do: closed as related change request", "WO history shows one record after reassignment (APAC)", [M("history")]),
    # ---- status / data_correction, high footprint
    "SM-2143299": (S_FOOT, "data_correction", "medium", "footprint: manual fix 692->695, transition not in StatusStep", "asks to reject a WO; status forced in DB", []),
    "SM-2132761": (S_FOOT, "data_correction", "medium", "footprint: manual fix 901->888, no history row", "asks to cancel a WO; status forced in DB", []),
    "SM-2100037": (S_FOOT, "data_correction", "medium", "footprint: manual fix 653->651 (step exists), no history row", "asks to reject a WO that has an error; status forced in DB", []),
    "SM-1967154": (S_FOOT, "data_correction", "medium", "footprint: manual fix 2222->707, transition not in StatusStep", "asks to move WO back to New Application", []),
    "SM-2070070": (S_FOOT, "user_error", "high", "D17: T70 2271->2257 closed on purpose; devs forced it by hand", "asks Transfer status for a financed WO; blocked by design", []),
    # ---- status / data_correction, no change in the ticket window
    "SM-2142402": (S_NOCHG, "data_correction", "low", "no log rows for WO; comment only 'solved'", "asks to reject a WO that was set up wrongly", []),
    "SM-2122464": (S_NOCHG, "data_correction", "medium", "status change request, comment 'completed'; no change inside window", "asks status Exchange required", []),
    "SM-2010200": (S_NOCHG, "data_correction", "medium", "bulk status change request, comment 'completed'; no change inside window", "asks status Transfer for several WOs", []),
    "SM-2147977": (S_NOCHG, "data_correction", "medium", "status change request, comment 'DONE'; no change inside window", "asks status Beko Approved for several WOs", []),
    "SM-2137438": (S_NOCHG, "data_correction", "medium", "comment: serial number fixed in system; no tracked field change", "asks to correct a WO serial number", []),
    # ---- functional_defect
    "SM-2143981": (S_FUNC, "master_data", "medium", "comment: product-tenant relation updated, retry", "alternate model cannot be picked in an exchange", [M("sku", "product", "tenant"), Q("product_sku_lookup")]),
    "SM-2112749": (S_FUNC, "master_data", "medium", "comment: service shop had wrong storage location, updated", "quoted error: material not available at warehouse on parts order", [M("storage", "warehouse", "service shop", "servis")]),
    "SM-2128512": (S_FUNC, "user_error", "medium", "comment: no issue found on system side, temporary", "search and create reported broken; support sees no problem", [NCD]),
    "SM-2070960": (S_FUNC, "master_data", "high", "comment: no labour definition for product hierarchy", "labour charge not shown for a model", [M("labour", "labor", "hierarchy")]),
    "SM-2092603": (S_FUNC, "user_error", "medium", "comment: field behaves so by design (asset history feature)", "sale date pulled wrongly from asset history", [NCD]),
    "SM-2104030": (S_FUNC, "unknown", "low", "quoted error 'Unexpected Error'; comment only 'resolved'", "unexpected error when completing a repair", [M("unexpected error")]),
    "SM-2088913": (S_FUNC, "master_data", "low", "labour missing on WO; comment only 'resolved'", "labour items not offered on a Bosnia WO", [M("labour", "labor")]),
    "SM-2082092": (S_FUNC, "data_correction", "low", "wrong warranty type on WO; comment only 'solved'", "asks approval of WO showing wrong warranty type", [M("warranty")]),
    # ---- integration_failure
    "SM-2146596": (S_INT, "integration", "medium", "recurring error 'missing FY Document'; comment 'added'", "invoice cannot go out: missing FY document", [M("fy"), Q("wo_erp_sap")]),
    "SM-2144810": (S_INT, "integration", "medium", "comment: Sirius fixes done, SAP returned sales-document error", "warranty package sent to SAP creates no SD document", [M("sap"), Q("sap_rfc_rest_calls")]),
    "SM-2122065": (S_INT, "integration", "medium", "same recurring 'missing FY Document' group; comment 'solved'", "WO with missing FY document error", [M("fy"), Q("wo_erp_sap")]),
    "SM-2118792": (S_INT, "integration", "low", "FI package send to SAP errors for 3 partners; resolved, no detail", "FI package fails to send to SAP for a few partners", [M("fi", "package", "sap")]),
    "SM-2100070": (S_INT, "data_correction", "medium", "comment: package updated and error resolved ('FI document not balanced')", "report transfer to SAP fails: FI document not balanced", [M("fi", "balanced", "package")]),
    "SM-2120545": (S_INT, "data_correction", "low", "comment: necessary additions made (virtual stock); little detail", "virtual stock says no parts although SAP has them", [M("stock")]),
    # ---- master_data_request
    "SM-2133110": (S_MD, "master_data", "high", "ticket asks to add SKUs; solved", "asks to add new SKUs for Serbia", [M("sku", "product"), Q("product_sku_lookup")]),
    "SM-2092729": (S_MD, "master_data", "high", "ticket asks to add SKU for Baltics tenant; 'done'", "asks to add one SKU for Baltics", [M("sku", "tenant"), Q("product_sku_lookup")]),
    "SM-2102064": (S_MD, "master_data", "high", "ticket asks to add product code; 'added'", "asks to add one product code for Serbia", [M("sku", "product"), Q("product_sku_lookup")]),
    "SM-2123794": (S_MD, "master_data", "medium", "Jira Won't Do: model/material must be defined in SAP first", "asks to register an SKU and part list for a model", [M("sap", "material", "sku")]),
    "SM-2070909": (S_MD, "master_data", "medium", "no defect codes offered for a WO; resolved", "defect codes not shown so job cannot be completed", [M("defect code", "defect")]),
    "SM-2135453": (S_MD, "config", "low", "comment: labour coefficient only applies from its configuration date", "labour coefficient of a shop not applied on repair", [M("coefficient", "multiplier", "tariff")]),
    # ---- access_request
    "SM-2143363": (S_ACC, "access", "high", "comment: disabled account was activated", "user cannot log in; account blocked", [M("user", "account", "okta", "disabled"), Q("user_access_lookup")]),
    "SM-2122261": (S_ACC, "access", "medium", "comment: Okta rights exist, app authorization to be defined by country admin", "no portal access for a store user", [M("user", "okta", "authoriz", "access"), Q("user_access_lookup")]),
    "SM-2132602": (S_ACC, "access", "medium", "comment: user account was deleted; access restore requested", "restore access to two country segments for call-centre users", [M("user", "account", "access", "deleted"), Q("user_access_lookup")]),
    "SM-2142046": (S_ACC, "access", "medium", "quoted 'not authorized to login'; resolved", "login rejected as not authorized", [M("user", "authoriz", "access", "role"), Q("user_access_lookup")]),
    # ---- notification_failure
    "SM-2001523": (S_NOT, "config", "high", "comment: configuration updated (NPS status condition), unsent SMS sent", "NPS SMS stopped after sending condition was changed", [M("nps", "sms", "notification"), Q("notifications_for_wo")]),
    "SM-2091967": (S_NOT, "user_error", "medium", "comment: mail sent fine by Sirius, recipient mailbox issue", "partner does not receive notifications", [NCD, M("notification", "mail", "email")]),
    "SM-2127733": (S_NOT, "config", "low", "welcome and NPS SMS not sent; comment only 'resolved'", "welcome and NPS SMS not sent for a test order", [M("sms", "nps", "notification"), Q("notifications_for_wo")]),
    # ---- performance / report
    "SM-2147237": (S_PERF, "unknown", "low", "page not loading on one WO; comment only 'resolved'", "exchange approval page does not load for one WO", []),
    "SM-2094146": (S_PERF, "user_error", "high", "comment: repairs with that status are excluded from the report by design", "repair missing from a report", [NCD, M("report")]),
    "SM-2061644": (S_PERF, "unknown", "low", "support sees no performance issue; intermittent slowness", "slow system across CRM, Portal and Partner", [M("slow", "performance", "timeout")]),
}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build():
    tickets = {t["key"]: t for t in json.loads(TICKETS.read_text(encoding="utf-8"))["tickets"]}
    wins = {}
    if WINDOWS.exists():
        for line in WINDOWS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                w = json.loads(line)
                wins[w["key"]] = w
    items, notes = [], []
    for key, (stratum, cls, conf, basis, note, extra) in LABELS.items():
        t = tickets[key]
        w = wins.get(key) if wins.get(key, {}).get("status") == "ok" else None
        wos = list((w or {}).get("wos", []))
        wo_ids = [o["work_order_id"] for o in wos] or [_int(x) for x in (t.get("entities") or {}).get("work_order_ids_strong") or []]
        wo_ids = [x for x in wo_ids if x]
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
                        findings.append({"type": "transition_missing", "tenant_id": _int(o["tenant_id"]),
                                         "from": _int(c["from"]), "to": _int(c["to"])})
                    break
            if stratum == S_NOCHG and not any(f["type"] == "manual_fix_detected" for f in findings):
                findings.append({"type": "no_manual_fix"})
        findings.extend(extra)
        if cls == "user_error" and stratum == S_FOOT:
            findings = findings[:2] + [NCD]
        findings = findings[:3]
        region = "APAC" if key == "SM-1777386" else "EU"
        split = "dev" if key in DEV else "test"
        items.append({
            "key": key, "split": split, "stratum": stratum, "region": region,
            "problem_type": t.get("problem_type"), "app": t.get("app_guess"),
            "expected_work_order_ids": wo_ids, "expected_tenant_id": tenant,
            "expected_classification": cls, "label_confidence": conf, "label_basis": basis,
            "expected_findings": findings, "needs_human_review": conf == "low", "note": note[:120],
        })
        notes.append("- %s [%s/%s, %s] %s -> %s: %s" % (key, split, stratum, conf, t.get("resolution"), cls, basis))
    meta = {"version": 1, "n": len(items), "label_kind": "silver (heuristic; not developer-verified)",
            "classes": ["code_defect", "master_data", "access", "data_correction", "integration", "config", "user_error", "unknown"]}
    return {"meta": meta, "tickets": items}, notes


def main() -> int:
    data, notes = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "eval_set.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    head = ("# Labeling notes (silver labels)\n\nOne line per ticket: key [split/stratum, label confidence] Jira resolution -> expected class: basis.\n"
            "No PII; generated by tools/eval/build_eval_set.py.\n\n")
    (OUT / "labeling_notes.md").write_text(head + "\n".join(notes) + "\n", encoding="utf-8")
    print("wrote %d tickets" % len(data["tickets"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
