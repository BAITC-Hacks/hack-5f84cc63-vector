# Forecasting v1: regular observed sales

Implemented on 23 September 2026. This is a monthly forecast of cleaned observed
sales, not latent demand or an order recommendation. No stockout correction, safety
stock, replenishment, UI, or LLM is implemented here. Ingestion and cleaning contracts
are unchanged. Configuration: `config/forecast.json`.

## Calendar, scope, and missing values

- Input is the completed demand run's `cleaned_transactions.jsonl`. Verify its hash,
  source selection, unique row IDs, row count, and nonnegative-or-null regular quantity.
- Grain: supplier + SKU + warehouse + stock unit. Never pool quantities or errors
  across different units. The coverage universe contains transaction-observed series,
  not catalog-only SKUs with no transaction observations.
- The supplied cutoff is **2026-09-22**, independent of the computer's current date.
  Later rows are ignored before building the series universe. Current-month transactions
  remain in monthly diagnostics but never enter training or validation as full months.
- Source coverage from January 2025 is an explicit, traceable assumption based on
  observed positive history, not a supplier confirmation that the export is complete.
  Earlier corrections are retained as outside-coverage diagnostics.
- An observed month is numeric only if every transaction in that group has a resolved
  `qty_regular`. Unresolved negatives/missing quantities invalidate the monthly target;
  the known subtotal stays visible. One observed row does not prove full export coverage.
- A month without records is unknown, not zero. A month containing valid transactions
  whose regular quantities sum to zero is an observed zero. Unknown months break history;
  no interpolation, carry-forward, or zero fill is performed.
- At least three consecutive resolved months ending in August are required for a
  production forecast. Missing dimensions, undated records, mixed synthetic/real data,
  or unresolved quantities within those three months exclude the series for data quality.
  Other short/gapped histories receive `insufficient_history`, with no fabricated forecast.

## Models and interpretable components

All quantities remain unrounded, nonnegative stock units. Rounding to purchasing
units or MOQ belongs to replenishment, not forecasting.

| Model | Method |
|---|---|
| `mean` | Operational baseline: mean of the last three resolved consecutive months. |
| `ewma` | Exponential smoothing with fixed alpha 0.4, initialized from the first training observation. Future level is constant. |
| `trend_seasonal` | With 6–23 months, median pairwise slopes over the last six months plus a median intercept. With at least 24 months, median annual differences estimate growth, then average detrended levels by calendar phase estimate seasonality. |
| `seasonal_naive` | Repeat the previous annual cycle; requires at least 12 training months at every validation origin. |
| `naive` | Additional last-observation benchmark. It is reported alongside every model on identical evaluation pairs, without changing model selection. |

The trend slope is capped at 25% of the recent mean per month, in either direction;
forecasts are floored at zero. The cap, history windows, alpha, and selection thresholds
are explicit provisional parameters, not fitted using the holdout. Annual seasonality
and growth are components of the forecast, never additions applied afterward.

The real sources provide at most 20 consecutive eligible months by August 2026.
Consequently, `trend_seasonal` uses its **trend-only** branch on real data. Learned
two-cycle seasonality plus growth is validated on clearly labeled synthetic scenarios.
`seasonal_naive` can still use a previous real annual cycle where coverage permits.
Portfolio coefficients from the source workbooks are not applied: their SKU applicability
and historical availability remain unresolved. This stage does not silently assume they
were available at historical origins.

## Selection and untouched evaluation

August 2026 is the fixed final holdout month for every SKU. If its target is missing,
report an unavailable holdout; do not move it to an earlier convenient period.

1. Use only history through July for selection. From its trailing consecutive block,
   take the latest four origins allowing all four subsequent target months to end by July.
   For the full January 2025–July 2026 history, origins are December 2025–March 2026.
2. At each origin, fit on its prefix only and predict horizons 1–4. Every candidate must
   support all the same origins and targets. Otherwise mark it unavailable; never compare
   candidates on different periods within one SKU's selection.
3. Select a challenger only if pooled MAE improves on the three-month mean by at least
   5% and its mean absolute error is no worse on at least three of the four origins.
   Choose the lowest-MAE qualifying model; deterministic tie order is declared in code.
   Otherwise retain `mean`. Too few origins also retain `mean` with an explicit fallback.
4. Freeze the selected model. Fit through July and evaluate August once. Report all
   candidate benchmarks for diagnosis, but never use their August ranking to choose a model.
5. Refit the frozen choice using resolved history through August for production.
   September is excluded. Forecast **October–December**, which are horizons 2–4 from
   the training end. These are full future months, not a forecast of remaining September.

Rolling validation covers horizons 1–4; independent final holdout covers **horizon 1
only**. Origin windows overlap, so the four folds are not independent statistical
replicates. The selection gate is a reproducible heuristic, not proof of superiority.
Holdout results below were reported without retuning the model or thresholds against them.

## Metrics and coverage

`MAE = mean(abs(forecast - actual))`; `Bias = mean(forecast - actual)`.
Positive bias means overprediction. `WAPE = sum(abs(error)) / sum(abs(actual))`,
stored as a ratio; zero denominator yields null, never a fabricated zero error.
MAE and bias use stock units. WAPE weights high-volume observations; interpret it with
MAE, bias, and coverage, not as an average SKU percentage error.

Reports group metrics by supplier and unit. Each candidate is compared with both
the operational mean and last-value naive on exactly the same series/origin/target
pairs. Different models may have different *reported* coverage because unavailable
models cannot predict short histories; their paired baseline makes that difference visible.
The selected-policy holdout includes all eligible selected predictions, including
baseline fallbacks. Its win share includes ties, so a high win share can coexist with
worse aggregate MAE when a few errors are large.

## Verified local results

Input demand run: `050e703d461b`; final forecast runs: `6933ebced13e` and `fc15984841e5`.

| Coverage | IEK | SystemElectric |
|---|---:|---:|
| Total transaction-observed SKU-series | 2,151 | 565 |
| Forecastable | 744 | 248 |
| Insufficient or gapped history | 1,401 | 313 |
| Excluded for unresolved data quality | 6 | 4 |
| Independently evaluated on August | 618 | 225 |

Total: **992 forecastable series, 2,976 forecasts**. Selected models among forecastable
series: mean 752, EWMA 128, seasonal naive 76, trend-only 36. Of the mean choices,
395 have insufficient rolling-validation history; forecasts do not imply validated accuracy.

August holdout, same evaluated series for all columns in each row:

| Segment | Series | Selected MAE | Mean baseline MAE | Last-value naive MAE | Selected WAPE | Selected bias |
|---|---:|---:|---:|---:|---:|---:|
| IEK / "шт" | 546 | 63.94 | 60.13 | 62.89 | 31.69% | -13.12 |
| IEK / "м" | 43 | 357.57 | 392.50 | 698.05 | 46.07% | -2.68 |
| IEK / "упак" | 29 | 26.24 | 33.60 | 28.76 | 22.70% | -18.14 |
| SystemElectric / "шт" | 225 | 350.74 | 302.70 | 306.65 | 32.03% | +86.20 |

**The selected policy is not universally better.** It improves MAE versus the mean
by 8.90% for IEK meters and 21.90% for packs, but worsens it by 6.34% for IEK pieces
and 15.87% for SE pieces. Do not present per-SKU selection as an established improvement
for these latter segments, or treat the forecast table as an approved order plan.
Both selected and mean-baseline quantities remain in every forecast row for review.
Further changes would make August development data; a fresh untouched evaluation or
properly nested chronological evaluation would then be required for a new quality claim.

54 tests passed, including synthetic seasonality and growth, holdout perturbation,
origin causality, missing/partial months, negative quantities, sparse fallback, units,
zero-denominator metrics, integrity checks, and deterministic runs. An audit reconstructed
the history for all **49,911 historical predictions** and **2,976 production forecasts**,
replayed them, checked all 2,716 coverage records, and confirmed byte-equivalent reruns.
This verifies computation and chronology, not the unavailable business truth about demand.

## Run and output contract

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.forecast_cli data/processed/demand/<demand-run>
.venv/Scripts/python.exe -X utf8 scripts/verify_forecast.py data/processed/forecast/<forecast-run> --compare data/processed/forecast/<second-run>
```

`--config` accepts a versioned policy file; `--output-dir` must name a new child of
`data/processed/`. No existing run or raw Excel file is overwritten.

- `monthly_series.jsonl`: monthly targets, known/raw subtotals, row counts, missingness,
  outlier counts, source IDs and inherited assumptions, including partial-month diagnostics.
- `coverage.jsonl`: every observed series, eligibility, exclusion reasons, history length,
  frozen model choice, selection reason, and holdout availability.
- `model_scores.jsonl`: common-origin candidate metrics and selection gates.
- `backtest.jsonl`: every validation/holdout prediction, actual, origin, horizon, training
  start, role, source IDs, and whether a holdout model was selected before evaluation.
- `forecasts.jsonl`: SKU, supplier, warehouse, unit, target month, training boundaries,
  model, forecast_qty, baseline_forecast_qty, fitted components, and assumptions.
- `forecast_summary.json`: coverage, paired metrics, good/bad holdout examples, config,
  input/code/output hashes, source choices, and limitations. Audit adds `verification.json`.

Assumptions: `FORECAST-OBSERVED-SALES-V1` (sales are not latent demand),
`FORECAST-MONTH-COVERAGE-V1` (resolved observed months proxy full-month export totals),
`FORECAST-MODEL-POLICY-V1` (fixed modeling/selection parameters), and per-source coverage
IDs from config. Actual posting timestamps and observed stockout intervals remain missing.
