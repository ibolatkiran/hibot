## Manual spot-check (11 random chains, verified against source)

Chains were sampled randomly (fixed seeds) from `trace_index.json` and verified by reading the frontend call line, the controller action, and the manager method source.

| chain (app) | frontend line | controller | manager -> db object | verdict |
|---|---|---|---|---|
| ControlSerialNumber (partner) | `SerialNumberParse.js:215` | `TechnicianController.cs:3304` | `TechnicianManager.ControlSerialNumber` -> SP `dbo.ControlSerialNumber` | correct |
| GetFileList (technician) | `FilePreview.js:199` | `:5251` | `GetFileList` -> SP `dbo.GetFileList` | correct |
| SearchScheduling (portal) | `schedulingSlice.js:83` | `:6187` | `SearchScheduling` -> SP `dbo.SearchScheduling` (name on next line after `Query<...>(`) | correct |
| GetConsumerTitles (partner) | `AssignedJob.js:1260` | `:1220` | inline `SELECT ... FROM dbo.GetConsumerTitles(...)` -> function | correct |
| GetServiceShopOffWorkTask (portal) | `serviceShopMaterialSlice.js:22` | `:617` | SP `dbo.GetServiceShopOffWorkTask` | correct |
| GetMaterialsByIds (partner, HeadOffice) | `TriageMaterialOrder.js:1395` | `HeadOfficeController.cs:480` | ADO `new SqlCommand("dbo.GetMaterialsByIds")` + StoredProcedure | correct |
| download-pdf (partner) | `Header.js:1599` | `:2269` | `GetBonaPdfData` -> inline `FROM [dbo].[BonaDataPdfRequest](@WorkOrderId)` -> function; also `CallByteArray` | correct |
| GetHistoricalBom (partner) | `SelectDialog.js:107` | `:5318` | `GetHistoricalBomMaterials` SP + `SetMaterialBom` | correct |
| GetSiriusSupportInformation (technician) | `ContactDetail.js:45` | `:394` | inline FROM `[dbo].[GetSiriusSupportInformation](...)` -> function | correct |
| Email2Case heartbeat (crm) | `signalr/email2case.js:259` | `Email2CaseController.cs:161` | `OperatorConnectionService.UpdateHeartbeatAsync` -> `UPDATE [Email2Case].[OperatorState]` | correct |
| GetEmployeeRole (portal) | `employeeSlice.js:38` | `:4084` | EF `_siriusContext.Role` -> table `Role` (found after adding DbSet detection) | correct |

Frontend saga chains (4 random in technician app, e.g. `AssignedJob.js -> labourActions::getLabourRequest -> GET_LABOUR_REQUEST -> sagas/labours::getLabours -> api/afterSalesDataServices::getLabours`) were verified by reading the action/saga/api files: all correct.

Result: 11/11 backend chains correct, 4/4 saga chains correct. Observed (genuine, not matcher errors) frontend/backend mismatches among the unmatched calls: legacy `Artemis*` endpoints that no longer exist in the API (e.g. `SetArtemisServicePaperCreate`, `GetArtemisFisDetay`), `OmniChannel/SearchPhoneLog` (only the manager exists), partner `HeadOffice/GetUserTypes` (the API exposes it as `Technician/GetUserTypes`), `Technician/payment/create-session` (the API controller is `Payment`), and GET-vs-POST verb mismatches (`GetSiriusMaterialsBySearchKey`, `GetMaterialAvailabilitySirius`) flagged as `verb-mismatch+controller+action`.
Known imperfection seen during checking: the `GetHistoricalBom` downstream list also contains small helpers of the manager class (`IsNumeric`, `ConvertToDataTable`) because same-class calls are followed; DB lists are not affected.
