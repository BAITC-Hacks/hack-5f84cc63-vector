"""Local review helpers; calculations remain in the existing replenishment engine."""
from copy import deepcopy
import csv
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path

from .replenishment import recommend


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def discover_runs(root):
    """List completed runs, collapsing identical replays, scenario runs first."""
    found, seen = [], set()
    paths = sorted(Path(root).glob("*/replenishment_summary.json"),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in paths:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            key = (summary["tables"]["recommendations.jsonl"]["sha256"],
                   fingerprint(summary["effective_config"]))
            if summary.get("status") != "complete" or key in seen:
                continue
            seen.add(key)
            found.append({"path": str(path.parent), "mode": summary["mode"],
                          "planning_date": summary["planning_date"]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda r: r["mode"] != "demo_scenario")


def checked_table(path, metadata):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
        raise ValueError(f"Integrity check failed: {Path(path).name}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    if len(rows) != metadata["rows"]:
        raise ValueError(f"Row count mismatch: {Path(path).name}")
    return rows


def load_run(path):
    path = Path(path)
    summary = json.loads((path / "replenishment_summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "complete":
        raise ValueError("Only completed runs can be reviewed")
    rows = checked_table(path / "recommendations.jsonl", summary["tables"]["recommendations.jsonl"])
    if len({r["series_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate recommendation series")
    return summary, rows


def load_products(processed, summary):
    """Resolve the exact upstream manifest; never choose an unrelated latest run."""
    expected = summary.get("input_sha256", {}).get("ingestion_summary")
    for path in sorted((Path(processed) / "canonical").glob("*/ingestion_summary.json")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            continue
        source = json.loads(path.read_text(encoding="utf-8"))
        rows = checked_table(path.parent / "products.jsonl", source["tables"]["products.jsonl"])
        return {(r["supplier_id"], r["sku_id"]): r for r in rows}
    return {}


def scenario(record, policies, *, stock, lead, review, safety, delay, reason,
             receipt_qty=0, receipt_date=None):
    """Edit one series, retain original evidence, and delay only future receipts."""
    if not reason.strip():
        raise ValueError("Provide a reason for the scenario")
    if type(delay) is not int or delay < 0:
        raise ValueError("Receipt delay must be a nonnegative integer")
    inputs = deepcopy(record["inputs"])
    changes = {"available_stock": stock, "lead_time_days": lead,
               "review_period_days": review, "safety_days": safety}
    for key, value in changes.items():
        previous = inputs[key]
        inputs[key] = {"value": value, "status": "scenario", "id": f"UI-{key.upper()}",
                       "reason": reason.strip(), "original_input": previous}
    inputs["available_stock"]["snapshot_date"] = record["planning_date"]
    if delay:
        incoming = inputs["incoming"]
        incoming["original_input"] = deepcopy(incoming)
        incoming.update(status="scenario", id="UI-RECEIPT-DELAY", reason=reason.strip())
        for receipt in incoming.get("shipments", []):
            arrival = date.fromisoformat(receipt["arrival_date"])
            if receipt["arrival_date"] >= record["planning_date"]:
                receipt["original_arrival_date"] = receipt["arrival_date"]
                receipt["arrival_date"] = (arrival + timedelta(days=delay)).isoformat()
                receipt.update(status="scenario", id="UI-RECEIPT-DELAY", reason=reason.strip())
    if type(receipt_qty) not in (int, float) or not math.isfinite(receipt_qty) or receipt_qty < 0:
        raise ValueError("Scenario receipt must be finite and nonnegative")
    if receipt_qty:
        if not receipt_date or date.fromisoformat(receipt_date) < date.fromisoformat(record["planning_date"]):
            raise ValueError("Scenario receipt must arrive on or after the planning date")
        incoming = inputs["incoming"]
        incoming.update(status="scenario", id="UI-ADDITIONAL-RECEIPT", reason=reason.strip())
        incoming.setdefault("shipments", []).append({"shipment_id": "UI-ADDITIONAL-RECEIPT",
            "qty_stock_units": receipt_qty, "arrival_date": receipt_date,
            "stock_uom": record["stock_uom"], "warehouse_id": record["warehouse_id"],
            "status": "scenario", "id": "UI-ADDITIONAL-RECEIPT", "reason": reason.strip()})
    forecasts = deepcopy(record.get("forecast_values_used", []))
    result = recommend(record, forecasts, inputs, record["planning_date"], policies)
    result.update(forecast_values_used=forecasts, is_synthetic_inventory_scenario=True,
                  ui_scenario={"stock": stock, "lead": lead, "review": review,
                               "safety": safety, "delay": delay, "reason": reason.strip(),
                               "receipt_qty": receipt_qty, "receipt_date": receipt_date})
    return result


def draft_line(record, quantity, reason=""):
    if record.get("recommended_quantity") is None or record["recommendation_status"] == "unavailable":
        raise ValueError("Unavailable recommendations cannot enter a draft")
    if type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("Draft quantity must be finite and positive; remove the line to skip it")
    if quantity != record["recommended_quantity"] and not reason.strip():
        raise ValueError("A manual quantity change requires a reason")
    calc = record["calculation"]
    minimum, step = calc.get("minimum_stock_units"), calc.get("effective_rounding_step")
    if minimum and quantity < minimum:
        raise ValueError(f"Quantity is below the applied minimum of {minimum}")
    if step and Decimal(str(quantity)) % Decimal(str(step)) != 0:
        raise ValueError(f"Quantity must be a multiple of the applied step {step}")
    return {"recommendation": deepcopy(record), "order_quantity": quantity,
            "manager_reason": reason.strip()}


def review_draft(cart, reviewer, acknowledged, reviewed_at):
    if not cart or not reviewer.strip() or not acknowledged:
        raise ValueError("Review requires a nonempty draft, reviewer name, and acknowledgement")
    for line in cart.values():
        draft_line(line["recommendation"], line["order_quantity"], line["manager_reason"])
    return {"cart_sha256": fingerprint(cart), "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at, "scope": "local_draft_review_only"}


def review_is_current(cart, review):
    return bool(cart and review and review.get("cart_sha256") == fingerprint(cart))


def _spreadsheet_safe(value):
    # Formula injection is possible in identifiers, reasons, and reviewer names.
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def export_draft(cart, review, run_id):
    if not review_is_current(cart, review):
        raise ValueError("The current draft must be reviewed before export")
    lines = sorted(cart.values(), key=lambda x: tuple(str(x["recommendation"].get(k, ""))
                   for k in ("supplier_id", "sku_id", "warehouse_id", "stock_uom")))
    payload = {"schema_version": 1, "status": "reviewed_draft_not_sent", "source_run": run_id,
               "review": review, "lines": lines}
    fields = ["supplier_id", "sku_id", "warehouse_id", "stock_uom", "planning_date",
              "recommended_quantity", "order_quantity", "recommendation_status", "urgency",
              "forecast_model", "stockout_adjustment_status", "manager_reason", "explanation",
              "assumption_ids", "reviewer", "reviewed_at", "source_run"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for line in lines:
        record = line["recommendation"]
        row = {k: record.get(k, "") for k in fields}
        row.update(order_quantity=line["order_quantity"], manager_reason=line["manager_reason"],
                   assumption_ids=";".join(record["assumption_ids"]), reviewer=review["reviewer"],
                   reviewed_at=review["reviewed_at"], source_run=run_id)
        writer.writerow({k: _spreadsheet_safe(v) if isinstance(v, str) else v for k, v in row.items()})
    return output.getvalue().encode("utf-8-sig"), json.dumps(payload, ensure_ascii=False,
                    sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
