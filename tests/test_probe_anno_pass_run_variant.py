"""The annotation-pass probe's ``_run_variant``, DRIVEN rather than read.

WHY THIS FILE EXISTS. The probe's other tests bind ``_run_variant`` by reading
its source for call names, which this repo has already found wanting: `if
False:` and `None and f(...)` keep every string being asserted on. Round 2
(revised) made the ORDER of the steps load-bearing, and an order is exactly
what a source grep cannot see:

  * V8's own model capture must run BEFORE the white suppression, because the
    model pass paints every model element and restores each to a blank --
    which would wipe white overrides applied first;
  * the annotation pass must see the fiducials in their reserved colours and
    every other model member white;
  * CropBoxVisible must be on for BOTH exports and back off afterwards;
  * and, the point of the round, nothing under an annotation transaction may
    write CropBox or CropBoxActive.

So the real ``_run_variant`` runs here, calling the REAL production passes
against the shared fake Revit DB, and those facts are asserted at the moment
each export happens. The mutations this is wired to are listed on each test.
"""
import json
import os
import types

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.revit.annotation import split_stage_a_pass_membership

from tests.dynamo import probe_stage_a_anno_pass_variants as probe
from tests.stage_a_capture_fakes import (
    FakeDiag, FakeElement, FakeViewPlan, install_fake_revit_db,
)
from tests.stage_a_capture_fakes import FakeCategory
from tests.test_stage_a_annotation_pass_probe_switches import (
    ANNO_CAT, MODEL_CAT, OTHER_MODEL_CAT, VIEW_ID, _BBox, _SizedDoc, _elements,
    _model_pass_elements, _raster,
)

VIEWERS_CAT = FakeCategory("Views", -2000279, cat_type="Annotation")

_STATUS = types.SimpleNamespace(Started="Started", Committed="Committed",
                                RolledBack="RolledBack")
_LOG = []


class _Tx(object):
    """A Transaction that logs its name while open and reports Committed."""

    def __init__(self, doc, name):
        self.name = name

    def Start(self):
        _LOG.append(("tx_start", self.name))
        return _STATUS.Started

    def Commit(self):
        _LOG.append(("tx_end", self.name))
        return _STATUS.Committed

    def RollBack(self):
        _LOG.append(("tx_end", self.name))
        return _STATUS.RolledBack


class _Group(_Tx):
    def RollBack(self):
        _LOG.append(("group_rollback", self.name))
        return _STATUS.RolledBack


def _open_tx():
    """The innermost open transaction's name, or None."""
    stack = []
    for entry in _LOG:
        if entry[0] == "tx_start":
            stack.append(entry[1])
        elif entry[0] == "tx_end" and stack:
            stack.pop()
    return stack[-1] if stack else None


class _Manager(object):
    LeftAnnotationCropOffset = 0.25
    RightAnnotationCropOffset = 0.25
    TopAnnotationCropOffset = 0.25
    BottomAnnotationCropOffset = 0.25
    ShapeSet = False

    def GetCropShape(self):
        return []


class _ProbeView(FakeViewPlan):
    """Logs every crop-property write with the transaction it happened in."""

    def __init__(self, view_id):
        object.__setattr__(self, "_logging", False)
        FakeViewPlan.__init__(self, view_id)
        self.CropBoxVisible = False
        self._manager = _Manager()
        object.__setattr__(self, "_logging", True)

    def __setattr__(self, name, value):
        if name in ("CropBox", "CropBoxActive", "CropBoxVisible") and self._logging:
            _LOG.append(("write", name, value, _open_tx()))
        object.__setattr__(self, name, value)

    def GetCropRegionShapeManager(self):
        return self._manager

    def SetElementOverrides(self, eid, ogs):
        _LOG.append(("override", int(eid.IntegerValue), _open_tx()))
        FakeViewPlan.SetElementOverrides(self, eid, ogs)


def _colour_of(ogs):
    colour = getattr(ogs, "ProjectionLineColor", None)
    if colour is None:
        return None
    return (colour.r, colour.g, colour.b)


def _run(tmp_path, monkeypatch, variant):
    del _LOG[:]
    elements = _elements()
    # Two model elements with bboxes, far apart on BOTH axes, so a fiducial pair
    # exists inside the model crop (20,15)-(80,60).
    elements[0].bbox = _BBox((25, 18, 0), (26, 19, 0))
    elements[1].bbox = _BBox((70, 52, 0), (71, 53, 0))
    # A third model member that is NOT a fiducial, so "every other model member
    # is white" has something to be true of.
    elements.append(FakeElement(1003, MODEL_CAT))
    view = _ProbeView(VIEW_ID)
    # The view's own crop-region element: OST_Viewers, named like the view, and
    # a MODEL member by membership (no owner view).
    elements.append(FakeElement(1004, VIEWERS_CAT, name=view.Name))
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    doc.IsModifiable = False
    diag = FakeDiag()
    exports = []

    def _at_export(opts):
        exports.append({
            "path": opts.FilePath,
            "crop_box_visible": view.CropBoxVisible,
            "overrides": dict((eid, _colour_of(ogs))
                              for eid, ogs in view.element_overrides.items()),
        })

    monkeypatch.setattr(probe, "_force_close_dynamo_transaction", lambda: None)
    with install_fake_revit_db() as fake_db:
        fake_db.Transaction = _Tx
        fake_db.TransactionGroup = _Group
        fake_db.TransactionStatus = _STATUS
        raster = _raster()
        cfg = probe._model_config(str(tmp_path / "model"), 150.0)
        cfg.include_linked_rvt = False
        geom = {}
        model_out = color_id_buffer.export_color_id_buffer_view(
            doc, view, _model_pass_elements(elements), cfg, diag=diag,
            raster=raster, geometry_out=geom)
        assert model_out["success"], model_out.get("failure_reason")
        model_members, anno_members, unresolved, _basis = (
            split_stage_a_pass_membership(elements, capture_view_id_int=VIEW_ID))
        candidates = [{"id": 1001, "rect": (25.0, 18.0, 26.0, 19.0),
                       "category": "Walls"},
                      {"id": 1002, "rect": (70.0, 52.0, 71.0, 53.0),
                       "category": "Floors"}]
        context = {
            "diag": diag, "raster": raster, "geom": geom,
            "model_tiff_path": model_out["tiff_path"],
            "model_tiff_sha256": probe._sha256(model_out["tiff_path"]),
            "white_override_capability": {"state": "value"},
            "model_members": model_members,
            "annotation_members": anno_members,
            "unresolved_count": len(unresolved),
            "link_categories": [],
            "link_visibility": {"state": "value", "instances": []},
            "authored_model_overrides": {"state": "value"},
            "elements": _model_pass_elements(elements),
            "model_pass_ms": 10.0,
            "authored_crop": probe.crop_region_record(view, raster.view_basis),
            "fiducial_choice": probe.choose_fiducial_pair(
                candidates, (20.0, 15.0, 80.0, 60.0), 0.05),
            "crop_region_elements": probe.crop_region_elements(
                doc, view, model_members),
        }
        del _LOG[:]
        doc.on_export_image = _at_export
        report = probe._run_variant(
            doc, view, variant, context,
            {"probe_dir": str(tmp_path / "probe"), "export_dpi": 150.0})
    return report, view, exports, doc


def _anno_crop_writes():
    return [entry for entry in _LOG if entry[0] == "write"
            and entry[1] in ("CropBox", "CropBoxActive")
            and entry[3] is not None and "ANNO" in entry[3]]


def test_v8_runs_its_own_model_capture_BEFORE_the_white_suppression(tmp_path, monkeypatch):
    """Mutation: move ``_own_model_pass`` after the pre-state transaction. The
    model pass then blanks the white overrides and the annotation export sees
    model members unsuppressed."""
    report, view, exports, doc = _run(tmp_path, monkeypatch, probe.V8)
    assert len(exports) == 2, exports
    model_export, anno_export = exports
    assert os.sep + "model" + os.sep in model_export["path"]
    # No WHITE override existed when the model pass exported.
    assert (255, 255, 255) not in model_export["overrides"].values()
    # At the annotation export: fiducials reserved, every other model member white.
    overrides = anno_export["overrides"]
    assert overrides[1001] == probe.FIDUCIAL_COLOURS[0]
    assert overrides[1002] == probe.FIDUCIAL_COLOURS[1]
    assert overrides[1003] == (255, 255, 255)
    assert report["own_model_pass"]["success"] is True
    assert report["own_model_pass"]["geometry_matches_shared"] is True


def test_v8_has_the_crop_boundary_visible_in_BOTH_exports_and_puts_it_back(
        tmp_path, monkeypatch):
    """Mutation: drop the CropBoxVisible write, or its restore."""
    report, view, exports, doc = _run(tmp_path, monkeypatch, probe.V8)
    assert [e["crop_box_visible"] for e in exports] == [True, True]
    assert view.CropBoxVisible is False
    verdict = report["restore"]["after_explicit_restore"]
    assert verdict["crop_box_visible"]["status"] == "restored"
    assert verdict["overall"] == "restored", verdict


def test_v7_and_v8_never_write_the_crop_inside_the_annotation_pass(tmp_path, monkeypatch):
    """THE ROUND'S POINT, asserted where it happens. Mutation: set
    ``color_id_buffer_anno_crop_mode`` from anything but the plan."""
    for variant in (probe.V7, probe.V8):
        _run(tmp_path / variant, monkeypatch, variant)
        assert _anno_crop_writes() == [], variant


def test_the_v0_control_still_writes_the_crop_so_the_check_above_can_fail(
        tmp_path, monkeypatch):
    """The CONTROL. Without it the assertion above would also pass against a
    log that never recorded crop writes at all."""
    _run(tmp_path, monkeypatch, probe.V0)
    assert _anno_crop_writes(), "the shipped pass writes frame B as the crop"


def test_v8_concludes_RAN_and_measured_every_request(tmp_path, monkeypatch):
    report, view, exports, doc = _run(tmp_path, monkeypatch, probe.V8)
    assert report["measurement"]["measured"] is True, report["measurement"]
    assert report["conclusion"] == "RAN", (report["conclusion"], report["exceptions"])
    assert report["annotation_pass"]["crop_mode"] == "untouched"
    assert report["document_safe"] is True


def test_v8_writes_the_boundary_and_fiducials_into_both_sidecars(tmp_path, monkeypatch):
    """A consumer reading the capture alone must learn the boundary is present
    and must be subtracted. Mutation: drop ``_annotate_variant_sidecars``."""
    report, view, exports, doc = _run(tmp_path, monkeypatch, probe.V8)
    assert all(v is None for v in report["sidecar_annotations"].values()), (
        report["sidecar_annotations"])
    with open(report["annotation_pass"]["sidecar_path"]) as handle:
        anno = json.load(handle)
    assert anno["probe_crop_boundary"]["must_be_subtracted"] is True
    assert anno["probe_crop_boundary"]["crop_box_visible_during_capture"] is True
    assert [f["id"] for f in anno["probe_fiducials"]["fiducials"]] == [1001, 1002]
    with open(report["own_model_pass"]["sidecar_path"]) as handle:
        model = json.load(handle)
    assert model["probe_crop_boundary"]["pass"] == "model"
    assert model["probe_crop_boundary"]["drawn_at_uv"] == (
        report["own_model_pass"]["boundary_drawn_at_uv"])


def test_every_model_override_is_blank_after_the_variant(tmp_path, monkeypatch):
    """The fiducials are model members, so the one blank loop reverses them.
    Mutation: drop the blank loop."""
    report, view, exports, doc = _run(tmp_path, monkeypatch, probe.V8)
    for eid in (1001, 1002, 1003):
        assert _colour_of(view.element_overrides[eid]) is None, eid


def test_v8_leaves_the_crop_region_element_untouched_and_v7_whitens_it(
        tmp_path, monkeypatch):
    """Round 2 found the boundary in V0 and in neither V7 nor V8 -- the shape
    of membership suppression whitening F1's own ruler. V8 now leaves the
    view's crop-region element alone, in BOTH directions (no white override,
    no blank restore write); V7 is the control that still whitens it.
    Mutation: drop exclude_ids at the call site, or the restore-loop skip."""
    report, view, exports, doc = _run(tmp_path / "v8", monkeypatch, probe.V8)
    assert [e for e in _LOG if e[0] == "override" and e[1] == 1004] == []
    assert exports[-1]["overrides"].get(1004) is None
    suppression = report["pre_state"]["white_membership_suppression"]
    assert suppression["excluded_element_ids"] == [1004]
    assert report["conclusion"] == "RAN", report["exceptions"]

    report7, view7, exports7, _doc = _run(tmp_path / "v7", monkeypatch, probe.V7)
    assert exports7[-1]["overrides"].get(1004) == (255, 255, 255)
    assert report7["pre_state"]["white_membership_suppression"][
        "excluded_element_ids"] == []
