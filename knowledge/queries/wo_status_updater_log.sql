/*
id: wo_status_updater_log
title: Automatic status updater log (EU)
purpose: Automatic status changes made by the dbo.StatusUpdater job for one work order (old -> new status, message) joined to the tenant's updater rules (Catalog.StatusUpdaterData), plus how many active rules could fire for the current status. Use for "status changed by itself", "order auto-closed/auto-cancelled", "job did not move the order".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU
tables: Operation.WorkOrder, dbo.StatusUpdaterLog, Catalog.Tenant, Catalog.Status, Catalog.AppTranslationEntry, Catalog.StatusUpdaterData
pii: Message:free_text_body
interpret: Each log row is one automatic change (LogDate is UTC). RuleFrequency/RuleActive come from Catalog.StatusUpdaterData for the same old/new pair and tenant: NULL means no rule row matches (change was made by an old/other rule set). If there are no log rows but PendingRuleCount > 0 the order is eligible for a rule but the job has not moved it (check job_health). PendingRuleCount = 0 means no active rule starts from the current status.
next: wo_status_timeline, wo_field_changes, job_health
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT
    wo.Id AS WorkOrderId, wo.StatusId AS CurrentStatusId, cn.FieldText AS CurrentStatusName,
    (SELECT COUNT(*) FROM Catalog.StatusUpdaterData r
      WHERE r.TenantId = @TenantId AND r.Active = 1 AND r.OldStatusId = wo.StatusId) AS PendingRuleCount,
    l.Id AS LogId, l.LogDate, l.OldStatusId, onm.FieldText AS OldStatusName,
    l.NewStatusId, nnm.FieldText AS NewStatusName, l.Message,
    sud.Frequency AS RuleFrequency, sud.Active AS RuleActive, sud.Creation_Date AS RuleCreated
FROM Operation.WorkOrder wo
CROSS JOIN lang
LEFT JOIN dbo.StatusUpdaterLog l ON l.WorkOrderId = wo.Id
LEFT JOIN Catalog.Status cur ON cur.Id = wo.StatusId
LEFT JOIN Catalog.Status ost ON ost.Id = l.OldStatusId
LEFT JOIN Catalog.Status nst ON nst.Id = l.NewStatusId
LEFT JOIN Catalog.StatusUpdaterData sud
       ON sud.OldStatusId = l.OldStatusId AND sud.NewStatusId = l.NewStatusId AND sud.TenantId = @TenantId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = cur.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) cn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = ost.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) onm
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = nst.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) nnm
WHERE wo.Id = @WorkOrderId AND wo.TenantId = @TenantId
ORDER BY l.LogDate, l.Id
