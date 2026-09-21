"""Stage A step 3 -- the annotation raster pass, over frame B.

WHAT THIS FILE IS REALLY FOR. Step 3's promise is not "an annotation TIFF
exists"; it is that the annotation TIFF and the model TIFF REGISTER, and that
the model TIFF is not degraded to get there. Both are claims about two
captures at once, so every gate here drives BOTH call sites in one test and
compares them -- testing either alone binds it to its own copy of the
lattice, which is the defect class CLAUDE.md records twice over.

The model pass is run first, its frame_export_geometry() result is caught
through ``geometry_out``, and that exact object is handed to the annotation
pass. That is production's wiring, not the test's: if a future change makes
the annotation pass size itself independently, the registration assertions
below come apart.

CONTROLS. ``test_the_two_passes_render_different_rectangles`` pins that A and
B actually differ in this fixture. Without it every registration assertion
would also pass against a fixture where the model crop IS the frame, which
is the degenerate case that proves nothing.
"""
import json
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

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

VIEW_ID = 77
# B is the annotation-expanded frame; A is the narrower model crop inside it.
# They are deliberately unequal -- see the control at the bottom.
FRAME_BOUNDS = Bounds2D(0.0, 0.0, 120.0, 90.0)
MODEL_BOUNDS = Bounds2D(20.0, 15.0, 80.0, 60.0)
CELL = 1.0

MODEL_CAT = FakeCategory("Walls", 10, cat_type="Model")
ANNO_CAT = FakeCategory("Door Tags", -2000460, cat_type="Annotation")
# The known gap this pass does NOT close: OST_Lines is Model-typed but is on
# VIEW_ONLY_MODEL_BIC_NAMES, so neither pass hides it.
LINES_CAT = FakeCategory("Lines", -2000051, cat_type="Model")


def _write_tiff_header(path, width, height):
    """A minimal little-endian TIFF the dimension check can read."""
    entries = [(256, 3, 1, width), (257, 3, 1, height)]
    header = struct.pack("<2sHI", b"II", 42, 8)
    body = struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        body += struct.pack("<HHIHH", tag, typ, count, value, 0)
    body += struct.pack("<I", 0)
    with open(path, "wb") as f:
        f.write(header + body)


class _SizedDoc(FakeDoc):
    """ExportImage writes a TIFF whose header says exactly what was asked."""

    def __init__(self, **kw):
        FakeDoc.__init__(self, **kw)
        self.exported = []

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        px = int(opts.PixelSize)
        out_dir = os.path.dirname(opts.FilePath)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        # Square-ish stand-in: the fitted axis is what the check compares.
        _write_tiff_header(opts.FilePath + ".tiff", px, px)
        self.exported.append({"pixel_size": px, "path": opts.FilePath})


def _raster():
    return types.SimpleNamespace(
        W=max(1, int(math.ceil(FRAME_BOUNDS.width() / CELL))),
        H=max(1, int(math.ceil(FRAME_BOUNDS.height() / CELL))),
        cell_size_ft=CELL,
        bounds_xy=FRAME_BOUNDS,
        model_clip_bounds=MODEL_BOUNDS,
        anno_frame_bounds=None,
        anno_cap_envelope_applied=False,
        view_basis=_PLAN_BASIS,
    )


def _elements():
    """Three model elements and three annotations, interleaved.

    OwnerViewId is what separates them -- not the category, which is why the
    detail line below carries a MODEL-typed category and still belongs to the
    annotation pass.
    """
    return [
        FakeElement(1001, MODEL_CAT),                                  # model
        FakeElement(2001, ANNO_CAT, owner_view_id=VIEW_ID),            # anno
        FakeElement(1002, MODEL_CAT),                                  # model
        FakeElement(2002, ANNO_CAT, owner_view_id=VIEW_ID),            # anno
        FakeElement(1003, MODEL_CAT),                                  # model
        FakeElement(2003, LINES_CAT, owner_view_id=VIEW_ID),           # detail line
    ]


def _model_pass_elements(elements):
    """What collect_view_elements would actually hand the model pass.

    NOT the same list the annotation pass gets, and the difference is
    production's, not this fixture's. revit/collection_policy.py's
    should_include_element includes an element only when its category is
    CategoryType.Model, and separately excludes view-specific lines -- so an
    annotation never reaches the model pass to begin with. Handing both
    passes one unfiltered list would invent an overlap that production does
    not have, and then test assertions against it.
    """
    return [e for e in elements
            if getattr(e.Category, "CategoryType", None) == "Model"
            and int(e.OwnerViewId.IntegerValue) == -1]


def _run_both_passes(tmp_path, elements=None, view=None):
    """Run the model pass, then the annotation pass on ITS geometry.

    Returns (model_result, anno_result, geom, doc, view, diag).
    """
    elements = _elements() if elements is None else elements
    view = FakeViewPlan(view_id=VIEW_ID) if view is None else view
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    geom = {}

    with install_fake_revit_db():
        model_result = color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=_model_pass_elements(elements), cfg=cfg, diag=diag,
            raster=_raster(), elem_cache=None, geometry_out=geom,
        )
        # The annotation pass gets the WHOLE view and splits it itself: its
        # membership rule is OwnerViewId, not whatever a collector handed it.
        anno_result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, geom, diag=diag, raster=_raster(), elements=elements,
        )
    return model_result, anno_result, geom, doc, view, diag


# --- registration: the two captures share one lattice ----------------------

def test_the_annotation_pass_renders_frame_b_at_the_model_passs_fpp(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    reg = anno_result["metadata"]["registration"]
    # B, whole, on the lattice the model pass established.
    assert reg["frame_snapped_uv"] == [float(v) for v in geom["frame_snapped_uv"]]
    assert reg["frame_px"] == [int(v) for v in geom["frame_px"]]
    assert reg["achieved_fpp_ft"] == pytest.approx(geom["achieved_fpp_ft"])
    # And that is what was actually handed to Revit, not merely recorded.
    assert reg["rendered_uv"] == [float(v) for v in geom["frame_snapped_uv"]]


def test_the_model_image_sits_inside_the_annotation_image_at_an_integer_offset(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    reg = anno_result["metadata"]["registration"]
    off_u, off_v = reg["model_crop_offset_px"]
    crop_u, crop_v = reg["model_crop_px"]
    frame_u, frame_v = reg["frame_px"]

    # Integer, in range, and A fits inside B. This is decision A's whole
    # claim: overlay by translation, no resampling.
    assert isinstance(off_u, int) and isinstance(off_v, int)
    assert 0 <= off_u and 0 <= off_v
    assert off_u + crop_u <= frame_u
    assert off_v + crop_v <= frame_v


def test_the_two_exports_are_the_pixel_sizes_the_shared_lattice_predicts(tmp_path):
    """Exercises BOTH call sites' arguments, not just the shared formula.

    A signature check cannot tell frame_px from crop_px -- both are
    two-value tuples of ints. So the fixture is built so they DIFFER, and
    the assertion is that each pass asked Revit for its own one.
    """
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    assert len(doc.exported) == 2
    model_px, anno_px = doc.exported[0]["pixel_size"], doc.exported[1]["pixel_size"]

    assert model_px == int(geom["crop_px"][0])     # A, fitted on width
    assert anno_px == int(geom["frame_px"][0])     # B, fitted on width
    assert anno_px > model_px


def test_the_two_passes_render_different_rectangles(tmp_path):
    """CONTROL. Without this, every assertion above also passes on a fixture
    where A IS B -- the degenerate case that discriminates nothing."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    assert geom["crop_is_frame"] is False
    assert geom["crop_px"] != geom["frame_px"]
    assert tuple(geom["crop_offset_px"]) != (0, 0)


# --- membership is OwnerViewId, and it reaches the paint -------------------

def test_only_owner_view_elements_are_painted_by_the_annotation_pass(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    md = anno_result["metadata"]
    painted = set(int(k) for k in md["color_assignment_map"])
    assert painted == {2001, 2002, 2003}
    assert md["membership"]["membership_rule"] == "OwnerViewId"
    assert md["membership"]["annotation_count"] == 3
    assert md["membership"]["model_count"] == 3
    assert md["membership"]["unresolved_count"] == 0


def test_a_model_typed_category_does_not_override_owner_view_membership(tmp_path):
    """The detail line carries OST_Lines, a MODEL-typed category, and is
    still an annotation-pass member. Category type cannot answer this."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    assert "2003" in anno_result["metadata"]["color_assignment_map"]


# --- the two passes' palettes are independent ------------------------------

def test_each_pass_carries_its_own_colour_map(tmp_path):
    """Per-pass palettes (Greg, 2026-09-21). The same RGB may appear in both
    maps: they are never decoded against each other."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    model_map = model_result["metadata"]["color_assignment_map"]
    anno_map = anno_result["metadata"]["color_assignment_map"]

    # Disjoint ELEMENTS -- that is the guarantee, and it holds because the
    # two passes are fed by different rules: the model pass by
    # collection_policy (CategoryType.Model only), this one by OwnerViewId.
    assert set(model_map) & set(anno_map) == set()
    assert set(model_map) == {"1001", "1002", "1003"}
    assert set(anno_map) == {"2001", "2002", "2003"}
    # Colours are allowed to COINCIDE across the two maps -- both passes
    # start from the same palette and neither is ever decoded against the
    # other's map. Asserted rather than left implicit so a future "make them
    # globally unique" change has to be a decision, not a silent halving of
    # the per-view capacity Greg chose separate palettes to protect.
    assert [list(v) for v in anno_map.values()][0] in [list(v) for v in model_map.values()]


# --- the model TIFF is not degraded ---------------------------------------

def test_the_model_pass_still_hides_every_annotation_category(tmp_path):
    """Step 3's hard constraint: no annotation pixels over model content."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    hidden = model_result["metadata"]["categories_hidden"]
    assert str(ANNO_CAT.Id.IntegerValue) in hidden or ANNO_CAT.Id.IntegerValue in hidden


def test_the_annotation_pass_hides_model_categories_and_not_annotation_ones(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    hidden = anno_result["metadata"]["categories_hidden"]
    assert MODEL_CAT.Id.IntegerValue in hidden
    assert ANNO_CAT.Id.IntegerValue not in hidden
    # The documented gap: OST_Lines is left visible, because hiding it would
    # take the detail lines with it. Pinned so it stays KNOWN, not silent.
    assert LINES_CAT.Id.IntegerValue not in hidden


def test_the_two_passes_hide_disjoint_category_sets(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    model_hidden = set(int(k) for k in model_result["metadata"]["categories_hidden"])
    anno_hidden = set(int(k) for k in anno_result["metadata"]["categories_hidden"])
    assert model_hidden & anno_hidden == set()


# --- restore is VERIFIED BY READING BACK ----------------------------------

def test_restore_is_verified_by_reading_the_overrides_back(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    check = anno_result["metadata"]["override_restore_check"]
    assert check["status"] == "value"
    assert check["method"] == "read_back"
    assert check["painted_count"] == 3
    assert check["verified_cleared_count"] == 3
    assert check["still_set_count"] == 0
    assert check["unreadable_count"] == 0
    assert anno_result["success"] is True


def test_an_override_left_behind_fails_the_capture(tmp_path):
    """The read-back is not decoration.

    A view whose SetElementOverrides silently keeps the paint on one element
    -- the curtain-panel bug's exact shape -- must come back as a FAILED
    capture, not a successful one with a note.
    """
    class _StickyView(FakeViewPlan):
        def SetElementOverrides(self, eid, ogs):
            if int(eid.IntegerValue) == 2002 and not getattr(ogs, "calls", None):
                # A blank OGS (the restore) is ignored for this element; the
                # painted one stays. Exactly "no exception, still painted".
                return
            FakeViewPlan.SetElementOverrides(self, eid, ogs)

        def GetElementOverrides(self, eid):
            ogs = FakeViewPlan.GetElementOverrides(self, eid)
            if int(eid.IntegerValue) == 2002:
                ogs.ProjectionLineColor = types.SimpleNamespace(IsValid=True)
            return ogs

    _m, anno_result, _g, _d, _v, diag = _run_both_passes(
        tmp_path, view=_StickyView(view_id=VIEW_ID))

    check = anno_result["metadata"]["override_restore_check"]
    assert check["still_set_count"] == 1
    assert check["still_set"][0]["element_id"] == 2002
    assert "projection_line_color" in check["still_set"][0]["reason"]
    assert anno_result["success"] is False
    assert anno_result["failure_reason"] == "annotation_overrides_not_restored"
    assert any(e["callsite"] == "verify_annotation_overrides_restored"
               for e in diag.errors)


def test_an_unreadable_override_is_neither_cleared_nor_still_set(tmp_path):
    """Three-valued. A member that cannot be read does not certify a clear."""
    class _BlindView(FakeViewPlan):
        def GetElementOverrides(self, eid):
            ogs = FakeViewPlan.GetElementOverrides(self, eid)
            if int(eid.IntegerValue) == 2001:
                class _Boom(object):
                    @property
                    def ProjectionLineColor(self):
                        raise RuntimeError("cannot read")

                    def __getattr__(self, name):
                        return None
                return _Boom()
            return ogs

    _m, anno_result, _g, _d, _v, diag = _run_both_passes(
        tmp_path, view=_BlindView(view_id=VIEW_ID))

    check = anno_result["metadata"]["override_restore_check"]
    assert check["unreadable_count"] == 1
    assert check["unreadable"][0]["element_id"] == 2001
    # Not counted as cleared, and not counted as still-set.
    assert check["verified_cleared_count"] == 2
    assert check["still_set_count"] == 0


# --- refusal ---------------------------------------------------------------

def test_the_annotation_pass_refuses_without_the_model_passs_geometry(tmp_path):
    """No lattice, no capture. A capture that cannot register is worse than
    none, because nothing downstream can tell the two apart."""
    elements = _elements()
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT])
    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()

    with install_fake_revit_db():
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, FakeViewPlan(view_id=VIEW_ID), cfg, {}, diag=diag,
            raster=_raster(), elements=elements,
        )

    assert result["success"] is False
    assert result["failure_reason"] == "annotation_pass_no_export_geometry"
    assert result["tiff_path"] is None
    assert doc.export_image_calls == []
    assert any(e["callsite"] == "annotation_pass_geometry" for e in diag.errors)


# --- outputs do not collide ------------------------------------------------

def test_the_two_passes_write_separate_files(tmp_path):
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)

    assert model_result["tiff_path"] != anno_result["tiff_path"]
    assert model_result["sidecar_path"] != anno_result["sidecar_path"]
    assert os.path.exists(anno_result["sidecar_path"])
    assert os.path.exists(model_result["sidecar_path"])
    # The annotation sidecar names its companion rather than leaving a reader
    # to reconstruct the path from a suffix convention.
    with open(anno_result["sidecar_path"]) as f:
        sidecar = json.load(f)
    assert sidecar["model_pass_tiff_path"] == model_result["tiff_path"]
    assert sidecar["pass"] == "annotation"
    assert sidecar["schema"] == color_id_buffer.ANNOTATION_PASS_SCHEMA


def test_the_unconfirmed_api_claims_ride_in_the_sidecar(tmp_path):
    """They are load-bearing for this pass and must not live only in a commit
    message: a capture has to be explicable from its own sidecar."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    claims = anno_result["metadata"]["unconfirmed_api_claims"]
    assert any("annotation crop" in c for c in claims)
    assert any("revision clouds" in c for c in claims)


# --- geometry_out is an out-parameter, not a return-shape change -----------

def test_geometry_out_does_not_change_the_model_passs_return_shape(tmp_path):
    """streaming.py copies every key of that dict into full_results, which is
    serialised. geom holds tuples and is not a data product."""
    model_result, anno_result, geom, doc, view, diag = _run_both_passes(tmp_path)
    assert "geometry_out" not in model_result
    assert not any("geom" in k for k in model_result)
    json.dumps({k: v for k, v in model_result.items() if k != "metadata"})


def test_the_model_pass_still_runs_without_a_geometry_out(tmp_path):
    """CONTROL: the parameter is optional and its absence changes nothing."""
    elements = _elements()
    doc = _SizedDoc(elements=elements, link_instances=[],
                    categories=[MODEL_CAT, ANNO_CAT, LINES_CAT])
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    with install_fake_revit_db():
        result = color_id_buffer.export_color_id_buffer_view(
            doc, FakeViewPlan(view_id=VIEW_ID), elements=elements, cfg=cfg,
            diag=FakeDiag(), raster=_raster(), elem_cache=None,
        )
    assert result["success"] is True
