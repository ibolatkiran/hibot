/*
id: workcase_tree
title: Work case with all its work orders
purpose: Shows a work case (customer request) and every work order under it with status names, plus cancellation info. Use for "duplicate work orders", "case closed but order open", "reopened case", "case cancelled", or when a ticket quotes a case id instead of a work order id.
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkCaseId | int | optional | Operation.WorkCase.Id
param: WorkOrderId | int | optional | any work order of the case (its WorkCaseId is used)
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
require_any: WorkCaseId, WorkOrderId
regions: EU, APAC
tables: Operation.WorkCase, Operation.WorkOrder, Catalog.Tenant, Catalog.Status, Catalog.AppTranslationEntry, Catalog.WorkCaseCancellation, Catalog.CancellationReason, Catalog.Product
pii: CancellationNote:free_text_body
interpret: First columns describe the case (CaseStatusName, OpenDate, CloseDate, ReOpenDate, product), the remaining ones one work order per row (OrderedBy OpenDate). A closed case (CaseCloseDate set / CaseIsComplete = 1) with open orders, or several open orders for the same case, explains most "duplicate" or "stuck" reports. CancellationReason is filled when the case was cancelled (Catalog.WorkCaseCancellation). ReOpenDate/ReOpenCloseDate show reopen cycles.
next: wo_header, wo_status_timeline, wo_history_function
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id),
target AS (
    SELECT wc0.Id
    FROM Operation.WorkCase wc0
    WHERE wc0.TenantId = @TenantId
      AND wc0.Id = COALESCE(@WorkCaseId, (SELECT w0.WorkCaseId FROM Operation.WorkOrder w0
                                           WHERE w0.Id = @WorkOrderId AND w0.TenantId = @TenantId))
)
SELECT TOP 100
    wc.Id AS WorkCaseId, wc.StatusId AS CaseStatusId, cn.FieldText AS CaseStatusName, cst.IsComplete AS CaseIsComplete,
    wc.OpenDate AS CaseOpenDate, wc.CloseDate AS CaseCloseDate, wc.CallDate, wc.ReOpenDate, wc.ReOpenCloseDate,
    wc.ExternalId AS CaseExternalId, wc.ChannelId AS CaseChannelId, wc.CaseOwnerId,
    wc.ProductId, p.SKU, wc.AssetId, wc.ServiceErrorCode, wc.CustomerErrorCode,
    canc.CancellationReasonId, crn.FieldText AS CancellationReason, canc.CancellationNote, canc.CreateDate AS CancelledAt,
    wo.Id AS WorkOrderId, wo.StatusId AS StatusId, wn.FieldText AS WorkOrderStatusName, wst.IsOpen AS WorkOrderIsOpen,
    wo.OpenDate AS WorkOrderOpenDate, wo.AppointmentDate, wo.EmployeeId, wo.ServiceShopId,
    wo.Reopen, wo.TaskTypeId, wo.ExternalId AS WorkOrderExternalId
FROM target
CROSS JOIN lang
JOIN Operation.WorkCase wc ON wc.Id = target.Id
LEFT JOIN Operation.WorkOrder wo ON wo.WorkCaseId = wc.Id AND wo.TenantId = @TenantId
LEFT JOIN Catalog.Status cst ON cst.Id = wc.StatusId
LEFT JOIN Catalog.Status wst ON wst.Id = wo.StatusId
LEFT JOIN Catalog.Product p ON p.Id = wc.ProductId
LEFT JOIN Catalog.WorkCaseCancellation canc ON canc.WorkCaseId = wc.Id
LEFT JOIN Catalog.CancellationReason cr ON cr.Id = canc.CancellationReasonId AND cr.TenantId = @TenantId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = cst.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) cn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = wst.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) wn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = cr.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) crn
ORDER BY wo.OpenDate, wo.Id
