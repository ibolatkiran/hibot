"""Run folders: runs/<run_id>/{input.json, incident.json, matches.json, steps.jsonl, queries.json, report.json, report.md}."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Optional

from . import config

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,100}$")


def new_run_id(key: Optional[str]) -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9-]", "", key or "text")[:30] or "text"
    return "%s-%s-%s" % (ts, label, secrets.token_hex(2))


def run_path(run_id: str, base: Optional[Path] = None) -> Path:
    if not RUN_ID_RE.match(run_id or "") or ".." in run_id:
        raise ValueError("bad run id %r" % run_id)
    return (base or config.runs_dir()) / run_id


def _write(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def save_run(run: dict, markdown: str, base: Optional[Path] = None) -> Path:
    d = run_path(run["run_id"], base)
    d.mkdir(parents=True, exist_ok=True)

    def dump(name: str, obj: Any) -> None:
        _write(d / name, json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")

    dump("input.json", run["input"])
    dump("incident.json", run["incident"])
    dump("matches.json", [{"card_id": m["card"]["id"], "score": m["score"], "reasons": m["reasons"]} for m in run.get("matches") or []])
    _write(d / "steps.jsonl", "".join(json.dumps({k: v for k, v in s.items() if k != "action_text"}, ensure_ascii=False, default=str) + "\n" for s in run["steps"]))
    dump("queries.json", run["queries"])
    dump("report.json", {k: v for k, v in run.items() if k not in ("steps", "incident", "queries", "input")})
    _write(d / "report.md", markdown)
    return d


def load_run(run_id: str, base: Optional[Path] = None) -> dict:
    d = run_path(run_id, base)
    f = d / "report.json"
    if not f.exists():
        raise FileNotFoundError("run %s not found (%s)" % (run_id, d))
    run = json.loads(f.read_text(encoding="utf-8"))
    for name in ("incident", "queries"):
        p = d / (name + ".json")
        run[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return run
