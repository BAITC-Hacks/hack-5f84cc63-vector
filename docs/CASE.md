# HACKALEM AI / Electrokomplekt case

Source: the complete information package pasted by the user on 23.09.2026.
These are case requirements; availability of the listed inputs in local Excel files is checked separately.

## Goal and user
Automatically calculate supplier replenishment orders. The user is a procurement
manager who runs a calculation by warehouse or category, reviews recommendations,
adjusts them, and approves an order. The value is reduced excess stock, shortages, and lost sales.

## Required capabilities and acceptance checks

| Requirement | MVP acceptance check |
|---|---|
| Per-SKU need incorporating history, current stock, incoming goods, categories, and growth forecast | Changing each applicable input affects the calculation; the explanation shows its contribution. Rounding and zero need can mask small changes in final quantity, so also check intermediate values. |
| Seasonality and sustained growth | On a seasonal test series, the forecast reproduces the seasonal pattern and distinguishes growth from a one-off spike. |
| Stockout demand compensation | With confirmed product unavailability, estimated need exceeds the calculation based on raw sales. |
| Exclusion of one-off large orders, including large purchases by one customer | Injecting a large one-off order into a test dataset does not substantially increase regular order quantities; define the threshold for “substantial” before testing. |
| Supplier-grouped orders with per-item explanations | SKU/article, supplier, quantity, explanation, urgency; viewing and export. |

Case inputs: sales with date, article, quantity, anonymized customer, and price;
warehouse stock; stockout periods; suppliers and lead times; the 1C material statement.
Categories and growth forecasts are also named in the required calculation.

## Optional
Shortage-risk prioritization, MOQ/supplier terms, category charts, and automatic
export/distribution. Analyze the MOQ files, but do not let this feature displace the
required calculation. Per-row urgency and an advanced risk ranking are different levels of functionality.

## Constraints
- Sending orders to suppliers without approval by the responsible employee is prohibited.
- Using non-anonymized customer data in calculations is prohibited.
- Privacy, explainability, robustness to anomalies, and accounting-system export compatibility are required.
- Realistically distributed synthetic data is allowed for development and testing.
  Label it separately; never present it as real customer data or evidence of historical performance.

## Deliverables
A repository and README documenting calculation methodology, the outlier exclusion algorithm, and run instructions.
An LLM and Streamlit are not standalone mandatory case requirements.
