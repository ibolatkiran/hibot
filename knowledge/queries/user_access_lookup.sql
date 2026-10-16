/*
id: user_access_lookup
title: User access lookup (no credentials)
purpose: For a user code: the Sirius user record (tenant, active flag, user type), tenant memberships, the linked employee records (role, service shop, active) and the tenants each employee may access. Use for "user cannot log in / sees no data / wrong country / missing menu", "user inactive", "no access to tenant X". Never returns passwords or tokens.
param: UserCode | string | required max=50 | Catalog.SiriusUser.UserCode (login / user code)
param: TenantId | int | optional | restrict to one tenant
regions: EU, APAC
tables: Catalog.SiriusUser, Catalog.SiriusUserTenant, Catalog.Employee, Catalog.Role, Catalog.EmployeeTenantAuthority
pii_sources: Dsc, UserName
row_limit: 100
interpret: Kind SIRIUS_USER = login record (Active = IsActive, Detail1 = tenant code, Detail2 = user type id); none = the user code does not exist. USER_TENANT = tenants the user is a member of (missing tenant = no access to that country). EMPLOYEE = the person record per tenant (Code = technician code, Detail1 = role, Detail2 = service shop id; Active = 0 means deactivated, no row = user not provisioned as an employee). EMPLOYEE_TENANT_AUTH = extra tenants the employee is authorised for. Role names come from Catalog.Role.
next: tenant_lookup, wo_header
*/
WITH u AS (
    SELECT su.Id, su.UserCode, su.TenantId, su.TenantCode, su.IsActive, su.UserType
    FROM Catalog.SiriusUser su
    WHERE su.UserCode = @UserCode AND (@TenantId IS NULL OR su.TenantId = @TenantId)
),
emp AS (
    SELECT e.Id, e.TechnicianCode, e.TenantId, e.Active, e.RoleId, e.ServiceShopId
    FROM Catalog.Employee e
    WHERE (e.SiriusUserId IN (SELECT u1.Id FROM u u1) OR e.UserName = @UserCode)
      AND (@TenantId IS NULL OR e.TenantId = @TenantId)
)
SELECT 'SIRIUS_USER' AS Kind, u.Id AS RefId, u.UserCode AS Code, u.TenantId, CAST(u.IsActive AS int) AS Active,
       u.TenantCode AS Detail1, CAST(u.UserType AS nvarchar(50)) AS Detail2
FROM u
UNION ALL
SELECT 'USER_TENANT', sut.Id, sut.UserCode, sut.TenantId, NULL, NULL, NULL
FROM Catalog.SiriusUserTenant sut
WHERE sut.UserCode = @UserCode AND (@TenantId IS NULL OR sut.TenantId = @TenantId)
UNION ALL
SELECT 'EMPLOYEE', x.Id, x.TechnicianCode, x.TenantId, x.Active, r.Dsc, 'shop ' + CAST(x.ServiceShopId AS varchar(20))
FROM emp x
LEFT JOIN Catalog.Role r ON r.Id = x.RoleId
UNION ALL
SELECT 'EMPLOYEE_TENANT_AUTH', a.Id, CAST(a.EmployeeId AS nvarchar(50)), a.TenantId, NULL, NULL, NULL
FROM Catalog.EmployeeTenantAuthority a
WHERE a.EmployeeId IN (SELECT x2.Id FROM emp x2)
  AND (@TenantId IS NULL OR a.TenantId = @TenantId)
ORDER BY Kind, RefId
