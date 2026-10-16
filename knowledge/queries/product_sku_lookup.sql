/*
id: product_sku_lookup
title: Product / SKU existence for a tenant
purpose: Does a SKU, marketing code or EAN exist in Catalog.Product and is it assigned to the tenant (Catalog.ProductTenant)? Also shows description, brand, BOM line count and whether product-change notifications are still pending. Use for "product/SKU not found in country X", "cannot create work order for model", "SKU missing after SAP sync".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: Sku | string | optional max=30 | Catalog.Product.SKU (also tried left-padded with zeros to 18 chars and as MarketingCode)
param: MarketingCode | string | optional max=50 | Catalog.Product.MarketingCode
param: EanCode | string | optional max=13 | Catalog.Product.EANCode
require_any: Sku, MarketingCode, EanCode
regions: EU, APAC
tables: Catalog.Product, Catalog.Tenant, Catalog.ProductTenant, Catalog.Brand, Catalog.ProductDesc, Catalog.ProductMaterial, Log.ProductChanges, Gateway.ProductChangeNotification
row_limit: 50
interpret: NO ROWS = the SKU/marketing code/EAN does not exist in Catalog.Product at all (not synced from the product master; check sap/product sync jobs with job_health and the exact spelling/padding). A row with AssignedToTenant = 0 = the product exists but is NOT assigned to this tenant (country): that is the usual cause of "missing product in country X" - it needs a Catalog.ProductTenant row. BomLines = 0 means no spare-parts list. LastProductChange shows the last master-data change (Log.ProductChanges); PendingChangeNotifications > 0 means change notifications are still queued (Gateway.ProductChangeNotification). TenantName/CountryCode confirm the tenant is the intended country.
next: tenant_lookup, job_health, outbound_queue_backlog
*/
SELECT TOP 50
    p.Id AS ProductId, p.SKU, p.MarketingCode, p.EANCode, p.ExternalId, p.CreateDate, p.BrandId, b.Dsc AS BrandName,
    p.HierarchyId, pd.ProductDescription,
    tn.Id AS TenantId, tn.Dsc AS TenantName, tn.CountryCode,
    CASE WHEN pt.Id IS NULL THEN 0 ELSE 1 END AS AssignedToTenant,
    (SELECT COUNT(*) FROM Catalog.ProductMaterial pm WHERE pm.ProductId = p.Id) AS BomLines,
    (SELECT MAX(pc.ChangeDate) FROM Log.ProductChanges pc WHERE pc.ProductId = p.Id) AS LastProductChange,
    (SELECT COUNT(*) FROM Gateway.ProductChangeNotification n WHERE n.ProductId = p.Id) AS PendingChangeNotifications
FROM Catalog.Product p
CROSS JOIN Catalog.Tenant tn
LEFT JOIN Catalog.ProductTenant pt ON pt.ProductId = p.Id AND pt.TenantId = tn.Id
LEFT JOIN Catalog.Brand b ON b.Id = p.BrandId
OUTER APPLY (SELECT TOP 1 d.[Desc] AS ProductDescription FROM Catalog.ProductDesc d
              WHERE d.ProductId = p.Id
              ORDER BY CASE WHEN d.LanguageId = tn.LanguageId THEN 0 ELSE 1 END, d.LanguageId) pd
WHERE tn.Id = @TenantId
  AND ((@Sku IS NOT NULL AND (p.SKU = CAST(@Sku AS varchar(18))
                              OR p.SKU = RIGHT('000000000000000000' + CAST(@Sku AS varchar(18)), 18)
                              OR p.MarketingCode = CAST(@Sku AS varchar(50))))
    OR (@MarketingCode IS NOT NULL AND p.MarketingCode = CAST(@MarketingCode AS varchar(50)))
    OR (@EanCode IS NOT NULL AND p.EANCode = CAST(@EanCode AS varchar(13))))
ORDER BY p.Id
