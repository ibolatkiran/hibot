/*
id: wo_header
title: Work order header
purpose: First look at a work order: status (with name), case, product/SKU, service shop, technician, warranty type, reject reason, dates and cost totals. Use for any ticket that quotes a work order id, ExternalId or ExternalWorkOrderId (wrong status, missing technician, wrong warranty, rejected order, appointment questions).
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | optional | Operation.WorkOrder.Id
param: ExternalId | string | optional max=100 | Operation.WorkOrder.ExternalId (id in the source system, e.g. CRM / SAP)
param: ExternalWorkOrderId | string | optional max=12 | Operation.WorkOrder.ExternalWorkOrderId
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
require_any: WorkOrderId, ExternalId, ExternalWorkOrderId
regions: EU, APAC
tables: Operation.WorkOrder, Operation.WorkCase, Catalog.Tenant, Catalog.Status, Catalog.AppTranslationEntry, Catalog.ServiceShop, Catalog.Employee, Catalog.WarrantyType, Catalog.RejectReason, Catalog.Product
pii: ContactName:name, ContactPhone:phone, TechnicianName:name, StatusNote:free_text_body, CallCenterNote:free_text_body, RejectDescription:free_text_body
pii_sources: Dsc
row_limit: 20
interpret: StatusName/IsOpen/IsComplete is the CURRENT status. Compare StatusId with CaseStatusId (case vs order). NULL ServiceShopName/TechnicianName means the FK points to a missing/other-tenant row. NULL StatusName means the status has no translation (check LanguageId). ErpDocumentId set means an ERP document exists (see wo_erp_sap). Dates are UTC; Catalog.Tenant.ShowUTCHour is the display offset.
next: wo_status_timeline, wo_field_changes, wo_status_transition_check, workcase_tree, wo_erp_sap
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT TOP 20
    wo.Id AS WorkOrderId, wo.WorkCaseId, wo.TenantId, wo.ExternalId, wo.ExternalWorkOrderId,
    wo.ExternalServicePaparNr, wo.PolicyNr, wo.SalesDocumentSapNo,
    wo.StatusId, stn.FieldText AS StatusName, st.IsOpen, st.IsComplete, st.Active AS StatusActive,
    wo.StatusNote,
    wo.OpenDate, wo.OperationDate, wo.AppointmentDate, wo.AppointmentEndDate, wo.FirstAppointmentDate,
    wo.OperationStartDate, wo.VerificationDate, wo.WarrantyDate,
    wo.ServiceShopId, ss.Dsc AS ServiceShopName, ss.SourceServiceShopCode, ss.Active AS ServiceShopActive,
    wo.EmployeeId, emp.TechnicianCode, emp.Dsc AS TechnicianName, emp.Active AS TechnicianActive,
    wo.WarrantyTypeId, wtn.FieldText AS WarrantyTypeName,
    wo.RejectReasonId, rrn.FieldText AS RejectReasonName, wo.RejectDescription,
    wo.PriorityId, wo.ChannelId, wo.TaskTypeId, wo.Reopen, wo.NoInvoice, wo.ErpDocumentId,
    wo.MaterialCost, wo.LabourCost, wo.TransportaionCost, wo.ExtraCost, wo.TotalCost,
    wo.ContactName, wo.ContactPhone, wo.CallCenterNote,
    wc.StatusId AS CaseStatusId, cstn.FieldText AS CaseStatusName, wc.OpenDate AS CaseOpenDate,
    wc.CloseDate AS CaseCloseDate, wc.ReOpenDate AS CaseReOpenDate, wc.ExternalId AS CaseExternalId,
    wc.ProductId, p.SKU, p.MarketingCode, wc.AssetId, wc.ServiceErrorCode, wc.CustomerErrorCode
FROM Operation.WorkOrder wo
CROSS JOIN lang
LEFT JOIN Operation.WorkCase wc ON wc.Id = wo.WorkCaseId
LEFT JOIN Catalog.Status st ON st.Id = wo.StatusId
LEFT JOIN Catalog.Status cst ON cst.Id = wc.StatusId
LEFT JOIN Catalog.ServiceShop ss ON ss.Id = wo.ServiceShopId
LEFT JOIN Catalog.Employee emp ON emp.Id = wo.EmployeeId
LEFT JOIN Catalog.WarrantyType wt ON wt.Id = wo.WarrantyTypeId
LEFT JOIN Catalog.RejectReason rr ON rr.Id = wo.RejectReasonId
LEFT JOIN Catalog.Product p ON p.Id = wc.ProductId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = st.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) stn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = cst.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) cstn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = wt.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) wtn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = rr.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) rrn
WHERE wo.TenantId = @TenantId
  AND (@WorkOrderId IS NULL OR wo.Id = @WorkOrderId)
  AND (@ExternalId IS NULL OR wo.ExternalId = CAST(@ExternalId AS varchar(100)))
  AND (@ExternalWorkOrderId IS NULL OR wo.ExternalWorkOrderId = CAST(@ExternalWorkOrderId AS varchar(12)))
ORDER BY wo.Id
OPTION (RECOMPILE)
