# HiBot DB schema catalog

Machine-readable catalog of the Sirius DB, generated from the two SSDT SQL projects (read-only):

- EU: `d:\repos\sirius-eu\gfsm-europe` (superset)
- APAC: `d:\repos\gfsm-apac\ahsiriusdbsrv.database.windows.net`

## Regenerate

```
cd d:\repos\HiBot
set PYTHONIOENCODING=utf-8
python tools/schema_catalog/build.py [--eu PATH] [--apac PATH] [--out PATH]
```

Python 3 stdlib only; deterministic (sorted output). Code: `tools/schema_catalog/{build,sqlcat,classify,reports}.py`.

## Files

| File | Content |
|---|---|
| `objects.json` | One record per object keyed `Schema.Name` (case-insensitive merge of EU+APAC). |
| `domains.json` | Domain tag per non-backup table + `pii_columns`; `by_domain` index. |
| `diagnostic_tables.md` | Logging/audit/integration/job/status-history tables: exact columns, join hints, regions. |
| `lookups.json` | Catalog status/lookup tables and how to resolve display names via `Catalog.AppTranslationEntry`. |
| `security_principals.json` | Names only of roles/users from Security folders (no contents; e-mail-named users counted, not listed). |

## objects.json record format

Common: `type` (table/view/procedure/function/udt/synonym/sequence), `regions` (["EU","APAC"]), `source_files` {region: path}, `region_identical` (normalized text equal in both regions; absent when only in one), `is_backup_or_copy`, `line_count`, `referenced_by`.

- table: `columns` [{name, sql_type, nullable, identity, default, description?}], `primary_key`, `indexes` [{name, columns, unique, clustered, include?, filter?}], `foreign_keys`, `triggers` [{name, events, timing, disabled, called_objects, written_tables, called_procs, raises_errors}], `trigger_calls`, `has_TenantId`, `written_by` (procs/triggers that INSERT/UPDATE/DELETE/MERGE it), `description`, `eu_only_columns`/`apac_only_columns`.
- procedure/function/view: `parameters` [{name, sql_type, default, output?}], `referenced_objects` (ALL known objects mentioned, includes written tables and called procs), `written_tables`, `called_procs`, `raises_errors` [{kind, message, severity|number}] (literal text; `{@var}` = interpolated expression), `writes_error_log`, `dynamic_sql_refs` (known objects named inside string literals only), `returns` (functions), `eu_only_refs`/`apac_only_refs`.
- Bodies are NOT stored; open `source_files`.

## Stats (this run)

- Objects: 3476; by type: {'function': 616, 'procedure': 1229, 'table': 1349, 'view': 197, 'synonym': 4, 'udt': 80, 'sequence': 1}
- By region presence: {'EU+APAC': 2480, 'EU': 901, 'APAC': 95}
- Per type and region: {'function': {'EU': 615, 'APAC': 549}, 'procedure': {'EU': 1216, 'APAC': 999}, 'table': {'EU': 1268, 'APAC': 763}, 'view': {'EU': 197, 'APAC': 187}, 'synonym': {'EU': 4, 'APAC': 4}, 'udt': {'EU': 80, 'APAC': 73}, 'sequence': {'EU': 1}}
- Backup/copy flagged (heuristic): 234; tables with TenantId: 457; triggers: 32
- Domains (non-backup tables): {'country_specific': 310, 'integration_gateway': 94, 'status_lookup': 83, 'logging_audit': 77, 'other': 69, 'product_catalog': 68, 'erp_sap_invoicing': 68, 'notification_sms_email': 60, 'material_spare_parts': 60, 'payment': 59, 'work_order': 58, 'survey_nps': 47, 'user_auth': 32, 'consumer_crm': 28, 'service_shop_partner': 26, 'technician_scheduling': 19, 'job_scheduling': 11, 'translation_i18n': 7, 'work_case': 4}
- Tables flagged PII: 234
- Diagnostic tables not found: ['Operation.WorkOrderErpDetailsFi (requested name does not exist; actual table is Operation.WorkOrderErpFi)', 'Operation.WorkOrderErpDetailsMm (requested name does not exist; actual table is Operation.WorkOrderErpMm)']
- Skipped folders (unknown kinds): {'EU': {}, 'APAC': {}}

## Known limitations of the regex parsing

- Not a real T-SQL parser. Comments and string literals are stripped before reference extraction; objects are matched only if the `schema.name` token (bracketed or not) equals a known file-derived object. Unqualified names resolve to `dbo` only.
- Dynamic SQL (`EXEC(@sql)`, `sp_executesql`) is not analysed; known names inside string literals are listed separately as `dynamic_sql_refs`.
- Aliased column references like `T.Col` never match because `T.Col` is not a known object; but an alias identical to a schema name, or a column named like an object, can produce false positives.
- `referenced_objects` does not distinguish reads from writes (use `written_tables`). Writes via `UPDATE alias SET ... FROM table alias` are resolved only when the alias is declared in a FROM/JOIN.
- Writes to temp tables, table variables, synonyms (resolved only to the synonym key) and cross-database/linked-server objects are not tracked.
- RAISERROR/THROW: only the message expression is captured; format args and non-literal messages appear as `{@var}`.
- `is_backup_or_copy` and domains are name heuristics and will have false positives/negatives; domain precedence is first-match.
- Triggers: embedded CREATE TRIGGER batches; DISABLE TRIGGER statements detected in same file.
- Computed columns have sql_type `computed`; user-defined types are kept as written.
- When EU and APAC definitions differ, the EU parse is used (APAC only for APAC-only objects); differences summarised in `eu_only_*`/`apac_only_*`.
- Row data (lookup values, tenants) is not available: only structure.
