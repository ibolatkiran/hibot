/*
id: db_errors_for_wo
title: Database errors mentioning a work order
purpose: SQL errors written by stored procedures (Util.AddErrorLog -> Log.DB_Errors) whose message carries "WOId:<id>". Use first when a user action on a work order failed with a generic error, a job skipped an order, or an integration step threw inside the database.
param: WorkOrderId | int | required | work order id searched as "WOId:<id>" (exact id, not a prefix)
param: FromDate | datetime | optional | window start (UTC-ish server time); default now minus 7 days
param: ToDate | datetime | optional | window end (exclusive); default open
param: ErrorProcedure | string | optional like max=200 | only errors from procedures whose name contains this text
regions: EU, APAC
tables: Log.DB_Errors
pii: UserName:name, ErrorMessage:free_text_body
row_limit: 100
interpret: Newest first. ErrorProcedure is the procedure that caught the error, ErrorLine the line, ErrorMessage the full text (masked/truncated). ErrorNumber 547 = FK/constraint, 2627/2601 = duplicate key, 515 = NULL into NOT NULL, 245/8114 = conversion, 1205 = deadlock, 50000 = custom RAISERROR. The same message repeating at fixed intervals = a job retrying. No rows does NOT prove no error: only procedures that call Util.AddErrorLog with WOId are covered; also try db_errors_recent for the procedure, and widen FromDate. The table has no TenantId, so the id match is global - confirm the tenant via wo_header.
next: db_errors_recent, db_errors_list, wo_debug_log, job_health
*/
SELECT TOP 100
    e.ErrorID, e.ErrorDateTime, e.ErrorProcedure, e.ErrorNumber, e.ErrorSeverity, e.ErrorState, e.ErrorLine,
    e.UserName, e.ErrorMessage
FROM Log.DB_Errors e
WHERE e.ErrorDateTime >= COALESCE(@FromDate, DATEADD(DAY, -7, GETDATE()))
  AND (@ToDate IS NULL OR e.ErrorDateTime < @ToDate)
  AND (e.ErrorMessage LIKE '%WOId:' + CAST(@WorkOrderId AS varchar(20)) + '[^0-9]%'
    OR e.ErrorMessage LIKE '%WOId:' + CAST(@WorkOrderId AS varchar(20))
    OR e.ErrorMessage LIKE '%WOId: ' + CAST(@WorkOrderId AS varchar(20)) + '[^0-9]%'
    OR e.ErrorMessage LIKE '%WOId: ' + CAST(@WorkOrderId AS varchar(20)))
  AND (@ErrorProcedure IS NULL OR e.ErrorProcedure LIKE '%' + @ErrorProcedure + '%')
ORDER BY e.ErrorDateTime DESC, e.ErrorID DESC
