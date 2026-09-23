"""Input snapshot guard tests; source fixtures are bytes, not Excel workbooks."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from vector_pipeline.config import sha256
from vector_pipeline.pipeline import run
from vector_pipeline.adapters import SourceLayoutError


class PipelineGuardTests(unittest.TestCase):
    def fixture(self,root):
        (root/"data").mkdir()
        (root/"config").mkdir()
        raw=root/"data"/"fixture.xlsx"
        raw.write_bytes(b"not a workbook; validation must reject before parsing")
        spec={"source_id":"test","supplier_id":"IEK","path":"data/fixture.xlsx","sha256":sha256(raw),
            "sheet":"Sheet1","role":"transactions","header_row":1,"data_start_row":2,"headers":[],"columns":{},"options":{}}
        (root/"config"/"ingestion.json").write_text('{"version":1}')
        return spec,raw

    def save(self,root,sources):
        (root/"config"/"sources.json").write_text(json.dumps({"version":1,"sources":sources}))

    def test_changed_source_is_rejected_before_any_output(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); spec,raw=self.fixture(root)
            self.save(root,[spec]); raw.write_bytes(b"replaced content")
            with self.assertRaises(SourceLayoutError):
                run(root=root)
            self.assertFalse((root/"data"/"processed").exists())

    def test_undeclared_identical_source_cannot_double_count(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); spec,_=self.fixture(root)
            second=deepcopy(spec); second["source_id"]="duplicate"
            self.save(root,[spec,second])
            with self.assertRaisesRegex(ValueError,"duplicate_of"):
                run(root=root)

    def test_unregistered_file_is_not_silently_ignored(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); spec,_=self.fixture(root)
            self.save(root,[spec]); (root/"data"/"new.xlsx").write_bytes(b"new source")
            with self.assertRaisesRegex(SourceLayoutError,"registry"):
                run(root=root)


if __name__=="__main__":
    unittest.main()
