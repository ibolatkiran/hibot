/*
id: material_orders
title: Spare-part (material) orders of a work order
purpose: Material orders created for a work order with header status, ERP/PO numbers and every line (material code/name, ordered vs shipped quantity, line status). Use for "spare part not delivered", "part order stuck", "wrong part ordered", "PO not created in SAP".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Operation.MaterialOrder, Operation.MaterialOrderDetail, Catalog.Tenant, Catalog.MaterialOrderStatus, Catalog.MaterialOrderDetailStatus, Catalog.Material, Catalog.AppTranslationEntry
pii: ItemNote:free_text_body, Description:free_text_body
interpret: One row per order line (header columns repeat). MaterialOrderStatus is the header state; DetailStatus (Catalog.MaterialOrderDetailStatus.Status) is per line. Qty > ShipedQty with an old CreateDate = not delivered yet. Empty ErpDocumentNr/PONumber means the order was not sent to ERP (see sap_rfc_rest_calls / wo_erp_sap). MaterialIdReplaced set = the part was substituted. FreeOfCharge = warranty part. No rows = no material order exists for this work order.
next: sap_rfc_rest_calls, wo_lines_totals, wo_erp_sap
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT TOP 500
    mo.Id AS MaterialOrderId, mo.MaterialOrderStatusId, mosn.FieldText AS MaterialOrderStatus, mo.CreateDate,
    mo.PONumber, mo.ErpDocumentNr, mo.ExternalId, mo.FreeOfCharge, mo.SapShipToCode,
    mo.BillToServiceShopId, mo.ShipToToServiceShopId, mo.LastUpdateEmployeeId, mo.Dsc AS Description,
    d.Id AS DetailId, d.ItemNr, d.MaterialId, m.Code AS MaterialCode, mn.FieldText AS MaterialName, d.MaterialIdReplaced,
    d.Qty, d.ShipedQty, d.MaterialOrderDetailStatusId, ds.Status AS DetailStatus, d.ItemNote
FROM Operation.MaterialOrder mo
CROSS JOIN lang
LEFT JOIN Operation.MaterialOrderDetail d ON d.MaterialOrderId = mo.Id
LEFT JOIN Catalog.MaterialOrderStatus mos ON mos.Id = mo.MaterialOrderStatusId
LEFT JOIN Catalog.MaterialOrderDetailStatus ds ON ds.StatusId = d.MaterialOrderDetailStatusId
LEFT JOIN Catalog.Material m ON m.Id = d.MaterialId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = mos.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) mosn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = m.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) mn
WHERE mo.WorkOrderId = @WorkOrderId AND mo.TenantId = @TenantId
ORDER BY mo.Id, d.ItemNr
