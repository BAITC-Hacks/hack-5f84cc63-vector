"""Small monthly models and chronological selection; no inventory or lost-demand logic."""
from collections import Counter, defaultdict
from datetime import date
import math
from statistics import mean, median

from .demand import stable_id

MODELS = ("mean", "ewma", "trend_seasonal", "seasonal_naive")
DIMENSIONS = ("supplier_id", "sku_id", "warehouse_id", "stock_uom")


def shift_month(month, count):
    year, number = map(int, month.split("-"))
    year, index = divmod(year*12+number-1+count, 12)
    return f"{year:04d}-{index+1:02d}"


def month_distance(first, last):
    a, b = (list(map(int, m.split("-"))) for m in (first, last))
    return (b[0]-a[0])*12+b[1]-a[1]


def validate_config(cfg):
    if cfg.get("version") != 1 or cfg.get("coverage_policy") != "unknown_months_break_history":
        raise ValueError("Unsupported forecasting policy")
    date.fromisoformat(cfg["as_of_date"])
    for key in ("horizon_months", "min_history_months", "validation_origins", "validation_horizon", "examples_per_supplier"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if cfg["validation_horizon"] < cfg["horizon_months"]+1:
        raise ValueError("Validate the extra horizon caused by excluding the current partial month")
    if cfg["validation_origins"] < 3:
        raise ValueError("At least three rolling origins are required")
    for key in ("minimum_mae_improvement", "minimum_origin_win_share"):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]) or not 0 <= cfg[key] <= 1:
            raise ValueError(f"Invalid selection threshold: {key}")
    if not 0 < cfg["models"]["ewma"]["alpha"] <= 1:
        raise ValueError("EWMA alpha must be in (0, 1]")
    if cfg["models"].keys() != set(MODELS):
        raise ValueError("Explicit configuration for every model is required")
    for value in (cfg["models"]["mean"]["window"], cfg["models"]["trend_seasonal"]["trend_window"],
                  cfg["models"]["trend_seasonal"]["seasonal_min_months"], cfg["models"]["seasonal_naive"]["period"]):
        if type(value) is not int or value < 1:
            raise ValueError("Model windows must be positive integers")
    if cfg["models"]["trend_seasonal"]["trend_window"] < 2 or cfg["models"]["trend_seasonal"]["seasonal_min_months"] < 24:
        raise ValueError("Trend requires two months; learned annual seasonality requires two cycles")
    if cfg["models"]["seasonal_naive"]["period"] != 12:
        raise ValueError("This monthly contract uses annual seasonality")
    for value in (cfg["models"]["trend_seasonal"]["maximum_monthly_slope_fraction"], cfg["numeric_tolerance"]):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Slope bound and tolerance must be finite and nonnegative")
    if cfg["min_history_months"] < cfg["models"]["mean"]["window"] or not cfg["assumption_ids"]:
        raise ValueError("Minimum history must support baseline; assumption IDs are required")
    for coverage in cfg["source_coverage"].values():
        date.fromisoformat(coverage["start_month"]+"-01")
        if coverage["status"] not in {"assumption", "confirmed"} or not coverage.get("id") or not coverage.get("reason"):
            raise ValueError("Source coverage requires explicit evidence status, ID and reason")
        if coverage["status"] == "confirmed" and not coverage.get("evidence"):
            raise ValueError("Confirmed coverage requires evidence")


def build_series(rows, cfg):
    """Stream cleaned rows; a numeric monthly target requires every row to be resolved."""
    groups = {}
    as_of = cfg["as_of_date"]
    partial = as_of[:7]
    audit = Counter()
    for row in rows:
        audit["input_rows"] += 1
        occurred = row["occurred_at"]
        if occurred and occurred[:10] > as_of:
            audit["future_rows_ignored"] += 1
            continue
        key = tuple(row[k] for k in DIMENSIONS)
        item = groups.setdefault(key, {"months": {}, "source_ids": set(), "assumption_ids": set(),
                                      "undated_rows": 0, "synthetic": set()})
        coverage = cfg["source_coverage"][row["source_id"]]
        item["source_ids"].add(row["source_id"])
        item["assumption_ids"].update(row["assumption_ids"])
        item["assumption_ids"].update(cfg["assumption_ids"])
        if coverage["status"] == "assumption":
            item["assumption_ids"].add(coverage["id"])
        item["synthetic"].add(row["is_synthetic"])
        if occurred is None:
            item["undated_rows"] += 1
            continue
        month = occurred[:7]
        cell = item["months"].setdefault(month, {"transaction_rows": 0, "unresolved_rows": 0,
            "outlier_rows": 0, "qty_regular_known": 0.0, "qty_positive_raw": 0.0, "qty_signed_raw": 0.0})
        cell["transaction_rows"] += 1
        cell["unresolved_rows"] += row["qty_regular"] is None
        cell["outlier_rows"] += row["is_outlier"]
        cell["qty_regular_known"] += row["qty_regular"] or 0
        cell["qty_positive_raw"] += max(0, row["qty_raw"] or 0)
        cell["qty_signed_raw"] += row["qty_raw"] or 0
    series, monthly = [], []
    for key, item in sorted(groups.items(), key=lambda kv: str(kv[0])):
        source_ids = sorted(item["source_ids"])
        start = min(cfg["source_coverage"][sid]["start_month"] for sid in source_ids)
        metadata = dict(zip(DIMENSIONS, key))
        metadata.update(series_id=stable_id(key), source_ids=source_ids,
                        assumption_ids=sorted(item["assumption_ids"]), is_synthetic=True in item["synthetic"])
        entries = []
        # Earlier corrections stay in the audit, but cannot invent usable early history.
        months = set(item["months"])
        months.update(shift_month(start, i) for i in range(max(0, month_distance(start, partial)+1)))
        for month in sorted(months):
            cell = item["months"].get(month)
            reason = ("outside_source_coverage" if month < start else "partial_month" if month == partial else
                      "no_observations" if cell is None else "unresolved_quantity" if cell["unresolved_rows"] else "observed")
            entry = {**metadata, "month": month, "status": reason,
                     "qty_target": cell["qty_regular_known"] if reason == "observed" else None,
                     **(cell or {"transaction_rows": 0, "unresolved_rows": 0, "outlier_rows": 0,
                                 "qty_regular_known": None, "qty_positive_raw": None, "qty_signed_raw": None})}
            entries.append(entry)
            monthly.append(entry)
        series.append({**metadata, "months": entries, "undated_rows": item["undated_rows"],
                       "invalid_dimensions": None in key, "mixed_synthetic": len(item["synthetic"]) > 1})
    return series, monthly, dict(audit)


def trailing_history(cells, end_month):
    lookup = {c["month"]: c for c in cells}
    result, month = [], end_month
    while month in lookup and lookup[month]["qty_target"] is not None:
        result.append(lookup[month])
        month = shift_month(month, -1)
    return list(reversed(result))


def predict(model, values, horizon, cfg):
    """Return unrounded nonnegative quantities plus fitted components, or unavailable."""
    if len(values) < cfg["min_history_months"]:
        return None
    if model == "naive":
        return [values[-1]]*horizon, {"training_months": len(values), "level": values[-1], "seasonality_applied": False}
    model_cfg = cfg["models"][model]
    details = {"training_months": len(values), "seasonality_applied": False}
    if model == "mean":
        level = mean(values[-model_cfg["window"]:])
        estimates = [level]*horizon
        details["level"] = level
    elif model == "ewma":
        level = values[0]
        for value in values[1:]:
            level = model_cfg["alpha"]*value+(1-model_cfg["alpha"])*level
        estimates = [level]*horizon
        details.update(level=level, alpha=model_cfg["alpha"])
    elif model == "seasonal_naive":
        period = model_cfg["period"]
        if len(values) < period:
            return None
        estimates = [values[-period+(i % period)] for i in range(horizon)]
        details.update(seasonality_applied=True, reference="previous annual cycle", growth_applied=False)
    else:
        window = model_cfg["trend_window"]
        if len(values) < window:
            return None
        seasonal = len(values) >= model_cfg["seasonal_min_months"]
        if seasonal:
            # Annual differences cancel the seasonal component before estimating growth.
            slope = median((values[i]-values[i-12])/12 for i in range(12, len(values)))
        else:
            tail = values[-window:]
            slope = median((tail[j]-tail[i])/(j-i) for i in range(window) for j in range(i+1, window))
        bound = model_cfg["maximum_monthly_slope_fraction"]*mean(values[-window:])
        slope = min(bound, max(-bound, slope))
        if seasonal:
            intercepts = [mean(values[i]-slope*i for i in range(phase, len(values), 12)) for phase in range(12)]
            estimates = [intercepts[(len(values)+i) % 12]+slope*(len(values)+i) for i in range(horizon)]
            details["seasonal_intercepts"] = intercepts
        else:
            offset = len(values)-window
            intercept = median(values[i]-slope*i for i in range(offset, len(values)))
            estimates = [intercept+slope*(len(values)+i) for i in range(horizon)]
            details["intercept"] = intercept
        details.update(slope_per_month=slope, seasonality_applied=seasonal,
                       seasonality_status="learned_two_cycles" if seasonal else "insufficient_annual_history_trend_only")
    return [max(0.0, value) for value in estimates], details


def metrics(pairs):
    if not pairs:
        return {"n": 0, "mae": None, "wape": None, "bias": None}
    errors = [forecast-actual for forecast, actual in pairs]
    denominator = math.fsum(abs(actual) for _, actual in pairs)
    absolute = math.fsum(abs(error) for error in errors)
    return {"n": len(pairs), "mae": absolute/len(pairs), "wape": absolute/denominator if denominator else None,
            "bias": math.fsum(errors)/len(pairs)}


def select_model(history, cfg):
    """Only pre-holdout cells may be supplied. All candidates use identical origins/targets."""
    horizon, folds = cfg["validation_horizon"], cfg["validation_origins"]
    stop = len(history)-horizon
    origins = list(range(stop-folds+1, stop+1))
    if not origins or origins[0] < cfg["min_history_months"]:
        return "mean", "insufficient_validation_baseline", [], []
    scores, predictions = [], []
    errors = {}
    for model in MODELS:
        pairs, origin_errors, records = [], [], []
        for n in origins:
            fit = predict(model, [c["qty_target"] for c in history[:n]], horizon, cfg)
            if fit is None:
                break
            fold_pairs = []
            for h, forecast in enumerate(fit[0], 1):
                target = history[n+h-1]
                pair = (forecast, target["qty_target"])
                fold_pairs.append(pair)
                records.append({"model": model, "origin_month": history[n-1]["month"],
                    "train_start_month": history[0]["month"], "target_month": target["month"], "horizon": h,
                    "forecast": forecast, "actual": pair[1], "evaluation_role": "selection"})
            pairs.extend(fold_pairs)
            origin_errors.append(metrics(fold_pairs)["mae"])
        if len(origin_errors) != folds:
            scores.append({"model": model, "status": "unavailable_on_common_origins"})
            continue
        predictions.extend(records)
        errors[model] = origin_errors
        scores.append({"model": model, "status": "evaluated", **metrics(pairs), "origins": folds})
    baseline = next(s for s in scores if s["model"] == "mean")
    candidates = []
    tolerance = cfg["numeric_tolerance"]
    for score in scores:
        if score["status"] != "evaluated":
            continue
        wins = sum(a <= b+tolerance for a, b in zip(errors[score["model"]], errors["mean"])) / folds
        improvement = (baseline["mae"]-score["mae"])/baseline["mae"] if baseline["mae"] > tolerance else None
        eligible = score["model"] != "mean" and improvement is not None and improvement >= cfg["minimum_mae_improvement"] and wins >= cfg["minimum_origin_win_share"] and score["mae"] < baseline["mae"]-tolerance
        score.update(baseline_mae=baseline["mae"], relative_mae_improvement=improvement,
                     origin_win_share=wins, selection_eligible=eligible)
        if eligible:
            candidates.append(score)
    selected = min(candidates, key=lambda s: (s["mae"], MODELS.index(s["model"]))) if candidates else baseline
    return selected["model"], "validated_improvement" if candidates else "baseline_not_reliably_beaten", scores, predictions


def forecast_series(item, cfg):
    current = cfg["as_of_date"][:7]
    holdout = shift_month(current, -1)
    metadata = {k: item[k] for k in (*DIMENSIONS, "series_id", "source_ids", "assumption_ids", "is_synthetic")}
    pre = trailing_history(item["months"], shift_month(holdout, -1))
    history = trailing_history(item["months"], holdout)
    # Lock the model before inspecting holdout actuals or production-history eligibility.
    excluded_identity = item["invalid_dimensions"] or item["undated_rows"] or item["mixed_synthetic"]
    model, reason, scores, backtest = select_model([] if excluded_identity else pre, cfg)
    for row in backtest:
        row.update(metadata)
    for row in scores:
        row.update(metadata)
    # Last-value naive is an additional read-only benchmark, never selected on holdout.
    for row in list(backtest):
        if row["model"] == "mean":
            previous = next(c["qty_target"] for c in pre if c["month"] == row["origin_month"])
            backtest.append({**row, "model": "naive", "forecast": previous})
    lookup = {c["month"]: c for c in item["months"]}
    actual = lookup.get(holdout, {}).get("qty_target")
    if not item["invalid_dimensions"] and not item["undated_rows"] and not item["mixed_synthetic"]:
        for candidate in (*MODELS, "naive"):
            fit = predict(candidate, [c["qty_target"] for c in pre], 1, cfg)
            if fit is not None and actual is not None:
                backtest.append({**metadata, "model": candidate, "origin_month": shift_month(holdout, -1),
                    "train_start_month": pre[0]["month"], "target_month": holdout, "horizon": 1,
                    "forecast": fit[0][0], "actual": actual, "evaluation_role": "holdout",
                    "selected_before_holdout": candidate == model})
    recent = [lookup.get(shift_month(holdout, -i), {}) for i in range(cfg["min_history_months"])]
    quality_problem = item["invalid_dimensions"] or item["undated_rows"] or item["mixed_synthetic"] or any(c.get("status") == "unresolved_quantity" for c in recent)
    status = "excluded_data_quality" if quality_problem else "forecastable" if len(history) >= cfg["min_history_months"] else "insufficient_history"
    coverage = {**metadata, "status": status, "contiguous_history_months": len(history),
        "pre_holdout_history_months": len(pre), "selected_model": model, "selection_reason": reason,
        "selection_last_target_month": max((r["target_month"] for r in backtest if r["evaluation_role"] == "selection"), default=None),
        "holdout_month": holdout, "holdout_evaluated": any(r["evaluation_role"] == "holdout" and r["selected_before_holdout"] for r in backtest),
        "exclusion_reasons": (["missing_dimensions"] if item["invalid_dimensions"] else [])+
            (["undated_transactions"] if item["undated_rows"] else [])+(["mixed_synthetic_and_real"] if item["mixed_synthetic"] else [])+
            (["unresolved_recent_quantity"] if any(c.get("status") == "unresolved_quantity" for c in recent) else [])+
            (["not_enough_consecutive_observed_months"] if status == "insufficient_history" else [])}
    forecasts = []
    if status == "forecastable":
        fit = predict(model, [c["qty_target"] for c in history], cfg["horizon_months"]+1, cfg)
        baseline_fit = predict("mean", [c["qty_target"] for c in history], cfg["horizon_months"]+1, cfg)
        if fit is None:
            raise ValueError("Selected model unexpectedly unavailable for production refit")
        for i in range(1, cfg["horizon_months"]+1):
            forecasts.append({**metadata, "as_of_date": cfg["as_of_date"], "target_month": shift_month(current, i),
                "training_start_month": history[0]["month"], "training_end_month": holdout, "horizon_from_training_end": i+1,
                "model": model, "selection_reason": reason, "forecast_qty": fit[0][i],
                "baseline_forecast_qty": baseline_fit[0][i], "components": fit[1],
                "meaning": "regular_observed_sales", "quality_flags": ["stockout_not_corrected", "partial_current_month_excluded", "source_coverage_assumed"]})
    return coverage, forecasts, scores, backtest
