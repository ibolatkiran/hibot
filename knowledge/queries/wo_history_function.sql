/*
id: wo_history_function
title: Work order history as the UI function returns it
purpose: Runs dbo.GetSiriusWorkOrderHistory (what the web history tab shows) and puts the row counts of the raw tables next to it. Use for "history shows only 1 record", "history is missing steps", "history shows another work order".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Catalog.Tenant, Operation.WorkOrderGpsInfo, Operation.WorkOrder, Catalog.Status, Catalog.AppTranslationEntry, dbo.GetSiriusWorkOrderHistory
pii: ISLEM_YAPAN:name, CancellationNote:free_text_body, StatusNote:free_text_body, GPSNote:free_text_body
interpret: The function returns the history of ALL work orders of the same WorkCase (see WorkOrderId / IsRequestedWorkOrder) and INNER JOINs Catalog.Status + AppTranslationEntry in the requested language. Compare counters: RawGpsRowsThisOrder = rows in Operation.WorkOrderGpsInfo for this order; RawGpsRowsWholeCase = same for all orders of the case; FunctionRows = rows returned. If Raw > Function: rows were dropped - RawRowsMissingTranslation > 0 means a status has no translation in this language (fix translation data); otherwise a join to WorkOrder/WorkCase failed. If RawGpsRowsThisOrder is 1 the history was really never written (see wo_status_timeline / wo_field_changes). One row with NULL function columns = function returned nothing.
next: wo_status_timeline, wo_field_changes, workcase_tree
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT
    cnt.RawGpsRowsThisOrder, cnt.RawGpsRowsWholeCase, cnt.RawRowsMissingTranslation,
    COUNT(h.WorkOrderId) OVER () AS FunctionRows,
    CASE WHEN h.WorkOrderId = @WorkOrderId THEN 1 ELSE 0 END AS IsRequestedWorkOrder,
    h.WorkOrderId, h.TARIH, h.DURUM, h.MobileStatusId, h.ISLEM_YAPAN, h.KULLANICI, h.SIRA, h.SysDate,
    h.StatusNote, h.GPSNote, h.CancellationReasonId, h.CancellationReason, h.CancellationNote
FROM lang
CROSS APPLY (
    SELECT
        (SELECT COUNT(*) FROM Operation.WorkOrderGpsInfo g
          WHERE g.WorkOrderId = @WorkOrderId AND g.TenantId = @TenantId) AS RawGpsRowsThisOrder,
        (SELECT COUNT(*) FROM Operation.WorkOrderGpsInfo g
           JOIN Operation.WorkOrder w ON w.Id = g.WorkOrderId
          WHERE g.TenantId = @TenantId
            AND w.WorkCaseId = (SELECT w0.WorkCaseId FROM Operation.WorkOrder w0
                                 WHERE w0.Id = @WorkOrderId AND w0.TenantId = @TenantId)) AS RawGpsRowsWholeCase,
        (SELECT COUNT(*) FROM Operation.WorkOrderGpsInfo g
          WHERE g.WorkOrderId = @WorkOrderId AND g.TenantId = @TenantId
            AND NOT EXISTS (SELECT 1 FROM Catalog.Status s
                              JOIN Catalog.AppTranslationEntry e ON e.AppTranslationId = s.AppTranslationId
                             WHERE s.Id = g.StatusId AND e.LanguageId = lang.Id)) AS RawRowsMissingTranslation
) cnt
OUTER APPLY dbo.GetSiriusWorkOrderHistory(@WorkOrderId, @TenantId, lang.Id) h
ORDER BY h.SIRA
