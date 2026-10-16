/*
id: wo_status_timeline
title: Work order status timeline
purpose: Status history of one work order from Operation.WorkOrderGpsInfo (despite its name this is the status-transition table), with status names, who did it, the previous status and the time between steps. Use for "status went back", "status skipped", "stuck in status", "who changed the status".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Operation.WorkOrderGpsInfo, Catalog.Tenant, Catalog.Status, Catalog.AppTranslationEntry, Catalog.Employee, Operation.WorkOrder
pii: EmployeeName:name, Comment:free_text_body
pii_sources: Dsc
interpret: One row per status transition ordered by OperationDate (business time) then Id. CurrentStatusId is Operation.WorkOrder.StatusId now: if the last row's StatusId differs from CurrentStatusId, the order was changed without a history row (direct UPDATE / trigger / job). MinutesSincePrev is large where the order stalled. SysDate is when the row was written (UTC) - OperationDate far from SysDate means an offline/mobile sync delay. Few rows may also mean history rows were never written (compare with wo_history_function).
next: wo_history_function, wo_field_changes, wo_status_updater_log, wo_status_transition_check
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT
    g.Id AS HistoryId, g.WorkOrderId, g.StatusId, stn.FieldText AS StatusName, st.IsOpen, st.IsComplete,
    g.OperationDate, g.SysDate,
    LAG(g.StatusId) OVER (ORDER BY g.OperationDate, g.Id) AS PrevStatusId,
    DATEDIFF(MINUTE, LAG(g.OperationDate) OVER (ORDER BY g.OperationDate, g.Id), g.OperationDate) AS MinutesSincePrev,
    g.EmployeeId, emp.TechnicianCode, emp.Dsc AS EmployeeName, g.Comment,
    (SELECT w.StatusId FROM Operation.WorkOrder w WHERE w.Id = g.WorkOrderId) AS CurrentStatusId
FROM Operation.WorkOrderGpsInfo g
CROSS JOIN lang
LEFT JOIN Catalog.Status st ON st.Id = g.StatusId
LEFT JOIN Catalog.Employee emp ON emp.Id = g.EmployeeId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = st.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) stn
WHERE g.WorkOrderId = @WorkOrderId AND g.TenantId = @TenantId
ORDER BY g.OperationDate, g.Id
