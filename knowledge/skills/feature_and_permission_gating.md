---
skill: feature_and_permission_gating
detector: capability
keywords: (cannot|can't|unable|not able).{0,30}(edit|change|update|modify|see|find)|not editable|read.?only|\bbutton\b|permission|authori[sz]|yetki|please (update|change|enable|allow)
required_evidence:
- id: caller_screen_read | description: Read the CALLING SCREEN code around the gate (button / field render condition, user-type / role / tenant lists, env flags, TODO comments) | satisfied_by: read_file:\.(js|jsx|tsx|ts)$
- id: user_type_lookup | description: Resolve the ids found in code (user types / roles) to meaning and tenant: Catalog.SiriusUserType | satisfied_by: template:user_type_lookup ; sql:SiriusUserType
---
# "Cannot do X" / field not editable / please update a field: is the feature there, and who may use it?

Many "requests" are not bugs. The feature often **already exists** but is hidden for this user type, tenant or environment, often by a **hard-coded list in the frontend**. Find the gate before calling it a change request.

## Steps
1. **Find the capability in the backend**: `code_search` / `graph_search_text` for the action (e.g. `RepairDate`, `Cancel`, `Reject`, `ChangeStatus`) → endpoint + SP. Read the SP: what does it change, any rules (status, date checks)?
2. **Find every screen that calls it**: `graph_trace_endpoint(...).callers_by_app` (portal = HeadOffice, crm = CallCenter, partner = ServiceShopCallCenter, technician). The same feature may be gated differently in each app.
3. **Read the gate in each calling screen** (`read_file` around the call and around the button/field render). Look for:
   - user type / role id lists (`UserTypeId`, `RoleId`, `.includes(`), `SYSTEM_CONFIG`, `REACT_APP_*` env flags, tenant checks, `tenantFeatureVisibility`
   - status conditions (field only editable in certain statuses)
   - `TODO` comments ("yeni ülkenin user type Id'leri eklenecek")
4. **Resolve ids found in code to meaning**: user types → `Catalog.SiriusUserType` (+ name via `Catalog.AppTranslationEntry`), tenants → `Catalog.Tenant`, statuses → `Catalog.Status`. Which tenant does each listed id belong to? Is the ticket's user type in the list?
5. **Backend checks too**: does the controller/manager/SP also restrict (role, tenant, status)? `Catalog.MenuAuthorization` / `EmployeeTenantAuthority` for menu-level permissions.
6. Conclude with options, from cheapest:
   (a) who can already do it today (workaround, no code);
   (b) extend the gate (add the user type/tenant; code or config change, needs a business decision);
   (c) a structural fix (move hard-coded lists to DB/tenant config).
   Classification: `change_request` / `route_to_backlog`, or `config` / `config_change` / `code_change` for permission-scope fixes.

## Worked example: SM-2172385 (Morocco, "Update the repair date field")
- `POST api/Technician/SetRepairDate` (TechnicianController.cs:6413) → `dbo.SetRepairDate` updates `Operation.WorkOrder.VerificationDate` and audits to `Log.WorkOrderDateEditAction`.
- Callers: crm/partner `src/views/WorkOrder/Summary.js`, portal `src/views/Operation/JobControl`.
- Partner `Summary.js:73-90`: the edit button is gated by `SYSTEM_CONFIG.Live.repairDateEditUserTypeIds = [1218, 1233]`, with the comments "EG: GFSM-7863" and "TODO: yeni ülkenin user type Id'leri eklenecek".
- `Catalog.SiriusUserType`: 1218 = T70 Egypt BackOffice-HQ, 1233 = T76 Morocco BackOffice-HQ. Morocco Service partner (920) is not in the list.
- Options: (a) Morocco BackOffice-HQ fixes dates today; (b) add 920 if the business approves; (c) move the list to tenant config. (FINDINGS §17)

## Also check how the value is set today (request = "correct / update field X")
7. `column_writers(table, column)` for the field behind the request: is it set AUTOMATICALLY by a status step (e.g. in `WorkOrder.UpdateStatus`, payment control, send-to-center), so that the real answer is "the date comes from action Y; the manual edit is only an override"? Count the audit rows of the manual feature for the tenant (e.g. `Log.WorkOrderDateEditAction`: 0 rows = the override is never used there) and compare one work order's `wo_field_changes` around the status change. Put this in the report next to the gating finding.
- SM-2172385: `VerificationDate` is auto-set with `GETUTCDATE()` by the status-step SPs (`WorkOrder.UpdateStatus.sql:42-44`), so the closing action determines the "repair date" unless the manual override is used (FINDINGS §20).
