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
