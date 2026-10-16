# HiBot code graph

One queryable graph joining the **code map** (`knowledge/codemap`: screen -> API call -> endpoint -> manager method -> db objects) with the **DB schema catalog** (`knowledge/schema/objects.json`: procedures/functions/views with referenced/written tables and called procs, tables with triggers). Built by `tools/graph/build.py` (stdlib only, deterministic), queried by `hibot/knowledge/graph.py` (`CodeGraph`).

## Regenerate

```
cd d:\repos\HiBot
set PYTHONIOENCODING=utf-8
python tools/schema_catalog/build.py && python tools/code_map/build.py   # inputs, if stale
python tools/graph/build.py          # ~3 s -> knowledge/graph/graph.json (~4.5 MB, git-ignored) + stats.json
python -m unittest tests.test_graph
```

## graph.json format

`{meta, nodes, edges, errors, i18n}`

- `nodes`: ordered dict `id -> attrs`. Ids: `screen:<app>:<file>`, `route:<app>:<path>`, `endpoint:<VERB> <route>`, `method:<Class.Method>`, `proc:<schema.name>`, `func:..`, `view:..`, `table:<Schema.Name>`, `trigger:<Schema.Table>.<name>`. Apps: portal, crm, partner, technician. DB nodes carry `regions` (["EU","APAC"]), `is_backup_or_copy`, `file` (EU source preferred), `unresolved` (name referenced by code but absent from the catalog). Tables carry `columns`, `domain`. Code nodes carry `file`/`line`.
- `edges`: `[src_index, dst_index, type, attrs?]` (indexes into the node order; attrs hold `file`/`line` call sites and `via`). Types:
  `renders` route -> screen; `invokes` screen -> endpoint (call site file:line) **and** method/endpoint -> proc/func/view called from C#; `calls` endpoint -> method, method -> method, proc/trigger -> proc; `uses` proc/func/view -> func/view; `reads` / `writes` routine/trigger (or inline SQL in C#) -> table; `touches` method -> table via EF/ADO (read vs write unknown); `fires` table -> trigger. Reverse direction ("called_by") is computed at load time.
- `errors`: error-text origins (`db_procedure`, `db_function`, `db_trigger`, `backend_method`, `backend_endpoint`) with `text`, `node`, `file`, `line`.
- `i18n`: per app `texts["ns::key"] = [en, tr]` and `key_usage["ns::key"] = [screen files]` (copied from the codemap).

## Query API (`from hibot.knowledge import CodeGraph; g = CodeGraph.load()`)

All results are JSON-serializable. Items are dicts with `name`, `evidence`, and `file`/`line` where known. Backup/copy objects (`is_backup_or_copy`) are excluded from transitive expansion, reverse results and search unless `include_backups=True`; objects code invokes directly, or that you name explicitly, are always shown (flagged).

| call | returns |
|---|---|
| `trace_endpoint("Technician/GetSiriusWorkOrderHistory")` (also full route, `GET api/...`, `.../123`, or just a unique action/route tail) | `{endpoint, file, line, callers[{app,screen,file,line,evidence}], callers_by_app, methods[], db_objects{procs,funcs,views}, tables_read[], tables_written[], tables_code_access[], triggers[{name,table,events,writes,calls}], files[{path,line,what}], error_messages[]}` |
| `trace_screen("portal", file_or_route)` | matched screens, every endpoint (with call site and full trace) plus aggregated tables/db_objects/triggers. Matching: exact file, exact route, file substring, route substring |
| `trace_db_object("Operation.WorkOrderGpsInfo")` (also `dbo.X`, bare `X`, `Table.Column`, trigger name) | table: `readers`, `writers`, `code_access`, `triggers_on_table`; routine: `called_by`, forward `tables_read/written/triggers`; both: `endpoints[{endpoint,evidence path,via_trigger}]`, `n_endpoints`, `screens`, `screens_by_app`, `apps`, `methods`. Column queries add `mentions_column` (text search of each object's source) |
| `search_error_text(text, limit=10)` | ranked origins `{kind(db_*/backend_*/i18n), score, text, origin, file, line, evidence, endpoints[], screens[]}` |
| `search_text(text, app=None, limit=10)` | BM25 over screens (path, routes, i18n en/tr texts, called routes), endpoints (route, method names, error texts), procs/funcs/views/tables (name, table descriptions/columns); `{type, name, score, evidence(matched terms), ...}` |

How the transitive forward closure works: endpoint -> methods (following `calls`) -> directly invoked routines -> `calls`/`uses` recursively; each visited routine contributes its `reads`/`writes`; every written table adds its enabled triggers, which in turn contribute their own writes/calls (e.g. `Operation.WorkOrder` -> `TR_WorkOrder_U` -> `Log.WorkOrderJsonLog`). Reverse traversal is the mirror image (a writer of table X also reaches endpoints through triggers on X, flagged `via_trigger`).

## Stats (this build)

Nodes 7487: table 1385, proc 1293, func 624, view 197, trigger 32, method 1755, endpoint 934, screen 926, route 341.
Edges 16954: reads 7480, invokes 3357, calls 2667, uses 1376, writes 1084, touches 634, renders 324, fires 32.
Endpoints reaching >= 1 table (incl. EF/ADO `touches`): **89.6%** (837/934); via SQL objects/inline SQL with a known read/write: 66.7%; with a screen caller: 813/934. Non-backup SPs with >= 1 resolved table: **95.2%** direct, 96.9% transitive. Unresolved db refs from C#: 64 proc, 8 func, 36 table (names not in the catalog, e.g. EF DbSet names like `Employees1`, other-region objects). 926 screens, 623 call at least one endpoint. 711 error-text records.

## Worked examples

`trace_endpoint("Technician/GetSiriusWorkOrderHistory")` -> callers in portal (`WorkOrderDetail/History/index.js`, `Bulletin`, `ProductExchange`), crm, partner, technician (`WorkOrder/History.js`); `TechnicianController.cs:2093` -> `TechnicianManager.GetSiriusWorkOrderHistory` (`TechnicianManager.cs:6131`) -> `func dbo.GetSiriusWorkOrderHistory` -> reads Operation.WorkOrderGpsInfo, Operation.WorkOrder, Operation.WorkCase, Catalog.Status, Catalog.AppTranslationEntry, ... (12 tables, no writes).

`trace_db_object("Operation.WorkOrderGpsInfo")` -> 54 endpoints, screens in all 4 apps (crm 38, technician 35, partner 25, portal 21).

`trace_endpoint("POST api/Technician/SetSiriusWorkOrderStatusUpdate")` -> writes Operation.WorkOrder; triggers `TR_WorkOrder_U` (writes Log.WorkOrderJsonLog) and `Trg_WorkOrder_Update`.

`search_error_text("Failed to change status please check data!")` -> trigger `TR_WorkOrder_U` (Operation/Tables/WorkOrder.sql:196), reachable from 89 endpoints.

## Limitations

- Inherits all codemap/schema limitations (regex parsing, no dynamic SQL, no SP bodies in codemap).
- `reads` excludes tables that a routine also writes (the catalog does not separate them), so such tables show as writes only; `referenced_objects` procs not in `called_procs` are not linked.
- EF/ADO table names are unqualified (and EF uses DbSet plural names): resolved by singularising and a schema priority (Operation, Catalog, dbo, ...); ambiguous matches (`ambiguous_via_code`) may pick the wrong schema. Read vs write for these is unknown (`tables_code_access`).
- Triggers are assumed to fire on any write (event type not matched); disabled triggers are skipped in traversal.
- Transitive closure is a superset: a routine's branches that are not taken at runtime are included; parameters/conditions are not modelled. A table reachable only through a trigger chain is flagged `via_trigger` in reverse results.
- Region availability is per object; a path may exist only in one region (check `regions`).
- `search_text` is lexical (tokens, light plural stripping, Turkish folding); no semantics. Unused i18n keys have no screens, so error-text hits on them show only the key.
- Column-level lookups are text search in source, not parsed column lineage.
