/*
id: wo_detail_material_types
title: Work order detail lines grouped by Catalog.Material.Type (what dbo.PricingWorkOrder sums)
purpose: Column provenance of Operation.WorkOrder.LabourCost / MaterialCost / TransportaionCost / ExtraCost: dbo.PricingWorkOrder sums WorkOrderDetail.Price per Catalog.Material.Type (1 = labour, 2 = spare part, 3 = transport, 4 = extra). This shows every detail line with its Material.Type, code, defect, price and quantity, plus per-type line count and price sum, so "LabourCost is 0 / wrong", "labour not showing", "total does not match" can be explained: no Type-1 line = nothing to sum. Lines with MaterialId NULL (defect-only lines, e.g. a symptom/"Installation" entry) are NOT counted by any cost column.
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
regions: EU, APAC
tables: Operation.WorkOrder, Operation.WorkOrderDetail, Catalog.Material
row_limit: 100
interpret: One row per detail line (a header-only row with NULL line columns = the order has no lines). MaterialType: 1 labour, 2 spare part, 3 transport, 4 extra, NULL = line without material (defect only). TypeLines / TypePriceSum are per MaterialType over the whole order. HeaderLabourCost is Operation.WorkOrder.LabourCost: it must equal the TypePriceSum of Type 1 (no Quantity multiplication for labour). No Type-1 line = the technician/partner never added a labour (the labour list comes from dbo.GetSiriusLabours, which depends on the product's labour materials and price groups for the tenant: check product_labour_setup) - the cost calculation itself is not at fault.
next: product_labour_setup, wo_lines_totals, wo_field_changes
*/
SELECT TOP 100
    wo.Id AS WorkOrderId, wo.LabourCost AS HeaderLabourCost, wo.MaterialCost AS HeaderMaterialCost,
    wo.TransportaionCost AS HeaderTransportCost, wo.ExtraCost AS HeaderExtraCost,
    d.Id AS DetailId, d.DefectId, d.MaterialId, m.Type AS MaterialType, m.Code AS MaterialCode,
    d.Quantity, d.Price,
    COUNT(d.Id) OVER (PARTITION BY m.Type) AS TypeLines,
    SUM(d.Price) OVER (PARTITION BY m.Type) AS TypePriceSum
FROM Operation.WorkOrder wo
LEFT JOIN Operation.WorkOrderDetail d ON d.WorkOrderId = wo.Id AND d.TenantId = @TenantId
LEFT JOIN Catalog.Material m ON m.Id = d.MaterialId
WHERE wo.Id = @WorkOrderId AND wo.TenantId = @TenantId
ORDER BY m.Type, d.Id
OPTION (RECOMPILE)
