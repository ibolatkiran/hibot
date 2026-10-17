"""FastAPI app implementing the HiBot service contract (project/DELIVERY_DBCT.md)."""
from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hibot.agent import cards as cardmod
from hibot.agent import config
from hibot.agent import feedback as fbmod

from . import __version__
from .jobs import RunStore, WorkerPool

log = logging.getLogger("hibot.service")

KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,15}-\d{1,10}$")
MAX_TEXT = 8000
MAX_SHORT = 200


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        self.status, self.msg = status, msg


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _short(v: Any, name: str, required: bool = False) -> str:
    if v is None or v == "":
        if required:
            raise ApiError(400, "%s is required" % name)
        return ""
    if not isinstance(v, str) or len(v) > MAX_SHORT:
        raise ApiError(400, "%s must be a string of at most %d characters" % (name, MAX_SHORT))
    return v.strip()


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:                                              # noqa: BLE001
        raise ApiError(400, "body must be valid JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def _card_updated(c: dict) -> Optional[str]:
    h = c.get("history") or []
    return (h[-1].get("ts") if h else None) or c.get("created_at")


def create_app(service_key: Optional[str] = None, *, runs_dir: Optional[Path] = None, workers: int = 2,
               timeout: float = 240.0, loop: str = "v1") -> FastAPI:
    key = service_key if service_key is not None else os.environ.get("HIBOT_SERVICE_KEY", "")
    if not key:
        raise RuntimeError("HIBOT_SERVICE_KEY is not set; refusing to start")
    key_b = key.encode("utf-8")
    runs = RunStore(runs_dir)
    pool = WorkerPool(runs, workers=workers, timeout=timeout, loop=loop)
    recovered = runs.recover()
    pool.start()
    app = FastAPI(title="HiBot service", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runs, app.state.pool = runs, pool
    log.info("service ready: %d persisted runs, workers=%d timeout=%ss loop=%s", recovered, workers, timeout, loop)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        sent = request.headers.get("x-hibot-key", "")
        if not hmac.compare_digest(sent.encode("utf-8"), key_b):
            return _err(401, "missing or invalid X-HiBot-Key")
        try:
            return await call_next(request)
        except Exception:                                          # noqa: BLE001 - never leak a stack trace
            log.exception("unhandled error")
            return _err(500, "internal error")

    @app.exception_handler(ApiError)
    async def _api_error(_r, e: ApiError):
        return _err(e.status, e.msg)

    @app.exception_handler(RequestValidationError)
    async def _val(_r, e):
        return _err(400, "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r, e: StarletteHTTPException):
        return _err(e.status_code, {404: "not found", 405: "method not allowed"}.get(e.status_code, "error"))

    def view(st: dict) -> dict:
        out = {k: st.get(k) for k in ("run_id", "status", "ticket_key", "created_at", "finished_at")}
        if st.get("error"):
            out["error"] = st["error"]
        if st["status"] == "done":
            out["report"], out["report_markdown"] = runs.load_report(st)
        return out

    def get_run(run_id: str) -> dict:
        st = runs.get(run_id)
        if not st:
            raise ApiError(404, "run not found")
        return st

    @app.post("/analyses", status_code=202)
    async def create_analysis(request: Request):
        b = await _json_body(request)
        tk = b.get("ticket_key")
        text = b.get("text")
        if tk is not None and (not isinstance(tk, str) or not KEY_RE.match(tk.strip())):
            raise ApiError(400, "ticket_key must look like SM-1234567")
        tk = tk.strip().upper() if tk else None
        if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT):
            raise ApiError(400, "text must be a string of at most %d characters" % MAX_TEXT)
        text = text.strip() if text else None
        if not tk and not text:
            raise ApiError(400, "ticket_key and/or text is required")
        region = b.get("region")
        if region is not None:
            if not isinstance(region, str) or region.upper() not in config.REGIONS:
                raise ApiError(400, "region must be EU or APAC")
            region = region.upper()
        by = _short(b.get("requested_by"), "requested_by", required=True)
        st = runs.create(tk, text, region, by, loop)
        pool.submit(st["run_id"], tk, text, region)
        log.info("run %s key=%s status=queued", st["run_id"], tk)
        return JSONResponse({"run_id": st["run_id"], "status": "queued"}, status_code=202)

    @app.get("/analyses/{run_id}")
    async def analysis(run_id: str):
        return view(get_run(run_id))

    @app.post("/analyses/{run_id}/feedback")
    async def feedback(run_id: str, request: Request):
        st = get_run(run_id)
        b = await _json_body(request)
        verdict = b.get("verdict")
        if verdict not in fbmod.VERDICTS:
            raise ApiError(400, "verdict must be one of %s" % ", ".join(fbmod.VERDICTS))
        note = b.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ApiError(400, "note must be a string of at most 2000 characters")
        by = _short(b.get("by"), "by", required=True)
        if st["status"] != "done":
            raise ApiError(409, "run is not finished (status=%s)" % st["status"])
        with runs.lock:                                            # one feedback per run: no double card boosting
            if runs.get(run_id).get("feedback"):
                raise ApiError(409, "feedback already recorded for this run")
            try:
                res = fbmod.record_feedback(st["pipeline_run_id"], verdict, ((note or "") + " [by %s]" % by).strip())
            except FileNotFoundError:
                raise ApiError(404, "run data not found")
            except ValueError as e:
                raise ApiError(400, str(e))
            runs.update(run_id, feedback={"verdict": verdict, "note": note or "", "by": by})
        log.info("run %s feedback=%s by=%s", run_id, verdict, by)
        return {"ok": True, "card_effect": "; ".join(res.get("actions") or []) or "no card change"}

    @app.get("/cards")
    async def list_cards(status: Optional[str] = None):
        if status is not None and status not in cardmod.STATUSES:
            raise ApiError(400, "status must be one of %s" % ", ".join(cardmod.STATUSES))
        out = []
        for c in cardmod.load_cards(include_rejected=True):
            if status and c.get("status") != status:
                continue
            rct, ra = cardmod.card_axes(c)
            out.append({"id": c["id"], "status": c["status"], "title": c["title"], "root_cause_type": rct,
                        "resolution_action": ra, "confidence": c.get("confidence"),
                        "support": len(c.get("evidence_tickets") or []), "updated_at": _card_updated(c)})
        return out

    @app.get("/cards/{card_id}")
    async def card(card_id: str):
        try:
            cardmod.card_path(card_id)
        except ValueError:
            raise ApiError(404, "card not found")
        for c in cardmod.load_cards(include_rejected=True):
            if c["id"] == card_id:
                return c
        raise ApiError(404, "card not found")

    @app.get("/health")
    async def health():
        from hibot.agent.executor import live_available
        return {"ok": True, "llm": os.environ.get("HIBOT_LLM_FORMAT") or "unconfigured",
                "db_regions": [r for r in config.REGIONS if live_available(r)], "version": __version__}

    return app
