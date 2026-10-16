"""Smoke-test a read-only DB connection.

    python -m hibot.db.probe --region EU

1. connects with HIBOT_DB_<REGION> (from HiBot/.env or the environment) — the string is never printed
2. checks the login really is read-only (fixed, hard-coded permission query; not LLM-generated)
3. runs the vetted `tenant_lookup` template through the normal safe path (validator + audit log)
"""
from __future__ import annotations

import argparse
import sys

from hibot.envfile import load_env

# Constant diagnostic query: role membership + effective database permissions of the current login.
PERMISSION_CHECK = """
SELECT
    SUSER_SNAME()                                         AS LoginName,
    DB_NAME()                                             AS DatabaseName,
    IS_ROLEMEMBER('db_datareader')                        AS IsDataReader,
    IS_ROLEMEMBER('db_datawriter')                        AS IsDataWriter,
    IS_ROLEMEMBER('db_owner')                             AS IsDbOwner,
    IS_ROLEMEMBER('db_ddladmin')                          AS IsDdlAdmin,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT')    AS CanInsert,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE')    AS CanUpdate,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE')    AS CanDelete,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE')   AS CanExecute,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER')     AS CanAlter,
    DATABASEPROPERTYEX(DB_NAME(), 'Updateability')        AS Updateability
"""
WRITE_FLAGS = ("IsDataWriter", "IsDbOwner", "IsDdlAdmin", "CanInsert", "CanUpdate", "CanDelete", "CanAlter")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="EU", choices=["EU", "APAC"])
    ap.add_argument("--country", default="TR", help="country code for the tenant_lookup smoke query")
    a = ap.parse_args()
    print("Loaded from .env:", ", ".join(load_env()) or "(nothing)")

    from hibot.db import runner
    try:
        import pyodbc  # noqa: F401
    except ImportError:
        print("pyodbc is not installed: python -m pip install -r requirements.txt")
        return 2
    try:
        conn_str = runner.build_connection_string(runner.connection_string_for(a.region))
    except runner.RunnerError as e:
        print("CONFIG ERROR:", e)
        return 2

    print("ODBC drivers:", ", ".join(pyodbc.drivers()) or "(none)")
    try:
        conn = pyodbc.connect(conn_str, timeout=15, readonly=True)
    except Exception as e:                                   # noqa: BLE001
        print("CONNECT FAILED:", runner.scrub(str(e), [conn_str]))
        return 1
    cur = conn.cursor()
    cur.execute(PERMISSION_CHECK)
    cols = [d[0] for d in cur.description]
    perms = dict(zip(cols, cur.fetchone()))
    conn.close()
    print("\n== Permissions")
    for k, v in perms.items():
        print("  %-14s %s" % (k, v))
    writable = [k for k in WRITE_FLAGS if perms.get(k) == 1] + (
        ["Updateability=READ_WRITE"] if perms.get("Updateability") == "READ_WRITE" and perms.get("CanUpdate") == 1 else [])
    if writable:
        print("\n⚠️  LOGIN IS NOT READ-ONLY: %s. Ask the DBA for a db_datareader-only login." % ", ".join(writable))
    else:
        print("\n✅ Login is read-only (no write/DDL permission).")

    print("\n== Smoke query: tenant_lookup(CountryCode=%s) via safe path" % a.country)
    qr = runner.QueryRunner(a.region)
    try:
        res = qr.run_template("tenant_lookup", {"CountryCode": a.country})
    finally:
        qr.close()
    d = res.to_dict()
    print("  ok=%s rows=%s elapsed_ms=%s error=%s" % (d.get("ok"), d.get("row_count"), d.get("elapsed_ms"), d.get("error")))
    for row in (d.get("rows") or [])[:5]:
        print("  ", row)
    return 0 if d.get("ok") and not writable else 1


if __name__ == "__main__":
    sys.exit(main())
