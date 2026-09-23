# Vector — AI Procurement Copilot

HackAlem AI / Electrokomplekt. EDA, ingestion, cleaning, forecasting, and replenishment v1 are implemented.
Explicit-interval stockout correction is implemented; real intervals and current operational stock remain unavailable.
The interface is not implemented yet.

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

data/ and .venv/ are excluded from Git. The report is reproducible locally.
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
order methodology remains to be implemented.

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

Next stage: a dashboard for reviewing recommendations and assumptions, editing scenarios,
and exporting manager-approved drafts. No automatic supplier sending or LLM calculation.
