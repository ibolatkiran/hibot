# Cannot change / cancel / reject / close; "please change status to …"

## Facts
- Allowed transitions per tenant: `Catalog.StatusStep (TenantId, StatusId → NextStatusId)`. `Catalog.Status` gives IsOpen/IsComplete; names via `AppTranslationEntry`.
- Application status changes write a history row in `Operation.WorkOrderGpsInfo` (despite its name, this is the status history). Every WorkOrder update writes before/after JSON to `Log.WorkOrderJsonLog` (trigger `TR_WorkOrder_U`).
- Automatic status job: `dbo.StatusUpdater` → `dbo.StatusUpdaterLog` (EU), rules in `Catalog.StatusUpdaterData`.

## Steps
1. `wo_locate` → tenant. `wo_header` → current status. For closed tickets, **reconstruct the status at ticket creation** (`wo_field_changes`, undo later changes).
2. Determine the requested target status (from text: cancel → Cancelled, transfer → Transfer, …).
3. `status_step_check(TenantId, From, To)`: is the transition configured? Also list what *is* allowed from the current status (`wo_status_transition_check`).
4. `wo_status_timeline` + `wo_field_changes`: did the status move? Was there a history row? (no history row + UpdateUserId unchanged = manual DB fix, see [ticket_window_footprint.md](ticket_window_footprint.md))
5. Check known cards (`cards_match`). A confirmed card may say the restriction is **deliberate** (e.g. Egypt Financed → Transfer, D17).
6. If the transition is configured but still fails: read the status-change SP / endpoint for extra rules (required fields, IsStatusNoteRequired, locks via `Work.IsWorkOrderLocked`, tenant branches) → `error_message_trace.md`.
7. Conclude: missing configuration (`config`), deliberate restriction (`user_error` / `explain_to_user`), data correction request (`data_issue` / `data_fix`), or code path bug.

## Worked examples
- SM-2139009 / SM-2139002 (South Africa T68): 329 No Proof or Money → 314 Cancelled is not in StatusStep; developers forced it in the DB (no history row). (FINDINGS §12, §14)
- SM-2126600 (Egypt T70): 2271 Financed → 2257 Transfer is closed on purpose (D17). Explain to the user; flag manual forcing as a process violation.
