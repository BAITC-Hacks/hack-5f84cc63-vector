"""Run with: python -m streamlit run app.py."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from vector_pipeline.dashboard import (discover_runs, draft_line, export_draft, fingerprint,
    load_products, load_run, review_draft, review_is_current, scenario)
from vector_pipeline.evidence_ui import render_evidence

st.set_page_config(page_title="Vector | Procurement workspace", page_icon="↗", layout="wide")
st.markdown("""<style>
.block-container {max-width:1500px;padding-top:2rem;padding-bottom:3rem}
h1 {letter-spacing:-1.5px!important;font-weight:750!important}
h2,h3 {letter-spacing:-.5px!important}
[data-testid="stMetric"] {background:white;border:1px solid #E3E9F0;border-radius:12px;padding:16px 20px}
[data-testid="stMetricValue"] {font-size:2rem;font-weight:700}
[data-testid="stSidebar"] {border-right:1px solid #E3E9F0}
.brand {font-size:30px;font-weight:800;letter-spacing:-1.4px;color:#17334B}
.brand span {color:#087F71}.eyebrow {font-size:11px;letter-spacing:2px;font-weight:750;color:#64748B;text-transform:uppercase}
.scenario-note {background:#FFF5DF;border:1px solid #F1D99F;border-radius:10px;padding:12px 18px;color:#795616;font-size:14px;margin:10px 0 22px}
.hero-qty {font-size:44px;font-weight:750;letter-spacing:-1.5px;color:#087F71;line-height:1.2}
.muted {color:#64748B;font-size:14px}.formula {background:#EDF6F3;border-radius:10px;padding:14px 18px;font-size:16px}
</style>""", unsafe_allow_html=True)

ROOT = Path(__file__).resolve().parent
PROCESSED = Path(os.environ.get("VECTOR_PROCESSED_ROOT", str(ROOT / "data" / "processed")))
URGENCY = {"expedite_or_transfer": "Earlier supply needed", "order_now": "Order now",
           "no_order_needed": "Covered", "unavailable": "Missing inputs"}


def fmt(value):
    return "—" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def file_stamp(path):
    info = Path(path).stat()
    return info.st_mtime_ns, info.st_size


@st.cache_data(show_spinner="Loading verified recommendations…")
def cached_run(path, stamp):
    return load_run(path)


@st.cache_data(show_spinner=False)
def cached_products(root, source_hash):
    return load_products(root, {"input_sha256": {"ingestion_summary": source_hash}})


def invalidate_review():
    st.session_state.review = None


def navigate(page, supplier=None, sku=None, detail="Order explanation"):
    """Navigation only: never add a draft line or overwrite a scenario."""
    st.session_state.workspace = page
    if supplier is not None:
        st.session_state.update(filter_supplier=supplier, filter_query=sku or "", filter_action="All actions",
                                filter_warehouse="All warehouses", filter_unit="All units", detail_default=detail)


with st.sidebar:
    st.markdown('<div class="brand">vector<span>↗</span></div>', unsafe_allow_html=True)
    st.caption("PROCUREMENT WORKSPACE")
    st.divider()
    page = st.radio("Workspace", ["Order planning", "Case validation", "Draft review", "Data coverage"],
                    label_visibility="collapsed", key="workspace")
    st.divider()
    runs = discover_runs(PROCESSED / "replenishment")
    if not runs:
        st.info("Create a replenishment run to start. See README → Replenishment v1.")
        st.stop()
    selected_run = st.selectbox("Calculation run", runs, format_func=lambda r:
        f"{'Scenario' if r['mode'] == 'demo_scenario' else 'Explicit inputs'} · {r['planning_date']} · {Path(r['path']).name[:6]}")
    st.caption("Local workspace · No supplier connection")

run_path = Path(selected_run["path"])
try:
    stamp = (file_stamp(run_path / "replenishment_summary.json"), file_stamp(run_path / "recommendations.jsonl"))
    summary, originals = cached_run(str(run_path), stamp)
except (OSError, ValueError, KeyError) as exc:
    st.error(f"Cannot open this run: {exc}")
    st.stop()
run_key = fingerprint([str(run_path), stamp])
if st.session_state.get("run_key") != run_key:
    st.session_state.update(run_key=run_key, overrides={}, cart={}, review=None)
try:
    products = cached_products(str(PROCESSED), summary.get("input_sha256", {}).get("ingestion_summary"))
except (OSError, ValueError, KeyError):
    products = {}
    st.warning("Product names could not be verified. Recommendations remain available by SKU.")
original_by_id = {r["series_id"]: r for r in originals}
rows = [st.session_state.overrides.get(r["series_id"], r) for r in originals]


def product(row):
    return products.get((row["supplier_id"], row["sku_id"]), {})


def table_row(row):
    calc = row.get("calculation") or {}
    return {"Supplier": row["supplier_id"], "SKU": row["sku_id"],
            "Product": product(row).get("name") or "Name unresolved",
            "Action": URGENCY.get(row["urgency"], row["urgency"]),
            "Order": row["recommended_quantity"], "Unit": row["stock_uom"],
            "Available": calc.get("available_stock"), "Incoming": calc.get("incoming_supply_used"),
            "Shortage from": calc.get("first_shortage_without_order"),
            "Status": row["recommendation_status"], "Warehouse": row["warehouse_id"]}


st.markdown('<div class="eyebrow">ELECTROKOMPLEKT / DECISION WORKSPACE</div>', unsafe_allow_html=True)
st.title(page)
scenario_count = sum(r["recommendation_status"] == "scenario" for r in rows)
if page == "Case validation":
    st.caption("Why Vector works · Before / after evidence for the five HackAlem requirements")
else:
    st.caption(f"Planning date: {summary['planning_date']}  ·  {len(rows):,} SKU series  ·  Warehouse and stock units preserved")
if page != "Case validation" and (summary["mode"] == "demo_scenario" or scenario_count):
    st.markdown('<div class="scenario-note"><b>SCENARIO WORKSPACE</b> &nbsp; Real sales forecasts with assumed stock, supplier times and supply coverage. Quantities and shortage alerts are scenario results, not confirmed purchasing needs.</div>', unsafe_allow_html=True)
elif page != "Case validation":
    st.info("Explicit-input mode: quantities remain unavailable wherever operational inputs are unresolved.")
if notice := st.session_state.pop("notice", None):
    st.success(notice)


def show_projection(row):
    timeline = pd.DataFrame(row["timeline"])
    long = timeline.melt(id_vars=["date"], value_vars=["balance_without_order", "balance_with_order"],
                         var_name="Plan", value_name="Projected balance")
    long["Plan"] = long["Plan"].map({"balance_without_order": "Existing supply", "balance_with_order": "With recommended order"})
    chart = alt.Chart(long).mark_line(strokeWidth=2.5).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%d %b")),
        y=alt.Y("Projected balance:Q", title=f"Projected balance ({row['stock_uom']})"),
        color=alt.Color("Plan:N", scale=alt.Scale(domain=["Existing supply", "With recommended order"],
            range=["#D18B49", "#087F71"]), legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("date:T", title="Date"), "Plan:N", alt.Tooltip("Projected balance:Q", format=",.2f")])
    zero = alt.Chart(pd.DataFrame({"zero": [0]})).mark_rule(color="#9CAABB", strokeDash=[4, 4]).encode(y="zero:Q")
    st.altair_chart((chart + zero).properties(height=275), width="stretch")
    st.caption("Negative balance represents accumulated unmet demand in the scenario. It is not measured lost sales. The chart uses the engine recommendation, before any manual draft quantity change.")


def show_details(row):
    sid = row["series_id"]
    original = original_by_id[sid]
    info = product(row)
    st.divider()
    st.subheader(f"{row['sku_id']} · {row['supplier_id']}")
    st.write(info.get("name") or "Product name unresolved in the source data")
    st.caption(f"{row['warehouse_id']} · {row['stock_uom']} · Model: {row.get('forecast_model') or 'unavailable'}")
    tabs = st.tabs(["Order explanation", "What-if scenario", "Source evidence"],
                   default=st.session_state.pop("detail_default", "Order explanation"))
    with tabs[0]:
        calc = row.get("calculation")
        if not calc:
            st.warning("Calculation unavailable: " + ", ".join(row["unavailable_reasons"]))
        else:
            left, right = st.columns([1, 1.65], gap="large")
            with left:
                st.markdown('<div class="eyebrow">RECOMMENDED ORDER</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="hero-qty">{fmt(row["recommended_quantity"])} <span style="font-size:20px">{escape(row["stock_uom"])}</span></div>', unsafe_allow_html=True)
                st.caption(f"{row['recommendation_status'].capitalize()} · {calc['protection_days']}-day protection period")
                components = {"Forecast over protection period": calc["forecast_demand"],
                    "+ Safety buffer": calc["safety_stock"], "− Available stock": calc["available_stock"],
                    "− Dated incoming supply": calc["incoming_supply_used"], "= Net requirement": calc["net_requirement"],
                    "Timing requirement": calc["timing_requirement"], "Rounding added": calc["rounding_increment"]}
                st.dataframe(pd.DataFrame({"Component": list(components), "Quantity": [fmt(v) for v in components.values()]}),
                    hide_index=True, width="stretch")
                st.caption("Order = round(max(0, net requirement, timing requirement)), subject to applied minimum and multiple.")
                unresolved = sum(not c.get("applied", False) for c in row["constraint_audit"])
                if unresolved:
                    st.caption(f"{unresolved} supplier constraint(s) unresolved / not applied. Stock-unit rounding does not establish supplier MOQ.")
            with right:
                st.markdown("**Inventory projection**")
                show_projection(row)
                if calc["peak_shortage_before_new_order"] > 0:
                    st.warning(f"Earlier supply needed: projected shortage starts {calc['first_shortage_without_order']}; the new order arrives {calc['new_order_arrival_date']}. Expedite or transfer stock to cover the gap.")
                else:
                    st.success(f"The plan covers demand before the new order arrives on {calc['new_order_arrival_date']}.")
            st.caption(f"Stockout compensation: {row['stockout_adjustment_status']}. Forecast quantity is based on regular observed sales unless explicit intervals were supplied.")
            with st.form(f"draft-{sid}"):
                cols = st.columns([1, 2, 1])
                quantity = cols[0].number_input("Draft quantity", min_value=0.0, value=float(row["recommended_quantity"]),
                    key=f"draft-qty-{fingerprint(row)[:16]}")
                reason = cols[1].text_input("Reason for manual quantity change", key=f"draft-reason-{sid}")
                add = cols[2].form_submit_button("Add / update draft", width="stretch")
            if add:
                try:
                    st.session_state.cart[sid] = draft_line(row, quantity, reason)
                    invalidate_review()
                    st.session_state.notice = "Added to draft. Open Draft review to check and export the selected lines."
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with tabs[1]:
        if not original.get("calculation"):
            st.info("This version edits eligible recommendations. Resolve the missing upstream inputs and create a new run for this series.")
        else:
            st.caption("Changes apply only to this SKU in this browser session. The stored run and forecasts stay unchanged. Receipt delay affects existing receipts on or after the planning date; the additional receipt uses its own date.")
            base = original["calculation"]
            current = row.get("ui_scenario", {})
            with st.form(f"scenario-{sid}"):
                a, b, c = st.columns(3)
                stock = a.number_input(f"Available stock ({row['stock_uom']})", min_value=0.0, value=float(current.get("stock", base["available_stock"])))
                lead = b.number_input("Lead time (days)", min_value=0, max_value=365, value=int(current.get("lead", base["lead_time_days"])))
                review = c.number_input("Review period (days)", min_value=1, max_value=365, value=int(current.get("review", base["review_period_days"])))
                a, b, c = st.columns(3)
                safety = a.number_input("Safety buffer (days)", min_value=0.0, max_value=365.0, value=float(current.get("safety", base["safety_days"])))
                delay = b.number_input("Existing receipt delay (days)", min_value=0, max_value=365, value=int(current.get("delay", 0)))
                receipt_qty = c.number_input(f"Additional incoming ({row['stock_uom']})", min_value=0.0, value=float(current.get("receipt_qty", 0)))
                receipt_date = st.date_input("Additional receipt arrival", value=date.fromisoformat(current.get("receipt_date") or row["planning_date"]) + (timedelta(days=14) if not current.get("receipt_date") else timedelta()), min_value=date.fromisoformat(row["planning_date"]))
                reason = st.text_input("Scenario reason", value=current.get("reason", ""), placeholder="For example: supplier delay or a revised stock count")
                applied = st.form_submit_button("Recalculate SKU", type="primary")
            if applied:
                try:
                    changed = scenario(original, summary["effective_config"]["policies"], stock=stock,
                        lead=lead, review=review, safety=safety, delay=delay, reason=reason,
                        receipt_qty=receipt_qty, receipt_date=receipt_date.isoformat())
                    st.session_state.overrides[sid] = changed
                    # Old manual quantities must not survive an upstream scenario change unnoticed.
                    st.session_state.cart.pop(sid, None)
                    invalidate_review()
                    st.session_state.notice = (f"Recalculated {row['sku_id']}: {fmt(original['recommended_quantity'])} → "
                        f"{fmt(changed['recommended_quantity'])} {row['stock_uom']}. "
                        "If the SKU no longer matches the current filters, select All actions to inspect it.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if sid in st.session_state.overrides:
                st.info(f"Scenario applied. Original order: {fmt(original['recommended_quantity'])} → current order: {fmt(row['recommended_quantity'])} {row['stock_uom']}. Add the revised line to the draft again if needed.")
                if st.button("Reset this SKU", key=f"reset-{sid}"):
                    del st.session_state.overrides[sid]
                    st.session_state.cart.pop(sid, None)
                    invalidate_review()
                    st.rerun()
    with tabs[2]:
        forecasts = row.get("forecast_values_used", [])
        if forecasts:
            st.markdown("**Monthly forecast used by the engine**")
            st.dataframe(pd.DataFrame([{"Month": f["target_month"], "Forecast": f["forecast_qty"],
                "Mean baseline": f.get("baseline_forecast_qty"), "Model": f["model"],
                "As of": f["as_of_date"]} for f in forecasts]), hide_index=True, width="stretch")
        st.caption("Seasonality and growth are included in the model where supported; they are not added a second time. Category and customer IDs are unavailable in the supplied data.")
        with st.expander("Input values, evidence and assumptions"):
            st.json(row["inputs"])
            st.write("Assumption IDs", row["assumption_ids"])
        with st.expander("Incoming supply and ordering constraints"):
            st.json({"incoming": row["incoming_audit"], "constraints": row["constraint_audit"]})
        with st.expander("Product identity and source references"):
            st.json(info or {"sku_id": row["sku_id"], "product_dimension": "unavailable"})


if page == "Order planning":
    a, b, c, d = st.columns(4)
    a.metric("Ready for review", f"{sum(r['recommended_quantity'] is not None for r in rows):,}")
    b.metric("Earlier supply needed", f"{sum(r['urgency'] == 'expedite_or_transfer' for r in rows):,}")
    c.metric("Missing inputs", f"{sum(r['recommendation_status'] == 'unavailable' for r in rows):,}")
    d.metric("Draft lines", len(st.session_state.cart))
    st.caption("Counts cover the entire selected run, including session edits. Each series is a supplier / SKU / warehouse / unit combination.")
    st.subheader("Recommendations")
    cols = st.columns([1, 1.4, 1, 1, 1.7])
    supplier = cols[0].selectbox("Supplier", ["All suppliers"] + sorted({r["supplier_id"] for r in rows}), key="filter_supplier")
    action = cols[1].selectbox("Action", ["Positive orders", "All actions"] + list(URGENCY.values()), key="filter_action")
    warehouse = cols[2].selectbox("Warehouse", ["All warehouses"] + sorted({r["warehouse_id"] for r in rows if r["warehouse_id"]}), key="filter_warehouse")
    unit = cols[3].selectbox("Unit", ["All units"] + sorted({r["stock_uom"] for r in rows if r["stock_uom"]}), key="filter_unit")
    query = cols[4].text_input("Find SKU or product", placeholder="Type a code or name", key="filter_query")
    filtered = [r for r in rows if (supplier == "All suppliers" or r["supplier_id"] == supplier)
        and (warehouse == "All warehouses" or r["warehouse_id"] == warehouse)
        and (unit == "All units" or r["stock_uom"] == unit)
        and (action == "All actions" or (action == "Positive orders" and (r["recommended_quantity"] or 0) > 0)
             or URGENCY.get(r["urgency"]) == action)
        and (not query or query.casefold() in (r["sku_id"] + " " + (product(r).get("name") or "") + " " + (product(r).get("supplier_article") or "")).casefold())]
    filtered.sort(key=lambda r: (list(URGENCY).index(r["urgency"]), r["supplier_id"], r["sku_id"]))
    st.caption(f"{len(filtered):,} matching series · Select a row to inspect the calculation. Quantities are never summed across different units.")
    if filtered:
        event = st.dataframe(pd.DataFrame([table_row(r) for r in filtered]), hide_index=True,
            width="stretch", height=330, on_select="rerun", selection_mode="single-row",
            key=f"orders-{fingerprint([r['series_id'] for r in filtered])[:16]}",
            column_config={"Order": st.column_config.NumberColumn(format="%.2f"),
                           "Available": st.column_config.NumberColumn(format="%.2f"),
                           "Incoming": st.column_config.NumberColumn(format="%.2f")})
        chosen = event.selection.rows[0] if event.selection.rows else 0
        show_details(filtered[chosen])
    else:
        st.info("No matching recommendations. Adjust the filters; use All actions to inspect unavailable series.")

elif page == "Case validation":
    render_evidence(ROOT, PROCESSED, summary, navigate)

elif page == "Draft review":
    cart = st.session_state.cart
    st.write("Review the selected lines by supplier, then export a local draft. Exports do not send orders or authorize a purchase in another system.")
    if not cart:
        st.info("Your draft is empty. Select a recommendation in Order planning and add it to the draft.")
    else:
        for supplier_id in sorted({line["recommendation"]["supplier_id"] for line in cart.values()}):
            st.subheader(supplier_id)
            selected = [line for line in cart.values() if line["recommendation"]["supplier_id"] == supplier_id]
            st.dataframe(pd.DataFrame([{**table_row(x["recommendation"]), "Draft quantity": x["order_quantity"],
                "Manager reason": x["manager_reason"]} for x in selected]), hide_index=True, width="stretch")
        remove = st.multiselect("Remove draft lines", list(cart), format_func=lambda sid:
            f"{cart[sid]['recommendation']['supplier_id']} · {cart[sid]['recommendation']['sku_id']} · {cart[sid]['recommendation']['stock_uom']}")
        if st.button("Remove selected", disabled=not remove):
            for sid in remove:
                del cart[sid]
            invalidate_review()
            st.rerun()
        with st.form("review-draft"):
            reviewer = st.text_input("Reviewer name")
            acknowledged = st.checkbox("I reviewed these exact quantities, scenario assumptions, timing alerts and unresolved supplier constraints.")
            submitted = st.form_submit_button("Mark draft reviewed", type="primary")
        if submitted:
            try:
                st.session_state.review = review_draft(cart, reviewer, acknowledged, datetime.now(timezone.utc).isoformat())
            except ValueError as exc:
                st.error(str(exc))
        if review_is_current(cart, st.session_state.review):
            csv_bytes, json_bytes = export_draft(cart, st.session_state.review, run_path.name)
            st.success(f"Reviewed by {st.session_state.review['reviewer']}. Export is ready; nothing has been sent.")
            a, b = st.columns(2)
            a.download_button("Download reviewed CSV", csv_bytes, file_name="vector_reviewed_draft.csv", mime="text/csv", width="stretch")
            b.download_button("Download full audit JSON", json_bytes, file_name="vector_reviewed_draft.json", mime="application/json", width="stretch")
            st.caption("CSV is a generic UTF-8 exchange format; compatibility with the company's 1C import template is not yet validated. JSON preserves exact SKU strings, source inputs and calculation details. This local review is not an authenticated corporate approval.")
        else:
            st.caption("Export becomes available after review. Any change to draft lines or their scenario invalidates the previous review.")

else:
    st.subheader("What this run can support")
    a, b, c = st.columns(3)
    a.metric("Series in run", f"{len(rows):,}")
    b.metric("Scenario quantities", f"{scenario_count:,}")
    c.metric("Unavailable quantities", f"{sum(r['recommended_quantity'] is None for r in rows):,}")
    st.dataframe(pd.DataFrame([{"Supplier": supplier_id,
        "Total series": sum(r["supplier_id"] == supplier_id for r in rows),
        "Available recommendations": sum(r["supplier_id"] == supplier_id and r["recommended_quantity"] is not None for r in rows)}
        for supplier_id in sorted({r["supplier_id"] for r in rows})]), hide_index=True, width="stretch")
    reasons = Counter(reason for r in rows for reason in r["unavailable_reasons"])
    st.markdown("**Reasons a quantity is unavailable**")
    st.dataframe(pd.DataFrame([{"Reason": k, "Series": v} for k, v in reasons.most_common()]), hide_index=True, width="stretch")
    st.markdown("**Evidence limits on the supplied dataset**")
    st.write("Current available stock, supplier lead times and actual stockout intervals have not been confirmed. Category and customer IDs are missing. Historical monthly stock is not treated as current stock, and document IDs are not customer IDs.")
    st.write("Forecast v1 uses regular observed sales. Model selection used rolling historical origins before the final August 2026 holdout. On that holdout, the selected policy lost to the mean baseline for IEK and SystemElectric pieces; it improved IEK meters and packs. No inventory-cost improvement has been established.")
    st.write("The real data provide at most 20 consecutive complete months. Two-cycle trend/seasonality fitting is not established on those series; synthetic tests verify that the forecasting and stockout mechanisms work.")
    st.caption("These findings describe the supplied project dataset, not a newly measured score for every selected run. See README and the forecast/replenishment policies for the audited results.")
    with st.expander("Run provenance"):
        st.json({k: summary.get(k) for k in ("mode", "planning_date", "input_sha256", "code_sha256", "tables")})

st.divider()
st.caption(f"VECTOR · Run {run_path.name} · {len(st.session_state.overrides)} edited SKU scenario(s) · Session drafts reset when the calculation run changes.")
