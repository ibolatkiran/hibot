/*
id: wo_debug_log
title: Work order procedure debug log (EU)
purpose: Debug breadcrumbs written by procedures that handle a work order (dbo.WorkOrderDebugLog: procedure name, role, service shop in/out, branch flag, note). Use to see which service shop / role / language a procedure resolved for an order when a list or a permission check returned the wrong thing.
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: FromDate | datetime | optional | window start; default now minus 14 days
regions: EU
tables: dbo.WorkOrderDebugLog
pii: Note:free_text_body
row_limit: 100
interpret: Newest first. ServiceShopIdIn is the shop of the caller, ServiceShopIdOut the shop the procedure resolved; IsBranch=1 means branch logic applied. A Note usually holds the decision text. No rows = no procedure wrote debug rows for this order (logging is only on some code paths).
next: db_errors_for_wo, wo_header
*/
SELECT TOP 100
    d.Id, d.LogDate, d.ProcName, d.RoleId, d.LanguageId, d.ServiceShopIdIn, d.ServiceShopIdOut, d.IsBranch, d.Note
FROM dbo.WorkOrderDebugLog d
WHERE d.WorkOrderId = @WorkOrderId AND d.TenantId = @TenantId
  AND d.LogDate >= COALESCE(@FromDate, DATEADD(DAY, -14, GETDATE()))
ORDER BY d.LogDate DESC, d.Id DESC
