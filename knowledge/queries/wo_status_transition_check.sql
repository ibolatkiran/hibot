/*
id: wo_status_transition_check
title: Allowed next statuses for a work order
purpose: Current status of a work order versus the tenant's configured status workflow (Catalog.StatusStep = allowed next statuses, Catalog.StatusTenant = tenant status mapping, IsOpen/IsComplete flags). Use for "cannot move to status X", "status button missing", "order cannot be closed/reopened", "status not allowed".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Operation.WorkOrder, Catalog.Tenant, Catalog.Status, Catalog.StatusTenant, Catalog.StatusStep, Catalog.AppTranslationEntry, Operation.WorkOrderGpsInfo
interpret: One row per allowed next status (NextStatusId). If NextStatusId is NULL the tenant has NO StatusStep rows for the current status (dead end - configuration gap) - check the status exists for the tenant (StatusTenantId/LocalId NULL means no Catalog.StatusTenant mapping). To test a specific move, look for the target in the NextStatusId column; if absent the transition is not configured for this tenant. NextActive = 0 means the target status is disabled. LatestHistoryStatusId <> CurrentStatusId means the history table disagrees with the order.
next: wo_status_timeline, wo_field_changes, wo_status_updater_log
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT
    wo.Id AS WorkOrderId, wo.StatusId AS CurrentStatusId, cn.FieldText AS CurrentStatusName,
    cur.IsOpen AS CurrentIsOpen, cur.IsComplete AS CurrentIsComplete, cur.Active AS CurrentActive,
    cur.TenantId AS CurrentStatusTenantId, stt.Id AS StatusTenantId, stt.LocalId, stt.MobileStatusId AS TenantMobileStatusId,
    (SELECT TOP 1 g.StatusId FROM Operation.WorkOrderGpsInfo g
      WHERE g.WorkOrderId = wo.Id AND g.TenantId = @TenantId
      ORDER BY g.OperationDate DESC, g.Id DESC) AS LatestHistoryStatusId,
    ss.NextStatusId, nn.FieldText AS NextStatusName, nxt.IsOpen AS NextIsOpen, nxt.IsComplete AS NextIsComplete,
    nxt.Active AS NextActive
FROM Operation.WorkOrder wo
CROSS JOIN lang
LEFT JOIN Catalog.Status cur ON cur.Id = wo.StatusId
LEFT JOIN Catalog.StatusTenant stt ON stt.StatusId = wo.StatusId AND stt.TenantId = @TenantId
LEFT JOIN Catalog.StatusStep ss ON ss.StatusId = wo.StatusId AND ss.TenantId = @TenantId
LEFT JOIN Catalog.Status nxt ON nxt.Id = ss.NextStatusId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = cur.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) cn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = nxt.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) nn
WHERE wo.Id = @WorkOrderId AND wo.TenantId = @TenantId
ORDER BY ss.NextStatusId
