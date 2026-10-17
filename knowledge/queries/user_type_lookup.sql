/*
id: user_type_lookup
title: Sirius user types (tenant + translated name) by id list and/or tenant
purpose: Resolve user-type ids found in code (hard-coded UI permission lists such as `repairDateEditUserTypeIds`, `includes(UserTypeId)`) to their tenant (country) and translated name, and/or list all user types of a tenant (BackOffice, Service partner, Technician ...). Use for "this field/button is not editable for our users", "which user types may do X", "is our service-partner user type in the allowed list". No personal data: only Catalog.SiriusUserType + names.
param: Ids | string | optional max=200 | comma separated Catalog.SiriusUserType.Id list (ids from a code gating list), e.g. 1218,1233
param: TenantId | int | optional | list every user type of this tenant (the ticket's tenant, to compare with the ids in code)
require_any: Ids, TenantId
regions: EU, APAC
tables: Catalog.SiriusUserType, Catalog.Tenant, Catalog.AppTranslationEntry
row_limit: 100
interpret: One row per user type: UserTypeId, TenantId + CountryCode (which country it belongs to), UserTypeName (translated; BackOffice-HQ = head-office staff, Service = service-partner users, Technician = field technicians), Active, InIdList (1 = the id was in the Ids list you passed). Typical use: take the ids a UI screen hard-codes as allowed, run with Ids to learn WHICH tenants/roles they are, then run with the ticket's TenantId and check whether the ticket reporter's user type (e.g. the Service partner type of that country) is in the code list. Only InIdList = 1 rows of the TICKET's tenant are allowed by the list (ids of other tenants do not matter for it). A user type of the ticket's tenant that is NOT in the code list = the restriction is by code (change request / permission-scope), not data.
next: tenant_lookup, user_access_lookup
*/
WITH ids AS (
    SELECT TRY_CAST(LTRIM(RTRIM(s.value)) AS int) AS Id
    FROM STRING_SPLIT(COALESCE(@Ids, ''), ',') s
)
SELECT TOP 100
    ut.Id AS UserTypeId, ut.TenantId, tn.CountryCode, tn.Dsc AS TenantName, nm.FieldText AS UserTypeName,
    ut.Active, ut.Service, CASE WHEN EXISTS (SELECT 1 FROM ids WHERE ids.Id = ut.Id) THEN 1 ELSE 0 END AS InIdList
FROM Catalog.SiriusUserType ut
JOIN Catalog.Tenant tn ON tn.Id = ut.TenantId
OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
             WHERE e.AppTranslationId = ut.AppTranslationId
             ORDER BY CASE WHEN e.LanguageId = tn.LanguageId THEN 0 ELSE 1 END, e.LanguageId) nm
WHERE ut.TenantId = @TenantId
   OR ut.Id IN (SELECT i.Id FROM ids i WHERE i.Id IS NOT NULL)
ORDER BY ut.TenantId, ut.Id
OPTION (RECOMPILE)
