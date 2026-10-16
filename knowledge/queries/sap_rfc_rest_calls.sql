/*
id: sap_rfc_rest_calls
title: SAP RFC and REST call log
purpose: Raw outbound integration traffic: SAP RFC calls (Log.RFCCalls, keyed by Id_Value = usually the work order id) and REST calls (Log.RESTEntries, keyed by Key/Value) with request/response bodies (PII-masked, truncated). Use for "SAP did not receive", "integration returned error", "call retried / failed", "wrong payload sent".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: IdValue | int | optional | Log.RFCCalls.Id_Value (work order id / document id); also used as the REST Value unless RestValue is given
param: RestKey | string | optional max=100 | Log.RESTEntries.Key to filter (e.g. the key name used by the integration)
param: RestValue | string | optional max=100 | Log.RESTEntries.Value (defaults to IdValue)
param: FailedOnly | bool | optional default=0 | 1 = only failed calls (RFC Status=0, REST Result=0)
param: FromDate | datetime | optional | start of window (UTC); default now minus 30 days
param: ToDate | datetime | optional | end of window (exclusive); default open
require_any: IdValue, RestValue
regions: EU, APAC
tables: Log.RFCCalls, Log.RESTEntries
pii: RequestBody:free_text_body, ResponseBody:free_text_body
row_limit: 100
interpret: Source RFC/REST, newest first (up to 50 of each). Success = 1/0 (RFC Status, REST Result); HttpStatus and TryCount are REST only. Method column = RFC Resource or HTTP method; Endpoint = RFC function name or REST URL. Read the ResponseBody/ErrorText of the newest failing call first; repeated TryCount > 1 with the same body = retry loop on a permanent error. Bodies are truncated and masked - they show the shape of the payload, not customer data. If nothing is found widen FromDate or try the other key (RestKey/RestValue).
next: wo_erp_sap, db_errors_for_wo, outbound_queue_backlog
*/
WITH rfc AS (
    SELECT TOP 50 r.Id, r.[Timestamp] AS CallTime, r.FunctionName AS Endpoint, r.Id_Key AS KeyName,
           CAST(r.Id_Value AS nvarchar(100)) AS KeyValue, CAST(r.Status AS int) AS Success,
           CAST(NULL AS int) AS HttpStatus, CAST(NULL AS int) AS TryCount, r.Resource AS Method,
           r.Request AS RequestBody, r.Response AS ResponseBody
    FROM Log.RFCCalls r
    WHERE @IdValue IS NOT NULL AND r.Id_Value = @IdValue
      AND r.[Timestamp] >= COALESCE(@FromDate, DATEADD(DAY, -30, GETDATE()))
      AND (@ToDate IS NULL OR r.[Timestamp] < @ToDate)
      AND (@FailedOnly = 0 OR r.Status = 0)
    ORDER BY r.[Timestamp] DESC, r.Id DESC
),
rest AS (
    SELECT TOP 50 e.Id, e.[Timestamp] AS CallTime, e.RequestUrl AS Endpoint, e.[Key] AS KeyName,
           e.[Value] AS KeyValue, CAST(e.Result AS int) AS Success,
           e.StatusCode AS HttpStatus, e.TryCount AS TryCount, e.HttpMethod AS Method,
           e.RequestBody, e.ResponseBody
    FROM Log.RESTEntries e
    WHERE e.TenantId = @TenantId
      AND e.[Value] = COALESCE(@RestValue, CAST(@IdValue AS nvarchar(100)))
      AND (@RestKey IS NULL OR e.[Key] = @RestKey)
      AND e.[Timestamp] >= COALESCE(@FromDate, DATEADD(DAY, -30, GETDATE()))
      AND (@ToDate IS NULL OR e.[Timestamp] < @ToDate)
      AND (@FailedOnly = 0 OR e.Result = 0)
    ORDER BY e.[Timestamp] DESC, e.Id DESC
)
SELECT 'RFC' AS Source, Id, CallTime, Endpoint, KeyName, KeyValue, Success, HttpStatus, TryCount, Method, RequestBody, ResponseBody
FROM rfc
UNION ALL
SELECT 'REST', Id, CallTime, Endpoint, KeyName, KeyValue, Success, HttpStatus, TryCount, Method, RequestBody, ResponseBody
FROM rest
ORDER BY CallTime DESC
