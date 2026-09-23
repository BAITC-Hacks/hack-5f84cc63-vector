# Verified EDA findings

Verified on 23.09.2026: 12 Excel files, 13 sheets, all rows. Script: scripts/eda.py.
Evidence: data/processed/eda_summary.json (SHA-256, coordinates, and profiles);
map: [DATA.md](DATA.md). Sources were not modified; formulas and caches were read without recalculation.
Findings apply to the current local versions, not every file mentioned in earlier discussions.

## SystemElectric filenames do not match their contents

| File in data/system_electric/ | Verified contents |
|---|---|
| MOQ SystemElectric.xlsx | "Лист_1": 554 SKUs, code, article, order multiple |
| Динамика продаж_Syseme Electric_2025-2026.xlsx | Byte-identical copy of MOQ; SHA-256 matches |
| Ежемесячные остатки SystemElectric 2024-2026.xlsx | "Лист_1": transactions with date, document, warehouse, quantity |
| Ежемесячные продажи в кол-м выражении SystemElectric 2024-2026.xlsx | "Лист_1": 701 SKUs × 33 months; the meaning of the quantities is not labeled |
| Сезонность SystemElectric 2024-2026.xlsx | "Лист_1": 554 SKUs × 33 months, order multiple, total; "Лист1": aggregate seasonality |
| Товар в пути_SystemElectric на 22.09.2026.xlsx | "Лист1": aggregate seasonality, without SKUs or shipments |

The cause of these mismatches is unresolved. Do not automatically rename source files:
use a source registry based on contents.
The SE master with SKU growth, SKU seasonality, reserved/free stock, and incoming goods
described in earlier discussions **was not found in the current set**.
The 701-SKU table resembles inventory snapshots based on its numerical behavior, but
this is a hypothesis, not a confirmed role. Beginning/end-of-month semantics and the current SE stock date are unknown.

## Transactions: exact counts

Totals excluded; no abs(), null imputation, or deduplication applied.

| Metric | IEK / "Динамика продаж" | SE / file labeled "Ежемесячные остатки" |
|---|---:|---:|
| Rows with SKU | 171 603 | 77 312 |
| Unique SKUs | 2 151 | 565 |
| Positive quantity | 171 470 | 76 997 |
| Negative quantity | 115 | 302 |
| Blank quantity | 18 | 13 |
| Zero quantity | 0 | 0 |
| Exact duplicate rows | 0 | 0 |
| Sum of numeric quantities for export reconciliation | 3 846 998 | 6 837 478 |

IEK contains "шт", "м", and "упак": the combined total is not a business volume in one unit.
SE contains only "шт". All transactions refer to warehouse "Алматы".
IEK dates: 20.07.2023–22.09.2026; SE: 18.01.2023–22.09.2026.

Negative rows also occur in the main history: IEK has 3 in 2025 and 8 in 2026;
SE has 4 in 2026. The claim “negative quantities only occur in 2023–2024” is incorrect.
The meaning of the sign (return, correction, or another convention) remains unconfirmed.
A:H schema: "Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество".
Customer ID and price are absent. A document is not a customer.

Totals at A171605:H171605 in IEK and A77314:H77314 in SE are not transactions.
Maximum after excluding totals: IEK 210 000 (H38405), SE 90 000 (H14950).
These are investigation candidates, not proven one-off orders.
A global column IQR mixes SKUs/units and is not a production demand filter.

XML contains empty type-e cells without values. The script counts this artifact separately
as nulls, not as displayed Excel errors. A blank quantity is not zero.

## IEK inventory and "Итого"

"Лист_1": 2 853 SKUs; D1:AJ1 covers January 2024–September 2026;
D3:AK3 is labeled "нач. остаток". This is beginning-of-month, not end-of-month.
Monthly granularity cannot reveal exact days of product unavailability.

For all 1 732 rows with numeric AK ("Итого"), the value equals D (January 2024).
The grand total 592 937 also matches in D2857/AK2857.
The hypothesis that "Итого" is current stock at the export date is not supported by this check.
Clarify its meaning; do not use it as current available stock.

## IEK shipments

"Лист4" D:I contains 6 shipments labeled "поступление до":
30.09, 01.10, 10.10, and 15.10.2026. These are deadlines, not guaranteed arrival times.
2 623 rows with codes, 2 616 unique codes, 3 exact duplicate rows.
Review repeated SKUs before aggregation or removal.
Product descriptions mention purchasing in reels and accounting in meters: unit conversion is required.

## MOQ: a matching code is not sufficient

IEK / "Лист7": 1 938 rows, 1 937 SKUs. Column E contains 1 938 external price-list VLOOKUPs;
cached values exist, but 15 values are #N/A. The external price list was not recalculated.
SKU 270400035_ repeats at rows 427/1875: different names, value 6 in both rows.

| IEK dataset | SKUs | Code absent from MOQ | Code present, invalid value | Usable positive value |
|---|---:|---:|---:|---:|
| Transactions | 2 151 | 413 | 8 | 1 730 |
| Inventory | 2 853 | 999 | 8 | 1 846 |
| Monthly quantities | 2 463 | 702 | 8 | 1 753 |
| Incoming goods | 2 616 | 710 | 14 | 1 892 |

413 and 999 were confirmed, but those counts did not include invalid values.

SE MOQ: 554 SKUs with positive order multiples. Of 565 transaction SKUs, 545 are covered
and 20 lack rows. In the SKU sheet of "Сезонность", 26 order multiples are 0,
while the standalone MOQ contains 1 for those codes. This is a source conflict.
Never silently default MOQ to 1. Minimum order quantity and order multiple are separate
constraints; the meaning of IEK "Мин. разр. к отгр." requires confirmation.

## Seasonality and growth

IEK / "Сезонность": A3:N6 contains aggregate monthly/yearly measures, without SKUs.
D/G/J11:22 contains precomputed yearly coefficients; L11:L22 is "СЕЗОННОСТЬ".
H37:H39 labels the October–December 2026 coefficients as forecasts.
The earlier claim “IEK has no precomputed coefficients” is incorrect.
Units of the aggregate measures are not explicitly labeled: numerical scale alone
does not confirm the earlier interpretation as revenue in tenge.

SE: aggregate coefficients appear on "Лист1" in "Сезонность" and in "Товар в пути".
In the latter, L11:L22 is an aggregate coefficient and M15:N15 is labeled
"Поправка 2026/2025 (янв-сен)", with a value of approximately 0,99054. This is aggregate, not SKU growth.
2026 is incomplete; formula-generated October–December zeros do not establish zero sales.
Respect coefficient periods and availability in backtesting to avoid future leakage.

## Monthly quantity discrepancies against transactions

Comparison includes only SKU–month pairs with numeric monthly cells and observed
transactions, preserving signs and leaving missing values unfilled:

- IEK "Ежемесячные продажи": 22 250 pairs; 14 505 match, 7 745 differ.
- SE "Сезонность", "Лист_1": 7 020 pairs; 2 669 match, 4 351 differ.
- SE "Ежемесячные продажи": 7 263 pairs; 32 match.
  Low agreement alone does not prove that the table contains inventory.

IEK monthly-sales and SE "Сезонность" row totals equal the sum of available numeric
months for all rows with numeric totals (2 460 and 554).
The reasons for source discrepancies are unknown. Do not add sources representing
the same sales or transfer transaction corrections to monthly series before reconciling scope.

## Missing information

No explicit category fields, customer IDs, transaction prices, stockout periods,
standard supplier lead times, SE shipments, or the reported SE master table were found.
IEK shipment dates do not replace standard lead times.
Questions: [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md); schema: [ARCHITECTURE.md](ARCHITECTURE.md).
