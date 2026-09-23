# Observed-sales cleaning and one-off screening v1

This stage produces a regular observed-sales measure. It does not estimate latent
demand, compensate for stockouts, forecast, or calculate orders. A flagged document
is an explainable one-off **candidate**, not a verified customer classification.

## Sources

The explicit policy is `config/demand.json`; selection uses reviewed source IDs.

| Supplier | Primary transaction source | Original sheet |
|---|---|---|
| IEK | `iek_transactions`: `data/IEK/Динамика продаж_2025-2026.xlsx` | "Лист_1" |
| SystemElectric | `se_transactions`: `data/system_electric/Ежемесячные остатки SystemElectric 2024-2026.xlsx` | "Лист_1" |

Monthly measures are reconciliation-only. No monthly fallback is enabled, even for
SKUs absent from transactions. Validating scope and semantics is a prerequisite to
implementing a fallback. Source filenames never decide precedence. Any unexpected
transaction source fails the run instead of being silently added or dropped.

## Transaction and document rules

- Retain every canonical transaction, its raw quantity/source value, source coordinates,
  quality flags, and synthetic marker. Never apply `abs()` to sales quantities.
- Record positive, negative, zero, and missing quantity states separately.
- Negative quantities remain unresolved return/correction candidates with
  `qty_regular=null`. They are not subtracted from regular sales or made positive.
- Missing/invalid quantities, invalid dates, quarantined rows, totals, and unresolved
  exact duplicates cannot enter regular sales or the detector baseline. No zero imputation.
- Group by supplier + source + SKU + document number + calendar date + warehouse + unit.
  Document text is preserved in transactions but is not a customer ID or an identifier fallback.
  A missing document number creates a singleton keyed by the transaction record ID.
- A document containing an invalid/missing/negative line has no regular quantity for
  the entire group; computing a positive subtotal would conceal an incomplete document.
  A clean zero-only document retains zero and does not train the positive-order baseline.
- Unknown warehouse/unit groups retain valid positive sales with
  `unresolved_granularity`, without statistical screening or baseline learning.
- Flagged documents remain in the output. Their positive quantity is fully excluded
  from `qty_regular` (set to zero); no winsorized amount is silently substituted.
  All other eligible positive rows retain their original quantity, including sparse history.

Document counts in reports mean **document/SKU/warehouse/unit/day groups**, not unique
invoices. On the current source set each group happens to contain one source row.

## Causal robust baseline

For each supplier/SKU/warehouse/unit, use at most 200 eligible positive documents
from the previous 365 calendar days. Only dates strictly before the document date
are eligible. Classify all documents from a day before adding that day's observations.
This prevents same-day or partial-document leakage and makes input row order irrelevant.
The history capacity uses stable document-ID order for ties on the oldest retained day.

Minimum history: 20 documents on at least five distinct days. Below either threshold,
preserve the positive sale and assign `insufficient_history`. No category or global
quantity fallback mixes units or products. All thresholds are configurable assumptions.

For sorted past quantities, compute median M, linearly interpolated Q1 and Q3,
and MAD = median of absolute deviations from M. The absolute value here applies to
deviations, not signed sales. The upper threshold is:

```text
T = max(8 * M, Q3 + 6 * (Q3 - Q1), M + 8 * 1.4826 * MAD)
outlier_score = current_document_quantity / T
```

A quantity must strictly exceed every fence. Zero IQR/MAD is valid: the median ratio
still supplies a positive fence. Scores are ratios, not probabilities or calibrated risk.

Before flagging, check comparable prior orders between half and twice the current
quantity. If at least three such documents occur on three prior days in the retained
history, keep the quantity and label `repeated_large_order`. Otherwise flag it as
`flagged_one_off_candidate`. A routinely large SKU can also remain `regular` because
its median/IQR already reflects those quantities.

Raw quantities of previously flagged, otherwise valid documents stay in history.
This allows recurring bulk orders to become recognized rather than being excluded
forever. Earlier decisions are never retroactively revised using later recurrence.
The first large orders in a newly emerging pattern can therefore still be flagged.

Each scored group exports baseline dates/counts, median, quartiles, MAD, all three
fences, comparable-order counts, score, and a reason. Missing history exports counts
and dates without fabricated statistics.

## Reconciliation

Compare each monthly source separately at supplier/SKU/month grain. Export transaction
breakdowns by warehouse/unit with signed numeric raw totals, regular observed totals,
negative-row counts, missing quantities, and unresolved regular-row counts.

Numeric differences use monthly quantity minus signed observed transactions. Raw
negative signs are intentionally retained in this diagnostic; cleaning does not
rewrite the reference. Mixed/unknown transaction units or incompatible declared
dimensions block scalar comparison. Unknown monthly units allow a labeled numeric
diagnostic only, never a claim that the units match. Unknown monthly warehouse scope
is also flagged. Totals never sum quantities across different units.

Blank monthly values remain null. Absent transactions remain unobserved, not zero.
Reports distinguish matches, differences, missing monthly quantities, absent transaction
observations, unavailable transaction quantities, dimension conflicts, and transaction-only
keys within each reference source's period range. All comparisons retain scope and
coverage warnings; the last observed transaction month has a possible-partial-month flag.
Numeric agreement alone does not validate a source as sales or inventory.

## Assumptions and limitations

| Assumption ID | Meaning |
|---|---|
| DEMAND-POSITIVE-OBSERVED-V1 | Eligible positive quantities represent observed sales; absence of sales does not prove zero demand. |
| DEMAND-NEGATIVE-UNRESOLVED-V1 | Keep negatives unresolved and outside regular sales until semantics are confirmed. |
| DEMAND-DOCUMENT-PROXY-V1 | Document/SKU groups proxy large purchases; document identity is not customer identity. |
| DEMAND-ROBUST-THRESHOLDS-V1 | History limits, robust fences, recurrence rule, and full exclusion are provisional screening policy. |
| DEMAND-EVENT-DATE-AVAILABILITY-V1 | Event dates proxy availability because actual posting/ingestion timestamps are absent. |

These IDs and inherited ingestion assumptions are attached to cleaned rows and groups.
No real client-level acceptance claim is possible without anonymized customer IDs.
No business-labeled one-off dataset exists, so precision/recall cannot be reported.
True project orders may be flagged; repeated exceptional orders may be retained.
Sparse SKUs deliberately remain unfiltered. Growth, seasonality, and stockouts are not
modeled in this stage. Event-date causality does not prove historical availability of
backdated corrections. Original monthly/transaction discrepancies remain unresolved.

## Reproduction and artifacts

```powershell
.venv/Scripts/python.exe -X utf8 -m vector_pipeline.demand_cli data/processed/canonical/<run-directory>
```

New output directory: `data/processed/demand/<run-id>/`. It contains
`cleaned_transactions.jsonl`, `document_demand.jsonl`, `outlier_records.jsonl`,
`reconciliation.jsonl`, and `demand_summary.json`. The summary captures source choices,
effective configuration, input/code hashes, output hashes/counts, supplier metrics,
real examples, and reconciliation status counts. No Excel file is opened or changed.
Canonical contracts remain unchanged. Existing run directories are never overwritten;
failed runs have `FAILED.json` and no complete summary.

Outputs and summary are byte-equivalent for the same inputs, config, and code; only
the containing directory name changes. Synthetic tests cover extreme injection,
normal/repeated bulk orders, zero dispersion, sparse history, no future/same-day
leakage, grouping, invalid rows, negatives, unit separation, reconciliation, artifact
tampering, and deterministic reruns.

Audit a run independently and optionally compare a repeated run:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/verify_demand.py data/processed/canonical/<canonical-run> data/processed/demand/<demand-run> --compare data/processed/demand/<second-demand-run>
```

The audit checks hashes/counts, original-field preservation, document membership and
quantity conservation, exclusion rules, baseline dates, robust fences, recurrence
evidence, summary counts, and byte-equivalence. It writes `verification.json`.

## Reviewed local results (23 September 2026)

Input: canonical run `20260923T093014Z-bbefd1bc`, with the shipped demand configuration.

| Metric | IEK | SystemElectric |
|---|---:|---:|
| Document/SKU groups | 171,603 | 77,312 |
| Groups with sufficient history for statistical scoring | 143,938 | 68,262 |
| Flagged candidates | 918 | 451 |
| Flagged share of all groups | 0.535% | 0.583% |
| Flagged share of scored groups | 0.638% | 0.661% |
| Insufficient history, sale retained | 27,532 | 8,735 |
| Above-fence recurring quantities retained | 2,620 | 1,264 |
| Unresolved negative quantities | 115 | 302 |
| Missing quantities | 18 | 13 |

The source set yields one transaction per document/SKU group; multiple lines are
covered in synthetic tests. In total, 1,369 candidates are flagged. No quantity was
converted with `abs()` and no sparse-history sale was automatically excluded.

Numeric reconciliation independently reproduces the EDA overlap results:

| Monthly source | Numeric pairs | Matches | Differences |
|---|---:|---:|---:|
| iek_monthly_quantities | 22,250 | 14,505 | 7,745 |
| se_monthly_quantities (SKU sheet in "Сезонность") | 7,020 | 2,669 | 4,351 |
| se_monthly_unspecified (701-SKU table) | 7,263 | 32 | 7,231 |

These diagnostics do not establish which monthly source is correct. Full examples,
including source coordinates and baseline statistics, remain in the local summary;
row-level business data is excluded from Git.

Validation: 38 tests passed. Full runs `050e703d461b` and `f802073e92f5` produced
byte-identical artifacts and summaries. The independent audit of all 248,915
transaction/document groups passed; evidence is `verification.json` in the first run.
