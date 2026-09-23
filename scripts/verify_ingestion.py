"""Verify canonical artifacts against contracts, source hashes, and EDA profiles."""
from collections import Counter, defaultdict
import argparse
import json
import math
from pathlib import Path

from vector_pipeline.config import resolve_options, sha256
from vector_pipeline.schema import SCHEMAS, validate


def verify(directory, eda_path=Path("data/processed/eda_summary.json")):
    directory=Path(directory)
    report=json.loads((directory/"ingestion_summary.json").read_text(encoding="utf-8"))
    manifest=json.loads((directory/"source_manifest.json").read_text(encoding="utf-8"))
    eda=json.loads(Path(eda_path).read_text(encoding="utf-8"))
    profiles={(f["path"],s["sheet"]):(f,s) for f in eda["files"] for s in f["sheets"]}
    sources={s["source_id"]:s for s in manifest["sources"]}
    products=set()
    referenced_products=set()
    observed=defaultdict(Counter)
    counts=defaultdict(Counter)
    checks=0
    for table in SCHEMAS:
        path=directory/f"{table}.jsonl"
        expected=report["tables"][path.name]
        assert sha256(path)==expected["sha256"],f"Artifact hash changed: {path.name}"
        ids=set()
        row_count=0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                record=json.loads(line)
                validate(table,record)
                row_count+=1
                if table=="products":
                    key=(record["supplier_id"],record["sku_id"])
                    assert key not in products,"Duplicate canonical product"
                    products.add(key)
                    continue
                assert record["record_id"] not in ids,f"Duplicate record ID in {table}"
                ids.add(record["record_id"])
                sid=record["source_id"]
                assert sid in sources,"Unknown provenance source"
                assert record["source_sheet"]==sources[sid]["sheet"]
                assert record["supplier_id"]==sources[sid]["supplier_id"]
                counts[sid][table]+=1
                if record.get("sku_id"):
                    referenced_products.add((record["supplier_id"],record["sku_id"]))
                field={"transactions":"qty_raw","monthly_measures":"qty_raw","inventory_snapshots":"qty_on_hand",
                    "shipments":"qty","order_constraints":"constraint_value"}.get(table)
                if field:
                    key=(sid,table,record["source_column"])
                    value=record[field]
                    observed[key]["rows"]+=1
                    if value is None:
                        observed[key]["null_or_invalid"]+=1
                    else:
                        observed[key]["numeric"]+=1
                        observed[key]["sum"]+=value
                        observed[key]["negative"]+=value<0
                        observed[key]["zero"]+=value==0
                        observed[key]["positive"]+=value>0
        assert row_count==expected["rows"],f"Row count mismatch: {table}"
        checks+=1
    assert referenced_products<=products,"Foreign key references missing products"
    for sid,source in sources.items():
        f,p=profiles[(source["path"],source["sheet"])]
        assert f["sha256"]==source["sha256"]==sha256(source["path"]),f"Source changed: {sid}"
        if source.get("duplicate_of"):
            assert not counts[sid],"Identical source was ingested twice"
            continue
        assert counts[sid],f"Source produced no canonical records: {sid}"
        role=source["role"]
        options,_=resolve_options(source,report["effective_settings"])
        if role=="transactions":
            table="transactions"
            selected=[p["columns"][source["columns"]["quantity"]]]
        elif role in {"monthly_quantity_unspecified","monthly_beginning_inventory"}:
            table="inventory_snapshots" if options.get("measure_kind")=="inventory" else "monthly_measures"
            selected=[c for c in p["columns"] if c["column"] in p["month_columns"]]
        elif role=="order_constraints":
            table="order_constraints"
            selected=[p["columns"][source["columns"]["constraint"]]]
        elif role=="dated_shipments":
            table="shipments"
            columns={c["column"] for c in p["shipment_columns"]}
            selected=[c for c in p["columns"] if c["column"] in columns]
        else:
            continue
        assert counts[sid][table]==p["data_rows"]*len(selected),f"Unexpected normalized size: {sid}"
        for col in selected:
            obs=observed[(sid,table,col["column"])]
            num=col.get("numeric",{})
            assert obs["rows"]==col["rows_profiled"]
            assert obs["numeric"]==num.get("count",0)
            assert obs["null_or_invalid"]==col["rows_profiled"]-num.get("count",0)
            for sign in ("negative","positive","zero"):
                assert obs[sign]==num.get(sign,0),f"Sign mismatch: {sid}/{col['column']}"
            assert math.isclose(obs["sum"],num.get("sum",0),rel_tol=1e-12,abs_tol=1e-6),f"Sum mismatch: {sid}/{col['column']}"
            checks+=1
    result={"status":"passed","table_and_column_checks":checks,"product_keys":len(products),
        "foreign_keys_valid":True,"source_hashes_unchanged":True,"duplicate_file_excluded":True}
    (directory/"verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.directory),indent=2))
