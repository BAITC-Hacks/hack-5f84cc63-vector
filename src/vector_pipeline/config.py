"""Validated source manifests and explicit, traceable semantic overrides."""
import hashlib
import json
from pathlib import Path
import re

ALLOWED_OVERRIDES = {
    "measure_kind":{"sales","inventory","unknown"},
    "snapshot_semantics":{"beginning","end","unknown"},
    "constraint_kind":{"minimum","multiple","unknown"},
}
ROLES = {"transactions","monthly_beginning_inventory","monthly_quantity_unspecified","dated_shipments","order_constraints","aggregate_seasonality"}


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle,"sha256").hexdigest()


def load_config(manifest_path, settings_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    settings = json.loads(Path(settings_path).read_text(encoding="utf-8-sig"))
    if manifest.get("version") != 1 or settings.get("version") != 1:
        raise ValueError("Unsupported configuration version")
    sources = manifest["sources"]
    ids = [s["source_id"] for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source_id in manifest")
    for source in sources:
        if source["role"] not in ROLES or source["supplier_id"] not in {"IEK","SystemElectric"}:
            raise ValueError(f"Unknown role/supplier: {source['source_id']}")
        if not re.fullmatch(r"[0-9a-f]{64}",source["sha256"]) or source["header_row"] < 1:
            raise ValueError(f"Invalid fingerprint/header: {source['source_id']}")
        for key,value in source.get("options",{}).items():
            if key not in ALLOWED_OVERRIDES or value not in ALLOWED_OVERRIDES[key]:
                raise ValueError(f"Unsupported source option: {source['source_id']}.{key}")
        if source.get("duplicate_of") not in [None,*ids]:
            raise ValueError("Unknown duplicate target")
    for sid, overrides in settings.get("source_overrides",{}).items():
        if sid not in ids:
            raise ValueError(f"Unknown override source: {sid}")
        for key,item in overrides.items():
            if key not in ALLOWED_OVERRIDES or item.get("value") not in ALLOWED_OVERRIDES[key]:
                raise ValueError(f"Unsupported override: {sid}.{key}")
            if item.get("status") not in {"assumption","confirmed"} or not item.get("reason") or not item.get("id"):
                raise ValueError("Overrides require an id, status (assumption/confirmed), and reason")
            if item["status"] == "confirmed" and not item.get("evidence"):
                raise ValueError("Confirmed overrides require evidence")
            role=next(s["role"] for s in sources if s["source_id"]==sid)
            if key in {"measure_kind","snapshot_semantics"} and role not in {"monthly_quantity_unspecified","monthly_beginning_inventory"}:
                raise ValueError(f"Monthly override does not apply to {sid}")
            if key=="constraint_kind" and role not in {"order_constraints","monthly_quantity_unspecified"}:
                raise ValueError(f"Constraint override does not apply to {sid}")
    for supplier,policy in settings.get("supplier_policies",{}).items():
        if supplier not in {"IEK","SystemElectric"}:
            raise ValueError(f"Unknown policy supplier: {supplier}")
        for key,item in policy.items():
            if key not in {"lead_time_days","review_period_days","service_level"}:
                raise ValueError(f"Unknown policy parameter: {key}")
            value=item.get("value")
            status=item.get("status")
            if value is None:
                if status != "unresolved":
                    raise ValueError("Null policy values must be unresolved")
            else:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0 < value < float('inf'):
                    raise ValueError("Policy values must be finite and positive")
                if key=="service_level" and value>=1:
                    raise ValueError("Service level must be below 1")
                if status not in {"assumption","confirmed"} or not item.get("reason"):
                    raise ValueError("Policy values require assumption/confirmed status and reason")
                if status=="confirmed" and not item.get("evidence"):
                    raise ValueError("Confirmed policy values require evidence")
    return manifest, settings


def resolve_options(source, settings):
    options=dict(source.get("options",{}))
    assumptions=[]
    for key,item in settings.get("source_overrides",{}).get(source["source_id"],{}).items():
        options[key]=item["value"]
        if item["status"]=="assumption":
            assumptions.append(item["id"])
    return options, assumptions
