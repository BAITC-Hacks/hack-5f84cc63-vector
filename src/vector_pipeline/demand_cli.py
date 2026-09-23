"""Reproducible demand cleaning from a verified canonical run; never opens Excel."""
import argparse
from collections import Counter
import json
from pathlib import Path
from uuid import uuid4

from .config import sha256
from .demand import clean_transactions, row_key, validate_config
from .pipeline import dump
from .reconciliation import reconcile
from .schema import validate


def read_jsonl(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def write_jsonl(path, records):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n")
    return {"rows": len(records), "sha256": sha256(path)}


def supplier_summary(supplier, cleaned, documents, count):
    docs = [d for d in documents if d["supplier_id"] == supplier]
    rows = [r for r in cleaned if r["supplier_id"] == supplier]
    flagged = [d for d in docs if d["is_outlier"]]
    scored = [d for d in docs if d["outlier_score"] is not None]
    example_fields = ("document_id", "document_number", "date", "sku_id", "warehouse_id", "stock_uom",
                      "qty_raw", "qty_regular", "cleaning_status", "outlier_score", "baseline", "source_id", "source_rows")
    def examples(candidates):
        return [{k: d[k] for k in example_fields} for d in sorted(candidates, key=lambda d: (-(d["outlier_score"] or 0), d["document_id"]))[:count]]
    return {"document_sku_groups": len(docs), "statistically_scored_groups": len(scored),
            "flagged_groups": len(flagged), "flagged_pct_all_groups": 100*len(flagged)/len(docs) if docs else 0,
            "flagged_pct_scored_groups": 100*len(flagged)/len(scored) if scored else 0,
            "document_status_counts": dict(sorted(Counter(d["cleaning_status"] for d in docs).items())),
            "transaction_rows": len(rows), "quantity_state_counts": dict(sorted(Counter(r["quantity_state"] for r in rows).items())),
            "transaction_status_counts": dict(sorted(Counter(r["cleaning_status"] for r in rows).items())),
            "missing_document_identifier_groups": sum(d["missing_document_identifier"] for d in docs),
            "flagged_examples": examples(flagged),
            "repeated_large_examples": examples([d for d in docs if d["cleaning_status"] == "repeated_large_order"]),
            "unflagged_scored_examples": examples([d for d in scored if not d["is_outlier"]])}


def run(canonical, config_path="config/demand.json", output_dir=None, root="."):
    root = Path(root).resolve()
    canonical = (root/canonical).resolve()
    config_path = (root/config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    inputs = {"config": config_path, "ingestion_summary": canonical/"ingestion_summary.json",
              "source_manifest": canonical/"source_manifest.json"}
    summary = json.loads(inputs["ingestion_summary"].read_text(encoding="utf-8"))
    manifest = json.loads(inputs["source_manifest"].read_text(encoding="utf-8"))
    if summary["status"] != "complete" or (canonical/"FAILED.json").exists():
        raise ValueError("Demand cleaning requires a completed canonical run")
    if sha256(inputs["source_manifest"]) != summary["config_sha256"]["manifest"]:
        # The manifest is reserialized during ingestion: compare semantic contents
        # with the fingerprinted registry when the exact bytes differ.
        registry = root/"config"/"sources.json"
        if sha256(registry) != summary["config_sha256"]["manifest"] or json.loads(registry.read_text(encoding="utf-8")) != manifest:
            raise ValueError("Canonical manifest does not match its fingerprinted registry")
        inputs["registry"] = registry
    sources = {s["source_id"]: s for s in manifest["sources"]}
    for supplier, sid in config["primary_sources"].items():
        source = sources[sid]
        if source["supplier_id"] != supplier or source["role"] != "transactions" or source.get("duplicate_of"):
            raise ValueError("Primary source must be a reviewed, active transaction source")
    data = {}
    for table in ("transactions", "monthly_measures", "quarantine", "source_controls"):
        path = canonical/f"{table}.jsonl"
        if sha256(path) != summary["tables"][path.name]["sha256"]:
            raise ValueError(f"Canonical artifact changed: {table}")
        inputs[table] = path
        data[table] = list(read_jsonl(path))
        if len(data[table]) != summary["tables"][path.name]["rows"]:
            raise ValueError(f"Canonical row count mismatch: {table}")
        for row in data[table]:
            validate(table, row)
        ids = [r["record_id"] for r in data[table]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate canonical record IDs: {table}")
    for row in data["transactions"]:
        if row["source_id"] != config["primary_sources"].get(row["supplier_id"]):
            raise ValueError("Unselected transaction source; review explicit source policy")
    input_hashes = {name: sha256(path) for name, path in inputs.items()}
    code_paths = [Path(__file__), Path(__file__).with_name("demand.py"), Path(__file__).with_name("reconciliation.py")]
    code_hashes = {p.name: sha256(p) for p in code_paths}
    directory = (root/output_dir).resolve() if output_dir else root/"data"/"processed"/"demand"/uuid4().hex[:12]
    allowed = root/"data"/"processed"
    if not directory.is_relative_to(allowed) or directory == allowed:
        raise ValueError("Demand run must be a new child of data/processed")
    directory.mkdir(parents=True, exist_ok=False)
    try:
        cleaned, documents = clean_transactions(data["transactions"], config,
            {row_key(r) for r in data["quarantine"]}, {row_key(r) for r in data["source_controls"]})
        reconciliation, reconciliation_counts = reconcile(cleaned, data["monthly_measures"], config)
        artifacts = {}
        for table, records in (("cleaned_transactions", cleaned), ("document_demand", documents),
                               ("outlier_records", [d for d in documents if d["is_outlier"]]),
                               ("reconciliation", reconciliation)):
            artifacts[table+".jsonl"] = write_jsonl(directory/(table+".jsonl"), records)
        selected = {supplier: {"source_id": sid, "path": sources[sid]["path"], "sheet": sources[sid]["sheet"],
                              "meaning": "observed_sales_not_ground_truth_demand"}
                    for supplier, sid in config["primary_sources"].items()}
        report = {"status": "complete", "version": 1, "input_sha256": input_hashes,
                  "code_sha256": code_hashes, "effective_config": config, "primary_sources": selected,
                  "tables": artifacts, "suppliers": {supplier: supplier_summary(supplier, cleaned, documents, config["examples_per_supplier"])
                    for supplier in sorted(config["primary_sources"])},
                  "reconciliation": reconciliation_counts,
                  "limitations": ["Flags are one-off candidates, not verified business labels or client-level evidence.",
                    "Documents are SKU/warehouse/unit/day groups, not unique invoices or customers.",
                    "Only earlier dates enter the baseline; same-day documents are excluded from each other's history.",
                    "Transaction event dates proxy data availability; posting/ingestion timestamps are absent.",
                    "Unresolved negatives and incomplete documents have null regular quantities, not inferred zero demand.",
                    "No stockout correction; missing months and absent observations are not zero demand.",
                    "Monthly comparisons are numeric diagnostics; semantics, units and warehouse scope are not validated.",
                    "Thresholds are explicit assumptions; repeated anomalies may be retained and genuine first bulk sales may be flagged."]}
        if any(sha256(path) != input_hashes[name] for name, path in inputs.items()):
            raise RuntimeError("Input changed during demand cleaning")
        if any(sha256(p) != code_hashes[p.name] for p in code_paths):
            raise RuntimeError("Demand implementation changed during the run")
        dump(directory/"demand_summary.json", report)
        return directory, report
    except Exception as exc:
        dump(directory/"FAILED.json", {"status": "failed", "error": str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical", help="Completed canonical run directory")
    parser.add_argument("--config", default="config/demand.json")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    directory, _ = run(args.canonical, args.config, args.output_dir)
    print(directory)


if __name__ == "__main__":
    main()
