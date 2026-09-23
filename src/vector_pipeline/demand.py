"""Causal, explainable document screening of observed sales, not latent demand."""
from collections import defaultdict, deque
from datetime import date, timedelta
from itertools import groupby
import hashlib
import json
import math
from statistics import median


def stable_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_config(config):
    if config.get("version") != 1:
        raise ValueError("Unsupported demand config version")
    for key, value in {"monthly_policy": "reconciliation_only",
                       "negative_policy": "unresolved_exclude_from_regular",
                       "outlier_action": "exclude_document_positive_quantity"}.items():
        if config.get(key) != value:
            raise ValueError(f"Unsupported demand policy: {key}")
    if not config.get("primary_sources") or not config.get("assumption_ids"):
        raise ValueError("Explicit primary sources and assumption IDs are required")
    detector = config["detector"]
    integer_keys = ("lookback_days", "max_history_documents", "min_history_documents",
                    "min_history_days", "min_similar_documents", "min_similar_days")
    for key in integer_keys:
        if type(detector[key]) is not int or detector[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("median_multiplier", "iqr_multiplier", "mad_multiplier", "mad_scale", "repeat_quantity_factor"):
        value = detector[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if detector["median_multiplier"] <= 1 or detector["repeat_quantity_factor"] <= 1:
        raise ValueError("Quantity multipliers must exceed one")
    if detector["min_history_documents"] > detector["max_history_documents"]:
        raise ValueError("Minimum history exceeds history capacity")
    if detector["min_history_days"] > detector["min_history_documents"]:
        raise ValueError("Minimum days exceeds minimum documents")
    if detector["min_similar_days"] > detector["min_similar_documents"]:
        raise ValueError("Minimum similar days exceeds similar documents")
    for value in config["reconciliation"].values():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Reconciliation tolerances must be finite and nonnegative")
    if type(config["examples_per_supplier"]) is not int or config["examples_per_supplier"] < 1:
        raise ValueError("Example count must be a positive integer")


def row_key(row):
    return row["source_id"], row["source_sheet"], row["source_row"]


def quantity_state(value):
    return "missing" if value is None else "negative" if value < 0 else "positive" if value > 0 else "zero"


def exclusion_reason(row, quarantined, controls):
    if row_key(row) in controls or {"total_row", "subtotal_row"}.intersection(row["quality_flags"]):
        return "excluded_total"
    if row["qty_raw"] is None:
        return "missing_quantity"
    if row_key(row) in quarantined:
        return "quarantined"
    if not row["occurred_at"]:
        return "invalid_date"
    if "exact_duplicate_row" in row["quality_flags"]:
        return "duplicate_unresolved"
    return None


def build_documents(transactions, config, quarantined=(), controls=()):
    """A document without a number stays a singleton, even with matching text."""
    groups = defaultdict(list)
    quarantined, controls = set(quarantined), set(controls)
    for row in transactions:
        identifier = ["number", row["document_number"]] if row["document_number"] else ["row", row["record_id"]]
        key = (row["supplier_id"], row["source_id"], row["sku_id"],
               (row["occurred_at"] or "")[:10], row["warehouse_id"], row["stock_uom"],
               json.dumps(identifier))
        groups[key].append(row)
    documents = []
    row_decisions = {}
    for key, rows in sorted(groups.items(), key=lambda item: json.dumps(item[0])):
        rows.sort(key=lambda r: r["record_id"])
        reasons = {r["record_id"]: exclusion_reason(r, quarantined, controls) for r in rows}
        numeric = [r["qty_raw"] for r in rows if r["qty_raw"] is not None]
        positive = [r["qty_raw"] for r in rows if r["qty_raw"] is not None and r["qty_raw"] > 0]
        negatives = any(q < 0 for q in numeric)
        blocked = any(reasons.values())
        status = ("incomplete_or_invalid_document" if blocked else
                  "unresolved_return_correction" if negatives else
                  "zero_quantity" if not positive else
                  "unresolved_granularity" if key[4] is None or key[5] is None else "pending")
        doc = {"document_id": stable_id(key), "supplier_id": key[0], "source_id": key[1],
               "sku_id": key[2], "date": key[3] or None, "warehouse_id": key[4], "stock_uom": key[5],
               "document_number": rows[0]["document_number"], "missing_document_identifier": not bool(rows[0]["document_number"]),
               "transaction_ids": [r["record_id"] for r in rows],
               "source_rows": [r["source_row"] for r in rows], "source_sheet": rows[0]["source_sheet"],
               "qty_raw": math.fsum(numeric) if numeric else None,
               "qty_positive_raw": math.fsum(positive), "missing_quantity_rows": len(rows)-len(numeric),
               "qty_regular": None if blocked or negatives else math.fsum(positive),
               "cleaning_status": status, "is_outlier": False, "outlier_score": None,
               "outlier_reason": status, "baseline": None,
               "quality_flags": sorted({f for r in rows for f in r["quality_flags"]}),
               "assumption_ids": sorted(set(config["assumption_ids"]) | {a for r in rows for a in r["assumption_ids"]}),
               "is_synthetic": any(r["is_synthetic"] for r in rows)}
        documents.append(doc)
        for row in rows:
            row_decisions[row["record_id"]] = (doc, reasons[row["record_id"]])
    return documents, row_decisions


def quantile(values, probability):
    """Linear interpolation on sorted observations, including tiny samples."""
    position = (len(values)-1)*probability
    lower = int(position)
    upper = min(lower+1, len(values)-1)
    return values[lower] + (values[upper]-values[lower])*(position-lower)


def classify(doc, history, cfg):
    values = sorted(item[1] for item in history)
    distinct_days = len({item[0] for item in history})
    baseline = {"history_documents": len(values), "history_days": distinct_days,
                "history_start": history[0][0].isoformat() if history else None,
                "history_end": history[-1][0].isoformat() if history else None}
    doc["baseline"] = baseline
    if len(values) < cfg["min_history_documents"] or distinct_days < cfg["min_history_days"]:
        doc.update(cleaning_status="insufficient_history", outlier_reason="Positive sale preserved; too little prior history")
        return
    center = median(values)
    q1, q3 = quantile(values, .25), quantile(values, .75)
    mad = median([abs(value-center) for value in values])  # Deviations, never signed sales.
    fences = {"median_ratio": center*cfg["median_multiplier"],
              "iqr": q3+cfg["iqr_multiplier"]*(q3-q1),
              "mad": center+cfg["mad_multiplier"]*cfg["mad_scale"]*mad}
    threshold = max(fences.values())
    qty = doc["qty_positive_raw"]
    similar = [item for item in history if qty/cfg["repeat_quantity_factor"] <= item[1] <= qty*cfg["repeat_quantity_factor"]]
    repeated = len(similar) >= cfg["min_similar_documents"] and len({item[0] for item in similar}) >= cfg["min_similar_days"]
    baseline.update(median=center, q1=q1, q3=q3, mad=mad, fences=fences, threshold=threshold,
                    similar_documents=len(similar), similar_days=len({item[0] for item in similar}),
                    quantity_to_median=qty/center)
    doc["outlier_score"] = qty/threshold
    if qty > threshold and not repeated:
        doc.update(is_outlier=True, qty_regular=0.0, cleaning_status="flagged_one_off_candidate",
                   outlier_reason="Quantity exceeds all robust fences; insufficient prior comparable repeats")
    elif qty > threshold:
        doc.update(cleaning_status="repeated_large_order", outlier_reason="Above robust fences, but comparable quantities recur on prior days")
    else:
        doc.update(cleaning_status="regular", outlier_reason="Quantity does not exceed every robust fence")


def clean_transactions(transactions, config, quarantined=(), controls=()):
    documents, decisions = build_documents(transactions, config, quarantined, controls)
    series = defaultdict(list)
    for doc in documents:
        if doc["cleaning_status"] == "pending":
            series[(doc["supplier_id"], doc["sku_id"], doc["warehouse_id"], doc["stock_uom"])].append(doc)
    cfg = config["detector"]
    for docs in series.values():
        history = deque(maxlen=cfg["max_history_documents"])
        for day_string, batch in groupby(sorted(docs, key=lambda d: (d["date"], d["document_id"])), key=lambda d: d["date"]):
            batch = list(batch)
            day = date.fromisoformat(day_string)
            cutoff = day-timedelta(days=cfg["lookback_days"])
            while history and history[0][0] < cutoff:
                history.popleft()
            # All same-day decisions precede updates: no partial-document or tie leakage.
            for doc in batch:
                classify(doc, history, cfg)
            for doc in batch:
                # Keep flagged raw history too, so recurring bulk sales can become recognized.
                history.append((day, doc["qty_positive_raw"]))
    cleaned = []
    for row in sorted(transactions, key=lambda r: r["record_id"]):
        doc, excluded = decisions[row["record_id"]]
        state = quantity_state(row["qty_raw"])
        status = excluded or ("unresolved_return_correction" if state == "negative" else doc["cleaning_status"])
        qty_regular = (None if doc["qty_regular"] is None else
                       0.0 if doc["is_outlier"] else row["qty_raw"])
        cleaned.append({**row, "document_id": doc["document_id"], "quantity_state": state,
                        "qty_regular": qty_regular, "is_outlier": doc["is_outlier"],
                        "outlier_score": doc["outlier_score"], "outlier_reason": doc["outlier_reason"],
                        "cleaning_status": status, "assumption_ids": doc["assumption_ids"]})
    documents.sort(key=lambda d: (d["date"] or "", d["document_id"]))
    return cleaned, documents
