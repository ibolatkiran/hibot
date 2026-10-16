/*
id: outbound_queue_backlog
title: Outbound queue backlog and errors
purpose: Health of the outbound integration queues: failing/retrying gateway notifications (Gateway.Notification), recent package post errors (Gateway.PostErrorLog) and unprocessed Email2Case outbox messages (Email2Case.OutboxMessage), grouped by procedure / package / event type. Use for "integration stopped", "messages not leaving the system", "everything is delayed", "customers get nothing".
param: WindowHours | int | optional default=24 min=1 max=720 | look-back window in hours for notification and post-error rows (the outbox shows all unprocessed rows regardless of age)
regions: EU, APAC
tables: Gateway.Notification, Gateway.PostErrorLog, Email2Case.OutboxMessage
row_limit: 100
interpret: Each row is a group: Source / Name (procedure, package or event type), Cnt, MaxRetry (notifications only), FirstAt/LastAt and one truncated SampleText. Gateway.Notification rows count only entries with Retry > 0 or an ErrorText: a high MaxRetry with a recent LastAt = a poison message retried forever. Email2Case.OutboxMessage rows are UNPROCESSED messages: an old FirstAt means the outbox processor is down or blocked. A single dominant Name/SampleText = one broken integration; many Names at once = platform-wide problem (check job_health and db_errors_recent).
next: job_health, db_errors_recent, sap_rfc_rest_calls
*/
SELECT 'Gateway.Notification' AS Source, n.ProcedureName AS Name, COUNT(*) AS Cnt, MAX(n.Retry) AS MaxRetry,
       MIN(n.CreateDate) AS FirstAt, MAX(n.CreateDate) AS LastAt, MAX(LEFT(n.ErrorText, 300)) AS SampleText
FROM Gateway.Notification n
WHERE n.CreateDate >= DATEADD(HOUR, -@WindowHours, GETDATE())
  AND (n.Retry > 0 OR n.ErrorText IS NOT NULL)
GROUP BY n.ProcedureName
UNION ALL
SELECT 'Gateway.PostErrorLog', p.PackageName, COUNT(*), NULL, MIN(p.CreateDate), MAX(p.CreateDate), MAX(LEFT(p.ErrorText, 300))
FROM Gateway.PostErrorLog p
WHERE p.CreateDate >= DATEADD(HOUR, -@WindowHours, GETDATE())
GROUP BY p.PackageName
UNION ALL
SELECT 'Email2Case.OutboxMessage (unprocessed)', o.EventType, COUNT(*), NULL, MIN(o.CreatedAt), MAX(o.CreatedAt), MAX(LEFT(o.Message, 300))
FROM Email2Case.OutboxMessage o
WHERE o.ProcessedAt IS NULL
GROUP BY o.EventType
ORDER BY LastAt DESC
