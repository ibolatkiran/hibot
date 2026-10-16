"""Batch DB footprint of historical tickets (DECISIONS D13): what changed on the ticket's work order between creation and resolution.

Deterministic, no LLM.  Reuses hibot.agent.ticket_window (window + manual-fix logic), hibot.agent.preflight (wo_locate) and
hibot.agent.executor (templates through hibot.db.runner, audit log on).  Sequential, polite to PROD, resumable.

    python tools/learning/batch_footprint.py --limit 20
    python tools/learning/batch_footprint.py --since 2026-01-01
Output: knowledge/learning/ticket_windows.jsonl (git-ignored) + knowledge/learning/checkpoint.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hibot.agent import preflight, ticket_window   # noqa: E402

TICKETS = ROOT / "knowledge" / "incidents" / "tickets_enriched.json"
OUT_DIR = ROOT / "knowledge" / "learning"
OUT = OUT_DIR / "ticket_windows.jsonl"
CHECKPOINT = OUT_DIR / "checkpoint.json"
REGION = "EU"
MAX_STEP_CHECKS = 8          # distinct uncached status_step_check queries per ticket
MAX_WOS_PER_NUMBER = 2
LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
EPOCH_CREATED = "2000-01-01 00:00"


# ------------------------------------------------------------------ pure logic

def eligible_numbers(t: dict) -> List[str]:
    """Work-order-like numbers (cap 3) of a ticket that has a resolved timestamp; [] when not eligible."""
    if not t.get("resolved") or ticket_window.parse_resolved(t.get("resolved")) is None:
        return []
    return preflight.candidate_numbers(t)


def select_tickets(tickets: List[dict], *, since: Optional[str] = None, done: Optional[Set[str]] = None,
                   limit: Optional[int] = None) -> List[dict]:
    """Eligible, not yet done, created >= since (YYYY-MM-DD), newest first, at most `limit`."""
    done = done or set()
    out = []
    for t in tickets:
        if t.get("key") in done or not eligible_numbers(t):
            continue
        if since and str(t.get("created") or "")[:10] < since:
            continue
        out.append(t)
    out.sort(key=lambda t: (str(t.get("created") or ""), str(t.get("key"))), reverse=True)
    return out[:limit] if limit else out


def load_checkpoint(path: Path = CHECKPOINT, out_path: Path = OUT) -> Set[str]:
    """Keys already processed: checkpoint file plus every key present in the jsonl (a crash may lose the checkpoint tail)."""
    done: Set[str] = set()
    try:
        done |= set(json.loads(path.read_text(encoding="utf-8")).get("done") or [])
    except (OSError, ValueError):
        pass
    try:
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["key"])
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return done


def save_checkpoint(done: Set[str], path: Path = CHECKPOINT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": sorted(done), "updated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}),
                   encoding="utf-8")
    os.replace(tmp, path)


def _change_view(c: dict) -> dict:
    return {"log_id": c["log_id"], "at_utc": c["at_utc"], "status_change": c["status_change"],
            "from": c["old_status_id"], "from_name": c["old_status_name"], "to": c["new_status_id"], "to_name": c["new_status_name"],
            "fields": c["changed_fields"], "update_user_id": c["update_user_id"],
            "update_user_unchanged": c["update_user_unchanged"], "history_row_found": c["history_row_found"],
            "transition_allowed": None, "steps_into_target": None}


def window_block(inc: dict, queries: List[dict]) -> Optional[dict]:
    """Ticket-window block for one WO from its executed wo_field_changes / wo_status_timeline records (same logic as the agent)."""
    tw = ticket_window.compute(inc, queries)
    if tw is None:
        return None
    # baseline: the same logic over ALL time; whatever is not in the ticket window is "outside"
    allw = ticket_window.compute(dict(inc, created=EPOCH_CREATED, resolved=None), queries)
    in_ids = {c["log_id"] for c in tw["changes"]}
    outside = [c for c in (allw["changes"] if allw else []) if c["log_id"] not in in_ids and c["status_change"]]
    fc = ticket_window._latest(queries, "wo_field_changes")
    n_rows = len(ticket_window._rows(fc)) if fc else 0
    changes = [_change_view(c) for c in tw["changes"]]
    sc = [c for c in changes if c["status_change"]]
    other_fields = sorted({f for c in changes for f in c["fields"] if f != "UpdateUserId"})
    return {
        "window_start_utc": tw["window"]["start_utc"], "window_end_utc": tw["window"]["end_utc"],
        "changes": changes, "status_changes": sc, "changed_fields": other_fields,
        "no_change_in_window": not changes,
        "history_rows_in_window": len(tw["history_rows_in_window"]), "history_checked": tw["history_checked"],
        "manual_level": tw["manual_fix"]["level"], "manual_suspected": tw["manual_fix"]["suspected"],
        "log_rows_returned": n_rows, "log_truncated": n_rows >= 200,
        "outside": {"status_changes": len(outside),
                    "no_history": sum(1 for c in outside if c["history_row_found"] is False)},
    }


def attach_transitions(block: dict, lookup) -> None:
    """lookup(from, to) -> {"allowed": bool|None, "steps_into_target": int|None} or None; sets it on every status change."""
    for c in block["status_changes"]:
        r = lookup(c["from"], c["to"]) or {}
        c["transition_allowed"] = r.get("allowed")
        c["steps_into_target"] = r.get("steps_into_target")


def parse_step_check(rec: Optional[dict]) -> dict:
    """status_step_check record -> {"allowed", "steps_into_target"} (allowed None when the query failed / unknown status)."""
    if not rec or not rec.get("executed"):
        return {"allowed": None, "steps_into_target": None}
    rows = ticket_window._rows(rec)
    if not rows:
        return {"allowed": None, "steps_into_target": None}
    return {"allowed": any(r.get("IsRequestedTarget") == 1 for r in rows), "steps_into_target": rows[0].get("StepsIntoTarget")}


def ticket_header(t: dict) -> dict:
    return {"key": t.get("key"), "created": t.get("created"), "resolved": t.get("resolved"), "resolution": t.get("resolution"),
            "problem_type": t.get("problem_type"), "app_guess": t.get("app_guess"), "country": t.get("country"),
            "group_id": t.get("group_id"), "numbers": preflight.candidate_numbers(t)}


def finalize(rec: dict) -> dict:
    """Ticket-level roll-up from the per-WO blocks."""
    wos = [w for w in rec["wos"] if w.get("window")]
    rec["manual_level"] = max((w["window"]["manual_level"] for w in wos), key=lambda l: LEVEL_ORDER[l], default="none")
    rec["any_change_in_window"] = any(not w["window"]["no_change_in_window"] for w in wos)
    rec["any_status_change"] = any(w["window"]["status_changes"] for w in wos)
    return rec


# ------------------------------------------------------------------ DB side

class Footprinter:
    def __init__(self, ex, delay: float = 0.3):
        self.ex = ex
        self.delay = delay
        self.step_cache: Dict[Tuple[Any, str, str], dict] = {}
        self.calls = 0

    def _q(self, qid: str, params: dict, why: str) -> dict:
        self.ex.query_count = 0                      # the batch is bounded by design, not by the agent budget
        self.ex.queries = []
        obs = self.ex._run_query({"query_id": qid, "params": params, "region": REGION, "why": why})
        self.calls += 1
        rec = obs.get("record")
        if rec is None:
            return {"query_id": qid, "executed": False, "ok": False, "error": obs.get("text", "")[:200], "columns": [], "rows": []}
        return rec

    def process(self, t: dict) -> dict:
        rec = ticket_header(t)
        rec["status"] = "ok"
        rec["wos"] = []
        rec["errors"] = []
        located = 0
        for num in rec["numbers"]:
            self.ex.query_count = 0
            self.ex.queries = []
            loc = preflight._locate(self.ex, num, REGION)
            self.calls += 1
            if not loc["ok"]:
                rec["errors"].append("wo_locate %s: %s" % (num, str(loc["error"])[:120]))
                continue
            for row in loc["rows"][:MAX_WOS_PER_NUMBER]:
                located += 1
                rec["wos"].append(self._wo(t, num, row))
            time.sleep(self.delay / 3)
        if not located:
            rec["status"] = "error" if rec["errors"] else "not_found_eu"
        elif not any(w.get("window") for w in rec["wos"]):
            rec["status"] = "error"
            rec["errors"] += [w["error"] for w in rec["wos"] if w.get("error")]
        return finalize(rec)

    def _wo(self, t: dict, number: str, row: dict) -> dict:
        tenant, wid = row.get("TenantId"), row.get("WorkOrderId")
        w: Dict[str, Any] = {"number": number, "work_order_id": wid, "tenant_id": tenant, "tenant_name": row.get("TenantName"),
                             "matched_on": row.get("MatchedOn"),
                             "current_status_id": str(row.get("StatusId")) if row.get("StatusId") is not None else None}
        p = {"TenantId": tenant, "WorkOrderId": wid}
        qs = [self._q("wo_status_timeline", p, "learning: status history"), self._q("wo_field_changes", p, "learning: field change log")]
        if not qs[1].get("executed"):
            w["error"] = "wo_field_changes: %s" % str(qs[1].get("error"))[:120]
            return w
        block = window_block(t, qs)
        if block is None:
            w["error"] = "window not computable"
            return w
        fresh = 0

        def lookup(frm, to):
            nonlocal fresh
            k = (tenant, str(frm), str(to))
            if k not in self.step_cache:
                if fresh >= MAX_STEP_CHECKS or not (str(frm).isdigit() and str(to).isdigit()):
                    return None
                fresh += 1
                self.step_cache[k] = parse_step_check(self._q(
                    "status_step_check", {"TenantId": tenant, "FromStatusId": int(frm), "ToStatusId": int(to)},
                    "learning: is the observed transition configured?"))
            return self.step_cache[k]
        attach_transitions(block, lookup)
        w["window"] = block
        return w


def make_executor():
    from hibot.agent import config
    from hibot.agent.code_context import CodeContext
    from hibot.agent.executor import LIVE, Executor, live_available
    from hibot.db.templates import TemplateRegistry
    from hibot.envfile import load_env
    load_env()
    if not live_available(REGION):
        raise SystemExit("EU database is not configured (HIBOT_DB_EU); nothing to do")
    return Executor(registry=TemplateRegistry.load(), code=CodeContext(), mode=LIVE, region=REGION,
                    budget=config.Budget(max_queries=10 ** 6))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=None, help="process at most N (not yet done) tickets")
    ap.add_argument("--since", default=None, help="only tickets created on/after YYYY-MM-DD")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between tickets")
    ap.add_argument("--tickets", default=str(TICKETS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--checkpoint", default=str(CHECKPOINT))
    ap.add_argument("--reset", action="store_true", help="ignore the checkpoint and the existing output (starts over, truncates)")
    a = ap.parse_args(argv)
    out, cp = Path(a.out), Path(a.checkpoint)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.reset:
        out.write_text("", encoding="utf-8")
        cp.unlink(missing_ok=True)
    tickets = json.loads(Path(a.tickets).read_text(encoding="utf-8"))["tickets"]
    done = load_checkpoint(cp, out)
    todo = select_tickets(tickets, since=a.since, done=done, limit=a.limit)
    n_elig = sum(1 for t in tickets if eligible_numbers(t))
    print("tickets %d, eligible %d, already done %d, to do now %d" % (len(tickets), n_elig, len(done), len(todo)), flush=True)
    if not todo:
        return 0
    fp = Footprinter(make_executor(), a.delay)
    t0 = time.time()
    counts: Dict[str, int] = {}
    try:
        with out.open("a", encoding="utf-8") as f:
            for i, t in enumerate(todo, 1):
                try:
                    rec = fp.process(t)
                except Exception as e:                                   # noqa: BLE001 - one ticket must not stop the batch
                    rec = dict(ticket_header(t), status="error", wos=[], errors=["%s: %s" % (e.__class__.__name__, str(e)[:120])])
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()
                done.add(t["key"])
                counts[rec["status"]] = counts.get(rec["status"], 0) + 1
                if i % 10 == 0:
                    save_checkpoint(done, cp)
                if i % 25 == 0 or i == len(todo):
                    print("%d/%d  %s  queries=%d  %.0fs" % (i, len(todo), counts, fp.calls, time.time() - t0), flush=True)
                time.sleep(a.delay)
    except KeyboardInterrupt:
        print("interrupted; progress is saved", flush=True)
    finally:
        save_checkpoint(done, cp)
        fp.ex.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
