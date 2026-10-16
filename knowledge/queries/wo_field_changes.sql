/*
id: wo_field_changes
title: Work order field-level change log
purpose: Which fields changed on a work order and when, from Log.WorkOrderJsonLog (one row per UPDATE, written by trigger TR_WorkOrder_U). Mirrors dbo.getChangesByWorkOrderId but NULL-safe and with status names. Use for "who/what changed the status, technician, shop, appointment, warranty type or reject reason", "value flipped back", "field overwritten by a job".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
param: LanguageId | int | optional | Catalog.AppLanguage.Id used for status/lookup names; defaults to the tenant language, falls back to any language
regions: EU, APAC
tables: Log.WorkOrderJsonLog, Catalog.Tenant, Catalog.Status, Catalog.AppTranslationEntry, Catalog.Employee
pii: ChangedByName:name
pii_sources: Dsc
interpret: One row per UPDATE ordered by Id. ChangedFields lists every tracked column whose value differs between the old (Deleted) and new (Inserted) row image; only the most useful old/new pairs are returned (status, technician, shop, dates, warranty/reject, UpdateUserId = Employee id of the user who saved). An UPDATE that changes nothing tracked shows an empty ChangedFields. A burst of rows within seconds usually means a job or a retry loop. Rows with TenantId NULL are included. If there are no rows the trigger did not fire (order created in the same transaction, trigger disabled, or multi-row update which the trigger skips).
next: wo_status_timeline, wo_status_updater_log, wo_header
*/
WITH lang AS (SELECT COALESCE(@LanguageId, (SELECT TOP 1 t.LanguageId FROM Catalog.Tenant t WHERE t.Id = @TenantId)) AS Id)
SELECT
    l.Id AS LogId, l.LogDate,
    JSON_VALUE(l.Deleted,'$[0].StatusId') AS OldStatusId, osn.FieldText AS OldStatusName,
    JSON_VALUE(l.Inserted,'$[0].StatusId') AS NewStatusId, nsn.FieldText AS NewStatusName,
    JSON_VALUE(l.Deleted,'$[0].EmployeeId') AS OldEmployeeId, JSON_VALUE(l.Inserted,'$[0].EmployeeId') AS NewEmployeeId,
    JSON_VALUE(l.Deleted,'$[0].ServiceShopId') AS OldServiceShopId, JSON_VALUE(l.Inserted,'$[0].ServiceShopId') AS NewServiceShopId,
    JSON_VALUE(l.Deleted,'$[0].WarrantyTypeId') AS OldWarrantyTypeId, JSON_VALUE(l.Inserted,'$[0].WarrantyTypeId') AS NewWarrantyTypeId,
    JSON_VALUE(l.Deleted,'$[0].RejectReasonId') AS OldRejectReasonId, JSON_VALUE(l.Inserted,'$[0].RejectReasonId') AS NewRejectReasonId,
    JSON_VALUE(l.Deleted,'$[0].AppointmentDate') AS OldAppointmentDate, JSON_VALUE(l.Inserted,'$[0].AppointmentDate') AS NewAppointmentDate,
    JSON_VALUE(l.Deleted,'$[0].OperationDate') AS OldOperationDate, JSON_VALUE(l.Inserted,'$[0].OperationDate') AS NewOperationDate,
    JSON_VALUE(l.Deleted,'$[0].VerificationDate') AS OldVerificationDate, JSON_VALUE(l.Inserted,'$[0].VerificationDate') AS NewVerificationDate,
    JSON_VALUE(l.Inserted,'$[0].UpdateUserId') AS UpdateUserId, ub.TechnicianCode AS UpdateUserCode, ub.Dsc AS ChangedByName,
    CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ConsumerAddressId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ConsumerAddressId'), '~') THEN 'ConsumerAddressId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ContactName'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ContactName'), '~') THEN 'ContactName, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ContactPhone'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ContactPhone'), '~') THEN 'ContactPhone, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ServiceShopId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ServiceShopId'), '~') THEN 'ServiceShopId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].EmployeeId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].EmployeeId'), '~') THEN 'EmployeeId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].OnSiteOperation'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].OnSiteOperation'), '~') THEN 'OnSiteOperation, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].WarrantyTypeId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].WarrantyTypeId'), '~') THEN 'WarrantyTypeId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].StatusId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].StatusId'), '~') THEN 'StatusId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].IssueId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].IssueId'), '~') THEN 'IssueId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].OperationDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].OperationDate'), '~') THEN 'OperationDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].OpenDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].OpenDate'), '~') THEN 'OpenDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].AppointmentDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].AppointmentDate'), '~') THEN 'AppointmentDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].OperationStartDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].OperationStartDate'), '~') THEN 'OperationStartDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].BarcodeReadDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].BarcodeReadDate'), '~') THEN 'BarcodeReadDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].VerificationDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].VerificationDate'), '~') THEN 'VerificationDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].Barcode'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].Barcode'), '~') THEN 'Barcode, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].RejectReasonId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].RejectReasonId'), '~') THEN 'RejectReasonId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].PaymentTypeId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].PaymentTypeId'), '~') THEN 'PaymentTypeId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].CreateUserId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].CreateUserId'), '~') THEN 'CreateUserId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].UpdateUserId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].UpdateUserId'), '~') THEN 'UpdateUserId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].TenantId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].TenantId'), '~') THEN 'TenantId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ContactId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ContactId'), '~') THEN 'ContactId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ExternalServicePaparNr'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ExternalServicePaparNr'), '~') THEN 'ExternalServicePaparNr, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ProductionYear'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ProductionYear'), '~') THEN 'ProductionYear, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ProductionMonth'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ProductionMonth'), '~') THEN 'ProductionMonth, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].SerialNr'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].SerialNr'), '~') THEN 'SerialNr, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].AttributeId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].AttributeId'), '~') THEN 'AttributeId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].Reopen'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].Reopen'), '~') THEN 'Reopen, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].FirstAppointmentDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].FirstAppointmentDate'), '~') THEN 'FirstAppointmentDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].WarrantyDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].WarrantyDate'), '~') THEN 'WarrantyDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ChannelId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ChannelId'), '~') THEN 'ChannelId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ProjectCode'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ProjectCode'), '~') THEN 'ProjectCode, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].PriorityId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].PriorityId'), '~') THEN 'PriorityId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].RejectDescription'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].RejectDescription'), '~') THEN 'RejectDescription, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].CallCenterNote'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].CallCenterNote'), '~') THEN 'CallCenterNote, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].MaterialCost'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].MaterialCost'), '~') THEN 'MaterialCost, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].LabourCost'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].LabourCost'), '~') THEN 'LabourCost, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].TransportaionCost'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].TransportaionCost'), '~') THEN 'TransportaionCost, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].TotalCost'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].TotalCost'), '~') THEN 'TotalCost, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ErpDocumentId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ErpDocumentId'), '~') THEN 'ErpDocumentId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].StatusNote'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].StatusNote'), '~') THEN 'StatusNote, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].AppointmentEndDate'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].AppointmentEndDate'), '~') THEN 'AppointmentEndDate, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ConsumerInfo'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ConsumerInfo'), '~') THEN 'ConsumerInfo, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].SourceOfDefect'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].SourceOfDefect'), '~') THEN 'SourceOfDefect, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ExtraCost'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ExtraCost'), '~') THEN 'ExtraCost, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].NoInvoice'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].NoInvoice'), '~') THEN 'NoInvoice, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].DistributorId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].DistributorId'), '~') THEN 'DistributorId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].ExternalId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].ExternalId'), '~') THEN 'ExternalId, ' ELSE '' END
    + CASE WHEN ISNULL(JSON_VALUE(l.Deleted,'$[0].TaskTypeId'), '~') <> ISNULL(JSON_VALUE(l.Inserted,'$[0].TaskTypeId'), '~') THEN 'TaskTypeId, ' ELSE '' END AS ChangedFields
FROM Log.WorkOrderJsonLog l
CROSS JOIN lang
LEFT JOIN Catalog.Status os ON os.Id = TRY_CAST(JSON_VALUE(l.Deleted,'$[0].StatusId') AS int)
LEFT JOIN Catalog.Status ns ON ns.Id = TRY_CAST(JSON_VALUE(l.Inserted,'$[0].StatusId') AS int)
LEFT JOIN Catalog.Employee ub ON ub.Id = TRY_CAST(JSON_VALUE(l.Inserted,'$[0].UpdateUserId') AS int)
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = os.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) osn
    OUTER APPLY (SELECT TOP 1 e.FieldText FROM Catalog.AppTranslationEntry e
                WHERE e.AppTranslationId = ns.AppTranslationId
                ORDER BY CASE WHEN e.LanguageId = lang.Id THEN 0 ELSE 1 END, e.LanguageId) nsn
WHERE l.WorkOrderId = @WorkOrderId AND (l.TenantId = @TenantId OR l.TenantId IS NULL)
ORDER BY l.Id
