"""The annotation sidecar records each element's API class, in the FILE.

Filled and masking regions are Detail Items, like detail components, so the
analysis cannot tell them apart by category -- and it counts a filled region
by AREA (it masks the model) where everything else counts by ink
(docs/DESIGN_ANALYSIS_GRID.md, item 3). The class is the Python type name of
the object already in hand: no Revit read.
"""
import json

from tests.stage_a_capture_fakes import FakeCategory, FakeElement
from tests.test_stage_a_annotation_pass import (
    VIEW_ID, _BBox, _elements, _run_both_passes)

DETAIL = FakeCategory("Detail Items", -2002000, cat_type="Annotation")


class FilledRegion(FakeElement):
    """Named as the Revit type is."""


def test_the_FILE_records_a_filled_regions_class(tmp_path):
    region = FilledRegion(2301, DETAIL, owner_view_id=VIEW_ID,
                          bbox=_BBox((40, 40, 0), (44, 43, 0)))
    _m, anno, _g, _d, _v, _diag = _run_both_passes(
        tmp_path, elements=_elements() + [region])
    with open(anno["sidecar_path"]) as handle:
        bbox_map = json.load(handle)["annotation_bbox_map"]
    assert bbox_map["2301"]["element_class"] == "FilledRegion"
    assert bbox_map["2301"]["category"] == "Detail Items"
    # Control: an ordinary annotation element records its own class.
    assert bbox_map["2001"]["element_class"] == "FakeElement"
