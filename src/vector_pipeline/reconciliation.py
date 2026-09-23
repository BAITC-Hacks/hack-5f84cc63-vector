"""Numeric diagnostics against monthly sources without declaring scope equivalence."""
from collections import Counter, defaultdict
import math


def reconcile(transactions, monthly, config):
    grouped = defaultdict(list)
    supplier_last_date = {}
    for row in transactions:
        if not row["occurred_at"]:
            continue
        supplier = row["supplier_id"]
        supplier_last_date[supplier] = max(supplier_last_date.get(supplier, ""), row["occurred_at"][:10])
        grouped[(supplier, row["sku_id"], row["occurred_at"][:7])].append(row)
    breakdowns = {}
    for key, rows in grouped.items():
        dimensions = defaultdict(list)
        for row in rows:
            dimensions[(row["warehouse_id"], row["stock_uom"])].append(row)
        breakdowns[key] = []
        for (warehouse, unit), members in sorted(dimensions.items(), key=lambda item: str(item[0])):
            raw = [r["qty_raw"] for r in members if r["qty_raw"] is not None]
            regular = [r["qty_regular"] for r in members if r["qty_regular"] is not None]
            breakdowns[key].append({"warehouse_id": warehouse, "stock_uom": unit,
                "transaction_rows": len(members), "raw_numeric_rows": len(raw),
                "missing_quantity_rows": len(members)-len(raw),
                "negative_rows": sum(r["qty_raw"] is not None and r["qty_raw"] < 0 for r in members),
                "signed_observed_quantity": math.fsum(raw) if raw else None,
                "regular_observed_quantity": math.fsum(regular) if regular else None,
                "unresolved_regular_rows": len(members)-len(regular)})
    reports = []
    counters = defaultdict(Counter)
    source_keys = defaultdict(set)
    source_months = defaultdict(set)
    source_suppliers = {}
    for cell in sorted(monthly, key=lambda r: r["record_id"]):
        sid = cell["source_id"]
        key = (cell["supplier_id"], cell["sku_id"], cell["month"])
        source_keys[sid].add(key)
        source_months[sid].add(cell["month"])
        source_suppliers[sid] = cell["supplier_id"]
        parts = breakdowns.get(key, [])
        units = {p["stock_uom"] for p in parts}
        compatible_units = len(units) == 1 and None not in units and (cell["stock_uom"] is None or cell["stock_uom"] in units)
        compatible_warehouse = cell["warehouse_id"] is None or all(p["warehouse_id"] == cell["warehouse_id"] for p in parts)
        flags = ["source_scope_equivalence_unverified", "transaction_month_coverage_unverified"]
        if cell["measure_kind"] != "sales":
            flags.append("monthly_semantics_unresolved_or_not_sales")
        if cell["stock_uom"] is None:
            flags.append("monthly_unit_unknown_numeric_comparison_only")
        if cell["warehouse_id"] is None:
            flags.append("monthly_warehouse_unknown")
        if cell["month"] == supplier_last_date.get(cell["supplier_id"], "")[:7]:
            flags.append("transaction_boundary_month_potentially_partial")
        raw_parts = [p["signed_observed_quantity"] for p in parts if p["signed_observed_quantity"] is not None]
        tx_value = math.fsum(raw_parts) if raw_parts and compatible_units and compatible_warehouse else None
        delta = None
        if cell["qty_raw"] is None:
            status = "monthly_missing"
        elif not parts:
            status = "no_transaction_observations"
        elif not compatible_units or not compatible_warehouse:
            status = "incompatible_or_ambiguous_dimensions"
        elif tx_value is None:
            status = "transaction_quantity_missing"
        else:
            delta = cell["qty_raw"]-tx_value
            tolerance = config["reconciliation"]
            equal = math.isclose(cell["qty_raw"], tx_value, abs_tol=tolerance["absolute_tolerance"], rel_tol=tolerance["relative_tolerance"])
            status = "numeric_match" if equal else "numeric_difference"
            if any(p["missing_quantity_rows"] for p in parts):
                flags.append("transaction_total_has_missing_quantities")
        counters[sid][status] += 1
        reports.append({"monthly_record_id": cell["record_id"], "source_id": sid,
            "source_sheet": cell["source_sheet"], "source_row": cell["source_row"], "source_column": cell["source_column"],
            "supplier_id": cell["supplier_id"], "sku_id": cell["sku_id"], "month": cell["month"],
            "measure_kind": cell["measure_kind"], "monthly_quantity": cell["qty_raw"],
            "monthly_warehouse_id": cell["warehouse_id"], "monthly_stock_uom": cell["stock_uom"],
            "transaction_breakdown": parts, "transaction_signed_quantity": tx_value,
            "difference_monthly_minus_transactions": delta, "status": status, "quality_flags": flags})
    # Include transaction-only SKU/month keys inside each monthly source's period range.
    for sid in sorted(source_keys):
        for key in sorted(grouped):
            supplier, sku, month = key
            if supplier != source_suppliers[sid] or month not in source_months[sid] or key in source_keys[sid]:
                continue
            counters[sid]["transaction_only_key"] += 1
            reports.append({"source_id": sid, "monthly_record_id": None, "supplier_id": supplier,
                            "sku_id": sku, "month": month, "status": "transaction_only_key",
                            "monthly_quantity": None, "transaction_breakdown": breakdowns[key],
                            "difference_monthly_minus_transactions": None})
    return reports, {sid: dict(counts) for sid, counts in sorted(counters.items())}
