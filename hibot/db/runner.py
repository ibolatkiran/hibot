"""Executes validated, read-only SQL against the Sirius databases.

Safety properties (see README.md):

* Connection strings come from the environment (HIBOT_DB_EU / HIBOT_DB_APAC)
  and are never logged or put into error messages.
* ``ApplicationIntent=ReadOnly`` is appended when absent.
* Every statement is validated/rewritten by ``safe_sql`` first - the runner
  has no code path that executes unvalidated text.
* The connection runs with READ UNCOMMITTED, LOCK_TIMEOUT, DEADLOCK_PRIORITY LOW,
  autocommit and a query timeout; at most ``row_limit`` rows are fetched
  client-side regardless of the SQL.
* Result rows are PII-masked before they are returned.
* Each execution (also rejected ones) is appended to an audit log
  (logs/query_audit.jsonl) without any result data.  If the audit log cannot
  be written, nothing is executed (fail closed).

pyodbc is imported lazily; tests inject a fake ``connect`` callable.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import masking, safe_sql
from .catalog import REGIONS, SchemaCatalog, get_catalog
from .templates import PreparedQuery, TemplateError, TemplateRegistry

DEFAULT_TIMEOUT_SECONDS = 30
ENV_VARS = {"EU": "HIBOT_DB_EU", "APAC": "HIBOT_DB_APAC"}
ENV_AUDIT_LOG = "HIBOT_AUDIT_LOG"
SESSION_SETUP = (
    "SET NOCOUNT ON",
    "SET QUOTED_IDENTIFIER ON",
    "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
    "SET LOCK_TIMEOUT 5000",
    "SET DEADLOCK_PRIORITY LOW",
)


class RunnerError(RuntimeError):
    """Configuration or execution problem (message never contains secrets)."""


class AuditError(RunnerError):
    pass


@dataclass
class QueryResult:
    ok: bool
    region: str
    query_id: str
    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    rewrites: List[str] = field(default_factory=list)
    masking: Dict[str, Any] = field(default_factory=dict)
    sql_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "region": self.region, "query_id": self.query_id,
            "columns": self.columns, "rows": self.rows, "row_count": self.row_count,
            "truncated": self.truncated, "elapsed_ms": self.elapsed_ms, "error": self.error,
            "warnings": self.warnings, "rewrites": self.rewrites, "masking": self.masking,
        }


# ------------------------------------------------------------ connection string

_KEY_RE = re.compile(r"(?:^|;)\s*([^=;]+?)\s*=", re.I)


def build_connection_string(raw: str) -> str:
    """Append ApplicationIntent=ReadOnly (and an application name) if absent."""
    keys = {k.strip().lower().replace(" ", "") for k in _KEY_RE.findall(raw)}
    out = raw.rstrip().rstrip(";")
    if "applicationintent" not in keys:
        out += ";ApplicationIntent=ReadOnly"
    if "applicationname" not in keys and "app" not in keys:
        out += ";APP=HiBot-ReadOnly"
    return out


def connection_string_for(region: str, env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    var = ENV_VARS[region]
    raw = env.get(var, "").strip()
    if not raw:
        raise RunnerError("environment variable %s is not set (connection string for region %s)" % (var, region))
    return build_connection_string(raw)


_SECRET_PAT = re.compile(r"(?i)\b(pwd|password|uid|user id|user|accountkey|sharedaccesskey|secret|token)\s*=\s*[^;]*")


def scrub(message: str, secrets: Sequence[str] = ()) -> str:
    """Remove credentials / connection-string fragments from an error text."""
    msg = str(message)
    for s in secrets:
        if s:
            msg = msg.replace(s, "[conn]")
    msg = _SECRET_PAT.sub(lambda m: m.group(1) + "=***", msg)
    msg = re.sub(r"(?i)(server|data source)\s*=\s*[^;]*", r"\1=***", msg)
    return msg[:500]


# ------------------------------------------------------------------ audit log

def default_audit_path() -> Path:
    env = os.environ.get(ENV_AUDIT_LOG)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "logs" / "query_audit.jsonl"


class AuditLog:
    """Append-only JSON-lines audit trail.  Never stores result data."""

    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else default_audit_path()
        self._lock = threading.Lock()

    def ensure_writable(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8"):
                pass
        except OSError as e:
            raise AuditError("audit log %s is not writable (%s) - refusing to run queries"
                             % (self.path, e.__class__.__name__))

    def write(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str, sort_keys=False)
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                raise AuditError("cannot write audit log (%s)" % e.__class__.__name__)


def _audit_params(params: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (params or {}).items():
        if isinstance(v, str):
            out[k] = masking.truncate(masking.mask_text(v), 200)
        elif isinstance(v, (_dt.datetime, _dt.date)):
            out[k] = v.isoformat()
        elif v is None or isinstance(v, (int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)[:100]
    return out


# --------------------------------------------------------------------- runner

def _default_connect(conn_str: str, timeout: int):
    try:
        import pyodbc  # lazy: only needed when really talking to a database
    except ImportError:
        raise RunnerError("pyodbc is not installed (pip install -r requirements.txt)")
    # autocommit: never leave a transaction (and its locks) open
    return pyodbc.connect(conn_str, autocommit=True, timeout=15)


class QueryRunner:
    def __init__(self, region: str, *, registry: Optional[TemplateRegistry] = None,
                 catalog: Optional[SchemaCatalog] = None, audit: Optional[AuditLog] = None,
                 timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 connect: Optional[Callable[[str, int], Any]] = None,
                 env: Optional[Mapping[str, str]] = None):
        region = region.upper()
        if region not in REGIONS:
            raise RunnerError("unknown region %r (use EU or APAC)" % region)
        self.region = region
        self.catalog = catalog
        self.registry = registry
        self.audit = audit or AuditLog()
        self.timeout = int(timeout_seconds)
        self._connect = connect or _default_connect
        self._env = env
        self._conn = None
        self._conn_str: Optional[str] = None

    # -- public API ---------------------------------------------------------
    def run_template(self, query_id: str, params: Optional[Mapping[str, Any]] = None) -> QueryResult:
        reg = self._registry()
        try:
            prepared = reg.prepare(query_id, params, self.region)
        except TemplateError as e:
            return self._reject(query_id, params, str(e), sql_hash="")
        return self._execute(prepared.sql, prepared.args, query_id=query_id, params=prepared.params,
                             row_limit=prepared.row_limit, pii=prepared.pii, warnings=prepared.warnings,
                             rewrites=[])

    def run_sql(self, sql: str, *, row_limit: int = safe_sql.DEFAULT_ROW_LIMIT) -> QueryResult:
        """Ad-hoc SQL: strict validation, PII columns refused, TOP enforced."""
        res = safe_sql.validate(sql, region=self.region, catalog=self._catalog(), row_limit=row_limit)
        if not res.ok:
            return self._reject("adhoc", None, "; ".join(res.errors),
                                sql_hash=_hash(sql), warnings=res.warnings)
        return self._execute(res.positional_sql, [], query_id="adhoc", params=None,
                             row_limit=res.row_limit, pii={}, warnings=res.warnings, rewrites=res.rewrites)

    def run_request(self, request: Mapping[str, Any]) -> QueryResult:
        """Dispatch an LLM JSON request: {"query_id": ..., "params": {...}} or {"sql": ...}."""
        if not isinstance(request, Mapping):
            return self._reject("invalid", None, "request must be a JSON object", sql_hash="")
        if request.get("query_id"):
            return self.run_template(str(request["query_id"]), request.get("params") or {})
        if request.get("sql"):
            return self.run_sql(str(request["sql"]), row_limit=request.get("row_limit") or safe_sql.DEFAULT_ROW_LIMIT)
        return self._reject("invalid", None, "request needs 'query_id' (preferred) or 'sql'", sql_hash="")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # -- internals ----------------------------------------------------------
    def _catalog(self) -> SchemaCatalog:
        if self.catalog is None:
            self.catalog = get_catalog()
        return self.catalog

    def _registry(self) -> TemplateRegistry:
        if self.registry is None:
            self.registry = TemplateRegistry.load(catalog=self._catalog())
        return self.registry

    def _get_conn(self):
        if self._conn is None:
            self._conn_str = connection_string_for(self.region, self._env)
            conn = self._connect(self._conn_str, self.timeout)
            try:
                conn.timeout = self.timeout          # pyodbc: per-statement query timeout
            except Exception:
                pass
            cur = conn.cursor()
            for stmt in SESSION_SETUP:
                cur.execute(stmt)
            try:
                cur.close()
            except Exception:
                pass
            self._conn = conn
        return self._conn

    def _reject(self, query_id, params, message, *, sql_hash, warnings=None) -> QueryResult:
        self.audit.write(self._entry(query_id, params, sql_hash, 0, 0, "rejected: " + message[:300], False))
        return QueryResult(ok=False, region=self.region, query_id=query_id, error=message,
                           warnings=list(warnings or []), sql_hash=sql_hash)

    def _entry(self, query_id, params, sql_hash, row_count, elapsed_ms, error, truncated) -> dict:
        return {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
            "region": self.region, "query_id": query_id or "adhoc",
            "params": _audit_params(params), "sql_hash": sql_hash,
            "row_count": row_count, "elapsed_ms": elapsed_ms, "truncated": bool(truncated),
            "error": error,
        }

    def _execute(self, sql: str, args: Sequence[Any], *, query_id: str, params, row_limit: int,
                 pii: Mapping[str, str], warnings: List[str], rewrites: List[str]) -> QueryResult:
        sql_hash = _hash(sql)
        self.audit.ensure_writable()        # fail closed before touching the database
        start = time.monotonic()
        error: Optional[str] = None
        columns: List[str] = []
        rows: List[List[Any]] = []
        truncated = False
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(sql, list(args)) if args else cur.execute(sql)
                if cur.description is None:
                    raise RunnerError("statement returned no result set")
                columns = [d[0] for d in cur.description]
                fetched = cur.fetchmany(row_limit + 1)
                if len(fetched) > row_limit:
                    truncated = True
                    fetched = fetched[:row_limit]
                rows = [list(r) for r in fetched]
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001 - everything is reported, scrubbed
            # DB error texts can quote data values ("converting 'John' to int"): scrub + mask
            error = "%s: %s" % (e.__class__.__name__,
                                masking.mask_text(scrub(str(e), [self._conn_str or ""]))[:300])
            self.close()                    # drop a possibly broken connection
        elapsed_ms = int((time.monotonic() - start) * 1000)
        masked, report = masking.mask_rows(columns, rows, pii=pii)
        self.audit.write(self._entry(query_id, params, sql_hash, len(masked), elapsed_ms, error, truncated))
        return QueryResult(ok=error is None, region=self.region, query_id=query_id, columns=columns,
                           rows=masked, row_count=len(masked), truncated=truncated, elapsed_ms=elapsed_ms,
                           error=error, warnings=list(warnings), rewrites=list(rewrites),
                           masking=report.to_dict(), sql_hash=sql_hash)


def _hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8", "replace")).hexdigest()[:16]
