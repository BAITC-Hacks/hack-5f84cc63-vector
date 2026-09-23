from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vector_pipeline.evidence import load_acceptance
from test_dashboard import record, write_run


def publish(processed, report):
    raw = json.dumps(report, sort_keys=True).encode()
    path = Path(processed) / "acceptance" / hashlib.sha256(raw).hexdigest()[:12] / "case_acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def integrity_fixture(root):
    root = Path(root)
    fingerprints = {}
    for relative in ["app.py", "scripts/accept_case.py", "docs/CASE.md"] + [
            f"src/vector_pipeline/{name}.py" for name in ("demand", "forecast", "stockout", "replenishment")]:
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture", encoding="utf-8")
        fingerprints[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    processed = root / "data/processed"
    for relative in ("replenishment/test/recommendations.jsonl", "demand/test/outlier_records.jsonl"):
        file = processed / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    report = {"version": 1, "execution_status": "PASS", "input_files_unchanged": True,
        "not_a_business_approval": True, "app_sha256": fingerprints["app.py"],
        "acceptance_script_sha256": fingerprints["scripts/accept_case.py"],
        "case_definition_sha256": fingerprints["docs/CASE.md"], "unchanged_input_sha256": fingerprints,
        "cases": {key: {"status": "PARTIAL" if key in "AD" else "PASS"} for key in "ABCDE"},
        "real_examples": {"replenishment_run": "test", "demand_run": "test", "recommendation_sha256": digest, "outlier_sha256": digest}}
    publish(processed, report)
    return processed, report


class EvidenceIntegrityTests(unittest.TestCase):
    def test_missing_report_and_valid_content_address(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "No acceptance"):
                load_acceptance(root, Path(root) / "data/processed")
            processed, report = integrity_fixture(root)
            loaded, path, raw = load_acceptance(root, processed)
            self.assertEqual(loaded, report)
            self.assertEqual(path.parent.name, hashlib.sha256(raw).hexdigest()[:12])

    def test_tampered_report_and_stale_code_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            processed, report = integrity_fixture(root)
            path = publish(processed, report)
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "integrity"):
                load_acceptance(root, processed)
            publish(processed, report)
            (Path(root) / "src/vector_pipeline/forecast.py").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                load_acceptance(root, processed)

    def test_changed_artifact_and_path_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            processed, report = integrity_fixture(root)
            file = processed / "demand/test/outlier_records.jsonl"
            file.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact"):
                load_acceptance(root, processed)
            report["unchanged_input_sha256"]["../outside"] = "unused"
            publish(processed, report)
            with self.assertRaisesRegex(ValueError, "outside"):
                load_acceptance(root, processed)

    def test_latest_failed_run_never_falls_back_to_old_pass(self):
        with tempfile.TemporaryDirectory() as root:
            processed, report = integrity_fixture(root)
            old = publish(processed, report)
            os.utime(old, (1, 1))
            report["execution_status"] = "FAIL"
            report["cases"]["D"] = {"status": "FAIL", "error": "Acceptance failure"}
            publish(processed, report)
            self.assertEqual(load_acceptance(root, processed)[0]["cases"]["D"]["status"], "FAIL")


def ui_fixture():
    from scripts.accept_case import base_case, seasonal_case, stockout_case, one_off_case
    examples = dict(zip("ABCD", (base_case(), seasonal_case(), stockout_case(), one_off_case())))
    examples["E"] = {"evidence_type": "synthetic_AppTest", "recommended_quantities": [105, 105],
        "reviewed_draft_quantities": [110, 105], **{key: True for key in ("reason_required", "review_required",
        "edit_revokes_review", "stale_line_removed", "unrelated_line_preserved", "csv_and_json_verified")}}
    return {"execution_status": "PASS", "registered_workbooks_verified": 0,
        "cases": {key: {"status": "PARTIAL" if key in "AD" else "PASS", "evidence": value,
                        "limitations": ["Synthetic test fixture"]} for key, value in examples.items()},
        "real_examples": {"replenishment_run": "fixture", "recommendation_sha256": "replaced-by-fixture",
            "IEK": {"sku": "00123_", "baseline_order": 105, "stock_200_order": 0,
                    "stock_200_incoming_100_order": 0, "calculation": record()["calculation"]},
            "real_outlier_candidate": {"supplier_id": "IEK", "sku_id": "SYN-CANDIDATE", "date": "2026-08-28",
                "qty_raw": 9000, "qty_regular": 0, "stock_uom": "pieces", "baseline": {"median": 10, "threshold": 80}}}}


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install dashboard extras")
class EvidenceUITests(unittest.TestCase):
    def test_proofs_navigation_and_no_side_effect_on_draft(self):
        from streamlit.testing.v1 import AppTest
        def button(app, label):
            return next(x for x in app.button if x.label == label)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": tmp}):
            run = write_run(tmp)
            report = ui_fixture()
            report["real_examples"]["recommendation_sha256"] = hashlib.sha256((run / "recommendations.jsonl").read_bytes()).hexdigest()
            with patch("vector_pipeline.evidence_ui.load_acceptance", return_value=(report, Path("fixture/case_acceptance.json"), b"{}")):
                app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
                app.radio(key="language").set_value("en").run()
                button(app, "Add / update draft").click().run()
                before = deepcopy(app.session_state.cart)
                app.radio(key="workspace").set_value("Case validation").run()
                html = "\n".join(x.value for x in app.markdown)
                self.assertEqual(html.count(">PARTIAL</span>"), 2)
                self.assertEqual(html.count(">PASS</span>"), 3)
                self.assertIn("customer-level grouping is not implemented", html)
                for label, key in (("Seasonal proof", "B"), ("Stockout proof", "C"),
                                   ("One-off proof + real candidate", "D"), ("Review workflow proof", "E")):
                    button(app, label).click().run()
                    self.assertEqual(app.session_state.evidence_detail, key)
                    self.assertFalse(app.exception)
                button(app, "Open forecast inputs").click().run()
                self.assertEqual(app.radio(key="workspace").value, "Order planning")
                self.assertEqual(app.session_state.filter_query, "00123_")
                self.assertEqual(app.get("tab_container")[0].proto.tab_container.default_tab_index, 2)
                self.assertEqual(app.session_state.cart, before)
                app.radio(key="workspace").set_value("Case validation").run()
                button(app, "Open draft review").click().run()
                self.assertEqual(app.radio(key="workspace").value, "Draft review")
                self.assertFalse(app.get("download_button"))

    def test_stale_report_hidden_and_other_run_links_disabled(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": tmp}):
            write_run(tmp)
            with patch("vector_pipeline.evidence_ui.load_acceptance", side_effect=ValueError("Acceptance report is stale")):
                app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
                app.radio(key="language").set_value("en").run()
                app.radio(key="workspace").set_value("Case validation").run()
                self.assertTrue(app.warning)
                self.assertFalse(app.button)
            with patch("vector_pipeline.evidence_ui.load_acceptance", return_value=(ui_fixture(), Path("fixture/case_acceptance.json"), b"{}")):
                app.run()
                self.assertTrue(next(x for x in app.button if x.label == "Open IEK calculation").disabled)
                self.assertTrue(next(x for x in app.button if x.label == "Open forecast inputs").disabled)
                self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
