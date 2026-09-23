from copy import deepcopy
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vector_pipeline.dashboard import (discover_runs, draft_line, export_draft, load_run,
    review_draft, review_is_current, scenario)
from vector_pipeline.replenishment import recommend


POLICIES = {k: {"status": "confirmed", "evidence": "Synthetic test policy"}
            for k in ("daily_allocation", "unmet_demand_policy")}
POLICIES["numeric_tolerance"] = 1e-9


def spec(value=None, **kw):
    return {"value": value, "status": "confirmed", "evidence": "Synthetic fixture", **kw}


def record():
    series = {"series_id": "S", "supplier_id": "IEK", "sku_id": "00123_", "warehouse_id": "W", "stock_uom": "pieces"}
    forecasts = [{**series, "target_month": "2026-10", "forecast_qty": 310,
                  "baseline_forecast_qty": 310, "model": "mean", "as_of_date": "2026-09-22"}]
    inputs = {"available_stock": spec(15, snapshot_date="2026-10-01"), "lead_time_days": spec(3),
              "review_period_days": spec(7), "safety_days": spec(2), "incoming": spec(shipments=[]),
              "constraints": [], "stock_quantity_increment": spec(1)}
    result = recommend(series, forecasts, inputs, "2026-10-01", POLICIES)
    result["forecast_values_used"] = forecasts
    return result


def change(row, **kw):
    values = dict(stock=15, lead=3, review=7, safety=2, delay=0, reason="Synthetic scenario")
    values.update(kw)
    return scenario(row, POLICIES, **values)


def write_run(root):
    path = Path(root) / "replenishment" / "fixture"
    path.mkdir(parents=True)
    raw = (json.dumps(record()) + "\n").encode()
    (path / "recommendations.jsonl").write_bytes(raw)
    summary = {"status": "complete", "mode": "explicit_inputs_only", "planning_date": "2026-10-01",
               "effective_config": {"policies": POLICIES}, "tables": {
                   "recommendations.jsonl": {"rows": 1, "sha256": hashlib.sha256(raw).hexdigest()}}}
    (path / "replenishment_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return path


class DashboardTests(unittest.TestCase):
    def test_scenario_changes_order_preserves_original_and_forecast(self):
        row = record()
        saved = deepcopy(row)
        changed = change(row, stock=55)
        self.assertEqual(changed["recommended_quantity"], 65)
        self.assertEqual(row, saved)
        self.assertEqual(changed["forecast_values_used"], row["forecast_values_used"])
        self.assertEqual(changed["inputs"]["available_stock"]["original_input"], row["inputs"]["available_stock"])
        self.assertEqual(changed["recommendation_status"], "scenario")

    def test_additional_supply_and_delays_preserve_past_receipts(self):
        row = record()
        for key, arrival in (("past", "2026-09-30"), ("future", "2026-10-03")):
            row["inputs"]["incoming"]["shipments"].append({**spec(), "shipment_id": key,
                "arrival_date": arrival, "qty_stock_units": 10, "warehouse_id": "W", "stock_uom": "pieces"})
        changed = change(row, delay=14, receipt_qty=30, receipt_date="2026-10-02")
        shipments = changed["inputs"]["incoming"]["shipments"]
        self.assertEqual([s["arrival_date"] for s in shipments], ["2026-09-30", "2026-10-17", "2026-10-02"])
        self.assertEqual(changed["calculation"]["incoming_supply_used"], 30)
        self.assertEqual(changed["recommended_quantity"], 75)

    def test_invalid_scenario_and_missing_horizon(self):
        for kwargs in ({"stock": -1}, {"reason": " "}, {"delay": -1}, {"receipt_qty": float("nan")},
                       {"receipt_qty": 1, "receipt_date": "2026-09-30"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                change(record(), **kwargs)
        result = change(record(), lead=90)
        self.assertIsNone(result["recommended_quantity"])
        with self.assertRaises(ValueError):
            draft_line(result, 10)

    def test_manual_override_requires_reason_and_respects_rounding(self):
        row = record()
        for qty, reason in ((110, ""), (105.5, "review"), (0, "skip"), (float("nan"), "review")):
            with self.subTest(qty=qty), self.assertRaises(ValueError):
                draft_line(row, qty, reason)
        self.assertEqual(draft_line(row, 110, "Manager adjustment")["order_quantity"], 110)
        row["calculation"]["minimum_stock_units"] = 100
        with self.assertRaises(ValueError):
            draft_line(row, 90, "Adjustment")

    def test_review_gate_revokes_for_any_quantity_or_input_change(self):
        cart = {"S": draft_line(record(), 105)}
        with self.assertRaises(ValueError):
            export_draft(cart, None, "fixture")
        for name, ack in (("", True), ("Test", False)):
            with self.assertRaises(ValueError):
                review_draft(cart, name, ack, "test-time")
        review = review_draft(cart, "Test reviewer", True, "test-time")
        self.assertTrue(review_is_current(cart, review))
        cart["S"]["recommendation"]["inputs"]["safety_days"]["value"] = 3
        self.assertFalse(review_is_current(cart, review))
        with self.assertRaises(ValueError):
            export_draft(cart, review, "fixture")

    def test_exports_keep_identifiers_status_and_safe_csv_text(self):
        row = record()
        row["explanation"] = '=HYPERLINK("bad")'
        cart = {"S": draft_line(row, 110, "+unsafe formula")}
        review = review_draft(cart, "@reviewer", True, "test-time")
        csv_bytes, audit = export_draft(cart, review, "fixture")
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        exported = next(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        self.assertEqual(exported["sku_id"], "00123_")
        self.assertTrue(exported["explanation"].startswith("'="))
        self.assertTrue(exported["reviewer"].startswith("'@"))
        self.assertTrue(exported["manager_reason"].startswith("'+"))
        payload = json.loads(audit)
        self.assertEqual(payload["status"], "reviewed_draft_not_sent")
        self.assertEqual(payload["lines"][0]["recommendation"]["sku_id"], "00123_")
        self.assertEqual(payload["lines"][0]["recommendation"]["recommended_quantity"], 105)

    def test_only_complete_intact_runs_are_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_run(root)
            self.assertEqual(len(discover_runs(Path(root) / "replenishment")), 1)
            self.assertEqual(len(load_run(path)[1]), 1)
            (path / "recommendations.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Integrity"):
                load_run(path)


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install the dashboard extra for UI tests")
class DashboardUITests(unittest.TestCase):
    def test_review_export_and_scenario_change_flow(self):
        from streamlit.testing.v1 import AppTest

        def widget(items, label):
            return next(x for x in items if x.label == label)

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": root}):
            write_run(root)
            app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
            self.assertFalse(app.exception)
            widget(app.button, "Add / update draft").click().run()
            self.assertEqual(len(app.session_state.cart), 1)
            app.radio[0].set_value("Draft review").run()
            self.assertFalse(app.get("download_button"))
            widget(app.text_input, "Reviewer name").set_value("Synthetic UI tester")
            app.checkbox[0].check()
            widget(app.button, "Mark draft reviewed").click().run()
            self.assertEqual(len(app.get("download_button")), 2)
            app.radio[0].set_value("Order planning").run()
            widget(app.number_input, "Available stock (pieces)").set_value(55)
            widget(app.text_input, "Scenario reason").set_value("Synthetic stock revision")
            widget(app.button, "Recalculate SKU").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.overrides["S"]["recommended_quantity"], 65)
            self.assertFalse(app.session_state.cart)
            self.assertIsNone(app.session_state.review)
            widget(app.button, "Reset this SKU").click().run()
            self.assertFalse(app.session_state.overrides)
            self.assertFalse(app.exception)
            app.radio[0].set_value("Data coverage").run()
            self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
