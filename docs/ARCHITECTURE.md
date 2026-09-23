# Architecture and proposed canonical schema

Status on 23.09.2026: EDA, ingestion, cleaning, forecasting, explicit-interval stockout correction,
and replenishment v1 are implemented. A local Streamlit review workspace is implemented;
LLM features are not implemented.
The table below describes the intended model; the implemented subset is specified separately below.

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

## Implemented ingestion contract

- Package: `src/vector_pipeline/`; entry point: `python -m vector_pipeline.cli`.
- `config/sources.json`: explicit paths, SHA-256, sheets, exact normalized headers,
  field positions, reviewed structural roles, and one `duplicate_of` mapping.
  Every raw workbook must be registered. Header/hash drift fails before publication.
- `config/ingestion.json`: explicit semantic overrides and unresolved supplier policies.
  Missing business answers do not prevent ingestion; assumptions require an ID and reason,
  propagate to records, and are captured with the effective configuration.
- Runtime contracts and validation: `schema.py`; serialized contracts: `schema.json` in each run.
- Files are JSONL, using JSON numbers, nulls, strings, and ISO dates; source codes retain
  leading zeros and underscores. No database or forecasting framework is required.

Implemented fact tables: transactions, monthly_measures, inventory_snapshots, shipments,
order_constraints, aggregate_measures, source_coefficients, source_controls, and quarantine.
`product_observations` stores distinct source/SKU/name/article/unit combinations with the
first supporting row. `products` has one supplier/SKU key and candidate attributes plus
references to those observations. Conflicting attributes remain null in the selected
field; all candidates and their source observations remain available. Repeated identical
product observations need not be emitted for every transaction; each transaction retains
its own source coordinates.

`products` is a consolidated dimension: it uses `source_refs` rather than one fabricated
source row. Other facts use the shared provenance fields described above. Record IDs are
derived from source ID, source fingerprint, row, column, and output table.

Operational rules for downstream consumers:

- Transactions preserve all SKU rows, including null quantities and negative signs.
  Invalid date/quantity rows are also copied into quarantine. They are not clean demand yet.
- Monthly blanks remain explicit null observations. IEK beginning stock becomes inventory
  snapshots; monthly series with unknown meaning remain monthly_measures. A labeled override
  can route a series to inventory; unknown snapshot semantics retain a null snapshot date.
- Stock `qty_available` and `qty_reserved` are null unless supplied. Historical monthly
  snapshots are not current-stock values. The "Итого" column is kept only in source_controls.
- Shipments represent source shipment cells, including blank quantities. They are not all
  confirmed positive incoming orders. Dates retain `date_kind=deadline`; units remain unknown.
- IEK constraint semantics remain unknown; positive cached values are preserved without
  inventing a minimum or multiple. SE uses the explicitly labeled order multiple. Zero/error
  constraints are invalid, not replaced with 1. Conflicting source values remain separate.
- Aggregate seasonal series remain separate by source and reference period. Portfolio
  applicability and availability flags prevent treating them as established SKU history.
- Quarantine contains invalid SKU rows, invalid transactions, and invalid standalone MOQ
  rows. Other issues, such as embedded zero multiples, null monthly cells, and duplicate
  observations, remain in their canonical tables with flags and report-level conflict groups.
- Supplier policies are currently stored in the configuration snapshot, not as a populated
  fact table. No observed stockout table is produced because no source supplies intervals.

Each run records table hashes/counts, source status, quality flags, duplicate groups,
constraint conflicts, source row audit counts, and unchanged source hashes.
Output is written into a new run directory; failures are marked and are not published as complete.

Validation on the reviewed source set: 248 915 transaction rows, 3 886 product keys,
94 149 historical inventory cells, 122 694 monthly quantity cells, 15 738 shipment cells,
3 046 constraint observations, and 26 conflicting constraint groups.
19 unit tests and 153 independent table/column checks passed, including foreign keys and source hashes.

## Implemented demand cleaning

`demand_cli.py` consumes verified canonical transactions, monthly measures, quarantine,
and source controls. `demand.py` implements document aggregation and causal robust
screening; `reconciliation.py` produces separate source diagnostics. Configuration:
`config/demand.json`; complete policy: [DEMAND_POLICY.md](DEMAND_POLICY.md).

Ingestion contracts are unchanged. Cleaned transactions retain every canonical field
and add document_id, quantity_state, qty_regular, is_outlier, outlier_score,
outlier_reason, and cleaning_status; assumption_ids include the screening policy.
Document groups expose source-row/transaction references and baseline statistics.
The baseline preserves SKU, warehouse, and unit and uses only earlier calendar days.
Monthly quantities never enter the regular-sales measure. Negative or incomplete
documents remain unresolved; sparse positive history is preserved. The cleaning stage
does not compensate stockouts or infer customers; stockout adjustment is implemented downstream.
Runs publish deterministic JSONL and a hashed summary
only after input/config/code integrity checks.

## Implemented forecasting, replenishment and review

Forecasting v1 is implemented in `forecast.py` and `forecast_cli.py`; policy and measured
results are in [FORECAST_POLICY.md](FORECAST_POLICY.md). It consumes cleaned transactions,
preserves supplier/SKU/warehouse/unit grain, and produces monthly series, coverage,
model scores, rolling/holdout predictions, forecasts, and a hashed summary. Unknown or
unresolved months break history. Selection uses only pre-holdout origins; production
refits the frozen model including the now-observed holdout. No ingestion or cleaning
contract changed. Mean-baseline forecasts accompany selected-model quantities.

Replenishment is implemented in `replenishment.py` and `replenishment_cli.py`.
`stockout.py` accepts explicit intervals, corrects eligible history and refits the frozen
forecast model without changing selection or holdout claims. Real intervals remain absent.
Orders use a dated inventory projection, end-horizon net need and a timing requirement,
then explicit ordering/stock-unit rounding constraints. Statuses distinguish confirmed,
scenario and unavailable inputs. Full methods: [REPLENISHMENT_POLICY.md](REPLENISHMENT_POLICY.md).

The manager-facing dashboard is implemented in `app.py`, with pure review/scenario/export
helpers in `dashboard.py`. Streamlit is an optional project dependency. It loads completed,
hash-verified recommendation runs and the exact matching canonical product dimension.
Supplier/SKU/warehouse/unit remain separate; conflicting product names stay unresolved.
Scenario forms call the existing `recommend()` function and preserve original input evidence;
they do not modify files, refit forecasts or change core calculation policies.

Session state holds scenario overrides and selected draft lines. A scenario change removes
its old draft line. Manual quantities require a reason and obey the applied constraints.
Review records a fingerprint of the complete cart, reviewer name and UTC timestamp; any
cart change revokes export eligibility. CSV is supplier-sorted, UTF-8/BOM and protected
against formula injection. JSON includes the full input/calculation audit. This is local
draft review, not authenticated organizational authorization or supplier transmission.
Specific 1C-template compatibility and browser visual QA remain unverified.

`scripts/accept_case.py` executes synthetic cross-stage input sensitivity, joint seasonality/growth,
stockout-to-order correction, one-off injection through cleaning/forecast/order, and a two-supplier
AppTest review/export flow. It checks real example artifacts, all 12 registered workbook hashes,
and unchanged core/config/source hashes, and publishes a content-addressed local JSON report.
Case status: A PARTIAL (missing categories and unresolved coefficient scope), B PASS (synthetic),
C PASS (synthetic), D PARTIAL (document proxy without customer IDs), E PASS (local workflow).
`evidence.py` verifies the latest content-addressed acceptance report against the current
app, acceptance script, case definition, core/config/source fingerprints and referenced
recommendation/outlier artifacts. Missing/stale/corrupt reports do not yield green claims;
a failed latest report is shown rather than silently replaced with an earlier PASS.
`evidence_ui.py` renders the five **Case validation** cards and their saved calculation/chart
details without calling forecasting or order functions. Real-sales/scenario-input evidence
is labeled separately from synthetic mechanism tests. A/D remain PARTIAL; customer-level
grouping is not implemented and would require both anonymized IDs and a logic extension.
Buttons navigate to matching SKU calculations, forecast inputs and draft review without
changing scenario or cart state. A mismatched run disables real-example links.
See [the four-minute walkthrough](../DEMO.md), now entirely inside the dashboard.
86 tests pass; browser visual QA remains pending because no browser surface was available.

Next: visual rehearsal, partner input/export validation and order-quality evaluation.
Do not treat observed sales as latent demand. Model selection has not beaten the baseline
in all holdout segments; retain that limitation in product explanations.
Do not remove repeated catalog codes without resolving conflicts.

The implemented path is qty_raw/qty_regular/is_outlier/reason → forecasts → recommendations
with calculation components, assumptions, version, urgency, and explanation.
Recommended_Order v1 runs with explicit current-stock, horizon, units and shipment inputs;
the shipped real-data demonstration supplies labeled scenario assumptions where evidence is missing.
Check shortages before arrivals, not only the final inventory balance.
Do not conflate minimum quantity and order multiple. Any future LLM must explain computed numbers.
