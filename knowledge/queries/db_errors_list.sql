/*
id: db_errors_list
title: Recent database errors, individual rows
purpose: Individual Log.DB_Errors rows for a procedure and/or time window / message text (the detail view after db_errors_recent). Use to read actual error messages around an incident time.
param: ErrorProcedure | string | optional like max=200 | procedure name contains this text
param: MessageContains | string | optional like max=100 | message contains this text (e.g. a work order id, a table name)
param: FromDate | datetime | optional | window start; default now minus 24 hours
param: ToDate | datetime | optional | window end (exclusive); default open
require_any: ErrorProcedure, MessageContains
regions: EU, APAC
tables: Log.DB_Errors
pii: UserName:name, ErrorMessage:free_text_body
row_limit: 100
interpret: Newest first, full (masked, truncated) ErrorMessage. UserName is the database/application caller. Use ErrorDateTime to line up with the incident time from the ticket (server time, usually UTC). Identical messages with growing timestamps = a retrying job.
next: db_errors_recent, db_errors_for_wo
*/
SELECT TOP 100
    e.ErrorID, e.ErrorDateTime, e.ErrorProcedure, e.ErrorNumber, e.ErrorSeverity, e.ErrorState, e.ErrorLine,
    e.UserName, e.ErrorMessage
FROM Log.DB_Errors e
WHERE e.ErrorDateTime >= COALESCE(@FromDate, DATEADD(HOUR, -24, GETDATE()))
  AND (@ToDate IS NULL OR e.ErrorDateTime < @ToDate)
  AND (@ErrorProcedure IS NULL OR e.ErrorProcedure LIKE '%' + @ErrorProcedure + '%')
  AND (@MessageContains IS NULL OR e.ErrorMessage LIKE '%' + @MessageContains + '%')
ORDER BY e.ErrorDateTime DESC, e.ErrorID DESC
