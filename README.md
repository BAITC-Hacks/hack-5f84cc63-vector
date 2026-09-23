# Vector — AI Procurement Copilot

HackAlem AI / Electrokomplekt. EDA, ingestion, cleaning, forecasting, and replenishment v1 are implemented.
Explicit-interval stockout correction is implemented; real intervals and current operational stock remain unavailable.
The local Streamlit workspace supports recommendation review, per-SKU scenarios,
supplier-grouped drafts, and reviewed CSV/JSON exports.

## Open the dashboard

```powershell
.venv/Scripts/python.exe -m pip install -e ".[dashboard]"
.venv/Scripts/python.exe -m streamlit run app.py
```

Open http://127.0.0.1:8501. A completed replenishment run is required; use the pipeline
commands below if none exists. The dashboard discovers completed runs, verifies the
recommendation hash/count, and defaults to the most recent distinct demonstration run.
The sidebar also offers strict explicit-input runs. Changing runs resets session drafts.

The interface defaults to **Russian**. Use **RU / EN** in the sidebar to switch languages.
All four pages, forms, table headings, chart labels and evidence summaries are localized
through `src/vector_pipeline/i18n.py`. Widget options keep stable internal IDs: switching
language preserves the selected run, filters, applied scenarios, draft lines and recorded
review. Reviewed CSV/JSON schemas, source names/SKU codes, audit keys and entered reasons
remain unchanged. Documentation and code remain English; source/audit JSON is not translated.
The workflow labels below use English; their Russian equivalents appear when RU is selected.

1. **Order planning:** filter by supplier, warehouse, unit, action, or SKU/product;
   select a table row to see its calculation, inventory projection, assumptions and source evidence.
2. **What-if scenario:** change available stock, lead/review times, safety days,
   future receipt delays or an additional dated receipt. Supply a reason and recalculate.
   Edits affect one SKU in the current session. The engine and frozen forecasts are unchanged.
3. Add selected recommendations to the draft. Manual quantities require a reason
   and must satisfy the applied minimum and rounding step. Remove a line to skip it.
4. **Draft review:** inspect supplier groups, enter a reviewer name, acknowledge the
   exact quantities and assumptions, then download CSV and the complete audit JSON.
   Editing a draft invalidates review; editing a scenario removes that SKU's stale draft line.
5. **Data coverage:** inspect unavailable quantities, missing inputs and measured limitations.
6. **Case validation:** scan five requirement cards with source/evidence labels, before/after
   quantities and honest PASS/PARTIAL statuses. Open the saved proofs, the matching SKU's
   calculation/forecast inputs, or draft review. No acceptance test runs during page rendering.

The default workspace is explicitly a **scenario**, not confirmed company purchasing needs.
The inventory chart shows the engine's recommendation, not a manually overridden draft quantity.
No quantity totals combine different units, and no currency savings are invented.
Product names are loaded only from the matching, verified canonical run; conflicts remain unresolved.

CSV is UTF-8 with BOM and formula-sensitive strings are escaped. JSON preserves exact
SKU identifiers, input evidence and calculation details. CSV compatibility with the
company's specific 1C template remains **unvalidated**; automatic Excel type inference
can alter numeric-looking identifiers, so import SKU columns as text or use JSON.
Review is a local acknowledgement, not authenticated corporate approval. Nothing is
sent to suppliers. Drafts/edits remain in browser-session memory until downloaded;
reloads, disconnection or run changes can discard them. The server binds to localhost
and Streamlit usage telemetry is disabled.

Install the dashboard extra before running the full test suite. UI tests use synthetic
fixtures and Streamlit AppTest; they do not approve real business orders. Set
`VECTOR_PROCESSED_ROOT` to use an alternate local processed-data directory.

## Run (PowerShell, Python 3.11+)

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -X utf8 scripts/eda.py
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.cli
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Source Excel directories: data/IEK/ and data/system_electric/. Run from the project root.
Workbooks are opened read-only; SHA-256 is checked before and after processing.
Do not replace source files during a run.

EDA outputs: data/processed/eda_summary.json and docs/DATA.md.
Rerunning EDA overwrites these generated reports. Alternative output paths:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/eda.py --output data/processed/eda_check.json --markdown data/processed/eda_check.md
```

data/processed/ and .venv/ are excluded from Git. The report is reproducible locally.
Do not add source sales data to a public repository.

## EDA methodology

All workbooks, sheets, and rows are analyzed for headers, types, dates, SKUs, nulls,
duplicates, totals, signs, and extreme numeric values. Codes are joined after trimming
whitespace, without removing leading zeros or underscores.
Totals and rows without codes are excluded from SKU profiles but retained in the report.
Signs are preserved; blanks do not become zeros. Formulas are read together with their
cached values, without recalculation. Empty XML cells of type e are tracked separately as nulls.

MOQ coverage distinguishes absent codes, invalid values, and positive values.
Monthly quantities are compared with transactions only on observed numeric pairs.
Column-level 3×IQR diagnostics mix SKUs and units: they are not a production algorithm
for excluding one-off orders. Document-level screening is implemented separately below;
order methodology is documented in [Replenishment policy](docs/REPLENISHMENT_POLICY.md).

## Findings

12 files do not constitute a complete input set: some SE filenames do not match their
contents; two files are copies of MOQ, and the reported SE master table was not found.

[Findings](docs/DATA_FINDINGS.md) · [Questions](docs/OPEN_QUESTIONS.md) ·
[Schema](docs/ARCHITECTURE.md) · [Plan](docs/TODO.md) · [Requirements](docs/CASE.md).

## Supplier ingestion

`config/sources.json` contains 13 reviewed sheet mappings across 12 files, including
the explicitly excluded identical SE MOQ copy. Source SHA-256 and headers must match;
new, missing, or changed workbooks require a reviewed registry update. No role is inferred
from a filename at runtime. Changing filenames alone also requires a path update.

`IEKAdapter` and `SystemElectricAdapter` produce shared, validated JSONL contracts.
Each run creates a new directory under `data/processed/canonical/`; the CLI prints its path.
Existing runs are not overwritten. `ingestion_summary.json` is written only after
the run completes and source hashes have been rechecked. Failed runs contain `FAILED.json`.

Outputs include products, product observations, transactions, monthly measures,
inventory snapshots, shipment cells, order constraints, aggregate measures,
source coefficients, source controls, quarantine, and manifest/schema snapshots.
See [implementation details](docs/ARCHITECTURE.md#implemented-ingestion-contract).

Validate a completed run independently against the EDA profiles:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/verify_ingestion.py data/processed/canonical/<run-directory>
```

Replace `<run-directory>` with the directory printed by ingestion. Validation checks
artifact/source hashes, schema types, IDs, product references, source exclusion, and
column-level numeric counts, sums, signs, and missing values.

## Explicit assumptions

`config/ingestion.json` keeps lead times, review periods, and service levels unresolved
by default. Ingestion succeeds without inventing these inputs. Policy values are recorded
for later calculation stages; ingestion does not use them to calculate orders.

To explore a monthly interpretation, add an explicit entry to `source_overrides`:

```json
{
  "se_monthly_unspecified": {
    "measure_kind": {
      "value": "inventory",
      "status": "assumption",
      "id": "SE-INVENTORY-DEMO",
      "reason": "Temporary interpretation for a labeled demo; not confirmed by the supplier"
    }
  }
}
```

This is an example, **not enabled in the shipped configuration**. The source remains
unknown by default. `snapshot_semantics` can similarly be `beginning`, `end`, or `unknown`;
`constraint_kind` can be `minimum`, `multiple`, or `unknown`. Overrides require an ID,
reason, and `assumption`/`confirmed` status; confirmed values require evidence.
Assumed values propagate `assumption_ids` and `assumption_applied` to affected records.
Confirmations and the effective settings remain in each run's report.

## Demand cleaning and outlier detection v1

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.demand_cli data/processed/canonical/<run-directory>
```

Use a completed canonical run. Outputs are written to a new `data/processed/demand/`
directory: cleaned transactions, document-level demand, flagged documents, per-source
monthly reconciliation, and a summary with counts, examples, configuration, and hashes.

Transactions are the primary **observed-sales** source; monthly tables are reference-only.
An explainable median/IQR/MAD detector uses earlier days within each SKU/warehouse/unit.
It preserves sparse-history sales and recurring large orders, and excludes flagged
one-off candidates from a separate `qty_regular` measure while retaining raw quantities.
Unresolved negatives and invalid documents retain null regular quantities.

Thresholds and minimum history are in `config/demand.json`. See
[Demand policy](docs/DEMAND_POLICY.md) for the formula, source choices, recurrence rule,
assumptions, reconciliation semantics, local results, limitations, and reproduction details.
`scripts/verify_demand.py` independently audits a run and can compare repeated outputs.
Flags have no business-confirmed labels yet; document IDs do not identify customers.

## Forecasting v1

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.forecast_cli data/processed/demand/<demand-run>
.venv/Scripts/python.exe -X utf8 scripts/verify_forecast.py data/processed/forecast/<forecast-run>
```

Configuration: `config/forecast.json`, with an explicit cutoff of 2026-09-22.
Models: three-month mean, EWMA, trend with seasonality when history supports it, and
seasonal naive. Last-value naive is an additional benchmark. Rolling-origin selection
excludes the final August holdout. Missing months are unknown, and September is excluded
as partial. Forecasts cover October–December in each SKU's warehouse and stock unit.

Outputs include monthly series, coverage/exclusion reasons, model scores, historical
predictions, the forecast table with both selected and baseline quantities, and a summary.
See [Forecast policy and measured results](docs/FORECAST_POLICY.md).

On the current data, 992 of 2,716 transaction-observed series are forecastable.
The selected policy beats the mean baseline for IEK meters/packs but loses for IEK/SE
pieces on the August holdout. This limitation is reported explicitly; the model is not
claimed to improve every segment. No inventory or order-quality improvement is established.

## Replenishment v1

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.replenishment_cli data/processed/forecast/<forecast-run> data/processed/canonical/<canonical-run> --demo
```

This explicitly enables an October 1 planning scenario with real forecasts and labeled
synthetic stock/lead-time inputs. It produces 992 scenario recommendations on the current
data, with formulas, dated supply, shortage dates, rounding rules, and ten readable examples.
Without `--demo` or `--inputs`, missing operational inputs produce unavailable quantities.
Outputs are under `data/processed/replenishment/`; open `examples.md` for the first ten orders.

Stockout correction accepts explicit intervals and matching daily observations, estimates
lost sales from earlier history, and refits the frozen forecast model. Real intervals are
absent; synthetic tests demonstrate the correction. Forecast v1 was not tuned in this stage.
See [Replenishment policy, inputs and verified results](docs/REPLENISHMENT_POLICY.md).

The dashboard uses these outputs directly; see the launch and review workflow above.
Next: rehearse the complete case demo, validate the partner's operational inputs and
1C import template, and measure order quality under an explicit historical evaluation protocol.
No automatic supplier sending or LLM calculation is implemented.

## Case acceptance and jury walkthrough

```powershell
.venv/Scripts/python.exe -X utf8 scripts/accept_case.py
```

This runs the existing cleaning, forecast, stockout, order and dashboard code on explicit
synthetic scenarios, verifies real demo examples, and checks all 12 registered workbook
hashes and unchanged core/config/source files. The command prints a compact result and
writes `data/processed/acceptance/<report-hash>/case_acceptance.json`. Identical reruns
produce the same report. Use `--replenishment` and `--demand` to select the audited runs;
the default run IDs are documented in the policy files. Failure of an executable check
returns a nonzero exit code. PARTIAL means a known requirement/evidence gap, not a failed test.

| Must-have | Result | Evidence boundary |
|---|---|---|
| Base replenishment | PARTIAL | Numeric inputs affect orders correctly; category data and validated external coefficient applicability are missing. |
| Seasonality and growth | PASS | Joint mechanism verified on 36 synthetic months; no new real-data superiority claim. |
| Stockout compensation | PASS | Synthetic intervals increase the order from 67 to 104; real intervals remain absent. |
| One-off exclusion | PARTIAL | A 9,000-unit injection leaves the regular order at 63 instead of 1,224 unscreened; client-level detection cannot be established without customer IDs. |
| Explained supplier-grouped reviewed orders | PASS | Two-supplier AppTest verifies manual adjustment, review, CSV/JSON and invalidation; local review only. |

[DEMO.md](DEMO.md) gives a 3–5 minute script with exact clicks and expected values.
The demonstration now stays inside the dashboard: **Case validation** reads the latest
acceptance report and provides seasonal, stockout, anomaly and review evidence below its
five summary cards. It verifies the report content hash, current code/config/source hashes,
and referenced data artifacts. Missing, stale or corrupt reports show a regeneration command;
a failed latest run never falls back to an older PASS. Re-run acceptance after code changes.
Recorded examples remain separate from current session edits. Links to real SKU calculations
are disabled when the selected calculation snapshot differs from the audited one.

91 tests pass, including evidence integrity/staleness, status rendering, proof controls,
workflow navigation, translation coverage, placeholder parity and RU/EN switching with
unchanged scenario/review/export state. Browser visual QA, the exact
1C template, and operational data confirmation remain open. No new model or LLM was added.
