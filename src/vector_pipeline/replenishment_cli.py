"""Publish explained order scenarios or unavailable results; never send supplier orders."""
import argparse
from calendar import monthrange
from collections import Counter, defaultdict
import json
from pathlib import Path
from uuid import uuid4

from .config import sha256
from .demand_cli import read_jsonl, write_jsonl
from .pipeline import dump
from .replenishment import recommend
from .stockout import correct_history, refit_adjusted


def assumed(value, config, suffix, **extra):
    return {"value": value, "status": "scenario", "id": config["demo"]["id"]+"-"+suffix,
            "reason": config["demo"]["reason"], **extra}


def demo_inputs(series, forecasts, shipments, constraints, config):
    supplier, sku = series["supplier_id"], series["sku_id"]
    values = config["demo"]["supplier_values"][supplier]
    inputs = {k: assumed(values[k], config, supplier+"-"+k) for k in ("lead_time_days", "review_period_days", "safety_days")}
    start = config["planning_date"]
    first = next((r for r in forecasts if r["target_month"] == start[:7]), None)
    stock = first["forecast_qty"]/monthrange(int(start[:4]), int(start[5:7]))[1]*values["available_stock_days"] if first else 0
    inputs["available_stock"] = assumed(stock, config, supplier+"-STOCK", snapshot_date=start,
        generation_rule=f"{values['available_stock_days']} days of the planning month's forecast; synthetic quantity, not measured inventory")
    quantum = config["demo"]["stock_quantity_increments"].get(series["stock_uom"])
    if quantum is not None:
        inputs["stock_quantity_increment"] = assumed(quantum, config, "STOCK-QUANTITY-INCREMENT",
            reason="Explicit demo stock-unit rounding; this is not an inferred supplier MOQ")
    deliveries, ignored = [], []
    for row in shipments:
        if row["qty"] is None or row["qty"] <= 0:
            continue
        if "exact_duplicate_row" in row["quality_flags"] or "repeated_sku_in_source" in row["quality_flags"] or not row["expected_date"]:
            ignored.append({"record_id": row["record_id"], "reason": "ambiguous_source_shipment"})
            continue
        deliveries.append({"shipment_id": row["record_id"], "source_shipment_id": row["shipment_id"],
            "qty_stock_units": row["qty"], "stock_uom": series["stock_uom"], "warehouse_id": series["warehouse_id"],
            "arrival_date": row["expected_date"], "source_date_kind": row["date_kind"],
            "source_id": row["source_id"], "source_row": row["source_row"],
            **{k: v for k, v in assumed(None, config, "SHIPMENT-INTERPRETATION").items() if k != "value"}})
    inputs["incoming"] = assumed(None, config, supplier+"-INCOMING-COVERAGE", shipments=deliveries,
        excluded_source_rows=ignored, completeness_note="Unlisted or ambiguous supply assumed absent in this scenario; not a verified supplier balance")
    inputs["constraints"] = []
    unique_values = {str(c["constraint_value_raw"]) for c in constraints}
    for row in constraints:
        if supplier == "SystemElectric" and row["source_id"] == "se_constraints" and row["value_status"] == "valid" and len(unique_values) == 1:
            inputs["constraints"].append({**assumed(row["order_multiple"], config, "SE-ORDER-UNIT"),
                "kind": "multiple", "order_to_stock_conversion": 1, "order_uom": series["stock_uom"],
                "source_id": row["source_id"], "source_row": row["source_row"]})
            break
    if not inputs["constraints"]:
        inputs["constraints"] = [{"kind": "unknown", "status": "unresolved", "value": None,
            "reason": "No conflict-free rule with resolved semantics and order units", "source_record_ids": [r["record_id"] for r in constraints]}]
    return inputs


def representative(rows):
    chosen = []
    for supplier in sorted({r["supplier_id"] for r in rows}):
        available = [r for r in rows if r["supplier_id"] == supplier and r["recommended_quantity"] is not None]
        selected = []
        # Deliberately include timing, zero-need, rounded and ordinary examples, not only largest orders.
        predicates = [lambda r: r["recommended_quantity"] == 0,
                      lambda r: r["calculation"]["timing_requirement"] > max(0, r["calculation"]["net_requirement"]),
                      lambda r: r["calculation"]["incoming_supply_used"] > 0,
                      lambda r: (r["calculation"]["multiple_stock_units"] or 0) > 1,
                      lambda r: r["urgency"] == "expedite_or_transfer"]
        for predicate in predicates:
            match = next((r for r in available if predicate(r) and r not in selected), None)
            if match:
                selected.append(match)
        selected.extend(r for r in available if r not in selected)
        chosen.extend(selected[:5])
    return chosen[:10]


def run(forecast, canonical, config_path="config/replenishment.json", demo=False, inputs_path=None,
        intervals_path=None, demand=None, output_dir=None, root="."):
    root = Path(root).resolve()
    forecast, canonical = (root/forecast).resolve(), (root/canonical).resolve()
    config_path = (root/config_path).resolve()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg["version"] != 1 or (demo and inputs_path):
        raise ValueError("Unsupported configuration or mutually exclusive demo/manual inputs")
    if demo and (cfg["demo"]["shipment_interpretation"] != "deadline_as_arrival_stock_unit_same_warehouse" or
                 cfg["demo"]["se_constraint_interpretation"] != "standalone_multiple_in_stock_units_if_no_conflict"):
        raise ValueError("Unsupported demo source interpretation")
    paths = {"config": config_path, "forecast_summary": forecast/"forecast_summary.json", "ingestion_summary": canonical/"ingestion_summary.json"}
    fingerprints = {k: sha256(p) for k, p in paths.items()}
    fs = json.loads(paths["forecast_summary"].read_text(encoding="utf-8"))
    cs = json.loads(paths["ingestion_summary"].read_text(encoding="utf-8"))
    if any(s["status"] != "complete" for s in (fs, cs)) or (forecast/"FAILED.json").exists() or (canonical/"FAILED.json").exists():
        raise ValueError("Completed upstream runs are required")
    data = {}
    for directory, summary, table_names in ((forecast, fs, ("coverage", "forecasts", "monthly_series")),
                                           (canonical, cs, ("shipments", "order_constraints"))):
        for name in table_names:
            p = directory/(name+".jsonl")
            fingerprints[name] = sha256(p)
            paths[name] = p
            if fingerprints[name] != summary["tables"][p.name]["sha256"]:
                raise ValueError(f"Changed upstream artifact: {name}")
            data[name] = list(read_jsonl(p))
            if len(data[name]) != summary["tables"][p.name]["rows"]:
                raise ValueError(f"Upstream row count mismatch: {name}")
    def external(name, path):
        paths[name] = (root/path).resolve()
        fingerprints[name] = sha256(paths[name])
        return json.loads(paths[name].read_text(encoding="utf-8"))
    supplied = external("operational_inputs", inputs_path) if inputs_path else {"series": []}
    by_input = {r["series_id"]: r for r in supplied["series"]}
    if len(by_input) != len(supplied["series"]):
        raise ValueError("Duplicate operational input series")
    intervals = external("stockout_intervals", intervals_path)["intervals"] if intervals_path else []
    by_forecast, by_month, by_intervals, daily = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(dict)
    for name, target in (("forecasts", by_forecast), ("monthly_series", by_month)):
        for row in data[name]:
            target[row["series_id"]].append(row)
    universe = {r["series_id"] for r in data["coverage"]}
    if set(by_input)-universe:
        raise ValueError("Operational inputs reference unknown forecast series")
    for interval in intervals:
        if interval["series_id"] not in universe:
            raise ValueError("Stockout interval references unknown series")
        by_intervals[interval["series_id"]].append(interval)
    if intervals:
        if demand is None:
            raise ValueError("Explicit intervals require the matching demand run for daily observations")
        p = (root/demand/"cleaned_transactions.jsonl").resolve()
        paths["daily_transactions"] = p
        fingerprints["daily_transactions"] = sha256(p)
        if fingerprints["daily_transactions"] != fs["input_sha256"]["cleaned_transactions"]:
            raise ValueError("Daily transactions differ from the forecasting input")
        keys = {tuple(r.get(k) for k in ("supplier_id", "sku_id", "warehouse_id", "stock_uom")): r["series_id"] for r in data["coverage"]}
        for row in read_jsonl(p):
            key = tuple(row[k] for k in ("supplier_id", "sku_id", "warehouse_id", "stock_uom"))
            sid = keys.get(key)
            if sid not in by_intervals or not row["occurred_at"]:
                continue
            day = row["occurred_at"][:10]
            old = daily[sid].get(day, 0)
            daily[sid][day] = old+row["qty_regular"] if old is not None and row["qty_regular"] is not None else None
    sources = defaultdict(list)
    constraints = defaultdict(list)
    for r in data["shipments"]:
        sources[(r["supplier_id"], r["sku_id"])].append(r)
    for r in data["order_constraints"]:
        constraints[(r["supplier_id"], r["sku_id"])].append(r)
    code_paths = [Path(__file__), Path(__file__).with_name("replenishment.py"), Path(__file__).with_name("stockout.py"), Path(__file__).with_name("forecast.py")]
    code_hashes = {p.name: sha256(p) for p in code_paths}
    recommendations, stockout_audit = [], []
    for series in data["coverage"]:
        sid = series["series_id"]
        forecasts = by_forecast[sid]
        if sid in by_intervals:
            adjusted, audit = correct_history(by_month[sid], daily[sid], by_intervals[sid], fs["as_of_date"], cfg["stockout"])
            forecasts = refit_adjusted(forecasts, adjusted, fs["effective_config"], audit)
        else:
            audit = {"status": "unavailable_no_intervals", "estimated_lost_sales": None, "assumption_ids": [], "changed_months": []}
        stockout_audit.append({"series_id": sid, **audit})
        key = (series["supplier_id"], series["sku_id"])
        inputs = demo_inputs(series, forecasts, sources[key], constraints[key], cfg) if demo else dict(by_input.get(sid, {}))
        inputs["stockout_adjustment_status"] = audit["status"]
        result = recommend(series, forecasts, inputs, cfg["planning_date"], cfg["policies"])
        result["assumption_ids"] = sorted(set(result["assumption_ids"]) | set(audit["assumption_ids"]))
        if result["recommendation_status"] == "confirmed" and result["assumption_ids"]:
            result["recommendation_status"] = "scenario"
        result["forecast_values_used"] = forecasts
        result["is_synthetic_inventory_scenario"] = demo
        recommendations.append(result)
    directory = (root/output_dir).resolve() if output_dir else root/"data"/"processed"/"replenishment"/uuid4().hex[:12]
    if directory == root/"data"/"processed" or not directory.is_relative_to(root/"data"/"processed"):
        raise ValueError("Output must be a new child of data/processed")
    directory.mkdir(parents=True, exist_ok=False)
    try:
        tables = {"recommendations.jsonl": write_jsonl(directory/"recommendations.jsonl", recommendations),
                  "stockout_adjustments.jsonl": write_jsonl(directory/"stockout_adjustments.jsonl", stockout_audit)}
        examples = representative(recommendations)
        summary = {"status": "complete", "mode": "demo_scenario" if demo else "explicit_inputs_only", "planning_date": cfg["planning_date"],
            "input_sha256": fingerprints, "code_sha256": code_hashes, "effective_config": cfg, "tables": tables,
            "recommendation_status_counts": dict(Counter(r["recommendation_status"] for r in recommendations)),
            "urgency_counts": dict(Counter(r["urgency"] for r in recommendations)),
            "stockout_status_counts": dict(Counter(r["status"] for r in stockout_audit)),
            "examples": [{k: v for k, v in r.items() if k not in {"timeline", "forecast_values_used"}} for r in examples],
            "limitations": ["All default demo stocks and supplier times are explicit scenarios, not partner-confirmed data.",
                "Forecast v1 and its poor holdout segments are unchanged; stockout refits do not establish new validation results.",
                "No real stockout intervals were provided; no real lost-sales estimate is fabricated.",
                "Uniform daily allocation and carried unmet demand are explicit calculation assumptions.",
                "Ordering constraints may remain unresolved; quantities require manager review and unit confirmation.",
                "Source coverage does not include missing category/growth/master inputs; no claim that all case inputs are available."]}
        lines = ["# Replenishment v1 examples", "", f"Planning date: {cfg['planning_date']}. Mode: {summary['mode']}.",
                 "These are reviewable scenarios, not approved supplier orders.", ""]
        for r in examples:
            lines.extend([f"## {r['supplier_id']} / {r['sku_id']} / {r['stock_uom']}", "", r["explanation"], "",
                          f"Status: {r['recommendation_status']}; urgency: {r['urgency']}; stockout: {r['stockout_adjustment_status']}.", "",
                          "Assumptions: "+", ".join(r["assumption_ids"]), ""])
        (directory/"examples.md").write_text("\n".join(lines), encoding="utf-8")
        if any(sha256(p) != fingerprints[k] for k, p in paths.items()) or any(sha256(p) != code_hashes[p.name] for p in code_paths):
            raise RuntimeError("Input or code changed during calculation")
        dump(directory/"replenishment_summary.json", summary)
        return directory, summary
    except Exception as exc:
        dump(directory/"FAILED.json", {"status": "failed", "error": str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forecast")
    parser.add_argument("canonical")
    parser.add_argument("--config", default="config/replenishment.json")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--inputs")
    parser.add_argument("--stockouts")
    parser.add_argument("--demand")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    directory, _ = run(args.forecast, args.canonical, args.config, args.demo, args.inputs, args.stockouts, args.demand, args.output_dir)
    print(directory)


if __name__ == "__main__":
    main()
