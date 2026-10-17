# Tenant and region resolution

## Facts
- Every Sirius table is tenant-scoped (`TenantId`). There are **no FK constraints**; join on Id **and** TenantId.
- The ticket's country / "Country Code" field is the **reporter's** country, not necessarily the work order's tenant. One country can match several tenants (`CountryCode 'tr'` also matches "Central (Arçelik Hitachi)").
- EU DB (gfsm-europe) is a superset; APAC (ahsiriusdbsrv) has the same core schema. APAC is not configured yet.

## Steps
1. For every work-order-like number in the ticket: `wo_locate(Number)` → WorkOrderId, TenantId, TenantName, MatchedOn (Id / ExternalId / ExternalWorkOrderId).
2. 0 rows in EU → try APAC (if configured) → else the number may be a WorkCase id, SAP document, SKU, phone number; say so.
3. No work order in the ticket → tenant from explicit tenant/country + `tenant_lookup`; if several tenants match, list them and pick by company code / ticket context, stating the ambiguity.
4. Use the resolved TenantId in every later query.

## Worked example
SM-2122480: ticket country TR, `tenant_lookup(TR)` returned tenant 86 "Central (Arçelik Hitachi)", WO not found there. `wo_locate(22197255)` → tenant 70 Egypt. (FINDINGS §11)
