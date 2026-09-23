from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from vector_pipeline.config import sha256
from vector_pipeline.demand import clean_transactions, row_key, validate_config
from vector_pipeline.demand_cli import run, write_jsonl
from vector_pipeline.pipeline import dump
from vector_pipeline.reconciliation import reconcile
from scripts.verify_demand import verify


CONFIG = json.loads((Path(__file__).parents[1]/"config/demand.json").read_text())


def transaction(index, quantity=10, **changes):
    row = {"record_id": f"tx-{index:06}", "source_id": "iek_transactions", "source_sheet": "test",
           "source_row": index+1, "source_column": "H", "supplier_id": "IEK", "sku_id": "001_",
           "sku_raw": "001_", "quality_flags": [], "assumption_ids": [], "is_synthetic": True,
           "occurred_at": (datetime(2025, 1, 1)+timedelta(days=index)).isoformat(),
           "document_number": f"doc-{index}", "document_text": "Synthetic sale", "warehouse_id": "W1",
           "qty_raw": quantity, "qty_source_value": quantity, "stock_uom": "pieces",
           "customer_id": None, "price": None, "currency": None}
    row.update(changes)
    return row


def monthly(index=1, quantity=200, **changes):
    row = {"record_id": f"monthly-{index}", "source_id": "monthly_reference", "source_sheet": "test",
           "source_row": index+1, "source_column": "C", "supplier_id": "IEK", "sku_id": "001_",
           "quality_flags": [], "assumption_ids": [], "is_synthetic": True, "month": "2025-01",
           "qty_raw": quantity, "qty_source_value": quantity, "stock_uom": None,
           "warehouse_id": None, "measure_kind": "unknown", "snapshot_semantics": "unknown"}
    row.update(changes)
    return row


class DemandTests(unittest.TestCase):
    def clean(self, rows, **kwargs):
        return clean_transactions(rows, CONFIG, **kwargs)

    def test_repeated_normal_and_extreme_injection(self):
        rows = [transaction(i, [8, 10, 12][i % 3]) for i in range(30)]
        cleaned, docs = self.clean(rows+[transaction(30, 4500)])
        self.assertFalse(any(d["is_outlier"] for d in docs[:-1]))
        self.assertTrue(docs[-1]["is_outlier"])
        self.assertEqual(docs[-1]["qty_raw"], 4500)
        self.assertEqual(docs[-1]["qty_regular"], 0)
        baseline, _ = self.clean(rows)
        self.assertEqual(sum(r["qty_regular"] for r in cleaned), sum(r["qty_regular"] for r in baseline))

    def test_zero_mad_and_iqr_still_flags_extreme(self):
        _, docs = self.clean([transaction(i) for i in range(25)]+[transaction(25, 1000)])
        self.assertEqual(docs[-1]["baseline"]["mad"], 0)
        self.assertEqual(docs[-1]["baseline"]["threshold"], 80)
        self.assertTrue(docs[-1]["is_outlier"])

    def test_negative_zero_missing_remain_distinct(self):
        rows, _ = self.clean([transaction(0, -17), transaction(1, 0), transaction(2, None)])
        self.assertEqual([r["qty_raw"] for r in rows], [-17, 0, None])
        self.assertEqual([r["qty_regular"] for r in rows], [None, 0, None])
        self.assertEqual([r["quantity_state"] for r in rows], ["negative", "zero", "missing"])
        self.assertEqual(rows[0]["cleaning_status"], "unresolved_return_correction")

    def test_sparse_sale_preserved(self):
        rows, docs = self.clean([transaction(0, 100000)])
        self.assertEqual(rows[0]["qty_regular"], 100000)
        self.assertEqual(docs[0]["cleaning_status"], "insufficient_history")
        self.assertEqual(self.clean([transaction(0, 100000)]), (rows, docs))

    def test_repeated_bulk_history_prevents_automatic_exclusion(self):
        rows = [transaction(i) for i in range(50)]
        rows += [transaction(i, 1000) for i in range(50, 54)]
        _, docs = self.clean(rows)
        self.assertTrue(docs[-2]["is_outlier"])
        self.assertEqual(docs[-1]["cleaning_status"], "repeated_large_order")
        self.assertEqual(docs[-1]["qty_regular"], 1000)
        self.assertEqual(docs[-1]["baseline"]["similar_days"], 3)

    def test_routinely_large_orders_remain_regular(self):
        _, docs = self.clean([transaction(i, [2000, 3000, 4000][i % 3]) for i in range(30)]+[transaction(30, 5000)])
        self.assertFalse(docs[-1]["is_outlier"])
        self.assertEqual(docs[-1]["qty_regular"], 5000)

    def test_future_append_cannot_change_previous_decisions(self):
        prefix = [transaction(i) for i in range(30)]+[transaction(30, 10000)]
        before_rows, before_docs = self.clean(prefix)
        after_rows, after_docs = self.clean(prefix+[transaction(i, 100000) for i in range(31, 60)])
        self.assertEqual(before_rows, after_rows[:len(before_rows)])
        self.assertEqual(before_docs, after_docs[:len(before_docs)])

    def test_same_day_history_never_enters_baseline(self):
        rows = [transaction(i) for i in range(19)]
        rows += [transaction(20+i, 10000, occurred_at="2025-02-01T12:00:00") for i in range(5)]
        _, docs = self.clean(rows)
        self.assertTrue(all(d["cleaning_status"] == "insufficient_history" for d in docs))
        self.assertTrue(all(d["baseline"]["history_documents"] == 19 for d in docs[-5:]))

    def test_document_lines_aggregate_and_propagate_decision(self):
        rows = [transaction(i) for i in range(25)]
        rows += [transaction(30, 50), transaction(31, 50, document_number="doc-30", occurred_at=transaction(30)["occurred_at"])]
        cleaned, docs = self.clean(rows)
        self.assertEqual(len(docs), 26)
        self.assertTrue(docs[-1]["is_outlier"])
        self.assertEqual(docs[-1]["qty_raw"], 100)
        self.assertEqual([r["qty_regular"] for r in cleaned[-2:]], [0, 0])

    def test_missing_identifier_and_dimensions_never_merge(self):
        rows = [transaction(i, document_number=None, occurred_at="2025-01-01T12:00:00") for i in range(2)]
        rows += [transaction(2, stock_uom="meters", document_number="same"),
                 transaction(3, warehouse_id="W2", document_number="same", occurred_at=transaction(2)["occurred_at"])]
        _, docs = self.clean(rows)
        self.assertEqual(len(docs), 4)
        self.assertEqual(sum(d["missing_document_identifier"] for d in docs), 2)

    def test_total_quarantine_and_duplicate_cannot_enter_regular_or_history(self):
        rows = [transaction(0), transaction(1), transaction(2, quality_flags=["exact_duplicate_row"])]
        cleaned, docs = self.clean(rows, quarantined={row_key(rows[0])}, controls={row_key(rows[1])})
        self.assertTrue(all(r["qty_regular"] is None for r in cleaned))
        self.assertTrue(all(d["baseline"] is None for d in docs))

    def test_invalid_document_line_blocks_whole_document(self):
        rows = [transaction(0), transaction(1, None, document_number="doc-0", occurred_at=transaction(0)["occurred_at"])]
        cleaned, docs = self.clean(rows)
        self.assertTrue(all(r["qty_regular"] is None for r in cleaned))
        self.assertEqual(docs[0]["cleaning_status"], "incomplete_or_invalid_document")

    def test_history_expires_and_units_do_not_share_baseline(self):
        rows = [transaction(i) for i in range(25)]
        rows += [transaction(25, 1000, stock_uom="meters"), transaction(500, 1000)]
        _, docs = self.clean(rows)
        self.assertEqual([d["cleaning_status"] for d in docs[-2:]], ["insufficient_history"]*2)

    def test_order_independent_outputs(self):
        rows = [transaction(i, i+1) for i in range(30)]
        self.assertEqual(self.clean(rows), self.clean(list(reversed(rows))))

    def test_bad_config_rejected(self):
        cfg = deepcopy(CONFIG)
        cfg["detector"]["median_multiplier"] = float("nan")
        with self.assertRaises(ValueError):
            validate_config(cfg)


class ReconciliationTests(unittest.TestCase):
    def test_signed_raw_comparison_separate_from_regular_and_missing(self):
        cleaned, _ = clean_transactions([transaction(0, 20), transaction(1, -5)], CONFIG)
        reports, counts = reconcile(cleaned, [monthly(quantity=15), monthly(2, None, sku_id="absent")], CONFIG)
        self.assertEqual(counts["monthly_reference"], {"numeric_match": 1, "monthly_missing": 1})
        self.assertEqual(reports[0]["transaction_signed_quantity"], 15)
        self.assertEqual(reports[0]["transaction_breakdown"][0]["regular_observed_quantity"], 20)
        self.assertIn("monthly_unit_unknown_numeric_comparison_only", reports[0]["quality_flags"])

    def test_mixed_units_are_not_summed_and_absence_not_zero(self):
        cleaned, _ = clean_transactions([transaction(0), transaction(1, stock_uom="meters")], CONFIG)
        reports, _ = reconcile(cleaned, [monthly(), monthly(2, 0, sku_id="absent")], CONFIG)
        self.assertEqual(reports[0]["status"], "incompatible_or_ambiguous_dimensions")
        self.assertIsNone(reports[0]["transaction_signed_quantity"])
        self.assertEqual(reports[1]["status"], "no_transaction_observations")


class DemandRunTests(unittest.TestCase):
    def fixture(self, root):
        canonical = root/"data"/"processed"/"canonical"/"test"
        canonical.mkdir(parents=True)
        cfg = deepcopy(CONFIG)
        cfg["primary_sources"] = {"IEK": "iek_transactions"}
        dump(root/"demand.json", cfg)
        dump(canonical/"source_manifest.json", {"sources": [{"source_id": "iek_transactions", "supplier_id": "IEK",
            "role": "transactions", "sheet": "test", "path": "synthetic.xlsx"}]})
        tables = {}
        for name, rows in {"transactions": [transaction(i) for i in range(25)]+[transaction(25, 1000)],
                           "monthly_measures": [monthly()], "quarantine": [], "source_controls": []}.items():
            tables[name+".jsonl"] = write_jsonl(canonical/(name+".jsonl"), rows)
        dump(canonical/"ingestion_summary.json", {"status": "complete", "tables": tables,
            "config_sha256": {"manifest": sha256(canonical/"source_manifest.json")}})
        return canonical

    def test_end_to_end_repeat_hashes_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = self.fixture(root)
            first, a = run(canonical, "demand.json", root=root)
            second, b = run(canonical, "demand.json", root=root)
            self.assertEqual(a, b)
            self.assertEqual(sha256(first/"demand_summary.json"), sha256(second/"demand_summary.json"))
            self.assertEqual(a["suppliers"]["IEK"]["flagged_groups"], 1)
            audit = verify(canonical, first, second)
            self.assertTrue(audit["byte_equivalent_rerun"])
            with (canonical/"transactions.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                run(canonical, "demand.json", root=root)

    def test_existing_run_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = self.fixture(root)
            output, _ = run(canonical, "demand.json", root=root)
            with self.assertRaises(FileExistsError):
                run(canonical, "demand.json", output_dir=output, root=root)


if __name__ == "__main__":
    unittest.main()
