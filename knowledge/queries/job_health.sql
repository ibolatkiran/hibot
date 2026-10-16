/*
id: job_health
title: Background job health
purpose: Latest run of every job in SiriusInt.JobRunLog (status, duration, failures in the window) plus procedures that logged errors in SiriusInt.SiriusIntLog. Use for "job did not run", "data not synced / updated since X", "batch stuck", "integration job failing".
param: WindowHours | int | optional default=24 min=1 max=720 | look-back window in hours for run and error counts
param: FromDate | datetime | optional | window start (UTC); default now minus WindowHours. With FromDate/ToDate the "last run" is the last run BEFORE ToDate (look back 30 days from FromDate)
param: ToDate | datetime | optional | window end (exclusive, UTC); default open (now). Use FromDate/ToDate to look at the time around an old incident
param: JobName | string | optional like max=100 | only jobs / procedures whose name contains this text
regions: EU, APAC
tables: SiriusInt.JobRunLog, SiriusInt.SiriusIntLog
pii: SampleText:free_text_body
row_limit: 150
interpret: JobRunLog rows: one per job = its LAST run (LastAt = start, LastEnd, LastStatus, LastDurationMin). LastEnd NULL with an old LastAt = the run never finished (hung or crashed). FailuresInWindow counts runs whose status mentions fail/error or that logged an ErrorMessage; RunsInWindow = 0 means the job did not run in the window at all (scheduler problem). SiriusIntLog rows: one per procedure with error entries in the window (Cnt = errors, SampleText = last error). Compare LastAt with the time the business symptom started.
next: db_errors_recent, outbound_queue_backlog, wo_status_updater_log
*/
WITH runs AS (
    SELECT j.JobName, j.StartTime, j.EndTime, j.Status, j.ErrorMessage,
           ROW_NUMBER() OVER (PARTITION BY j.JobName ORDER BY j.StartTime DESC, j.Id DESC) AS rn
    FROM SiriusInt.JobRunLog j
    WHERE j.StartTime >= DATEADD(DAY, -30, COALESCE(@FromDate, GETDATE()))
      AND (@ToDate IS NULL OR j.StartTime < @ToDate)
      AND (@JobName IS NULL OR j.JobName LIKE '%' + @JobName + '%')
),
stats AS (
    SELECT j.JobName, COUNT(*) AS RunsInWindow,
           SUM(CASE WHEN j.Status LIKE '%fail%' OR j.Status LIKE '%error%'
                      OR (j.ErrorMessage IS NOT NULL AND j.ErrorMessage <> '') THEN 1 ELSE 0 END) AS FailuresInWindow
    FROM SiriusInt.JobRunLog j
    WHERE j.StartTime >= COALESCE(@FromDate, DATEADD(HOUR, -@WindowHours, GETDATE()))
      AND (@ToDate IS NULL OR j.StartTime < @ToDate)
      AND (@JobName IS NULL OR j.JobName LIKE '%' + @JobName + '%')
    GROUP BY j.JobName
)
SELECT 'JobRunLog' AS Source, r.JobName AS Name, r.StartTime AS LastAt, r.EndTime AS LastEnd, r.Status AS LastStatus,
       DATEDIFF(MINUTE, r.StartTime, COALESCE(r.EndTime, GETDATE())) AS LastDurationMin,
       COALESCE(s.RunsInWindow, 0) AS RunsInWindow, COALESCE(s.FailuresInWindow, 0) AS FailuresInWindow,
       LEFT(r.ErrorMessage, 300) AS SampleText
FROM runs r
LEFT JOIN stats s ON s.JobName = r.JobName
WHERE r.rn = 1
UNION ALL
SELECT 'SiriusIntLog', l.ProcedureName, MAX(l.CreateDate), NULL, NULL, NULL, NULL, COUNT(*), MAX(LEFT(l.ErrorText, 300))
FROM SiriusInt.SiriusIntLog l
WHERE l.CreateDate >= COALESCE(@FromDate, DATEADD(HOUR, -@WindowHours, GETDATE()))
  AND (@ToDate IS NULL OR l.CreateDate < @ToDate)
  AND (@JobName IS NULL OR l.ProcedureName LIKE '%' + @JobName + '%')
GROUP BY l.ProcedureName
ORDER BY LastAt DESC
