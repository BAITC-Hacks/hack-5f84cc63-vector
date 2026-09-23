"""Explicit stockout intervals -> estimated lost sales -> frozen-model production refit."""
from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
from statistics import mean

from .forecast import month_distance, predict, shift_month, trailing_history
from .replenishment import evidence, number


def correct_history(months, daily_sales, intervals, as_of_date, config):
    """Daily sales use cleaned observed quantities; absent days rely on declared export coverage."""
    if type(config["baseline_months"]) is not int or config["baseline_months"] < 1 or not config["assumption_id"]:
        raise ValueError("A positive baseline window and estimation assumption ID are required")
    corrected = deepcopy(months)
    lookup = {c["month"]: c for c in corrected}
    original = {c["month"]: c for c in months}
    audits, days, assumptions = [], {}, set()
    for interval in intervals:
        if not evidence(interval, assumptions):
            audits.append({"interval_id": interval["interval_id"], "status": "unavailable_unresolved_interval"})
            continue
        start, end = date.fromisoformat(interval["start_date"]), date.fromisoformat(interval["end_date_exclusive"])
        known = date.fromisoformat(interval["known_at"])
        if end <= start:
            raise ValueError("Stockout intervals must have positive duration")
        if known.isoformat() > as_of_date:
            audits.append({"interval_id": interval["interval_id"], "status": "not_known_at_cutoff"})
            continue
        for offset in range((end-start).days):
            day = start+timedelta(days=offset)
            if day.isoformat() <= as_of_date:
                days.setdefault(day, []).append(interval["interval_id"])
    for day, ids in sorted(days.items()):
        month = day.strftime("%Y-%m")
        prior = trailing_history(months, shift_month(month, -1))[-config["baseline_months"]:]
        target = original.get(month)
        if len(prior) < config["baseline_months"] or target is None or target["qty_target"] is None or daily_sales.get(day.isoformat(), 0) is None:
            audits.append({"date": day.isoformat(), "interval_ids": ids, "status": "unavailable_history_or_target"})
            continue
        rate = mean(c["qty_target"]/monthrange(int(c["month"][:4]), int(c["month"][5:]))[1] for c in prior)
        observed = float(number(daily_sales.get(day.isoformat(), 0)))
        lost = max(0.0, rate-observed)
        lookup[month]["qty_target"] += lost
        audits.append({"date": day.isoformat(), "interval_ids": ids, "status": "estimated",
                       "prior_months": [c["month"] for c in prior], "expected_daily_sales": rate,
                       "observed_daily_sales": observed, "estimated_lost_sales": lost})
    applied = [a for a in audits if a["status"] == "estimated"]
    if applied:
        assumptions.add(config["assumption_id"])
    return corrected, {"status": "adjusted" if applied else "unavailable_no_intervals" if not intervals else "unavailable_correction",
        "estimated_lost_sales": sum(a["estimated_lost_sales"] for a in applied),
        "assumption_ids": sorted(assumptions), "daily_audit": audits,
        "changed_months": [{"month": c["month"], "original_quantity": original[c["month"]]["qty_target"], "adjusted_quantity": c["qty_target"]}
                           for c in corrected if c["qty_target"] != original[c["month"]]["qty_target"]]}


def refit_adjusted(forecasts, corrected_months, forecast_config, adjustment):
    if adjustment["status"] != "adjusted" or not forecasts:
        return forecasts
    result = deepcopy(forecasts)
    history = trailing_history(corrected_months, forecasts[0]["training_end_month"])
    last = max(r["target_month"] for r in forecasts)
    horizon = month_distance(forecasts[0]["training_end_month"], last)
    fitted = predict(forecasts[0]["model"], [c["qty_target"] for c in history], horizon, forecast_config)
    if fitted is None:
        raise ValueError("Insufficient history for frozen-model stockout refit")
    for row in result:
        row["unadjusted_forecast_qty"] = row["forecast_qty"]
        row["forecast_qty"] = fitted[0][month_distance(row["training_end_month"], row["target_month"])-1]
        row["components"] = fitted[1]
        row["assumption_ids"] = sorted(set(row["assumption_ids"]) | set(adjustment["assumption_ids"]))
        row["meaning"] = "estimated_stockout_adjusted_sales"
        row["quality_flags"] = [f for f in row.get("quality_flags", []) if f != "stockout_not_corrected"]+["stockout_adjustment_estimated"]
    return result
