/*
id: status_step_check
title: Is a specific status transition configured for the tenant?
purpose: Answers "can status A move to status B for this tenant?" from Catalog.StatusStep (the workflow table the app uses to allow a status change). Use when a ticket says a status/cancel/reject/close cannot be done, or when Log.WorkOrderJsonLog shows a status change (A to B) that has no history row (suspected manual DB fix).
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: FromStatusId | int | required | Catalog.Status.Id the order is/was in
param: ToStatusId | int | optional | Catalog.Status.Id the user wants (or someone moved to); marks IsRequestedTarget
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Catalog.Status, Catalog.Tenant, Catalog.StatusStep, Catalog.AppTranslationEntry
interpret: One row per configured next status of FromStatusId for the tenant. IsRequestedTarget = 1 means FromStatusId to ToStatusId IS allowed. If no row has IsRequestedTarget = 1 the transition is NOT configured: the UI/API cannot do it (configuration gap, usually fixed by adding a Catalog.StatusStep row, or the restriction is intentional - confirm with the business). NextStatusId NULL = the status has no outgoing steps at all (dead end). StepsIntoTarget = how many StatusStep rows of the tenant lead into ToStatusId from any status (0 = the tenant cannot reach that status through the workflow at all).
next: wo_status_transition_check, wo_status_timeline, wo_field_changes
*/
SELECT
    fs.Id AS FromStatusId, fn.FieldText AS FromStatusName,
    ss.NextStatusId, nn.FieldText AS NextStatusName,
    CASE WHEN ss.NextStatusId = @ToStatusId THEN 1 ELSE 0 END AS IsRequestedTarget,
    (SELECT COUNT(*) FROM Catalog.StatusStep x WHERE x.TenantId = @TenantId AND x.NextStatusId = @ToStatusId) AS StepsIntoTarget
FROM Catalog.Status fs
LEFT JOIN Catalog.Tenant tn ON tn.Id = @TenantId
LEFT JOIN Catalog.StatusStep ss ON ss.StatusId = fs.Id AND ss.TenantId = @TenantId
LEFT JOIN Catalog.Status nxt ON nxt.Id = ss.NextStatusId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = fs.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = COALESCE(@LanguageId, tn.LanguageId) THEN 0 ELSE 1 END, e.LanguageId) fn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = nxt.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = COALESCE(@LanguageId, tn.LanguageId) THEN 0 ELSE 1 END, e.LanguageId) nn
WHERE fs.Id = @FromStatusId
ORDER BY ss.NextStatusId
