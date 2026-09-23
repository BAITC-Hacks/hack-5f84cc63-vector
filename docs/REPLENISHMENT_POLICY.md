# Replenishment and explicit stockout correction v1

Implemented on 23 September 2026. Forecasting v1 models, thresholds, selection and
holdout results are frozen. This stage consumes forecasts and produces deterministic,
reviewable order quantities. It does not send orders, optimize budgets, add a UI, or use an LLM.

## Evidence and scope

Every recommendation retains supplier, SKU, warehouse, stock unit, forecast model,
forecast rows used, operational inputs, assumptions, intermediate quantities, daily
inventory projections, source shipment/constraint references, and explanation text.

`confirmed` means the supplied calculation inputs/policies have evidence and no
inherited assumptions; it does not guarantee forecast accuracy or supplier acceptance.
`scenario` means at least one input or policy is assumed/estimated. `unavailable`
means required data or forecast coverage is missing; its recommended quantity is null.
Confirmed inputs require `evidence`; scenario/estimated inputs require `id` and `reason`.
All recommendations require manager review, including zero-quantity and confirmed rows.

The real exports lack current available stock, supplier lead times and observed
stockout intervals. Historical beginning-of-month stock and "Итого" are never used
as current available inventory. An explicit stock snapshot must match the planning date.
Without operational inputs the run reports unavailable quantities, not hidden zeros.

## Deterministic calculation

The protection interval is `[planning_date, planning_date + lead_days + review_days)`.
Monthly forecast quantities are allocated uniformly over calendar days, under an
explicit allocation assumption. No extrapolation outside available forecast months.
Existing receipts arrive at the start of their specified day, before that day's demand.
The hypothetical new order arrives at `planning_date + lead_days`.

```text
protection_days = lead_days + review_days
demand = sum(daily forecast over protection interval)
safety_stock = demand / protection_days * safety_days
target_stock = demand + safety_stock
net_requirement = target_stock - available_stock - qualifying_incoming

balance_without_order(t) = available_stock
                        + existing receipts through t
                        - forecast demand through t
timing_requirement = max(0, -balance_without_order(t))
                     over days at/after the new order's arrival

unrounded_requirement = max(0, net_requirement, timing_requirement)
recommended_quantity = apply known minimum/multiple and explicit stock increment
                       to positive unrounded_requirement only
```

The timing term prevents a late but in-horizon receipt from concealing a shortage
that a new order could prevent earlier. Example: demand 100, stock 50, existing receipt
100 on day 10, new-order lead time one day. End-horizon net need is -50, but 40 units
are needed to cover days before the late receipt; the engine recommends 40.

Receipts before the stock snapshot are not counted again. Receipts on/after the
exclusive protection end are ignored. Date and unit/warehouse mismatches are explicit;
unresolved incoming supply is not silently zero. Repeated shipment IDs are rejected.
The output distinguishes used supply and supply ignored because of timing, with row-level reasons.

Negative projected balances represent accumulated unmet demand carried forward,
under an explicit policy assumption. They are not measured lost sales. Shortages before
the new order can arrive remain visible even when its quantity eventually restores stock:
`expedite_or_transfer` means the manager needs faster supply or a transfer; increasing
an ordinary order cannot repair an earlier shortage. `order_now` and `no_order_needed`
cover the other cases. Risk dates assume uniform daily demand, not exact customer timing.

## Ordering constraints

Apply a minimum only for positive need; zero need stays zero. Apply a multiple only
with known semantics, evidence status, order unit, and an explicit stock-unit conversion.
Unknown IEK MOQ semantics and conflicting SE constraints remain visible and unapplied.
No default MOQ of 1 is introduced.

Stock-unit granularity is a separate optional input, `stock_quantity_increment`.
If both granularity and a purchasing multiple exist, round to their smallest common
decimal step so both are satisfied. Example: increment 1 and multiple 1.5 require step 3.
The numeric comparison tolerance is 1e-9 stock units; it is not a demand or MOQ assumption.
The output retains the minimum, multiple, stock increment, effective step, and rounding increment.

## Stockout interface

Explicit intervals require series ID, interval ID, start date, exclusive end date,
`known_at`, and evidence status. Future-known intervals cannot change a past calculation.
Overlapping intervals are unioned by day, not counted twice.

For each eligible affected day, estimate the expected daily rate as the mean of the
daily rates of the preceding three consecutive resolved months. Use original history,
never a later month or corrections being generated in the same call. Estimated lost
sales are `max(0, expected_daily_rate - observed_cleaned_sales_on_day)`.
An absent daily record is treated as zero observed sales only under the explicit
export-coverage/rate-estimation assumption; it does not establish zero customer demand.
Unknown monthly targets, null daily quantities, or insufficient prior history are not imputed.

Add estimated losses to the eligible historical month, then call the existing
forecast model with corrected history. The model identity and hyperparameters remain
frozen. This is a production refit, not a new historical evaluation; the old forecast
holdout metrics do not validate this adjusted forecast. There is no extra correction
added to the final order formula, avoiding double counting.

No real intervals are supplied, so the real-series report says
`unavailable_no_intervals` for all 2,716 series. This does not block an explicitly
labeled recommendation based on observed-sales forecasts.
Synthetic acceptance: normal rate 10/day, July observed sales 20 with 29 explicit
stockout days. Estimated loss 290; the frozen three-month mean forecast increases
from 210 to 306.67, and the same replenishment calculation increases its order.
This demonstrates the mechanism, not measured real lost demand.

## Shipped scenario and verified outputs

`--demo` explicitly enables `DEMO-ORDER-OCTOBER-2026`, planning on **1 October 2026**.
It is a future planning illustration using real October–December forecasts, not an
operational order calculated for the September export date.

| Demo input | IEK | SystemElectric |
|---|---:|---:|
| Lead time, days | 45 | 30 |
| Review period, days | 30 | 30 |
| Safety buffer, days | 7 | 7 |
| Synthetic available stock | 12 days of October forecast | 20 days of October forecast |

Additional labeled assumptions: IEK shipment deadlines are modeled as receipt dates;
shipment quantities are interpreted in the series stock unit and warehouse. Flagged
ambiguous shipment rows are excluded and unlisted supply is assumed absent. SE has no
actual shipment source, so zero incoming is explicitly a scenario assumption.
Unambiguous SE standalone multiples are interpreted in stock units only if embedded
source values do not conflict. Demo stock granularity is 1 "шт", 1 "упак", or 0.01 "м";
these are scenario rounding rules, not claims about supplier minimum batches.

Verified demo run `0f0c62e51923` and repeated run `23d775227fc3`:

- 992 scenario recommendations; 1,724 unavailable series lacking eligible forecasts.
- 976 positive orders and 16 zero orders. The 941 expedite/transfer alerts follow the
  synthetic stock/lead assumptions and must not be presented as actual company shortages.
- Strict run `29031d06e29c`: all 2,716 quantities unavailable without operational inputs.
- All 992 calculations independently reconstructed from intermediate quantities and
  daily totals; full repeated artifacts and summaries matched.
- 72 tests passed, including monotonicity, receipt timing, zero need, rounding/conversions,
  evidence and availability, stockout overlap/causality, correction-to-forecast-to-order,
  and complete deterministic runs. Source Excel and forecasting implementation were unchanged.

Representative IEK calculation: `010300014_`, demand 366.87 + safety 34.24 - stock
53.42 - incoming 96 = net 251.69; explicit one-piece stock increment yields **252**.
SE `020100158_`: demand 573.61 + safety 66.92 - stock 188.17 = 452.36; explicit
interpreted multiple 10 yields **460**. Full-precision numbers, ten examples and every
assumption are in the run artifacts. Displays round intermediate numbers only for readability.

## Running and supplying real inputs

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.replenishment_cli data/processed/forecast/<forecast-run> data/processed/canonical/<canonical-run> --demo
```

Omit `--demo` for explicit-input mode. Supply `--inputs path.json` with
`{"series": [...]}`. Each entry has `series_id`, lead_time_days, review_period_days,
safety_days, available_stock, incoming, and optional constraints/stock_quantity_increment.
Numeric inputs use `{ "value": 45, "status": "confirmed", "evidence": "..." }` or
`{ "value": 45, "status": "scenario", "id": "...", "reason": "..." }`.
Stock additionally requires `snapshot_date`; incoming requires an explicit `shipments`
list (an empty list explicitly means no incoming). Each shipment specifies unique ID,
arrival_date, qty_stock_units, stock_uom, warehouse_id, and evidence metadata.
Constraints specify kind minimum/multiple, value, order_uom, order_to_stock_conversion,
and evidence metadata. Manual inputs and demo generation are mutually exclusive.

For corrections, also provide `--stockouts intervals.json --demand <matching-demand-run>`.
The interval file contains `{"intervals": [...]}` with the fields described above.
Cutoff for historical correction is the frozen forecast cutoff, not the later planning date.

Outputs under a new `data/processed/replenishment/<run-id>/`: recommendations.jsonl,
stockout_adjustments.jsonl, replenishment_summary.json, and human-readable examples.md.
The verified local run also has verification.json from the full arithmetic/reproduction audit.
Missing categories, explicit SKU growth inputs and other absent source fields remain
unresolved. No claim of full case completion or improved inventory cost has been made.
