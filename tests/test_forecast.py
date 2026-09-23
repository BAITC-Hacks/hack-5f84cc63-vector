from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from vector_pipeline.config import sha256
from vector_pipeline.demand_cli import write_jsonl
from vector_pipeline.forecast import build_series, forecast_series, metrics, predict, select_model, shift_month, validate_config
from vector_pipeline.forecast_cli import run, summarize
from vector_pipeline.pipeline import dump
from scripts.verify_forecast import verify

CONFIG = json.loads((Path(__file__).parents[1]/"config/forecast.json").read_text())


def row(month, quantity=100, index=0, **changes):
    result = {"record_id": f"{month}-{index}", "supplier_id": "IEK", "sku_id": "001_", "warehouse_id": "W1",
        "stock_uom": "pieces", "source_id": "iek_transactions", "occurred_at": month+"-15T12:00:00",
        "qty_raw": quantity, "qty_regular": quantity, "is_outlier": False, "assumption_ids": [], "is_synthetic": True}
    result.update(changes)
    return result


def fixture(values, start="2023-01"):
    cfg = deepcopy(CONFIG)
    cfg["as_of_date"] = shift_month(start, len(values))+"-22"
    cfg["source_coverage"] = {"iek_transactions": {"start_month": start, "status": "assumption", "id": "TEST-COVERAGE", "reason": "Synthetic fixture"}}
    items, monthly, _ = build_series([row(shift_month(start, i), v) for i, v in enumerate(values)], cfg)
    return items[0], cfg, monthly


class ModelTests(unittest.TestCase):
    def test_constant_baseline_not_replaced_on_ties(self):
        item, cfg, _ = fixture([100]*25)
        c, f, scores, backtest = forecast_series(item, cfg)
        self.assertEqual(c["selected_model"], "mean")
        self.assertTrue(all(r["forecast_qty"] == 100 for r in f))
        self.assertTrue(all(r["target_month"] < c["holdout_month"] for r in backtest if r["evaluation_role"] == "selection"))
        self.assertTrue(scores)

    def test_sustained_growth_beats_flat_baseline(self):
        item, cfg, _ = fixture([100+10*i for i in range(30)])
        c, f, _, _ = forecast_series(item, cfg)
        self.assertEqual(c["selected_model"], "trend_seasonal")
        self.assertGreater(f[1]["forecast_qty"], f[0]["forecast_qty"])
        # Last full month is index 29; current partial month index 30 is skipped.
        self.assertAlmostEqual(f[0]["forecast_qty"], 100+10*31)

    def test_annual_pattern_is_reproduced(self):
        pattern = [100, 100, 100, 100, 300, 300, 600, 600, 100, 100, 50, 50]
        item, cfg, _ = fixture(pattern*3)
        c, f, _, _ = forecast_series(item, cfg)
        self.assertIn(c["selected_model"], {"seasonal_naive", "trend_seasonal"})
        self.assertTrue(all(r["components"]["seasonality_applied"] for r in f))
        self.assertEqual([r["forecast_qty"] for r in f], [100, 100, 100])
        fitted, detail = predict("trend_seasonal", pattern*3, 12, cfg)
        self.assertEqual(fitted, pattern)
        self.assertTrue(detail["seasonality_applied"])

    def test_seasonal_growth_not_applied_twice(self):
        pattern = [0, 10, 20, 10, 0, 30, 50, 60, 10, 0, -10, -20]
        values = [100+5*i+pattern[i % 12] for i in range(36)]
        _, cfg, _ = fixture(values)
        fitted, detail = predict("trend_seasonal", values, 12, cfg)
        self.assertEqual(detail["slope_per_month"], 5)
        for i, value in enumerate(fitted, 36):
            self.assertAlmostEqual(value, 100+5*i+pattern[i % 12])

    def test_short_annual_history_is_not_fabricated(self):
        result = predict("trend_seasonal", [100+i for i in range(20)], 3, CONFIG)
        self.assertFalse(result[1]["seasonality_applied"])
        self.assertIsNone(predict("seasonal_naive", [100]*11, 3, CONFIG))

    def test_holdout_values_never_change_selection_scores_or_predictions(self):
        item, cfg, _ = fixture([100+3*i for i in range(24)])
        before = forecast_series(item, cfg)
        other = deepcopy(item)
        holdout = before[0]["holdout_month"]
        for cell in other["months"]:
            if cell["month"] == holdout:
                cell["qty_target"] = 10000000
        after = forecast_series(other, cfg)
        self.assertEqual(before[0]["selected_model"], after[0]["selected_model"])
        self.assertEqual(before[2], after[2])
        selection = lambda result: [r for r in result[3] if r["evaluation_role"] == "selection"]
        self.assertEqual(selection(before), selection(after))
        prediction = lambda result: [(r["model"], r["forecast"]) for r in result[3] if r["evaluation_role"] == "holdout"]
        self.assertEqual(prediction(before), prediction(after))
        self.assertNotEqual(before[1], after[1])  # Production refit may use the now-observed holdout.

    def test_rolling_prediction_does_not_see_its_target(self):
        item, cfg, _ = fixture([100+i for i in range(26)])
        history = [c for c in item["months"] if c["qty_target"] is not None][:-1]
        _, _, _, before = select_model(history, cfg)
        other = deepcopy(history)
        other[-1]["qty_target"] = 1000000
        _, _, _, after = select_model(other, cfg)
        self.assertEqual([(r["model"], r["origin_month"], r["forecast"]) for r in before],
                         [(r["model"], r["origin_month"], r["forecast"]) for r in after])

    def test_selection_fallback_with_too_few_folds(self):
        item, cfg, _ = fixture([100, 120, 90, 110])
        c, f, scores, _ = forecast_series(item, cfg)
        self.assertEqual(c["selection_reason"], "insufficient_validation_baseline")
        self.assertEqual(c["status"], "forecastable")
        self.assertEqual(len(f), 3)
        self.assertEqual(scores, [])

    def test_zero_targets_and_nonnegative_forecasts(self):
        self.assertIsNone(metrics([(1, 0), (0, 0)])["wape"])
        self.assertEqual(metrics([(12, 10), (8, 10)])["bias"], 0)
        self.assertEqual(metrics([(12, 10), (12, 10)])["bias"], 2)
        for model in ("mean", "ewma", "trend_seasonal", "seasonal_naive"):
            fitted = predict(model, [100-i*4 for i in range(24)], 10, CONFIG)
            self.assertTrue(all(v >= 0 for v in fitted[0]))


class CoverageTests(unittest.TestCase):
    def test_missing_months_not_zero_and_negative_month_not_partial_sum(self):
        cfg = deepcopy(CONFIG)
        rows = [row("2026-05"), row("2026-07"), row("2026-08", -10, qty_regular=None), row("2026-08", 20, index=1)]
        items, monthly, _ = build_series(rows, cfg)
        lookup = {m["month"]: m for m in monthly}
        self.assertIsNone(lookup["2026-06"]["qty_target"])
        self.assertIsNone(lookup["2026-08"]["qty_target"])
        self.assertEqual(lookup["2026-08"]["qty_regular_known"], 20)
        self.assertEqual(forecast_series(items[0], cfg)[0]["status"], "excluded_data_quality")

    def test_partial_month_and_future_never_affect_training_or_universe(self):
        cfg = deepcopy(CONFIG)
        rows = [row(shift_month("2025-01", i)) for i in range(20)]
        first, _, _ = build_series(rows, cfg)
        second, months, audit = build_series(rows+[row("2026-09", 900000), row("2026-10", sku_id="FUTURE")], cfg)
        self.assertEqual(len(second), 1)
        self.assertEqual(audit["future_rows_ignored"], 1)
        self.assertIsNone(next(m["qty_target"] for m in months if m["month"] == "2026-09"))
        self.assertEqual(forecast_series(first[0], cfg), forecast_series(second[0], cfg))

    def test_sparse_missing_holdout_not_shifted_to_an_earlier_month(self):
        cfg = deepcopy(CONFIG)
        items, _, _ = build_series([row("2026-04"), row("2026-05"), row("2026-06"), row("2026-07")], cfg)
        c, f, _, b = forecast_series(items[0], cfg)
        self.assertEqual(c["holdout_month"], "2026-08")
        self.assertFalse(c["holdout_evaluated"])
        self.assertEqual(c["status"], "insufficient_history")
        self.assertEqual(f, [])
        self.assertFalse(any(r["evaluation_role"] == "holdout" for r in b))

    def test_units_warehouses_and_invalid_dimensions(self):
        cfg = deepcopy(CONFIG)
        rows = [row("2026-08"), row("2026-08", index=1, stock_uom="meters"),
                row("2026-08", index=2, warehouse_id="W2"), row("2026-08", index=3, stock_uom=None)]
        items, _, _ = build_series(rows, cfg)
        self.assertEqual(len(items), 4)
        outputs = [forecast_series(item, cfg) for item in items]
        self.assertEqual(sum(o[0]["status"] == "excluded_data_quality" for o in outputs), 1)

    def test_metrics_compared_on_paired_series_and_units(self):
        item, cfg, _ = fixture([100+i*5 for i in range(25)])
        c, f, _, b = forecast_series(item, cfg)
        suppliers, comparisons, _ = summarize([c], f, b, cfg)
        self.assertEqual(suppliers["IEK"]["forecastable"], 1)
        for comparison in comparisons:
            self.assertEqual(comparison["metrics"]["n"], comparison["baseline_same_pairs"]["n"])
            self.assertEqual(comparison["stock_uom"], "pieces")


class ForecastRunTests(unittest.TestCase):
    def make_input(self, root):
        _, cfg, _ = fixture([100+5*i for i in range(24)], start="2024-01")
        directory = root/"data"/"processed"/"demand"/"test"
        directory.mkdir(parents=True)
        table = write_jsonl(directory/"cleaned_transactions.jsonl", [row(shift_month("2024-01", i), 100+5*i) for i in range(24)])
        dump(root/"forecast.json", cfg)
        dump(directory/"demand_summary.json", {"status": "complete", "tables": {"cleaned_transactions.jsonl": table},
            "primary_sources": {"IEK": {"source_id": "iek_transactions"}}})
        return directory

    def test_complete_reproducible_run_and_tampered_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            demand = self.make_input(root)
            first, a = run(demand, "forecast.json", root=root)
            second, b = run(demand, "forecast.json", root=root)
            self.assertEqual(a, b)
            self.assertEqual(sha256(first/"forecast_summary.json"), sha256(second/"forecast_summary.json"))
            self.assertEqual(a["tables"]["forecasts.jsonl"]["rows"], 3)
            self.assertTrue(verify(first, second)["byte_equivalent_rerun"])
            with self.assertRaises(FileExistsError):
                run(demand, "forecast.json", output_dir=first, root=root)
            with (demand/"cleaned_transactions.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                run(demand, "forecast.json", root=root)

    def test_config_rejects_invalid_policy(self):
        cfg = deepcopy(CONFIG)
        cfg["models"]["ewma"]["alpha"] = float("nan")
        with self.assertRaises(ValueError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
