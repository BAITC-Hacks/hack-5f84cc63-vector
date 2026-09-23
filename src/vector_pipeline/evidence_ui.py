"""A short jury view of recorded evidence, separate from live session scenarios."""
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from .evidence import load_acceptance


def quantity(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def proof(key):
    st.session_state.evidence_detail = key


def card(key, title, case, origin, value, caption, boundary):
    status = case["status"]
    color = "partial" if status == "PARTIAL" else "pass" if status == "PASS" else "fail"
    st.markdown(f'<div class="proof-heading"><b>{escape(key)} · {escape(title)}</b>'
        f'<span class="proof-status {color}">{escape(status)}</span></div>'
        f'<div class="proof-origin">{escape(origin)}</div>'
        f'<div class="proof-value">{escape(value)}</div>'
        f'<div class="proof-caption">{escape(caption)}</div>'
        f'<div class="proof-boundary">{escape(boundary)}</div>', unsafe_allow_html=True)


def render_evidence(root, processed, summary, navigate):
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
        st.warning(str(exc))
        st.info("Regenerate evidence before presenting this screen. No saved PASS claims are shown until the report matches the current code and inputs.")
        st.code(".venv/Scripts/python.exe -X utf8 scripts/accept_case.py", language="powershell")
        return
    if report.get("execution_status") != "PASS" or any(c["status"] == "FAIL" for c in report["cases"].values()):
        st.error("The latest acceptance run has failed checks. Resolve them before using this page for the jury.")
        st.json(report["cases"])
        return

    cases, real = report["cases"], report["real_examples"]
    a, b, c, d, e = (cases[key]["evidence"] for key in "ABCDE")
    iek = real["IEK"]
    same_run = summary["tables"]["recommendations.jsonl"]["sha256"] == real["recommendation_sha256"]
    statuses = ", ".join(f"{sum(c['status'] == status for c in cases.values())} {status}" for status in ("PASS", "PARTIAL"))
    st.caption(f"5 must-haves · {statuses} · Recorded acceptance evidence, not a live score or a business approval. Session edits do not change these proofs.")
    st.caption("Scan the five cards first. Proof buttons select the calculation or chart under Inspect the evidence below.")
    if not same_run:
        st.info("This evidence belongs to a different calculation snapshot. Choose the audited October scenario in the sidebar to open its SKU examples. The saved proof remains visible here.")

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        card("A", "Replenishment responds", cases["A"], "Real sales · scenario stock and receipts",
             " → ".join(quantity(iek[k]) for k in ("baseline_order", "stock_200_order", "stock_200_incoming_100_order")),
             f"IEK {iek['sku']} · original → stock 200 → incoming 100 · шт",
             "PARTIAL: categories are missing; external coefficient applicability is unconfirmed. Neither is applied.")
        x, y = st.columns(2)
        x.button("Open IEK calculation", on_click=navigate, args=("Order planning", "IEK", iek["sku"], "Order explanation"),
                 disabled=not same_run, width="stretch")
        y.button("Calculation proof", on_click=proof, args=("A",), width="stretch")
    with right, st.container(border=True):
        card("B", "Seasonality + growth", cases["B"], "Synthetic · 36 months of generated history",
             f"+{quantity(b['components']['slope_per_month'])} units / month",
             "Oct / Nov / Dec: " + " / ".join(quantity(v) for v in b["seasonality_and_growth"]),
             "The annual pattern and persistent growth are recovered together. This is not a real-data accuracy claim.")
        x, y = st.columns(2)
        x.button("Seasonal proof", on_click=proof, args=("B",), width="stretch")
        y.button("Open forecast inputs", on_click=navigate, args=("Order planning", "IEK", iek["sku"], "Source evidence"),
                 disabled=not same_run, width="stretch")

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        card("C", "Recover censored demand", cases["C"], "Synthetic · explicit stockout interval",
             f"{quantity(c['order_before'])} → {quantity(c['order_after'])}",
             f"Order before → after compensation · {quantity(c['estimated_lost_sales'])} estimated missing units",
             "Real stockout intervals were not supplied. The dashboard does not fabricate lost demand for real SKUs.")
        st.button("Stockout proof", on_click=proof, args=("C",), width="stretch")
    with right, st.container(border=True):
        amounts = d["order_quantities"]
        card("D", "One-off order protection", cases["D"], "Synthetic injection · real candidate available below",
             f"{quantity(amounts['baseline'])} → {quantity(amounts['with_screening'])}",
             f"Inject +{quantity(d['injected_quantity'])} units · without cleaning: {quantity(amounts['without_screening'])}",
             "PARTIAL: client_id is absent. The detector groups documents; customer-level grouping is not implemented.")
        st.button("One-off proof + real candidate", on_click=proof, args=("D",), width="stretch")

    with st.container(border=True):
        left, right = st.columns([3, 1])
        with left:
            card("E", "The manager stays in control", cases["E"], "Synthetic AppTest · two supplier groups",
                 "Draft → review → export",
                 f"Test quantity: {quantity(e['recommended_quantities'][0])} → {quantity(e['reviewed_draft_quantities'][0])} with a reason. Editing the scenario revokes review.",
                 "Local reviewed drafts only; nothing is sent. Corporate authorization and the exact 1C import format are not validated.")
        with right:
            st.button("Open draft review", on_click=navigate, args=("Draft review",), width="stretch")
            st.button("Review workflow proof", on_click=proof, args=("E",), width="stretch")

    st.divider()
    st.subheader("Inspect the evidence")
    labels = {"A": "A · Calculation", "B": "B · Seasonality", "C": "C · Stockout", "D": "D · One-off orders", "E": "E · Review"}
    detail = st.radio("Proof detail", list(labels), format_func=labels.get, horizontal=True,
                      key="evidence_detail", label_visibility="collapsed")
    if detail == "A":
        calc = iek["calculation"]
        st.write(f"**Real sales / scenario inventory — IEK {iek['sku']}**")
        st.write(f"{quantity(calc['forecast_demand'])} forecast + {quantity(calc['safety_stock'])} safety − "
                 f"{quantity(calc['available_stock'])} available − {quantity(calc['incoming_supply_used'])} incoming "
                 f"= {quantity(calc['net_requirement'])} net. After timing checks and rounding: {quantity(iek['baseline_order'])} шт.")
        st.caption("Opening the example shows its current session values; the proof above records the audited scenario before your edits.")
        with st.expander("Synthetic sensitivity checks"):
            st.dataframe(pd.DataFrame({"Scenario": list(a["order_quantities"]), "Order (pieces)": list(a["order_quantities"].values())}), hide_index=True, width="stretch")
    elif detail == "B":
        st.write("**Synthetic forecast — the same history, with each component removed in turn**")
        frame = pd.DataFrame({"Month": b["months"], "Seasonality + growth": b["seasonality_and_growth"],
                              "Growth only": b["growth_only"], "Seasonality only": b["seasonality_only"]})
        chart = alt.Chart(frame.melt("Month", var_name="History", value_name="Forecast")).mark_line(point=True, strokeWidth=3).encode(
            x="Month:O", y=alt.Y("Forecast:Q", title="Forecast (pieces)", scale=alt.Scale(zero=True)),
            color=alt.Color("History:N", scale=alt.Scale(range=["#8296AC", "#087F71", "#D18B49"]), legend=alt.Legend(orient="bottom")),
            tooltip=["Month:O", "History:N", "Forecast:Q"])
        st.altair_chart(chart.properties(height=240), width="stretch")
        st.dataframe(frame, hide_index=True, width="stretch")
        st.caption(f"Selected model: {b['selected_model']}. Selection precedes holdout {b['holdout_month']}. Real series have at most 20 complete consecutive months; this 36-month example is generated.")
    elif detail == "C":
        st.write("**Synthetic July stockout → estimated missing sales → frozen-model refit → order**")
        st.dataframe(pd.DataFrame({"Measure": ["July observed / corrected sales", "Monthly forecast", "Recommended order"],
            "Before": [c["observed_july"], c["forecast_before"], c["order_before"]],
            "After": [c["corrected_july"], c["forecast_after"], c["order_after"]]}), hide_index=True, width="stretch")
        st.caption("Quantities are pieces. The forecast model is held fixed; corrections use prior history and intervals known by the cutoff.")
        with st.expander("Daily stockout calculation"):
            st.dataframe(pd.DataFrame(c["daily_audit"]), hide_index=True, width="stretch")
    elif detail == "D":
        st.write(f"**Synthetic: the {quantity(d['injected_quantity'])}-unit injection passes through cleaning, forecasting and ordering**")
        frame = pd.DataFrame({"Scenario": ["Normal history", "Injection + cleaning", "Injection, cleaning bypassed"],
                              "Order": [amounts[k] for k in ("baseline", "with_screening", "without_screening")]})
        st.altair_chart(alt.Chart(frame).mark_bar(cornerRadiusEnd=4).encode(
            y=alt.Y("Scenario:N", sort=None, title=None), x=alt.X("Order:Q", title="Order (pieces)"),
            color=alt.Color("Scenario:N", scale=alt.Scale(range=["#087F71", "#087F71", "#D18B49"]), legend=None),
            tooltip=["Scenario:N", "Order:Q"]).properties(height=155), width="stretch")
        st.caption(f"Observed inflation: {d['observed_relative_inflation']:.0%}; preset fixture limit: {d['allowed_relative_inflation']:.0%}. This bound is not a universal accuracy guarantee.")
        candidate = real["real_outlier_candidate"]
        st.write(f"**Real candidate — {candidate['supplier_id']} · {candidate['sku_id']} · {candidate['date']}**")
        st.write(f"Raw **{quantity(candidate['qty_raw'])}** → regular **{quantity(candidate['qty_regular'])}** {candidate['stock_uom']}; "
                 f"prior median **{quantity(candidate['baseline']['median'])}**, threshold **{quantity(candidate['baseline']['threshold'])}**.")
        st.caption("A flagged candidate is not a verified customer anomaly. Document ID is not customer identity; customer-level screening requires data and implementation work.")
        with st.expander("Candidate provenance and prior-history baseline"):
            st.json(candidate)
    else:
        checks = {"Manual adjustment requires a reason": e["reason_required"], "Review required before reviewed export": e["review_required"],
                  "Scenario edit revokes review": e["edit_revokes_review"], "Stale draft line removed": e["stale_line_removed"],
                  "Unrelated supplier line preserved": e["unrelated_line_preserved"], "CSV and audit JSON verified": e["csv_and_json_verified"]}
        st.dataframe(pd.DataFrame({"Workflow check": list(checks), "Passed in AppTest": list(checks.values())}), hide_index=True, width="stretch")
        st.caption("This is automated fixture evidence, not approval of your current draft. Open Draft review to perform the actual local workflow.")
    with st.expander("Report provenance and full limitations"):
        st.caption(f"Report {path.parent.name} · {report['registered_workbooks_verified']} source workbook hashes checked · snapshot {real['replenishment_run']}")
        st.json({key: {"status": value["status"], "evidence_type": value["evidence"]["evidence_type"],
                       "limitations": value["limitations"]} for key, value in cases.items()})
        st.download_button("Download acceptance evidence JSON", raw, file_name="vector_case_acceptance.json", mime="application/json")
