"""Verify forecasting accounting and replay predictions using only their stated history."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from vector_pipeline.config import sha256
from vector_pipeline.demand_cli import read_jsonl
from vector_pipeline.forecast import predict, select_model, shift_month, trailing_history
from vector_pipeline.pipeline import dump


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(directory, compare=None):
    directory = Path(directory)
    summary = json.loads((directory/"forecast_summary.json").read_text(encoding="utf-8"))
    require(summary["status"] == "complete" and not (directory/"FAILED.json").exists(), "Incomplete forecast run")
    cfg = summary["effective_config"]
    data = {}
    for filename, table in summary["tables"].items():
        require(sha256(directory/filename) == table["sha256"], f"Artifact fingerprint: {filename}")
        data[filename] = list(read_jsonl(directory/filename))
        require(len(data[filename]) == table["rows"], f"Row count: {filename}")
    cells = defaultdict(list)
    for cell in data["monthly_series.jsonl"]:
        require((cell["qty_target"] is not None) == (cell["status"] == "observed"), "Unknown month became a numeric target")
        if cell["qty_target"] is not None:
            require(cell["qty_target"] == cell["qty_regular_known"] and cell["unresolved_rows"] == 0, "Incomplete target")
        cells[cell["series_id"]].append(cell)
    coverage = {r["series_id"]: r for r in data["coverage.jsonl"]}
    require(len(coverage) == len(data["coverage.jsonl"]), "Duplicate coverage series")
    require(coverage.keys() == cells.keys(), "Coverage does not account for every series")
    current, holdout = cfg["as_of_date"][:7], summary["holdout_month"]
    for sid, item in coverage.items():
        if any(reason in item["exclusion_reasons"] for reason in ("missing_dimensions", "undated_transactions", "mixed_synthetic_and_real")):
            continue
        pre = trailing_history(cells[sid], shift_month(holdout, -1))
        selected, reason, _, _ = select_model(pre, cfg)
        require(selected == item["selected_model"] and reason == item["selection_reason"], "Selection depends on data outside pre-holdout history")
    replayed = 0
    for row in data["backtest.jsonl"]:
        sid = row["series_id"]
        require(row["origin_month"] < row["target_month"], "Origin is not before target")
        role = row["evaluation_role"]
        require(row["target_month"] < holdout if role == "selection" else row["target_month"] == holdout, "Holdout/selection overlap")
        history = trailing_history(cells[sid], row["origin_month"])
        require(history[0]["month"] == row["train_start_month"], "Training start mismatch")
        fit = predict(row["model"], [c["qty_target"] for c in history], row["horizon"], cfg)
        require(fit is not None and math.isclose(fit[0][-1], row["forecast"], rel_tol=1e-12, abs_tol=1e-9), "Prediction replay mismatch")
        target = next(c for c in cells[sid] if c["month"] == row["target_month"])
        require(target["qty_target"] == row["actual"], "Backtest target mismatch")
        if role == "holdout":
            require(row["selected_before_holdout"] == (coverage[sid]["selected_model"] == row["model"]), "Holdout changed selected model")
        replayed += 1
    counts = Counter()
    prediction_keys = set()
    for row in data["forecasts.jsonl"]:
        sid = row["series_id"]
        key = (sid, row["target_month"])
        require(key not in prediction_keys, "Duplicate forecast target")
        prediction_keys.add(key)
        require(coverage[sid]["status"] == "forecastable" and row["model"] == coverage[sid]["selected_model"], "Unexpected forecast series/model")
        require(row["target_month"] > current and row["training_end_month"] == holdout, "Partial month entered production training/target")
        history = trailing_history(cells[sid], holdout)
        fit = predict(row["model"], [c["qty_target"] for c in history], row["horizon_from_training_end"], cfg)
        base = predict("mean", [c["qty_target"] for c in history], row["horizon_from_training_end"], cfg)
        require(fit[0][-1] == row["forecast_qty"] and base[0][-1] == row["baseline_forecast_qty"], "Production forecast replay mismatch")
        require(row["forecast_qty"] >= 0 and math.isfinite(row["forecast_qty"]), "Invalid forecast quantity")
        counts[sid] += 1
    require(all(counts[sid] == (cfg["horizon_months"] if c["status"] == "forecastable" else 0) for sid, c in coverage.items()), "Forecast coverage/count mismatch")
    for supplier, reported in summary["coverage"].items():
        actual = Counter(r["status"] for r in coverage.values() if r["supplier_id"] == supplier)
        require(sum(actual.values()) == reported["total_sku_series"], "Supplier universe mismatch")
        require(all(actual[k] == reported[k] for k in ("forecastable", "insufficient_history", "excluded_data_quality")), "Supplier coverage mismatch")
    if compare:
        other = Path(compare)
        require(sha256(directory/"forecast_summary.json") == sha256(other/"forecast_summary.json"), "Rerun summary differs")
        require(all(sha256(directory/f) == sha256(other/f) for f in summary["tables"]), "Rerun artifact differs")
    report = {"status": "passed", "sku_series_checked": len(coverage), "historical_predictions_replayed": replayed,
              "production_forecasts_replayed": sum(counts.values()), "byte_equivalent_rerun": bool(compare)}
    dump(directory/"verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forecast")
    parser.add_argument("--compare")
    args = parser.parse_args()
    print(json.dumps(verify(args.forecast, args.compare), indent=2))
