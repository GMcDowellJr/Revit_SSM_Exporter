"""D2: an import's Stage A pass is decided by OwnerViewId alone, by ONE helper.

Greg (2026-09-30): an ImportInstance whose OwnerViewId is this view is
annotation; anything else -- model-placed, or an OwnerViewId that cannot be
read -- is model. Both passes decide through collection_policy.import_pass(),
so no import is claimed twice or by neither.

Driven through BOTH real passes on the shared fake Revit; asserted on the two
sidecar FILES' color_assignment_map (CLAUDE.md, defect class 4).

The discriminating case is the last: ViewSpecific True, OwnerViewId raising.
On 716ce56 the model pass dropped it (ViewSpecific said annotation) and the
annotation split left it unresolved (no owner) -- painted by NEITHER pass.
"""
import json

import vop_interwoven.revit.collection as revit_collection

from tests.stage_a_capture_fakes import FakeCategory, FakeElement, FakeElementId
from tests.test_stage_a_annotation_pass import VIEW_ID, _elements, _run_both_passes

OWNED, PLACED, UNREADABLE = 5001, 5002, 5003


class ImportInstance(FakeElement):
    """Named as the Revit type is: production matches it by type NAME."""

    def __init__(self, elem_id, view_specific, owner_view_id=None):
        # Not CategoryType "Model": the harness's _model_pass_elements would
        # then read OwnerViewId outside production. Imports reach the model
        # pass through DWG expansion (monkeypatched below), as in production.
        FakeElement.__init__(self, elem_id, FakeCategory("site.dwg", 70, cat_type="Import"),
                             owner_view_id=owner_view_id)
        self.ViewSpecific = view_specific


class _OwnerUnreadable(ImportInstance):
    @property
    def OwnerViewId(self):
        raise RuntimeError("OwnerViewId unavailable")

    @OwnerViewId.setter
    def OwnerViewId(self, _value):
        pass


_OwnerUnreadable.__name__ = "ImportInstance"


def _both(tmp_path, monkeypatch, imports):
    def _expand(doc_, view_, elems, cfg, diag=None, elem_cache=None,
                dwg_omitted_out=None):
        return ([{"element": e, "source_type": "HOST"} for e in elems]
                + [{"element": i, "source_type": "DWG"} for i in imports])
    monkeypatch.setattr(revit_collection, "expand_host_link_import_model_elements",
                        _expand)
    model, anno, _g, _doc, _v, diag = _run_both_passes(
        tmp_path, elements=_elements() + list(imports))
    maps = []
    for result in (model, anno):
        with open(result["sidecar_path"]) as handle:
            maps.append(set(int(k) for k in json.load(handle)["color_assignment_map"]))
    with open(model["sidecar_path"]) as handle:
        records = dict((r["element_id"], r)
                       for r in json.load(handle)["view_specific_imports"])
    return maps[0], maps[1], records, model, anno


def test_d2_an_owned_import_is_only_in_the_annotation_map(tmp_path, monkeypatch):
    model, anno, records, _m, _a = _both(
        tmp_path, monkeypatch, [ImportInstance(OWNED, True, owner_view_id=VIEW_ID)])
    assert OWNED in anno and OWNED not in model
    assert records[OWNED]["classification"] == "annotation"
    assert records[OWNED]["import_pass"]["owner_view_id"] == VIEW_ID


def test_d2_a_model_placed_import_is_only_in_the_model_map(tmp_path, monkeypatch):
    model, anno, records, _m, _a = _both(
        tmp_path, monkeypatch, [ImportInstance(PLACED, False)])
    assert PLACED in model and PLACED not in anno
    assert records[PLACED]["classification"] == "model"


def test_d2_an_unreadable_owner_is_only_in_the_model_map_never_twice(tmp_path, monkeypatch):
    """ViewSpecific True, so the OLD predicate called it annotation: red on
    716ce56, where it was in neither map."""
    model, anno, records, _m, anno_result = _both(
        tmp_path, monkeypatch, [_OwnerUnreadable(UNREADABLE, True)])
    assert UNREADABLE in model
    assert UNREADABLE not in anno
    record = records[UNREADABLE]
    assert record["classification"] == "model"
    assert record["import_pass"]["state"] == "unavailable"
    assert "RuntimeError" in record["import_pass"]["reason"]
    # Recorded as a diagnostic, deciding nothing.
    assert record["view_specific"] == {"state": "value", "value": True}
    with open(anno_result["sidecar_path"]) as handle:
        membership = json.load(handle)["membership"]
    assert membership["unresolved_count"] == 0
    # Placed in the model pass, but NOT reported as a clean read (Codex,
    # PR #222): its own basis, and the failed read with its reason.
    assert membership["basis_counts"]["import_owner_unreadable"] == 1
    assert membership["basis_counts"]["import_not_owned_by_view"] == 0
    [entry] = membership["import_owner_unreadable"]
    assert entry["element_id"] == UNREADABLE
    assert entry["state"] == "unavailable"
    assert "RuntimeError" in entry["reason"]


def test_d2_control_a_readable_import_is_not_listed_as_unreadable(tmp_path, monkeypatch):
    _m, _a, _r, _model, anno_result = _both(
        tmp_path, monkeypatch, [ImportInstance(OWNED, True, owner_view_id=VIEW_ID),
                                ImportInstance(PLACED, False)])
    with open(anno_result["sidecar_path"]) as handle:
        membership = json.load(handle)["membership"]
    assert membership["import_owner_unreadable"] == []
    assert membership["basis_counts"]["import_owner_unreadable"] == 0


def test_d2_one_predicate_all_three_together(tmp_path, monkeypatch):
    """Every import lands in exactly one map."""
    imports = [ImportInstance(OWNED, True, owner_view_id=VIEW_ID),
               ImportInstance(PLACED, False), _OwnerUnreadable(UNREADABLE, True)]
    model, anno, _r, _m, _a = _both(tmp_path, monkeypatch, imports)
    for eid in (OWNED, PLACED, UNREADABLE):
        assert (eid in model) != (eid in anno), eid


def test_d2_import_pass_is_owner_view_only():
    from vop_interwoven.revit.collection_policy import import_pass
    assert import_pass(FakeElement(1), VIEW_ID)["state"] == "not_applicable"
    # ViewSpecific does not enter into it, either way.
    assert import_pass(ImportInstance(1, False, owner_view_id=VIEW_ID),
                       VIEW_ID)["pass"] == "annotation"
    assert import_pass(ImportInstance(1, True), VIEW_ID)["pass"] == "model"
    other = import_pass(ImportInstance(1, True, owner_view_id=88), VIEW_ID)
    assert other == {"state": "value", "owner_view_id": 88, "pass": "model"}
    broken = import_pass(_OwnerUnreadable(1, True), VIEW_ID)
    assert broken["state"] == "unavailable" and broken["pass"] == "model"
    assert import_pass(ImportInstance(1, True, owner_view_id=VIEW_ID),
                       None)["pass"] == "model"
