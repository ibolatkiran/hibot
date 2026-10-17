---
skill: value_provenance
keywords: (labou?r|price|cost|total|amount|discount|tutar|fiyat|işçilik|date|tarih)\b.{0,60}(\b0\b|zero|wrong|incorrect|missing|empty|blank|not (show|visible|display)|yanlış|gözük|görün)|not showing|not visible|shows 0
required_evidence:
- id: column_writers | description: Find who writes the value: column_writers on the table / column behind the screen field | satisfied_by: tool:column_writers
- id: writer_sp_read | description: Read the SP / code that computes or sets the value, around the assignment (cite file:line) | satisfied_by: read_file:\.(sql|cs)$
- id: inputs_query | description: Query the INPUTS of that computation for the concrete record(s) of the ticket (are the input rows there, with the expected flags / types?) | satisfied_by: sql:. ; template:wo_lines_totals,wo_detail_material_types,product_labour_setup,wo_discount_log,product_sku_lookup
---
# A value is 0 / wrong / missing: follow its provenance

Use when the ticket says a cost, price, labour charge, total, date, status name or any displayed value is 0, wrong, empty or "not showing".

## Steps
1. **Name the value in system terms.** Which column feeds the screen? Use the screen → endpoint → SP chain (`graph_trace_endpoint` on the screen's call) or `code_search` for the label's i18n key. Typical: `Operation.WorkOrder.LabourCost / MaterialCost / TotalCost / VerificationDate`.
2. **Find who writes it**: `column_writers(table, column)` → SPs that assign it (and C# that updates it). Open the SP body around the assignment (`read_file` on the sqlproj path).
3. **Write down the formula/inputs** from the code, e.g. `LabourCost = SUM(WorkOrderDetail.Price) where Catalog.Material.Type = 1`.
4. **Query the inputs for the concrete records** (`sql`): are the input rows there? With the expected type/flags? Example: `WorkOrderDetail` lines joined to `Catalog.Material` with `Type`.
5. **If inputs are missing, ask why they could not be created**: which screen/SP provides the selectable inputs (e.g. labour list `GET api/Technician/GetSiriusLabours` → `dbo.GetSiriusLabours`), read its join chain, and check each link for the concrete product/tenant (counts per link).
6. **Compare with a working sibling** (same hierarchy/tenant, or same product in another tenant) to tell "this item missing" from "whole category/tenant never set up".
7. Conclude: calculation bug (formula wrong) vs input missing (master data / user did not add) vs input not selectable (configuration / master data chain broken).

## Worked example: SM-2169006 (Bangladesh, "Sirius is not showing Labour Charge")
- `dbo.PricingWorkOrder`: LabourCost = SUM(Price) of detail lines whose `Catalog.Material.Type = 1`. The formula is fine.
- The three WOs had **no Type=1 lines** (only TRNS Type 3 and Extra Type 4; the "Installation" defect line has MaterialId NULL).
- The labour list comes from `dbo.GetSiriusLabours`: `Catalog.ProductMaterial` → `ProductMaterialPriceGroup` (tenant) → `PriceListEntry` → service-shop payment group, warranty type, `MaterialVat`.
- SKUs 7440000008/7440000011 (tenant 59, hierarchy 121): ProductTenant yes, ProductMaterial **0**, PriceGroup **0**; no hierarchy-121 product in tenant 59 has labour price groups.
- Root cause: `master_data` / `master_data_change`. Labour setup was never done for that category in Bangladesh (FINDINGS §18).
