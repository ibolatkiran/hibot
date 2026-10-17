"""Run store + bounded worker pool. Pure stdlib (no FastAPI import), so it is easy to test."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hibot.agent import config, store

log = logging.getLogger("hibot.service")

STATE_FILE = "service_state.json"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def execute_analysis(key: Optional[str], text: Optional[str], region: Optional[str], loop: str) -> dict:
    """Same code path as `python -m hibot analyze <key> --live --llm http` (reuses the pipeline; nothing is spawned).
    Returns the pipeline run dict (run_id, run_dir, report, markdown, region...)."""
    from hibot.agent import intake, pipeline
    from hibot.agent.executor import LIVE, live_available
    from hibot.llm.client import client_from_env

    budget = config.Budget.for_loop(loop)
    inc = intake.build_incident(key, text)
    region = (region or inc.get("region_guess") or "EU").upper()
    if not live_available(region):
        raise RuntimeError("HIBOT_DB_%s is not configured on the HiBot service (live analysis impossible)" % region)
    if loop == "v2":
        from hibot.agent.loop_v2 import make_client
        llm = make_client()
    else:
        llm = client_from_env("http")
    return pipeline.analyze(key, text, llm=llm, mode=LIVE, region=region, budget=budget, demo=False, incident=inc, loop=loop)


class RunStore:
    """In-memory index + runs/<run_id>/service_state.json per run (atomic writes, one lock)."""

    def __init__(self, runs_dir: Optional[Path] = None):
        self.base = Path(runs_dir) if runs_dir else config.runs_dir()
        self.lock = threading.RLock()
        self._runs: Dict[str, dict] = {}

    def _path(self, run_id: str) -> Path:
        return store.run_path(run_id, self.base) / STATE_FILE

    def _persist(self, st: dict) -> None:
        p = self._path(st["run_id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)

    def recover(self) -> int:
        """Load persisted states; queued/running runs of a previous process become failed. Returns the number loaded."""
        n = 0
        if not self.base.exists():
            return 0
        for f in self.base.glob("*/" + STATE_FILE):
            try:
                st = json.loads(f.read_text(encoding="utf-8"))
                store.run_path(st["run_id"], self.base)
            except (OSError, ValueError, KeyError):
                continue
            if st.get("status") in ("queued", "running"):
                st.update(status="failed", error="service restarted", finished_at=_now())
                try:
                    self._persist(st)
                except OSError:
                    pass
            with self.lock:
                self._runs[st["run_id"]] = st
            n += 1
        return n

    def create(self, key: Optional[str], text: Optional[str], region: Optional[str], requested_by: str, loop: str) -> dict:
        run_id = store.new_run_id(key)
        st = {"run_id": run_id, "status": "queued", "ticket_key": key, "region": region, "requested_by": requested_by,
              "loop": loop, "created_at": _now(), "started_at": None, "finished_at": None, "error": None,
              "pipeline_run_id": None, "feedback": None}
        with self.lock:
            self._runs[run_id] = st
            self._persist(st)
        return dict(st)

    def update(self, run_id: str, **fields: Any) -> dict:
        with self.lock:
            st = self._runs[run_id]
            st.update(fields)
            self._persist(st)
            return dict(st)

    def get(self, run_id: str) -> Optional[dict]:
        with self.lock:
            st = self._runs.get(run_id)
            return dict(st) if st else None

    def load_report(self, st: dict) -> tuple:
        """(report dict, markdown) of a finished run, read from the pipeline run folder."""
        pid = st.get("pipeline_run_id")
        if not pid:
            return None, None
        d = store.run_path(pid, self.base)
        try:
            rep = json.loads((d / "report.json").read_text(encoding="utf-8")).get("report")
        except (OSError, ValueError):
            rep = None
        try:
            md = (d / "report.md").read_text(encoding="utf-8")
        except OSError:
            md = None
        return rep, md


class WorkerPool:
    """`workers` threads pull queued runs. Each analysis runs in its own thread so a timeout can mark the run failed;
    Python cannot kill a thread, so a timed-out analysis keeps its slot (semaphore) until it really ends."""

    def __init__(self, runs: RunStore, workers: int = 2, timeout: float = 240.0, loop: str = "v1"):
        self.runs, self.timeout, self.loop = runs, timeout, loop
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._slots = threading.Semaphore(max(1, workers))
        self._threads = [threading.Thread(target=self._worker, name="hibot-worker-%d" % i, daemon=True)
                         for i in range(max(1, workers))]
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            for t in self._threads:
                t.start()

    def stop(self) -> None:
        for _ in self._threads:
            self._q.put(None)

    def submit(self, run_id: str, key: Optional[str], text: Optional[str], region: Optional[str]) -> None:
        self._q.put((run_id, key, text, region))

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            run_id, key, text, region = item
            self._slots.acquire()                         # released by the analysis thread when it really ends
            try:
                self._run_one(run_id, key, text, region)
            except Exception:                             # noqa: BLE001 - a worker must never die
                log.exception("run %s: worker error", run_id)

    def _run_one(self, run_id: str, key, text, region) -> None:
        t0 = time.monotonic()
        self.runs.update(run_id, status="running", started_at=_now())
        log.info("run %s key=%s status=running", run_id, key)
        box: Dict[str, Any] = {}

        def target() -> None:
            try:
                box["run"] = execute_analysis(key, text, region, self.loop)
            except BaseException as e:                    # noqa: BLE001
                box["err"] = e
            finally:
                self._slots.release()

        th = threading.Thread(target=target, name="hibot-run-" + run_id, daemon=True)
        th.start()
        th.join(self.timeout)
        elapsed = time.monotonic() - t0
        if th.is_alive():
            self.runs.update(run_id, status="failed", finished_at=_now(),
                             error="analysis timed out after %d s" % int(self.timeout))
            log.warning("run %s key=%s status=failed (timeout) elapsed=%.1fs", run_id, key, elapsed)
            return
        if "err" in box:
            e = box["err"]
            self.runs.update(run_id, status="failed", finished_at=_now(),
                             error="%s: %s" % (e.__class__.__name__, str(e)[:300]))
            log.warning("run %s key=%s status=failed (%s) elapsed=%.1fs", run_id, key, e.__class__.__name__, elapsed)
            return
        run = box["run"]
        self.runs.update(run_id, status="done", finished_at=_now(), pipeline_run_id=run["run_id"],
                         region=run.get("region") or region)
        log.info("run %s key=%s status=done elapsed=%.1fs", run_id, key, elapsed)
