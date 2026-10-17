---
skill: access_request
problem_types: access_request
keywords: \baccess\b|\blogin\b|log in|password|okta|authori[sz]|yetki|\brole\b|new user|account
required_evidence:
- id: user_lookup | when: user_code | description: Look the user code up (Catalog.SiriusUser, employee, role, tenant authority) | satisfied_by: template:user_access_lookup ; sql:SiriusUser|EmployeeTenantAuthority
---
# Access, role, login, new user

## Steps
1. Recognise the request type from the text: new user, role/authorization change, locked account/password, menu not visible. Access requests are recognisable without DB evidence; classify `access` / `grant_access` even if no user code is given (state that the code is missing).
2. If a user code / technician code is present: `user_access_lookup` → `Catalog.SiriusUser`, `Employee`, `Role`, `EmployeeTenantAuthority` (never read password columns).
3. "Menu/screen not visible": `Catalog.Menu`, `MenuAuthorization` for the user type/role; frontend gating → [feature_and_permission_gating.md](feature_and_permission_gating.md).
4. Login problems: Okta is the identity provider; `Log.LoginLog` only for the configured tenants. A single-session rule logs users out when they sign in elsewhere ("Başka bir cihazdan giriş yaptığınız…").
5. Manusoft / Contact / Salesforce users are outside the Sirius DB; route them to those teams.
