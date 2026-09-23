# Open questions after EDA

Checked on 23.09.2026. SE MOQ was found and read; its previously reported absence is resolved.

| Priority | Question | Until resolved |
|---|---|---|
| P0 | Why do SE filenames differ from their contents? Where is the master with reserved/free stock and incoming goods? | Use a content-based registry; do not substitute seasonality for shipments. |
| P0 | Do SE monthly quantities in "Ежемесячные продажи" represent stock or movements, beginning or end of month? | measure_kind/snapshot_semantics=unknown. |
| P0 | Where is current available stock by warehouse, and what is its snapshot date? | Do not use IEK "Итого": it matches January 2024. |
| P0 | What are lead times, review periods, and the target service level? | Explicit scenario parameters instead of invented business standards. |
| P0 | Why do monthly quantities differ from transactions, and which source takes precedence? | Do not add sources or transfer corrections between them. |
| P1 | What do negative quantities in 2023–2026 and blank quantities mean? | Preserve signs, isolate nulls; no abs() or zero imputation. |
| P1 | Where are categories, anonymized customers, prices, stockout periods, and the 1C statement/V2 report? | Flag their absence; keep synthetic acceptance tests separate from real data. |
| P1 | Does a blank month mean zero or unknown? What is the September 2026 cutoff? | No automatic imputation; explicitly account for incomplete periods. |
| P1 | Is the IEK constraint a minimum or a multiple? How should reels/meters/packs be converted? | Separate minimum_order_qty/order_multiple/uom fields. |
| P1 | How should #N/A, missing MOQ, and 26 SE 0/1 conflicts be resolved? | Quality flags; no hidden fallback to 1. |
| P1 | Are repeated IEK shipment rows duplicates or separate items? | Retain rows and provenance until resolved. |
| P1 | How should "поступление до" be interpreted? | Store date_kind=deadline, not actual arrival. |
| P2 | What are the units of aggregate seasonality measures, and are their coefficients applicable to SKUs? | Preserve period/provenance; do not apply twice. |
| P2 | Which order import format does 1C accept? | Export compatibility remains unverified. |

Record the source, date, rule, and affected calculations for every answer.
Do not claim customer-level detection has been validated on real data without client_id.
Document aggregation is a separately labeled approximation.

## Implementation policy while answers are pending

Ingestion is implemented and runs with unresolved values. Source semantic overrides in
`config/ingestion.json` require a reason and `assumption`/`confirmed` status; confirmed
overrides require evidence. Assumptions are carried into records and the run report.
Supplier policy values remain null by default. Explicit scenario values can be configured
for subsequent stages without presenting them as established supplier rules.
Completing ingestion does not close the business questions above.

Demand cleaning v1 now explicitly selects transactions as primary observed sales and
uses monthly sources only for reconciliation. This implementation choice does not
resolve the reason for discrepancies or validate a monthly fallback. Negative quantities
remain unresolved with null regular quantities. See [DEMAND_POLICY.md](DEMAND_POLICY.md).
Business labels for one-off candidates and actual document posting/availability times
are still needed to assess detection quality and historical backdated corrections.

Forecasting v1 uses a labeled January 2025 source-coverage assumption. Supplier
confirmation of export completeness, product activity dates, and zero-versus-missing
months is still required. Forecasts currently cover 992 of 2,716 transaction-observed
series; the remainder are reported explicitly rather than zero-filled. Portfolio
seasonality coefficients still lack confirmed SKU applicability and historical
availability. See [FORECAST_POLICY.md](FORECAST_POLICY.md) for the independent holdout
results, including the segments where model selection underperforms the baseline.

Replenishment v1 now runs in explicit-input or demo-scenario mode. The October 1 demo
does not resolve missing current stock or supplier lead times: each assumed value is
labeled. Confirm intramonth demand/receipt timing, whether unmet demand is backordered
or lost, and allowed stock-unit increments before operational use. Source shipment
deadlines, warehouse/unit assignment and SE order-unit conversion remain assumptions.
The interval-based stockout interface is implemented, but real intervals remain unavailable.
