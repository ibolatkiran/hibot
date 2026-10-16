"""Offline pattern mining over knowledge/learning/ticket_windows.jsonl (output of batch_footprint.py).  No DB, no LLM.

    python tools/learning/mine_patterns.py            # summary.md + draft cards
    python tools/learning/mine_patterns.py --no-cards

Produces knowledge/learning/summary.md (tracked) and knowledge/cards/draft_footprint_<slug>.json (status "draft",
created_by "batch_footprint"; never overwrites a card that is not such a draft).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hibot.agent import cards  # noqa: E402

LEARN = ROOT / "knowledge" / "learning"
JSONL = LEARN / "ticket_windows.jsonl"
SUMMARY = LEARN / "summary.md"
TICKETS = ROOT / "knowledge" / "incidents" / "tickets_enriched.json"
CREATED_BY = "batch_footprint"
MIN_TICKETS = 2
MAX_DRAFT_CONF = 0.7
CHAIN_SECONDS = 120
FIELD_FOCUS = ("ServiceShopId", "EmployeeId", "WarrantyTypeId")
STOP = set("the and for with from that this have has not can cannot unable are was were job order please issue sirius "
           "when after into your our you cant dont does show shows error about need help hello regards thanks "
           "ile icin bir ama veya olarak yapilamiyor".split())


# ------------------------------------------------------------------ loading

def load_records(path: Path = JSONL) -> List[dict]:
    """jsonl -> records, one per ticket key (a later line replaces an earlier one)."""
    by_key: Dict[str, dict] = {}
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("key"):
                    by_key[r["key"]] = r
    except OSError:
        pass
    return list(by_key.values())


def _day(s: Optional[str]) -> str:
    return str(s or "")[:10]


# ------------------------------------------------------------------ mining

def is_suspect(c: dict) -> bool:
    """A status change in the window that has no application history row (the agent's heuristic for a manual DB fix)."""
    return bool(c.get("status_change")) and c.get("history_row_found") is False


def _secs(at: Optional[str]) -> Optional[float]:
    import datetime as _dt
    try:
        return _dt.datetime.strptime(at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def in_chain(c: dict, status_changes: List[dict]) -> bool:
    """True when another status change of the same work order happened within CHAIN_SECONDS (job / trigger / automatic workflow look)."""
    t = _secs(c.get("at_utc"))
    if t is None:
        return False
    return any(o is not c and o.get("log_id") != c.get("log_id") and _secs(o.get("at_utc")) is not None
               and abs(_secs(o["at_utc"]) - t) <= CHAIN_SECONDS for o in status_changes)


def mine(records: Iterable[dict]) -> Dict[str, Any]:
    recs = list(records)
    statuses = collections.Counter(r.get("status") for r in recs)
    ok = [r for r in recs if r.get("status") == "ok"]
    funnel = {
        "processed": len(recs), "not_found_eu": statuses.get("not_found_eu", 0), "error": statuses.get("error", 0),
        "found_in_eu": len(ok),
        "with_window_change": sum(1 for r in ok if r.get("any_change_in_window")),
        "with_status_change": sum(1 for r in ok if r.get("any_status_change")),
        "manual_high": sum(1 for r in ok if r.get("manual_level") == "high"),
        "manual_medium": sum(1 for r in ok if r.get("manual_level") == "medium"),
        "no_change_in_window": sum(1 for r in ok if not r.get("any_change_in_window")),
    }

    pats: Dict[Tuple, dict] = {}
    fields: Dict[Tuple, dict] = {}
    base_all = {"in_status": 0, "in_nohist": 0, "out_status": 0, "out_nohist": 0}
    by_pt: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in ok:
        pt = r.get("problem_type") or "other"
        by_pt[pt]["tickets"] += 1
        by_pt[pt]["no_change"] += 0 if r.get("any_change_in_window") else 1
        by_pt[pt]["manual_high"] += 1 if r.get("manual_level") == "high" else 0
        by_pt[pt]["manual_medium"] += 1 if r.get("manual_level") == "medium" else 0
        seen_p, seen_f = set(), set()
        for w in r.get("wos") or []:
            b = w.get("window")
            if not b:
                continue
            base_all["out_status"] += b["outside"]["status_changes"]
            base_all["out_nohist"] += b["outside"]["no_history"]
            tenant = w.get("tenant_id")
            for c in b["changes"]:
                if c.get("status_change"):
                    base_all["in_status"] += 1
                    base_all["in_nohist"] += 1 if is_suspect(c) else 0
                    if is_suspect(c):
                        k = (tenant, str(c["from"]), str(c["to"]), c.get("transition_allowed"))
                        p = pats.setdefault(k, {"tenant": tenant, "tenant_name": w.get("tenant_name"), "from": k[1], "to": k[2],
                                                "allowed": k[3], "from_name": c.get("from_name"), "to_name": c.get("to_name"),
                                                "tickets": {}, "high": set(), "chained": set(), "out_status": 0, "out_nohist": 0, "seen_wo": set()})
                        p["tickets"].setdefault(r["key"], r)
                        if c.get("update_user_unchanged"):
                            p["high"].add(r["key"])
                        if in_chain(c, b["status_changes"]):
                            p["chained"].add(r["key"])
                        if (r["key"], w.get("work_order_id")) not in p["seen_wo"]:
                            p["seen_wo"].add((r["key"], w.get("work_order_id")))
                            p["out_status"] += b["outside"]["status_changes"]
                            p["out_nohist"] += b["outside"]["no_history"]
                        seen_p.add(k)
                # field-level corrections performed without a user-attributed update
                if "UpdateUserId" not in (c.get("fields") or []):
                    for f in c.get("fields") or []:
                        if f in ("StatusId",):
                            continue
                        fk = (tenant, f)
                        if fk not in seen_f:
                            seen_f.add(fk)
                            d = fields.setdefault(fk, {"tenant": tenant, "field": f, "tickets": {}})
                            d["tickets"].setdefault(r["key"], r)
    patterns = []
    for p in pats.values():
        ts = p["tickets"]
        dates = sorted(_day(t.get("created")) for t in ts.values())
        patterns.append({
            "tenant": p["tenant"], "tenant_name": p["tenant_name"], "from": p["from"], "to": p["to"], "allowed": p["allowed"],
            "from_name": p["from_name"], "to_name": p["to_name"], "count": len(ts), "keys": sorted(ts, reverse=True),
            "high": len(p["high"]), "chained": len(p["chained"]), "first": dates[0], "last": dates[-1],
            "problem_types": dict(collections.Counter(t.get("problem_type") or "other" for t in ts.values()).most_common()),
            "apps": dict(collections.Counter(t.get("app_guess") or "unknown" for t in ts.values()).most_common()),
            "out_status": p["out_status"], "out_nohist": p["out_nohist"],
        })
    patterns.sort(key=lambda p: (-p["count"], -p["high"], str(p["tenant"]), p["from"], p["to"]))
    fpat = []
    for d in fields.values():
        ts = d["tickets"]
        fpat.append({"tenant": d["tenant"], "field": d["field"], "count": len(ts), "keys": sorted(ts, reverse=True)[:5],
                     "focus": d["field"] in FIELD_FOCUS})
    fpat.sort(key=lambda p: (-p["count"], str(p["tenant"]), p["field"]))
    return {"funnel": funnel, "patterns": patterns, "field_patterns": fpat, "baseline": base_all,
            "by_problem_type": {k: dict(v) for k, v in sorted(by_pt.items(), key=lambda kv: -kv[1]["tickets"])}}


# ------------------------------------------------------------------ cards

def summary_keywords(keys: Iterable[str], tickets: Dict[str, dict], extra: Iterable[str] = (), top: int = 5) -> List[str]:
    """Words (>=4 letters) present in >= 40% of the tickets' scrubbed summaries, plus status names that occur in them."""
    keys = [k for k in keys if k in tickets]
    texts = [cards.fold((tickets[k].get("text_clean") or {}).get("summary") or "") for k in keys]
    out: List[str] = []
    if texts:
        df: collections.Counter = collections.Counter()
        for t in texts:
            df.update({w for w in re.findall(r"[a-z]{4,}", re.sub(r"<[^>]*>", " ", t)) if w not in STOP})
        need = max(2, int(0.4 * len(texts) + 0.999)) if len(texts) > 1 else 1
        out = [w for w, n in sorted(df.items(), key=lambda kv: (-kv[1], kv[0])) if n >= need][:top]
    for e in extra:
        f = cards.fold(e or "").strip()
        if f and len(f) >= 4 and f not in out and any(f in t for t in texts):
            out.append(f)
    return out[:top + 2]


def _majority(counter: Dict[str, int], min_share: float = 0.5) -> Optional[str]:
    tot = sum(counter.values())
    for k, n in counter.items():
        if tot and n / tot >= min_share and k not in ("other", "unknown", None):
            return k
    return None


def slug_for(p: dict) -> str:
    return "t%s_%s_to_%s" % (p["tenant"], p["from"], p["to"])


def build_card(p: dict, tickets: Dict[str, dict], now: Optional[str] = None) -> dict:
    n = p["count"]
    ts = now or cards.now()
    fn = "%s (%s)" % (p["from"], p["from_name"]) if p["from_name"] else p["from"]
    tn = "%s (%s)" % (p["to"], p["to_name"]) if p["to_name"] else p["to"]
    where = "Tenant %s%s" % (p["tenant"], " " + str(p["tenant_name"]) if p["tenant_name"] else "")
    if p["allowed"] is False:
        cause = ("%s: status %s -> %s is NOT configured in Catalog.StatusStep, so the application refuses it; in %d resolved tickets "
                 "(%s .. %s) the work order status was nevertheless changed in Log.WorkOrderJsonLog without an "
                 "Operation.WorkOrderGpsInfo history row, i.e. most likely forced directly in the database by a developer."
                 % (where, fn, tn, n, p["first"], p["last"]))
        fixes = ["Confirm in status_step_check that %s -> %s is missing for the tenant (IsRequestedTarget = 0)." % (p["from"], p["to"]),
                 "Decide with the business whether the transition should be allowed; if yes add the Catalog.StatusStep row (config change), "
                 "if it is a deliberate restriction explain it to the requester instead of forcing the status.",
                 "Check wo_field_changes: a status change without a history row and with UpdateUserId unchanged confirms the manual fix.",
                 "If the app refuses for another reason, investigate why the application flow fails (see wo_status_transition_check)."]
        title = "%s: status %s -> %s not configured; developers force it manually" % (where, p["from"], p["to"])
    else:
        cause = ("%s: status %s -> %s is %s but in %d resolved tickets (%s .. %s) the change was made without an Operation.WorkOrderGpsInfo "
                 "history row (suspected direct DB update, a job or a trigger). Heuristic, not confirmed."
                 % (where, fn, tn, "configured" if p["allowed"] else "of unknown configuration", n, p["first"], p["last"]))
        fixes = ["Run wo_field_changes and wo_status_timeline: is UpdateUserId unchanged and the history row missing?",
                 "Check wo_status_updater_log for a job/rule that writes this status without history (false positive of the heuristic).",
                 "If it is a manual correction, find out which application step failed for the user (code / data problem)."]
        title = "%s: status %s -> %s changed without history row (suspected manual fix)" % (where, p["from"], p["to"])
    chained = p.get("chained", 0)
    conf_pen = 0.0
    if chained * 2 >= n:
        conf_pen = 0.2
        cause += (" CAUTION: in %d of %d tickets the change sits within %d s of another status change on the same work order "
                  "(looks like a job / trigger / automatic workflow rather than a manual fix)." % (chained, n, CHAIN_SECONDS))
    kws = summary_keywords(p["keys"], tickets, extra=[p["from_name"] or ""])
    t = {"const": p["tenant"]}
    return {
        "id": "draft_footprint_" + slug_for(p),
        "title": title[:200],
        "symptom_signature": {"problem_type": _majority(p["problem_types"]), "app": _majority(p["apps"]),
                              "keywords": kws, "entity_requirements": ["work_order_ids"]},
        "root_cause": cause,
        "verification_queries": [
            {"query_id": "wo_locate", "params_from_entities": {"Number": "entities.work_order_ids[0]"}},
            {"query_id": "wo_status_timeline", "params_from_entities": {"TenantId": t, "WorkOrderId": "entities.work_order_ids[0]"}},
            {"query_id": "wo_field_changes", "params_from_entities": {"TenantId": t, "WorkOrderId": "entities.work_order_ids[0]"}},
            {"query_id": "status_step_check", "params_from_entities": {"TenantId": t, "FromStatusId": {"const": int(p["from"])},
                                                                       "ToStatusId": {"const": int(p["to"])}}}],
        "fix_steps": fixes,
        "code_locations": [],
        "confidence": round(max(0.1, min(MAX_DRAFT_CONF, 0.1 + 0.1 * n) - conf_pen), 2),
        "status": "draft",
        "evidence_tickets": p["keys"][:20],
        "created_by": CREATED_BY,
        "created_at": ts,
        "history": [{"ts": ts, "event": "created", "run_id": None,
                     "note": "batch_footprint: %d tickets, %d with UpdateUserId unchanged; heuristic, NOT confirmed" % (n, p["high"])}],
    }


def write_cards(patterns: List[dict], tickets: Dict[str, dict], directory: Optional[Path] = None, max_cards: int = 25) -> Dict[str, List[str]]:
    res: Dict[str, List[str]] = {"written": [], "skipped_existing": []}
    for p in patterns:
        if p["count"] < MIN_TICKETS or len(res["written"]) >= max_cards:
            continue
        try:
            int(p["from"]), int(p["to"])
        except (TypeError, ValueError):
            continue
        card = build_card(p, tickets)
        path = cards.card_path(card["id"], directory)
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = {}
            if old.get("status") != "draft" or old.get("created_by") != CREATED_BY:
                res["skipped_existing"].append(card["id"])
                continue
            card["created_at"] = old.get("created_at") or card["created_at"]
            card["history"] = (old.get("history") or []) + [dict(card["history"][0], event="evidence_added")]
        cards.save_card(card, directory)
        res["written"].append(card["id"])
    return res


# ------------------------------------------------------------------ summary

def _pct(a: int, b: int) -> str:
    return "%.0f%%" % (100.0 * a / b) if b else "n/a"


def render_summary(m: Dict[str, Any], *, total_tickets: int, resolved: int, eligible: int, card_ids: List[str], generated: str = "") -> str:
    f, bl = m["funnel"], m["baseline"]
    L = ["# Learning from DB footprints: batch summary", "",
         "Generated by `tools/learning/mine_patterns.py` over `knowledge/learning/ticket_windows.jsonl`%s. Deterministic, no LLM. "
         "Everything below is a HEURISTIC read of Log.WorkOrderJsonLog vs Operation.WorkOrderGpsInfo, not confirmed diagnosis." % (
             " (%s)" % generated if generated else ""), "",
         "## Method", "",
         "* Ticket window = Jira `created` (UTC+03:00) - 5 min .. `resolved` + 15 min, DB datetimes UTC (same code as `hibot/agent/ticket_window.py`).",
         "* Per work order (`wo_locate`, EU only): `wo_status_timeline` + `wo_field_changes` (newest 200 log rows) + `status_step_check` for every status change in the window.",
         "* A status change with no application history row (+-5 min) is a *suspected manual DB fix* (`high` when `UpdateUserId` also did not change, else `medium`).",
         "* `transition_allowed` = the from->to step exists in Catalog.StatusStep of the tenant.", "",
         "## Eligibility funnel", "",
         "| step | tickets |", "|---|---:|",
         "| all tickets | %d |" % total_tickets, "| with resolved timestamp | %d |" % resolved,
         "| eligible (resolved + work-order-like number) | %d |" % eligible,
         "| processed | %d |" % f["processed"],
         "| not found in EU (skipped; likely APAC or not a WO number) | %d |" % f["not_found_eu"],
         "| errors / timeouts | %d |" % f["error"],
         "| found in EU | %d |" % f["found_in_eu"],
         "| with any tracked WO change in the window | %d (%s) |" % (f["with_window_change"], _pct(f["with_window_change"], f["found_in_eu"])),
         "| with a status change in the window | %d |" % f["with_status_change"],
         "| manual-fix suspected, high | %d |" % f["manual_high"],
         "| manual-fix suspected, medium | %d |" % f["manual_medium"],
         "| NO change on the WO in the window (explanation / config / elsewhere) | %d (%s) |" % (f["no_change_in_window"], _pct(f["no_change_in_window"], f["found_in_eu"])),
         "", "## Top 20 manual status-fix patterns", "",
         "| # | tenant | from -> to | allowed by StatusStep | tickets | UpdateUserId unchanged | within %d s of another status change (job-like) | dates | example tickets |" % CHAIN_SECONDS, "|---|---|---|---|---:|---:|---:|---|---|"]
    for i, p in enumerate(m["patterns"][:20], 1):
        al = {True: "yes", False: "NO", None: "?"}[p["allowed"]]
        L.append("| %d | %s %s | %s %s -> %s %s | %s | %d | %d | %d | %s..%s | %s |" % (
            i, p["tenant"], p["tenant_name"] or "", p["from"], p["from_name"] or "", p["to"], p["to_name"] or "", al, p["count"],
            p["high"], p.get("chained", 0), p["first"], p["last"], ", ".join(p["keys"][:3])))
    if not m["patterns"]:
        L.append("| | no pattern | | | | | | | |")
    L += ["", "## Field-level corrections without a user-attributed update (UpdateUserId not changed)", "",
          "| tenant | field | tickets | examples |", "|---|---|---:|---|"]
    fp = [p for p in m["field_patterns"] if p["count"] >= 2]
    foc = [p for p in fp if p["focus"]]
    for p in (foc + [x for x in fp if not x["focus"]])[:15]:
        L.append("| %s | %s%s | %d | %s |" % (p["tenant"], p["field"], " *" if p["focus"] else "", p["count"], ", ".join(p["keys"][:3])))
    L += ["", "(* = ServiceShopId / EmployeeId / WarrantyTypeId, the fields called out for data-correction patterns.)", "",
          "## Per problem_type (tickets found in EU)", "",
          "| problem_type | tickets | no WO change in window | share | manual high | manual medium |", "|---|---:|---:|---:|---:|---:|"]
    for pt, c in m["by_problem_type"].items():
        L.append("| %s | %d | %d | %s | %d | %d |" % (pt, c["tickets"], c.get("no_change", 0), _pct(c.get("no_change", 0), c["tickets"]),
                                                   c.get("manual_high", 0), c.get("manual_medium", 0)))
    L += ["", "## Card candidates", ""]
    L += ["* `knowledge/cards/%s.json` (draft, not confirmed)" % c for c in card_ids] or ["* none (no pattern with >= %d tickets)" % MIN_TICKETS]
    L += ["", "## Baseline / false-positive check", "",
          "Jobs or triggers that write a status without a history row would look like manual fixes. For every work order the same test was run on its status "
          "changes OUTSIDE the ticket window (within the newest 200 log rows):", "",
          "* in the ticket windows: %d status changes, %d without history row (%s)" % (bl["in_status"], bl["in_nohist"], _pct(bl["in_nohist"], bl["in_status"])),
          "* outside the windows (same work orders): %d status changes, %d without history row (%s)" % (bl["out_status"], bl["out_nohist"], _pct(bl["out_nohist"], bl["out_status"])),
          "", "A much higher share inside the windows than outside supports the manual-fix reading; a similar share means the missing history row is normal for that kind of change. "
          "Per-pattern outside rates are in the table below (top 10).", "",
          "| tenant | from -> to | outside status changes | outside without history |", "|---|---|---:|---:|"]
    for p in m["patterns"][:10]:
        L.append("| %s | %s -> %s | %d | %d (%s) |" % (p["tenant"], p["from"], p["to"], p["out_status"], p["out_nohist"], _pct(p["out_nohist"], p["out_status"])))
    L += ["", "## Limitations", "",
          "* Heuristic margins (-5/+15 min, +-5 min history match); a ticket resolved long after the real fix, or a fix done before the ticket, is missed or misattributed.",
          "* `Log.WorkOrderJsonLog` is filled by a trigger that skips multi-row updates; only the newest 200 rows per work order are read (`log_truncated` in the jsonl).",
          "* Only tracked columns are logged; fixes in other tables (master data, StatusStep itself, ERP) leave no trace here, so 'no change' does not mean 'nothing was done'.",
          "* Only EU is covered; numbers not found in EU (APAC, WorkCase, SAP documents, SKUs) are skipped. Ticket numbers are regex-extracted: a number may belong to another work order.",
          "* A work order can be touched by several tickets/jobs in the window; the developer fix cannot be separated from automatic changes except by the history-row test.",
          "* Status step checks are capped at 8 new (tenant, from, to) lookups per ticket; transition_allowed is `?` beyond that.",
          "* Drafts are never confirmed knowledge: confirm with a developer before using them as root cause.", "",
          "## Re-run", "",
          "```",
          "set PYTHONIOENCODING=utf-8",
          "python tools/learning/batch_footprint.py --limit 20     # dry pass; resumable, --since YYYY-MM-DD, --reset to start over",
          "python tools/learning/batch_footprint.py                # full EU run (checkpoint: knowledge/learning/checkpoint.json)",
          "python tools/learning/mine_patterns.py                  # summary.md + draft cards",
          "```", ""]
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jsonl", default=str(JSONL))
    ap.add_argument("--tickets", default=str(TICKETS))
    ap.add_argument("--summary", default=str(SUMMARY))
    ap.add_argument("--no-cards", action="store_true")
    a = ap.parse_args(argv)
    from tools.learning.batch_footprint import eligible_numbers   # noqa: E402
    tl = json.loads(Path(a.tickets).read_text(encoding="utf-8"))["tickets"]
    by_key = {t["key"]: t for t in tl}
    m = mine(load_records(Path(a.jsonl)))
    res = {"written": []} if a.no_cards else write_cards(m["patterns"], by_key)
    txt = render_summary(m, total_tickets=len(tl), resolved=sum(1 for t in tl if t.get("resolved")),
                         eligible=sum(1 for t in tl if eligible_numbers(t)), card_ids=res["written"])
    Path(a.summary).write_text(txt, encoding="utf-8")
    print("summary -> %s; cards written %d" % (a.summary, len(res["written"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
