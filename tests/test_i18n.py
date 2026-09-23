import ast
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
from string import Formatter
import tempfile
import unittest
from unittest.mock import patch

from vector_pipeline.dashboard import export_draft
from vector_pipeline.i18n import TEXT, localize_frame, translate


class TranslationTests(unittest.TestCase):
    def test_catalog_and_format_fields_match(self):
        self.assertEqual(set(TEXT["ru"]), set(TEXT["en"]))
        for message in TEXT["en"]:
            fields = lambda text: {(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(text) if name is not None}
            with self.subTest(message=message):
                self.assertEqual(fields(message), fields(TEXT["ru"][message]))
                self.assertTrue(TEXT["ru"][message])

    def test_every_literal_translation_call_has_a_catalog_entry(self):
        root = Path(__file__).parents[1]
        messages = set()
        for path in (root / "app.py", root / "src/vector_pipeline/evidence_ui.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        messages.add(node.args[0].value)
        self.assertFalse(messages - TEXT["ru"].keys())

    def test_unknown_source_text_and_identifiers_are_not_formatted(self):
        self.assertEqual(translate("Original {source} 00123_"), "Original {source} 00123_")
        self.assertEqual(translate("Projected balance ({unit})", unit="шт"), "Прогноз остатка (шт)")


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install dashboard extras")
class LanguageUITests(unittest.TestCase):
    def test_language_switch_preserves_scenario_review_and_export(self):
        from streamlit.testing.v1 import AppTest
        from test_dashboard import write_run

        def widget(items, message, **values):
            return next(x for x in items if x.label == translate(message, **values))

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": tmp}):
            write_run(tmp)
            app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
            self.assertEqual(app.radio(key="language").value, "ru")
            self.assertEqual(app.title[0].value, "Планирование заказов")
            self.assertIn("Поставщик", app.dataframe[0].value.columns)
            widget(app.number_input, "Available stock ({v0})", v0="pieces").set_value(55)
            widget(app.text_input, "Scenario reason").set_value("Тест: уточнение остатка")
            widget(app.button, "Recalculate SKU").click().run()
            self.assertEqual(app.session_state.overrides["S"]["recommended_quantity"], 65)
            app.selectbox(key="filter_supplier").set_value("IEK").run()
            before = deepcopy(app.session_state.overrides)
            app.radio(key="language").set_value("en").run()
            self.assertEqual(app.title[0].value, "Order planning")
            self.assertEqual(app.session_state.filter_supplier, "IEK")
            self.assertEqual(app.session_state.overrides, before)
            app.radio(key="language").set_value("ru").run()
            widget(app.number_input, "Draft quantity").set_value(70)
            widget(app.text_input, "Reason for manual quantity change").set_value("Тест: количество менеджера")
            widget(app.button, "Add / update draft").click().run()
            app.radio(key="workspace").set_value("Draft review").run()
            widget(app.text_input, "Reviewer name").set_value("Тестовый проверяющий")
            app.checkbox[0].check()
            widget(app.button, "Mark draft reviewed").click().run()
            cart, review = deepcopy(app.session_state.cart), deepcopy(app.session_state.review)
            before_export = export_draft(cart, review, "test")
            for language in ("en", "ru"):
                app.radio(key="language").set_value(language).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state.cart, cart)
                self.assertEqual(app.session_state.review, review)
                self.assertEqual(len(app.get("download_button")), 2)
                self.assertEqual(export_draft(app.session_state.cart, app.session_state.review, "test"), before_export)
            app.radio(key="workspace").set_value("Data coverage").run()
            self.assertEqual(app.title[0].value, "Качество данных")
            self.assertFalse(app.exception)

    def test_evidence_in_both_languages_and_source_values_unchanged(self):
        import pandas as pd
        from streamlit.testing.v1 import AppTest
        from test_dashboard import write_run
        from test_evidence import ui_fixture

        frame = pd.DataFrame({"SKU": ["00001"], "Product": ["Order"], "Manager reason": ["Scenario"], "Order": [17]})
        localized = localize_frame(frame)
        self.assertEqual(localized["Товар"].iloc[0], "Order")
        self.assertEqual(localized["Причина изменения менеджером"].iloc[0], "Scenario")
        self.assertEqual(localized["SKU"].iloc[0], "00001")
        self.assertEqual(frame["Order"].iloc[0], 17)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_PROCESSED_ROOT": tmp}):
            run = write_run(tmp)
            report = ui_fixture()
            report["real_examples"]["recommendation_sha256"] = hashlib.sha256((run / "recommendations.jsonl").read_bytes()).hexdigest()
            with patch("vector_pipeline.evidence_ui.load_acceptance", return_value=(report, Path("fixture/case_acceptance.json"), b"{}")):
                app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
                app.radio(key="workspace").set_value("Case validation").run()
                for language in ("ru", "en"):
                    app.radio(key="language").set_value(language).run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.title[0].value, translate("Case validation", language))
                    html = "\n".join(x.value for x in app.markdown)
                    self.assertEqual(html.count(">"+translate("PARTIAL", language)+"</span>"), 2)
                    for key in "BCDE":
                        app.radio(key="evidence_detail").set_value(key).run()
                        self.assertFalse(app.exception)
                app.radio(key="language").set_value("ru").run()
                next(x for x in app.button if x.label == translate("Open IEK calculation")).click().run()
                self.assertEqual(app.session_state.filter_query, "00123_")
                self.assertEqual(app.title[0].value, "Планирование заказов")


if __name__ == "__main__":
    unittest.main()
