# Work plan

- [x] Record the goal; keep AGENTS.md stable.
- [x] Align CASE.md with the complete case: five must-haves; MOQ is optional.
- [x] Analyze all 12 Excel files / 13 sheets.
- [x] Create pyproject.toml, scripts/eda.py, and run instructions.
- [x] Save data/processed/eda_summary.json and verified DATA.md.
- [x] Check signs, nulls, totals, duplicates, formulas, MOQ, and SKU intersections.
- [x] Record SE filename/content mismatches and source discrepancies.
- [x] Propose the canonical schema in ARCHITECTURE.md.

Next stage: adapters and canonical tables, not another general EDA.

- [ ] Resolve P0: SE master/semantics, current stock, lead times, source precedence.
- [ ] Create the source registry and IEK/SE adapters with validation.
- [ ] Save normalized tables and problematic rows with provenance.
- [ ] Agree on negative-quantity, blank-month, and duplicate policies.
- [ ] Prepare synthetic acceptance scenarios for the five must-haves.
- [ ] Implement one-off order handling and stockout estimation; agree on correction/forecast order.
- [ ] Specify a baseline and Recommended_Order v1 with scenario parameters.
- [ ] Test history without future leakage and verify the influence of every input.
- [ ] Add the interface, manager review, and compatible export.
- [ ] Add an LLM after the core calculation, if needed.
