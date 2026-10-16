"""Run the HiBot pipeline over the evaluation set (sequential, polite, resumable).

    python tools/eval/run_eval.py --tag fake1 --llm fake --limit 3            # dry-run smoke test, no DB, no LLM
    python tools/eval/run_eval.py --tag live1 --llm http --live --split test --delay 3 --resume

Checkpoint + results: runs/eval_<tag>/results.jsonl (one JSON record per ticket, appended as soon as the ticket is done;
`runs/` is git-ignored).  Per-ticket run folders go to runs/eval_<tag>/runs/.  --resume skips keys that already have an
error-free record.  Score afterwards with tools/eval/score.py --tag <tag>.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVAL_SET = ROOT / "knowledge" / "eval" / "eval_set.json"


def select(items: list, split: str, keys: Optional[list], limit: Optional[int]) -> list:
    sel = [i for i in items if split == "all" or i["split"] == split]
    if keys:
        wanted = {k.strip() for k in keys}
        by_key = {i["key"]: i for i in items}
        sel = [by_key[k] for k in keys if k in by_key]
        unknown = wanted - set(by_key)
        if unknown:
            raise SystemExit("error: keys not in eval set: %s" % ", ".join(sorted(unknown)))
    return sel[:limit] if limit else sel


def done_keys(results: Path) -> set:
    out = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not r.get("error"):
                    out.add(r["key"])
    return out


def summarize_run(run: dict, elapsed: float, item: dict) -> dict:
    qs = [{"query_id": q.get("query_id"), "kind": q.get("kind"), "region": q.get("region"), "executed": bool(q.get("executed")),
           "ok": q.get("ok"), "row_count": q.get("row_count")} for q in run.get("queries") or []]
    matches = run.get("matches") or []
    top = matches[0] if matches else None
    card = None
    if top:
        c = top.get("card") or {}
        card = {"id": c.get("id") if isinstance(c, dict) else str(c), "score": top.get("score")}
    budget = run.get("budget") or {}
    return {
        "key": item["key"], "split": item["split"], "stratum": item["stratum"], "error": None,
        "run_id": run.get("run_id"), "mode": run.get("mode"), "region": run.get("region"), "llm": run.get("llm"),
        "elapsed_s": round(elapsed, 2), "llm_calls": run.get("llm_calls"), "forced_final": run.get("forced_final"),
        "queries": qs,
        "budget_used": {"steps": len(run.get("steps") or []), "max_steps": budget.get("max_steps"),
                        "queries": len(qs), "max_queries": budget.get("max_queries")},
        "matched_card": card, "preflight": run.get("preflight"), "baseline": run.get("baseline"),
        "warnings": run.get("warnings"), "report": run.get("report"),
    }


def run_one(item: dict, args, budget):
    from hibot.agent import intake, pipeline
    from hibot.agent.demo import build_demo_llm
    from hibot.agent.executor import DRY, LIVE
    from hibot.llm.client import client_from_env

    region = (args.region or item.get("region") or "EU").upper()
    inc = intake.build_incident(item["key"], None)
    demo = args.llm == "fake"
    llm = (lambda i, m, r: build_demo_llm(i, m, r)) if demo else client_from_env("http")
    return pipeline.analyze(item["key"], None, llm=llm, mode=LIVE if args.live else DRY, region=region, budget=budget,
                            demo=demo, incident=inc, runs_base=args.runs_base)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="run name; output in runs/eval_<tag>/")
    ap.add_argument("--split", choices=["test", "dev", "all"], default="test")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keys", help="comma separated Jira keys (overrides --split)")
    ap.add_argument("--llm", choices=["fake", "http"], default="fake")
    ap.add_argument("--live", action="store_true", help="really run queries (needs HIBOT_DB_<REGION>); default dry-run")
    ap.add_argument("--region", choices=["EU", "APAC"], help="override the region of every ticket (default: per ticket, EU)")
    ap.add_argument("--resume", action="store_true", help="skip tickets that already have an error-free record")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds to wait between tickets (not after the last)")
    ap.add_argument("--max-steps", type=int)
    ap.add_argument("--max-queries", type=int)
    ap.add_argument("--eval-set", default=str(EVAL_SET))
    ap.add_argument("--out-dir", help="default: runs/eval_<tag>")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass
    if a.llm == "http" or a.live:
        from hibot.envfile import load_env
        load_env()                                           # secrets from the git-ignored .env (names only are returned)
    from hibot.agent import config
    from hibot.agent.executor import live_available

    items = json.loads(Path(a.eval_set).read_text(encoding="utf-8"))["tickets"]
    sel = select(items, a.split, a.keys.split(",") if a.keys else None, a.limit)
    out = Path(a.out_dir) if a.out_dir else ROOT / "runs" / ("eval_" + a.tag)
    out.mkdir(parents=True, exist_ok=True)
    a.runs_base = out / "runs"
    results = out / "results.jsonl"
    if a.live:
        for reg in sorted({(a.region or i.get("region") or "EU").upper() for i in sel}):
            if not live_available(reg):
                print("error: --live needs HIBOT_DB_%s (not set)" % reg, file=sys.stderr)
                return 2
    kw = {k: v for k, v in (("max_steps", a.max_steps), ("max_queries", a.max_queries)) if v}
    skip = done_keys(results) if a.resume else set()
    todo = [i for i in sel if i["key"] not in skip]
    print("eval %s: %d tickets selected, %d to run (%d resumed) | llm=%s mode=%s" % (
        a.tag, len(sel), len(todo), len(sel) - len(todo), a.llm, "live" if a.live else "dry-run"))
    n_err = 0
    for n, item in enumerate(todo, 1):
        t0 = time.time()
        try:
            run = run_one(item, a, config.Budget(**kw))
            rec = summarize_run(run, time.time() - t0, item)
            rep = rec["report"] or {}
            print("[%d/%d] %s %-22s -> %s (%.2f) %ss q=%d" % (n, len(todo), item["key"], item["stratum"], rep.get("classification"),
                                                             float(rep.get("confidence") or 0), rec["elapsed_s"], len(rec["queries"])))
        except KeyboardInterrupt:
            print("interrupted; resume with --resume", file=sys.stderr)
            return 130
        except Exception as e:                               # noqa: BLE001  one bad ticket must not stop the batch
            n_err += 1
            rec = {"key": item["key"], "split": item["split"], "stratum": item["stratum"], "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                   "elapsed_s": round(time.time() - t0, 2), "queries": [], "report": None}
            print("[%d/%d] %s ERROR %s" % (n, len(todo), item["key"], rec["error"]), file=sys.stderr)
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if n < len(todo) and a.delay > 0 and (a.live or a.llm == "http"):
            time.sleep(a.delay)
    print("done: %d ok, %d errors -> %s" % (len(todo) - n_err, n_err, results))
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
