/*
id: notifications_for_wo
title: SMS / e-mail notifications of a work order
purpose: Every SMS or e-mail queued for a work order, from Operation.SmsEmailSendingData and Communication.SmsEmailSendingData, with template name, status description, queue/sent times and the provider response. Use for "customer did not get SMS/e-mail", "wrong message sent", "message sent twice", "notification delayed".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
regions: EU, APAC
tables: Operation.SmsEmailSendingData, Communication.SmsEmailSendingData, Catalog.SmsEmailTemplate, Catalog.SmsEmailSendingStatus
pii: SendingAddress:contact, SendingMessage:free_text_body, SendingResponse:free_text_body
pii_sources: SendingAddress
row_limit: 100
interpret: Source Operation = the operational queue, Communication = the communication-job queue (with provider response and CommunicationType). SendType distinguishes SMS vs e-mail (see the template). SentDate NULL = still queued or failed; compare SendingStatus/StatusDescription and SendingResponse (provider error). DelayMinutes = SentDate - QDate (large = sender job lagging, see job_health / outbound_queue_backlog). The same TemplateName twice close together = duplicate trigger. SendingAddress is masked; use its shape (e-mail vs phone) and presence only. No rows = nothing was queued (check the status flow and the template/permission setup of the tenant).
next: job_health, outbound_queue_backlog, wo_status_timeline
*/
WITH sends AS (
    SELECT 'Operation' AS Source, s.Id, s.QDate, s.SentDate, s.SendType, s.SendingStatus, s.TemplateId,
           s.SendingAddress, s.SendingMessage, CAST(NULL AS nvarchar(max)) AS SendingResponse,
           CAST(NULL AS int) AS CommunicationType
    FROM Operation.SmsEmailSendingData s
    WHERE s.WorkOrderId = @WorkOrderId AND s.TenantId = @TenantId
    UNION ALL
    SELECT 'Communication', c.Id, c.QDate, c.SentDate, c.SendType, c.SendingStatus, c.TemplateId,
           c.SendingAddress, c.SendingMessage, c.SendingResponse, c.CommunicationType
    FROM Communication.SmsEmailSendingData c
    WHERE c.WorkOrderId = @WorkOrderId AND c.TenantId = @TenantId
)
SELECT TOP 100
    x.Source, x.Id, x.QDate, x.SentDate, DATEDIFF(MINUTE, x.QDate, x.SentDate) AS DelayMinutes,
    x.SendType, x.CommunicationType, x.TemplateId, t.Dsc AS TemplateName,
    x.SendingStatus, st.Dsc AS StatusDescription, x.SendingAddress, x.SendingMessage, x.SendingResponse
FROM sends x
LEFT JOIN Catalog.SmsEmailTemplate t ON t.Id = x.TemplateId
LEFT JOIN Catalog.SmsEmailSendingStatus st ON st.Status = x.SendingStatus
ORDER BY x.QDate DESC, x.Source, x.Id DESC
