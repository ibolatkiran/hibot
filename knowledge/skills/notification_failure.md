# SMS / e-mail / NPS / push not sent

## Steps
1. Per work order: `notifications_for_wo` → `Operation.SmsEmailSendingData` and `Communication.SmsEmailSendingData` rows (SendType, SendingStatus → `Catalog.SmsEmailSendingStatus`, QDate, SentDate, TemplateId). Interpret:
   - no row → the message was never queued: check the trigger (status-change notification rules, `PrepareStatusNotifications`, tenant templates);
   - queued but not sent → job/provider problem (`SMSEmailJob`, `job_health`, provider errors; e.g. SM-2011812 SMS API 401);
   - sent → delivery is outside Sirius.
2. Tenant-wide ("since date X nothing is sent"): time window `job_health` + `outbound_queue_backlog` + `db_errors_recent`; compare sending counts before and after the date.
3. Templates and config: `SmsEmailTemplate` per tenant and status; NPS: `Operation.NpsData*`, `Communication.FlsNpsQueue`.
4. Conclude: missing template/rule (`config`), job/provider failure (`integration`), or delivered (`explain_to_user`).
