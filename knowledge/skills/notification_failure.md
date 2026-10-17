# SMS / e-mail / NPS / push not sent

## Steps
1. Per work order: `notifications_for_wo` → `Operation.SmsEmailSendingData` and `Communication.SmsEmailSendingData` rows (SendType, SendingStatus → `Catalog.SmsEmailSendingStatus`, QDate, SentDate, TemplateId). Interpret:
   - no row → the message was never queued: check the trigger (status-change notification rules, `PrepareStatusNotifications`, tenant templates);
   - queued but not sent → job/provider problem (`SMSEmailJob`, `job_health`, provider errors; e.g. SM-2011812 SMS API 401);
   - sent → delivery is outside Sirius.
2. Tenant-wide ("since date X nothing is sent"): time window `job_health` + `outbound_queue_backlog` + `db_errors_recent`; compare sending counts before and after the date.
3. Templates and config: `SmsEmailTemplate` per tenant and status; NPS: `Operation.NpsData*`, `Communication.FlsNpsQueue`.
4. Conclude: missing template/rule (`config`), job/provider failure (`integration`), or delivered (`explain_to_user`).

## "Send SMS/NPS on status X instead of Y": find the trigger rule first
Requests like "send the NPS SMS when the WO becomes Completed" are change requests, but the useful answer is **where the trigger is defined** and what exactly must change. Do not answer without reading the trigger code.
1. `code_search` the message type in the SQL repo (`Nps`, `Sms`, template name) → the preparing SP; read its status filter (`read_file`).
2. Resolve the tenant's statuses and the attribute the rule uses (often `Catalog.Status.MobileStatusId`, not the status id or name): `sql` on `Catalog.Status` for the tenant, with names via `AppTranslationEntry`.
3. Check one-per-work-order guards (e.g. `NOT EXISTS Operation.NpsData`): if the message is already sent at an earlier status, adding a later status alone will never fire.
4. Classify: rule hard-coded in an SP → `change_request` + `code_change` (name the SP and the line, propose the exact predicate). Rule in a config table → `config` + `config_change`. Never suggest changing `MobileStatusId` (it drives the mobile app).

### Worked example: SM-1808060 (South Africa T68, "NPS SMS when WO is changed to Completed")
- `Communication.PrepareNPSData` (gfsm-europe, lines 19-24) selects status changes whose new status has `MobileStatusId IN (13,4,9,19) OR (MobileStatusId = 8 AND TenantId <> 70)`; template 16; one NPS per WO (`NOT EXISTS Operation.NpsData`).
- T68: `DEFY App.Required` (318) has MobileStatusId 8, so NPS fires there today. `Completed` (335) has MobileStatusId 26, so it never fires. Invoiced (4), DEFY Approved (9), Exchange Approved (13) and Exchange Completed (19) also fire.
- Fix (code change in the SP, business decision): add a tenant-specific predicate for 68 / MobileStatusId 26 **and** exclude 68 from the `MobileStatusId = 8` branch. Otherwise the earlier NPS blocks the Completed one through the one-per-WO guard. Better long term: tenant → NPS trigger statuses in a config table.
