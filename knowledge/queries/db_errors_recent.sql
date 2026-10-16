/*
id: db_errors_recent
title: Recent database errors, grouped
purpose: Which stored procedures are failing right now and how often (grouped by procedure and error number from Log.DB_Errors). Use for "system slow / failing since X", "many users get an error", "job fails every run", or to see whether an error is isolated or systemic.
param: FromDate | datetime | optional | window start; default now minus 24 hours
param: ToDate | datetime | optional | window end (exclusive); default open
param: ErrorProcedure | string | optional like max=200 | only procedures whose name contains this text
param: MessageContains | string | optional like max=100 | only errors whose message contains this text
regions: EU, APAC
tables: Log.DB_Errors
pii: SampleMessage:free_text_body
row_limit: 100
interpret: Ordered by Occurrences. FirstSeen/LastSeen show when the error started and whether it is still happening (LastSeen close to now). A sudden spike of one ErrorNumber in one procedure right after a release = regression; evenly spread over time = chronic. Take the interesting ErrorProcedure to db_errors_list for individual messages. SampleMessage is only one (the alphabetically last) message of the group, truncated.
next: db_errors_list, db_errors_for_wo, job_health
*/
WITH errs AS (
    SELECT CAST(e.ErrorProcedure AS varchar(200)) AS ProcName, e.ErrorNumber, e.ErrorSeverity, e.ErrorDateTime,
           LEFT(e.ErrorMessage, 300) AS Msg
    FROM Log.DB_Errors e
    WHERE e.ErrorDateTime >= COALESCE(@FromDate, DATEADD(HOUR, -24, GETDATE()))
      AND (@ToDate IS NULL OR e.ErrorDateTime < @ToDate)
      AND (@ErrorProcedure IS NULL OR e.ErrorProcedure LIKE '%' + @ErrorProcedure + '%')
      AND (@MessageContains IS NULL OR e.ErrorMessage LIKE '%' + @MessageContains + '%')
)
SELECT TOP 100
    x.ProcName AS ErrorProcedure, x.ErrorNumber, x.ErrorSeverity,
    COUNT(*) AS Occurrences, MIN(x.ErrorDateTime) AS FirstSeen, MAX(x.ErrorDateTime) AS LastSeen,
    MAX(x.Msg) AS SampleMessage
FROM errs x
GROUP BY x.ProcName, x.ErrorNumber, x.ErrorSeverity
ORDER BY COUNT(*) DESC, MAX(x.ErrorDateTime) DESC
