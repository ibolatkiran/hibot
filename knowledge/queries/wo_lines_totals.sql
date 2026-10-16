/*
id: wo_lines_totals
title: Work order lines (parts / labour / defects) and totals
purpose: Detail lines of a work order (Operation.WorkOrderDetail with defect and material names) next to the header cost fields and the sum of the lines. Use for "wrong total / price", "missing spare part line", "VAT or currency wrong", "quantity wrong", "line disappeared".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Operation.WorkOrder, Operation.WorkOrderDetail, Catalog.Tenant, Catalog.Defect, Catalog.Material, Catalog.AppTranslationEntry
interpret: One row per line (a header-only row with NULL line columns means the order has no detail lines). A line has a DefectId (labour/defect) and/or a MaterialId (spare part with MaterialCode). LineTotal = Quantity*Price, LineTotalDiscounted uses DiscountPrice when set. Compare SumLineTotal / SumLineTotalDiscounted with the header MaterialCost+LabourCost+TransportaionCost+ExtraCost = TotalCost: a difference means totals were not recalculated (VAT is NOT included here - VatRate is per line). Header MaterialCost with no material lines = lines deleted after the totals were set. Discounts: see wo_discount_log (EU).
next: wo_discount_log, wo_erp_sap, wo_field_changes
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT TOP 500
    wo.Id AS WorkOrderId, wo.MaterialCost, wo.LabourCost, wo.TransportaionCost, wo.ExtraCost, wo.TotalCost,
    d.Id AS DetailId, d.DefectId, dn.FieldText AS DefectName, d.MaterialId, m.Code AS MaterialCode, mn.FieldText AS MaterialName,
    d.Quantity, d.Price, d.DiscountPrice, d.VatRate, d.VatRateId, d.CurrencyId, d.PriceListEntryId,
    d.Quantity * d.Price AS LineTotal,
    d.Quantity * COALESCE(d.DiscountPrice, d.Price) AS LineTotalDiscounted,
    SUM(d.Quantity * d.Price) OVER () AS SumLineTotal,
    SUM(d.Quantity * COALESCE(d.DiscountPrice, d.Price)) OVER () AS SumLineTotalDiscounted,
    COUNT(d.Id) OVER () AS LineCount
FROM Operation.WorkOrder wo
CROSS JOIN lang
LEFT JOIN Operation.WorkOrderDetail d ON d.WorkOrderId = wo.Id AND d.TenantId = @TenantId
LEFT JOIN Catalog.Defect df ON df.Id = d.DefectId
LEFT JOIN Catalog.Material m ON m.Id = d.MaterialId
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = df.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) dn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = m.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) mn
WHERE wo.Id = @WorkOrderId AND wo.TenantId = @TenantId
ORDER BY d.Id
