# Architecture and proposed canonical schema

Status on 23.09.2026: only EDA (scripts/eda.py) is implemented.
Forecasting, replenishment, Streamlit, and LLM features are not implemented.
The schema below is a next-stage proposal, not a completed calculation model.

## Flow

Immutable Excel → file/sheet/role registry → separate IEK/SE adapters →
shared tables → cleaning and features → demand/stockout → forecast → replenishment →
explanation → manager review → export.

Determine roles from verified structure. Do not ingest identical files twice.
Blank, zero, and error are different states. Preserve unknown semantics explicitly.

## Shared fields

All facts have source_id, source_sheet, source_row, source_column (where applicable),
quality_flags, and is_synthetic. The registry stores SHA-256.
SKU codes are strings preserving leading zeros and suffixes; retain the raw code separately.
The working key is supplier_id + sku_id; confirm global identity separately.

| Table | Grain and fields |
|---|---|
| source_manifest | source_id, path, sha256, sheet, observed_structure, approved_role, cutoff, semantics_status |
| products | supplier_id, sku_id, sku_raw, supplier_article, name, stock_uom, category_id nullable |
| transactions | Source-row ID; supplier_id, sku_id, occurred_at, document_number, document_text, warehouse_id, qty_raw, customer_id/price/currency nullable |
| monthly_measures | supplier_id, sku_id, month, measure_kind (sales/inventory/unknown), qty_raw; alternative sources kept separate |
| inventory_snapshots | supplier_id, sku_id, warehouse_id nullable, snapshot_date, snapshot_semantics (beginning/end/as_of/unknown), qty_on_hand, qty_reserved/qty_available nullable |
| shipments | supplier_id, sku_id, shipment_id, qty, uom, expected_date nullable, date_kind (deadline/expected/unknown), received_at nullable |
| order_constraints | supplier_id, sku_id, minimum_order_qty/order_multiple nullable, order_uom, conversion_to_stock_uom nullable, missing/error/conflict flags |
| source_coefficients | supplier_id, scope (portfolio/category/sku), scope_id, coefficient_kind, month_of_year nullable, value, reference_period, available_at, is_source_forecast |
| supplier_policies | supplier_id, lead_time_days, review_period_days, service/safety parameters, source_or_assumption |
| stockout_observations | supplier_id, sku_id, warehouse_id, start/end nullable, method (observed/proxy), evidence, confidence/status |

Do not invent categories, customers, lead times, or other missing information.
Initially store the 701-SKU SE monthly quantities with unknown semantics.
An unknown warehouse in a monthly table does not automatically mean "Алматы":
that warehouse has only been identified in transactions.

## Next stage

Source registry, adapters, and schema checks. Start with IEK, whose roles are better established;
ingest SE by contents with explicit missing inputs.
Determine monthly/transaction source precedence before demand cleaning.
Do not remove repeated catalog codes without resolving conflicts.

Later: qty_raw/qty_clean/is_outlier/reason → forecasts → recommendations with calculation
components, assumptions, version, urgency, and explanation.
Specify Recommended_Order v1 after establishing current stock, horizon, units, and shipment dates.
Check shortages before arrivals, not only the final inventory balance.
Do not conflate minimum quantity and order multiple. The LLM explains computed numbers.
