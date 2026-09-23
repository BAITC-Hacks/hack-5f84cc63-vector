"""Run monthly forecasting with rolling selection and an untouched final-month holdout."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from uuid import uuid4

from .config import sha256
from .demand_cli import read_jsonl, write_jsonl
from .forecast import MODELS, build_series, forecast_series, metrics, shift_month, validate_config
from .pipeline import dump


def summarize(coverage, predictions, backtest, cfg):
    suppliers = {}
    for supplier in sorted({r["supplier_id"] for r in coverage}):
        subset = [r for r in coverage if r["supplier_id"] == supplier]
        statuses = Counter(r["status"] for r in subset)
        suppliers[supplier] = {"total_sku_series": len(subset),
            **{s: statuses[s] for s in ("forecastable", "insufficient_history", "excluded_data_quality")},
            "holdout_evaluated_series": sum(r["holdout_evaluated"] for r in subset),
            "selected_model_counts_forecastable": dict(sorted(Counter(r["selected_model"] for r in subset if r["status"] == "forecastable").items())),
            "selection_reason_counts_forecastable": dict(sorted(Counter(r["selection_reason"] for r in subset if r["status"] == "forecastable").items()))}
    # All MAE/bias and pooled WAPE are separated by supplier and unit of measure.
    baseline = {(r["series_id"], r["evaluation_role"], r["origin_month"], r["target_month"], r["horizon"]): r
                for r in backtest if r["model"] == "mean"}
    naive = {(r["series_id"], r["evaluation_role"], r["origin_month"], r["target_month"], r["horizon"]): r
             for r in backtest if r["model"] == "naive"}
    buckets = defaultdict(list)
    for row in backtest:
        buckets[(row["supplier_id"], row["stock_uom"], row["evaluation_role"], row["model"])].append(row)
        if row["evaluation_role"] == "holdout" and row["selected_before_holdout"]:
            buckets[(row["supplier_id"], row["stock_uom"], "holdout", "selected_policy")].append(row)
    comparisons = []
    for (supplier, unit, role, model), rows in sorted(buckets.items(), key=lambda item: str(item[0])):
        pairs = [(r["forecast"], r["actual"]) for r in rows]
        reference = [baseline[(r["series_id"], role, r["origin_month"], r["target_month"], r["horizon"])] for r in rows]
        own = metrics(pairs)
        base = metrics([(r["forecast"], r["actual"]) for r in reference])
        naive_reference = [naive[(r["series_id"], role, r["origin_month"], r["target_month"], r["horizon"])] for r in rows]
        naive_metrics = metrics([(r["forecast"], r["actual"]) for r in naive_reference])
        comparisons.append({"supplier_id": supplier, "stock_uom": unit, "evaluation_role": role, "model": model,
            "sku_series": len({r["series_id"] for r in rows}), "metrics": own, "baseline_same_pairs": base,
            "naive_same_pairs": naive_metrics,
            "relative_mae_improvement": (base["mae"]-own["mae"])/base["mae"] if base["mae"] else None,
            "pair_win_share": sum(abs(a["forecast"]-a["actual"]) <= abs(b["forecast"]-b["actual"])+cfg["numeric_tolerance"] for a, b in zip(rows, reference))/len(rows)})
    examples = {}
    choices = {r["series_id"]: r for r in coverage}
    for supplier in suppliers:
        holdout = [r for r in backtest if r["supplier_id"] == supplier and r["evaluation_role"] == "holdout" and r["selected_before_holdout"] and choices[r["series_id"]]["status"] == "forecastable"]
        holdout.sort(key=lambda r: (abs(r["forecast"]-r["actual"])/max(r["actual"], cfg["numeric_tolerance"] or 1e-12), r["series_id"]))
        count = cfg["examples_per_supplier"]
        examples[supplier] = {"smallest_relative_holdout_errors": holdout[:count], "largest_relative_holdout_errors": list(reversed(holdout[-count:]))}
    return suppliers, comparisons, examples


def run(demand, config_path="config/forecast.json", output_dir=None, root="."):
    root = Path(root).resolve()
    demand = (root/demand).resolve()
    config_path = (root/config_path).resolve()
    paths = {"config": config_path, "demand_summary": demand/"demand_summary.json", "cleaned_transactions": demand/"cleaned_transactions.jsonl"}
    fingerprints = {key: sha256(path) for key, path in paths.items()}
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(cfg)
    upstream = json.loads(paths["demand_summary"].read_text(encoding="utf-8"))
    if upstream["status"] != "complete" or (demand/"FAILED.json").exists():
        raise ValueError("A completed demand run is required")
    if fingerprints["cleaned_transactions"] != upstream["tables"]["cleaned_transactions.jsonl"]["sha256"]:
        raise ValueError("Cleaned transactions fingerprint changed")
    primary = {supplier: spec["source_id"] for supplier, spec in upstream["primary_sources"].items()}
    if set(primary.values()) != set(cfg["source_coverage"]):
        raise ValueError("Source coverage configuration must match selected transaction sources")
    seen = set()
    def checked_rows():
        for row in read_jsonl(paths["cleaned_transactions"]):
            if row["record_id"] in seen or primary.get(row["supplier_id"]) != row["source_id"]:
                raise ValueError("Duplicate transaction ID or unexpected source")
            seen.add(row["record_id"])
            qty = row["qty_regular"]
            if qty is not None and (type(qty) not in (int, float) or not math.isfinite(qty) or qty < 0):
                raise ValueError("Regular quantity must be finite nonnegative or unresolved")
            yield row
    code_paths = [Path(__file__), Path(__file__).with_name("forecast.py")]
    code_hashes = {p.name: sha256(p) for p in code_paths}
    series, monthly, audit = build_series(checked_rows(), cfg)
    if audit.get("input_rows", 0) != upstream["tables"]["cleaned_transactions.jsonl"]["rows"]:
        raise ValueError("Cleaned transaction row count mismatch")
    coverage, forecasts, scores, backtest = [], [], [], []
    for item in series:
        c, f, s, b = forecast_series(item, cfg)
        coverage.append(c)
        forecasts.extend(f)
        scores.extend(s)
        backtest.extend(b)
    directory = (root/output_dir).resolve() if output_dir else root/"data"/"processed"/"forecast"/uuid4().hex[:12]
    allowed = root/"data"/"processed"
    if directory == allowed or not directory.is_relative_to(allowed):
        raise ValueError("Forecast output must be a new child of data/processed")
    directory.mkdir(parents=True, exist_ok=False)
    try:
        tables = {}
        for name, records in (("monthly_series", monthly), ("coverage", coverage), ("forecasts", forecasts),
                              ("model_scores", scores), ("backtest", backtest)):
            tables[name+".jsonl"] = write_jsonl(directory/(name+".jsonl"), records)
        suppliers, comparisons, examples = summarize(coverage, forecasts, backtest, cfg)
        report = {"status": "complete", "version": 1, "as_of_date": cfg["as_of_date"],
            "holdout_month": shift_month(cfg["as_of_date"][:7], -1), "effective_config": cfg,
            "input_sha256": fingerprints, "code_sha256": code_hashes, "primary_sources": upstream["primary_sources"],
            "tables": tables, "input_audit": audit, "coverage": suppliers, "model_comparisons": comparisons, "examples": examples,
            "limitations": ["Targets and forecasts are cleaned observed sales, not latent or stockout-compensated demand.",
                "Coverage universe consists of transaction-observed SKU/warehouse/unit series, not all catalog SKUs.",
                "Export coverage is an explicit assumption; months without observations remain unknown and break history.",
                "Final previous calendar month is reserved; if a SKU target is unavailable, its holdout is unavailable, not moved earlier.",
                "Model selection uses paired rolling forecasts for horizons 1-4; independent holdout evaluates horizon 1 only.",
                "The current month is excluded even on its last day; future forecasts start next calendar month.",
                "Models refit on resolved full-month history including holdout only after selection and evaluation.",
                "Source seasonality coefficients are not used: SKU applicability and historical availability are unresolved.",
                "No evidence supports claiming accuracy on unknown months, stockouts, inventory costs, or customer-level demand.",
                "Validation improvement is a policy gate, not a statistical guarantee of future improvement."]}
        if any(sha256(path) != fingerprints[key] for key, path in paths.items()):
            raise RuntimeError("Forecast inputs changed during the run")
        if any(sha256(p) != code_hashes[p.name] for p in code_paths):
            raise RuntimeError("Forecast implementation changed during the run")
        dump(directory/"forecast_summary.json", report)
        return directory, report
    except Exception as exc:
        dump(directory/"FAILED.json", {"status": "failed", "error": str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demand", help="Completed demand-cleaning run directory")
    parser.add_argument("--config", default="config/forecast.json")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    directory, _ = run(args.demand, args.config, args.output_dir)
    print(directory)


if __name__ == "__main__":
    main()
