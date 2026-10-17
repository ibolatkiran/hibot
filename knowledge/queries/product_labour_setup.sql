/*
id: product_labour_setup
title: Product labour / price-group setup for a tenant (by SKU list) and sibling coverage in the same hierarchy
purpose: Why does a work order show no labour lines / LabourCost 0 / "labour charge not showing" for a SKU in a country? Mirrors the join chain of dbo.GetSiriusLabours (labour list of the technician/partner apps): Catalog.ProductMaterial (labour materials, Material.Type = 1, tenant scoped) -> Catalog.ProductMaterialPriceGroup (tenant scoped price group) -> Catalog.PriceListEntry. Per SKU: product id + hierarchy, assignment to the tenant, number of labour materials and of active labour price groups for the tenant (and in ANY tenant), and how many sibling products of the same HierarchyId are assigned to the tenant and how many of them have labour price groups. Use for "Missing labour in Sirius <country>", "labour cost not showing", "no labour for model X", "add labour for new model/category".
param: TenantId | int | required | Catalog.Tenant.Id (the WORK ORDER's tenant)
param: Skus | string | required max=200 | comma separated Catalog.Product.SKU list (as in the ticket, e.g. 7440000008,7440000011)
regions: EU, APAC
tables: Catalog.Product, Catalog.ProductTenant, Catalog.ProductMaterial, Catalog.Material, Catalog.ProductMaterialPriceGroup
row_limit: 20
interpret: One row per SKU. LabourMaterials = Type-1 materials of the tenant linked to the product (Catalog.ProductMaterial); LabourPriceGroups = active ProductMaterialPriceGroup rows of the tenant for the product; the *AnyTenant columns show the same without the tenant filter. Both 0 for the tenant = the product has NO labour setup there, so GetSiriusLabours returns an empty labour list, no Type-1 line can be added to the work order and dbo.PricingWorkOrder (LabourCost = SUM(Price) of Type-1 detail lines) correctly gives 0: this is MASTER DATA (resolution master_data_change: define labour materials + price groups for the product/hierarchy in the tenant), not a code defect. HierarchyProductsInTenant / HierarchyProductsWithLabour show whether the whole category (HierarchyId) lacks labour setup in the tenant (0 of N = category never set up, e.g. a new launch) or only this SKU is missing (then copy the setup from a sibling). AssignedToTenant = 0 means the SKU is not assigned to the tenant at all (see product_sku_lookup). LabourMaterials > 0 but LabourPriceGroups = 0 (or no PriceListEntry for the shop/warranty payment group) = prices missing instead of labour codes.
next: wo_detail_material_types, product_sku_lookup, tenant_lookup
*/
WITH sk AS (
    SELECT DISTINCT CAST(LTRIM(RTRIM(s.value)) AS varchar(18)) AS Sku, CAST(RIGHT('000000000000000000' + LTRIM(RTRIM(s.value)), 18) AS varchar(18)) AS PaddedSku
    FROM STRING_SPLIT(CAST(@Skus AS varchar(200)), ',') s WHERE LTRIM(RTRIM(s.value)) <> ''
)
SELECT TOP 20
    p.Id AS ProductId, p.SKU, p.HierarchyId,
    CASE WHEN EXISTS (SELECT 1 FROM Catalog.ProductTenant pt WHERE pt.ProductId = p.Id AND pt.TenantId = @TenantId) THEN 1 ELSE 0 END AS AssignedToTenant,
    (SELECT COUNT(*) FROM Catalog.ProductMaterial pm JOIN Catalog.Material m ON m.Id = pm.MaterialId
      WHERE pm.ProductId = p.Id AND m.Type = 1 AND m.TenantId = @TenantId) AS LabourMaterials,
    (SELECT COUNT(*) FROM Catalog.ProductMaterialPriceGroup g
      WHERE g.ProductId = p.Id AND g.TenantId = @TenantId AND g.Active = 1) AS LabourPriceGroups,
    (SELECT COUNT(*) FROM Catalog.ProductMaterial pm2 JOIN Catalog.Material m2 ON m2.Id = pm2.MaterialId
      WHERE pm2.ProductId = p.Id AND m2.Type = 1) AS LabourMaterialsAnyTenant,
    (SELECT COUNT(*) FROM Catalog.ProductMaterialPriceGroup g2 WHERE g2.ProductId = p.Id AND g2.TenantId IS NOT NULL) AS PriceGroupsAnyTenant,
    (SELECT COUNT(*) FROM Catalog.Product sp
       JOIN Catalog.ProductTenant spt ON spt.ProductId = sp.Id AND spt.TenantId = @TenantId
      WHERE sp.HierarchyId = p.HierarchyId) AS HierarchyProductsInTenant,
    (SELECT COUNT(*) FROM Catalog.Product sp2
       JOIN Catalog.ProductTenant spt2 ON spt2.ProductId = sp2.Id AND spt2.TenantId = @TenantId
      WHERE sp2.HierarchyId = p.HierarchyId
        AND EXISTS (SELECT 1 FROM Catalog.ProductMaterialPriceGroup g3
                     WHERE g3.ProductId = sp2.Id AND g3.TenantId = @TenantId AND g3.Active = 1)) AS HierarchyProductsWithLabour
FROM sk
JOIN Catalog.Product p ON p.SKU = sk.Sku OR p.SKU = sk.PaddedSku
ORDER BY p.SKU
OPTION (RECOMPILE)
