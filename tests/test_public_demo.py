"""A targeted deployment smoke check, without the private data directory."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class PublicDemoTests(unittest.TestCase):
    def test_clean_checkout_workflow(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vector-public-smoke-") as tmp:
            clone = Path(tmp)
            for folder in ("src", "demo", "scripts"):
                shutil.copytree(root / folder, clone / folder, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            for relative in ("app.py", "docs/CASE.md", "config/demand.json", "config/forecast.json", "config/replenishment.json"):
                target = clone / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, target)
            env = {k: v for k, v in os.environ.items() if k != "VECTOR_PROCESSED_ROOT"}
            env["PYTHONPATH"] = str(clone / "src")
            check = subprocess.run([sys.executable, "-X", "utf8", "-c", r'''
from pathlib import Path
from streamlit.testing.v1 import AppTest
import vector_pipeline.dashboard as dashboard
assert Path(dashboard.__file__).is_relative_to(Path.cwd())
assert not Path("data").exists()
app = AppTest.from_file("app.py", default_timeout=30).run()
assert not app.exception and not app.error
assert any("ПУБЛИЧНОЕ ДЕМО" in x.value for x in app.info)
app.radio(key="language").set_value("en").run()
def widget(items, label):
    return next(w for w in items if w.label == label)
app.text_input(key="filter_query").set_value("SYN-IEK-001").run()
widget(app.number_input, "Available stock (шт)").set_value(55)
widget(app.text_input, "Scenario reason").set_value("Public demo smoke")
widget(app.button, "Recalculate SKU").click().run()
assert app.session_state.overrides["SYN-IEK-001"]["recommended_quantity"] == 65
widget(app.button, "Add / update draft").click().run()
app.radio(key="workspace").set_value("Draft review").run()
assert not app.get("download_button")
app.text_input(key="reviewer_name").set_value("Synthetic smoke reviewer")
app.checkbox(key="review_acknowledgement").check()
widget(app.button, "Mark draft reviewed").click().run()
assert len(app.get("download_button")) == 2
app.radio(key="workspace").set_value("Case validation").run()
assert not app.exception and not app.error and not app.warning
assert any("105 → 65 → 35" in x.value for x in app.markdown)
for detail in "BCDE":
    app.radio(key="evidence_detail").set_value(detail).run()
    assert not app.exception and not app.error
app.radio(key="language").set_value("ru").run()
assert any("ЧАСТИЧНО" in x.value for x in app.markdown)
app.radio(key="workspace").set_value("Data coverage").run()
assert not app.exception and not app.error
print("PASS: clean checkout, 4 pages, RU/EN, scenario, review and export; no private data")
'''], cwd=clone, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            print(check.stdout.strip())


if __name__ == "__main__":
    unittest.main()
