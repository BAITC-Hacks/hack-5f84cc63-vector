"""Read-only supplier adapters; physical layout is explicit in the manifest."""
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import re

from openpyxl.utils import get_column_letter

from .config import resolve_options
from .parsing import TOTAL, identifier, is_number, month, number, raw_value, text, timestamp


class SourceLayoutError(ValueError):
    """The workbook no longer matches its reviewed source contract."""


class SupplierAdapter:
    supplier_id = None

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.audit = Counter()
        self.duplicate_groups = []

    def meta(self, source, row, column, table, flags=(), assumptions=()):
        identity=f"{source['source_id']}:{source['sha256']}:{row}:{column}:{table}"
        return {"record_id":hashlib.sha256(identity.encode()).hexdigest(),
            "source_id":source["source_id"], "source_sheet":source["sheet"],
            "source_row":row, "source_column":column, "supplier_id":self.supplier_id,
            "quality_flags":sorted(set(flags) | ({"assumption_applied"} if assumptions else set())),
            "assumption_ids":list(assumptions), "is_synthetic":False}

    def quarantine(self, source, rownum, row, reason, assumptions=()):
        return "quarantine", {**self.meta(source,rownum,None,"quarantine",[reason],assumptions),
            "reason":reason,"raw_values":[raw_value(v) for v in row]}

    def parse(self, source, rows):
        if source["supplier_id"] != self.supplier_id:
            raise SourceLayoutError(f"{self.supplier_id} cannot read {source['supplier_id']}")
        options,assumptions=resolve_options(source,self.settings)
        if source["role"]=="aggregate_seasonality":
            yield from self.parse_aggregate(source,list(rows),assumptions)
            return
        headers=None
        seen_rows={}
        sku_rows=defaultdict(list)
        seen_products=set()
        for rownum,values in enumerate(rows,1):
            row=tuple(values)
            if rownum==source["header_row"]:
                headers=[text(v) for v in row]
                if headers != source["headers"]:
                    raise SourceLayoutError(f"Header mismatch: {source['source_id']}")
            if rownum<source["data_start_row"]:
                self.audit["header_or_preamble_rows"]+=1
                continue
            if headers is None:
                raise SourceLayoutError("Missing header row")
            if not any(text(v) for v in row):
                self.audit["blank_rows"]+=1
                continue
            if len(row)!=len(headers):
                raise SourceLayoutError(f"Row width changed: {source['source_id']} row {rownum}")
            if any(isinstance(v,str) and TOTAL.match(v.strip()) for v in row):
                self.audit["total_rows"]+=1
                for c,v in enumerate(row):
                    if v is not None:
                        yield "source_controls", {**self.meta(source,rownum,get_column_letter(c+1),"source_controls"),
                            "sku_id":None,"source_label":headers[c] or "unlabeled_total_cell","value_raw":raw_value(v)}
                continue
            indexes=source["columns"]
            sku_raw=row[indexes["sku"]]
            sku,flags=identifier(sku_raw)
            if sku is None:
                self.audit["rows_without_valid_sku"]+=1
                yield self.quarantine(source,rownum,row,"invalid_sku",assumptions)
                continue
            self.audit["sku_rows"]+=1
            rowkey=json.dumps([raw_value(v) for v in row],ensure_ascii=False,allow_nan=False)
            if rowkey in seen_rows:
                flags.append("exact_duplicate_row")
                self.audit["exact_duplicate_rows"]+=1
                self.duplicate_groups.append({"source_id":source["source_id"],"sku_id":sku,
                    "first_row":seen_rows[rowkey],"repeated_row":rownum,"kind":"exact_row"})
            else:
                seen_rows[rowkey]=rownum
            if source["role"]!="transactions":
                if sku_rows[sku]:
                    flags.append("repeated_sku_in_source")
                    self.duplicate_groups.append({"source_id":source["source_id"],"sku_id":sku,
                        "first_row":sku_rows[sku][0],"repeated_row":rownum,"kind":"sku"})
                sku_rows[sku].append(rownum)
            attrs={key:text(row[indexes[key]]) or None if key in indexes else None for key in ("name","supplier_article","stock_uom")}
            pkey=(sku,*attrs.values())
            if pkey not in seen_products:
                seen_products.add(pkey)
                yield "product_observations",{**self.meta(source,rownum,get_column_letter(indexes["sku"]+1),"product_observations",flags,assumptions),
                    "sku_id":sku,"sku_raw":raw_value(sku_raw),**attrs}
            role=source["role"]
            if role=="transactions":
                qty,qflags=number(row[indexes["quantity"]])
                occurred=timestamp(row[indexes["date"]])
                tflags=flags+qflags
                if occurred is None:
                    tflags.append("invalid_date")
                if qty is not None and qty<0:
                    tflags.append("negative_quantity_semantics_unresolved")
                yield "transactions",{**self.meta(source,rownum,get_column_letter(indexes["quantity"]+1),"transactions",tflags,assumptions),
                    "sku_id":sku,"sku_raw":raw_value(sku_raw),"occurred_at":occurred,
                    "document_number":text(row[indexes["document_number"]]) or None,
                    "document_text":text(row[indexes["document_text"]]) or None,
                    "warehouse_id":text(row[indexes["warehouse"]]) or None,
                    "qty_raw":qty,"qty_source_value":raw_value(row[indexes["quantity"]]),
                    "stock_uom":attrs["stock_uom"],"customer_id":None,"price":None,"currency":None}
                if qflags or occurred is None:
                    yield self.quarantine(source,rownum,row,"invalid_transaction_fields",assumptions)
            elif role in {"monthly_beginning_inventory","monthly_quantity_unspecified"}:
                kind=options.get("measure_kind","unknown")
                semantics=options.get("snapshot_semantics","unknown")
                for c,h in enumerate(headers):
                    period=month(h)
                    if period:
                        qty,qflags=number(row[c])
                        mflags=flags+qflags+["warehouse_unknown"]
                        if kind=="unknown":
                            mflags.append("measure_semantics_unresolved")
                        if kind=="inventory":
                            year,mon=map(int,period.split("-"))
                            day=1 if semantics=="beginning" else monthrange(year,mon)[1]
                            snapshot=date(year,mon,day).isoformat() if semantics in {"beginning","end"} else None
                            if snapshot is None:
                                mflags.append("snapshot_semantics_unresolved")
                            yield "inventory_snapshots",{**self.meta(source,rownum,get_column_letter(c+1),"inventory_snapshots",mflags,assumptions),
                                "sku_id":sku,"snapshot_date":snapshot,"period_month":period,"snapshot_semantics":semantics,
                                "qty_on_hand":qty,"qty_source_value":raw_value(row[c]),"qty_reserved":None,
                                "qty_available":None,"warehouse_id":None,"stock_uom":attrs["stock_uom"]}
                        else:
                            yield "monthly_measures",{**self.meta(source,rownum,get_column_letter(c+1),"monthly_measures",mflags,assumptions),
                                "sku_id":sku,"month":period,"measure_kind":kind,"qty_raw":qty,
                                "qty_source_value":raw_value(row[c]),"stock_uom":attrs["stock_uom"],"warehouse_id":None,
                                "snapshot_semantics":semantics}
                    elif h.lower()=="итого":
                        yield "source_controls",{**self.meta(source,rownum,get_column_letter(c+1),"source_controls",["total_semantics_not_used_for_stock"],assumptions),
                            "sku_id":sku,"source_label":h,"value_raw":raw_value(row[c])}
                if "constraint" in indexes:
                    yield self.constraint(source,rownum,row,sku,flags,indexes,headers,options,assumptions)
            elif role=="order_constraints":
                result=self.constraint(source,rownum,row,sku,flags,indexes,headers,options,assumptions)
                yield result
                if result[1]["value_status"]!="valid":
                    yield self.quarantine(source,rownum,row,"invalid_order_constraint",assumptions)
            elif role=="dated_shipments":
                for c,h in enumerate(headers):
                    if "поступление до" not in h.lower():
                        continue
                    qty,qflags=number(row[c])
                    arrival=re.search(r"поступление до\s+(\d{2}\.\d{2}\.\d{4})",h,re.I)
                    parsed=timestamp(arrival[1]) if arrival else None
                    shipment=re.search(r"УТ-\d+",h)
                    sflags=flags+qflags+["shipment_uom_unknown"]
                    if parsed is None:
                        sflags.append("arrival_date_unknown")
                    if qty is not None and qty<0:
                        sflags.append("negative_shipment_quantity")
                    yield "shipments",{**self.meta(source,rownum,get_column_letter(c+1),"shipments",sflags,assumptions),
                        "sku_id":sku,"shipment_id":shipment[0] if shipment else None,"qty":qty,
                        "qty_source_value":raw_value(row[c]),"uom":None,"expected_date":parsed[:10] if parsed else None,
                        "date_kind":"deadline" if parsed else "unknown","received_at":None,"source_header":str(row_source_header(source,c))}
        if headers is None:
            raise SourceLayoutError(f"Header not found: {source['source_id']}")

    def constraint(self,source,rownum,row,sku,flags,indexes,headers,options,assumptions):
        c=indexes["constraint"]
        value,qflags=number(row[c])
        kind=options.get("constraint_kind",self.default_constraint_kind())
        status="missing" if "missing_value" in qflags else "invalid" if qflags or (value is not None and value<=0) else "valid"
        flags=list(flags)+qflags
        if kind=="unknown":
            flags.append("constraint_semantics_unresolved")
        if value is not None and value<=0:
            flags.append("nonpositive_constraint")
        flags.append("order_uom_unknown")
        if source.get("external_formula_columns") and c in source["external_formula_columns"]:
            flags.append("external_formula_cached_value")
        return "order_constraints",{**self.meta(source,rownum,get_column_letter(c+1),"order_constraints",flags,assumptions),
            "sku_id":sku,"constraint_value_raw":raw_value(row[c]),"constraint_value":value,
            "source_label":headers[c],"constraint_kind":kind,
            "minimum_order_qty":value if kind=="minimum" and status=="valid" else None,
            "order_multiple":value if kind=="multiple" and status=="valid" else None,
            "order_uom":None,"conversion_to_stock_uom":None,"value_status":status}

    def default_constraint_kind(self):
        return "unknown"

    def parse_aggregate(self,source,rows,assumptions):
        headers=[text(v) for v in rows[source["header_row"]-1]]
        if headers!=source["headers"]:
            raise SourceLayoutError(f"Aggregate header mismatch: {source['source_id']}")
        self.audit["aggregate_sheet_rows"]+=len(rows)
        for r in (4,5,6):
            year=rows[r-1][0]
            if not isinstance(year,int) or not 2000<=year<=2100:
                raise SourceLayoutError(f"Year block changed: {source['source_id']} row {r}")
            for m in range(1,13):
                raw=rows[r-1][m]
                value,flags=number(raw)
                yield "aggregate_measures",{**self.meta(source,r,get_column_letter(m+1),"aggregate_measures",flags+["aggregate_units_unknown"],assumptions),
                    "month":f"{year}-{m:02d}","value":value,"value_raw":raw_value(raw),"metric":"source_aggregate_sales","uom":None}
        for c,reference in [(3,"2024"),(6,"2025"),(9,"2026_partial"),(11,"source_defined_combination")]:
            label=text(rows[9][c])
            if not label:
                continue
            if not ("сезон" in label.lower()):
                raise SourceLayoutError(f"Coefficient header changed: {source['source_id']} col {c+1}")
            for m,r in enumerate(range(11,23),1):
                raw=rows[r-1][c]
                value,flags=number(raw)
                flags += ["coefficient_availability_unknown","portfolio_to_sku_applicability_unresolved"]
                if reference=="2026_partial" and m>=10:
                    flags.append("unobserved_period_formula_zero")
                if reference=="source_defined_combination":
                    flags.append("mixed_reference_period_requires_review")
                yield "source_coefficients",{**self.meta(source,r,get_column_letter(c+1),"source_coefficients",flags,assumptions),
                    "scope":"portfolio","scope_id":self.supplier_id,"coefficient_kind":"seasonality","month_of_year":m,
                    "value":value,"value_raw":raw_value(raw),"reference_period":reference,"available_at":None,
                    "is_source_forecast":True if source.get("combined_future_months_are_forecast") and c==11 and m>=10 else None,
                    "source_label":label}
        if text(rows[14][12]).startswith("Поправка 2026/2025"):
            value,flags=number(rows[14][13])
            yield "source_coefficients",{**self.meta(source,15,"N","source_coefficients",flags+["coefficient_availability_unknown"],assumptions),
                "scope":"portfolio","scope_id":self.supplier_id,"coefficient_kind":"growth_adjustment","month_of_year":None,
                "value":value,"value_raw":raw_value(rows[14][13]),"reference_period":"2026_vs_2025_Jan_Sep",
                "available_at":None,"is_source_forecast":None,"source_label":text(rows[14][12])}


def row_source_header(source,column):
    return source["headers"][column]


class IEKAdapter(SupplierAdapter):
    supplier_id="IEK"


class SystemElectricAdapter(SupplierAdapter):
    supplier_id="SystemElectric"

    def default_constraint_kind(self):
        return "multiple"
