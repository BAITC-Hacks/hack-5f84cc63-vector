# Project instructions

## Goal
AI Procurement Copilot — HackAlem AI / Electrokomplekt: explainable inventory replenishment recommendations for IEK and SystemElectric.

## Context
Before working, read `docs/CASE.md`, `docs/ARCHITECTURE.md`, and `docs/TODO.md`.
Before changing ingestion, cleaning, forecasting, or order calculations, also read
`docs/DATA.md`, `docs/DATA_FINDINGS.md`, and `docs/OPEN_QUESTIONS.md`.
Respect evidence status: an imported EDA report is not equivalent to local verification.
Record new checks with the source file, sheet, method, and result.

## Rules
- Deterministic code calculates order quantities; the LLM only explains the results.
- Never send an order to a supplier automatically: the manager reviews and approves the draft.
- Do not modify source Excel files in `data/IEK/` or `data/system_electric/`.
- Separate supplier adapters must produce a shared internal schema.
- Never infer column semantics from filenames; never apply a global `abs()` to sales.
- Remove transaction totals before statistics; preserve raw values and correction reasons.
- Never silently default MOQ to 1. Expose assumptions in data, calculations, and the interface.
- Document unknown business rules in `docs/OPEN_QUESTIONS.md` and make them configurable.
- Priority: calculation correctness → interface → LLM; avoid unnecessary infrastructure.
- Update documentation and TODO when decisions or implementation status change.
- Use English for documentation, code, comments, tests, variable names, and commit messages.
  Preserve original source filenames, sheet names, column labels, and source values verbatim.
