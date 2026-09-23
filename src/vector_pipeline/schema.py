"""JSONL table contracts; validation is independent of supplier layout."""
from datetime import date, datetime
import math
import re

PROVENANCE = {
    "record_id": "str", "source_id": "str", "source_sheet": "str",
    "source_row": "int", "source_column": "str?", "supplier_id": "str",
    "quality_flags": "list", "assumption_ids": "list", "is_synthetic": "bool",
}
FIELDS = {
    "product_observations": {"sku_id":"str", "sku_raw":"any", "name":"str?", "supplier_article":"str?", "stock_uom":"str?"},
    "transactions": {"sku_id":"str", "sku_raw":"any", "occurred_at":"datetime?", "document_number":"str?",
        "document_text":"str?", "warehouse_id":"str?", "qty_raw":"number?", "qty_source_value":"any",
        "stock_uom":"str?", "customer_id":"str?", "price":"number?", "currency":"str?"},
    "monthly_measures": {"sku_id":"str", "month":"month", "measure_kind":"str", "qty_raw":"number?",
        "qty_source_value":"any", "stock_uom":"str?", "warehouse_id":"str?", "snapshot_semantics":"str"},
    "inventory_snapshots": {"sku_id":"str", "snapshot_date":"date?", "period_month":"month",
        "snapshot_semantics":"str", "qty_on_hand":"number?", "qty_source_value":"any",
        "qty_reserved":"number?", "qty_available":"number?", "warehouse_id":"str?", "stock_uom":"str?"},
    "shipments": {"sku_id":"str", "shipment_id":"str?", "qty":"number?", "qty_source_value":"any",
        "uom":"str?", "expected_date":"date?", "date_kind":"str", "received_at":"datetime?", "source_header":"str"},
    "order_constraints": {"sku_id":"str", "constraint_value_raw":"any", "constraint_value":"number?",
        "source_label":"str", "constraint_kind":"str", "minimum_order_qty":"number?", "order_multiple":"number?",
        "order_uom":"str?", "conversion_to_stock_uom":"number?", "value_status":"str"},
    "aggregate_measures": {"month":"month", "value":"number?", "value_raw":"any", "metric":"str", "uom":"str?"},
    "source_coefficients": {"scope":"str", "scope_id":"str", "coefficient_kind":"str",
        "month_of_year":"int?", "value":"number?", "value_raw":"any", "reference_period":"str",
        "available_at":"datetime?", "is_source_forecast":"bool?", "source_label":"str"},
    "source_controls": {"sku_id":"str?", "source_label":"str", "value_raw":"any"},
    "quarantine": {"reason":"str", "raw_values":"list"},
}
SCHEMAS = {table: {**PROVENANCE, **fields} for table, fields in FIELDS.items()}
SCHEMAS["products"] = {"supplier_id":"str", "sku_id":"str", "sku_raw_variants":"list",
    "name":"str?", "supplier_article":"str?", "stock_uom":"str?", "category_id":"str?",
    "attribute_candidates":"dict", "source_refs":"list", "quality_flags":"list", "is_synthetic":"bool"}
ENUMS = {
    "monthly_measures": {"measure_kind":{"sales","inventory","unknown"}, "snapshot_semantics":{"beginning","end","as_of","unknown"}},
    "inventory_snapshots": {"snapshot_semantics":{"beginning","end","unknown"}},
    "shipments": {"date_kind":{"deadline","expected","unknown"}},
    "order_constraints": {"constraint_kind":{"minimum","multiple","unknown"}, "value_status":{"valid","missing","invalid"}},
    "source_coefficients": {"scope":{"portfolio","category","sku"}},
}


def validate(table, record):
    fields = SCHEMAS[table]
    if record.keys() != fields.keys():
        raise ValueError(f"{table}: missing={fields.keys()-record.keys()}, extra={record.keys()-fields.keys()}")
    for key, spec in fields.items():
        value = record[key]
        if spec == "any" or (spec.endswith("?") and value is None):
            continue
        kind = spec.rstrip("?")
        ok = {"str":lambda: isinstance(value,str) and bool(value),
              "int":lambda: isinstance(value,int) and not isinstance(value,bool),
              "bool":lambda: isinstance(value,bool), "list":lambda: isinstance(value,list),
              "dict":lambda: isinstance(value,dict),
              "number":lambda: isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value),
              "month":lambda: isinstance(value,str) and bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",value)),
              "date":lambda: isinstance(value,str) and date.fromisoformat(value) is not None,
              "datetime":lambda: isinstance(value,str) and datetime.fromisoformat(value) is not None}[kind]()
        if not ok:
            raise ValueError(f"{table}.{key}: expected {spec}, got {value!r}")
    for key, options in ENUMS.get(table,{}).items():
        if record[key] not in options:
            raise ValueError(f"{table}.{key}: unsupported value {record[key]!r}")
    if "source_row" in record and record["source_row"] < 1:
        raise ValueError("Source row must use one-based Excel coordinates")
    if table == "order_constraints":
        for key in ("minimum_order_qty","order_multiple"):
            if record[key] is not None and record[key] <= 0:
                raise ValueError(f"{key} must be positive or null")
