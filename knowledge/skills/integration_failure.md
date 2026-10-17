# Integration failures: SAP / ERP / FY document / Salesforce / Atlas / Mule

## Facts
- Per-document SAP errors: `Operation.ErpDocument` (StatusId, DocumentNr, ErrorDescription, SendDate), `Operation.WorkOrderErpDetails`, `WorkOrderErpFi`, `WorkOrderErpMm` (`wo_erp_sap`).
- Call logs: `Log.RFCCalls` (SAP RFC; Id_Key / Id_Value), `Log.RESTEntries` (Key/Value, Result), `Gateway.PostLog` / `PostErrorLog` (inbound), `Gateway.Notification` (outbound queue), `Email2Case.OutboxMessage` (`sap_rfc_rest_calls`, `outbound_queue_backlog`).
- Jobs: `SiriusInt.JobRunLog`, `SiriusInt.SiriusIntLog` (`job_health`). Many schedules run outside this repo (SQL Agent).
- Other systems (Atlas, SAP, Salesforce) keep their own state; **0 rows in our logs does not prove anything about them.**

## Steps
1. With a work order: `wo_header` → `wo_erp_sap` (ErrorDescription is often the full answer, e.g. "Value … is not allowed for characteristic 'Product'") → `sap_rfc_rest_calls` for that WO/document.
2. Without a work order, or when many records are affected: time window around ticket creation (±24 h) → `job_health`, `outbound_queue_backlog`, `db_errors_recent`. A failing job or a growing queue explains mass symptoms.
3. Read the sending code path (OrderSapManager, FIPARK, `CreateFIPackage*` SPs) only if the error text points into our code.
4. Distinguish: our data is invalid for SAP (master data mapping, e.g. industrial vs commercial code) → `master_data`; our job/queue broken → `integration` / `code_change`; the other system is wrong → `escalate_other_system` with the exact evidence to hand over.
