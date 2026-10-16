/*
id: mobile_api_trace
title: Mobile / API request trace for a work order (EU)
purpose: What the technician app and the V2 API did for a work order: processed mobile operations (V2.MobileOperationLog), audited API requests (V2.ApiRequestAudit) and offline-queue transactions (Operation.OfflineTransactionLog), merged into one timeline. Use for "technician cannot complete / sync fails", "app shows old data", "operation rejected", "offline data not uploaded".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: CorrelationId | string | optional max=200 | also include API requests with this correlation id
param: FromDate | datetime | optional | window start; default now minus 14 days
regions: EU
tables: V2.MobileOperationLog, V2.ApiRequestAudit, Operation.OfflineTransactionLog
pii: RequestBody:free_text_body, ResponseBody:free_text_body
row_limit: 150
interpret: Source MobileOperation = one client operation (Outcome = Status, ErrorCode when rejected, CorrelationId links to the API call). ApiRequest = audited HTTP call (OperationName = method+endpoint, Outcome = HTTP status, DurationMs; matched by correlation id or work order id in the URL, so it can include other calls that merely contain the id). OfflineTransaction = request queued while offline (Outcome success/failed, bodies masked and truncated). Look for the first failed/4xx/5xx row and its ErrorCode/ResponseBody, and for operations never followed by a success (stuck offline queue). Empty = the app never reached the V2 API for this order in the window.
next: db_errors_for_wo, wo_status_timeline, wo_field_changes
*/
WITH mop AS (
    SELECT TOP 50 'MobileOperation' AS Source, m.Id, m.ProcessedAt AS EventTime, m.WorkOrderId, m.EmployeeId,
           m.OperationType AS OperationName, m.Status AS Outcome, m.ErrorCode, m.CorrelationId,
           CAST(NULL AS int) AS DurationMs, CAST(NULL AS nvarchar(max)) AS RequestBody, m.ResultPayload AS ResponseBody
    FROM V2.MobileOperationLog m
    WHERE m.WorkOrderId = @WorkOrderId AND m.TenantId = @TenantId
      AND m.ProcessedAt >= COALESCE(@FromDate, DATEADD(DAY, -14, GETDATE()))
    ORDER BY m.ProcessedAt DESC, m.Id DESC
),
api AS (
    SELECT TOP 50 'ApiRequest' AS Source, a.Id, a.CreatedAt AS EventTime, CAST(NULL AS int) AS WorkOrderId,
           CAST(NULL AS int) AS EmployeeId, a.HttpMethod + ' ' + a.Endpoint AS OperationName,
           CAST(a.StatusCode AS nvarchar(50)) AS Outcome, CAST(NULL AS nvarchar(100)) AS ErrorCode,
           a.CorrelationId, a.DurationMs, a.RequestPayload AS RequestBody, a.ResponsePayload AS ResponseBody
    FROM V2.ApiRequestAudit a
    WHERE a.TenantId = CAST(@TenantId AS nvarchar(50))
      AND a.CreatedAt >= COALESCE(@FromDate, DATEADD(DAY, -14, GETDATE()))
      AND (a.CorrelationId IN (SELECT m2.CorrelationId FROM V2.MobileOperationLog m2
                                WHERE m2.WorkOrderId = @WorkOrderId AND m2.TenantId = @TenantId)
           OR a.Endpoint LIKE '%/' + CAST(@WorkOrderId AS varchar(20)) + '%'
           OR (@CorrelationId IS NOT NULL AND a.CorrelationId = @CorrelationId))
    ORDER BY a.CreatedAt DESC, a.Id DESC
),
offl AS (
    SELECT TOP 50 'OfflineTransaction' AS Source, o.Id, o.CreateDate AS EventTime, o.WorkOrderId, o.EmployeeId,
           CAST(o.EndPointName AS nvarchar(200)) AS OperationName,
           CASE WHEN o.IsSuccess = 1 THEN 'success' ELSE 'failed' END AS Outcome,
           CAST(NULL AS nvarchar(100)) AS ErrorCode, CAST(NULL AS nvarchar(200)) AS CorrelationId,
           CAST(NULL AS int) AS DurationMs, o.Request AS RequestBody, o.Response AS ResponseBody
    FROM Operation.OfflineTransactionLog o
    WHERE o.WorkOrderId = @WorkOrderId
      AND o.CreateDate >= COALESCE(@FromDate, DATEADD(DAY, -14, GETDATE()))
    ORDER BY o.CreateDate DESC, o.Id DESC
)
SELECT Source, Id, EventTime, WorkOrderId, EmployeeId, OperationName, Outcome, ErrorCode, CorrelationId, DurationMs, RequestBody, ResponseBody
FROM mop
UNION ALL
SELECT Source, Id, EventTime, WorkOrderId, EmployeeId, OperationName, Outcome, ErrorCode, CorrelationId, DurationMs, RequestBody, ResponseBody
FROM api
UNION ALL
SELECT Source, Id, EventTime, WorkOrderId, EmployeeId, OperationName, Outcome, ErrorCode, CorrelationId, DurationMs, RequestBody, ResponseBody
FROM offl
ORDER BY EventTime DESC
