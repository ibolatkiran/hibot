# hibot.db - read-only production diagnostics layer

The incident agent may only look at production data through this package.
The company LLM endpoint has no tool calling, so the orchestrator asks the LLM
for JSON such as

```json
{"query_id": "wo_status_timeline", "params": {"work_order_id": 123, "tenant_id": 5}}
```

or, rarely, `{"sql": "SELECT ..."}`, and hands it to `QueryRunner.run_request`.

```
LLM JSON ──► runner.run_request ──► templates.prepare  (template path: params coerced, '?' binding)
                       │                    └─► safe_sql.validate(allow_pii=True)   (vetted SQL)
                       └─► safe_sql.validate(allow_pii=False)                       (ad-hoc SQL)
                                   │
                         audit (fail closed) ─► pyodbc (READ UNCOMMITTED, timeout) ─► fetchmany(row_limit+1)
                                   │
                         masking.mask_rows ─► QueryResult (columns, rows, row_count, truncated, elapsed_ms)
```

| Module | Purpose |
|---|---|
| `catalog.py` | Loads `knowledge/schema/{objects,domains}.json` (structure only). Secret / PII rules. |
| `safe_sql.py` | Tokenizer + validator + rewriter. Returns `SafeSqlResult(ok, rewritten_sql, positional_sql, param_order, errors, warnings, rewrites, objects, ...)`. |
| `masking.py` | PII masking of result rows and free text, truncation of bodies. |
| `templates.py` | Template parser/validator/registry, parameter coercion, prompt catalog text. |
| `runner.py` | Connection handling, execution, masking, audit log. |

## Safety model

Defence in depth - each layer assumes the previous one failed.

1. **The database login must be read-only.** `ApplicationIntent=ReadOnly` only
   *routes* to a readable secondary on Azure SQL; it does **not** stop writes on
   a primary. Provision HIBOT_DB_* with a dedicated login that is `db_datareader`
   only (no EXECUTE, no write permission, no access to credential tables). Everything
   below is additional.
2. **Validation (`safe_sql.validate`)** - deny by default:
   * Real tokenizer: strings (`'it''s'`, `N'..'`), `[brackets]`, `"quoted"`, line and nested block comments,
     variables, numbers. Keywords are matched only on *unquoted words*, so `[UpdateDate]`, `UpdateDate`,
     `AS [delete]` and `'...; DROP TABLE...'` inside a string are fine, while `SEL/**/ECT` is not a keyword.
   * Exactly one statement: a single trailing `;` is tolerated, anything else is rejected, a second
     top-level `SELECT` (stacked statement without `;`) is rejected. Must start with `SELECT` or `WITH`.
   * Forbidden keywords anywhere (INSERT UPDATE DELETE MERGE TRUNCATE DROP ALTER CREATE GRANT REVOKE DENY EXEC
     EXECUTE OPENROWSET OPENQUERY OPENDATASOURCE OPENXML BULK INTO WAITFOR SHUTDOWN DBCC BACKUP RESTORE KILL USE SET
     DECLARE CURSOR OPEN FETCH BEGIN IF WHILE PRINT RAISERROR THROW OUTPUT OFFSET NEXT (sequences) ...),
     `sp_*`/`xp_*` names, `#temp` tables, `@@system` variables, `$` / `::` / `{ODBC escapes}` / `?`,
     compound assignment operators, `SELECT @x = ...`.
   * Unicode: control, format and invisible characters are rejected everywhere; non-ASCII outside string
     literals is rejected (full-width `；`/letters, NBSP, zero-width space ...).
   * **Only catalog objects**: every FROM/JOIN/APPLY source and every `schema.fn(...)` call must exist in
     the catalog *for the target region*; only tables, views and functions are readable (procedures,
     synonyms, sequences, `sys.*`, `INFORMATION_SCHEMA`, 3/4-part cross-database names are rejected).
     Functions whose definition calls procedures or writes tables are rejected.
   * **Function allowlist** for built-ins (aggregates, string/date/math/JSON, CAST/CONVERT ...);
     everything else (`SUSER_SNAME`, `DB_NAME`, `OBJECT_DEFINITION`, `NEWID` ...) is rejected.
   * Table hints: only `NOLOCK` / `READUNCOMMITTED` (lock hints such as `UPDLOCK`/`HOLDLOCK` would block
     production writers). `OPTION` only `(RECOMPILE)` / `(MAXRECURSION n<=1000)`.
   * **Row limit**: `TOP n` is injected into every top-level SELECT (also each `UNION` branch) or an existing
     `TOP` is clamped (`TOP (CASE WHEN @p ...)` for a parameter). Default 200, max 1000. `TOP PERCENT`,
     `WITH TIES`, `OFFSET/FETCH` are rejected. The runner additionally never fetches more than `row_limit+1` rows.
   * **Credentials** (`password`, `pwd`, `secret`, `token`, `apikey`, `hash`, `salt`, ... as identifier *words*,
     so `ActivePassive` is fine) in any identifier position and tables like `FirstEmployeePassword` are
     rejected for ad-hoc **and** template SQL.
   * **PII** (`domains.json` `pii_columns`, plus a small supplemental list in `catalog.SUPPLEMENTAL_PII`):
     identity-type columns (names, phones, e-mails, addresses, ids, IBAN) cannot be referenced at all in ad-hoc
     SQL. Free-text/body columns (`ErrorMessage`, `Request`, `Response`, ...) are allowed but always masked and
     truncated on output. Vetted templates may select PII columns (`allow_pii=True`); they must declare them
     in the header and they are masked on output.
   * `SELECT *` / `alias.*` over a table with sensitive columns is **rewritten** to the explicit safe
     column list (`[alias].[Col]`, region-aware). `SELECT *` over a view (columns unknown) is rejected;
     over a function it is allowed with a warning (output is still masked); over a CTE/derived table it is
     left alone (the inner SELECT was already filtered).
   * Qualified column references (`alias.Col`) are checked against the catalog (EU-only columns are
     rejected for APAC) and against CTE / derived-table output names.
   * Warnings (never errors): table has `TenantId` but no `TenantId` predicate (PK lookups exempt),
     backup/copy tables, star over functions, unresolved qualifiers.
   * The executed text is **regenerated from the validated tokens** (comments dropped, `@Name` -> `?`),
     never the original string.
3. **Binding**: template values are always passed as ODBC parameters. Strings never reach the SQL text.
   Each value is type-coerced and range/length checked (`templates.coerce`); `like` parameters are
   wildcard-escaped; unknown parameters are rejected.
4. **Session**: `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED` (instead of per-table `WITH (NOLOCK)`;
   `SET` is forbidden in user SQL so the runner does it on the connection), `LOCK_TIMEOUT 5000`,
   `DEADLOCK_PRIORITY LOW`, `QUOTED_IDENTIFIER ON`, autocommit, query timeout (default 30 s),
   `ApplicationIntent=ReadOnly`. Dirty reads are acceptable for diagnostics.
5. **Masking** of every cell before it leaves the layer (see below). DB error messages are scrubbed
   (connection-string fragments) and masked too, because SQL Server quotes data values in errors.
6. **Audit** of every attempt (including rejected ones), no result data. If the audit file cannot be
   written, nothing is executed.

## Masking (`masking.py`)

* Declared PII columns (template header `pii:`) are masked by kind: `name` -> `J*** S***`, `email` ->
  `j***@e***.com`, `phone` -> `***67`, `contact` (e-mail or phone), `address` -> `[ADDRESS]`, `identity` /
  `iban_bank` -> `[REDACTED]`, `free_text_body` -> regex-masked + truncated.
* Column-name heuristics (`*Email*`, `*Phone*`, `FirstName`, `Address`, `TaxNumber`, `Password`, ...) for strings.
* Regex masking inside every string: e-mails, IBAN (mod-97 checked), payment cards (Luhn),
  `+` international / separator-grouped phone numbers (dates, GUIDs, IPs and plain ids are protected),
  JWT / Bearer tokens, and values of PII-looking keys in JSON (`"Phone":"..."`), XML (`<Email>..</Email>`)
  and `key=value` text (`password=`, `token=`, `iban=`, `firstname=` ...).
* Truncation (after masking): bodies 600 chars, error/message text 1500, anything else 2000
  (`...[truncated N chars]`).
* Values are made JSON-serialisable (datetime -> ISO, Decimal -> str, bytes -> `<binary N bytes>`).

## Environment variables

| Variable | Meaning |
|---|---|
| `HIBOT_DB_EU` | ODBC connection string for gfsm-europe (never logged, never in errors). |
| `HIBOT_DB_APAC` | ODBC connection string for ahsiriusdbsrv. |
| `HIBOT_AUDIT_LOG` | Override audit path (default `logs/query_audit.jsonl`). |
| `HIBOT_SCHEMA_DIR` | Override `knowledge/schema`. |
| `HIBOT_QUERIES_DIR` | Override `knowledge/queries`. |

`ApplicationIntent=ReadOnly` and `APP=HiBot-ReadOnly` are appended when absent. Example (do not commit real values):
`Driver={ODBC Driver 18 for SQL Server};Server=tcp:<host>,1433;Database=<db>;Uid=<ro-user>;Pwd=<secret>;Encrypt=yes`.

## Audit log

`logs/query_audit.jsonl`, one JSON object per line:

```json
{"ts":"2026-10-16T09:00:00.123+00:00","region":"EU","query_id":"wo_status_timeline",
 "params":{"WorkOrderId":123,"TenantId":5,"LanguageId":null},"sql_hash":"9f2c1a7e5b0d4c33",
 "row_count":12,"elapsed_ms":84,"truncated":false,"error":null}
```

`query_id` is `adhoc` for free-form SQL. `sql_hash` is the first 16 hex chars of SHA-256 of the *executed*
(rewritten, positional) SQL - the SQL text itself is not stored. String params are masked and cut to 200 chars.
Rejections are logged with `error: "rejected: <reason>"`. Result values are never written.

## Usage

```python
from hibot.db.runner import QueryRunner
r = QueryRunner("EU")                       # needs HIBOT_DB_EU
res = r.run_request({"query_id": "wo_header", "params": {"tenant_id": 5, "work_order_id": 123}})
if res.ok:
    print(res.columns, res.rows, res.truncated)
else:
    print(res.error)                        # safe to show to the LLM
```

```python
from hibot.db.templates import TemplateRegistry
reg = TemplateRegistry.load()
prompt_block = reg.catalog_text("EU")       # compact list for the LLM prompt
detail = reg.describe("wo_header")          # interpretation guide, fed back with the result
```

Tests: `cd d:\repos\HiBot && python -m unittest discover tests` (no DB, no pyodbc needed).

## Known gaps / limitations

* The validator cannot estimate query cost: an expensive but valid SELECT (large cross join, scalar UDF per row,
  `REPLICATE(...)` ) is stopped only by `TOP`, the 30 s timeout and `LOCK_TIMEOUT`. Prefer templates.
* Unqualified column names are not verified against the catalog (the database reports those errors).
* Functions are trusted by catalog analysis (no called procedures / written tables); a function that calls an
  extended procedure indirectly is not detectable. The read-only login is the backstop.
* PII masking is best-effort regex/column based: personal names inside free text, unusual id formats and
  plain digit strings are not detected. Declare PII columns in templates.
* The catalog is generated from the SSDT projects; drift from the live databases is possible (re-run
  `tools/schema_catalog/build.py`).
* READ UNCOMMITTED can show uncommitted or duplicated rows - fine for diagnostics, not for accounting.
* `"double quoted"` identifiers are accepted as identifiers (the runner sets QUOTED_IDENTIFIER ON).
* `OPENJSON` / `STRING_SPLIT` are allowed; XML methods (`col.value(...)`), `PIVOT` with exotic syntax and `FOR XML`
  nesting tricks may be rejected as unsupported (fail closed).
