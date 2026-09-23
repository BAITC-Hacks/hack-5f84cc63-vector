# Vector: four-minute jury walkthrough

**Claim:** explainable supplier orders, with acceptance evidence and manager review.
Real sales drive the main workflow; stock, supplier times and incomplete supply coverage
are explicitly **scenario assumptions**. No measured savings claim.

## Prepare before the pitch

From the project root, with dashboard dependencies installed:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
.venv/Scripts/python.exe -X utf8 scripts/accept_case.py
.venv/Scripts/python.exe -m streamlit run app.py
```

Expected: **91 tests OK**; A/D PARTIAL, B/C/E PASS. Re-run acceptance after code changes:
the evidence page rejects stale reports. Internet and NVIDIA APIs are not needed after
installation. Open http://127.0.0.1:8501 in a fresh session. Select **Сценарий · 2026-10-01**
with **992 / 941 / 1,724** ready / earlier supply / missing-input series. Identical replays
are collapsed; the audited recommendation SHA begins `38128a4ca20b`.

The interface defaults to Russian. Keep **RU** selected for this walkthrough; **EN** switches labels without changing calculations or drafts. Documentation remains English; the exact UI labels below are Russian.

The entire pitch stays in the dashboard. Proof buttons select details below the cards;
scroll to **Подробные доказательства** when opening a detailed proof.

## 0:00–0:55 — Five requirements, visible evidence

Click **Проверка кейса** in the sidebar. Scan the five cards:

| Card | Say and point to |
|---|---|
| A — PARTIAL | Real sales with scenario inputs: **404 → 272 → 172** as stock and timely supply increase. Categories and external coefficient applicability remain unresolved. |
| B — PASS / synthetic | Generated 36-month history recovers seasonality and **+5 units/month** growth. October–December: **295 / 310 / 305**. |
| C — PASS / synthetic | Explicit stockout correction raises the order **67 → 104**. Real stockout intervals were not supplied. |
| D — PARTIAL | Inject **+9,000**: regular order stays **63**, versus **1,224** without cleaning. Customer grouping requires missing IDs and further implementation. |
| E — PASS / synthetic workflow test | Manual changes require a reason; review enables draft export; later edits revoke review. Nothing is sent automatically. |

Say: “PASS proves this stated mechanism, not production readiness. Real and synthetic
evidence are labeled separately, and missing evidence stays PARTIAL.”

## 0:55–1:40 — Explain and change a real-sales recommendation

Click **Открыть расчёт IEK** on card A. It selects **IEK / `010300002_`**.
Scroll to **Обоснование заказа**: **404 "шт"** = rounded **403.63** net need, from
**431.48** forecast + **40.27** safety − **68.13** scenario stock − **0** incoming.
The graph shows shortage on **October 13**, before the new order arrives **November 15**:
an earlier receipt or transfer is needed, even after ordering.

Open **Что если?**. Set stock **200**, keep lead **45**, review **30**, safety **7**,
delay **0**, additional incoming **0**. Enter `Jury rehearsal: revised stock scenario` and
click **Пересчитать SKU**: **272**. Set additional incoming **100**, arrival **2026-10-02**,
and recalculate: **172**. Session edits never overwrite the saved run or forecasts.

## 1:40–2:25 — Inspect the strongest evidence

Return to **Проверка кейса → Разовый заказ: тест и реальный кандидат**; scroll to the detail.
The bars show **63 / 63 / 1,224**. Inflation is **0%**, below the preset **1%** bound
for this fixture. This tests cleaning → forecast → order, not just an outlier flag.

Below the bars, show the **real candidate**: SystemElectric **`030200192_`**, **2025-05-06**,
**90,000 "шт"**, prior median **50**, threshold **1,220**, regular quantity **0**.
Provenance: `se_transactions`, sheet "Лист_1", row **14950**, document **20000044832**,
in `Ежемесячные остатки SystemElectric 2024-2026.xlsx`. It is a candidate, not a verified
customer anomaly; the document number is not a client ID.

In **Выбор доказательства**, select **C · Дефицит**: July observed **20** → corrected **310**;
forecast **210 → 306.67**; order **67 → 104**. The daily audit shows 29 synthetic
unavailable days and **290** estimated missing units.

If asked about seasonality, choose **B · Сезонность**: combined **295 / 310 / 305**,
growth-only **285 / 290 / 295**, seasonality-only **110 / 120 / 110**.
This generated example is not a real-data accuracy claim.

## 2:25–3:40 — Manager correction, supplier grouping, review and export

Return to the cards and click **Открыть расчёт IEK**. The session still recommends **172**.
Under **Обоснование заказа**, set **Количество в черновике = 180**, reason
`Jury rehearsal: manager adjustment`, then **Добавить / обновить черновик**.
The engine quantity remains 172; the manager quantity is separate.

Set **Поставщик = SystemElectric**, search **`010400432_`**, and press Enter. Expected **9 "шт"**.
Click **Добавить / обновить черновик** unchanged. Open **Проверка черновика**: two supplier sections show
**IEK 180 / SystemElectric 9** with explanations.

Before review, reviewed-export buttons are absent. Enter **Demo reviewer**, acknowledge
the exact quantities/assumptions, click **Подтвердить проверку черновика**, then **Скачать проверенный CSV**
and **Скачать полный аудит JSON**. This is local demo review, not a corporate purchase approval.

## 3:40–4:00 — Close with the boundary

“The workflow is usable now. We distinguish scenario assumptions from company facts,
retain explanations and source provenance, and keep the manager in control. Next we
validate current stock, supplier times, category rules, client identifiers and the 1C
template with the partner.”

Open **Качество данных** if challenged. Do not claim universal forecast superiority,
verified anomaly labels, authenticated approval, production readiness or measured ROI.

## Readiness notes

- The calculation/review sequence and evidence controls/navigation pass AppTest. Browser
  visual QA and a timed laptop rehearsal remain pending: no browser surface was available.
  The four-minute timing is a planned sequence.
- Evidence is a frozen acceptance snapshot; session edits do not rewrite it. Real-example
  links are disabled for a different selected run. Return to the audited October scenario
  if needed; changing runs clears session drafts.
- Confirm whether the jury requires deployment. Local presentation is available; exact
  1C import compatibility and a hosted deployment are not implemented.
- After the timed pitch, optionally edit IEK's scenario again: its stale draft line is
  removed, SE's line remains, and review/export eligibility is revoked.
