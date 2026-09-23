"""Behavioral tests for supplier parsing and canonical contracts."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vector_pipeline.adapters import IEKAdapter, SystemElectricAdapter, SourceLayoutError
from vector_pipeline.config import load_config
from vector_pipeline.pipeline import CanonicalWriter
from vector_pipeline.schema import validate


MANIFEST=json.loads(Path("config/sources.json").read_text(encoding="utf-8"))


def source(sid):
    return deepcopy(next(s for s in MANIFEST["sources"] if s["source_id"]==sid))


def parse(adapter, spec, data_rows):
    rows=[spec["headers"]]+[[None]*len(spec["headers"]) for _ in range(spec["data_start_row"]-2)]+data_rows
    records=list(adapter.parse(spec,rows))
    for table,record in records:
        validate(table,record)
    return records


class AdapterTests(unittest.TestCase):
    def test_negative_missing_and_total_transactions(self):
        spec=source("iek_transactions")
        base=["22.09.2026 10:30:00","INV1","source document","001_","Lamp","шт","Алматы",-5]
        missing=base.copy(); missing[1]="INV2"; missing[7]=None
        total=["Итого",None,None,None,None,None,None,900000]
        records=parse(IEKAdapter(),spec,[base,missing,total])
        tx=[r for t,r in records if t=="transactions"]
        self.assertEqual([r["qty_raw"] for r in tx],[-5,None])
        self.assertEqual(tx[0]["sku_id"],"001_")
        self.assertIn("negative_quantity_semantics_unresolved",tx[0]["quality_flags"])
        self.assertEqual(tx[1]["source_row"],3)
        self.assertEqual(len([r for t,r in records if t=="quarantine"]),1)
        self.assertTrue(any(t=="source_controls" for t,r in records))

    def test_inventory_does_not_use_total_as_current_stock_or_blank_as_zero(self):
        spec=source("iek_inventory")
        row=[None]*len(spec["headers"])
        row[:4]=["Lamp","шт","001_",10]
        row[-1]=999
        records=parse(IEKAdapter(),spec,[row])
        inv=[r for t,r in records if t=="inventory_snapshots"]
        self.assertEqual(len(inv),33)
        self.assertEqual(inv[0]["snapshot_date"],"2024-01-01")
        self.assertEqual(inv[0]["qty_on_hand"],10)
        self.assertIsNone(inv[1]["qty_on_hand"])
        self.assertTrue(all(r["qty_available"] is None and r["warehouse_id"] is None for r in inv))
        self.assertFalse(any(r["qty_on_hand"]==999 for r in inv))

    def test_systemelectric_unknown_months_stay_unknown(self):
        spec=source("se_monthly_unspecified")
        row=[None]*len(spec["headers"]); row[:5]=[1,"Lamp","001_","шт",0]
        values=[r for t,r in parse(SystemElectricAdapter(),spec,[row]) if t=="monthly_measures"]
        self.assertEqual(values[0]["qty_raw"],0)
        self.assertIsNone(values[1]["qty_raw"])
        self.assertTrue(all(r["measure_kind"]=="unknown" for r in values))

    def test_assumed_inventory_semantics_are_traceable(self):
        spec=source("se_monthly_unspecified")
        settings={"source_overrides":{spec["source_id"]:{
            "measure_kind":{"value":"inventory","status":"assumption","id":"A1","reason":"Synthetic test scenario"},
            "snapshot_semantics":{"value":"end","status":"assumption","id":"A2","reason":"Synthetic test scenario"}}}}
        row=[None]*len(spec["headers"]); row[:5]=[1,"Lamp","001_","шт",0]
        values=[r for t,r in parse(SystemElectricAdapter(settings),spec,[row]) if t=="inventory_snapshots"]
        self.assertEqual(values[1]["snapshot_date"],"2024-02-29")
        self.assertEqual(values[0]["assumption_ids"],["A1","A2"])
        self.assertIn("assumption_applied",values[0]["quality_flags"])

    def test_moq_error_and_iek_semantics_are_not_defaulted(self):
        spec=source("iek_constraints")
        values=[r for t,r in parse(IEKAdapter(),spec,[[1,"001_","A1","Lamp","#N/A"],[2,"002_","A2","Lamp",6]]) if t=="order_constraints"]
        self.assertEqual(values[0]["value_status"],"invalid")
        self.assertIsNone(values[0]["order_multiple"])
        self.assertEqual(values[0]["constraint_value_raw"],"#N/A")
        self.assertEqual(values[1]["constraint_value"],6)
        self.assertIsNone(values[1]["minimum_order_qty"])
        self.assertIsNone(values[1]["order_multiple"])

    def test_se_zero_multiple_is_invalid(self):
        spec=source("se_constraints")
        result=[r for t,r in parse(SystemElectricAdapter(),spec,[[1,"Lamp","001_","A1",0]]) if t=="order_constraints"][0]
        self.assertEqual(result["value_status"],"invalid")
        self.assertIsNone(result["order_multiple"])

    def test_shipments_preserve_deadlines_and_repeated_rows(self):
        spec=source("iek_shipments")
        row=["001_","A1","Lamp",7,None,None,None,None,None]
        adapter=IEKAdapter()
        values=[r for t,r in parse(adapter,spec,[row,row]) if t=="shipments"]
        self.assertEqual(len(values),12)
        self.assertEqual(values[0]["expected_date"],"2026-10-10")
        self.assertEqual(values[0]["date_kind"],"deadline")
        self.assertIsNone(values[1]["qty"])
        self.assertIn("exact_duplicate_row",values[6]["quality_flags"])
        self.assertEqual(adapter.audit["exact_duplicate_rows"],1)

    def test_header_drift_and_wrong_supplier_fail(self):
        spec=source("iek_transactions")
        with self.assertRaises(SourceLayoutError):
            list(IEKAdapter().parse(spec,[["Wrong header"]]))
        with self.assertRaises(SourceLayoutError):
            list(SystemElectricAdapter().parse(spec,[spec["headers"]]))

    def test_conflicting_catalog_attributes_are_not_silently_chosen(self):
        spec=source("se_constraints")
        rows=[[1,"Lamp A","001_","A1",1],[2,"Lamp B","001_","A1",0]]
        with TemporaryDirectory() as tmp:
            writer=CanonicalWriter(Path(tmp))
            for table,record in parse(SystemElectricAdapter(),spec,rows):
                writer.write(table,record)
            writer.finish_products(); writer.close()
            product=json.loads((Path(tmp)/"products.jsonl").read_text(encoding="utf-8"))
            self.assertIsNone(product["name"])
            self.assertEqual(product["attribute_candidates"]["name"],["Lamp A","Lamp B"])
            self.assertIn("constraint_source_conflict",product["quality_flags"])

    def test_overrides_need_reason_and_status(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"settings.json"
            p.write_text(json.dumps({"version":1,"source_overrides":{"se_monthly_unspecified":{"measure_kind":{"value":"inventory"}}}}))
            with self.assertRaises(ValueError):
                load_config("config/sources.json",p)

    def test_schema_rejects_invalid_quantity_type(self):
        spec=source("se_constraints")
        rec=[r for t,r in parse(SystemElectricAdapter(),spec,[[1,"Lamp","001_","A1",5]]) if t=="order_constraints"][0]
        rec["order_multiple"]="5"
        with self.assertRaises(ValueError):
            validate("order_constraints",rec)


if __name__=="__main__":
    unittest.main()
