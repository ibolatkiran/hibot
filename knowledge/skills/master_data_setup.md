# Master data: missing product / SKU / tenant / labour / price / BOM / defect code / warranty type

About 28% of tickets are master-data requests. The answer is usually "this definition is missing for this tenant"; the job is to prove **which link** is missing and **how wide** the gap is.

## Steps
1. Extract the identifiers (SKU 12-digit, material codes, model names, defect/labour codes) and the tenant (from WO via `wo_locate`, else country → `tenant_lookup`).
2. Find the chain the application uses to offer the item, by reading the SP behind the selection list (do not guess):
   - Product visible for a country: `Catalog.Product` → `Catalog.ProductTenant` (tenant) (+ `ProductBuiltIn`).
   - Labour selectable: `dbo.GetSiriusLabours` → `ProductMaterial` → `ProductMaterialPriceGroup` (tenant) → `PriceListEntry` → service-shop payment group, `WarrantyType`, `MaterialVat`.
   - Spare parts / BOM: `Catalog.Material`, `ProductMaterial`, `PriceList*`, `MaterialAvailability`.
   - Defect / labour codes per language: `Catalog.Defect`, `Issue` + `AppTranslationEntry` for the tenant language.
3. Count each link for the concrete item and tenant (`product_sku_lookup`, `product_labour_setup`, or ad-hoc `sql`). The first link with 0 is the gap.
4. Measure the width: siblings in the same `HierarchyId` for the tenant, and the same SKU in other tenants. "One SKU missing" and "whole category never set up in this tenant" lead to different fixes and owners.
5. Point to the setup screen or endpoint the business uses (`graph_search_text` e.g. `LabourPriceManagement`, `SetProductLabourPriceGroup`, `CreateLabourAndPrice`).
6. Recurring pattern? Propose or update a card. Examples: Poland "Missing tenant for SKU" (SAP MM03), country "add model".

## Worked example
SM-2169006: see [value_provenance.md](value_provenance.md). Hierarchy 121 had no labour price groups in tenant 59, so the gap is category-wide.
