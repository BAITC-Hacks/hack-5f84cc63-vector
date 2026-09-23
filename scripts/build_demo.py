"""Build a small public workspace exclusively from generated histories.

No partner workbooks or private processed artifacts are read. Run from the repo root.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from accept_case import (CONFIGS, assumption, base_case, check, forecast, one_off_case,
                         order_inputs, sale, seasonal_case, stockout_case, workflow_case)
from vector_pipeline.dashboard import scenario
from vector_pipeline.demand import clean_transactions
from vector_pipeline.evidence import text_hash
from vector_pipeline.forecast import shift_month
from vector_pipeline.replenishment import recommend

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "demo"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def write_table(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in rows).encode()
    path.write_bytes(raw)
    return {"rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}


def generated_rows():
    orders, products, candidates = [], [], []
    kinds = ["Regular sales", "Seasonal growth", "Incoming supply", "One-off protection",
             "Covered stock", "Early shortage", "Regular sales", "Regular sales",
             "Regular sales", "Missing stock"]
    pattern = [0, 10, 20, 10, 0, 30, 50, 60, 10, 0, -10, -20]
    for supplier, prefix in (("IEK", "IEK"), ("SystemElectric", "SE")):
        for index in range(20):
            kind = kinds[index % len(kinds)]
            sku = f"SYN-{prefix}-{index + 1:03d}"
            unit = "м" if index == 8 else "шт"
            start = "2023-09" if kind == "Seasonal growth" else "2025-01"
            months = 36 if kind == "Seasonal growth" else 20
            transactions = [sale(f"{sku}-{i}", shift_month(start, i),
                100 + 5 * i + pattern[i % 12] if kind == "Seasonal growth" else 310 + index * 31,
                sku=sku) for i in range(months)]
            if kind == "One-off protection":
                transactions = [sale(f"{sku}-{m}-{d}", shift_month(start, m), 10, d, sku)
                                for m in range(months) for d in range(1, 21)]
                transactions.append(sale(f"{sku}-injected", "2026-08", 9000, 28, sku))
                transactions, docs = clean_transactions(transactions, CONFIGS["demand"])
                candidates.extend({**d, "supplier_id": supplier, "stock_uom": unit,
                                   "is_synthetic": True} for d in docs if d["is_outlier"])
            predicted = forecast(transactions, start)[0]
            series = {"series_id": sku, "supplier_id": supplier, "sku_id": sku,
                      "warehouse_id": "DEMO-WAREHOUSE", "stock_uom": unit}
            predicted = [{**f, **series, "is_synthetic": True} for f in predicted]
            inputs = order_inputs()
            if kind == "Incoming supply":
                inputs["incoming"]["shipments"] = [{**assumption(), "shipment_id": f"{sku}-RECEIPT",
                    "arrival_date": "2026-10-02", "qty_stock_units": 30,
                    "warehouse_id": series["warehouse_id"], "stock_uom": unit}]
            elif kind == "Covered stock":
                inputs["available_stock"]["value"] = 2000
            elif kind == "Early shortage":
                inputs["available_stock"]["value"] = 0
                inputs["lead_time_days"]["value"] = 10
            elif kind == "Missing stock":
                inputs["available_stock"] = {"value": None, "status": "unresolved"}
            if unit == "м":
                inputs["stock_quantity_increment"] = assumption(0.01)
            result = recommend(series, predicted, inputs, "2026-10-01", CONFIGS["replenishment"]["policies"])
            result.update(forecast_values_used=predicted, is_synthetic=True)
            orders.append(result)
            products.append({"supplier_id": supplier, "sku_id": sku, "supplier_article": sku,
                "name": f"Demo {prefix} {index + 1:02d} · {kind}", "stock_uom": unit,
                "category_id": None, "is_synthetic": True, "source_id": "GENERATED-PUBLIC-DEMO"})
    return orders, products, candidates


def main():
    orders, products, candidates = generated_rows()
    products_meta = write_table(DEST / "canonical/public/products.jsonl", products)
    ingestion_hash = write_json(DEST / "canonical/public/ingestion_summary.json", {
        "status": "complete", "is_synthetic": True, "tables": {"products.jsonl": products_meta}})
    recommendation_meta = write_table(DEST / "replenishment/public/recommendations.jsonl", orders)
    write_table(DEST / "demand/public/outlier_records.jsonl", candidates)
    write_json(DEST / "replenishment/public/replenishment_summary.json", {
        "status": "complete", "mode": "public_synthetic", "planning_date": "2026-10-01",
        "is_synthetic": True, "effective_config": {"policies": CONFIGS["replenishment"]["policies"]},
        "input_sha256": {"ingestion_summary": ingestion_hash},
        "tables": {"recommendations.jsonl": recommendation_meta}})

    # Only the existing small synthetic mechanisms and review flow; no full suite or real pipeline.
    checks = {"A": base_case(), "B": seasonal_case(), "C": stockout_case(),
              "D": one_off_case(), "E": workflow_case()}
    limits = {
        "A": ["Synthetic inputs only. Categories and external coefficient applicability remain unresolved."],
        "B": ["Generated 36-month history; not a claim about accuracy on partner data."],
        "C": ["Synthetic stockout intervals; real intervals were not supplied."],
        "D": ["Generated one-off document. Customer IDs are absent and customer grouping is not implemented."],
        "E": ["Session review only. No supplier transmission; exact 1C import format is unverified."],
    }
    first = orders[0]
    values = dict(stock=55, lead=3, review=7, safety=2, delay=0, reason="Synthetic public demonstration")
    changed = scenario(first, CONFIGS["replenishment"]["policies"], **values)
    incoming = scenario(first, CONFIGS["replenishment"]["policies"], **values,
                        receipt_qty=30, receipt_date="2026-10-02")
    check([first["recommended_quantity"], changed["recommended_quantity"], incoming["recommended_quantity"]] == [105, 65, 35],
          "Public walkthrough quantities changed")
    source_paths = [ROOT / "app.py", ROOT / "scripts/build_demo.py", ROOT / "scripts/accept_case.py", ROOT / "docs/CASE.md"]
    source_paths += list((ROOT / "src/vector_pipeline").glob("*.py"))
    source_paths += [ROOT / f"config/{name}.json" for name in ("demand", "forecast", "replenishment")]
    report = {"version": 1, "scope": "public_synthetic", "execution_status": "PASS",
        "input_files_unchanged": True, "not_a_business_approval": True, "registered_workbooks_verified": 0,
        "source_text_sha256": {p.relative_to(ROOT).as_posix(): text_hash(p) for p in source_paths},
        "artifact_sha256": {p.relative_to(DEST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for folder in ("canonical", "replenishment", "demand") for p in (DEST / folder).rglob("*.json*")},
        "cases": {key: {"status": "PARTIAL" if key in "AD" else "PASS", "evidence": evidence,
                        "limitations": limits[key]} for key, evidence in checks.items()},
        # Retain the UI report contract; scope and every displayed origin explicitly say synthetic.
        "real_examples": {"evidence_type": "public_synthetic", "replenishment_run": "public",
            "recommendation_sha256": recommendation_meta["sha256"], "real_outlier_candidate": candidates[0],
            "IEK": {"sku": first["sku_id"], "baseline_order": first["recommended_quantity"],
                "stock_value": 55, "incoming_value": 30, "stock_order": changed["recommended_quantity"],
                "incoming_order": incoming["recommended_quantity"], "calculation": deepcopy(first["calculation"])}}}
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    report_path = DEST / "acceptance" / hashlib.sha256(raw).hexdigest()[:12] / "case_acceptance.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(raw)
    # A Git checkout does not preserve report mtimes. Ship exactly one current report.
    for previous in (DEST / "acceptance").glob("*/case_acceptance.json"):
        if previous != report_path:
            previous.unlink()
    print(f"Public synthetic bundle: {len(orders)} SKU series, {sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file()):,} bytes")
    print("IEK SYN-IEK-001: 105 -> stock 55: 65 -> timely incoming 30: 35")
    print("No private files read. No partner records included.")


if __name__ == "__main__":
    main()
