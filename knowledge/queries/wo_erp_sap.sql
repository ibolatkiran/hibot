/*
id: wo_erp_sap
title: ERP / SAP documents of a work order
purpose: All ERP posting records of a work order - generic ERP details (Operation.WorkOrderErpDetails), FI documents (WorkOrderErpFi), MM documents (WorkOrderErpMm) - with the ERP package header (Operation.ErpDocument: status, error, send date). Use for "invoice not created in SAP", "SAP error", "document stuck / not sent", "FI/MM posting failed".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Operation.WorkOrderErpDetails, Operation.WorkOrderErpFi, Operation.WorkOrderErpMm, Operation.ErpDocument, Catalog.Tenant, Catalog.ErpDocumentStatus, Catalog.AppTranslationEntry
pii: ErrorDescription:free_text_body, ErpDocumentError:free_text_body
interpret: Source = ErpDetails / ErpFi / ErpMm. A non-empty ErrorDescription is the SAP/middleware error text (read it first). StatusId is translated via Catalog.ErpDocumentStatus (assumed shared status domain - if StatusName is NULL read the raw id; in code 2 = sent, 6/7 = special/failed states). ErpDocumentStatusId/SendDate/ErpDocumentError describe the package (one ErpDocument can bundle several orders). No row at all = the order never reached the ERP queue (check status flow and wo_header.ErpDocumentId). DocumentNr empty + StatusId not final = still pending. For the raw SAP/REST traffic use sap_rfc_rest_calls.
next: sap_rfc_rest_calls, db_errors_for_wo, wo_lines_totals, outbound_queue_backlog
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id),
erp AS (
    SELECT 'ErpDetails' AS Source, d.Id, d.ErpDocumentId, d.Type AS DocType, d.DocumentNr AS DocumentNr,
           d.StatusId, d.ErrorDescription, d.CreateDate
    FROM Operation.WorkOrderErpDetails d
    WHERE d.WorkOrderId = @WorkOrderId AND d.TenantId = @TenantId
    UNION ALL
    SELECT 'ErpFi', f.Id, NULL, NULL, f.FiDocumentNr, f.StatusId, f.ErrorDescription, f.CreateDate
    FROM Operation.WorkOrderErpFi f
    WHERE f.WorkOrderId = @WorkOrderId AND f.TenantId = @TenantId
    UNION ALL
    SELECT 'ErpMm', m.Id, NULL, NULL, m.MmDocumentNr, m.StatusId, m.ErrorDescription, m.CreateDate
    FROM Operation.WorkOrderErpMm m
    WHERE m.WorkOrderId = @WorkOrderId AND m.TenantId = @TenantId
)
SELECT TOP 200
    e.Source, e.Id, e.CreateDate, e.DocType, e.DocumentNr, e.StatusId, esn.FieldText AS StatusName, e.ErrorDescription,
    e.ErpDocumentId, ed.StatusId AS ErpDocumentStatusId, ed.DocumentType AS ErpDocumentType,
    ed.DocumentNr AS ErpDocumentNr, ed.InvoiceNr, ed.SendDate, ed.UpdateDate AS ErpDocumentUpdated,
    ed.ErrorDescription AS ErpDocumentError, ed.TotalCost AS ErpDocumentTotalCost
FROM erp e
CROSS JOIN lang
LEFT JOIN Operation.ErpDocument ed ON ed.Id = e.ErpDocumentId AND ed.TenantId = @TenantId
LEFT JOIN Catalog.ErpDocumentStatus es ON es.Id = e.StatusId
OUTER APPLY (SELECT TOP 1 x.FieldText FROM Catalog.AppTranslationEntry x
              WHERE x.AppTranslationId = es.AppTranslationId
              ORDER BY CASE WHEN x.LanguageId = lang.Id THEN 0 ELSE 1 END, x.LanguageId) esn
ORDER BY e.CreateDate, e.Source, e.Id
