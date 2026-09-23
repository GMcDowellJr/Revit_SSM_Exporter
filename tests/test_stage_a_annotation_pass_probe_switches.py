"""The two PROBE-ONLY switches on the Stage A annotation pass.

WHY THESE TESTS EXIST. Both switches are read with ``getattr`` off the cfg
object and are deliberately absent from ``Config``, so nothing about them is
reachable from a production run and nothing in the existing 1442 tests sets
them. That is exactly the shape of a feature that quietly stops working: the
suite stays green whichever way the switches behave, because the suite never
asks. So every behaviour each switch is supposed to have is asserted here, in
BOTH positions, together with a CONTROL pinning that the default position is
the shipped one -- otherwise a switch wired backwards would pass every
assertion about its "on" state while breaking every capture that never set it.

The mutation these are wired to: make ``suppress_model_categories_here`` or
``anno_smooth_edges_off`` unconditionally True or unconditionally False in
``vop_interwoven/color_id_buffer.py``. Each of those turns a test here red.
"""
import math
import os
import struct
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven.config import Config
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import ViewBasis

from tests.stage_a_capture_fakes import (
    FakeCategory,
    FakeDiag,
    FakeDoc,
    FakeElement,
    FakeElementId,
    FakeViewPlan,
    install_fake_revit_db,
)

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0),
                        forward=(0, 0, -1))
VIEW_ID = 77
FRAME_BOUNDS = Bounds2D(0.0, 0.0, 120.0, 90.0)
MODEL_BOUNDS = Bounds2D(20.0, 15.0, 80.0, 60.0)
CELL = 1.0

MODEL_CAT = FakeCategory("Walls", 10, cat_type="Model")
OTHER_MODEL_CAT = FakeCategory("Floors", 11, cat_type="Model")
ANNO_CAT = FakeCategory("Door Tags", -2000460, cat_type="Annotation")


def _write_tiff_header(path, width, height):
    entries = [(256, 3, 1, width), (257, 3, 1, height)]
    header = struct.pack("<2sHI", b"II", 42, 8)
    body = struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        body += struct.pack("<HHIHH", tag, typ, count, value, 0)
    body += struct.pack("<I", 0)
    with open(path, "wb") as handle:
        handle.write(header + body)


class _SizedDoc(FakeDoc):
    def __init__(self, **kw):
        FakeDoc.__init__(self, **kw)
        self.exported = []

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        if self.on_export_image is not None:
            self.on_export_image(opts)
        px = int(opts.PixelSize)
        out_dir = os.path.dirname(opts.FilePath)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        _write_tiff_header(opts.FilePath + ".tiff", px, px)
        self.exported.append({"pixel_size": px, "path": opts.FilePath})


class _Pt(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _BBox(object):
    def __init__(self, mn, mx):
        self.Min = _Pt(*mn)
        self.Max = _Pt(*mx)
        self.Transform = None


def _raster():
    return types.SimpleNamespace(
        W=max(1, int(math.ceil(FRAME_BOUNDS.width() / CELL))),
        H=max(1, int(math.ceil(FRAME_BOUNDS.height() / CELL))),
        cell_size_ft=CELL, bounds_xy=FRAME_BOUNDS,
        model_clip_bounds=MODEL_BOUNDS, anno_frame_bounds=None,
        anno_cap_envelope_applied=False, view_basis=_PLAN_BASIS,
    )


def _elements():
    return [
        FakeElement(1001, MODEL_CAT),
        FakeElement(1002, OTHER_MODEL_CAT),
        FakeElement(2001, ANNO_CAT, owner_view_id=VIEW_ID,
                    bbox=_BBox((25, 20, 0), (31, 23, 0))),
        FakeElement(2002, ANNO_CAT, owner_view_id=VIEW_ID,
                    bbox=_BBox((40, 30, 0), (47, 34, 0))),
    ]


def _model_pass_elements(elements):
    return [e for e in elements
            if getattr(e.Category, "CategoryType", None) == "Model"
            and int(e.OwnerViewId.IntegerValue) == -1]


def _run(tmp_path, suppression=None, smooth_edges_off=None,
         view_filters=None, smooth_edges_initial=False):
    """Run the model pass, then the annotation pass on ITS geometry.

    ``suppression``/``smooth_edges_off`` are set as ATTRIBUTES, the same way a
    probe sets them, because that is the only way production can be reached:
    neither is a Config parameter. Passing None leaves the attribute unset,
    which is the DEFAULT position and the control.
    """
    elements = _elements()
    view = FakeViewPlan(view_id=VIEW_ID)
    view._smooth_edges = smooth_edges_initial
    for filter_id in view_filters or []:
        view.filters.append(FakeElementId(filter_id))
        view.filter_enabled[filter_id] = True
        view.filter_visibility[filter_id] = True
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    if suppression is not None:
        cfg.color_id_buffer_anno_model_suppression = suppression
    if smooth_edges_off is not None:
        cfg.color_id_buffer_anno_smooth_edges_off = smooth_edges_off
    diag = FakeDiag()
    geom = {}
    # STATE AT EXPORT TIME. Everything the annotation pass changes it also
    # restores, so a post-hoc read of the view cannot tell "never touched it"
    # from "turned it off and back on" -- and for a view filter those are the
    # opposite captures. What the export SAW is the only discriminating
    # observable.
    at_export = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        # The model pass writes and restores category visibility too, so the
        # annotation pass's own calls are the ones AFTER this point.
        view.set_category_hidden_calls = []
        view._smooth_edges = smooth_edges_initial

        def _observe(_opts):
            at_export["filter_enabled"] = dict(view.filter_enabled)
            at_export["category_hidden"] = dict(view.category_hidden)
            at_export["smooth_edges"] = view._smooth_edges

        doc.on_export_image = _observe
        anno = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)
    return anno, doc, view, diag, at_export


# ======================================================================
# CONTROL: the default position is the shipped one
# ======================================================================

def test_control_by_default_the_pass_hides_model_categories(tmp_path):
    """The control. Every "external" assertion below would also pass against a
    switch stuck in the "external" position; this is what says the DEFAULT
    still hides."""
    anno, doc, view, diag, at_export = _run(tmp_path)
    hidden_true = [call for call in view.set_category_hidden_calls if call[1] is True]
    assert hidden_true, "the shipped annotation pass must hide model categories"
    # And they were still hidden WHEN THE EXPORT RAN, not merely toggled.
    assert any(at_export["category_hidden"].get(cat_id) is True
               for cat_id, _value in hidden_true)
    assert anno["metadata"]["model_suppression_mode"] == "hide_categories"


def test_control_by_default_the_pass_disables_the_views_filters(tmp_path):
    """The shipped pass turns the view's filters OFF for the export.

    Asserted at EXPORT TIME. The first version of this test read
    ``view.filter_enabled`` afterwards and passed whichever way the switch
    behaved, because the restore puts the filter back either way -- a test
    that could not fail, found by mutating production rather than by reading
    it.
    """
    anno, doc, view, diag, at_export = _run(tmp_path, view_filters=[901, 902])
    assert set(anno["metadata"]["filter_state"]) == {901, 902}
    assert at_export["filter_enabled"][901] is False
    assert at_export["filter_enabled"][902] is False
    # And restored afterwards.
    assert view.filter_enabled[901] is True
    assert view.filter_enabled[902] is True


def test_control_by_default_the_pass_does_not_touch_smooth_edges(tmp_path):
    anno, doc, view, diag, at_export = _run(tmp_path, smooth_edges_initial=True)
    assert anno["metadata"]["applied_smooth_edges"] == "not_attempted"
    assert anno["metadata"]["smooth_edges_read_error"] is None
    # Still ON when the export ran -- which is finding F3 exactly -- and left
    # as found afterwards.
    assert at_export["smooth_edges"] is True
    assert view._smooth_edges is True


# ======================================================================
# color_id_buffer_anno_model_suppression = "external"
# ======================================================================

def test_external_suppression_writes_no_model_category_visibility(tmp_path):
    anno, doc, view, diag, at_export = _run(tmp_path, suppression="external")
    assert view.set_category_hidden_calls == [], (
        "under 'external' the pass must not write model category visibility in "
        "EITHER direction -- not to hide, and not to restore")
    assert not any(value for value in at_export["category_hidden"].values()), (
        "no model category may be hidden while the export runs under 'external'")
    assert anno["metadata"]["model_suppression_mode"] == "external"


def test_external_suppression_still_records_what_it_read(tmp_path):
    """Not writing is not the same as not looking.

    A sidecar with an empty ``categories_hidden`` map would be
    indistinguishable from a project with no model categories, so the read is
    kept and only the WRITE is skipped.
    """
    anno, doc, view, diag, at_export = _run(tmp_path, suppression="external")
    assert anno["metadata"]["categories_hidden"], (
        "the model category state must still be READ and recorded under "
        "'external'")


def test_external_suppression_leaves_the_views_filters_enabled(tmp_path):
    """The whole point: the caller's own white filter must survive the export.

    The shipped pass disables every enabled+visible filter, which would undo
    exactly the suppression V1-V3 rely on.
    """
    anno, doc, view, diag, at_export = _run(tmp_path, suppression="external",
                                            view_filters=[901])
    # AT EXPORT TIME, which is the only reading that discriminates: the filter
    # has to be live while ExportImage runs, and a post-hoc read is True under
    # both modes.
    assert at_export["filter_enabled"][901] is True
    assert view.filter_enabled[901] is True
    assert not any(entry.get("callsite") == "annotation_restore_filter_enabled"
                   for entry in anno["metadata"]["restore_failures"])


def test_an_unknown_suppression_mode_raises_rather_than_defaulting(tmp_path):
    """A typo must not silently become the shipped behaviour.

    The mode decides whether "the annotation TIFF is annotation on white" was
    arranged by this function or by its caller. Falling back would make a
    variant that measured nothing look exactly like one that did.
    """
    with pytest.raises(ValueError) as excinfo:
        _run(tmp_path, suppression="extrenal")
    assert "color_id_buffer_anno_model_suppression" in str(excinfo.value)
    assert "extrenal" in str(excinfo.value)


# ======================================================================
# color_id_buffer_anno_smooth_edges_off = True
# ======================================================================

def test_smooth_edges_off_clears_it_and_restores_it(tmp_path):
    anno, doc, view, diag, at_export = _run(tmp_path, smooth_edges_off=True,
                                            smooth_edges_initial=True)
    assert anno["metadata"]["applied_smooth_edges"] is False
    assert anno["metadata"]["smooth_edges_read_error"] is None
    # OFF while the export ran -- the whole point of V2 -- and restored after.
    assert at_export["smooth_edges"] is False
    assert view._smooth_edges is True


def test_smooth_edges_off_is_written_after_the_display_style_change(tmp_path):
    """THE ORDERING IS THE MITIGATION, so the ordering is what is asserted.

    Whether setting ``DisplayStyle`` replaces the ViewDisplayModel -- and with
    it a SmoothEdges written beforehand -- is UNCONFIRMED on a real host. The
    switch lives in production precisely so the write lands AFTER that change,
    where the question cannot bite. A fake view that forgets SmoothEdges on a
    DisplayStyle write stands in for the worst case: if production wrote it
    first, the export would see True.
    """
    forgetful = {"seen": []}

    class _ForgetfulView(FakeViewPlan):
        def __setattr__(self, name, value):
            if name == "DisplayStyle":
                forgetful["seen"].append(("DisplayStyle", value))
                # The worst case: the display model is replaced, so anything
                # written to SmoothEdges before now is lost.
                object.__setattr__(self, "_smooth_edges", True)
            FakeViewPlan.__setattr__(self, name, value)

        def SetViewDisplayModel(self, dm):
            forgetful["seen"].append(("SmoothEdges", dm.SmoothEdges))
            FakeViewPlan.SetViewDisplayModel(self, dm)

    elements = _elements()
    view = _ForgetfulView(view_id=VIEW_ID)
    view._smooth_edges = True
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    cfg.color_id_buffer_anno_smooth_edges_off = True
    diag = FakeDiag()
    geom = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        forgetful["seen"] = []
        view._smooth_edges = True
        exported_smooth_edges = []
        doc.on_export_image = lambda opts: exported_smooth_edges.append(
            view._smooth_edges)
        color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)

    # The suppress transaction's DisplayStyle write comes first, and the
    # SmoothEdges write after it -- so what ExportImage sees is False.
    kinds = [name for name, _value in forgetful["seen"]]
    assert "DisplayStyle" in kinds, forgetful["seen"]
    display_index = kinds.index("DisplayStyle")
    smooth_after = [index for index, (name, value) in enumerate(forgetful["seen"])
                    if name == "SmoothEdges" and value is False
                    and index > display_index]
    assert smooth_after, (
        "SmoothEdges=False must be written AFTER the DisplayStyle change: "
        "{0}".format(forgetful["seen"]))
    assert exported_smooth_edges == [False], (
        "the export must run with SmoothEdges off; saw {0}".format(
            exported_smooth_edges))


def test_smooth_edges_off_reports_an_unreadable_state_rather_than_false(tmp_path):
    """A host with no ``SmoothEdges`` attribute has an UNKNOWN AA state.

    ``bool(getattr(dm, "SmoothEdges", None))`` would read that as False -- "AA
    is already off, nothing to do" -- which is the silent coercion the model
    pass's own sentinel exists to refuse.
    """
    class _NoSmoothEdges(object):
        def __init__(self):
            self.ShowShadows = False

        def Dispose(self):
            pass

    class _OldHostView(FakeViewPlan):
        def GetViewDisplayModel(self):
            return _NoSmoothEdges()

        def SetViewDisplayModel(self, dm):
            pass

    elements = _elements()
    view = _OldHostView(view_id=VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    cfg.color_id_buffer_anno_smooth_edges_off = True
    diag = FakeDiag()
    geom = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        anno = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)

    assert anno["metadata"]["smooth_edges_read_error"] == "AttributeError"
    assert anno["metadata"]["applied_smooth_edges"] == "read_failed"


def test_the_two_switches_are_independent(tmp_path):
    """V2 sets both, so neither may imply the other.

    A single knob doing both jobs would make V1 (filter only) and V2 (filter
    plus AA off) the same capture, and F3 would go unmeasured.
    """
    only_filter, _doc, view_a, _diag, at_export_a = _run(
        tmp_path / "a", suppression="external", smooth_edges_initial=True)
    assert only_filter["metadata"]["applied_smooth_edges"] == "not_attempted"
    assert at_export_a["smooth_edges"] is True
    assert view_a._smooth_edges is True

    only_aa, _doc, view_b, _diag, at_export_b = _run(
        tmp_path / "b", smooth_edges_off=True, smooth_edges_initial=True)
    assert only_aa["metadata"]["model_suppression_mode"] == "hide_categories"
    assert only_aa["metadata"]["applied_smooth_edges"] is False
    assert at_export_b["smooth_edges"] is False
    assert [call for call in view_b.set_category_hidden_calls if call[1] is True]


# ======================================================================
# the PERSISTED sidecar carries capture_faults (PR #215 review, round 2)
# ======================================================================

def test_the_persisted_sidecar_carries_capture_faults_and_failure_reason(tmp_path):
    """THE PRODUCER/CONSUMER CONTRACT, driven through real production.

    ``state_out`` used to be serialised ~100 lines BEFORE ``capture_faults`` was
    computed, so the persisted sidecar never carried them: a capture with faults
    was indistinguishable, in the file, from one with none. Only the returned
    in-memory metadata was right.

    This drives the REAL ``export_annotation_color_id_buffer_view``, forces a
    fault, and then READS THE FILE BACK. A test that injected
    ``capture_faults`` into a synthetic sidecar -- which is what the analyzer's
    own tests do -- cannot bind this: it asserts on a fixture the producer never
    wrote.
    """
    import json

    elements = _elements()
    view = FakeViewPlan(view_id=VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    geom = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        # FORCE A FAULT that is neither switch-related nor a restore failure:
        # a lattice mismatch, by telling the annotation pass the model export
        # came back at a width the lattice does not require. This is exactly the
        # class variant_measurement_check cannot see, because it happens after
        # any switch was applied.
        geom["model_accepted_px"] = int(geom["requested_px"]) + 7
        anno = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)

    assert anno["success"] is False
    assert anno["failure_reason"] == "annotation_lattice_mismatch"
    faults_in_memory = anno["metadata"]["capture_faults"]
    assert faults_in_memory, "the in-memory metadata must carry the fault"

    # AND THE FILE SAYS THE SAME THING. This is the assertion that was false.
    with open(anno["sidecar_path"], encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted["capture_faults"] == faults_in_memory
    assert persisted["failure_reason"] == "annotation_lattice_mismatch"


def test_a_clean_capture_persists_an_empty_fault_list_not_a_missing_key(tmp_path):
    """THE CONTROL. An absent key and an empty list are different facts.

    Without this, the assertion above would also pass against a producer that
    wrote ``capture_faults`` only when non-empty -- and a consumer could not then
    tell a clean capture from one written by an older build that never wrote the
    key at all.
    """
    import json

    anno, doc, view, diag, at_export = _run(tmp_path)
    assert anno["success"] is True
    assert anno["failure_reason"] is None
    with open(anno["sidecar_path"], encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert "capture_faults" in persisted
    assert persisted["capture_faults"] == []
    assert "failure_reason" in persisted
    assert persisted["failure_reason"] is None


# ======================================================================
# P1 (round 2 brief, defect R5): a category Revit REFUSES to override is
# not a capture fault
# ======================================================================

class _RefusingView(FakeViewPlan):
    """A view that refuses category overrides on one category.

    Models what Revit does on a non-overridable category: ``SetCategoryOverrides``
    raises "Category cannot be overridden". Observed on the 2026-09-22 run and
    on round 1, where it made every plan variant report
    ``annotation_view_state_not_restored``.
    """

    def __init__(self, view_id, refuse_ids=(), name="TestPlan"):
        FakeViewPlan.__init__(self, view_id, name)
        self._refuse_ids = set(int(v) for v in refuse_ids)
        self.category_override_writes = []

    def SetCategoryOverrides(self, cat_id, ogs):
        value = int(cat_id.IntegerValue)
        self.category_override_writes.append(value)
        if value in self._refuse_ids:
            raise Exception("Category cannot be overridden")
        FakeViewPlan.SetCategoryOverrides(self, cat_id, ogs)


def _run_with_view(tmp_path, view, cfg_mutate=None):
    elements = _elements()
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    if cfg_mutate is not None:
        cfg_mutate(cfg)
    diag = FakeDiag()
    geom = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        anno = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)
    return anno, doc, view, diag


def test_a_non_overridable_category_does_not_fail_the_capture(tmp_path):
    """R5, the defect this round exists to fix.

    Round 1: every plan variant returned ``success=false`` /
    ``annotation_view_state_not_restored`` with "2 restore step(s) raised" while
    all eight read-back obligations said restored and ``document_safe`` was true.
    Five good captures read as failures.
    """
    view = _RefusingView(VIEW_ID, refuse_ids=[ANNO_CAT.Id.IntegerValue])
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    assert anno["metadata"]["restore_failures"] == [], (
        "a category Revit refuses to override must not enter restore_failures")
    faults = [f["fault"] for f in anno["metadata"]["capture_faults"]]
    assert "annotation_view_state_not_restored" not in faults, faults
    assert anno["success"] is True, anno["failure_reason"]


def test_the_refusal_is_recorded_three_valued_rather_than_silently_skipped(tmp_path):
    """Not failing is not the same as not happening.

    A capture that quietly skipped the category would be indistinguishable from
    one where the halftone suppression worked -- and halftone is a graphics
    setting that changes exported colour, so which it was matters.
    """
    view = _RefusingView(VIEW_ID, refuse_ids=[ANNO_CAT.Id.IntegerValue])
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    outcomes = anno["metadata"]["category_halftone_outcomes"]
    assert outcomes, "every category considered must be recorded"
    refused = outcomes[ANNO_CAT.Id.IntegerValue]
    assert refused["outcome"] == "not_overridable"
    assert "cannot be overridden" in refused["error"].lower()
    # And it is NOT in the state map, so restore never tries to put it back.
    assert ANNO_CAT.Id.IntegerValue not in anno["metadata"][
        "category_halftone_state"]


def test_a_category_that_accepted_the_override_is_recorded_applied(tmp_path):
    """THE CONTROL. Without it the fix could classify every category as
    not_overridable and the halftone suppression would silently stop happening
    while every capture read clean."""
    view = _RefusingView(VIEW_ID, refuse_ids=[])
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    outcomes = anno["metadata"]["category_halftone_outcomes"]
    assert outcomes
    for cat_id, record in outcomes.items():
        assert record["outcome"] == "applied", (cat_id, record)
        assert record.get("restore") == "restored", (cat_id, record)
    # The state map carries exactly the categories that were changed.
    assert set(anno["metadata"]["category_halftone_state"]) == set(outcomes)


def test_a_genuine_restore_failure_still_fails_the_capture(tmp_path):
    """The other half of the fix, and the one that keeps it honest.

    A category that accepted the override on the way IN and fails on the way OUT
    for some other reason has left the view changed. That is still a capture
    fault -- the fix must not turn every restore error into a shrug.
    """
    class _FailsOnRestore(FakeViewPlan):
        def __init__(self, view_id):
            FakeViewPlan.__init__(self, view_id)
            self._suppressed = set()

        def SetCategoryOverrides(self, cat_id, ogs):
            value = int(cat_id.IntegerValue)
            if value in self._suppressed:
                raise Exception("InvalidOperationException: the document is busy")
            self._suppressed.add(value)
            FakeViewPlan.SetCategoryOverrides(self, cat_id, ogs)

    view = _FailsOnRestore(VIEW_ID)
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    assert anno["metadata"]["restore_failures"], (
        "a genuine restore failure must still be recorded")
    faults = [f["fault"] for f in anno["metadata"]["capture_faults"]]
    assert "annotation_view_state_not_restored" in faults
    assert anno["success"] is False
    outcomes = anno["metadata"]["category_halftone_outcomes"]
    assert any(record.get("restore") == "failed" for record in outcomes.values())


def test_the_suppress_side_state_is_recorded_only_after_the_write_succeeds(tmp_path):
    """The ROOT CAUSE, asserted directly.

    ``category_halftone_state`` used to be written before
    ``SetCategoryOverrides``, so a refused category was entered anyway and the
    restore loop tried to undo a change that never happened. The map must hold
    exactly the categories whose write landed.
    """
    view = _RefusingView(VIEW_ID, refuse_ids=[ANNO_CAT.Id.IntegerValue])
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    state = anno["metadata"]["category_halftone_state"]
    outcomes = anno["metadata"]["category_halftone_outcomes"]
    applied = {cid for cid, rec in outcomes.items() if rec["outcome"] == "applied"}
    refused = {cid for cid, rec in outcomes.items()
               if rec["outcome"] == "not_overridable"}
    assert set(state) == applied
    assert refused
    assert not (set(state) & refused)


def test_an_is_category_overridable_probe_short_circuits_the_write(tmp_path):
    """When the host exposes ``View.IsCategoryOverridable`` (UNCONFIRMED on
    2025), a refusal is anticipated rather than provoked -- so the write is not
    even attempted on a category the view has already said no to."""
    class _ProbeView(_RefusingView):
        def IsCategoryOverridable(self, cat_id):
            return int(cat_id.IntegerValue) not in self._refuse_ids

    view = _ProbeView(VIEW_ID, refuse_ids=[ANNO_CAT.Id.IntegerValue])
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    record = anno["metadata"]["category_halftone_outcomes"][
        ANNO_CAT.Id.IntegerValue]
    assert record["outcome"] == "not_overridable"
    assert record["detected"] == "View.IsCategoryOverridable"
    assert "error" not in record, "the write must not have been attempted"
    assert ANNO_CAT.Id.IntegerValue not in view.category_override_writes
    assert anno["success"] is True


def test_a_category_that_accepts_then_refuses_is_a_refusal_not_a_failure(tmp_path):
    """THE CASE THE RESTORE-SIDE CLASSIFIER EXISTS FOR, and it was unbound.

    The root-cause fix means a category Revit refuses up front never enters
    ``category_halftone_state``, so the restore loop never sees it -- which made
    the restore-side classification dead code under every other fixture here.
    Mutating ``_category_override_refused`` to always-False in the restore loop
    left the suite green, found by mutating the fix rather than by reading it.

    This fixture accepts the suppress write and refuses the restore write, which
    is the only way that branch is reachable. Revit refusing means nothing was
    left changed, so it is not a capture fault.
    """
    class _AcceptsThenRefuses(FakeViewPlan):
        def __init__(self, view_id, target_id):
            FakeViewPlan.__init__(self, view_id)
            self._target = int(target_id)
            self._seen = set()

        def SetCategoryOverrides(self, cat_id, ogs):
            value = int(cat_id.IntegerValue)
            if value == self._target and value in self._seen:
                raise Exception("Category cannot be overridden")
            self._seen.add(value)
            FakeViewPlan.SetCategoryOverrides(self, cat_id, ogs)

    view = _AcceptsThenRefuses(VIEW_ID, ANNO_CAT.Id.IntegerValue)
    anno, _doc, _view, _diag = _run_with_view(tmp_path, view)

    record = anno["metadata"]["category_halftone_outcomes"][
        ANNO_CAT.Id.IntegerValue]
    # Accepted going in -- so it IS in the state map, unlike the up-front case.
    assert record["outcome"] == "applied"
    assert ANNO_CAT.Id.IntegerValue in anno["metadata"]["category_halftone_state"]
    # And refused coming out, which is a refusal rather than a failure.
    assert record["restore"] == "not_overridable"
    assert "cannot be overridden" in record["restore_error"].lower()
    assert anno["metadata"]["restore_failures"] == []
    faults = [f["fault"] for f in anno["metadata"]["capture_faults"]]
    assert "annotation_view_state_not_restored" not in faults
    assert anno["success"] is True


# The VERBATIM exception Revit raised on the round-1 plan run (2026-09-22,
# view 19290402). Pasted rather than paraphrased: the classifier's whole job is
# to recognise THIS string, and a paraphrase would bind it to my wording instead
# of Revit's.
_REAL_REFUSAL = (
    "ArgumentException: Category cannot be overridden.\r\n"
    "Parameter name: categoryId\n"
    "   at Autodesk.Revit.DB.View.SetCategoryOverrides(ElementId categoryId, "
    "OverrideGraphicSettings overrideGraphicSettings)\r\n"
    "   at InvokeStub_View.SetCategoryOverrides(Object, Span`1)\r\n"
    "   at System.Reflection.MethodBaseInvoker.InvokeWithFewArgs(Object obj, "
    "BindingFlags invokeAttr, Binder binder, Object[] parameters, CultureInfo culture)"
)


def test_the_classifier_recognises_the_error_revit_actually_raised():
    """Bound to the run, not to my guess at the wording.

    Round 1's plan view produced this exact string twice, from
    ``annotation_restore_category_halftone``. If the marker set does not match
    it, the R5 fix does nothing on the only view known to trigger R5.
    """
    class _Ex(Exception):
        pass

    assert color_id_buffer._category_override_refused(_Ex(_REAL_REFUSAL)) is True


def test_the_classifier_does_not_swallow_an_unrelated_failure():
    """THE CONTROL, and the one that matters most here: a classifier that
    returned True for everything would silence every genuine restore failure
    while making the R5 symptom disappear."""
    class _Ex(Exception):
        pass

    for message in (
            "InvalidOperationException: The document is currently modifiable",
            "ArgumentException: categoryId is not a valid category",
            "Autodesk.Revit.Exceptions.ArgumentNullException: value cannot be null",
            "",
    ):
        assert color_id_buffer._category_override_refused(_Ex(message)) is False, message


def test_round_ones_refusal_came_after_a_SUCCESSFUL_suppress_write():
    """WHY THE ROOT-CAUSE FIX ALONE WOULD NOT HAVE FIXED R5.

    Round 1's plan sidecar recorded ELEVEN categories in
    ``category_halftone_state`` -- meaning eleven suppress writes SUCCEEDED --
    and two ``annotation_restore_category_halftone`` failures. So the two
    offenders accepted the override on the way in and refused it on the way out.
    Recording the state only after a successful write (the root cause of the
    other failure mode) does not touch that case at all; the restore-side
    classifier is the load-bearing half, and it was unbound until a mutation
    showed it.

    This test pins the shape of the real run so that reasoning stays checkable
    rather than living in a commit message.
    """
    class _AcceptsThenRefuses(FakeViewPlan):
        def __init__(self, view_id, target_id):
            FakeViewPlan.__init__(self, view_id)
            self._target = int(target_id)
            self._seen = set()

        def SetCategoryOverrides(self, cat_id, ogs):
            value = int(cat_id.IntegerValue)
            if value == self._target and value in self._seen:
                raise Exception(_REAL_REFUSAL)
            self._seen.add(value)
            FakeViewPlan.SetCategoryOverrides(self, cat_id, ogs)

    import os
    import tempfile
    target = ANNO_CAT.Id.IntegerValue
    with tempfile.TemporaryDirectory() as tmp:
        view = _AcceptsThenRefuses(VIEW_ID, target)
        anno, _doc, _view, _diag = _run_with_view(os.path.join(tmp, "x"), view)

    record = anno["metadata"]["category_halftone_outcomes"][target]
    assert record["outcome"] == "applied", "it must have ACCEPTED the suppress write"
    assert target in anno["metadata"]["category_halftone_state"]
    assert record["restore"] == "not_overridable"
    assert anno["metadata"]["restore_failures"] == []
    assert anno["success"] is True


# ======================================================================
# P2 (round 2 revised): color_id_buffer_anno_crop_mode = "untouched"
# ======================================================================
#
# Round 1 measured why this exists: the shipped pass widens view.CropBox to
# frame B, and datum extents clip to the crop -- so every capture taken so far
# lengthened level and grid lines and pulled in content from beyond the
# authored crop. Under "untouched" the pass writes NEITHER CropBox NOR
# CropBoxActive, in either direction.
#
# Wired to these mutations of color_id_buffer.py, each of which turns a test
# here red: make ``write_crop_here`` unconditionally True (the untouched tests
# see a write), unconditionally False (the control sees none), drop the guard
# on the restore step (untouched sees the restore write), or drop the guard on
# the ``annotation_frame_not_applied`` fault (untouched fails its capture).

class _CropRecordingView(FakeViewPlan):
    """Records every CropBox / CropBoxActive assignment once ``recording``."""

    def __init__(self, view_id, name="TestPlan"):
        object.__setattr__(self, "recording", False)
        object.__setattr__(self, "crop_writes", [])
        FakeViewPlan.__init__(self, view_id, name)

    def __setattr__(self, name, value):
        if name in ("CropBox", "CropBoxActive") and self.recording:
            self.crop_writes.append(name)
        object.__setattr__(self, name, value)


def _run_crop_mode(tmp_path, crop_mode=None, suppression="external"):
    elements = _elements()
    view = _CropRecordingView(view_id=VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    cfg.color_id_buffer_anno_model_suppression = suppression
    if crop_mode is not None:
        cfg.color_id_buffer_anno_crop_mode = crop_mode
    diag = FakeDiag()
    geom = {}
    at_export = {}
    with install_fake_revit_db():
        color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=diag, raster=_raster(), elem_cache=None, geometry_out=geom)
        # The MODEL pass writes and restores the crop too (to A); only the
        # annotation pass's writes are this switch's business.
        authored_box = view.CropBox
        view.recording = True

        def _observe(opts):
            at_export["crop_box"] = view.CropBox
            at_export["pixel_size"] = int(opts.PixelSize)

        doc.on_export_image = _observe
        anno = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements)
    return anno, view, geom, at_export, authored_box


def test_control_by_default_the_pass_writes_frame_b_as_the_crop(tmp_path):
    """The control: the DEFAULT position is the shipped one, which writes the
    crop and restores it. Without this, every 'untouched' assertion below would
    also pass against a switch stuck in the untouched position."""
    anno, view, geom, at_export, authored = _run_crop_mode(tmp_path)
    assert "CropBox" in view.crop_writes and "CropBoxActive" in view.crop_writes
    assert at_export["crop_box"] is not authored, (
        "the shipped pass must have replaced the crop while the export ran")
    registration = anno["metadata"]["registration"]
    assert registration["crop_mode"] == "frame_b"
    assert registration["rendered_uv"] == [float(v) for v in geom["frame_snapped_uv"]]
    assert registration["requested_px_source"] == "frame_px"
    assert at_export["pixel_size"] == int(geom["frame_px"][0])


def test_untouched_crop_mode_writes_neither_crop_property_in_either_direction(tmp_path):
    anno, view, geom, at_export, authored = _run_crop_mode(
        tmp_path, crop_mode="untouched")
    assert view.crop_writes == [], (
        "under 'untouched' the annotation pass must not write CropBox or "
        "CropBoxActive -- not to set frame B and not to restore it")
    # And the export SAW the authored crop, the only discriminating reading.
    assert at_export["crop_box"] is authored
    assert anno["metadata"]["registration"]["crop_mode"] == "untouched"


def test_untouched_crop_mode_records_the_rendered_rectangle_as_unknown(tmp_path):
    """None is the honest value: nothing was handed to Revit. The reason is what
    separates it from a frame_b crop that failed to apply."""
    anno, view, geom, at_export, authored = _run_crop_mode(
        tmp_path, crop_mode="untouched")
    registration = anno["metadata"]["registration"]
    assert registration["rendered_uv"] is None
    assert "untouched" in registration["rendered_uv_reason"]
    assert "MEASURED" in registration["rendered_uv_reason"]


def test_untouched_crop_mode_is_not_an_annotation_frame_not_applied_fault(tmp_path):
    """The capture never claims to render B, so there is no frame to have failed
    to apply. A fault here would mark every untouched capture failed."""
    anno, view, geom, at_export, authored = _run_crop_mode(
        tmp_path, crop_mode="untouched")
    faults = [f["fault"] for f in anno["metadata"]["capture_faults"]]
    assert "annotation_frame_not_applied" not in faults
    assert anno["success"] is True, anno["metadata"]["capture_faults"]


def test_untouched_crop_mode_requests_the_model_pass_crop_pixel_count(tmp_path):
    """B's pixel count would describe a rectangle this capture never asks for.
    The fixture's frame and crop differ in width, so the two candidates are
    distinguishable -- a fixture where they were equal would pass either way."""
    anno, view, geom, at_export, authored = _run_crop_mode(
        tmp_path, crop_mode="untouched")
    assert int(geom["crop_px"][0]) != int(geom["frame_px"][0])
    assert at_export["pixel_size"] == int(geom["crop_px"][0])
    assert anno["metadata"]["registration"]["requested_px_source"] == "model_crop_px"
    assert anno["metadata"]["resolution"]["requested_pixel_size"] == int(geom["crop_px"][0])


def test_an_unknown_crop_mode_raises_rather_than_defaulting(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _run_crop_mode(tmp_path, crop_mode="untuoched")
    assert "color_id_buffer_anno_crop_mode" in str(excinfo.value)
    assert "untuoched" in str(excinfo.value)


def test_crop_mode_is_not_a_config_field():
    """Probe-only, like the other two switches: no production default, no
    to_dict() representation, no way to reach a production run."""
    assert not hasattr(Config(), "color_id_buffer_anno_crop_mode")
    assert "color_id_buffer_anno_crop_mode" not in Config().to_dict()


# ---- color_id_buffer_model_lines_visible (MODEL pass, probe-only) ----------

LINES_CAT = FakeCategory("Lines", -2000051, cat_type="Model")


def _run_model_lines(tmp_path, lines_visible=None):
    elements = _elements()
    view = FakeViewPlan(view_id=VIEW_ID)
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT, LINES_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    if lines_visible is not None:
        cfg.color_id_buffer_model_lines_visible = lines_visible
    at_export = {}
    doc.on_export_image = lambda opts: at_export.update(
        lines_hidden=view.category_hidden.get(LINES_CAT.Id.IntegerValue, False),
        anno_hidden=view.category_hidden.get(ANNO_CAT.Id.IntegerValue, False))
    with install_fake_revit_db():
        out = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg,
            diag=FakeDiag(), raster=_raster(), elem_cache=None, geometry_out={})
    return out, view, at_export


def test_control_by_default_the_model_pass_hides_lines(tmp_path):
    """The CONTROL: without it the test below would also pass against a model
    pass that never hid OST_Lines at all."""
    out, view, at_export = _run_model_lines(tmp_path)
    assert at_export == {"lines_hidden": True, "anno_hidden": True}
    assert out["metadata"]["model_lines_visible"] is False


def test_model_lines_visible_leaves_lines_visible_and_nothing_else(tmp_path):
    """The probe's registration marks are detail lines; the model pass must
    draw them. Mutation: ignore the switch, or pop the wrong category."""
    out, view, at_export = _run_model_lines(tmp_path, lines_visible=True)
    assert at_export == {"lines_hidden": False, "anno_hidden": True}
    assert (LINES_CAT.Id.IntegerValue, True) not in view.set_category_hidden_calls
    assert out["metadata"]["model_lines_visible"] is True
    assert str(LINES_CAT.Id.IntegerValue) not in {
        str(k) for k in out["metadata"]["categories_hidden"]}


def test_model_lines_visible_is_not_a_config_field():
    assert not hasattr(Config(), "color_id_buffer_model_lines_visible")
    assert "color_id_buffer_model_lines_visible" not in Config().to_dict()
