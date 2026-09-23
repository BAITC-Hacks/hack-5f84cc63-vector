"""Audit demand artifacts independently of the detector and optionally compare reruns."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from vector_pipeline.config import sha256
from vector_pipeline.demand_cli import read_jsonl
from vector_pipeline.pipeline import dump


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(canonical, demand, compare=None):
    canonical, demand = Path(canonical), Path(demand)
    summary = json.loads((demand/"demand_summary.json").read_text(encoding="utf-8"))
    require(summary["status"] == "complete" and not (demand/"FAILED.json").exists(), "Incomplete demand run")
    for name, expected in summary["tables"].items():
        require(sha256(demand/name) == expected["sha256"], f"Artifact hash mismatch: {name}")
        require(sum(1 for _ in read_jsonl(demand/name)) == expected["rows"], f"Artifact count mismatch: {name}")
    for name in ("transactions", "monthly_measures", "quarantine", "source_controls"):
        require(sha256(canonical/(name+".jsonl")) == summary["input_sha256"][name], f"Canonical input mismatch: {name}")
    original = {r["record_id"]: r for r in read_jsonl(canonical/"transactions.jsonl")}
    blocked = {(r["source_id"], r["source_sheet"], r["source_row"])
               for table in ("quarantine", "source_controls") for r in read_jsonl(canonical/(table+".jsonl"))}
    members = defaultdict(list)
    signs = Counter()
    for row in read_jsonl(demand/"cleaned_transactions.jsonl"):
        raw = original.pop(row["record_id"])
        for key, value in raw.items():
            if key == "assumption_ids":
                require(set(value).issubset(row[key]), "Lost inherited assumptions")
            else:
                require(row[key] == value, f"Original field changed: {key}")
        qty, regular = row["qty_raw"], row["qty_regular"]
        signs["missing" if qty is None else "negative" if qty < 0 else "positive" if qty > 0 else "zero"] += 1
        if qty is None or qty < 0 or (row["source_id"], row["source_sheet"], row["source_row"]) in blocked:
            require(regular is None, "Invalid or unresolved quantity entered regular sales")
        if row["is_outlier"]:
            require(regular == 0, "Flagged quantity was not excluded")
        if row["cleaning_status"] in {"regular", "repeated_large_order", "insufficient_history", "unresolved_granularity"}:
            require(regular == qty, "Retained sale was changed")
        members[row["document_id"]].append((row["record_id"], qty, regular, row["is_outlier"]))
    require(not original, "Canonical transactions were lost")
    document_counts = defaultdict(Counter)
    flagged = {}
    for doc in read_jsonl(demand/"document_demand.jsonl"):
        rows = members.pop(doc["document_id"])
        require(set(doc["transaction_ids"]) == {r[0] for r in rows}, "Document membership mismatch")
        numeric = [r[1] for r in rows if r[1] is not None]
        require(doc["qty_raw"] == (math.fsum(numeric) if numeric else None), "Document raw quantity mismatch")
        regular = [r[2] for r in rows if r[2] is not None]
        require(doc["qty_regular"] == (math.fsum(regular) if regular else None), "Document regular quantity mismatch")
        require(all(r[3] == doc["is_outlier"] for r in rows), "Document flag propagation mismatch")
        baseline = doc["baseline"]
        if baseline and baseline["history_end"]:
            require(baseline["history_end"] < doc["date"], "Future or same-day baseline leakage")
        if doc["is_outlier"]:
            require(doc["qty_positive_raw"] > max(baseline["fences"].values()), "Flag below robust fences")
            flagged[doc["document_id"]] = doc
        if doc["cleaning_status"] == "repeated_large_order":
            cfg = summary["effective_config"]["detector"]
            require(baseline["similar_documents"] >= cfg["min_similar_documents"] and
                    baseline["similar_days"] >= cfg["min_similar_days"], "Missing recurrence evidence")
        document_counts[doc["supplier_id"]][doc["cleaning_status"]] += 1
    require(not members, "Transactions without document groups")
    for row in read_jsonl(demand/"outlier_records.jsonl"):
        require(flagged.pop(row["document_id"]) == row, "Outlier table differs from document table")
    require(not flagged, "Missing outlier records")
    for supplier, counts in document_counts.items():
        require(dict(counts) == summary["suppliers"][supplier]["document_status_counts"], "Supplier report count mismatch")
    recon_counts = defaultdict(Counter)
    for row in read_jsonl(demand/"reconciliation.jsonl"):
        recon_counts[row["source_id"]][row["status"]] += 1
    require(dict(recon_counts) == summary["reconciliation"], "Reconciliation summary mismatch")
    compared = False
    if compare:
        other = json.loads((Path(compare)/"demand_summary.json").read_text(encoding="utf-8"))
        require(summary == other, "Rerun summaries are not equivalent")
        for name in summary["tables"]:
            require(sha256(demand/name) == sha256(Path(compare)/name), f"Rerun mismatch: {name}")
        compared = True
    report = {"status": "passed", "transaction_rows": sum(signs.values()), "quantity_states": dict(signs),
              "documents_checked": sum(sum(c.values()) for c in document_counts.values()),
              "supplier_document_status_counts": dict(document_counts), "byte_equivalent_rerun": compared}
    dump(demand/"verification.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical")
    parser.add_argument("demand")
    parser.add_argument("--compare")
    args = parser.parse_args()
    print(json.dumps(verify(args.canonical, args.demand, args.compare), indent=2))
