from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from vector_pipeline.config import sha256
from vector_pipeline.demand_cli import write_jsonl
from vector_pipeline.pipeline import dump
from vector_pipeline.replenishment import recommend
from vector_pipeline.replenishment_cli import run
from vector_pipeline.stockout import correct_history, refit_adjusted


CONFIG = json.loads((Path(__file__).parents[1]/"config/replenishment.json").read_text())
FORECAST_CONFIG = json.loads((Path(__file__).parents[1]/"config/forecast.json").read_text())
SERIES = {"series_id": "S", "supplier_id": "IEK", "sku_id": "SKU", "warehouse_id": "W", "stock_uom": "pieces", "assumption_ids": []}


def spec(value=None, **extra):
    return {"value": value, "status": "confirmed", "evidence": "Synthetic acceptance fixture", **extra}


def inputs(stock=15, lead=3, review=7, safety=2):
    return {"lead_time_days": spec(lead), "review_period_days": spec(review), "safety_days": spec(safety),
            "available_stock": spec(stock, snapshot_date="2026-10-01"), "incoming": spec(shipments=[]), "constraints": []}


def forecasts(qty=310):
    return [{**SERIES, "target_month": m, "forecast_qty": qty, "model": "mean", "as_of_date": "2026-09-22",
             "training_end_month": "2026-08", "components": {}, "quality_flags": ["stockout_not_corrected"]}
            for m in ("2026-10", "2026-11", "2026-12")]


def shipment(qty, day, identifier="incoming"):
    return {**spec(), "shipment_id": identifier, "qty_stock_units": qty, "arrival_date": day,
            "stock_uom": "pieces", "warehouse_id": "W"}


def calculate(supplied=None, predicted=None, policies=None):
    return recommend(SERIES, predicted if predicted is not None else forecasts(), supplied if supplied is not None else inputs(),
                     "2026-10-01", policies or CONFIG["policies"])


class ReplenishmentTests(unittest.TestCase):
    def test_stock_incoming_and_demand_monotonicity(self):
        orders = [calculate(inputs(stock=s))["recommended_quantity"] for s in (0, 10, 20, 100, 200)]
        self.assertEqual(orders, sorted(orders, reverse=True))
        orders = []
        for qty in (0, 10, 20, 100, 200):
            data = inputs()
            data["incoming"]["shipments"] = [shipment(qty, "2026-10-02")]
            orders.append(calculate(data)["recommended_quantity"])
        self.assertEqual(orders, sorted(orders, reverse=True))
        orders = [calculate(predicted=forecasts(q))["recommended_quantity"] for q in (0, 100, 310, 620)]
        self.assertEqual(orders, sorted(orders))

    def test_late_receipt_cannot_hide_earlier_deficit(self):
        data = inputs(stock=50, lead=1, review=9, safety=0)
        data["incoming"]["shipments"] = [shipment(100, "2026-10-10")]
        result = calculate(data)
        self.assertEqual(result["calculation"]["net_requirement"], -50)
        self.assertEqual(result["recommended_quantity"], 40)
        self.assertEqual(result["calculation"]["first_shortage_without_order"], "2026-10-06")
        self.assertIsNone(result["calculation"]["first_shortage_with_order"])

    def test_receipt_outside_horizon_not_used_or_double_counted(self):
        data = inputs()
        data["incoming"]["shipments"] = [shipment(1000, "2026-10-11"), shipment(1000, "2026-09-30", "past")]
        result = calculate(data)
        self.assertEqual(result["calculation"]["incoming_supply_used"], 0)
        self.assertEqual(result["calculation"]["incoming_supply_ignored_timing"], 2000)
        self.assertEqual(result["recommended_quantity"], calculate()["recommended_quantity"])

    def test_zero_need_no_forced_moq(self):
        data = inputs(stock=1000)
        data["constraints"] = [{**spec(100), "kind": "minimum", "order_uom": "pieces", "order_to_stock_conversion": 1}]
        self.assertEqual(calculate(data)["recommended_quantity"], 0)

    def test_minimum_multiple_and_conversion(self):
        data = inputs(stock=50, safety=2)
        data["constraints"] = [{**spec(10), "kind": "minimum", "order_uom": "box", "order_to_stock_conversion": 10},
                               {**spec(3), "kind": "multiple", "order_uom": "box", "order_to_stock_conversion": 10}]
        result = calculate(data)
        self.assertEqual(result["calculation"]["net_requirement"], 70)
        self.assertEqual(result["recommended_quantity"], 120)

    def test_unknown_moq_semantics_or_conversion_not_applied(self):
        data = inputs(stock=50)
        for rule in ({**spec(10000), "kind": "unknown"}, {**spec(10000), "kind": "multiple", "order_uom": None}):
            data["constraints"] = [rule]
            result = calculate(data)
            self.assertEqual(result["recommended_quantity"], 70)
            self.assertFalse(result["constraint_audit"][0]["applied"])

    def test_stock_increment_and_order_multiple_both_satisfied(self):
        data = inputs(stock=49)
        data["constraints"] = [{**spec(1.5), "kind": "multiple", "order_uom": "pack", "order_to_stock_conversion": 1}]
        data["stock_quantity_increment"] = spec(1)
        result = calculate(data)
        self.assertEqual(result["calculation"]["effective_rounding_step"], 3)
        self.assertEqual(result["recommended_quantity"], 72)

    def test_intermediate_fields_reproduce_quantity(self):
        result = calculate()
        c = result["calculation"]
        self.assertAlmostEqual(c["net_requirement"], c["forecast_demand"]+c["safety_stock"]-c["available_stock"]-c["incoming_supply_used"])
        self.assertAlmostEqual(result["recommended_quantity"], max(0, c["net_requirement"], c["timing_requirement"])+c["rounding_increment"])
        self.assertAlmostEqual(sum(d["forecast_demand"] for d in result["timeline"]), c["forecast_demand"])

    def test_unpreventable_lead_time_shortage_is_explicit(self):
        result = calculate(inputs(stock=0))
        self.assertEqual(result["urgency"], "expedite_or_transfer")
        self.assertEqual(result["calculation"]["first_shortage_with_order"], "2026-10-01")
        self.assertEqual(result["calculation"]["peak_shortage_before_new_order"], 30)

    def test_missing_inputs_forecasts_and_stale_stock_are_unavailable(self):
        self.assertIsNone(calculate({})["recommended_quantity"])
        self.assertIn("forecast_unavailable", calculate(predicted=[])["unavailable_reasons"])
        data = inputs()
        data["available_stock"]["snapshot_date"] = "2024-01-01"
        self.assertIn("available_stock_snapshot_date_mismatch", calculate(data)["unavailable_reasons"])
        self.assertIn("missing_forecast_months:2027-01", calculate(inputs(lead=80, review=30))["unavailable_reasons"])

    def test_arrival_day_receipt_precedes_demand_and_dimensions_checked(self):
        data = inputs(stock=0, lead=0, review=1, safety=0)
        data["incoming"]["shipments"] = [shipment(10, "2026-10-01")]
        self.assertEqual(calculate(data)["recommended_quantity"], 0)
        data["incoming"]["shipments"][0]["stock_uom"] = "meters"
        self.assertEqual(calculate(data)["recommendation_status"], "unavailable")

    def test_confirmed_and_scenario_status_and_evidence_validation(self):
        policies = {"daily_allocation": spec(), "unmet_demand_policy": spec(), "numeric_tolerance": 1e-9}
        self.assertEqual(calculate(policies=policies)["recommendation_status"], "confirmed")
        self.assertEqual(calculate()["recommendation_status"], "scenario")
        data = inputs()
        data["lead_time_days"] = {"value": 3, "status": "scenario"}
        with self.assertRaises(ValueError):
            calculate(data)

    def test_duplicate_shipments_and_invalid_numbers_rejected(self):
        data = inputs()
        data["incoming"]["shipments"] = [shipment(10, "2026-10-01")]*2
        with self.assertRaises(ValueError):
            calculate(data)
        with self.assertRaises(ValueError):
            calculate(inputs(stock=float("nan")))


class StockoutTests(unittest.TestCase):
    def fixture(self):
        months = [{"month": m, "qty_target": q} for m, q in [("2026-03", 310), ("2026-04", 300), ("2026-05", 310),
                                                          ("2026-06", 300), ("2026-07", 20), ("2026-08", 310)]]
        interval = {**spec(), "interval_id": "stockout", "start_date": "2026-07-01", "end_date_exclusive": "2026-07-30", "known_at": "2026-07-31"}
        return months, interval

    def test_correction_then_frozen_forecast_increases_order(self):
        months, interval = self.fixture()
        corrected, audit = correct_history(months, {}, [interval], "2026-09-22", CONFIG["stockout"])
        self.assertEqual(audit["estimated_lost_sales"], 290)
        raw = forecasts(210)
        adjusted = refit_adjusted(raw, corrected, FORECAST_CONFIG, audit)
        self.assertGreater(adjusted[0]["forecast_qty"], raw[0]["forecast_qty"])
        self.assertGreater(calculate(predicted=adjusted)["recommended_quantity"], calculate(predicted=raw)["recommended_quantity"])
        self.assertEqual(months[4]["qty_target"], 20)
        self.assertEqual(adjusted[0]["model"], "mean")
        self.assertNotIn("stockout_not_corrected", adjusted[0]["quality_flags"])

    def test_overlap_not_counted_twice_and_observed_sales_subtracted(self):
        months, interval = self.fixture()
        a = correct_history(months, {}, [interval], "2026-09-22", CONFIG["stockout"])[1]
        b = correct_history(months, {}, [interval, {**interval, "interval_id": "overlap"}], "2026-09-22", CONFIG["stockout"])[1]
        self.assertEqual(a["estimated_lost_sales"], b["estimated_lost_sales"])
        c = correct_history(months, {"2026-07-01": 8}, [interval], "2026-09-22", CONFIG["stockout"])[1]
        self.assertEqual(a["estimated_lost_sales"]-c["estimated_lost_sales"], 8)

    def test_no_intervals_or_future_knowledge_no_fabricated_loss(self):
        months, interval = self.fixture()
        corrected, audit = correct_history(months, {}, [], "2026-09-22", CONFIG["stockout"])
        self.assertEqual(corrected, months)
        self.assertEqual(audit["status"], "unavailable_no_intervals")
        interval["known_at"] = "2027-01-01"
        self.assertEqual(correct_history(months, {}, [interval], "2026-09-22", CONFIG["stockout"])[1]["estimated_lost_sales"], 0)

    def test_unknown_month_or_insufficient_prior_history_not_imputed(self):
        months, interval = self.fixture()
        months[4]["qty_target"] = None
        corrected, audit = correct_history(months, {}, [interval], "2026-09-22", CONFIG["stockout"])
        self.assertIsNone(corrected[4]["qty_target"])
        self.assertEqual(audit["status"], "unavailable_correction")


class OrderRunTests(unittest.TestCase):
    def test_demo_and_unavailable_runs_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forecast, canonical = root/"forecast", root/"canonical"
            forecast.mkdir(); canonical.mkdir()
            tables = {}
            for table, records in {"coverage": [SERIES], "forecasts": forecasts(), "monthly_series": []}.items():
                tables[table+".jsonl"] = write_jsonl(forecast/(table+".jsonl"), records)
            dump(forecast/"forecast_summary.json", {"status": "complete", "tables": tables})
            tables = {name+".jsonl": write_jsonl(canonical/(name+".jsonl"), []) for name in ("shipments", "order_constraints")}
            dump(canonical/"ingestion_summary.json", {"status": "complete", "tables": tables})
            dump(root/"config.json", CONFIG)
            first, a = run(forecast, canonical, "config.json", demo=True, root=root)
            second, b = run(forecast, canonical, "config.json", demo=True, root=root)
            self.assertEqual(a, b)
            self.assertEqual(sha256(first/"examples.md"), sha256(second/"examples.md"))
            self.assertEqual(a["recommendation_status_counts"], {"scenario": 1})
            _, strict = run(forecast, canonical, "config.json", root=root)
            self.assertEqual(strict["recommendation_status_counts"], {"unavailable": 1})


if __name__ == "__main__":
    unittest.main()
