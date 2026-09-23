"""Reproduce the five case checks. Synthetic evidence is never a business order."""
import argparse
from copy import deepcopy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from vector_pipeline.dashboard import checked_table, export_draft, load_run, scenario
from vector_pipeline.demand import clean_transactions
from vector_pipeline.forecast import build_series, forecast_series, shift_month
from vector_pipeline.replenishment import recommend
from vector_pipeline.stockout import correct_history, refit_adjusted

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {name: json.loads((ROOT / f"config/{name}.json").read_text(encoding="utf-8"))
           for name in ("demand", "forecast", "replenishment")}
# Fixed before running the injection test; applies to this supported-history fixture.
MAX_ONE_OFF_ORDER_INFLATION = 0.01


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assumption(value=None, **extra):
    return {"value": value, "status": "scenario", "id": "ACCEPTANCE-SYNTHETIC-V1",
            "reason": "Synthetic acceptance fixture, not partner data", **extra}


def order_inputs():
    return {"available_stock": assumption(15, snapshot_date="2026-10-01"),
            "lead_time_days": assumption(3), "review_period_days": assumption(7),
            "safety_days": assumption(2), "incoming": assumption(shipments=[]),
            "constraints": [], "stock_quantity_increment": assumption(1)}


def sale(identifier, month, quantity, day=15, sku="SYN-ACCEPTANCE"):
    return {"record_id": identifier, "source_id": "iek_transactions", "source_sheet": "Synthetic acceptance",
            "source_row": 1, "source_column": "quantity", "supplier_id": "IEK", "sku_id": sku,
            "sku_raw": sku, "quality_flags": [], "assumption_ids": ["ACCEPTANCE-SYNTHETIC-V1"],
            "is_synthetic": True, "occurred_at": f"{month}-{day:02d}T12:00:00",
            "document_number": identifier, "document_text": "Synthetic sale", "warehouse_id": "SYN-WAREHOUSE",
            "qty_raw": quantity, "qty_regular": quantity, "qty_source_value": quantity,
            "stock_uom": "pieces", "is_outlier": False, "customer_id": None, "price": None, "currency": None}


def forecast(rows, start="2025-01"):
    config = deepcopy(CONFIGS["forecast"])
    config["source_coverage"] = {"iek_transactions": {"start_month": start, "status": "assumption",
        "id": "ACCEPTANCE-SYNTHETIC-COVERAGE", "reason": "Complete generated monthly sales"}}
    series, monthly, _ = build_series(rows, config)
    check(len(series) == 1, "Fixture must contain exactly one series")
    coverage, predicted, scores, backtest = forecast_series(series[0], config)
    check(coverage["status"] == "forecastable", "Synthetic history must be forecastable")
    check(all(r["target_month"] < coverage["holdout_month"] for r in backtest
              if r["evaluation_role"] == "selection"), "Model selection must precede holdout")
    return predicted, monthly, coverage, config


def order(predicted, inputs=None):
    result = recommend(predicted[0], predicted, inputs or order_inputs(), "2026-10-01",
                       CONFIGS["replenishment"]["policies"])
    result["forecast_values_used"] = deepcopy(predicted)
    check(result["recommended_quantity"] is not None, "Fixture order unexpectedly unavailable")
    return result


def base_case():
    predicted, _, _, _ = forecast([sale(str(i), shift_month("2025-01", i), 310) for i in range(20)])
    base = order(predicted)
    higher = deepcopy(predicted)
    for row in higher:
        row["forecast_qty"] *= 1.2
    variants = {"baseline": base["recommended_quantity"], "forecast_plus_20_percent": order(higher)["recommended_quantity"]}
    for name, key, value in (("stock_55", "available_stock", 55), ("lead_6_days", "lead_time_days", 6),
                             ("review_10_days", "review_period_days", 10), ("safety_5_days", "safety_days", 5)):
        inputs = order_inputs()
        inputs[key]["value"] = value
        variants[name] = order(predicted, inputs)["recommended_quantity"]
    for name, arrival in (("incoming_30_on_oct_02", "2026-10-02"), ("incoming_30_on_oct_11", "2026-10-11")):
        inputs = order_inputs()
        inputs["incoming"]["shipments"] = [{**assumption(), "shipment_id": name, "arrival_date": arrival,
            "qty_stock_units": 30, "warehouse_id": predicted[0]["warehouse_id"], "stock_uom": "pieces"}]
        variants[name] = order(predicted, inputs)["recommended_quantity"]
    check(variants == {"baseline": 105, "forecast_plus_20_percent": 129, "stock_55": 65,
        "lead_6_days": 135, "review_10_days": 135, "safety_5_days": 135,
        "incoming_30_on_oct_02": 75, "incoming_30_on_oct_11": 105}, "Unexpected input sensitivity")
    return {"evidence_type": "synthetic", "order_quantities": variants,
            "unit": "pieces", "planning_date": "2026-10-01"}


def seasonal_case():
    pattern = [0, 10, 20, 10, 0, 30, 50, 60, 10, 0, -10, -20]
    values = [100 + 5*i + pattern[i % 12] for i in range(36)]
    predicted, _, coverage, _ = forecast([sale(str(i), shift_month("2023-09", i), v)
                                          for i, v in enumerate(values)], "2023-09")
    check(coverage["selected_model"] == "trend_seasonal", "Expected validated seasonal-growth model")
    check([r["forecast_qty"] for r in predicted] == [295, 310, 305], "Seasonal pattern not reproduced")
    check(all(r["components"]["seasonality_applied"] and r["components"]["slope_per_month"] == 5
              for r in predicted), "Growth/seasonality components incorrect")
    flat_growth, _, _, _ = forecast([sale(str(i), shift_month("2023-09", i), 100+5*i)
                                     for i in range(36)], "2023-09")
    no_growth, _, _, _ = forecast([sale(str(i), shift_month("2023-09", i), 100+pattern[i % 12])
                                   for i in range(36)], "2023-09")
    check([r["forecast_qty"] for r in flat_growth] == [285, 290, 295], "Growth-only counterfactual incorrect")
    check([r["forecast_qty"] for r in no_growth] == [110, 120, 110], "Seasonality-only counterfactual incorrect")
    return {"evidence_type": "synthetic_36_months", "history_rule": "100 + 5 * month_index + repeating_12_month_pattern",
            "pattern": pattern, "months": [r["target_month"] for r in predicted],
            "seasonality_and_growth": [r["forecast_qty"] for r in predicted],
            "growth_only": [r["forecast_qty"] for r in flat_growth],
            "seasonality_only": [r["forecast_qty"] for r in no_growth], "selected_model": coverage["selected_model"],
            "holdout_month": coverage["holdout_month"], "components": predicted[0]["components"]}


def stockout_case():
    quantities = [310, 300, 310, 300, 20, 310]
    predicted, months, coverage, cfg = forecast([sale(str(i), shift_month("2026-03", i), v)
                                               for i, v in enumerate(quantities)], "2026-03")
    interval = {**assumption(), "interval_id": "SYN-JULY-STOCKOUT", "start_date": "2026-07-01",
                "end_date_exclusive": "2026-07-30", "known_at": "2026-07-31"}
    daily = {f"2026-07-{day:02d}": 0 if day < 30 else 10 for day in range(1, 32)}
    corrected, audit = correct_history(months, daily, [interval], "2026-09-22", CONFIGS["replenishment"]["stockout"])
    adjusted = refit_adjusted(predicted, corrected, cfg, audit)
    before = order(predicted)
    inputs = order_inputs()
    inputs["stockout_adjustment_status"] = audit["status"]
    after = order(adjusted, inputs)
    check(audit["estimated_lost_sales"] == 290, "Expected 29 days at 10 units/day")
    check(before["recommended_quantity"] == 67 and after["recommended_quantity"] == 104, "Stockout compensation must increase regular order")
    check(predicted[0]["model"] == adjusted[0]["model"] == "mean", "Do not reselect model during stockout refit")
    missing, missing_audit = correct_history(months, daily, [], "2026-09-22", CONFIGS["replenishment"]["stockout"])
    check(missing == months and missing_audit["estimated_lost_sales"] == 0, "No intervals must mean no invented correction")
    future = {**interval, "known_at": "2027-01-01"}
    check(correct_history(months, daily, [future], "2026-09-22", CONFIGS["replenishment"]["stockout"])[1]["estimated_lost_sales"] == 0,
          "Future interval knowledge leaked into correction")
    return {"evidence_type": "synthetic_interval_and_sales", "real_intervals_supplied": False,
            "observed_july": 20, "corrected_july": 310, "estimated_lost_sales": 290,
            "forecast_before": predicted[0]["forecast_qty"], "forecast_after": adjusted[0]["forecast_qty"],
            "order_before": before["recommended_quantity"], "order_after": after["recommended_quantity"],
            "unit": "pieces", "daily_audit": audit["daily_audit"]}


def one_off_case():
    transactions = [sale(f"normal-{month}-{day}", shift_month("2025-01", month), 10, day)
                    for month in range(20) for day in range(1, 21)]
    injected = sale("injected-one-off", "2026-08", 9000, 28)
    baseline, _ = clean_transactions(transactions, CONFIGS["demand"])
    cleaned, docs = clean_transactions(transactions + [injected], CONFIGS["demand"])
    flagged = next(d for d in docs if d["document_number"] == injected["document_number"])
    check(flagged["is_outlier"] and flagged["qty_raw"] == 9000 and flagged["qty_regular"] == 0,
          "Extreme document must be retained raw and excluded from regular sales")
    baseline_forecasts = forecast(baseline)[0]
    cleaned_forecasts = forecast(cleaned)[0]
    # Explicit counterfactual: same raw sales, with screening bypassed; no stored rows are changed.
    unfiltered = [{**row, "qty_regular": row["qty_raw"], "is_outlier": False} for row in cleaned]
    raw_forecasts = forecast(unfiltered)[0]
    quantities = {"baseline": order(baseline_forecasts)["recommended_quantity"],
                  "with_screening": order(cleaned_forecasts)["recommended_quantity"],
                  "without_screening": order(raw_forecasts)["recommended_quantity"]}
    inflation = quantities["with_screening"] / quantities["baseline"] - 1
    check(inflation <= MAX_ONE_OFF_ORDER_INFLATION, "One-off materially inflates regular replenishment")
    check(quantities["without_screening"] > quantities["baseline"] * 5, "Injection must actually challenge downstream ordering")
    check(flagged["baseline"]["history_end"] < flagged["date"], "Detector used future information")
    check(all(not r["is_outlier"] for r in cleaned if r["record_id"] != injected["record_id"]), "Normal sales incorrectly excluded")
    return {"evidence_type": "synthetic_transactions_through_cleaning_forecast_and_order",
            "normal_documents": len(transactions), "injected_quantity": 9000,
            "injection_month": "2026-08", "threshold": flagged["baseline"]["threshold"],
            "allowed_relative_inflation": MAX_ONE_OFF_ORDER_INFLATION, "observed_relative_inflation": inflation,
            "order_quantities": quantities, "baseline_forecast": baseline_forecasts[0]["forecast_qty"],
            "cleaned_forecast": cleaned_forecasts[0]["forecast_qty"],
            "unscreened_forecast": raw_forecasts[0]["forecast_qty"], "unit": "pieces"}


def workflow_case():
    """Drive the actual app with isolated generated data, never a human's live draft."""
    from streamlit.testing.v1 import AppTest

    def widget(items, label):
        return next(x for x in items if x.label == label)

    predicted = forecast([sale(str(i), shift_month("2025-01", i), 310) for i in range(20)])[0]
    first = order(predicted)
    se = [{**row, "series_id": "SYN-SE", "supplier_id": "SystemElectric", "sku_id": "SYN-SE-001"} for row in predicted]
    second = order(se)
    with tempfile.TemporaryDirectory(prefix="vector-acceptance-") as tmp, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": tmp}):
        run_path = Path(tmp) / "replenishment" / "synthetic-acceptance"
        run_path.mkdir(parents=True)
        raw = "".join(json.dumps(r) + "\n" for r in (first, second)).encode()
        (run_path / "recommendations.jsonl").write_bytes(raw)
        summary = {"status": "complete", "mode": "acceptance_synthetic", "planning_date": "2026-10-01",
            "effective_config": CONFIGS["replenishment"], "tables": {"recommendations.jsonl": {
                "rows": 2, "sha256": hashlib.sha256(raw).hexdigest()}}}
        (run_path / "replenishment_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        check(not app.exception, "Dashboard did not render")
        widget(app.number_input, "Draft quantity").set_value(110)
        widget(app.button, "Add / update draft").click().run()
        check(not app.session_state.cart and app.error, "Unexplained manual adjustment should be rejected")
        widget(app.text_input, "Reason for manual quantity change").set_value("Synthetic manager adjustment")
        widget(app.button, "Add / update draft").click().run()
        widget(app.selectbox, "Supplier").set_value("SystemElectric").run()
        widget(app.button, "Add / update draft").click().run()
        app.radio[0].set_value("Draft review").run()
        check(not app.get("download_button"), "Reviewed export exposed before review")
        check({h.value for h in app.subheader} == {"IEK", "SystemElectric"}, "Draft not grouped by supplier")
        widget(app.text_input, "Reviewer name").set_value("SYNTHETIC ACCEPTANCE TEST — not a human approval")
        app.checkbox[0].check()
        widget(app.button, "Mark draft reviewed").click().run()
        check(len(app.get("download_button")) == 2, "Reviewed CSV and JSON downloads missing")
        cart, review = app.session_state.cart, app.session_state.review
        csv_bytes, json_bytes = export_draft(cart, review, "synthetic-acceptance")
        audit = json.loads(json_bytes)
        exported = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        check([r["supplier_id"] for r in exported] == ["IEK", "SystemElectric"], "CSV supplier grouping lost")
        check([r["order_quantity"] for r in exported] == ["110.0", "105.0"], "Manual quantity not preserved")
        check(all(r["explanation"] for r in exported), "Every order requires an explanation")
        check(audit["status"] == "reviewed_draft_not_sent", "Export status must remain a draft")
        app.radio[0].set_value("Order planning").run()
        widget(app.selectbox, "Supplier").set_value("IEK").run()
        widget(app.number_input, "Available stock (pieces)").set_value(55)
        widget(app.text_input, "Scenario reason").set_value("Synthetic stock revision")
        widget(app.button, "Recalculate SKU").click().run()
        check(app.session_state.review is None and first["series_id"] not in app.session_state.cart,
              "Scenario edit must invalidate review and remove the stale line")
        check(len(app.session_state.cart) == 1, "Unrelated supplier draft line must remain")
        app.radio[0].set_value("Draft review").run()
        check(not app.get("download_button") and not app.exception, "Stale export still available")
    return {"evidence_type": "AppTest_with_synthetic_records", "suppliers": ["IEK", "SystemElectric"],
            "recommended_quantities": [105, 105], "reviewed_draft_quantities": [110, 105],
            "reason_required": True, "review_required": True, "edit_revokes_review": True,
            "stale_line_removed": True, "unrelated_line_preserved": True,
            "csv_and_json_verified": True, "human_approval_performed": False, "sent_to_supplier": False,
            "visual_browser_qa": "not_performed_no_browser_connector"}


def real_evidence(replenishment, demand):
    summary, rows = load_run(replenishment)
    first = next(r for r in rows if r["supplier_id"] == "IEK" and r["sku_id"] == "010300002_")
    second = next(r for r in rows if r["supplier_id"] == "SystemElectric" and r["sku_id"] == "010400432_")
    modified = []
    for receipt in (0, 100):
        changed = scenario(first, summary["effective_config"]["policies"], stock=200, lead=45, review=30,
            safety=7, delay=0, reason="Jury rehearsal scenario", receipt_qty=receipt, receipt_date="2026-10-02")
        modified.append(changed["recommended_quantity"])
    check(first["recommended_quantity"] == 404 and modified == [272, 172] and second["recommended_quantity"] == 9,
          "Real-data walkthrough numbers changed; update DEMO.md after investigation")
    demand = Path(demand)
    manifest = json.loads((demand / "demand_summary.json").read_text(encoding="utf-8"))
    check(manifest["status"] == "complete", "Demand run incomplete")
    candidates = checked_table(demand / "outlier_records.jsonl", manifest["tables"]["outlier_records.jsonl"])
    example = next(r for r in candidates if r["supplier_id"] == "SystemElectric" and r["sku_id"] == "030200192_"
                   and r["date"] == "2025-05-06" and r["qty_raw"] == 90000)
    check(example["qty_regular"] == 0 and example["baseline"]["median"] == 50 and
          example["baseline"]["threshold"] == 1220 and example["baseline"]["history_end"] < example["date"],
          "Real candidate evidence changed")
    return {"evidence_type": "real_observed_sales_with_scenario_inventory",
            "replenishment_run": Path(replenishment).name, "demand_run": demand.name,
            "recommendation_sha256": summary["tables"]["recommendations.jsonl"]["sha256"],
            "outlier_sha256": manifest["tables"]["outlier_records.jsonl"]["sha256"],
            "IEK": {"sku": first["sku_id"], "baseline_order": 404, "stock_200_order": 272,
                    "stock_200_incoming_100_order": 172, "calculation": first["calculation"]},
            "SystemElectric": {"sku": second["sku_id"], "baseline_order": 9}, "real_outlier_candidate": example}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replenishment", type=Path, default=ROOT / "data/processed/replenishment/0f0c62e51923")
    parser.add_argument("--demand", type=Path, default=ROOT / "data/processed/demand/050e703d461b")
    args = parser.parse_args()
    tracked = sorted((ROOT / "src/vector_pipeline").glob("*.py")) + sorted((ROOT / "config").glob("*.json"))
    # The complete source manifest protects all supplied workbooks, including duplicate files.
    source_registry = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))
    raw_paths = sorted((ROOT / "data/IEK").glob("*.xlsx")) + sorted((ROOT / "data/system_electric").glob("*.xlsx"))
    frozen_before = {str(p.relative_to(ROOT)): sha(p) for p in tracked + raw_paths}
    check(set(raw_paths) == {ROOT / s["path"] for s in source_registry["sources"]},
          "Workbook inventory differs from the source registry")
    check(all(frozen_before[str(Path(s["path"]))] == s["sha256"] for s in source_registry["sources"]),
          "A workbook differs from its registered source hash")
    report = {"version": 1, "cases": {}, "unchanged_input_sha256": frozen_before,
              "acceptance_script_sha256": sha(__file__), "case_definition_sha256": sha(ROOT / "docs/CASE.md"),
              "app_sha256": sha(ROOT / "app.py"), "registered_workbooks_verified": len(raw_paths),
              "not_a_business_approval": True}
    specs = [
        ("A", "PARTIAL", "Base replenishment", base_case,
         ["Numeric input sensitivity passes. Categories are absent; supplied aggregate coefficients lack validated SKU applicability/availability and are not applied.",
          "Real current stock, supplier times and complete incoming supply are not confirmed."]),
        ("B", "PASS", "Seasonality and sustainable growth", seasonal_case,
         ["Synthetic evidence only for joint learned seasonality/growth; real history has at most 20 complete consecutive months."]),
        ("C", "PASS", "Stockout compensation", stockout_case,
         ["Synthetic interval test passes. Real stockout intervals were not supplied; no real lost-sales estimate is claimed."]),
        ("D", "PARTIAL", "One-off exclusion", one_off_case,
         ["Document-level mechanism passes; client-level detection and business labels cannot be verified without anonymized customer IDs.",
          "The 1% inflation bound is an acceptance criterion for this supported-history fixture, not a universal accuracy guarantee."]),
        ("E", "PASS", "Supplier-grouped explained reviewed draft", workflow_case,
         ["Local review, not authenticated corporate authorization. Exact 1C template compatibility and visual browser QA remain unverified."])]
    for key, status, title, run, limitations in specs:
        try:
            report["cases"][key] = {"status": status, "mechanism": "PASS", "title": title,
                                     "evidence": run(), "limitations": limitations}
        except Exception as exc:
            report["cases"][key] = {"status": "FAIL", "title": title, "error": f"{type(exc).__name__}: {exc}"}
    try:
        report["real_examples"] = real_evidence(args.replenishment, args.demand)
    except Exception as exc:
        report["real_examples"] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    report["input_files_unchanged"] = frozen_before == {str(p.relative_to(ROOT)): sha(p) for p in tracked + raw_paths}
    check(report["input_files_unchanged"], "Acceptance run modified frozen code/config or source Excel")
    failed = any(c["status"] == "FAIL" for c in report["cases"].values()) or report["real_examples"].get("status") == "FAIL"
    report["execution_status"] = "FAIL" if failed else "PASS"
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    output = ROOT / "data/processed/acceptance" / hashlib.sha256(raw).hexdigest()[:12]
    output.mkdir(parents=True, exist_ok=True)
    (output / "case_acceptance.json").write_bytes(raw)
    for key, case in report["cases"].items():
        print(f"{key} {case['status']}: {case['title']}")
        if "error" in case:
            print("  " + case["error"])
    if not failed:
        print("A synthetic: baseline 105; forecast +20% -> 129; stock 55 -> 65; timely incoming 30 -> 75; late incoming -> 105.")
        print("B synthetic: Oct/Nov/Dec = 295 / 310 / 305; monthly growth 5; annual pattern reproduced.")
        print("C synthetic: 29 stockout days; estimated missing sales 290; order 67 -> 104.")
        d = report["cases"]["D"]["evidence"]
        print(f"D synthetic: +9000 one-off; orders {d['order_quantities']}; inflation {d['observed_relative_inflation']:.1%} (limit 1%).")
        print("D real candidate: SE 030200192_, 2025-05-06, raw 90000, median 50, threshold 1220, regular 0; not a verified customer anomaly.")
        print("E synthetic UI: two suppliers, manual adjustment 105 -> 110, review-gated CSV/JSON, edit invalidates review; nothing sent.")
        print("Real-sales demo / assumed inventory: IEK 010300002_ order 404 -> stock 200: 272 -> incoming 100 on Oct 2: 172; SE 010400432_: 9.")
    else:
        print(report["real_examples"].get("error", "See report for the failed check."))
    print(f"Evidence: {output / 'case_acceptance.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
