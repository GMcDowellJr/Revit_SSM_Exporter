"""Production's REGISTERED Stage A capture, DRIVEN end to end on the fake DB.

``export_registered_stage_a_view`` sequences the real model and annotation
passes, the registration marks and the membership white suppression inside one
TransactionGroup, and uses the ROLLBACK as the restore. What is asserted is
what each export SAW when it happened (the fake doc's ExportImage hook) and
what the FILES say afterwards -- not the return value alone, because a record
written before the read-back would carry no restore verdict at all
(CLAUDE.md, defect class 4).

The fake world is the annotation-pass variant probe's (its TransactionGroup
genuinely rolls view and document state back), so the probe and production
are held to the same harness.
"""
import json

import pytest
import types

import vop_interwoven.stage_a_registration as registration
from vop_interwoven.config import Config
from vop_interwoven.stage_a_registered_capture import (
    export_registered_stage_a_view, view_state_verdict,
)

from tests import test_probe_anno_pass_run_variant as world
from tests.stage_a_capture_fakes import FakeDiag, FakeElement, install_fake_revit_db
from tests.test_stage_a_annotation_pass_probe_switches import (
    ANNO_CAT, MODEL_CAT, OTHER_MODEL_CAT, VIEW_ID, _BBox, _SizedDoc, _elements,
    _model_pass_elements, _raster,
)

WHITE = (255, 255, 255)


def _run(tmp_path, rollback_restores=("view", "doc"), break_model_pass=False,
         monkeypatch=None, leave_view_changed=False, crop_active=True,
         extra_elements=(), view_setup=None, cfg_setup=None):
    del world._LOG[:]
    elements = _elements()
    elements[0].bbox = _BBox((25, 18, 0), (26, 19, 0))
    elements.append(FakeElement(1003, MODEL_CAT))
    elements.extend(extra_elements)
    view = world._ProbeView(VIEW_ID)
    if view_setup is not None:
        view_setup(view)
    view.Scale = 96
    object.__setattr__(view, "CropBoxActive", bool(crop_active))
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, OTHER_MODEL_CAT, ANNO_CAT,
                                world.LINES_CAT])
    doc.IsModifiable = False
    doc.Create = world._Create(doc)
    exports = []

    def _at_export(opts):
        exports.append({
            "path": opts.FilePath,
            "overrides": dict((eid, world._colour_of(ogs))
                              for eid, ogs in view.element_overrides.items()),
            "lines_hidden": view.category_hidden.get(
                world.LINES_CAT.Id.IntegerValue, False),
            "marks_in_doc": sorted(i for i in doc._by_id if i > world.MARK_ID_BASE),
            "crop_box_active": view.CropBoxActive,
            "hidden_elements": sorted(view.hidden_elements),
            "crop_box": (view.CropBox.Min.X, view.CropBox.Min.Y,
                         view.CropBox.Max.X, view.CropBox.Max.Y),
        })

    doc.on_export_image = _at_export
    cfg = Config(enable_color_id_buffer_stage_a=True,
                 color_id_buffer_registered_capture=True,
                 color_id_buffer_export_dpi=150.0)
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    if cfg_setup is not None:
        cfg_setup(cfg)
    diag = FakeDiag()
    with install_fake_revit_db() as fake_db:
        fake_db.Transaction = world._Tx
        fake_db.TransactionGroup = world._Group
        fake_db.TransactionStatus = world._STATUS
        fake_db.Line = world._Line
        fake_db.FilteredElementCollector = world._ViewCollector
        world._ROLLBACK_TARGETS[:] = [obj for name, obj in (("view", view),
                                                            ("doc", doc))
                                      if name in rollback_restores]
        if break_model_pass:
            monkeypatch.setattr(
                "vop_interwoven.color_id_buffer.export_color_id_buffer_view",
                lambda *a, **k: {"success": False, "failure_reason": "boom",
                                 "tiff_path": None, "sidecar_path": None})
        if leave_view_changed:
            from vop_interwoven import color_id_buffer as _cib
            real_anno = _cib.export_annotation_color_id_buffer_view

            def _leaky(*a, **k):
                result = real_anno(*a, **k)
                object.__setattr__(view, "CropBoxVisible", True)
                return result
            monkeypatch.setattr(
                "vop_interwoven.color_id_buffer."
                "export_annotation_color_id_buffer_view", _leaky)
        out = export_registered_stage_a_view(
            doc, view, _model_pass_elements(elements), cfg, diag=diag,
            raster=_raster())
    return out, view, doc, exports, diag


def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def test_both_passes_run_registered_and_the_view_comes_back(tmp_path):
    out, view, doc, exports, diag = _run(tmp_path)
    reg = out["registration"]
    assert reg["faults"] == [], reg["faults"]
    assert out["success"] is True and out["annotation_pass_success"] is True
    assert out["registration_success"] is True
    assert reg["marks"]["created_count"] == 12
    model_export, anno_export = exports
    # MODEL export: the marks exist, OST_Lines is visible, the marks are in
    # the reserved colour, and nothing is white yet.
    assert len(model_export["marks_in_doc"]) == 12
    assert model_export["lines_hidden"] is False
    mark_ids = [m["id"] for m in reg["marks"]["created"]]
    assert all(model_export["overrides"][i] == registration.MARK_COLOUR
               for i in mark_ids)
    assert WHITE not in model_export["overrides"].values()
    # ANNOTATION export: every model member white, the marks in the reserved
    # tick colour too (2026-10-02; before, each took a palette colour).
    for eid in (1001, 1002, 1003):
        assert anno_export["overrides"][eid] == WHITE, eid
    assert all(anno_export["overrides"][i] == registration.MARK_COLOUR
               for i in mark_ids)
    # The rollback put everything back, and the read-back says so.
    assert reg["restore"]["rolled_back"] is True
    assert all(v["status"] == "restored" for v in reg["restore"]["view_state"].values())
    assert reg["restore"]["marks_still_in_project"] == []
    assert reg["restore"]["element_overrides"]["left_behind"] == []
    assert [i for i in doc._by_id if i > world.MARK_ID_BASE] == []


def test_the_annotation_pass_never_writes_the_crop(tmp_path):
    """The fixture's crop is ACTIVE, so "authored_else_crop_a" leaves it alone.
    Mutation: set crop_mode "frame_b" in the annotation cfg."""
    _run(tmp_path)
    assert world._anno_crop_writes() == []


def test_the_FILES_carry_the_registration_record_with_its_restore_verdict(tmp_path):
    """Assert the FILE, not the return value (defect class 4). A clean capture
    persists an EMPTY fault list -- present and empty is a different fact from
    absent, which is what the control half of this test pins."""
    out, view, doc, exports, diag = _run(tmp_path)
    model = _sidecar(out["sidecar_path"])["registration_marks"]
    anno = _sidecar(out["annotation_sidecar_path"])["registration_marks"]
    for record, pass_name in ((model, "model"), (anno, "annotation")):
        assert record["pass"] == pass_name
        assert record["faults"] == []
        assert record["restore"]["rolled_back"] is True
        assert record["annotation_crop_mode"] == "authored_else_crop_a"
        assert len(record["marks"]) == 12
        # The reserved tick colour: ONE colour, in both captures.
        assert record["colour_mode"] == "shared"
        assert {tuple(m["rgb"]) for m in record["marks"]} == {registration.MARK_COLOUR}
    anno_side = _sidecar(out["annotation_sidecar_path"])
    # The ticks are not elements: no palette entry, named where they are.
    assert not set(str(m["id"]) for m in anno["marks"]) & set(
        anno_side["color_assignment_map"])
    assert anno_side["palette_reservation"]["mark_ids_painted"] == sorted(
        m["id"] for m in anno["marks"])


def test_neither_palette_hands_out_a_tick_look_alike_in_the_FILE(tmp_path):
    """Mutation: the registered capture not passing reserve_tick_colour turns
    this red (reserved_tick_colour None)."""
    out, _view, _doc, _e, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        side = _sidecar(path)
        assert side["palette_reservation"]["reserved_tick_colour"] == list(
            registration.MARK_COLOUR), path
        for key in ("color_assignment_map", "link_category_color_map"):
            for rgb in (side.get(key) or {}).values():
                assert not registration.fringe_reads_as_mark(tuple(rgb)), (path, rgb)


def test_a_rollback_that_undoes_nothing_is_a_fault_in_the_FILE(tmp_path):
    """The marks and the white overrides are removed ONLY by the rollback, so
    a rollback that undoes nothing leaves both, and both are faults. (The view
    properties read back restored even then: each pass restores its own
    explicitly -- which is why they need the separate test below.)
    Mutation: read back before the rollback, or skip either check."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=())
    faults = [f["fault"] for f in out["registration"]["faults"]]
    # The ticks left in the document also change what the view returns.
    assert faults == ["registration_marks_left_in_project",
                      "element_overrides_left_behind", "view_membership_changed"]
    assert out["registration_success"] is False
    persisted = _sidecar(out["annotation_sidecar_path"])["registration_marks"]["faults"]
    assert [f["fault"] for f in persisted] == faults
    assert diag.errors


def test_marks_left_behind_alone_are_a_fault(tmp_path):
    """View restored, document not: only the marks' own check can see it."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=("view",))
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["registration_marks_left_in_project", "view_membership_changed"]


def test_a_failed_model_pass_skips_the_annotation_pass_and_still_rolls_back(
        tmp_path, monkeypatch):
    out, view, doc, exports, diag = _run(tmp_path, break_model_pass=True,
                                         monkeypatch=monkeypatch)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["registered_capture_raised"]
    assert "annotation_pass" not in out
    assert out["registration"]["restore"]["rolled_back"] is True
    assert out["registration"]["restore"]["marks_still_in_project"] == []
    assert out["registration_success"] is False


def test_view_state_verdict_is_three_valued():
    value = {"state": "value", "value": 1}
    assert view_state_verdict({"a": value}, {"a": value})["a"]["status"] == "restored"
    assert view_state_verdict({"a": value}, {"a": {"state": "value", "value": 2}})[
        "a"]["status"] == "not_restored"
    assert view_state_verdict({"a": value}, {"a": {"state": "unavailable",
                                                   "reason": "x"}})[
        "a"]["status"] == "unverified"


def test_a_view_property_left_changed_is_a_fault_when_nothing_undoes_it(
        tmp_path, monkeypatch):
    """A pass that leaves CropBoxVisible changed, and a rollback that undoes
    nothing to the view: only the view-state read-back can see it.
    Mutation: drop crop_box_visible from view_state, or skip the verdict."""
    out, view, doc, exports, diag = _run(tmp_path, rollback_restores=("doc",),
                                         monkeypatch=monkeypatch,
                                         leave_view_changed=True)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert "view_state_not_restored" in faults
    assert out["registration"]["restore"]["view_state"]["crop_box_visible"][
        "status"] == "not_restored"


def test_the_same_leak_is_undone_by_a_real_rollback(tmp_path, monkeypatch):
    """The CONTROL for the test above."""
    out, view, doc, exports, diag = _run(tmp_path, monkeypatch=monkeypatch,
                                         leave_view_changed=True)
    assert out["registration"]["faults"] == []


def test_a_crop_INACTIVE_view_is_captured_with_crop_A_and_put_back(tmp_path):
    """Round 3b: an untouched crop-inactive plan exported its whole extent at
    2.9 px/ft. The registered capture now applies crop A for the annotation
    export -- the same rectangle the model export rendered -- and the view
    comes back crop-inactive. Mutation: pass "untouched" instead."""
    out, view, doc, exports, diag = _run(tmp_path, crop_active=False)
    model_export, anno_export = exports
    assert anno_export["crop_box_active"] is True
    assert anno_export["crop_box"] == model_export["crop_box"]
    registration_block = out["annotation_pass"]["metadata"]["registration"]
    assert registration_block["crop_applied"] == "crop_a"
    assert view.CropBoxActive is False
    assert out["registration"]["faults"] == []
    assert out["registration"]["mark_reference_source"] == "model_crop_a"


# ---- the view's other detail lines, out of the MODEL capture ----------------

DETAIL_LINE, MODEL_LINE = 3001, 3002


def _lines():
    return [FakeElement(DETAIL_LINE, world.LINES_CAT, owner_view_id=VIEW_ID),
            FakeElement(MODEL_LINE, world.LINES_CAT)]


def test_detail_lines_are_hidden_for_the_model_pass_only(tmp_path):
    """OST_Lines stays visible in the model pass so the marks draw; the view's
    OTHER detail lines are annotation and must not draw there. Hidden for the
    model export, shown for the annotation export, and gone from the hidden
    set afterwards. A model line is never touched: it is model content."""
    out, view, doc, exports, diag = _run(tmp_path, extra_elements=_lines())
    model_export, anno_export = exports
    assert DETAIL_LINE in model_export["hidden_elements"]
    assert DETAIL_LINE not in anno_export["hidden_elements"]
    assert MODEL_LINE not in model_export["hidden_elements"]
    assert DETAIL_LINE not in view.hidden_elements
    reg = out["registration"]
    assert reg["faults"] == [], reg["faults"]
    assert reg["detail_lines"]["hidden_for_model_pass"] == 1
    assert reg["restore"]["detail_lines"]["left_hidden"] == []
    # The FILE carries it: written last, after the read-back.
    assert _sidecar(out["sidecar_path"])["registration_marks"]["faults"] == []


def test_without_detail_lines_nothing_is_hidden(tmp_path):
    """The CONTROL: no view-owned line, no hide, and the record says so."""
    out, view, doc, exports, diag = _run(tmp_path)
    assert all(e["hidden_elements"] == [] for e in exports)
    assert out["registration"]["detail_lines"] == {"count": 0}


def test_a_detail_line_the_author_hid_stays_hidden(tmp_path):
    def _hide(view):
        view.hidden_elements.add(DETAIL_LINE)
    out, view, doc, exports, diag = _run(tmp_path, extra_elements=_lines(),
                                         view_setup=_hide)
    assert all(DETAIL_LINE in e["hidden_elements"] for e in exports)
    assert DETAIL_LINE in view.hidden_elements
    assert out["registration"]["detail_lines"]["already_hidden"] == 1
    assert out["registration"]["detail_lines"]["hidden_for_model_pass"] == 0


def test_a_hide_that_fails_is_a_fault(tmp_path):
    def _refuse(view):
        def _raise(_ids):
            raise RuntimeError("HideElements refused")
        view.HideElements = _raise
    out, view, doc, exports, diag = _run(tmp_path, extra_elements=_lines(),
                                         view_setup=_refuse)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["detail_lines_may_draw_in_model_capture"]
    assert out["registration_success"] is False


def test_a_show_that_fails_is_a_fault_and_the_rollback_still_restores(tmp_path):
    """The annotation capture then lacks the line -- a fault. The group's
    rollback puts the view back regardless, and the read-back confirms it."""
    def _refuse(view):
        def _raise(_ids):
            raise RuntimeError("UnhideElements refused")
        view.UnhideElements = _raise
    out, view, doc, exports, diag = _run(tmp_path, extra_elements=_lines(),
                                         view_setup=_refuse)
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert faults == ["detail_lines_missing_from_annotation_capture"]
    assert DETAIL_LINE in exports[1]["hidden_elements"]
    assert DETAIL_LINE not in view.hidden_elements
    assert out["registration"]["restore"]["detail_lines"]["left_hidden"] == []


def test_a_line_the_rollback_left_hidden_is_a_fault(tmp_path):
    """The read-back is the check, not the rollback's word: a group that does
    not restore the VIEW leaves the line hidden, and that is reported."""
    def _refuse(view):
        def _raise(_ids):
            raise RuntimeError("UnhideElements refused")
        view.UnhideElements = _raise
    out, view, doc, exports, diag = _run(tmp_path, extra_elements=_lines(),
                                         view_setup=_refuse,
                                         rollback_restores=("doc",))
    faults = [f["fault"] for f in out["registration"]["faults"]]
    assert "detail_lines_left_hidden" in faults
    assert out["registration"]["restore"]["detail_lines"]["left_hidden"] == [DETAIL_LINE]


# --- C7: the registered capture neither computes nor records frame B ---------

_FRAME_B_KEYS = ("frame_uv", "frame_source", "frame_extent_ft", "frame_snapped_uv",
                 "frame_px", "raster_bounds_uv", "crop_offset_px", "crop_is_frame",
                 "paper_fit_in", "paper_width_in", "paper_height_in",
                 "anno_cap_envelope_applied")


def test_c7_the_model_lattice_is_crop_As_own_and_frame_B_is_not_recorded(tmp_path):
    from tests.test_stage_a_annotation_pass_probe_switches import MODEL_BOUNDS
    from vop_interwoven.resolution_contract import frame_export_geometry
    out, _view, _doc, _exports, _diag = _run(tmp_path)
    frame = _sidecar(out["sidecar_path"])["frame"]
    assert frame["status"] == "value" and frame["sizing_frame"] == "crop_a"
    assert not (set(_FRAME_B_KEYS) & set(frame)), set(_FRAME_B_KEYS) & set(frame)
    # The lattice is A's: exactly what frame_export_geometry gives with A as
    # its own frame -- and NOT what it gives with the wider raster frame.
    a = (MODEL_BOUNDS.xmin, MODEL_BOUNDS.ymin, MODEL_BOUNDS.xmax, MODEL_BOUNDS.ymax)
    own = frame_export_geometry(a, a, 96.0, 150.0)
    assert frame["crop_px"] == list(own["crop_px"])
    # D: the view's crop is ACTIVE, so it is not written; crop_uv is the
    # authored crop as read -- not the snapped lattice rectangle, which the
    # export does not render and the frame does not record.
    assert frame["crop_uv"] == pytest.approx(list(a))
    assert "crop_snapped_uv" not in frame
    assert frame["crop_write"]["written"] is False
    assert frame["achieved_fpp_ft"] == pytest.approx(own["achieved_fpp_ft"])
    anno_reg = _sidecar(out["annotation_sidecar_path"])["registration"]
    assert anno_reg["sizing_frame"] == "crop_a"
    for key in ("frame_snapped_uv", "frame_px", "model_crop_offset_px", "crop_is_frame"):
        assert key not in anno_reg, key


def test_c7_control_the_frame_b_fallback_still_records_frame_B(tmp_path):
    """Without it a writer that dropped frame B everywhere would pass above."""
    from tests.test_frame_export_geometry_call_site import _export, _raster_at
    from vop_interwoven.core.math_utils import Bounds2D
    frame = _export(tmp_path, _raster_at(1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))[
        "metadata"]["frame"]
    assert "sizing_frame" not in frame
    for key in ("frame_uv", "frame_px", "frame_source", "paper_fit_in", "crop_offset_px"):
        assert key in frame, key


# --- P1: one capture_integrity record per view, completed after the rollback -

def test_p1_a_clean_registered_capture_files_a_complete_clean_integrity_record(tmp_path):
    from vop_interwoven.color_id_buffer import SIDECAR_PROBE_ONLY_KEYS
    out, _view, _doc, _exports, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        side = _sidecar(path)
        integrity = side["capture_integrity"]
        assert integrity["status"] == "value"
        assert integrity["completed_by"][-1] == "registered_capture"
        assert integrity["rolled_back"] is True
        assert integrity["marks_still_in_project"] == 0
        assert integrity["restore_failures"] == 0
        assert integrity["capture_faults"] == []
        assert integrity["paint_failures"] == 0
        assert not (set(SIDECAR_PROBE_ONLY_KEYS) & set(side)), path
        assert "view_graphics_state" in side or path == out["annotation_sidecar_path"]


def test_p1_a_rollback_that_undoes_nothing_is_counted_in_the_FILE(tmp_path):
    """The CONTROL for the clean record: the same fields, not clean."""
    out, _view, _doc, _exports, _diag = _run(tmp_path, rollback_restores=())
    integrity = _sidecar(out["annotation_sidecar_path"])["capture_integrity"]
    assert integrity["marks_still_in_project"] == 12
    assert [f["fault"] for f in integrity["capture_faults"]
            if f.get("source") == "registered_capture"] == [
        "registration_marks_left_in_project", "element_overrides_left_behind",
        "view_membership_changed"]


def test_t1_the_tick_line_style_reaches_BOTH_sidecar_files(tmp_path):
    """pipeline_0930_0739: the chosen line style was absent from all 16
    sidecars -- recorded in memory, never carried into registration_marks.
    What is pinned is that the FILE carries it, with the path that chose it."""
    out, _view, _doc, _exports, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        rm = _sidecar(path)["registration_marks"]
        assert rm.get("line_style") is not None, path
        assert rm["line_style"] == out["registration"]["marks"]["line_style"]
        assert rm["line_style"]["path"] == "visible_existing"
        assert rm["line_style"]["name"] == "<Thin Lines>"
        assert rm["line_style"]["subcategory_hidden_in_view"] is False


# --- capture-state probe Q6: a hidden line-style SUBCATEGORY -----------------
#
# The template of 5823803 / 11999340 hides "<Thin Lines>" with OST_Lines
# visible. T1 picked it anyway, no tick drew, and _lines_category_hidden (the
# parent) said all was well. Mutations: tick_line_style calling
# _thinnest_line_style WITHOUT view= turns the first test red; deleting the
# subcategory fault in export_registered_stage_a_view turns the last two red.

def _hide(*cat_ids):
    def _setup(view):
        for cat_id in cat_ids:
            view.category_hidden[int(cat_id)] = True
    return _setup


def _may_not_draw(path):
    return [f for f in _sidecar(path)["capture_integrity"]["capture_faults"]
            if f.get("fault") == "registration_marks_may_not_draw"]


def test_q6_a_hidden_thin_lines_subcategory_is_passed_over_in_the_FILE(tmp_path):
    out, view, _d, _e, _diag = _run(tmp_path, view_setup=_hide(world.THIN_LINES_SUBCAT_ID))
    style = _sidecar(out["sidecar_path"])["registration_marks"]["line_style"]
    # Not <Overhead>: also weight 1, but dashed (Q6 run 20261001T183711).
    assert style["path"] == "visible_existing" and style["name"] == "Lines"
    assert [e["name"] for e in style["not_solid"]] == ["<Overhead>"]
    assert [h["name"] for h in style["hidden"]] == ["<Thin Lines>"]
    assert _may_not_draw(out["sidecar_path"]) == []
    assert out["registration"]["success"] is True


def test_q6_every_existing_style_hidden_makes_a_temporary_subcategory(tmp_path):
    subs = [sub_id for _n, _s, sub_id, _w in world.LINE_STYLES]
    out, _view, doc, _e, _diag = _run(tmp_path, view_setup=_hide(*subs))
    style = _sidecar(out["annotation_sidecar_path"])["registration_marks"]["line_style"]
    assert style["path"] == "temporary" and style["created"] is True
    assert style["projection_line_weight"] == 1
    # A new subcategory is not solid until set (the fake's, like Revit's
    # default, is not); the ticks must not be dashed.
    assert style["projection_pattern_solid"] is True
    assert style["subcategory_id"] == world.TEMP_SUBCAT_ID
    assert style["subcategory_hidden_in_view"] is False
    assert _may_not_draw(out["sidecar_path"]) == []
    # Made inside the capture's group, so its rollback removed it.
    assert registration.TEMPORARY_TICK_SUBCATEGORY not in doc._fake_subcategories


def test_q6_no_visible_style_and_no_temporary_one_is_a_fault_in_the_FILE(
        tmp_path, monkeypatch):
    monkeypatch.setattr(world._Categories, "NewSubcategory",
                        lambda self, parent, name: (_ for _ in ()).throw(
                            RuntimeError("NewSubcategory refused")))
    subs = [sub_id for _n, _s, sub_id, _w in world.LINE_STYLES]
    out, _view, _doc, _e, _diag = _run(tmp_path, view_setup=_hide(*subs))
    style = _sidecar(out["sidecar_path"])["registration_marks"]["line_style"]
    assert style["path"] == "none" and "NewSubcategory refused" in style["reason"]
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        assert len(_may_not_draw(path)) == 1, path
    assert out["registration"]["success"] is False


def test_q6_a_temporary_style_whose_solid_pattern_does_not_take_is_a_fault_in_the_FILE(
        tmp_path, monkeypatch):
    """Codex, PR #226: SetLinePatternId ignored leaves the new subcategory
    dashed. The read-back says so; the style is unavailable and the capture
    faults rather than drawing broken ticks. Mutation: dropping the
    read-back refusal in _temporary_tick_style turns this red."""
    monkeypatch.setattr(world._Subcategory, "SetLinePatternId",
                        lambda self, pattern_id, kind: None)
    monkeypatch.setattr(world, "DASHED", world.DASHED + (
        registration.TEMPORARY_TICK_SUBCATEGORY,))
    subs = [sub_id for _n, _s, sub_id, _w in world.LINE_STYLES]
    out, _view, _doc, _e, _diag = _run(tmp_path, view_setup=_hide(*subs))
    style = _sidecar(out["sidecar_path"])["registration_marks"]["line_style"]
    assert style["path"] == "none"
    assert style["temporary"]["projection_pattern_solid"] is False
    assert "did not read back as Solid" in style["reason"]
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        assert len(_may_not_draw(path)) == 1, path
    assert out["registration"]["success"] is False


def test_q6_a_temporary_subcategory_the_view_hides_is_a_fault(tmp_path):
    """Whether a template that controls V/G draws a subcategory it has never
    seen is probe Q6b's question; production READS it, and faults."""
    subs = [sub_id for _n, _s, sub_id, _w in world.LINE_STYLES]
    out, _view, _doc, _e, _diag = _run(
        tmp_path, view_setup=_hide(world.TEMP_SUBCAT_ID, *subs))
    style = out["registration"]["marks"]["line_style"]
    assert style["path"] == "temporary" and style["subcategory_hidden_in_view"] is True
    assert len(_may_not_draw(out["sidecar_path"])) == 1


def test_the_annotation_capture_runs_with_anti_aliasing_off_in_the_FILE(tmp_path):
    """Greg (2026-09-30): AA off in BOTH passes; the tick-blend handling in
    tools/registration_marks.py is a fallback. pipeline_0930_0739's annotation
    sidecars read "not_attempted" because the registered capture never set it."""
    out, view, _doc, _exports, _diag = _run(tmp_path)
    assert _sidecar(out["annotation_sidecar_path"])["applied_smooth_edges"] is False
    assert _sidecar(out["sidecar_path"])["applied_smooth_edges"] is False


def test_a_tick_under_an_annotation_element_is_moved_in_the_FILE(tmp_path):
    """Greg (2026-09-30): ticks are kept clear of annotation bboxes. A tag
    placed over a tick's default position moves that tick, and the sidecar
    says so; the control run (no tag) moves nothing."""
    out, _v, _d, _e, _diag = _run(tmp_path / "a")
    marks = _sidecar(out["sidecar_path"])["registration_marks"]
    assert all(m.get("placement") in (None, "original") for m in marks["marks"])
    target = [m for m in marks["marks"] if m["key"] == "left_bottom_h"][0]
    (ua, va), (ub, vb) = target["uv0"], target["uv1"]
    tag = FakeElement(2009, ANNO_CAT, owner_view_id=VIEW_ID,
                      bbox=_BBox((min(ua, ub) - 0.5, va - 0.5, 0), (max(ua, ub) + 0.5, va + 0.5, 0)))

    out2, _v, _d, _e, _diag = _run(tmp_path / "b", extra_elements=[tag])
    side = _sidecar(out2["sidecar_path"])["registration_marks"]
    moved = [m for m in side["marks"] if m["key"] == "left_bottom_h"][0]
    assert moved["placement"] == "moved" and moved["was_covered_by"] == [2009]
    assert moved["uv0"] != target["uv0"]
    assert side["layout"]["relocation"]["moved"] >= 1
    assert side["mark_avoidance"]["avoid_rects"] >= 1
    assert out2["registration"]["marks"]["created_count"] == 12


# --- a write failure on one sidecar reaches the OTHER sidecar's FILE ---------

def _write_faults(path):
    return [f for f in _sidecar(path)["capture_integrity"]["capture_faults"]
            if f.get("fault") == "registration_sidecar_write_failed"]


def test_a_failed_registration_marks_write_is_in_the_peer_sidecars_integrity(tmp_path, monkeypatch):
    """Codex, PR #221: when writing registration_marks into the ANNOTATION
    sidecar failed, the MODEL sidecar's capture_integrity was completed before
    the fault existed, so the surviving file called the capture clean."""
    real = registration.annotate_sidecar

    def _fail_annotation(path, key, payload):
        if path.endswith("_anno.json"):
            return "OSError: disk full"
        return real(path, key, payload)
    monkeypatch.setattr(registration, "annotate_sidecar", _fail_annotation)
    out, _v, _d, _e, _diag = _run(tmp_path, monkeypatch=monkeypatch)
    assert out["registration_success"] is False
    faults = _write_faults(out["sidecar_path"])
    assert len(faults) == 1 and "annotation sidecar" in faults[0]["message"]


def test_a_failed_integrity_completion_is_in_the_already_completed_peer(tmp_path, monkeypatch):
    """The model sidecar is completed FIRST; the annotation completion then
    fails. The model's file must be re-completed with that fault -- once."""
    real = registration.complete_capture_integrity

    def _fail_annotation(path, record):
        if path.endswith("_anno.json"):
            return "OSError: read-only"
        return real(path, record)
    monkeypatch.setattr(registration, "complete_capture_integrity", _fail_annotation)
    out, _v, _d, _e, _diag = _run(tmp_path, monkeypatch=monkeypatch)
    assert out["registration_success"] is False
    faults = _write_faults(out["sidecar_path"])
    assert len(faults) == 1 and "annotation sidecar" in faults[0]["message"]
    integrity = _sidecar(out["sidecar_path"])["capture_integrity"]
    assert integrity["completed_by"].count("registered_capture") == 1


def test_completing_an_integrity_record_twice_gives_the_second_faults_once(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"capture_integrity": {
        "capture_faults": [{"fault": "pass_own", "source": "annotation_pass"}],
        "completed_by": ["annotation_pass"]}}))
    rec = {"restore": {"rolled_back": True, "marks_still_in_project": []},
           "faults": [{"fault": "a"}]}
    assert registration.complete_capture_integrity(str(path), rec) is None
    rec["faults"].append({"fault": "b"})
    assert registration.complete_capture_integrity(str(path), rec) is None
    integrity = json.loads(path.read_text())["capture_integrity"]
    assert [f["fault"] for f in integrity["capture_faults"]] == ["pass_own", "a", "b"]
    assert integrity["completed_by"] == ["annotation_pass", "registered_capture"]



# --- ticks sized on the ACHIEVED model lattice ------------------------------

def _arm_px(out):
    frame = _sidecar(out["sidecar_path"])["frame"]
    fpp = frame["achieved_fpp_ft"]
    arms = []
    for seg in out["registration"]["marks"]["created"]:
        lo, hi = sorted(seg["span_uv"])
        arms.append((hi - lo) / fpp)
    return fpp, arms


def test_ticks_are_sized_on_the_capped_model_lattice(tmp_path):
    """Codex, PR #221: the ticks were sized at the REQUESTED dpi, so when the
    axis cap coarsened the model lattice a 32 px arm came out
    32 * achieved/requested px. They are now sized with the model pass's own
    lattice (composed, not copied), so the prediction must EQUAL what the
    model sidecar records, and every arm is >= 32 px on it."""
    def _cap(cfg):
        cfg.color_id_buffer_cap_axis_px = 400
    out, _v, _d, _e, _diag = _run(tmp_path, cfg_setup=_cap)
    frame = _sidecar(out["sidecar_path"])["frame"]
    assert frame["cap_applied"] is True, frame          # the cap really fired
    assert out["registration"]["mark_fpp_basis"] == "model_lattice"
    fpp, arms = _arm_px(out)
    # The prediction IS the model pass's lattice, and it is coarser than the
    # requested dpi's (96 / (12 * 150)), which is what the ticks used to use.
    assert out["registration"]["marks"]["layout"]["fpp_ft"] == frame["achieved_fpp_ft"]
    assert frame["achieved_fpp_ft"] > 96.0 / (12.0 * 150.0)
    assert min(arms) >= registration.MARK_MIN_ARM_PX - 1e-6, arms


def test_control_uncapped_ticks_are_unchanged(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    frame = _sidecar(out["sidecar_path"])["frame"]
    assert frame["cap_applied"] is False
    _fpp, arms = _arm_px(out)
    assert out["registration"]["marks"]["layout"]["fpp_ft"] == frame["achieved_fpp_ft"]
    assert min(arms) >= registration.MARK_MIN_ARM_PX - 1e-6, arms


# --- Codex, PR #226: a tick that did not TAKE the chosen style ----------------
#
# The chosen style's visibility is what the capture checks, so a tick that kept
# its default -- possibly the hidden one -- passed on the chosen style's word.
# Each tick's style is now read back after the assignment. Mutation: dropping
# the read-back (applied = the assignment did not raise) turns "ignored" red;
# dropping the fault turns both red.

def _one_tick_keeps_its_default(monkeypatch, mode):
    real = world._Create.NewDetailCurve

    def _new(self, view, line):
        elem = real(self, view, line)
        if self.calls != 1:
            return elem
        default = elem.LineStyle

        def _set(obj, value):
            if mode == "raises":
                raise RuntimeError("LineStyle refused (fake)")
        elem.__class__ = type("_StickyCurve", (type(elem),),
                              {"LineStyle": property(lambda obj: default, _set)})
        return elem
    monkeypatch.setattr(world._Create, "NewDetailCurve", _new)


def _style_faults(path):
    return [f for f in _sidecar(path)["capture_integrity"]["capture_faults"]
            if f.get("fault") == "registration_mark_style_not_applied"]


@pytest.mark.parametrize("mode", ["ignored", "raises"])
def test_a_tick_that_keeps_its_default_style_is_a_fault_in_the_FILE(
        tmp_path, monkeypatch, mode):
    _one_tick_keeps_its_default(monkeypatch, mode)
    out, _view, _doc, _e, diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        faults = _style_faults(path)
        assert len(faults) == 1, path
    created = out["registration"]["marks"]["created"]
    assert [m["line_style_applied"] for m in created].count(False) == 1
    assert out["registration_success"] is False
    assert any(w.get("callsite") == "tick_line_style_apply" for w in diag.warnings)


def test_control_every_tick_takes_the_style_and_nothing_faults(tmp_path):
    out, _view, _doc, _e, diag = _run(tmp_path)
    assert _style_faults(out["sidecar_path"]) == []
    assert all(m["line_style_applied"] is True
               for m in out["registration"]["marks"]["created"])
    assert not any(w.get("callsite") == "tick_line_style_apply" for w in diag.warnings)


# --- the crop LOOP (2026-10-12) ------------------------------------------------------

def _rotated_loop(view, theta_deg, w=50.0, h=40.0):
    """A w x h rectangle rotated theta inside the fake crop (20,15)-(80,60):
    its bounding box is 50cos+40sin by 50sin+40cos, within the crop."""
    import math
    cu, cv = 50.0, 37.5
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    view.crop_loop = [(cu + du * c - dv * s, cv + du * s + dv * c)
                      for du, dv in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def test_control_an_ordinary_crops_loop_leaves_the_ticks_on_the_crop_box(tmp_path):
    out, view, doc, exports, diag = _run(tmp_path)
    reg = out["registration"]
    assert reg["crop_shape"]["state"] == "value"
    assert reg["crop_shape"]["classification"]["axis_aligned"] is True
    assert reg["crop_shape"]["reference"] == "crop_box"
    assert reg["mark_reference_source"] == "authored_crop"
    assert reg["marks"]["layout"]["reference_uv"] == [20.0, 15.0, 80.0, 60.0]
    assert reg["ticks_outside_crop_loop"] == [] and reg["faults"] == []
    for path in (out["sidecar_path"], out["annotation_pass"]["sidecar_path"]):
        on_disk = _sidecar(path)["registration_marks"]
        assert on_disk["crop_shape"]["reference"] == "crop_box"
        assert on_disk["ticks_outside_crop_loop"] == []


def test_a_rotated_crop_loop_puts_the_ticks_inside_it_in_the_FILE(tmp_path):
    """3939979's shape on the fake: the loop rotated 12.91 deg inside the
    crop box. The ticks go on the largest axis-aligned rectangle inside the
    loop, none lies outside it, no fault; the sidecars say so."""
    out, view, doc, exports, diag = _run(
        tmp_path, view_setup=lambda v: _rotated_loop(v, 12.91))
    reg = out["registration"]
    shape = reg["crop_shape"]
    assert shape["state"] == "value" and shape["loop_count"] == 1
    assert shape["classification"]["rectangular"] is True
    assert shape["classification"]["axis_aligned"] is False
    assert abs(shape["classification"]["rotation_deg"] - 12.91) < 1e-3
    assert shape["reference"] == "crop_loop_inscribed"
    assert reg["mark_reference_source"] == "crop_loop_inscribed"
    ref = reg["marks"]["layout"]["reference_uv"]
    assert ref == shape["inscribed_uv"]
    assert 20.0 < ref[0] < ref[2] < 80.0 and 15.0 < ref[1] < ref[3] < 60.0
    assert reg["marks"]["created_count"] == 12
    assert reg["ticks_outside_crop_loop"] == []
    assert [f["fault"] for f in reg["faults"]] == []
    # Every created tick's readback lies inside the loop.
    for mark in reg["marks"]["created"]:
        for u, v in mark["readback_uv"]:
            assert registration.point_in_loop(view.crop_loop, u, v), mark["key"]
    for path in (out["sidecar_path"], out["annotation_pass"]["sidecar_path"]):
        on_disk = _sidecar(path)["registration_marks"]
        assert on_disk["crop_shape"]["reference"] == "crop_loop_inscribed"
        assert on_disk["reference_source"] == "crop_loop_inscribed"
        assert on_disk["layout"]["reference_uv"] == ref


def test_the_shipped_placement_on_a_rotated_loop_would_have_faulted(tmp_path, monkeypatch):
    """Mutation of the fix: with the loop's reference refused, the ticks go
    back on the crop box and the pre-export check names all twelve."""
    monkeypatch.setattr(registration, "mark_reference_from_loop",
                        lambda loop, crop, tol: (None, {"reference": "none",
                                                        "classification": {},
                                                        "reason": "forced"}))
    out, view, doc, exports, diag = _run(
        tmp_path, view_setup=lambda v: _rotated_loop(v, 12.91))
    reg = out["registration"]
    assert reg["marks"]["layout"]["reference_uv"] == [20.0, 15.0, 80.0, 60.0]
    # On the fake's 60 x 45 ft crop one mid tick happens to sit near the
    # loop's tangent point (the shape the run's partial refusals had:
    # mid_bottom_v found on 7 of 8); every corner tick is outside.
    outside = reg["ticks_outside_crop_loop"]
    assert len(outside) >= 11
    assert all(k in outside for k in ("left_bottom_h", "left_bottom_v", "right_bottom_h",
                                      "right_bottom_v", "left_top_h", "left_top_v",
                                      "right_top_h", "right_top_v"))
    names = [f["fault"] for f in reg["faults"]]
    message = [f["message"] for f in reg["faults"] if f["fault"] == "registration_marks_outside_crop_loop"][0]
    assert message.startswith("{0} of 12 ticks lie outside".format(len(outside)))
    assert "registration_marks_outside_crop_loop" in names
    assert "registration_marks_no_room_in_crop_loop" in names
    for path in (out["sidecar_path"], out["annotation_pass"]["sidecar_path"]):
        on_disk = _sidecar(path)
        assert "registration_marks_outside_crop_loop" in [
            f["fault"] for f in on_disk["registration_marks"]["faults"]]
        assert "registration_marks_outside_crop_loop" in [
            f["fault"] for f in on_disk["capture_integrity"]["capture_faults"]]


def test_a_loop_that_will_not_read_is_a_fault_and_the_ticks_stay_on_the_box(tmp_path):
    def _refuse(view):
        view._manager.GetCropShape = lambda: (_ for _ in ()).throw(RuntimeError("no shape (fake)"))
    out, view, doc, exports, diag = _run(tmp_path, view_setup=_refuse)
    reg = out["registration"]
    assert reg["crop_shape"]["state"] == "unavailable"
    assert "no shape (fake)" in reg["crop_shape"]["reason"]
    assert reg["marks"]["layout"]["reference_uv"] == [20.0, 15.0, 80.0, 60.0]
    assert [f["fault"] for f in reg["faults"]] == ["crop_shape_unverified"]
    assert "ticks_outside_crop_loop" not in reg


def test_a_crop_inactive_views_stored_loop_is_not_read_and_the_ticks_stay_on_crop_A(tmp_path):
    """Codex, PR #236: with the crop inactive the reference is crop A, which
    both passes activate as the box; the view's stored (here rotated) loop
    is not what the export renders, so it must not move the ticks."""
    out, view, doc, exports, diag = _run(
        tmp_path, crop_active=False, view_setup=lambda v: _rotated_loop(v, 12.91))
    reg = out["registration"]
    assert reg["mark_reference_source"] == "model_crop_a"
    assert reg["crop_shape"]["state"] == "not_read"
    assert "model_crop_a" in reg["crop_shape"]["reason"]
    assert "ticks_outside_crop_loop" not in reg
    assert not [f for f in reg["faults"] if f["fault"].startswith("crop_shape")]
    assert reg["marks"]["created_count"] == 12 and out["success"] is True


def test_a_recapture_is_refused_when_the_loop_cannot_be_verified(tmp_path):
    """Codex, PR #236: the refusal guard needs the loop; without it the old
    capture must not be replaced."""
    from vop_interwoven import recapture as rc

    def _setup(view):
        view._manager.GetCropShape = lambda: (_ for _ in ()).throw(RuntimeError("no shape (fake)"))

    def _in_recapture(cfg):
        cfg._recapture_record = {"schema": rc.RECAPTURE_SCHEMA, "captured_at": "x",
                                 "exporter_version": "1", "reason": rc.RECAPTURE_REASON,
                                 "view_ids": [VIEW_ID], "replaces_exporter_version": "0"}
    out, view, doc, exports, diag = _run(tmp_path, view_setup=_setup, cfg_setup=_in_recapture)
    assert out["success"] is False and out["failure_reason"] == "recapture_refused"
    assert exports == [] and out["sidecar_path"] is None
    names = [f["fault"] for f in out["registration"]["faults"]]
    assert "crop_shape_unverified" in names and "recapture_refused" in names
    assert out["registration"]["restore"]["rolled_back"] is True
