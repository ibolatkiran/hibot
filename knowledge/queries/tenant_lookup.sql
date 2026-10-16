/*
id: tenant_lookup
title: Tenant (country) lookup
purpose: Find a tenant by id, country code or name part and see its language, UTC offsets, company code and basic configuration counts. Use first when a ticket says "country X" / "market X" and you need the TenantId, or to check tenant settings (language, UTC offset) behind wrong names or times.
param: TenantId | int | optional | Catalog.Tenant.Id
param: CountryCode | string | optional max=10 | Catalog.Tenant.CountryCode (e.g. TR, DE)
param: NameLike | string | optional like max=100 | part of the tenant name (Catalog.Tenant.Dsc)
require_any: TenantId, CountryCode, NameLike
regions: EU, APAC
tables: Catalog.Tenant, Catalog.AppLanguage, Catalog.ProductTenant, Catalog.StatusStep
row_limit: 50
interpret: TenantId is the value to pass to every other query. Active = 0 means the tenant is disabled. LanguageId/LanguageCode drive status names (see LanguageId parameters). ShowUTCHour = display offset used by the UI (the history function adds it), WriteUTCHour = offset applied when writing. AssignedProducts = rows in Catalog.ProductTenant (0 = no product assigned to this country); StatusStepRows = configured status workflow rows (0 = workflow not configured). Several tenants per country are possible - pick by Dsc/CompanyCode.
next: product_sku_lookup, wo_status_transition_check, user_access_lookup
*/
SELECT TOP 50
    t.Id AS TenantId, t.Dsc AS TenantName, t.CountryCode, t.Active, t.LanguageId, l.Code AS LanguageCode,
    t.ShowUTCHour, t.WriteUTCHour, t.OrganizationCode, t.CompanyCode, t.UsePriceGroup,
    (SELECT COUNT(*) FROM Catalog.ProductTenant pt WHERE pt.TenantId = t.Id) AS AssignedProducts,
    (SELECT COUNT(*) FROM Catalog.StatusStep ss WHERE ss.TenantId = t.Id) AS StatusStepRows
FROM Catalog.Tenant t
LEFT JOIN Catalog.AppLanguage l ON l.Id = t.LanguageId
WHERE (@TenantId IS NULL OR t.Id = @TenantId)
  AND (@CountryCode IS NULL OR t.CountryCode = @CountryCode)
  AND (@NameLike IS NULL OR t.Dsc LIKE '%' + @NameLike + '%')
ORDER BY t.Id
OPTION (RECOMPILE)
