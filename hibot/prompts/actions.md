## Actions (answer with exactly ONE JSON object)

1. Run a vetted query template (preferred). Template ids and their parameters are in the QUERY CATALOG.
   Parameter names are case-insensitive; `snake_case` works. Values must be literals, not placeholders.
```
{"action":"run_query","query_id":"wo_status_timeline","params":{"tenant_id":5,"work_order_id":12345678},"region":"EU","why":"..."}
```
2. Ad-hoc SQL (exception). One SELECT, small, tenant filtered, no personal data columns, no SELECT * on wide tables.
   It is validated against the schema catalog; a TOP limit is injected; rejected statements cost a step.
```
{"action":"run_sql","sql":"SELECT TOP 20 Id, Dsc FROM Catalog.Status WHERE TenantId = 5","region":"EU","why":"..."}
```
3. Look into the code graph (screen -> endpoint -> manager -> stored procedure -> tables).
   kind: endpoint (e.g. "GetSiriusWorkOrderHistory" or "GET api/Technician/..."), screen (route or screen name),
   db_object (e.g. "dbo.GetSiriusWorkOrderHistory"), error_text (message shown to the user), text (free search).
```
{"action":"inspect_code","target":{"kind":"endpoint","value":"GetSiriusWorkOrderHistory"},"why":"..."}
```
4. Read a small piece of source (max ~120 lines). Only the Sirius repositories; secret/config files are refused.
   `path` = absolute path, `<RepoFolder>/<relative path>`, or a schema object name such as "dbo.GetSiriusWorkOrderHistory".
```
{"action":"read_source","path":"dbo.GetSiriusWorkOrderHistory","start_line":1,"end_line":80,"why":"..."}
```
5. Finish. Send it when you can explain the cause, or when the budget is over / evidence is not obtainable.
```
{"action":"final_report",
 "summary":"1-3 cumle Turkce ozet",
 "classification":"code_defect|master_data|access|data_correction|integration|config|user_error|unknown",
 "root_cause":"Turkce kok neden; kanit yoksa 'belirsiz' yaz",
 "confidence":0.0,
 "evidence":[{"type":"query|code|similar_case|card","ref":"wo_history_function | path/File.cs:123 | SM-1234567 | card_id","finding":"gorulen somut sonuc"}],
 "code_locations":[{"repo":"SiriusApi","path":"Sirius/Controllers/X.cs","line":120,"why":"..."}],
 "fix_steps":["Turkce adimlar"],
 "verification_query_ids":["wo_history_function"],
 "suggested_card":{"title":"...","symptom_signature":{"problem_type":"master_data_request","app":null,"keywords":["..."],"entity_requirements":["sku_codes"]},
   "root_cause":"...","verification_queries":[{"query_id":"product_sku_lookup","params_from_entities":{"Sku":"entities.sku_codes[0]","TenantId":"$tenant"}}],
   "fix_steps":["..."],"code_locations":[]}}
```
`suggested_card` is optional: include it only when the finding is general enough to recur and is supported by evidence.
`confidence` is 0..1 (>= 0.8 only with executed-query or code evidence that decides the question).
`verification_query_ids` lists templates a developer should run to re-check (also useful when queries could not run).
