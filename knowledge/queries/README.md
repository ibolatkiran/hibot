# Query template library

One file per query: `<id>.sql` = a `/* ... */` header block + one `SELECT` / `WITH ... SELECT`.
Loaded by `hibot/db/templates.py`; every template is validated by `tests/test_templates.py` against the schema
catalog for each declared region. The LLM picks a template by `id` and supplies parameter values; values are bound
as ODBC parameters (`@Name` in the file becomes `?` at run time) and never formatted into the SQL.

## Header format

`key: value` per line; a line that starts with whitespace continues the previous value.

```
/*
id: wo_status_timeline                 (== file name, [a-z0-9_])
title: Work order status timeline
purpose: what it diagnoses / incident symptoms it answers ("history shows 1 record", "stuck in status")
param: TenantId | int | required | Catalog.Tenant.Id
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | name language, defaults to tenant language
param: ErrorProcedure | string | optional like max=200 | name contains this text
param: WindowHours | int | optional default=24 min=1 max=720 | look-back window
require_any: WorkOrderId, ExternalId    (optional: at least one of these must be supplied)
regions: EU, APAC                       (must be EXACTLY the regions where the SQL validates)
tables: Operation.WorkOrderGpsInfo, ... (must be EXACTLY the tables/functions the SQL reads; CTEs excluded)
pii: ContactName:name, ContactPhone:phone, Comment:free_text_body   (RESULT column names)
pii_sources: Dsc, UserName              (PII-flagged catalog columns used under a different alias)
interpret: how to read the result; what a NULL / 0 rows / big gap means; where to go next
next: wo_history_function, wo_field_changes   (follow-up template ids; must exist)
row_limit: 100                          (optional, default 200, max 1000)
*/
SELECT ...
```

* Param types: `int`, `bigint`, `string`, `datetime`, `date`, `bool`, `decimal`. Options after `required|optional`:
  `default=V`, `like` (string is wildcard-escaped for `LIKE '%' + @P + '%'`), `max=N` (string length / numeric max), `min=N`.
* Caller parameter names are matched case-insensitively and `snake_case` also works (`work_order_id`).
* PII kinds: `email`, `phone`, `contact` (e-mail or phone), `name`, `address`, `identity`, `iban_bank`, `free_text_body`.
* `row_limit` is also enforced by an injected `TOP`.

## Conventions (follow them when adding a template)

1. **Always tenant scoped**: take `TenantId` and filter every tenant table with it (PK lookups on lookup tables are fine).
   The validator warns about tenant tables without a predicate; shipped templates must produce zero warnings.
2. **Names, not ids**: resolve status/lookup ids through `Catalog.AppTranslationEntry` with the `lang` CTE and an
   `OUTER APPLY (SELECT TOP 1 e.FieldText ... ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId)`
   so a missing translation falls back to another language instead of dropping rows. Copy the pattern from `wo_header.sql`.
   Tables whose name column is plain text (`Catalog.ServiceShop.Dsc`, `Catalog.Role.Dsc`) are used directly.
3. **Small result sets**: `TOP n`, bounded time windows (`COALESCE(@FromDate, DATEADD(...))`), `SELECT TOP 50` per
   branch inside CTEs before `UNION ALL`, truncated samples (`LEFT(x, 300)`), no `SELECT *`.
4. **Only PII you need**, and declare it (`pii:` / `pii_sources:`). Never select credentials (validator rejects them).
5. **Match varchar columns with `CAST(@P AS varchar(n))`** so the driver's NVARCHAR parameter does not force a scan.
6. **Optional filters**: `(@P IS NULL OR col = @P)` plus `OPTION (RECOMPILE)` as the last clause.
7. No hints, no `SET`, no temp tables, no comments needed inside the SQL (they are stripped).
8. Use `[Key]`, `[Value]`, `[Timestamp]`, `[Desc]` brackets for awkward column names.
9. Region differences: if the SQL touches an EU-only object or column (`dbo.StatusUpdaterLog`, `V2.*`,
   `Log.WorkOrderDiscountLog`, `dbo.WorkOrderDebugLog`, `Operation.OfflineTransactionLog`, `Catalog.Material.IsWHR`,
   `Catalog.SmsEmailSendingStatus.DscEn`) declare `regions: EU` or split the template (`wo_lines_totals` /
   `wo_discount_log`). The test fails if the header does not say exactly where the SQL validates.

## How to add a template

1. Check real names in `knowledge/schema/diagnostic_tables.md` and `objects.json` (columns, `regions`, `has_TenantId`).
2. Copy the closest existing template, change header + SQL, name the file `<id>.sql`.
3. `cd d:\repos\HiBot && python -m unittest tests.test_templates -v` - it reports unknown objects/columns, region
   mismatches, header/table mismatches, undeclared PII, unused or undeclared parameters, broken `next:` links, warnings.
4. Add the id to `REQUIRED_IDS` in the test only if it is a mandatory template.

## Catalog (see `TemplateRegistry.catalog_text()` for the prompt form)

| id | regions | use for |
|---|---|---|
| `wo_header` | EU, APAC | first look at an order by id / ExternalId / ExternalWorkOrderId |
| `wo_status_timeline` | EU, APAC | status history with names, previous status, gaps |
| `wo_history_function` | EU, APAC | `dbo.GetSiriusWorkOrderHistory` vs raw rows ("history shows 1 record") |
| `wo_field_changes` | EU, APAC | which fields changed when (Log.WorkOrderJsonLog) |
| `wo_status_transition_check` | EU, APAC | allowed next statuses of the CURRENT status (StatusStep / StatusTenant) |
| `status_step_check` | EU, APAC | is transition FromStatusId -> ToStatusId configured for the tenant? (manual-DB-fix follow-up) |
| `wo_status_updater_log` | EU | automatic status changes + updater rules |
| `workcase_tree` | EU, APAC | case with all its work orders, cancellation |
| `wo_lines_totals` | EU, APAC | detail lines vs header totals |
| `wo_discount_log` | EU | manual discount log |
| `wo_erp_sap` | EU, APAC | ERP / FI / MM documents and errors |
| `sap_rfc_rest_calls` | EU, APAC | Log.RFCCalls + Log.RESTEntries (failed only option) |
| `material_orders` | EU, APAC | spare-part orders and lines |
| `db_errors_for_wo` | EU, APAC | Log.DB_Errors mentioning `WOId:<id>` |
| `db_errors_recent` | EU, APAC | errors grouped by procedure / number |
| `db_errors_list` | EU, APAC | individual error rows by procedure / text |
| `wo_debug_log` | EU | dbo.WorkOrderDebugLog |
| `mobile_api_trace` | EU | V2 mobile ops, API audit, offline queue |
| `notifications_for_wo` | EU, APAC | SMS / e-mail queue rows of an order |
| `outbound_queue_backlog` | EU, APAC | Gateway.Notification / PostErrorLog / Email2Case outbox |
| `job_health` | EU, APAC | SiriusInt job runs and error log |
| `technician_appointment_context` | EU, APAC | appointment vs technician/shop off-work and capacity |
| `product_sku_lookup` | EU, APAC | SKU exists? assigned to tenant? |
| `tenant_lookup` | EU, APAC | tenant by country / name |
| `user_access_lookup` | EU, APAC | user, tenant membership, employee, role (no credentials) |

## Live timings (PROD EU, 2026-10-16, SQL login read-only, 40 s timeout)

Rows / elapsed ms per template. Samples: WO 22811628 (tenant 68, ZA) and WO 22197255 (tenant 70, EG). All templates are < 8 s.

| template | params | rows (68 / 70) | ms (68 / 70) |
|---|---|---|---|
| wo_header | TenantId, WorkOrderId | 1 / 1 | 2465 / 793 |
| wo_status_timeline | TenantId, WorkOrderId | 3 / 16 | 329 / 498 |
| wo_history_function | TenantId, WorkOrderId | 3 / 14 | 321 / 321 |
| wo_field_changes | TenantId, WorkOrderId | 13 / 41 | 899 / 829 |
| wo_status_transition_check | TenantId, WorkOrderId | 1 / 1 | 403 / 287 |
| status_step_check | TenantId=68, FromStatusId=329, ToStatusId=314 | 4 | 1829 |
| wo_status_updater_log | TenantId, WorkOrderId | 1 / 1 | 471 / 290 |
| wo_lines_totals | TenantId, WorkOrderId | 1 / 5 | 344 / 291 |
| wo_discount_log | TenantId, WorkOrderId | 0 / 0 | 270 / 193 |
| wo_erp_sap | TenantId, WorkOrderId | 0 / 1 | 321 / 312 |
| material_orders | TenantId, WorkOrderId | 0 / 0 | 397 / 283 |
| wo_debug_log | TenantId, WorkOrderId | 0 / 0 | 326 / 296 |
| mobile_api_trace | TenantId, WorkOrderId | 0 / 0 | 2496 / 735 |
| notifications_for_wo | TenantId, WorkOrderId | 1 / 1 | 405 / 226 |
| technician_appointment_context | TenantId, WorkOrderId | 1 / 73 | 392 / 214 |
| workcase_tree | TenantId, WorkOrderId | 1 / 1 | 4801 / 3579 |
| sap_rfc_rest_calls | TenantId, IdValue=WO id | 0 / 0 | 1223 / 1551 |
| db_errors_for_wo | WorkOrderId | 0 / 0 | 1244 / 1400 |
| wo_locate | Number=22811628 | 1 | 2195 |
| tenant_lookup | CountryCode=ZA / TenantId=68 | 1 / 1 | 332 / 306 |
| product_sku_lookup | TenantId=68, Sku (a tenant-68 SKU) | 1 | 2363 |
| user_access_lookup | UserCode (a tenant-68 technician code), TenantId=68 | 0 | 293 |
| db_errors_recent | (defaults) | 8 | 1429 |
| db_errors_list | ErrorProcedure=Update | 100 | 1134 |
| outbound_queue_backlog | (defaults) | 4 | 5163 |
| job_health | (defaults) | 1 | 550 |

### Performance lesson (wo_field_changes timed out, fixed)
`WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 ... FROM Catalog.Tenant ...)) AS Id)` cross-joined to a
`Log.WorkOrderJsonLog` derived table made the optimizer abandon the `InxWorkOrderId` seek (>30 s, HYT00).
Parameter binding was NOT the cause (ints are bound as ints; `OPTION (RECOMPILE)` did not help). Fix: no `lang` CTE in
templates that read big log tables; join `Catalog.Tenant tn ON tn.Id = @TenantId` and use `COALESCE(@LanguageId, tn.LanguageId)`
inside the `OUTER APPLY` ordering (0.9 s). The log filter (WorkOrderId + tenant predicate) lives inside the derived table.
Other templates still use the `lang` CTE but only over small, tenant-keyed tables and are fast (table above).
