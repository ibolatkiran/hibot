/*
id: wo_discount_log
title: Manual discount log (EU)
purpose: Discounts applied to a work order with old and new totals per component (Log.WorkOrderDiscountLog). Use for "price changed after discount", "discount not applied", "grand total differs from invoice".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
regions: EU
tables: Log.WorkOrderDiscountLog
pii: Message:free_text_body
interpret: One row per applied discount, oldest first (AppliedAtUtc is UTC; EmployeeId is who applied it). DiscountAmount should equal OldGrandTotal-NewGrandTotal; per-component Old/New columns show where the reduction landed (labour, spare parts, transport, extra). No rows = no discount was ever logged (discount may have been applied by another path or not at all). Compare the last NewGrandTotal with wo_lines_totals.
next: wo_lines_totals, wo_erp_sap
*/
SELECT
    d.Id AS LogId, d.AppliedAtUtc, d.EmployeeId,
    d.OldLabourTotal, d.NewLabourTotal, d.OldSparePartTotal, d.NewSparePartTotal,
    d.OldTransportTotal, d.NewTransportTotal, d.OldExtraTotal, d.NewExtraTotal,
    d.OldGrandTotal, d.NewGrandTotal, d.DiscountAmount, d.Message
FROM Log.WorkOrderDiscountLog d
WHERE d.WorkOrderId = @WorkOrderId AND d.TenantId = @TenantId
ORDER BY d.AppliedAtUtc, d.Id
