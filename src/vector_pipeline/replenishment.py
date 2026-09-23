"""Deterministic stock-unit recommendations with dated supply and explicit evidence."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import math


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Expected a finite nonnegative quantity")
    return Decimal(str(value))


def evidence(item, assumptions):
    status = item.get("status", "unresolved")
    if status == "confirmed":
        if not item.get("evidence"):
            raise ValueError("Confirmed inputs require evidence")
    elif status in {"scenario", "estimated"}:
        if not item.get("id") or not item.get("reason"):
            raise ValueError("Non-confirmed inputs require an ID and reason")
        assumptions.add(item["id"])
    elif status != "unresolved":
        raise ValueError("Unknown evidence status")
    return status != "unresolved"


def recommend(series, forecasts, inputs, planning_date, policies):
    """Dates are [planning, planning + lead + review); arrivals precede daily demand."""
    assumptions = set(series.get("assumption_ids", []))
    for f in forecasts:
        assumptions.update(f.get("assumption_ids", []))
    result = {k: series.get(k) for k in ("series_id", "supplier_id", "sku_id", "warehouse_id", "stock_uom")}
    result.update(planning_date=planning_date, recommendation_status="unavailable", recommended_quantity=None,
                  urgency="unavailable", unavailable_reasons=[], assumption_ids=[], forecast_model=forecasts[0]["model"] if forecasts else None,
                  stockout_adjustment_status=inputs.get("stockout_adjustment_status", "unavailable_no_intervals"),
                  inputs=inputs, calculation=None, timeline=[], constraint_audit=[], incoming_audit=[],
                  requires_manager_approval=True, explanation="")
    start = date.fromisoformat(planning_date)
    ready = True
    for key in ("daily_allocation", "unmet_demand_policy"):
        ready = evidence(policies[key], assumptions) and ready
    for key in ("lead_time_days", "review_period_days", "safety_days", "available_stock", "incoming"):
        item = inputs.get(key, {"status": "unresolved"})
        if not evidence(item, assumptions):
            result["unavailable_reasons"].append(key)
    if not ready:
        result["unavailable_reasons"].append("calculation_policy_unresolved")
    if not forecasts:
        result["unavailable_reasons"].append("forecast_unavailable")
    if series.get("warehouse_id") is None or series.get("stock_uom") is None:
        result["unavailable_reasons"].append("series_dimensions_unresolved")
    def unavailable():
        result["assumption_ids"] = sorted(assumptions)
        result["explanation"] = "Recommendation unavailable: "+", ".join(result["unavailable_reasons"])
        return result
    if result["unavailable_reasons"]:
        return unavailable()
    if "shipments" not in inputs["incoming"] or not isinstance(inputs["incoming"]["shipments"], list):
        result["unavailable_reasons"].append("incoming_list_not_supplied")
        return unavailable()
    lead, review = inputs["lead_time_days"]["value"], inputs["review_period_days"]["value"]
    if type(lead) is not int or lead < 0 or type(review) is not int or review <= 0:
        raise ValueError("Lead time must be a nonnegative integer and review period a positive integer")
    days = lead+review
    end, order_arrival = start+timedelta(days=days), start+timedelta(days=lead)
    stock = number(inputs["available_stock"]["value"])
    if inputs["available_stock"].get("snapshot_date") != planning_date:
        result["unavailable_reasons"].append("available_stock_snapshot_date_mismatch")
        return unavailable()
    monthly = {}
    for row in forecasts:
        if any(row.get(k) != series.get(k) for k in ("series_id", "supplier_id", "sku_id", "warehouse_id", "stock_uom")):
            raise ValueError("Forecast dimensions do not match recommendation")
        if row["target_month"] in monthly:
            raise ValueError("Duplicate forecast month")
        if row["as_of_date"] > planning_date:
            result["unavailable_reasons"].append("forecast_not_available_at_planning_date")
        monthly[row["target_month"]] = number(row["forecast_qty"])
    dates = [start+timedelta(days=i) for i in range(days)]
    missing = sorted({d.strftime("%Y-%m") for d in dates}-monthly.keys())
    if missing:
        result["unavailable_reasons"].append("missing_forecast_months:"+",".join(missing))
    if result["unavailable_reasons"]:
        return unavailable()
    demand = [monthly[d.strftime("%Y-%m")]/Decimal(monthrange(d.year, d.month)[1]) for d in dates]
    total_demand = sum(demand, Decimal(0))
    safety = total_demand/Decimal(days)*number(inputs["safety_days"]["value"])
    arrivals, seen = {}, set()
    for shipment in inputs["incoming"].get("shipments", []):
        sid = shipment["shipment_id"]
        if sid in seen:
            raise ValueError("Duplicate shipment ID; review before summing")
        seen.add(sid)
        audit = {**shipment, "used_quantity": 0.0}
        if not evidence(shipment, assumptions):
            audit["reason"] = "unresolved_shipment"
            result["unavailable_reasons"].append("unresolved_incoming_shipment")
        else:
            qty = number(shipment["qty_stock_units"])
            arrival = date.fromisoformat(shipment["arrival_date"])
            if shipment.get("stock_uom") != series["stock_uom"] or shipment.get("warehouse_id") != series["warehouse_id"]:
                audit["reason"] = "dimension_mismatch"
                result["unavailable_reasons"].append("incoming_dimension_mismatch")
            elif arrival < start:
                audit["reason"] = "before_snapshot_not_counted_again"
            elif arrival >= end:
                audit["reason"] = "after_protection_window"
            else:
                arrivals[arrival] = arrivals.get(arrival, Decimal(0))+qty
                audit.update(reason="within_protection_window", used_quantity=float(qty))
        result["incoming_audit"].append(audit)
    if result["unavailable_reasons"]:
        return unavailable()
    incoming = sum(arrivals.values(), Decimal(0))
    target = total_demand+safety
    net = target-stock-incoming
    balance, worst_after_arrival, first_shortage, early_peak = stock, Decimal(0), None, Decimal(0)
    epsilon = number(policies["numeric_tolerance"])
    for day, daily in zip(dates, demand):
        delivery = arrivals.get(day, Decimal(0))
        balance += delivery-daily
        if balance < -epsilon and first_shortage is None:
            first_shortage = day
        if day >= order_arrival:
            worst_after_arrival = max(worst_after_arrival, -balance)
        else:
            early_peak = max(early_peak, -balance)
        result["timeline"].append({"date": day.isoformat(), "forecast_demand": float(daily),
            "existing_incoming": float(delivery), "balance_without_order": float(balance)})
    # A receipt near the end must not conceal a shortage the new order can prevent earlier.
    unrounded = max(Decimal(0), net, worst_after_arrival)
    if unrounded <= epsilon:
        unrounded = Decimal(0)
    minimum, multiple = Decimal(0), None
    for constraint in inputs.get("constraints", []):
        audit = dict(constraint)
        if constraint.get("kind") not in {"minimum", "multiple"} or not evidence(constraint, assumptions):
            audit["applied"] = False
            audit["reason"] = "semantics_or_evidence_unresolved"
        elif not constraint.get("order_to_stock_conversion") or not constraint.get("order_uom"):
            audit.update(applied=False, reason="order_unit_conversion_unresolved")
        else:
            value = number(constraint["value"])*number(constraint["order_to_stock_conversion"])
            if value <= 0:
                raise ValueError("Ordering constraints must be positive")
            if constraint["kind"] == "minimum":
                minimum = max(minimum, value)
            elif multiple is not None and multiple != value:
                raise ValueError("Conflicting order multiples")
            else:
                multiple = value
            audit.update(applied=True, value_stock_units=float(value))
        result["constraint_audit"].append(audit)
    quantum = None
    if inputs.get("stock_quantity_increment"):
        item = inputs["stock_quantity_increment"]
        if evidence(item, assumptions):
            quantum = number(item["value"])
            if quantum <= 0:
                raise ValueError("Stock quantity increment must be positive")
    steps = [v for v in (multiple, quantum) if v is not None]
    effective_step = None
    if steps:
        scale = Decimal(10)**max(0, max(-v.as_tuple().exponent for v in steps))
        effective_step = Decimal(math.lcm(*(int(v*scale) for v in steps)))/scale
    ordered = max(unrounded, minimum) if unrounded else Decimal(0)
    if ordered and effective_step is not None:
        ordered = ((ordered-epsilon)/effective_step).to_integral_value(rounding=ROUND_CEILING)*effective_step
    first_with_order = None
    for row in result["timeline"]:
        added = ordered if row["date"] >= order_arrival.isoformat() else Decimal(0)
        adjusted = Decimal(str(row["balance_without_order"]))+added
        row["balance_with_order"] = float(adjusted)
        if adjusted < -epsilon and first_with_order is None:
            first_with_order = row["date"]
    urgency = ("expedite_or_transfer" if first_shortage and first_shortage < order_arrival else
               "order_now" if ordered else "no_order_needed")
    result.update(recommendation_status="scenario" if assumptions else "confirmed", recommended_quantity=float(ordered),
        urgency=urgency, assumption_ids=sorted(assumptions),
        calculation={"lead_time_days": lead, "review_period_days": review, "protection_days": days,
            "protection_end_exclusive": end.isoformat(), "new_order_arrival_date": order_arrival.isoformat(),
            "forecast_demand": float(total_demand), "safety_stock": float(safety), "safety_days": inputs["safety_days"]["value"],
            "target_stock": float(target), "available_stock": float(stock), "incoming_supply_used": float(incoming),
            "incoming_supply_ignored_timing": sum(s["qty_stock_units"] for s in result["incoming_audit"] if s["reason"] in {"before_snapshot_not_counted_again", "after_protection_window"}),
            "net_requirement": float(net), "timing_requirement": float(worst_after_arrival), "unrounded_requirement": float(unrounded),
            "minimum_stock_units": float(minimum) if minimum else None, "multiple_stock_units": float(multiple) if multiple else None,
            "stock_quantity_increment": float(quantum) if quantum else None, "effective_rounding_step": float(effective_step) if effective_step else None,
            "rounding_increment": float(ordered-unrounded), "first_shortage_without_order": first_shortage.isoformat() if first_shortage else None,
            "first_shortage_with_order": first_with_order, "peak_shortage_before_new_order": float(early_peak)})
    result["explanation"] = (f"Demand {float(total_demand):.4f} + safety {float(safety):.4f} - available {float(stock):.4f} "
        f"- dated incoming {float(incoming):.4f} = net {float(net):.4f}. "
        f"Timing need {float(worst_after_arrival):.4f}; max(0, net, timing) = {float(unrounded):.4f}. "
        f"After explicit ordering constraints: {float(ordered):.4f} {series['stock_uom']}. "
        f"First shortage before the new order: {first_shortage.isoformat() if first_shortage and first_shortage < order_arrival else 'none'}. Manager review required.")
    return result
