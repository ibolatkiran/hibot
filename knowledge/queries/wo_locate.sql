/*
id: wo_locate
title: Locate a work order across tenants (find its real TenantId)
purpose: ALWAYS run this FIRST when a ticket gives a work order / job number. The number in a ticket may be Operation.WorkOrder.Id, ExternalId or ExternalWorkOrderId, and the ticket's country does NOT reliably identify the tenant (one country can have several tenants, e.g. CountryCode 'tr' also matches 'Central (Arçelik Hitachi)'). Returns the real TenantId to pass to every other query.
param: Number | string | required max=30 | the number from the ticket (matched against Id, ExternalId, ExternalWorkOrderId)
regions: EU, APAC
tables: Operation.WorkOrder, Catalog.Tenant
row_limit: 20
interpret: MatchedOn tells which column matched. Use WorkOrderId + TenantId from here in wo_header, wo_status_timeline etc. 0 rows = the number is not a work order in this region (try the other region, or it may be a WorkCase / SAP document / SKU number). Several rows = ambiguous external ids; pick by tenant/country from the ticket.
next: wo_header, wo_status_timeline, wo_field_changes, wo_erp_sap
*/
SELECT TOP 20
    w.Id AS WorkOrderId, w.TenantId, t.Dsc AS TenantName, t.CountryCode, w.WorkCaseId,
    w.ExternalId, w.ExternalWorkOrderId, w.StatusId, w.OpenDate, w.VerificationDate,
    CASE WHEN CAST(w.Id AS varchar(30)) = @Number THEN 'Id'
         WHEN w.ExternalId = @Number THEN 'ExternalId'
         ELSE 'ExternalWorkOrderId' END AS MatchedOn
FROM Operation.WorkOrder w
LEFT JOIN Catalog.Tenant t ON t.Id = w.TenantId
WHERE w.Id = TRY_CAST(@Number AS int)
   OR w.ExternalId = @Number
   OR w.ExternalWorkOrderId = @Number
ORDER BY CASE WHEN CAST(w.Id AS varchar(30)) = @Number THEN 0 ELSE 1 END, w.Id DESC
OPTION (RECOMPILE)
