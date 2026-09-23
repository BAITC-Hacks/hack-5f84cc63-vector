# Work plan

- [x] Record the goal; keep AGENTS.md stable.
- [x] Align CASE.md with the complete case: five must-haves; MOQ is optional.
- [x] Analyze all 12 Excel files / 13 sheets.
- [x] Create pyproject.toml, scripts/eda.py, and run instructions.
- [x] Save data/processed/eda_summary.json and verified DATA.md.
- [x] Check signs, nulls, totals, duplicates, formulas, MOQ, and SKU intersections.
- [x] Record SE filename/content mismatches and source discrepancies.
- [x] Propose the canonical schema in ARCHITECTURE.md.
- [x] Translate all eight project documents and the DATA.md generator to English.
- [x] Record the English-language convention while preserving source labels and values.

Current stage complete: Replenishment v1 and explicit-interval stockout correction.

- [ ] Resolve P0: SE master/semantics, current stock, lead times, source precedence.
- [x] Create the source registry and IEK/SE adapters with validation.
- [x] Save normalized tables and problematic rows with provenance.
- [x] Add explicit, traceable configuration overrides without inventing default business values.
- [x] Run on all reviewed sources; exclude the identical MOQ copy and preserve 26 source conflicts.
- [x] Pass 19 unit tests and 153 independent table/column checks against EDA, including source hashes and product references.
- [x] Implement explicit observed-sales precedence and reconciliation-only monthly sources.
- [x] Preserve negative/missing quantities and isolate invalid, quarantined, total, and unresolved duplicate rows.
- [x] Implement configurable document-level robust screening with prior-day history and recurrence checks.
- [x] Preserve sparse-history sales with insufficient_history; retain warehouse/unit granularity.
- [x] Add synthetic cleaning acceptance tests, no-future-leakage checks, and deterministic rerun checks.
- [x] Pass 38 tests and audit all 248,915 real transaction/document groups; preserve 417 negatives and 31 blanks.
- [x] Reproduce all four output artifacts and the summary byte-for-byte in two full runs; flag 918 IEK and 451 SE candidates.
- [ ] Confirm negative-quantity, blank-month, and duplicate business semantics; provisional policies are documented.
- [ ] Prepare synthetic acceptance scenarios for the five must-haves.
- [x] Implement one-off document candidates with separate raw/regular quantities and explanations.
- [ ] Validate candidate labels with procurement staff and anonymized customer data when available.
- [x] Implement explicit-interval stockout estimation before frozen-model production refit; synthetic acceptance passes.
- [ ] Obtain real stockout intervals and validate real lost-demand estimates; no intervals are fabricated.
- [x] Implement monthly observed-sales forecasts with explicit missing/partial-month policy and coverage reporting.
- [x] Compare mean, EWMA, trend/seasonality, and seasonal naive; include last-value naive benchmark.
- [x] Select on common rolling origins and keep final holdout out of model selection; preserve baseline fallback.
- [x] Run on real data: 992 forecastable series, 2,976 forecasts, 843 holdout-evaluated series.
- [x] Pass 54 tests; replay 49,911 historical predictions and 2,976 forecasts; verify identical full reruns.
- [x] Report holdout regressions honestly: selected policy loses to mean for IEK/SE pieces; no holdout-driven retuning.
- [ ] Improve model selection only with a fresh untouched evaluation or nested historical protocol; do not reuse August as untouched after tuning.
- [x] Implement Recommended_Order v1 with dated supply, safety days, timing requirements and explicit constraints.
- [x] Generate 992 scenario recommendations and ten explained examples; strict mode leaves unavailable inputs unresolved.
- [x] Pass 72 tests and audit all 992 recommendation calculations; full reruns match.
- [ ] Test history without future leakage and verify the influence of every input.
- [ ] Add the interface, manager review, and compatible export.
- [ ] Add an LLM after the core calculation, if needed.
