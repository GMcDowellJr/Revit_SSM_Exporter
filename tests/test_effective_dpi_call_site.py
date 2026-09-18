"""The CALL SITE's arguments, exercised rather than asserted about.

WHY THIS FILE EXISTS
--------------------
PR #202 first bound the achieved-dpi FORMULA to production by extracting
resolution_contract.effective_export_dpi(). Review then pointed out that this
left the ARGUMENT unbound, and it was right: with the extraction in place, the
original grid-extent defect could be fully reinstated at the call site --

    effective_export_dpi = _effective_export_dpi(raster.bounds_xy_tuple, ...)

-- and all 981 tests stayed GREEN. Verified by mutation before this file was
written. The reasons none of them noticed:

  * the numerical properties call the helper themselves with test-supplied
    crop bounds, so they never see production's argument;
  * a source-text check for "_effective_export_dpi(" still matches;
  * the signature check cannot help, because the grid rectangle and the render
    crop are both four-value tuples.

So this drives export_color_id_buffer_view itself, with a raster whose GRID is
deliberately wider than the rendered CROP, and asserts the reported dpi
describes the crop. Substituting the grid at the call site fails it.

THE HARNESS GAP THIS CLOSES
---------------------------
The shared fake Autodesk.Revit.DB (tests/test_color_id_buffer_shadow_
suppression.py) has no XYZ and no BoundingBoxXYZ, so crop_box_from_uv_bounds()
raises ImportError, the crop is never applied, and every existing end-to-end
test runs with bounds_xy=None. That is why nothing reached this call site
before. The two value types are added HERE, onto the already-installed fake
module, rather than into the shared harness -- this file needs them, and
widening the shared fake would change what every other test exercises.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.config import Config  # noqa: E402
from vop_interwoven.core.math_utils import Bounds2D  # noqa: E402
from vop_interwoven.revit.view_basis import ViewBasis  # noqa: E402
from vop_interwoven.resolution_contract import effective_export_dpi  # noqa: E402
import vop_interwoven.color_id_buffer as cib  # noqa: E402

from tests.test_color_id_buffer_shadow_suppression import (  # noqa: E402
    _FakeDiag as _HarnessDiag,
    _FakeView,
    _install_fake_revit_db,
)
from tests.test_stage_a_export_dimension_contract import _SizedDoc  # noqa: E402


class _XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _BBoxXYZ(object):
    def __init__(self):
        self.Min = _XYZ(0.0, 0.0, 0.0)
        self.Max = _XYZ(0.0, 0.0, 0.0)
        self.Transform = None


class _CropBox(object):
    """Identity-transform crop box. Depth is copied from it unchanged by
    crop_box_from_uv_bounds(), so Min.Z/Max.Z just have to exist."""

    def __init__(self):
        self.Min = _XYZ(0.0, 0.0, -100.0)
        self.Max = _XYZ(0.0, 0.0, 100.0)
        self.Transform = None


class _CroppableView(_FakeView):
    def __init__(self, view_id):
        _FakeView.__init__(self, view_id)
        self.CropBox = _CropBox()
        self.Scale = 96


class _Raster(object):
    """Grid rectangle WIDER than the render crop, which is the whole point:
    compute_model_crop() intersects model_clip_bounds with bounds_xy, so the
    TIFF spans the narrower rectangle while W/H/cell still describe the grid.
    If the two were equal this test could not tell them apart."""

    def __init__(self, grid, crop, cell_size_ft=1.0):
        self.bounds_xy = grid
        self.model_clip_bounds = crop
        self.W = int(round((grid.xmax - grid.xmin) / cell_size_ft))
        self.H = int(round((grid.ymax - grid.ymin) / cell_size_ft))
        self.cell_size_ft = cell_size_ft
        self.view_basis = ViewBasis((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                    (0.0, 1.0, 0.0), (0.0, 0.0, -1.0))


GRID = Bounds2D(0.0, 0.0, 100.0, 100.0)
CROP = Bounds2D(0.0, 0.0, 80.0, 80.0)


def _install_geometry_types():
    """Add XYZ/BoundingBoxXYZ to the already-installed fake DB module."""
    db = sys.modules["Autodesk.Revit.DB"]
    db.XYZ = _XYZ
    db.BoundingBoxXYZ = _BBoxXYZ


def _run(tmp_path):
    cfg = Config(cell_size_paper_in=0.125)
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = _HarnessDiag()
    doc = _SizedDoc(lambda px: (px, px))
    result = cib.export_color_id_buffer_view(
        doc, _CroppableView(view_id=900), elements=[], cfg=cfg, diag=diag,
        raster=_Raster(GRID, CROP), elem_cache=None)
    return result


def test_the_crop_path_is_actually_reached(tmp_path):
    """Guard on the guard. Every assertion below is vacuous if the crop is
    never applied -- which is exactly the state every other end-to-end test in
    this repo runs in, because the fake DB lacks the two geometry types."""
    with _install_fake_revit_db():
        _install_geometry_types()
        result = _run(tmp_path)
    bounds = result["metadata"].get("bounds_xy")
    assert bounds is not None, (
        "the crop was not applied, so this file proves nothing; check that "
        "XYZ/BoundingBoxXYZ reached the fake Autodesk.Revit.DB")
    assert bounds[2] - bounds[0] == pytest.approx(CROP.xmax - CROP.xmin)


def test_reported_dpi_describes_the_rendered_crop_not_the_grid(tmp_path):
    """THE BINDING. Production must pass the RENDER CROP.

    The grid is 100 ft where the crop is 80, so the two answers differ by 25%
    -- far outside any rounding -- and the grid answer is the lower one, which
    is what made the original defect read as a plausible dpi rather than as
    nonsense.
    """
    with _install_fake_revit_db():
        _install_geometry_types()
        result = _run(tmp_path)

    res = result["metadata"]["resolution"]
    reported = res["effective_export_dpi"]
    assert reported is not None

    aw, ah = res["actual_w"], res["actual_h"]
    scale = float(res["view_scale"])
    from_crop = effective_export_dpi(
        (CROP.xmin, CROP.ymin, CROP.xmax, CROP.ymax), aw, ah, scale)
    from_grid = effective_export_dpi(
        (GRID.xmin, GRID.ymin, GRID.xmax, GRID.ymax), aw, ah, scale)

    assert from_crop != pytest.approx(from_grid), (
        "fixture does not discriminate: the grid and crop give the same dpi")
    assert reported == pytest.approx(from_crop, rel=1e-9), (
        "reported {0} matches the GRID ({1}), not the rendered crop ({2}) -- "
        "the call site is passing the wrong rectangle".format(
            reported, from_grid, from_crop))


def test_reported_dpi_matches_what_a_decoder_derives_from_the_sidecar(tmp_path):
    """End to end, the sidecar must be self-consistent: the dpi it reports and
    the feet_per_pixel a decoder derives from its own "bounds_xy" must be the
    same statement. This is the identity the property tests assert in the
    abstract, checked here against a real emitted sidecar."""
    with _install_fake_revit_db():
        _install_geometry_types()
        result = _run(tmp_path)

    meta = result["metadata"]
    res = meta["resolution"]
    bounds = meta["bounds_xy"]
    aw, ah = res["actual_w"], res["actual_h"]
    scale = float(res["view_scale"])

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from clamp_pad_geometry import clamp_pad_geometry

    fpp, _px, _py = clamp_pad_geometry(bounds, aw, ah, measured_w=aw, measured_h=ah)
    assert res["effective_export_dpi"] == pytest.approx(
        scale / (12.0 * fpp), rel=1e-9)
