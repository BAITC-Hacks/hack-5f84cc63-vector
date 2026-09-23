"""A short jury view of recorded evidence, separate from live session scenarios."""
from html import escape
from functools import partial

import altair as alt
import pandas as pd
import streamlit as st

from .evidence import load_acceptance
from .i18n import translate, localize_frame, error_text


def quantity(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def proof(key):
    st.session_state.evidence_detail = key


def card(key, title, case, origin, value, caption, boundary):
    status = case["status"]
    color = "partial" if status == "PARTIAL" else "pass" if status == "PASS" else "fail"
    st.markdown(f'<div class="proof-heading"><b>{escape(key)} · {escape(title)}</b>'
        f'<span class="proof-status {color}">{escape(translate(status, st.session_state.get("language", "ru")))}</span></div>'
        f'<div class="proof-origin">{escape(origin)}</div>'
        f'<div class="proof-value">{escape(value)}</div>'
        f'<div class="proof-caption">{escape(caption)}</div>'
        f'<div class="proof-boundary">{escape(boundary)}</div>', unsafe_allow_html=True)


def render_evidence(root, processed, summary, navigate):
    language = st.session_state.get("language", "ru")
    t = partial(translate, language=language)
    st.markdown("""<style>
    .proof-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:17px}
    .proof-status{font-size:10px;letter-spacing:1px;font-weight:800;padding:4px 9px;border-radius:20px;white-space:nowrap}
    .proof-status.pass{color:#086653;background:#E2F3ED}.proof-status.partial{color:#875812;background:#FFF0CC}
    .proof-status.fail{color:#A62C31;background:#FFE5E5}
    .proof-origin{font-size:10px;letter-spacing:1px;color:#64748B;text-transform:uppercase;margin-top:8px}
    .proof-value{font-size:30px;line-height:1.25;letter-spacing:-1px;font-weight:750;color:#17334B;margin:10px 0 4px}
    .proof-caption{font-size:13px;color:#334155}.proof-boundary{font-size:12px;color:#64748B;margin:9px 0 12px}
    </style>""", unsafe_allow_html=True)
    try:
        report, path, raw = load_acceptance(root, processed)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.warning(error_text(exc, language))
        st.info(t('Regenerate evidence before presenting this screen. No saved PASS claims are shown until the report matches the current code and inputs.'))
        script = "build_demo.py" if summary.get("mode") == "public_synthetic" else "accept_case.py"
        st.code(f"python scripts/{script}", language="bash")
        return
    if report.get("execution_status") != "PASS" or any(c["status"] == "FAIL" for c in report["cases"].values()):
        st.error(t('The latest acceptance run has failed checks. Resolve them before using this page for the jury.'))
        st.json(report["cases"])
        return

    cases, real = report["cases"], report["real_examples"]
    synthetic = report.get("scope") == "public_synthetic"
    a, b, c, d, e = (cases[key]["evidence"] for key in "ABCDE")
    iek = real["IEK"]
    same_run = summary["tables"]["recommendations.jsonl"]["sha256"] == real["recommendation_sha256"]
    statuses = ", ".join(f"{sum(c['status'] == status for c in cases.values())} {t(status)}" for status in ("PASS", "PARTIAL"))
    st.caption(t('5 must-haves · {v0} · Recorded acceptance evidence, not a live score or a business approval. Session edits do not change these proofs.', v0=statuses))
    st.caption(t('Scan the five cards first. Proof buttons select the calculation or chart under Inspect the evidence below.'))
    if not same_run:
        st.info(t('This evidence belongs to a different calculation snapshot. Choose the audited October scenario in the sidebar to open its SKU examples. The saved proof remains visible here.'))

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        order_keys = ("baseline_order", "stock_order", "incoming_order") if synthetic else ("baseline_order", "stock_200_order", "stock_200_incoming_100_order")
        card("A", t('Replenishment responds'), cases["A"], t('Synthetic sales, stock and receipts' if synthetic else 'Real sales · scenario stock and receipts'),
             " → ".join(quantity(iek[k]) for k in order_keys),
             t('IEK {sku} · original → stock {stock} → incoming {incoming}', sku=iek['sku'], stock=iek.get('stock_value', 200), incoming=iek.get('incoming_value', 100)),
             t('PARTIAL: categories are missing; external coefficient applicability is unconfirmed. Neither is applied.'))
        x, y = st.columns(2)
        x.button(t('Open IEK calculation'), on_click=navigate, args=("Order planning", "IEK", iek["sku"], "Order explanation"),
                 disabled=not same_run, width="stretch")
        y.button(t('Calculation proof'), on_click=proof, args=("A",), width="stretch")
    with right, st.container(border=True):
        card("B", t('Seasonality + growth'), cases["B"], t('Synthetic · 36 months of generated history'),
             t('+{v0} units / month', v0=quantity(b['components']['slope_per_month'])),
             t("Oct / Nov / Dec: ") + " / ".join(quantity(v) for v in b["seasonality_and_growth"]),
             t('The annual pattern and persistent growth are recovered together. This is not a real-data accuracy claim.'))
        x, y = st.columns(2)
        x.button(t('Seasonal proof'), on_click=proof, args=("B",), width="stretch")
        y.button(t('Open forecast inputs'), on_click=navigate, args=("Order planning", "IEK", iek["sku"], "Source evidence"),
                 disabled=not same_run, width="stretch")

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        card("C", t('Recover censored demand'), cases["C"], t('Synthetic · explicit stockout interval'),
             t('{v0} → {v1}', v0=quantity(c['order_before']), v1=quantity(c['order_after'])),
             t('Order before → after compensation · {v0} estimated missing units', v0=quantity(c['estimated_lost_sales'])),
             t('Real stockout intervals were not supplied. The dashboard does not fabricate lost demand for real SKUs.'))
        st.button(t('Stockout proof'), on_click=proof, args=("C",), width="stretch")
    with right, st.container(border=True):
        amounts = d["order_quantities"]
        card("D", t('One-off order protection'), cases["D"], t('Synthetic injection · generated candidate below' if synthetic else 'Synthetic injection · real candidate available below'),
             t('{v0} → {v1}', v0=quantity(amounts['baseline']), v1=quantity(amounts['with_screening'])),
             t('Inject +{v0} units · without cleaning: {v1}', v0=quantity(d['injected_quantity']), v1=quantity(amounts['without_screening'])),
             t('PARTIAL: client_id is absent. The detector groups documents; customer-level grouping is not implemented.'))
        st.button(t('One-off proof + synthetic candidate' if synthetic else 'One-off proof + real candidate'), on_click=proof, args=("D",), width="stretch")

    with st.container(border=True):
        left, right = st.columns([3, 1])
        with left:
            card("E", t('The manager stays in control'), cases["E"], t('Synthetic AppTest · two supplier groups'),
                 t('Draft → review → export'),
                 t('Test quantity: {v0} → {v1} with a reason. Editing the scenario revokes review.', v0=quantity(e['recommended_quantities'][0]), v1=quantity(e['reviewed_draft_quantities'][0])),
                 t('Local reviewed drafts only; nothing is sent. Corporate authorization and the exact 1C import format are not validated.'))
        with right:
            st.button(t('Open draft review'), on_click=navigate, args=("Draft review",), width="stretch")
            st.button(t('Review workflow proof'), on_click=proof, args=("E",), width="stretch")

    st.divider()
    st.subheader(t('Inspect the evidence'))
    labels = {"A": "A · Calculation", "B": "B · Seasonality", "C": "C · Stockout", "D": "D · One-off orders", "E": "E · Review"}
    detail = st.radio(t('Proof detail'), list(labels), format_func=lambda key: t(labels[key]), horizontal=True,
                      key="evidence_detail", label_visibility="collapsed")
    if detail == "A":
        calc = iek["calculation"]
        st.write(t('**Synthetic example — IEK {v0}**' if synthetic else '**Real sales / scenario inventory — IEK {v0}**', v0=iek['sku']))
        st.write(t('{v0} forecast + {v1} safety − {v2} available − {v3} incoming = {v4} net. After timing checks and rounding: {v5} шт.', v0=quantity(calc['forecast_demand']), v1=quantity(calc['safety_stock']), v2=quantity(calc['available_stock']), v3=quantity(calc['incoming_supply_used']), v4=quantity(calc['net_requirement']), v5=quantity(iek['baseline_order'])))
        st.caption(t('Opening the example shows its current session values; the proof above records the audited scenario before your edits.'))
        with st.expander(t('Synthetic sensitivity checks')):
            st.dataframe(localize_frame(pd.DataFrame({"Scenario": list(a["order_quantities"]), "Order (pieces)": list(a["order_quantities"].values())}), language), hide_index=True, width="stretch")
    elif detail == "B":
        st.write(t('**Synthetic forecast — the same history, with each component removed in turn**'))
        frame = pd.DataFrame({"Month": b["months"], "Seasonality + growth": b["seasonality_and_growth"],
                              "Growth only": b["growth_only"], "Seasonality only": b["seasonality_only"]})
        chart_data = frame.melt("Month", var_name="History", value_name="Forecast")
        chart_data["History"] = chart_data["History"].map(t)
        chart = alt.Chart(chart_data).mark_line(point=True, strokeWidth=3).encode(
            x=alt.X("Month:O", title=t("Month")), y=alt.Y("Forecast:Q", title=t("Forecast (pieces)"), scale=alt.Scale(zero=True)),
            color=alt.Color("History:N", title=t("History"), scale=alt.Scale(domain=[t("Growth only"), t("Seasonality + growth"), t("Seasonality only")], range=["#8296AC", "#087F71", "#D18B49"]), legend=alt.Legend(orient="bottom")),
            tooltip=[alt.Tooltip("Month:O", title=t("Month")), alt.Tooltip("History:N", title=t("History")), alt.Tooltip("Forecast:Q", title=t("Forecast"))])
        st.altair_chart(chart.properties(height=240), width="stretch")
        st.dataframe(localize_frame(frame, language), hide_index=True, width="stretch")
        st.caption(t('Selected model: {v0}. Selection precedes holdout {v1}. Real series have at most 20 complete consecutive months; this 36-month example is generated.', v0=t(b['selected_model']), v1=b['holdout_month']))
    elif detail == "C":
        st.write(t('**Synthetic July stockout → estimated missing sales → frozen-model refit → order**'))
        st.dataframe(localize_frame(pd.DataFrame({"Measure": ["July observed / corrected sales", "Monthly forecast", "Recommended order"],
            "Before": [c["observed_july"], c["forecast_before"], c["order_before"]],
            "After": [c["corrected_july"], c["forecast_after"], c["order_after"]]}), language), hide_index=True, width="stretch")
        st.caption(t('Quantities are pieces. The forecast model is held fixed; corrections use prior history and intervals known by the cutoff.'))
        with st.expander(t('Daily stockout calculation')):
            st.dataframe(localize_frame(pd.DataFrame(c["daily_audit"]), language), hide_index=True, width="stretch")
    elif detail == "D":
        st.write(t('**Synthetic: the {v0}-unit injection passes through cleaning, forecasting and ordering**', v0=quantity(d['injected_quantity'])))
        frame = pd.DataFrame({"Scenario": ["Normal history", "Injection + cleaning", "Injection, cleaning bypassed"],
                              "Order": [amounts[k] for k in ("baseline", "with_screening", "without_screening")]})
        frame["Scenario"] = frame["Scenario"].map(t)
        st.altair_chart(alt.Chart(frame).mark_bar(cornerRadiusEnd=4).encode(
            y=alt.Y("Scenario:N", sort=None, title=None), x=alt.X("Order:Q", title=t("Order (pieces)")),
            color=alt.Color("Scenario:N", scale=alt.Scale(domain=frame["Scenario"].tolist(), range=["#087F71", "#087F71", "#D18B49"]), legend=None),
            tooltip=[alt.Tooltip("Scenario:N", title=t("Scenario")), alt.Tooltip("Order:Q", title=t("Order"))]).properties(height=155), width="stretch")
        st.caption(t('Observed inflation: {v0:.0%}; preset fixture limit: {v1:.0%}. This bound is not a universal accuracy guarantee.', v0=d['observed_relative_inflation'], v1=d['allowed_relative_inflation']))
        candidate = real["real_outlier_candidate"]
        st.write(t('**Synthetic candidate — {v0} · {v1} · {v2}**' if synthetic else '**Real candidate — {v0} · {v1} · {v2}**', v0=candidate['supplier_id'], v1=candidate['sku_id'], v2=candidate['date']))
        st.write(t('Raw **{v0}** → regular **{v1}** {v2}; prior median **{v3}**, threshold **{v4}**.', v0=quantity(candidate['qty_raw']), v1=quantity(candidate['qty_regular']), v2=candidate['stock_uom'], v3=quantity(candidate['baseline']['median']), v4=quantity(candidate['baseline']['threshold'])))
        st.caption(t('A flagged candidate is not a verified customer anomaly. Document ID is not customer identity; customer-level screening requires data and implementation work.'))
        with st.expander(t('Technical audit')):
            st.json(candidate)
    else:
        checks = {"Manual adjustment requires a reason": e["reason_required"], "Review required before reviewed export": e["review_required"],
                  "Scenario edit revokes review": e["edit_revokes_review"], "Stale draft line removed": e["stale_line_removed"],
                  "Unrelated supplier line preserved": e["unrelated_line_preserved"], "CSV and audit JSON verified": e["csv_and_json_verified"]}
        st.dataframe(localize_frame(pd.DataFrame({"Workflow check": list(checks), "Passed in AppTest": list(checks.values())}), language), hide_index=True, width="stretch")
        st.caption(t('This is automated fixture evidence, not approval of your current draft. Open Draft review to perform the actual local workflow.'))
    with st.expander(t('Report provenance and full limitations')):
        st.caption(t("Audit keys, source values and CSV/JSON exports retain their original language and schema."))
        st.caption(t('Report {v0} · {v1} source workbook hashes checked · snapshot {v2}', v0=path.parent.name, v1=report['registered_workbooks_verified'], v2=real['replenishment_run']))
        st.json({key: {"status": value["status"], "evidence_type": value["evidence"]["evidence_type"],
                       "limitations": value["limitations"]} for key, value in cases.items()})
        st.download_button(t('Download acceptance evidence JSON'), raw, file_name="vector_case_acceptance.json", mime="application/json")
