"""CLI:  python -m hibot analyze SM-1777386 [--text "..."] [--region EU|APAC] [--live] [--max-steps N] [--llm fake|http]
        python -m hibot feedback <run_id> --verdict correct|wrong|partial [--note "..."]"""
from __future__ import annotations

import argparse
import sys

from hibot.agent import config
from hibot.agent.executor import DRY, LIVE, live_available


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass


def cmd_analyze(a) -> int:
    from hibot.agent import intake, pipeline
    from hibot.agent.demo import build_demo_llm
    from hibot.llm.client import LLMError, client_from_env

    if not a.key and not a.text:
        print("error: give a Jira key and/or --text", file=sys.stderr)
        return 2
    budget = config.Budget(max_steps=a.max_steps, max_queries=a.max_queries)
    try:
        inc = intake.build_incident(a.key, a.text)
    except intake.IntakeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    region = (a.region or inc.get("region_guess") or "EU").upper()
    mode = LIVE if a.live else DRY
    if mode == LIVE and not live_available(region):
        print("error: --live needs the HIBOT_DB_%s environment variable (connection string); not set. "
              "Run without --live for dry-run." % region, file=sys.stderr)
        return 2
    demo = a.llm == "fake"
    try:
        llm = (lambda i, m, r: build_demo_llm(i, m, r)) if demo else client_from_env("http")
        run = pipeline.analyze(a.key, a.text, llm=llm, mode=mode, region=region, budget=budget, demo=demo, incident=inc)
    except LLMError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    rep = run["report"]
    print("Rapor: %s" % (run["run_dir"] + "\\report.md"))
    print("Run: %s | mod: %s | bölge: %s | llm: %s" % (run["run_id"], mode, region, run["llm"]))
    ran = sum(1 for q in run["queries"] if q["executed"])
    print("Sorgu: %d hazırlandı, %d gerçekten çalıştırıldı" % (len(run["queries"]), ran))
    print("Sınıflandırma: %s | güven: %.2f" % (rep["classification"], rep["confidence"]))
    print("Özet: %s" % rep["summary"])
    print("Geri bildirim: python -m hibot feedback %s --verdict correct|wrong|partial" % run["run_id"])
    return 0


def cmd_feedback(a) -> int:
    from hibot.agent.feedback import record_feedback
    try:
        res = record_feedback(a.run_id, a.verdict, a.note or "")
    except (FileNotFoundError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    print("Geri bildirim kaydedildi: %s" % res["feedback_file"])
    for line in res["actions"] or ["(kart değişikliği yok)"]:
        print(" - " + line)
    return 0


def main(argv=None) -> int:
    _utf8()
    p = argparse.ArgumentParser(prog="hibot", description="HiBot incident root-cause agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="analyse a Jira ticket or free text")
    an.add_argument("key", nargs="?", help="Jira key, e.g. SM-1777386")
    an.add_argument("--text", help="free ticket text (or extra text for a key)")
    an.add_argument("--region", choices=["EU", "APAC"], help="database region (default: guessed from the country)")
    an.add_argument("--live", action="store_true", help="really run queries (needs HIBOT_DB_<REGION>); default is dry-run")
    an.add_argument("--max-steps", type=int, default=config.Budget.max_steps)
    an.add_argument("--max-queries", type=int, default=config.Budget.max_queries)
    an.add_argument("--llm", choices=["fake", "http"], default="http", help="fake = scripted demo, http = HIBOT_LLM_* endpoint")
    an.set_defaults(fn=cmd_analyze)
    fb = sub.add_parser("feedback", help="one-click developer feedback on a run")
    fb.add_argument("run_id")
    fb.add_argument("--verdict", required=True, choices=["correct", "wrong", "partial"])
    fb.add_argument("--note")
    fb.set_defaults(fn=cmd_feedback)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
