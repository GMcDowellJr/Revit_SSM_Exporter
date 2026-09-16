"""
Stage A edge-drift onset probe (experiments D1-D5) for Revit 2025 / Dynamo 3.3.

Extraction only. This probe drives Revit, exports TIFFs, and writes a sidecar
JSON describing each export. It computes no image statistics, reaches no
conclusion, and changes nothing in the Stage A pipeline: palette generation,
the paint step, and the export defaults are imported from
``vop_interwoven.color_id_buffer`` rather than reimplemented here, so a capture
made by this probe is painted exactly the way production paints one.

All pixel analysis belongs to ``tools/analyze_stage_a_probe.py --export-metrics``,
which consumes the ``exports`` list this probe emits.

Every mutation happens inside a single TransactionGroup that is always rolled
back, and the pre/post state is compared so a failed rollback is visible rather
than assumed.

Dynamo inputs:
    IN[0] = target Revit view
    IN[1] = output directory
    IN[2] = case selection: "all" or a comma-separated subset of
            d1_determinism, d2_category_load, d3_size_sweep,
            d4_dpi_vs_pixel_size, d5_crop_tiles          (default "all")
    IN[3] = repetitions for D1                            (default 2)
    IN[4] = D3 pixel sizes: "native,10000,12000,15000" or a list
                                                          (default that list)
    IN[5] = export DPI used for the native-density calculation
                                                          (default 150.0)
    IN[6] = D2 step count: how many category buckets to unhide
                                                          (default 8)
    IN[7] = D5 tile grid: N for an NxN crop tiling         (default 2)
    IN[8] = max painted elements, 0/None for no limit      (default None)
    IN[9] = repository root containing vop_interwoven      (default: autodetect)

UNCONFIRMED Revit API assumptions this probe encodes (see the module doc in
tests/dynamo/PROBE_STAGE_A_DRIFT_ONSET.md): ImageExportOptions exposes no DPI
concept independent of PixelSize; ZoomFitType.FitToPage with
FitDirectionType.Horizontal makes PixelSize the output *width*; Revit clamps
export aspect to 10:1 by padding the short axis. Each is recorded in the
report as a prediction next to what the run actually produced, never asserted.
"""
from __future__ import absolute_import

import hashlib
import json
import os
import re
import sys
import time
import traceback


PROBE_NAME = "stage_a_drift_onset"
PROBE_VERSION = 1

CASES = (
    "d1_determinism",
    "d2_category_load",
    "d3_size_sweep",
    "d4_dpi_vs_pixel_size",
    "d5_crop_tiles",
)

DEFAULT_EXPORT_DPI = 150.0
DEFAULT_REPETITIONS = 2
DEFAULT_SIZE_SWEEP = ("native", 10000, 12000, 15000)
DEFAULT_D2_STEPS = 8
DEFAULT_TILE_GRID = 2
# The ceiling production already applies (color_id_buffer.MAX_STAGE_A_PIXEL_SIZE);
# D4 lowers DPI until native density fits under it instead of clamping.
D4_NATIVE_CEILING = 15000

_PROBE_CONTRACT = None


# --------------------------------------------------------------------------
# Repository / contract bootstrap (pasted Dynamo nodes have no __file__)
# --------------------------------------------------------------------------

def _candidate_roots(repo_root=None, output_dir=None):
    seen = []

    def add(path):
        if not path:
            return
        try:
            path = os.path.abspath(os.path.expanduser(str(path)))
        except Exception:
            return
        if os.path.isfile(path):
            path = os.path.dirname(path)
        cur = path
        for _ in range(9):
            if cur not in seen:
                seen.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    add(repo_root)
    for env_name in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        try:
            add(os.environ.get(env_name))
        except Exception:
            pass
    add(output_dir)
    try:
        add(os.getcwd())
    except Exception:
        pass
    try:
        add(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        # Expected when this file is pasted into a Dynamo Python node.
        pass
    home = os.path.expanduser("~")
    for tail in (("Documents", "Revit_SSM_Exporter"),
                 ("Documents", "GitHub", "Revit_SSM_Exporter"),
                 ("source", "repos", "Revit_SSM_Exporter"),
                 ("Revit_SSM_Exporter",)):
        add(os.path.join(home, *tail))
    return seen


def _add_repo_root_to_path(repo_root=None, output_dir=None):
    for root in _candidate_roots(repo_root, output_dir):
        if not (os.path.isdir(os.path.join(root, "vop_interwoven"))
                or os.path.isfile(os.path.join(root, "tests", "dynamo", "stage_a_probe_contract.py"))):
            continue
        for candidate in (root, os.path.join(root, "tests", "dynamo")):
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)
        return root
    if repo_root:
        raise RuntimeError(
            "IN[9] repository root does not contain vop_interwoven: {0}".format(repo_root))
    return None


def _probe_contract(output_dir=None, repo_root=None):
    global _PROBE_CONTRACT
    if _PROBE_CONTRACT is not None:
        return _PROBE_CONTRACT
    _add_repo_root_to_path(repo_root, output_dir)
    try:
        import tests.dynamo.stage_a_probe_contract as contract
    except ImportError:
        import stage_a_probe_contract as contract  # type: ignore
    _PROBE_CONTRACT = contract
    return contract


def _ensure_repo_import_path(output_dir=None, repo_root=None):
    try:
        import vop_interwoven  # noqa: F401
        return None
    except Exception:
        pass
    root = _add_repo_root_to_path(repo_root, output_dir)
    import vop_interwoven  # noqa: F401  (raises with a clear ImportError if absent)
    return root


# --------------------------------------------------------------------------
# Small pure helpers (unit-tested without Revit)
# --------------------------------------------------------------------------

def _safe_name(value):
    text = str(value or "view")
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", text).strip().replace(" ", "_") or "view"


def _safe_int_id(value):
    """Read an ElementId as an int, Revit 2025's 64-bit ``Value`` first.

    ``IntegerValue`` is the legacy 32-bit property and is deprecated in Revit
    2025; for an id outside the int32 range its getter can raise rather than
    return, and ``getattr(value, "IntegerValue", None)`` does not catch that --
    a default only covers AttributeError, not a throwing property. Reading
    ``Value`` first, with each property access in its own try, keeps a
    large-id document from failing the probe during collection, naming, or
    reporting.
    """
    for attr in ("Value", "IntegerValue"):
        try:
            inner = getattr(value, attr, None)
        except Exception:
            continue
        if inner is None:
            continue
        try:
            return int(inner)
        except (TypeError, ValueError):
            continue
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex),
            "traceback": traceback.format_exc()}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixels_per_foot(export_dpi, view_scale):
    """Paper-space DPI expressed as raster pixels per model foot."""
    export_dpi = float(export_dpi)
    view_scale = float(view_scale)
    if export_dpi <= 0 or view_scale <= 0:
        raise ValueError("export_dpi and view_scale must both be positive")
    return export_dpi * 12.0 / view_scale


def native_pixel_size(bounds_xy, export_dpi, view_scale):
    """Width in pixels at which one raster pixel equals one paper dot.

    This is the "native density" the drift baseline is stated against:
    ``extent_ft * dpi * 12 / view_scale``. Returns ``(width_px, height_px)``
    as floats -- callers decide how to round, because the rounding is itself
    something the experiments vary.
    """
    u0, v0, u1, v1 = [float(v) for v in bounds_xy]
    if u1 <= u0 or v1 <= v0:
        raise ValueError("bounds_xy must be a non-degenerate rectangle")
    ppf = pixels_per_foot(export_dpi, view_scale)
    return (u1 - u0) * ppf, (v1 - v0) * ppf


def dpi_for_native_ceiling(bounds_xy, view_scale, export_dpi, ceiling=D4_NATIVE_CEILING):
    """The highest DPI <= ``export_dpi`` whose native size fits under ``ceiling``.

    D4 separates "Revit was asked for more pixels than it will render" from
    "this raster is simply large": lowering DPI shrinks the *request* without
    changing the view, its content, or its crop.
    """
    width, height = native_pixel_size(bounds_xy, export_dpi, view_scale)
    longest = max(width, height)
    if longest <= ceiling:
        return float(export_dpi)
    return float(export_dpi) * float(ceiling) / longest


def snap_to_pixel_lattice(bounds_xy, export_dpi, view_scale, grid):
    """Split a rectangle into ``grid`` x ``grid`` tiles cut on whole pixels.

    A seam that falls between pixels would force each tile's own FitToPage
    rounding to disagree with its neighbour's, which is indistinguishable
    from the resampling D5 is trying to rule out. Each interior cut is
    therefore moved to the nearest exact multiple of one native pixel from
    the rectangle's low corner; the final tile absorbs whatever remains, so
    the tiles always re-tile the original rectangle exactly.
    """
    grid = int(grid)
    if grid < 1:
        raise ValueError("grid must be >= 1")
    u0, v0, u1, v1 = [float(v) for v in bounds_xy]
    if u1 <= u0 or v1 <= v0:
        raise ValueError("bounds_xy must be a non-degenerate rectangle")
    ppf = pixels_per_foot(export_dpi, view_scale)

    def cuts(lo, hi):
        span_px = (hi - lo) * ppf
        edges = [lo]
        for i in range(1, grid):
            edges.append(lo + round(span_px * i / grid) / ppf)
        edges.append(hi)
        return edges

    u_edges, v_edges = cuts(u0, u1), cuts(v0, v1)
    tiles = []
    for row in range(grid):
        for col in range(grid):
            tu0, tu1 = u_edges[col], u_edges[col + 1]
            tv0, tv1 = v_edges[row], v_edges[row + 1]
            width_px = (tu1 - tu0) * ppf
            height_px = (tv1 - tv0) * ppf
            tiles.append({
                "row": row, "col": col,
                "bounds_xy": [tu0, tv0, tu1, tv1],
                "native_width_px": width_px,
                "native_height_px": height_px,
                "requested_pixel_size": int(round(width_px)),
                # A seam is lattice-aligned when its pixel offset from the low
                # corner is a whole number; the residual is reported so a
                # misaligned seam is visible instead of assumed away.
                "seam_residual_px": {
                    "u_low": abs((tu0 - u0) * ppf - round((tu0 - u0) * ppf)),
                    "v_low": abs((tv0 - v0) * ppf - round((tv0 - v0) * ppf)),
                },
            })
    return tiles


def resolve_size_sweep(values, native_width_px):
    """Normalize a D3 sweep spec into concrete, de-duplicated pixel widths.

    ``"native"`` resolves to the rounded native width; a float in (0, 4] is a
    multiplier on native (so ``0.9`` and ``0.95`` express the sub-native half
    of D3); anything else is a literal pixel width.
    """
    if values is None:
        values = DEFAULT_SIZE_SWEEP
    if isinstance(values, str):
        values = [part.strip() for part in values.replace(";", ",").split(",") if part.strip()]
    resolved = []
    for raw in values:
        if isinstance(raw, str) and raw.strip().lower() == "native":
            label, width = "native", int(round(native_width_px))
        else:
            number = float(raw)
            if 0 < number <= 4.0 and not float(number).is_integer():
                label = "{0:g}x_native".format(number)
                width = int(round(native_width_px * number))
            else:
                label, width = "{0}px".format(int(number)), int(number)
        if width < 64:
            raise ValueError("pixel width {0} is below Revit's usable floor".format(width))
        entry = {"label": label, "requested_pixel_size": width}
        if entry not in resolved:
            resolved.append(entry)
    return resolved


def select_cases(selection):
    contract = _PROBE_CONTRACT
    if contract is None:
        # select_named lives in the contract; fall back to the same semantics
        # when the contract has not been bootstrapped yet (pure-test path).
        if selection is None or (isinstance(selection, str) and selection.strip().lower() == "all"):
            return list(CASES)
        requested = ([part.strip() for part in selection.replace(";", ",").split(",") if part.strip()]
                     if isinstance(selection, str) else [str(p).strip() for p in selection])
        unknown = [name for name in requested if name not in CASES]
        if unknown:
            raise ValueError("Unknown case(s): {0}. Supported values: {1}".format(unknown, list(CASES)))
        out = []
        for name in requested:
            if name not in out:
                out.append(name)
        return out
    return contract.select_named(selection, CASES, "drift case(s)")


# --------------------------------------------------------------------------
# Revit-side helpers
# --------------------------------------------------------------------------

def _get_current_document():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


def _unwrap_dynamo(obj):
    return getattr(obj, "InternalElement", obj)


def _bounds_tuple(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return (float(value["min_u"]), float(value["min_v"]),
                float(value["max_u"]), float(value["max_v"]))
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except Exception:
        return (float(value.Min.X), float(value.Min.Y), float(value.Max.X), float(value.Max.Y))


def _resolution_bounds(view):
    """The view-local rectangle (feet) the export covers, and where it came from."""
    try:
        if bool(getattr(view, "CropBoxActive", False)) and getattr(view, "CropBox", None) is not None:
            box = view.CropBox
            return (float(box.Min.X), float(box.Min.Y),
                    float(box.Max.X), float(box.Max.Y)), "active_model_crop"
    except Exception:
        pass
    from vop_interwoven.config import Config
    from vop_interwoven.revit.view_basis import make_view_basis, resolve_view_bounds
    doc = getattr(view, "Document", None)
    cfg = Config()
    scale = float(getattr(view, "Scale", 1) or 1)
    basis = make_view_basis(view)
    cell_size_ft = (float(cfg.cell_size_paper_in) * scale) / 12.0
    resolved = resolve_view_bounds(view, policy={
        "doc": doc, "basis": basis, "cfg": cfg,
        "buffer_ft": float(getattr(cfg, "bounds_buffer_ft", 0.0) or 0.0),
        "cell_size_ft": cell_size_ft,
        "max_W": getattr(cfg, "max_grid_cells_width", None),
        "max_H": getattr(cfg, "max_grid_cells_height", None),
    })
    model_bounds = resolved.get("model_bounds_uv")
    if model_bounds is not None:
        return _bounds_tuple(model_bounds), "resolved_model_bounds"
    canvas = resolved.get("bounds_uv")
    if canvas is not None:
        return _bounds_tuple(canvas), "canvas_bounds"
    raise RuntimeError("Could not resolve any export bounds for this view")


def apply_production_crop(view, bounds_xy):
    """Crop the view to ``bounds_xy`` the way production crops it.

    export_color_id_buffer_view builds the crop box with
    view_basis.crop_box_from_uv_bounds and sets CropBoxActive before
    exporting (color_id_buffer.py:1592-1596). A probe that only *computes*
    those bounds and leaves the crop inactive hands ExportImage a different
    extent: FitToPage then fits whatever the view happens to show, so visible
    content and realized pixels-per-foot can both differ from production --
    and the sidecar would be claiming a rectangle the TIFF does not span,
    which every bounds-derived metric (native_px, the 10:1 frame correction,
    out-of-bbox counts) is computed against.

    Uses the production helper rather than building a BoundingBoxXYZ here, so
    the UV-to-crop-local projection cannot drift from production's.

    Returns the rectangle actually cropped to, or None when the view has no
    CropBox -- matching the sidecar's own "bounds_xy" contract, where None
    means the export fell back to FitToPage's auto-computed extent.
    """
    from vop_interwoven.revit.view_basis import make_view_basis, crop_box_from_uv_bounds
    u0, v0, u1, v1 = [float(value) for value in bounds_xy]
    basis = make_view_basis(view)
    crop_box = crop_box_from_uv_bounds(view, basis, u0, v0, u1, v1)
    if crop_box is None:
        return None
    view.CropBox = crop_box
    view.CropBoxActive = True
    view.CropBoxVisible = False
    return (u0, v0, u1, v1)


def _collect_host_elements(doc, view, max_count=None):
    """Host elements to paint, resolved exactly the way production resolves them."""
    from vop_interwoven.config import Config
    from vop_interwoven.core.math_utils import Bounds2D
    from vop_interwoven.core.raster import ViewRaster
    from vop_interwoven.revit.collection import (
        collect_view_elements, expand_host_link_import_model_elements)
    from vop_interwoven.color_id_buffer import resolve_all
    cfg = Config()
    raster = ViewRaster(width=10, height=10, cell_size=1.0,
                        bounds=Bounds2D(0.0, 0.0, 1.0, 1.0),
                        tile_size=getattr(cfg, "tile_size", 16), cfg=cfg)
    elements = collect_view_elements(doc, view, raster, diag=None, cfg=cfg)
    expanded = expand_host_link_import_model_elements(
        doc, view, elements, cfg, diag=None, elem_cache=None)
    host, seen = [], set()
    for item in expanded:
        if item.get("source_type") != "HOST":
            continue
        elem = item.get("element")
        eid = getattr(elem, "Id", None)
        if eid is None or _safe_int_id(eid) in seen:
            continue
        seen.add(_safe_int_id(eid))
        host.append(elem)
    resolved = sorted(resolve_all(doc, host), key=lambda eid: _safe_int_id(eid))
    if max_count:
        resolved = resolved[:int(max_count)]
    return resolved, {"collected": len(elements), "host_candidates": len(host),
                      "painted": len(resolved)}


def production_palette_step(assignment_count, global_threshold):
    """The palette step production would use for this many assignments.

    Production does *not* size the lattice to the view's own element count --
    it calls ``choose_step(global_threshold)`` whenever the count fits under
    the configured threshold, so every view under it shares one global step
    and therefore one stable set of RGB values. Sizing to the per-view count
    instead yields step 8 rather than 6 for any realistic view, which is a
    different palette and a different set of assigned colors; a capture made
    that way could not be compared against a production one.
    """
    global_threshold = int(global_threshold)
    total = int(assignment_count)
    from vop_interwoven.color_id_buffer import choose_step
    return choose_step(global_threshold if total <= global_threshold else total)


def _paint(doc, view, element_ids):
    """Assign the production color-ID palette. Never a second palette generator."""
    from Autodesk.Revit.DB import Color
    from vop_interwoven.config import Config
    from vop_interwoven.color_id_buffer import (
        build_palette, _build_flat_color_ogs, _get_solid_pattern_id)
    global_threshold = getattr(
        Config(), "color_id_buffer_global_assignment_threshold", 32767)
    step = production_palette_step(len(element_ids), global_threshold)
    palette = build_palette(len(element_ids), step=step)
    solid = _get_solid_pattern_id(doc)
    if solid is None:
        raise RuntimeError("No solid drafting fill pattern found; cannot paint flat colors")
    color_map, failures = {}, []
    for index, eid in enumerate(element_ids):
        rgb = palette[index]
        color_map[str(_safe_int_id(eid))] = [int(rgb[0]), int(rgb[1]), int(rgb[2])]
        try:
            view.SetElementOverrides(
                eid, _build_flat_color_ogs(solid, Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))))
        except Exception as ex:
            failures.append({"element_id": _safe_int_id(eid),
                             "type": type(ex).__name__, "message": str(ex)})
    return color_map, failures, step


# EXACTLY the mutations export_color_id_buffer_view performs, in its order,
# and deliberately no others.
#
# An earlier revision of this probe borrowed stage_a_minimum_id_mutations'
# "full suppression" list, which is a superset. Two entries in it are wrong
# here:
#
#   visibility_off_filters_disabled -- production disables only filters that
#     were enabled AND VISIBLE (color_id_buffer.py:1519-1521). A filter whose
#     visibility is off stays enabled and keeps hiding its elements. Disabling
#     it reveals elements production hides, and they are not in the painted
#     set, so they render with uncontrolled colors and land in off_palette_px.
#
#   ambient_occlusion_off / sketchy_lines_off / depth_cueing_off -- production
#     does not touch these. Suppressing them makes the probe capture CLEANER
#     than production's, which for a drift investigation is the worse
#     direction of error: the probe could come back clean on a view that
#     drifts in production.
#
# Residual known divergence, recorded rather than papered over: production
# calls SetIsFilterEnabled(fid, False) on an enabled+visible filter, while
# visible_filter_graphics_neutralized clears its override and leaves it
# enabled. Both end with no filter graphics applied and the elements visible,
# but they are not the same API call.
PRODUCTION_SUPPRESSION_MUTATIONS = (
    "detach_template",
    "hide_annotation_categories",
    "visible_filter_graphics_neutralized",
    "phase_filter_neutralized",
    "display_style_flat_colors",
    "smooth_edges_off",
    "shadows_off",
)

# Applied separately, after collection: production neutralizes category
# halftone for the categories of the elements it actually resolved
# (color_id_buffer.py:1862-1875), which is not knowable before the collect.
POST_COLLECTION_MUTATIONS = ("category_halftone_neutralized",)


def _minimum_id_mutations_module():
    """The repo's own implementation of the production suppression set.

    Reusing it rather than writing a third copy: production applies these
    inline inside export_color_id_buffer_view with no callable seam, and
    stage_a_minimum_id_mutations already implements each one against the same
    Revit API with its own tests and template/unsupported handling. Importing
    a probe module is safe by the Stage A probe contract -- it opens no
    transaction, reads no Dynamo IN, and writes no artifact.
    """
    try:
        import tests.dynamo.probe_stage_a_minimum_id_mutations as module
    except ImportError:
        import probe_stage_a_minimum_id_mutations as module  # type: ignore
    return module


def normalization_shortfall(mutations):
    """Mutations that did not end in a state matching production's capture.

    APPLIED and ALREADY_MATCHED both leave the view in production's state.
    Anything else -- blocked by a template, unsupported on this Revit, or
    outright failed -- means this capture is not normalized the way a
    production capture is, and every metric taken from it has to be read
    knowing that. Returned rather than raised: a view that cannot reach the
    production state is itself a finding, not a crash.
    """
    ok = {"APPLIED", "ALREADY_MATCHED"}
    expected = set(PRODUCTION_SUPPRESSION_MUTATIONS) | set(POST_COLLECTION_MUTATIONS)
    mutations = mutations or {}
    shortfall = [mutation_id for mutation_id, record in mutations.items()
                 if (record or {}).get("status") not in ok]
    # A mutation that never ran at all is as much a shortfall as one that
    # failed; only counting the ones present would hide a skipped step.
    shortfall.extend(name for name in expected if name not in mutations)
    return sorted(set(shortfall))


def _apply_mutations(doc, view, element_ids, mutation_ids, into=None):
    """Run a suppression set, recording each mutation's own status."""
    module = _minimum_id_mutations_module()
    result = into if into is not None else {"mutations": {}}
    result.setdefault("mutations", {})
    for mutation_id in mutation_ids:
        try:
            module._apply_mutation(doc, view, element_ids, result, mutation_id)
        except Exception as ex:
            result["mutations"][mutation_id] = {
                "mutation_id": mutation_id, "status": "FAILED",
                "message": "{0}: {1}".format(type(ex).__name__, ex)}
    return result


def _normalize_view_state(doc, view):
    """Production's pre-collection suppression, in production's order.

    Runs BEFORE the element collect, because the neutral phase filter it
    installs changes what the collect returns -- see _run_native. Category
    halftone is not part of this set; it needs the resolved element ids and
    so runs after (POST_COLLECTION_MUTATIONS).
    """
    return _apply_mutations(doc, view, [], PRODUCTION_SUPPRESSION_MUTATIONS)


def _set_pixel_size(opts, requested):
    """Production's own back-off: halve until Revit accepts the request."""
    candidate = max(1, int(requested))
    while True:
        try:
            opts.PixelSize = candidate
            return candidate
        except Exception:
            if candidate <= 16:
                raise
            candidate = max(16, candidate // 2)


def _export_tiff(doc, view, path, requested_pixel_size):
    """Export defaults are production's, unchanged (color_id_buffer._export_tiff)."""
    from Autodesk.Revit.DB import (
        ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType)
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId]()
    ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    accepted = _set_pixel_size(opts, requested_pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_drift_onset_tmp")
    tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff
    opts.ShadowViewsFileType = tiff
    doc.ExportImage(opts)
    created = [f for f in (set(os.listdir(out_dir)) - before)
               if f.lower().endswith((".tif", ".tiff"))]
    if not created:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    created.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    source = os.path.join(out_dir, created[0])
    if os.path.exists(path):
        os.remove(path)
    os.rename(source, path)
    return path, accepted


def _image_dimensions(path):
    """Best-effort (w, h) without asserting anything about the pixels."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as img:
            return [int(img.size[0]), int(img.size[1])]
    except Exception:
        return None


# --------------------------------------------------------------------------
# Export record assembly
# --------------------------------------------------------------------------

def _record_export(doc, view, out_dir, base, case, label, requested_pixel_size,
                   bounds_xy, export_dpi, view_scale, extra=None):
    path = os.path.join(out_dir, "{0}.{1}.{2}.tiff".format(base, case, _safe_name(label)))
    record = {
        "case": case,
        "label": "{0}/{1}".format(case, label),
        "tiff_path": path,
        # Same contract as the Stage A sidecar's own field: the rectangle the
        # view was actually cropped to, or None when no crop could be applied
        # and FitToPage's auto-computed extent is what the TIFF spans. The
        # caller passes None for the latter so no bounds-derived metric is
        # computed against a rectangle the image does not cover.
        "bounds_xy": ([float(v) for v in bounds_xy] if bounds_xy is not None else None),
        "resolution": {
            "requested_pixel_size": int(requested_pixel_size),
            "pixel_size": None,
            "export_dpi": float(export_dpi),
            "view_scale": float(view_scale),
        },
        "export_error": None,
    }
    if extra:
        record.update(extra)
    started = time.time()
    exported, accepted = _export_tiff(doc, view, path, requested_pixel_size)
    record["export_ms"] = int(round((time.time() - started) * 1000.0))
    record["resolution"]["pixel_size"] = int(accepted)
    record["tiff_path"] = exported
    record["file_size_bytes"] = int(os.path.getsize(exported))
    # Byte identity is D1's whole question; recording it here means the answer
    # survives even if the analyzer never runs.
    record["sha256"] = _sha256_file(exported)
    record["dimensions_px"] = _image_dimensions(exported)
    if bounds_xy is not None:
        native_w, native_h = native_pixel_size(bounds_xy, export_dpi, view_scale)
        record["native_width_px"] = native_w
        record["native_height_px"] = native_h
    else:
        record["native_width_px"] = record["native_height_px"] = None
    return record


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

def _case_d1(ctx, repetitions):
    """D1: repeat one export unchanged and record whether the bytes match."""
    exports = []
    native_px = int(round(ctx["native_width_px"]))
    for index in range(max(2, int(repetitions))):
        exports.append(_record_export(
            ctx["doc"], ctx["view"], ctx["out_dir"], ctx["base"], "d1_determinism",
            "rep{0}".format(index), native_px, ctx["bounds_xy"],
            ctx["export_dpi"], ctx["view_scale"]))
    digests = [rec["sha256"] for rec in exports]
    return exports, {"repetitions": len(exports),
                     "sha256_values": digests,
                     "byte_identical": len(set(digests)) == 1}


def _case_d2(ctx, steps):
    """D2: hold size fixed and grow visible content one category bucket at a time.

    Categories are bucketed by descending painted-element count so load rises
    monotonically; the step-0 export has every hideable model category hidden,
    which is the zero-content control the later steps are read against.
    """
    from Autodesk.Revit.DB import CategoryType
    doc, view = ctx["doc"], ctx["view"]
    native_px = int(round(ctx["native_width_px"]))

    counts = {}
    for eid in ctx["element_ids"]:
        element = doc.GetElement(eid)
        category = getattr(element, "Category", None)
        cid = _safe_int_id(getattr(category, "Id", None))
        if cid is None:
            continue
        counts[cid] = counts.get(cid, 0) + 1

    hideable = []
    for cat in doc.Settings.Categories:
        cid = getattr(cat, "Id", None)
        cid_int = _safe_int_id(cid)
        if cid_int is None or cid_int not in counts:
            continue
        try:
            if cat.CategoryType != CategoryType.Model or not view.CanCategoryBeHidden(cid):
                continue
            hideable.append({"id": cid_int, "name": getattr(cat, "Name", None),
                             "painted_elements": counts[cid],
                             "was_hidden": bool(view.GetCategoryHidden(cid))})
        except Exception:
            continue
    hideable.sort(key=lambda item: (-item["painted_elements"], item["id"]))

    if not hideable:
        return [], {"skipped": "no model category in this view can be hidden",
                    "categories": []}

    steps = max(1, min(int(steps), len(hideable)))
    buckets, size = [], (len(hideable) + steps - 1) // steps
    for start in range(0, len(hideable), size):
        buckets.append(hideable[start:start + size])

    exports, step_records = [], []
    _mutate(doc, "d2_hide_all", lambda: _set_categories_hidden(view, hideable, True))
    revealed = []
    for index, bucket in enumerate(buckets + [None]):
        label = "step{0}".format(index)
        record = _record_export(
            doc, view, ctx["out_dir"], ctx["base"], "d2_category_load", label,
            native_px, ctx["bounds_xy"], ctx["export_dpi"], ctx["view_scale"],
            extra={"visible_categories": [c["name"] for c in revealed],
                   "visible_painted_elements": sum(c["painted_elements"] for c in revealed)})
        exports.append(record)
        step_records.append({"step": index,
                             "visible_category_count": len(revealed),
                             "visible_painted_elements": record["visible_painted_elements"],
                             "tiff_path": record["tiff_path"]})
        if bucket is None:
            break
        revealed = revealed + bucket
        _mutate(doc, "d2_unhide_{0}".format(index),
                lambda b=bucket: _set_categories_hidden(view, b, False))
    return exports, {"steps": step_records,
                     "categories": [{"id": c["id"], "name": c["name"],
                                     "painted_elements": c["painted_elements"]}
                                    for c in hideable]}


def _set_categories_hidden(view, categories, hidden):
    from Autodesk.Revit.DB import ElementId
    for item in categories:
        view.SetCategoryHidden(ElementId(int(item["id"])), bool(hidden))


def _case_d3(ctx, sweep):
    """D3: hold content fixed and sweep the requested pixel size."""
    exports = []
    for entry in resolve_size_sweep(sweep, ctx["native_width_px"]):
        exports.append(_record_export(
            ctx["doc"], ctx["view"], ctx["out_dir"], ctx["base"], "d3_size_sweep",
            entry["label"], entry["requested_pixel_size"], ctx["bounds_xy"],
            ctx["export_dpi"], ctx["view_scale"]))
    return exports, {"requested": [rec["resolution"]["requested_pixel_size"] for rec in exports]}


def _case_d4(ctx):
    """D4: lower DPI until native fits the ceiling, then ask for native exactly.

    Nothing about the view or its content changes -- only the size of the
    request -- so a clean result here and a drifted result at full DPI would
    isolate the request size, while drift in both would rule it out.
    """
    lowered = dpi_for_native_ceiling(ctx["bounds_xy"], ctx["view_scale"], ctx["export_dpi"])
    width, height = native_pixel_size(ctx["bounds_xy"], lowered, ctx["view_scale"])
    record = _record_export(
        ctx["doc"], ctx["view"], ctx["out_dir"], ctx["base"], "d4_dpi_vs_pixel_size",
        "dpi{0:.2f}".format(lowered), int(round(width)), ctx["bounds_xy"],
        lowered, ctx["view_scale"])
    return [record], {
        "original_export_dpi": ctx["export_dpi"],
        "lowered_export_dpi": lowered,
        "native_ceiling_px": D4_NATIVE_CEILING,
        "native_width_px_at_lowered_dpi": width,
        "native_height_px_at_lowered_dpi": height,
        # UNCONFIRMED: ImageExportOptions is driven here purely through
        # PixelSize. No run has shown Revit exposing a DPI input that changes
        # the raster independently of it.
        "dpi_is_request_side_only": "UNCONFIRMED",
    }


def _case_d5(ctx, grid):
    """D5: re-tile the same rectangle into NxN crops cut on the pixel lattice."""
    doc, view = ctx["doc"], ctx["view"]
    tiles = snap_to_pixel_lattice(ctx["bounds_xy"], ctx["export_dpi"], ctx["view_scale"], grid)
    original_box = getattr(view, "CropBox", None)
    original_active = bool(getattr(view, "CropBoxActive", False))
    original_visible = bool(getattr(view, "CropBoxVisible", False))
    exports, tile_records = [], []
    for tile in tiles:
        u0, v0, u1, v1 = tile["bounds_xy"]
        label = "r{0}c{1}".format(tile["row"], tile["col"])

        # Same UV-to-crop-local projection production uses; a hand-built box
        # would have to re-derive it and could drift from view_basis's.
        applied = {}
        _mutate(doc, "d5_crop_{0}".format(label),
                lambda t=tile: applied.__setitem__(
                    "bounds", apply_production_crop(view, t["bounds_xy"])))
        record = _record_export(
            doc, view, ctx["out_dir"], ctx["base"], "d5_crop_tiles", label,
            tile["requested_pixel_size"], applied.get("bounds"),
            ctx["export_dpi"], ctx["view_scale"],
            extra={"tile_row": tile["row"], "tile_col": tile["col"],
                   "seam_residual_px": tile["seam_residual_px"],
                   "requested_tile_bounds_xy": tile["bounds_xy"],
                   "crop_applied": applied.get("bounds") is not None})
        exports.append(record)
        tile_records.append({
            "row": tile["row"], "col": tile["col"],
            "requested_pixel_size": tile["requested_pixel_size"],
            "native_width_px": tile["native_width_px"],
            "native_height_px": tile["native_height_px"],
            "seam_residual_px": tile["seam_residual_px"],
            "actual_dimensions_px": record.get("dimensions_px"),
        })

    def restore_crop():
        if original_box is not None:
            view.CropBox = original_box
        view.CropBoxActive = original_active
        view.CropBoxVisible = original_visible
    _mutate(doc, "d5_restore_crop", restore_crop)
    return exports, {"grid": int(grid), "tiles": tile_records,
                     "original_crop_active": original_active}


# --------------------------------------------------------------------------
# Transaction plumbing
# --------------------------------------------------------------------------

def _mutate(doc, name, action):
    """Run one document mutation in its own committed transaction.

    ExportImage refuses to run with a transaction open, so every case commits
    its change before exporting; the enclosing TransactionGroup is what makes
    all of it temporary.
    """
    from Autodesk.Revit.DB import Transaction, TransactionStatus
    tx = Transaction(doc, "VOP Stage A Drift Probe: {0}".format(name))
    if tx.Start() != TransactionStatus.Started:
        raise RuntimeError("Transaction.Start failed for {0}".format(name))
    try:
        action()
        status = tx.Commit()
    except Exception:
        try:
            tx.RollBack()
        except Exception:
            pass
        raise
    if status != TransactionStatus.Committed:
        raise RuntimeError("Transaction.Commit returned {0} for {1}".format(status, name))


def _snapshot(doc, view):
    state = {"doc_is_modified": bool(getattr(doc, "IsModified", False)),
             "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)),
             "crop_box_active": None, "crop_box": None, "category_hidden": {}}
    try:
        state["crop_box_active"] = bool(view.CropBoxActive)
        box = view.CropBox
        state["crop_box"] = [float(box.Min.X), float(box.Min.Y),
                             float(box.Max.X), float(box.Max.Y)]
    except Exception as ex:
        state["crop_box_error"] = str(ex)
    try:
        for cat in doc.Settings.Categories:
            cid = getattr(cat, "Id", None)
            cid_int = _safe_int_id(cid)
            if cid_int is None:
                continue
            try:
                if view.CanCategoryBeHidden(cid):
                    state["category_hidden"][str(cid_int)] = bool(view.GetCategoryHidden(cid))
            except Exception:
                continue
    except Exception as ex:
        state["category_hidden_error"] = str(ex)
    return state


def _diff_state(before, after):
    diffs = []
    for key in sorted(set(before) | set(after)):
        if key.endswith("_error"):
            continue
        if before.get(key) != after.get(key):
            diffs.append({"key": key, "before": before.get(key), "after": after.get(key)})
    return diffs


# --------------------------------------------------------------------------
# Native driver
# --------------------------------------------------------------------------

def _run_native(raw_view, output_dir, selection="all", repetitions=DEFAULT_REPETITIONS,
                pixel_sizes=None, export_dpi=DEFAULT_EXPORT_DPI, d2_steps=DEFAULT_D2_STEPS,
                tile_grid=DEFAULT_TILE_GRID, max_elements=None, repo_root=None):
    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION,
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {}, "view": {}, "paint": {}, "cases": {},
        "exports": [], "color_assignment_map": {},
        "transaction_group": {"started": False, "rollback_attempted": False,
                              "rollback_succeeded": False},
        "state": {"before": {}, "after": {}, "restored": None, "differences": []},
        "unconfirmed_api_assumptions": [
            "ZoomFitType.FitToPage + FitDirectionType.Horizontal makes PixelSize the output width",
            "ImageExportOptions exposes no DPI input independent of PixelSize",
            "Revit clamps export aspect to 10:1 by symmetric padding of the short axis",
            "Revit's PixelSize ceiling is a fixed value rather than install/version dependent",
        ],
        "exceptions": [], "timings_ms": {},
    }
    doc = _get_current_document()
    group = None
    group_started = False
    json_path = None
    try:
        _ensure_repo_import_path(output_dir, repo_root)
        view = _unwrap_dynamo(raw_view)
        if view is None:
            raise ValueError("IN[0] did not resolve to a Revit view")
        cases = select_cases(selection)
        out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
        out_dir = os.path.join(out_base, "drift_onset_probe")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)

        view_scale = float(getattr(view, "Scale", 1) or 1)
        export_dpi = float(export_dpi)
        bounds_xy, bounds_source = _resolution_bounds(view)
        native_w, native_h = native_pixel_size(bounds_xy, export_dpi, view_scale)
        base = "{0}.{1}".format(_safe_name(getattr(view, "Name", "view")), _safe_int_id(view.Id))

        report["inputs"] = {"output_directory": out_base, "cases": cases,
                            "repetitions": int(repetitions), "pixel_sizes": pixel_sizes,
                            "export_dpi": export_dpi, "d2_steps": int(d2_steps),
                            "tile_grid": int(tile_grid), "max_elements": max_elements}
        report["view"] = {
            "id": _safe_int_id(view.Id), "name": getattr(view, "Name", None),
            "scale": view_scale, "bounds_xy": list(bounds_xy), "bounds_source": bounds_source,
            "native_width_px": native_w, "native_height_px": native_h,
            "native_max_axis_px": max(native_w, native_h),
            # The 10:1 clamp prediction, stated so the analyzer can check it
            # against the TIFF instead of the probe asserting it.
            "predicted_aspect": (bounds_xy[2] - bounds_xy[0]) / (bounds_xy[3] - bounds_xy[1]),
        }

        _force_close_dynamo_transaction()
        report["state"]["before"] = _snapshot(doc, view)

        from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
        group = TransactionGroup(doc, "VOP Stage A Drift Onset Probe")
        group_started = group.Start() == TransactionStatus.Started
        report["transaction_group"]["started"] = group_started
        if not group_started:
            raise RuntimeError("TransactionGroup.Start did not start")

        # Production's order, which is not incidental (color_id_buffer.py):
        # suppress -> crop -> collect -> neutralize category halftone -> paint.
        # Collecting first would paint a set gathered under the view's ORIGINAL
        # phase filter; the neutral phase filter installed during suppression
        # then reveals the elements that filter hid (demolished, temporary),
        # and ExportImage renders them with no colour assigned. Production
        # re-collects after the swap for exactly this reason (":1762"), and the
        # crop goes in first because it narrows what the collect returns.
        normalization = {}
        _mutate(doc, "normalize_view_state",
                lambda: normalization.update(_normalize_view_state(doc, view)))

        cropped = {}
        _mutate(doc, "apply_production_crop",
                lambda: cropped.__setitem__(
                    "bounds", apply_production_crop(view, bounds_xy)))
        crop_bounds = cropped.get("bounds")
        report["view"]["crop_applied"] = crop_bounds is not None
        report["view"]["crop_bounds_xy"] = list(crop_bounds) if crop_bounds else None
        if crop_bounds is None:
            report.setdefault("warnings", []).append({
                "stage": "apply_production_crop",
                "message": "This view has no CropBox, so the export falls back to "
                           "FitToPage's auto-computed extent. bounds_xy is recorded as "
                           "null and no bounds-derived metric (native_px, the 10:1 frame "
                           "correction, out-of-bbox counts) can be computed for it.",
            })

        element_ids, collect_stats = _collect_host_elements(doc, view, max_elements)

        _mutate(doc, "neutralize_category_halftone",
                lambda: _apply_mutations(doc, view, element_ids,
                                         POST_COLLECTION_MUTATIONS, into=normalization))

        shortfall = normalization_shortfall(normalization.get("mutations"))
        report["view_state_normalization"] = {
            "requested": list(PRODUCTION_SUPPRESSION_MUTATIONS) + list(POST_COLLECTION_MUTATIONS),
            "mutations": normalization.get("mutations", {}),
            "not_in_production_state": shortfall,
            "matches_production_capture_state": not shortfall,
            "known_divergences": [
                "production disables an enabled+visible filter via SetIsFilterEnabled(False); "
                "visible_filter_graphics_neutralized clears its override and leaves it enabled",
                "LINK elements are not painted by this probe (brief non-goal); a view with "
                "loaded links will carry uncontrolled LINK colours that production would "
                "have given category colours",
            ],
        }
        if shortfall:
            report.setdefault("warnings", []).append({
                "stage": "normalize_view_state",
                "message": "This capture is NOT in production's export state; {0} did not "
                           "apply. Blended pixels in it may come from those effects rather "
                           "than from rasterization.".format(", ".join(shortfall)),
            })

        paint_result = {}

        def do_paint():
            color_map, failures, step = _paint(doc, view, element_ids)
            paint_result.update({"color_map": color_map, "failures": failures, "step": step})
        _mutate(doc, "paint", do_paint)
        report["paint"] = {"collection": collect_stats,
                           "palette_step": paint_result.get("step"),
                           "paint_failures": paint_result.get("failures", [])}
        report["color_assignment_map"] = paint_result.get("color_map", {})

        ctx = {"doc": doc, "view": view, "out_dir": out_dir, "base": base,
               "bounds_xy": crop_bounds, "export_dpi": export_dpi, "view_scale": view_scale,
               "native_width_px": native_w, "element_ids": element_ids}

        runners = {
            "d1_determinism": lambda: _case_d1(ctx, repetitions),
            "d2_category_load": lambda: _case_d2(ctx, d2_steps),
            "d3_size_sweep": lambda: _case_d3(ctx, pixel_sizes),
            "d4_dpi_vs_pixel_size": lambda: _case_d4(ctx),
            "d5_crop_tiles": lambda: _case_d5(ctx, tile_grid),
        }
        for case in cases:
            started = time.time()
            try:
                exports, detail = runners[case]()
                report["exports"].extend(exports)
                report["cases"][case] = detail
            except Exception as ex:
                report["exceptions"].append(_exception_record(case, ex))
                report["cases"][case] = {"failed": True, "type": type(ex).__name__,
                                         "message": str(ex)}
            report["timings_ms"][case] = int(round((time.time() - started) * 1000.0))
    except Exception as ex:
        report["exceptions"].append(_exception_record("setup_or_outer", ex))
    finally:
        if group_started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                from Autodesk.Revit.DB import TransactionStatus
                status = group.RollBack()
                report["transaction_group"]["rollback_status"] = str(status)
                report["transaction_group"]["rollback_succeeded"] = status == TransactionStatus.RolledBack
            except Exception as ex:
                report["exceptions"].append(_exception_record("transaction_group_rollback", ex))
        try:
            view = _unwrap_dynamo(raw_view)
            if view is not None and report["state"]["before"]:
                report["state"]["after"] = _snapshot(doc, view)
                diffs = _diff_state(report["state"]["before"], report["state"]["after"])
                report["state"]["differences"] = diffs
                report["state"]["restored"] = len(diffs) == 0
        except Exception as ex:
            report["exceptions"].append(_exception_record("post_rollback_state_capture", ex))

        try:
            out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
            out_dir = os.path.join(out_base, "drift_onset_probe")
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            name = report["view"].get("name") or "view"
            json_path = os.path.join(
                out_dir, "{0}.{1}.drift_onset.json".format(
                    _safe_name(name), report["view"].get("id")))
            with open(json_path, "w") as handle:
                json.dump(report, handle, indent=2, sort_keys=True)
            report["json_report_path"] = json_path
        except Exception as ex:
            report["exceptions"].append(_exception_record("json_report_write", ex))
    return report


def run_probe(raw_view, output_dir, selection="all", repetitions=DEFAULT_REPETITIONS,
              pixel_sizes=None, export_dpi=DEFAULT_EXPORT_DPI, d2_steps=DEFAULT_D2_STEPS,
              tile_grid=DEFAULT_TILE_GRID, max_elements=None, repo_root=None):
    contract = _probe_contract(output_dir, repo_root)
    started_at = contract.utc_now_iso()
    native = _run_native(raw_view, output_dir, selection, repetitions, pixel_sizes,
                         export_dpi, d2_steps, tile_grid, max_elements, repo_root)
    rollback_ok = bool(native["transaction_group"].get("rollback_succeeded"))
    restored = native["state"].get("restored")
    artifacts = [rec["tiff_path"] for rec in native.get("exports", []) if rec.get("tiff_path")]
    if native.get("json_report_path"):
        artifacts.append(native["json_report_path"])
    errors = list(native.get("exceptions", []))
    if errors or not rollback_ok:
        status = "failed"
    elif not native.get("exports"):
        status = "inconclusive"
    else:
        status = "completed"
    return contract.execution_envelope(
        PROBE_NAME,
        {"selection": selection, "repetitions": repetitions, "pixel_sizes": pixel_sizes,
         "export_dpi": export_dpi, "d2_steps": d2_steps, "tile_grid": tile_grid,
         "max_elements": max_elements},
        contract.view_identity(raw_view), native, artifacts,
        "succeeded" if rollback_ok else ("failed" if native["transaction_group"].get("rollback_attempted") else "not_started"),
        "restored" if restored else ("not_restored" if restored is False else "not_checked"),
        started_at, execution_status=status, errors=errors,
        warnings=list(native.get("warnings", [])))


def dynamo_main(inputs):
    def at(index, default=None):
        value = inputs[index] if len(inputs) > index else None
        return default if value is None else value
    return run_probe(
        inputs[0], inputs[1],
        selection=at(2, "all"),
        repetitions=int(at(3, DEFAULT_REPETITIONS)),
        pixel_sizes=at(4),
        export_dpi=float(at(5, DEFAULT_EXPORT_DPI)),
        d2_steps=int(at(6, DEFAULT_D2_STEPS)),
        tile_grid=int(at(7, DEFAULT_TILE_GRID)),
        max_elements=at(8),
        repo_root=at(9),
    )


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821  (Dynamo injects IN)
    except Exception as fatal:
        OUT = {"probe_id": PROBE_NAME, "execution_status": "failed",
               "fatal_exception": _exception_record("dynamo_entrypoint", fatal)}
