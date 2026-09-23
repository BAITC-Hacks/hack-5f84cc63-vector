"""Run with: python -m streamlit run app.py."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from functools import partial
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from vector_pipeline.dashboard import (discover_runs, draft_line, export_draft, fingerprint,
    load_products, load_run, review_draft, review_is_current, scenario)
from vector_pipeline.evidence_ui import render_evidence
from vector_pipeline.i18n import translate, localize_frame, error_text

st.set_page_config(page_title="Vector", page_icon="↗", layout="wide")
st.session_state.setdefault("language", "ru")
language = st.sidebar.radio("Язык / Language", ["ru", "en"], format_func=str.upper,
                            horizontal=True, key="language")
t = partial(translate, language=language)
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


@st.cache_data(show_spinner=False)
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
    st.caption(t('PROCUREMENT WORKSPACE'))
    st.divider()
    page = st.radio(t('Workspace'), ["Order planning", "Case validation", "Draft review", "Data coverage"],
                    label_visibility="collapsed", key="workspace", format_func=t)
    st.divider()
    runs = discover_runs(PROCESSED / "replenishment")
    if not runs:
        st.info(t('Create a replenishment run to start. See README → Replenishment v1.'))
        st.stop()
    selected_run = st.selectbox(t('Calculation run'), runs, format_func=lambda r:
        f"{t('Scenario' if r['mode'] == 'demo_scenario' else 'Explicit inputs')} · {r['planning_date']} · {Path(r['path']).name[:6]}", key="calculation_run")
    st.caption(t('Local workspace · No supplier connection'))

run_path = Path(selected_run["path"])
try:
    stamp = (file_stamp(run_path / "replenishment_summary.json"), file_stamp(run_path / "recommendations.jsonl"))
    summary, originals = cached_run(str(run_path), stamp)
except (OSError, ValueError, KeyError) as exc:
    st.error(t('Cannot open this run: {v0}', v0=exc))
    st.stop()
run_key = fingerprint([str(run_path), stamp])
if st.session_state.get("run_key") != run_key:
    st.session_state.update(run_key=run_key, overrides={}, cart={}, review=None)
try:
    products = cached_products(str(PROCESSED), summary.get("input_sha256", {}).get("ingestion_summary"))
except (OSError, ValueError, KeyError):
    products = {}
    st.warning(t('Product names could not be verified. Recommendations remain available by SKU.'))
original_by_id = {r["series_id"]: r for r in originals}
rows = [st.session_state.overrides.get(r["series_id"], r) for r in originals]


def product(row):
    return products.get((row["supplier_id"], row["sku_id"]), {})


def table_row(row):
    calc = row.get("calculation") or {}
    return {"Supplier": row["supplier_id"], "SKU": row["sku_id"],
            "Product": product(row).get("name") or t("Name unresolved"),
            "Action": URGENCY.get(row["urgency"], row["urgency"]),
            "Order": row["recommended_quantity"], "Unit": row["stock_uom"],
            "Available": calc.get("available_stock"), "Incoming": calc.get("incoming_supply_used"),
            "Shortage from": calc.get("first_shortage_without_order"),
            "Status": row["recommendation_status"], "Warehouse": row["warehouse_id"]}


st.markdown(f'<div class="eyebrow">{t("ELECTROKOMPLEKT / DECISION WORKSPACE")}</div>', unsafe_allow_html=True)
st.title(t(page))
scenario_count = sum(r["recommendation_status"] == "scenario" for r in rows)
if page == "Case validation":
    st.caption(t('Why Vector works · Before / after evidence for the five HackAlem requirements'))
else:
    st.caption(t('Planning date: {v0}  ·  {v1:,} SKU series  ·  Warehouse and stock units preserved', v0=summary['planning_date'], v1=len(rows)))
if page != "Case validation" and (summary["mode"] == "demo_scenario" or scenario_count):
    st.markdown(f'<div class="scenario-note"><b>{t("SCENARIO WORKSPACE")}</b> &nbsp; {t("Real sales forecasts with assumed stock, supplier times and supply coverage. Quantities and shortage alerts are scenario results, not confirmed purchasing needs.")}</div>', unsafe_allow_html=True)
elif page != "Case validation":
    st.info(t('Explicit-input mode: quantities remain unavailable wherever operational inputs are unresolved.'))
if notice := st.session_state.pop("notice", None):
    st.success(t(notice[0], **notice[1]) if isinstance(notice, tuple) else t(notice))


def show_projection(row):
    timeline = pd.DataFrame(row["timeline"])
    long = timeline.melt(id_vars=["date"], value_vars=["balance_without_order", "balance_with_order"],
                         var_name="Plan", value_name="Projected balance")
    long["Plan"] = long["Plan"].map({"balance_without_order": t("Existing supply"), "balance_with_order": t("With recommended order")})
    chart = alt.Chart(long).mark_line(strokeWidth=2.5).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%d.%m")),
        y=alt.Y("Projected balance:Q", title=t("Projected balance ({unit})", unit=row['stock_uom'])),
        color=alt.Color("Plan:N", scale=alt.Scale(domain=[t("Existing supply"), t("With recommended order")],
            range=["#D18B49", "#087F71"]), legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("date:T", title=t("Date")), alt.Tooltip("Plan:N", title=t("Plan")), alt.Tooltip("Projected balance:Q", title=t("Projected balance"), format=",.2f")])
    zero = alt.Chart(pd.DataFrame({"zero": [0]})).mark_rule(color="#9CAABB", strokeDash=[4, 4]).encode(y="zero:Q")
    st.altair_chart((chart + zero).properties(height=275), width="stretch")
    st.caption(t('Negative balance represents accumulated unmet demand in the scenario. It is not measured lost sales. The chart uses the engine recommendation, before any manual draft quantity change.'))


def show_details(row):
    sid = row["series_id"]
    original = original_by_id[sid]
    info = product(row)
    st.divider()
    st.subheader(t('{v0} · {v1}', v0=row['sku_id'], v1=row['supplier_id']))
    st.write(info.get("name") or t("Product name unresolved in the source data"))
    st.caption(t('{v0} · {v1} · Model: {v2}', v0=row['warehouse_id'], v1=row['stock_uom'], v2=t(row.get('forecast_model') or 'unavailable')))
    tabs = st.tabs([t(label) for label in ["Order explanation", "What-if scenario", "Source evidence"]],
                   default=t(st.session_state.pop("detail_default", "Order explanation")))
    with tabs[0]:
        calc = row.get("calculation")
        if not calc:
            st.warning(t("Calculation unavailable: {reasons}", reasons=", ".join(t(reason) for reason in row["unavailable_reasons"])))
        else:
            left, right = st.columns([1, 1.65], gap="large")
            with left:
                st.markdown(f'<div class="eyebrow">{t("RECOMMENDED ORDER")}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="hero-qty">{fmt(row["recommended_quantity"])} <span style="font-size:20px">{escape(row["stock_uom"])}</span></div>', unsafe_allow_html=True)
                st.caption(t('{v0} · {v1}-day protection period', v0=t(row['recommendation_status']), v1=calc['protection_days']))
                components = {"Forecast over protection period": calc["forecast_demand"],
                    "+ Safety buffer": calc["safety_stock"], "− Available stock": calc["available_stock"],
                    "− Dated incoming supply": calc["incoming_supply_used"], "= Net requirement": calc["net_requirement"],
                    "Timing requirement": calc["timing_requirement"], "Rounding added": calc["rounding_increment"]}
                st.dataframe(localize_frame(pd.DataFrame({"Component": list(components), "Quantity": [fmt(v) for v in components.values()]}), language),
                    hide_index=True, width="stretch")
                st.caption(t('Order = round(max(0, net requirement, timing requirement)), subject to applied minimum and multiple.'))
                unresolved = sum(not c.get("applied", False) for c in row["constraint_audit"])
                if unresolved:
                    st.caption(t('{v0} supplier constraint(s) unresolved / not applied. Stock-unit rounding does not establish supplier MOQ.', v0=unresolved))
            with right:
                st.markdown(t("**Inventory projection**"))
                show_projection(row)
                if calc["peak_shortage_before_new_order"] > 0:
                    st.warning(t('Earlier supply needed: projected shortage starts {v0}; the new order arrives {v1}. Expedite or transfer stock to cover the gap.', v0=calc['first_shortage_without_order'], v1=calc['new_order_arrival_date']))
                else:
                    st.success(t('The plan covers demand before the new order arrives on {v0}.', v0=calc['new_order_arrival_date']))
            st.caption(t('Stockout compensation: {v0}. Forecast quantity is based on regular observed sales unless explicit intervals were supplied.', v0=t(row['stockout_adjustment_status'])))
            with st.form(f"draft-{sid}"):
                cols = st.columns([1, 2, 1])
                quantity = cols[0].number_input(t('Draft quantity'), min_value=0.0, value=float(row["recommended_quantity"]),
                    key=f"draft-qty-{fingerprint(row)[:16]}")
                reason = cols[1].text_input(t('Reason for manual quantity change'), key=f"draft-reason-{sid}")
                add = cols[2].form_submit_button(t('Add / update draft'), width="stretch")
            if add:
                try:
                    st.session_state.cart[sid] = draft_line(row, quantity, reason)
                    invalidate_review()
                    st.session_state.notice = "Added to draft. Open Draft review to check and export the selected lines."
                    st.rerun()
                except ValueError as exc:
                    st.error(error_text(exc, language))
    with tabs[1]:
        if not original.get("calculation"):
            st.info(t('This version edits eligible recommendations. Resolve the missing upstream inputs and create a new run for this series.'))
        else:
            st.caption(t('Changes apply only to this SKU in this browser session. The stored run and forecasts stay unchanged. Receipt delay affects existing receipts on or after the planning date; the additional receipt uses its own date.'))
            base = original["calculation"]
            current = row.get("ui_scenario", {})
            widget_prefix = f"scenario-{sid}-{fingerprint(current)[:12]}"
            with st.form(f"scenario-{sid}"):
                a, b, c = st.columns(3)
                stock = a.number_input(t('Available stock ({v0})', v0=row['stock_uom']), min_value=0.0, value=float(current.get("stock", base["available_stock"])), key=widget_prefix+"-stock")
                lead = b.number_input(t('Lead time (days)'), min_value=0, max_value=365, value=int(current.get("lead", base["lead_time_days"])), key=widget_prefix+"-lead")
                review = c.number_input(t('Review period (days)'), min_value=1, max_value=365, value=int(current.get("review", base["review_period_days"])), key=widget_prefix+"-review")
                a, b, c = st.columns(3)
                safety = a.number_input(t('Safety buffer (days)'), min_value=0.0, max_value=365.0, value=float(current.get("safety", base["safety_days"])), key=widget_prefix+"-safety")
                delay = b.number_input(t('Existing receipt delay (days)'), min_value=0, max_value=365, value=int(current.get("delay", 0)), key=widget_prefix+"-delay")
                receipt_qty = c.number_input(t('Additional incoming ({v0})', v0=row['stock_uom']), min_value=0.0, value=float(current.get("receipt_qty", 0)), key=widget_prefix+"-receipt-qty")
                receipt_date = st.date_input(t('Additional receipt arrival'), value=date.fromisoformat(current.get("receipt_date") or row["planning_date"]) + (timedelta(days=14) if not current.get("receipt_date") else timedelta()), min_value=date.fromisoformat(row["planning_date"]), key=widget_prefix+"-receipt-date")
                reason = st.text_input(t('Scenario reason'), value=current.get("reason", ""), placeholder=t('For example: supplier delay or a revised stock count'), key=widget_prefix+"-reason")
                applied = st.form_submit_button(t('Recalculate SKU'), type="primary")
            if applied:
                try:
                    changed = scenario(original, summary["effective_config"]["policies"], stock=stock,
                        lead=lead, review=review, safety=safety, delay=delay, reason=reason,
                        receipt_qty=receipt_qty, receipt_date=receipt_date.isoformat())
                    st.session_state.overrides[sid] = changed
                    # Old manual quantities must not survive an upstream scenario change unnoticed.
                    st.session_state.cart.pop(sid, None)
                    invalidate_review()
                    st.session_state.notice = ("Recalculated {sku}: {before} → {after} {unit}. If the SKU no longer matches the filters, select All actions.",
                        {"sku": row['sku_id'], "before": fmt(original['recommended_quantity']), "after": fmt(changed['recommended_quantity']), "unit": row['stock_uom']})
                    st.rerun()
                except ValueError as exc:
                    st.error(error_text(exc, language))
            if sid in st.session_state.overrides:
                st.info(t('Scenario applied. Original order: {v0} → current order: {v1} {v2}. Add the revised line to the draft again if needed.', v0=fmt(original['recommended_quantity']), v1=fmt(row['recommended_quantity']), v2=row['stock_uom']))
                if st.button(t('Reset this SKU'), key=f"reset-{sid}"):
                    del st.session_state.overrides[sid]
                    st.session_state.cart.pop(sid, None)
                    invalidate_review()
                    st.rerun()
    with tabs[2]:
        forecasts = row.get("forecast_values_used", [])
        if forecasts:
            st.markdown(t("**Monthly forecast used by the engine**"))
            st.dataframe(localize_frame(pd.DataFrame([{"Month": f["target_month"], "Forecast": f["forecast_qty"],
                "Mean baseline": f.get("baseline_forecast_qty"), "Model": f["model"],
                "As of": f["as_of_date"]} for f in forecasts]), language), hide_index=True, width="stretch")
        st.caption(t('Seasonality and growth are included in the model where supported; they are not added a second time. Category and customer IDs are unavailable in the supplied data.'))
        with st.expander(t('Input values, evidence and assumptions')):
            st.caption(t("Audit keys, source values and CSV/JSON exports retain their original language and schema."))
            st.json(row["inputs"])
            st.write(t('Assumption IDs'), row["assumption_ids"])
        with st.expander(t('Incoming supply and ordering constraints')):
            st.json({"incoming": row["incoming_audit"], "constraints": row["constraint_audit"]})
        with st.expander(t('Product identity and source references')):
            st.json(info or {"sku_id": row["sku_id"], "product_dimension": "unavailable"})


if page == "Order planning":
    a, b, c, d = st.columns(4)
    a.metric(t('Ready for review'), f"{sum(r['recommended_quantity'] is not None for r in rows):,}")
    b.metric(t('Earlier supply needed'), f"{sum(r['urgency'] == 'expedite_or_transfer' for r in rows):,}")
    c.metric(t('Missing inputs'), f"{sum(r['recommendation_status'] == 'unavailable' for r in rows):,}")
    d.metric(t('Draft lines'), len(st.session_state.cart))
    st.caption(t('Counts cover the entire selected run, including session edits. Each series is a supplier / SKU / warehouse / unit combination.'))
    st.subheader(t('Recommendations'))
    cols = st.columns([1, 1.4, 1, 1, 1.7])
    supplier = cols[0].selectbox(t('Supplier'), ["All suppliers"] + sorted({r["supplier_id"] for r in rows}), key="filter_supplier", format_func=t)
    action = cols[1].selectbox(t('Action'), ["Positive orders", "All actions"] + list(URGENCY.values()), key="filter_action", format_func=t)
    warehouse = cols[2].selectbox(t('Warehouse'), ["All warehouses"] + sorted({r["warehouse_id"] for r in rows if r["warehouse_id"]}), key="filter_warehouse", format_func=t)
    unit = cols[3].selectbox(t('Unit'), ["All units"] + sorted({r["stock_uom"] for r in rows if r["stock_uom"]}), key="filter_unit", format_func=t)
    query = cols[4].text_input(t('Find SKU or product'), placeholder=t('Type a code or name'), key="filter_query")
    filtered = [r for r in rows if (supplier == "All suppliers" or r["supplier_id"] == supplier)
        and (warehouse == "All warehouses" or r["warehouse_id"] == warehouse)
        and (unit == "All units" or r["stock_uom"] == unit)
        and (action == "All actions" or (action == "Positive orders" and (r["recommended_quantity"] or 0) > 0)
             or URGENCY.get(r["urgency"]) == action)
        and (not query or query.casefold() in (r["sku_id"] + " " + (product(r).get("name") or "") + " " + (product(r).get("supplier_article") or "")).casefold())]
    filtered.sort(key=lambda r: (list(URGENCY).index(r["urgency"]), r["supplier_id"], r["sku_id"]))
    st.caption(t('{v0:,} matching series · Select a row to inspect the calculation. Quantities are never summed across different units.', v0=len(filtered)))
    if filtered:
        event = st.dataframe(localize_frame(pd.DataFrame([table_row(r) for r in filtered]), language), hide_index=True,
            width="stretch", height=330, on_select="rerun", selection_mode="single-row",
            key=f"orders-{fingerprint([r['series_id'] for r in filtered])[:16]}",
            column_config={t("Order"): st.column_config.NumberColumn(format="%.2f"),
                           t("Available"): st.column_config.NumberColumn(format="%.2f"),
                           t("Incoming"): st.column_config.NumberColumn(format="%.2f")})
        chosen = event.selection.rows[0] if event.selection.rows else 0
        show_details(filtered[chosen])
    else:
        st.info(t('No matching recommendations. Adjust the filters; use All actions to inspect unavailable series.'))

elif page == "Case validation":
    render_evidence(ROOT, PROCESSED, summary, navigate)

elif page == "Draft review":
    cart = st.session_state.cart
    st.write(t('Review the selected lines by supplier, then export a local draft. Exports do not send orders or authorize a purchase in another system.'))
    if not cart:
        st.info(t('Your draft is empty. Select a recommendation in Order planning and add it to the draft.'))
    else:
        for supplier_id in sorted({line["recommendation"]["supplier_id"] for line in cart.values()}):
            st.subheader(supplier_id)
            selected = [line for line in cart.values() if line["recommendation"]["supplier_id"] == supplier_id]
            st.dataframe(localize_frame(pd.DataFrame([{**table_row(x["recommendation"]), "Draft quantity": x["order_quantity"],
                "Manager reason": x["manager_reason"]} for x in selected]), language), hide_index=True, width="stretch")
        remove = st.multiselect(t("Remove draft lines"), list(cart), format_func=lambda sid:
            f"{cart[sid]['recommendation']['supplier_id']} · {cart[sid]['recommendation']['sku_id']} · {cart[sid]['recommendation']['stock_uom']}", key="remove_draft_lines")
        if st.button(t('Remove selected'), disabled=not remove):
            for sid in remove:
                del cart[sid]
            invalidate_review()
            st.rerun()
        with st.form("review-draft"):
            reviewer = st.text_input(t('Reviewer name'), key="reviewer_name")
            acknowledged = st.checkbox(t('I reviewed these exact quantities, scenario assumptions, timing alerts and unresolved supplier constraints.'), key="review_acknowledgement")
            submitted = st.form_submit_button(t('Mark draft reviewed'), type="primary")
        if submitted:
            try:
                st.session_state.review = review_draft(cart, reviewer, acknowledged, datetime.now(timezone.utc).isoformat())
            except ValueError as exc:
                st.error(error_text(exc, language))
        if review_is_current(cart, st.session_state.review):
            csv_bytes, json_bytes = export_draft(cart, st.session_state.review, run_path.name)
            st.success(t('Reviewed by {v0}. Export is ready; nothing has been sent.', v0=st.session_state.review['reviewer']))
            a, b = st.columns(2)
            a.download_button(t('Download reviewed CSV'), csv_bytes, file_name="vector_reviewed_draft.csv", mime="text/csv", width="stretch")
            b.download_button(t('Download full audit JSON'), json_bytes, file_name="vector_reviewed_draft.json", mime="application/json", width="stretch")
            st.caption(t("CSV is a generic UTF-8 exchange format; compatibility with the company's 1C import template is not yet validated. JSON preserves exact SKU strings, source inputs and calculation details. This local review is not an authenticated corporate approval."))
        else:
            st.caption(t('Export becomes available after review. Any change to draft lines or their scenario invalidates the previous review.'))

else:
    st.subheader(t('What this run can support'))
    a, b, c = st.columns(3)
    a.metric(t('Series in run'), f"{len(rows):,}")
    b.metric(t('Scenario quantities'), f"{scenario_count:,}")
    c.metric(t('Unavailable quantities'), f"{sum(r['recommended_quantity'] is None for r in rows):,}")
    st.dataframe(localize_frame(pd.DataFrame([{"Supplier": supplier_id,
        "Total series": sum(r["supplier_id"] == supplier_id for r in rows),
        "Available recommendations": sum(r["supplier_id"] == supplier_id and r["recommended_quantity"] is not None for r in rows)}
        for supplier_id in sorted({r["supplier_id"] for r in rows})]), language), hide_index=True, width="stretch")
    reasons = Counter(reason for r in rows for reason in r["unavailable_reasons"])
    st.markdown(t("**Reasons a quantity is unavailable**"))
    st.dataframe(localize_frame(pd.DataFrame([{"Reason": k, "Series": v} for k, v in reasons.most_common()]), language), hide_index=True, width="stretch")
    st.markdown(t("**Evidence limits on the supplied dataset**"))
    st.write(t('Current available stock, supplier lead times and actual stockout intervals have not been confirmed. Category and customer IDs are missing. Historical monthly stock is not treated as current stock, and document IDs are not customer IDs.'))
    st.write(t('Forecast v1 uses regular observed sales. Model selection used rolling historical origins before the final August 2026 holdout. On that holdout, the selected policy lost to the mean baseline for IEK and SystemElectric pieces; it improved IEK meters and packs. No inventory-cost improvement has been established.'))
    st.write(t('The real data provide at most 20 consecutive complete months. Two-cycle trend/seasonality fitting is not established on those series; synthetic tests verify that the forecasting and stockout mechanisms work.'))
    st.caption(t('These findings describe the supplied project dataset, not a newly measured score for every selected run. See README and the forecast/replenishment policies for the audited results.'))
    with st.expander(t('Run provenance')):
        st.json({k: summary.get(k) for k in ("mode", "planning_date", "input_sha256", "code_sha256", "tables")})

st.divider()
st.caption(t('VECTOR · Run {v0} · {v1} edited SKU scenario(s) · Session drafts reset when the calculation run changes.', v0=run_path.name, v1=len(st.session_state.overrides)))
