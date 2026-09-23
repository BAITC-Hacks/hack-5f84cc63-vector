"""Read acceptance artifacts without rerunning or changing business calculations."""
import hashlib
import json
from pathlib import Path


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_hash(path):
    """Portable source fingerprint across Git CRLF/LF checkouts."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _within(root, relative):
    path = (root / str(relative).replace("\\", "/")).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evidence references a path outside its workspace")
    return path


def load_acceptance(root, processed):
    """Use the latest report, including failures; never hide it with an older PASS."""
    root, processed = Path(root), Path(processed)
    paths = list((processed / "acceptance").glob("*/case_acceptance.json"))
    if not paths:
        raise ValueError("No acceptance report is available")
    path = max(paths, key=lambda p: (p.stat().st_mtime_ns, p.parent.name))
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if path.parent.name != digest[:12]:
        raise ValueError("Acceptance report integrity check failed")
    report = json.loads(raw)
    if report.get("version") != 1 or set(report.get("cases", {})) != set("ABCDE"):
        raise ValueError("Unsupported or incomplete acceptance report")
    for case in report["cases"].values():
        if case.get("status") not in {"PASS", "PARTIAL", "FAIL"}:
            raise ValueError("Unknown acceptance status")
    if not report.get("input_files_unchanged") or not report.get("not_a_business_approval"):
        raise ValueError("Acceptance provenance is incomplete")
    if report.get("scope") == "public_synthetic":
        required = {f"src/vector_pipeline/{name}.py" for name in ("demand", "forecast", "stockout", "replenishment")}
        expected = report["source_text_sha256"]
        if not required.issubset(expected):
            raise ValueError("Acceptance report omits core calculation fingerprints")
        for relative, wanted in expected.items():
            file = _within(root, relative)
            if not file.is_file() or text_hash(file) != wanted:
                raise ValueError(f"Acceptance report is stale: {relative}")
        for relative, wanted in report["artifact_sha256"].items():
            file = _within(processed, relative)
            if not file.is_file() or _hash(file) != wanted:
                raise ValueError("Acceptance source artifact changed or is missing")
        return report, path, raw
    expected = {k.replace("\\", "/"): v for k, v in report["unchanged_input_sha256"].items()}
    required = {f"src/vector_pipeline/{name}.py" for name in ("demand", "forecast", "stockout", "replenishment")}
    if not required.issubset(expected):
        raise ValueError("Acceptance report omits core calculation fingerprints")
    expected.update({"app.py": report["app_sha256"], "scripts/accept_case.py": report["acceptance_script_sha256"],
                     "docs/CASE.md": report["case_definition_sha256"]})
    for relative, wanted in expected.items():
        file = _within(root, relative)
        if not file.is_file() or _hash(file) != wanted:
            raise ValueError(f"Acceptance report is stale: {relative}")
    # Successful numeric cards must reference the same saved artifacts the test inspected.
    if report.get("execution_status") == "PASS":
        real = report["real_examples"]
        for relative, wanted in ((f"replenishment/{real['replenishment_run']}/recommendations.jsonl", real["recommendation_sha256"]),
                                 (f"demand/{real['demand_run']}/outlier_records.jsonl", real["outlier_sha256"])):
            file = _within(processed, relative)
            if not file.is_file() or _hash(file) != wanted:
                raise ValueError("Acceptance source artifact changed or is missing")
    return report, path, raw
