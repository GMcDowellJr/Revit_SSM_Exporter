"""
Stage A edge-drift onset probe (experiments D1-D5) for Revit 2025 / Dynamo 3.3.

Extraction only. Every capture is produced by calling production's own Stage A
path -- ``pipeline.init_view_raster`` -> ``collection.collect_view_elements``
-> ``color_id_buffer.export_color_id_buffer_view`` -- not by a sequence that
mirrors it. Bounds resolution, the suppression set and its ordering, the render
crop, the palette step, the paint step, the link-category filters and the
export options are all production's, so a probe capture cannot drift from a
production one by construction.

That is a deliberate correction. Earlier revisions of this probe rebuilt the
capture sequence by hand and diverged from it three separate times -- wrong
palette step, wrong suppression set, wrong ordering, no render crop -- each
one silently invalidating the very measurements the probe exists to take. An
experiment here now varies exactly one input (the config, the view's crop, or
its category visibility) and then calls production.

The probe computes no image statistics and reaches no conclusion. All pixel
analysis belongs to ``tools/analyze_stage_a_probe.py --export-metrics``, which
reads the production sidecar each capture writes; point it at this probe's
``captures/`` directory.

Every mutation happens inside a single TransactionGroup that is always rolled
back, and the pre/post state is compared so a failed rollback is visible
rather than assumed. Production's own Diagnostics are captured and returned:
a clamped pixel size, a read-only phase filter, a crop that would not apply or
a display style that could not be set all change how a capture must be read.

Dynamo inputs:
    IN[0] = target Revit view
    IN[1] = output directory
    IN[2] = case selection: "all" or a comma-separated subset of
            d1_determinism, d2_category_load, d3_size_sweep,
            d4_dpi_vs_pixel_size, d5_crop_tiles          (default "all")
    IN[3] = repetitions for D1                            (default 2)
    IN[4] = D3 sizes: "native,10000,12000,15000" or a list
                                                          (default that list)
    IN[5] = export DPI for the baseline capture           (default 150.0)
    IN[6] = D2 step count: how many category buckets to reveal
                                                          (default 8)
    IN[7] = D5 tile grid: N for an NxN crop tiling        (default 2)
    IN[8] = accepted and ignored; production resolves its own element set
    IN[9] = repository root containing vop_interwoven     (default: autodetect)

UNCONFIRMED Revit API assumptions this probe's *planning* encodes (see
tests/dynamo/PROBE_STAGE_A_COLOR_ID_ANOMALIES.md): ImageExportOptions exposes
no DPI concept independent of PixelSize; ZoomFitType.FitToPage with
FitDirectionType.Horizontal makes PixelSize the output width; Revit clamps
export aspect to 10:1 by padding the short axis. Each is recorded in the
report as a prediction beside what the run actually produced, never asserted.
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


def _file_mtime(path):
    try:
        return os.path.getmtime(path) if path else None
    except OSError:
        return None


# Recorded at import, compared at run. Revit keeps one Python interpreter alive
# for the whole session, so a module imported before an edit stays imported
# after it: the campaign JSON is re-read from disk every run, but the probe
# code is not. A run can therefore execute an old probe against a new campaign
# and produce artifacts that look current and are not -- which is exactly what
# happened on 2026-09-16, where a stale drift module swept the wrong D2
# buckets while the white-blend module beside it was up to date.
MODULE_FILE = os.path.abspath(__file__) if "__file__" in globals() else None
MODULE_MTIME_AT_IMPORT = _file_mtime(MODULE_FILE)


def source_record(module):
    """Whether ``module``'s file on disk has changed since it was imported."""
    path = getattr(module, "MODULE_FILE", None)
    at_import = getattr(module, "MODULE_MTIME_AT_IMPORT", None)
    now = _file_mtime(path)
    return {"module": getattr(module, "__name__", None), "file": path,
            "mtime_at_import": at_import, "mtime_now": now,
            # Unknown is not stale: a module loaded from a zip or with no
            # readable file cannot be checked, and refusing to run on that
            # would block an install this probe is meant to work on.
            "stale": bool(path and at_import is not None and now is not None
                          and now != at_import)}


def source_records():
    return [source_record(sys.modules[__name__])]


def stale_sources(records):
    return [record for record in records if record.get("stale")]


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
# The ceiling production already applies (color_id_buffer.MAX_STAGE_A_PIXEL_SIZE),
# which with FitDirectionType.Horizontal bounds the WIDTH only.
D4_WIDTH_CEILING = 15000
# The height ceiling is Run 1's finding, not a production constant. Every
# capture in that run split on exported height at a threshold in (9927, 10079],
# with widths up to 15000 perfectly hard-edged. 9900 sits under the low end of
# that bracket, so it is safe wherever in it the real limit falls; a follow-up
# sweep at 0.98x/0.99x/1.00x on 871863 would pin it exactly.
D4_HEIGHT_CEILING = 9900
# Kept as the old name so an existing caller does not break. It was the width
# ceiling all along, which is why D4 lowered no DPI in Run 1: it compared the
# LONGEST axis against 15000 on a view whose height problem started at 10079.
D4_NATIVE_CEILING = D4_WIDTH_CEILING

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


def run_token(output_dir):
    """A short name for THIS run, taken from its own output directory.

    Two jobs on the same view produce artifacts with identical names -- the
    campaign runs four D jobs against 871863 and two B jobs against 587278 --
    and per-job directories only hide that while the files stay in their
    folders. They do not stay there: captures get pooled for analysis, copied
    off the Revit host, and attached to a message. A name that only says
    "SITE PLAN AT LEVEL 4, view 587278" cannot then be told apart from the
    other job's, and the earlier one is what gets overwritten.

    The batch executor gives every job its own output directory named after
    the job, so its basename is already the distinguishing token; outside a
    campaign it is whatever directory the run was pointed at. Empty or
    degenerate (a drive root) yields "", and callers leave the token out.
    """
    text = str(output_dir or "").strip()
    if not text:
        # abspath("") is the process cwd, whose name would then be stamped
        # onto every artifact of a run that named no directory at all.
        return ""
    base = os.path.basename(os.path.normpath(os.path.abspath(
        os.path.expanduser(text))))
    if not base or base in (os.sep, os.altsep, ".", ".."):
        return ""
    return _safe_name(base)


def _stem(*parts):
    """Join the non-empty parts of an artifact name with dots.

    Every artifact name starts with the run token so that two jobs on the same
    view stay distinguishable once their files are pooled.
    """
    return ".".join(str(part) for part in parts if part not in (None, ""))


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


def dpi_for_native_ceiling(bounds_xy, view_scale, export_dpi,
                           ceiling=None, height_ceiling=None):
    """The highest DPI <= ``export_dpi`` that fits both export ceilings.

    D4 separates "Revit was asked for more pixels than it will render" from
    "this raster is simply large": lowering DPI shrinks the *request* without
    changing the view, its content, or its crop.

    The two axes have different limits and they are not interchangeable. Width
    is what PixelSize sets, capped by production at MAX_STAGE_A_PIXEL_SIZE.
    Height is derived from the view's extents and is where the drift threshold
    sits. Comparing the longest axis against the width ceiling -- what this did
    before Run 1 -- passes a view whose height is already over the line, which
    is exactly why D4 lowered nothing and duplicated D1.
    """
    ceiling = float(D4_WIDTH_CEILING if ceiling is None else ceiling)
    height_ceiling = float(D4_HEIGHT_CEILING if height_ceiling is None else height_ceiling)
    width, height = native_pixel_size(bounds_xy, export_dpi, view_scale)
    factor = min(ceiling / width if width > ceiling else 1.0,
                 height_ceiling / height if height > height_ceiling else 1.0)
    return float(export_dpi) * factor


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


def _now_ms():
    return int(round(time.time() * 1000.0))


def _utc_now_iso():
    from datetime import datetime
    try:
        from datetime import timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except ImportError:  # pragma: no cover - very old runtimes
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _image_dimensions(path):
    """Best-effort (w, h) without asserting anything about the pixels."""
    try:
        from PIL import Image
    except Exception:
        return None
    # The captures this probe exists to measure are exactly the ones Pillow
    # refuses by default: its ~179 Mpx decompression-bomb guard rejects a
    # 15000x14463 export, and the failure is silent here because the caller
    # only records the dimensions. That is why the largest captures in the
    # 2026-09-16 run came back with dimensions_px: null.
    try:
        Image.MAX_IMAGE_PIXELS = None
    except Exception:
        pass
    try:
        with Image.open(path) as img:
            return [int(img.size[0]), int(img.size[1])]
    except Exception:
        return None


def _mutate(doc, name, action):
    """Run one document mutation in its own committed transaction.

    ExportImage refuses to run with a transaction open and
    export_color_id_buffer_view opens its own, so every experiment commits its
    change before capturing; the enclosing TransactionGroup is what makes all
    of it temporary.
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


def _set_view_crop(view, bounds_xy):
    """Pin the view's crop to a UV rectangle, via the production helper.

    Used to hold the raster's bounds fixed while an experiment varies
    something else (D2), and to select each tile (D5). resolve_view_bounds
    takes its bounds straight from an active crop box (view_basis.py:901-917),
    so pinning the crop pins everything production derives from it: grid size,
    paper width, pixel size, and the rectangle the render is cropped to.

    Returns the rectangle applied, or None if the view has no CropBox.
    """
    from vop_interwoven.revit.view_basis import make_view_basis, crop_box_from_uv_bounds
    u0, v0, u1, v1 = [float(value) for value in bounds_xy]
    crop_box = crop_box_from_uv_bounds(view, make_view_basis(view), u0, v0, u1, v1)
    if crop_box is None:
        return None
    view.CropBox = crop_box
    view.CropBoxActive = True
    view.CropBoxVisible = False
    return (u0, v0, u1, v1)


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
# Production capture
#
# Every capture below is produced by export_color_id_buffer_view itself, not
# by a sequence mirroring it. That is the whole point: bounds resolution, the
# suppression set and its ordering, the crop, the palette step, the paint
# step, the link-category filters and the export options are production's, so
# a probe capture cannot diverge from a production one by construction. An
# experiment varies exactly one input -- the config, the view's crop, or its
# category visibility -- and then calls it.
# --------------------------------------------------------------------------

def build_capture_config(output_dir, export_dpi=None, overrides=None):
    """A Config that runs production's Stage A path into ``output_dir``."""
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.output_dir = output_dir
    cfg.enable_color_id_buffer_stage_a = True
    if export_dpi is not None:
        cfg.color_id_buffer_export_dpi = float(export_dpi)
    for key, value in (overrides or {}).items():
        setattr(cfg, key, value)
    return cfg


def dpi_for_pixel_width(target_px, paper_width_in):
    """The export DPI at which production's own formula yields ``target_px``.

    export_color_id_buffer_view computes
    ``pixel_size = round(export_dpi * paper_width_in)`` and then clamps to
    [64, MAX_STAGE_A_PIXEL_SIZE]. Driving the size through DPI rather than
    writing PixelSize directly keeps the request on production's own code
    path, including that clamp -- which is itself one of the things D3 and D4
    are trying to characterize, so bypassing it would defeat the experiment.
    """
    paper_width_in = float(paper_width_in)
    if paper_width_in <= 0:
        raise ValueError("paper_width_in must be positive")
    target_px = int(target_px)
    if target_px < 1:
        raise ValueError("target_px must be positive")
    return target_px / paper_width_in


def plan_capture_geometry(doc, view, cfg, diag=None):
    """The raster production would build, and the numbers experiments plan against.

    Built with production's own init_view_raster so the planning numbers are
    the ones the capture will actually use, not a second derivation of them.
    """
    from vop_interwoven.pipeline import init_view_raster
    raster = init_view_raster(doc, view, cfg, diag=diag)
    bounds = raster.bounds_xy
    scale = float(getattr(view, "Scale", 1) or 1)
    paper_width_in = (float(raster.W) * float(raster.cell_size_ft) * 12.0) / max(scale, 1.0e-6)
    return {
        "bounds_xy": (float(bounds.xmin), float(bounds.ymin),
                      float(bounds.xmax), float(bounds.ymax)),
        "grid_W": int(raster.W), "grid_H": int(raster.H),
        "cell_size_ft": float(raster.cell_size_ft),
        "view_scale": scale,
        "paper_width_in": paper_width_in,
        # What production would request at this config's DPI. The brief's
        # "native" density is exactly this.
        "native_pixel_size": int(round(float(cfg.color_id_buffer_export_dpi) * paper_width_in)),
        "bounds_source": (raster.bounds_meta or {}).get("reason")
                         if hasattr(raster, "bounds_meta") else None,
    }


def production_capture(doc, view, cfg, case, label, capture_dir, diag=None, run=""):
    """Run production's Stage A capture once and keep its artifacts under ``label``.

    export_color_id_buffer_view writes one TIFF and one sidecar per view and
    would overwrite them on the next call, so both are moved to a
    case/label-specific name and the sidecar's own ``tiff_path`` is rewritten
    to match. The moved sidecar stays a valid standalone input for
    ``analyze_stage_a_probe.py --export-metrics``.
    """
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements
    from vop_interwoven.color_id_buffer import export_color_id_buffer_view

    raster = init_view_raster(doc, view, cfg, diag=diag)
    elements = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
    started = _now_ms()
    out = export_color_id_buffer_view(
        doc, view, elements, cfg, diag=diag, raster=raster, elem_cache=None)
    elapsed_ms = _now_ms() - started

    if not os.path.isdir(capture_dir):
        os.makedirs(capture_dir)
    # The view id is part of the name, not decoration. Case and label alone
    # collide across views: running D1 on one view and D3 on another into the
    # same output directory produced identically-named files, so the second
    # run silently overwrote the first's sidecar while its TIFF stayed, leaving
    # sidecars and images crossed between views and a capture set that looks
    # complete and is not.
    stem = _stem(run, _safe_int_id(getattr(view, "Id", None)),
                 _safe_name(case), _safe_name(label))
    tiff_path = os.path.join(capture_dir, stem + ".tiff")
    sidecar_path = os.path.join(capture_dir, stem + ".json")
    for source, destination in ((out.get("tiff_path"), tiff_path),
                                (out.get("sidecar_path"), sidecar_path)):
        if source and os.path.exists(source):
            if os.path.exists(destination):
                os.remove(destination)
            os.rename(source, destination)

    sidecar = {}
    if os.path.exists(sidecar_path):
        with open(sidecar_path) as handle:
            sidecar = json.load(handle)
        # Recorded as a bare file name, not an absolute path. The sidecar and
        # its TIFF are written side by side and travel together; an absolute
        # path is only correct while the capture set stays exactly where it was
        # written, and a set of several-hundred-megabyte captures gets moved.
        # Readers resolve a relative tiff_path against the sidecar's own
        # directory, so a moved capture set stays analysable.
        sidecar["tiff_path"] = os.path.basename(tiff_path)
        sidecar["tiff_path_at_capture"] = tiff_path
        sidecar["probe_case"] = case
        sidecar["probe_label"] = label
        # When this capture was taken. A re-run writes over a capture of the
        # same name, but a job that is skipped or fails leaves the PREVIOUS
        # run's file sitting there looking current -- and nothing else in the
        # sidecar distinguishes the two. This is how a stale capture is spotted
        # without having to trust a file modification time that copying resets.
        sidecar["captured_at"] = _utc_now_iso()
        with open(sidecar_path, "w") as handle:
            json.dump(sidecar, handle, indent=2, sort_keys=True)

    record = {
        "case": case,
        "label": "{0}/{1}".format(case, label),
        "tiff_path": tiff_path,
        "sidecar_path": sidecar_path,
        # bounds_xy keeps production's own contract: the rectangle the view
        # was actually cropped to, or null when no crop could be applied.
        "bounds_xy": sidecar.get("bounds_xy"),
        "resolution": sidecar.get("resolution"),
        "color_assignment_count": out.get("color_assignment_count"),
        "paint_failures": sidecar.get("paint_failures"),
        "applied_display_style": sidecar.get("applied_display_style"),
        "applied_smooth_edges": sidecar.get("applied_smooth_edges"),
        "applied_show_shadows": sidecar.get("applied_show_shadows"),
        "uncolorable_link_categories": sidecar.get("uncolorable_link_categories"),
        "failed_link_categories": sidecar.get("failed_link_categories"),
        "capture_ms": elapsed_ms,
        "grid": {"W": int(raster.W), "H": int(raster.H),
                 "cell_size_ft": float(raster.cell_size_ft)},
    }
    if os.path.exists(tiff_path):
        record["file_size_bytes"] = int(os.path.getsize(tiff_path))
        # Byte identity is D1's whole question; recorded here so the answer
        # survives even if the analyzer never runs.
        record["sha256"] = _sha256_file(tiff_path)
        record["dimensions_px"] = _image_dimensions(tiff_path)
    return record


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

def _case_d1(ctx, repetitions):
    """D1: repeat one capture with nothing changed and compare the bytes."""
    exports = []
    for index in range(max(2, int(repetitions))):
        exports.append(ctx["capture"](ctx["cfg"], "d1_determinism",
                                      "rep{0}".format(index)))
    digests = [record.get("sha256") for record in exports]
    return exports, {"repetitions": len(exports), "sha256_values": digests,
                     "byte_identical": len(set(digests)) == 1 and None not in digests}


def production_category_load(doc, view, cfg, diag=None):
    """Per-category element counts, taken from production's own collection.

    D2 grows visible content one bucket at a time and asks where drift starts.
    That only localizes an onset if a bucket is a meaningful amount of drawn
    content, which means the buckets have to be built from the element set the
    capture actually paints -- production's -- and ordered by how much each
    category contributes. Enumerating ``doc.Settings.Categories`` instead
    includes every category the document defines whether or not this view
    draws anything from it, so empty categories consume buckets and the
    category carrying the load can land in an arbitrary late step.

    Returns (counts_by_category_id, uncategorized_count, collected_count).
    """
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements

    raster = init_view_raster(doc, view, cfg, diag=diag)
    elements = collect_view_elements(doc, view, raster, diag=diag, cfg=cfg)
    counts, uncategorized, collected = {}, 0, 0
    for element in elements:
        collected += 1
        cid = _safe_int_id(getattr(getattr(element, "Category", None), "Id", None))
        if cid is None:
            uncategorized += 1
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts, uncategorized, collected


def _case_d2(ctx, steps):
    """D2: hold size and density fixed, grow visible content one bucket at a time.

    The view's crop is pinned to the planned rectangle first. Without that,
    hiding model categories shrinks the model extents, resolve_view_bounds
    returns a smaller rectangle, and the export size changes with the content
    -- which is exactly the confound D2 exists to remove.

    Buckets are built from production's collected element set, in descending
    element count, so step N always carries more drawn content than step N-1
    and the step where drift appears is the onset. The crop is pinned before
    the count is taken, so the counts describe the captures rather than some
    other extent.
    """
    from Autodesk.Revit.DB import CategoryType, ElementId
    doc, view = ctx["doc"], ctx["view"]

    _mutate(doc, "d2_pin_crop", lambda: _set_view_crop(view, ctx["bounds_xy"]))

    load, uncategorized, collected = production_category_load(
        doc, view, ctx["cfg"], diag=ctx.get("diag"))

    hideable, pinned_visible = [], []
    for cat in doc.Settings.Categories:
        cid = getattr(cat, "Id", None)
        cid_int = _safe_int_id(cid)
        if cid_int is None or not load.get(cid_int):
            continue
        try:
            if cat.CategoryType != CategoryType.Model:
                continue
            entry = {"id": cid_int, "name": getattr(cat, "Name", None),
                     "collected_element_count": int(load[cid_int]),
                     "was_hidden": bool(view.GetCategoryHidden(cid))}
            if not view.CanCategoryBeHidden(cid):
                # Carries load but cannot be hidden: visible in every step,
                # including step 0. Recorded, because it is the floor the
                # sweep starts from and it is not zero.
                pinned_visible.append(entry)
                continue
            hideable.append(entry)
        except Exception:
            continue
    hideable = [item for item in hideable if not item["was_hidden"]]
    # Descending painted load; category id only breaks ties, so the order is
    # deterministic across runs.
    hideable.sort(key=lambda item: (-item["collected_element_count"], item["id"]))
    load_note = {"collected_element_count": collected,
                 "uncategorized_element_count": uncategorized,
                 "always_visible_categories": [
                     {"id": c["id"], "name": c["name"],
                      "collected_element_count": c["collected_element_count"]}
                     for c in pinned_visible],
                 "always_visible_element_count": sum(
                     c["collected_element_count"] for c in pinned_visible)}
    if not hideable:
        return [], dict(load_note, categories=[], skipped=(
            "production collected {0} element(s) here, but no category carrying any of "
            "them is both visible and hideable in this view".format(collected)))

    def set_hidden(items, hidden):
        for item in items:
            view.SetCategoryHidden(ElementId(int(item["id"])), bool(hidden))

    steps = max(1, min(int(steps), len(hideable)))
    size = (len(hideable) + steps - 1) // steps
    buckets = [hideable[start:start + size] for start in range(0, len(hideable), size)]

    _mutate(doc, "d2_hide_all", lambda: set_hidden(hideable, True))
    exports, step_records, revealed = [], [], []
    base_load = load_note["always_visible_element_count"]
    for index, bucket in enumerate(buckets + [None]):
        record = ctx["capture"](ctx["cfg"], "d2_category_load", "step{0}".format(index))
        revealed_load = sum(item["collected_element_count"] for item in revealed)
        record["visible_categories"] = [item["name"] for item in revealed]
        record["revealed_element_count"] = revealed_load + base_load
        exports.append(record)
        step_records.append({"step": index,
                             "visible_category_count": len(revealed),
                             "visible_categories": [item["name"] for item in revealed],
                             # What the step is a step OF: the sweep is over
                             # drawn content, and category count alone does not
                             # say how much of it each step added.
                             "revealed_element_count": revealed_load + base_load,
                             "revealed_element_count_from_buckets": revealed_load,
                             "color_assignment_count": record.get("color_assignment_count"),
                             "tiff_path": record["tiff_path"]})
        if bucket is None:
            break
        revealed = revealed + bucket
        _mutate(doc, "d2_unhide_{0}".format(index),
                lambda b=bucket: set_hidden(b, False))
    return exports, dict(load_note,
                         steps=step_records,
                         crop_pinned_to=list(ctx["bounds_xy"]),
                         bucket_order="descending production-collected element count",
                         categories=[{"id": c["id"], "name": c["name"],
                                      "collected_element_count": c["collected_element_count"]}
                                     for c in hideable])


def _case_d3(ctx, sweep):
    """D3: hold content fixed and sweep the requested size, through DPI."""
    exports, requested = [], []
    for entry in resolve_size_sweep(sweep, ctx["native_pixel_size"]):
        dpi = dpi_for_pixel_width(entry["requested_pixel_size"], ctx["paper_width_in"])
        cfg = build_capture_config(ctx["cfg_output_dir"], export_dpi=dpi)
        record = ctx["capture"](cfg, "d3_size_sweep", entry["label"])
        record["planned_pixel_size"] = entry["requested_pixel_size"]
        record["planned_export_dpi"] = dpi
        exports.append(record)
        requested.append({"label": entry["label"],
                          "planned_pixel_size": entry["requested_pixel_size"],
                          "planned_export_dpi": dpi,
                          "accepted": (record.get("resolution") or {}).get("pixel_size")})
    return exports, {"requested": requested,
                     "note": "size is driven through cfg.color_id_buffer_export_dpi so "
                             "production's own pixel-size formula and its "
                             "MAX_STAGE_A_PIXEL_SIZE clamp both stay on the code path"}


def _case_d4(ctx):
    """D4: lower DPI until native fits the ceiling, then capture at exactly that."""
    lowered = dpi_for_native_ceiling(ctx["bounds_xy"], ctx["view_scale"], ctx["export_dpi"])
    cfg = build_capture_config(ctx["cfg_output_dir"], export_dpi=lowered)
    record = ctx["capture"](cfg, "d4_dpi_vs_pixel_size", "dpi{0:.2f}".format(lowered))
    return [record], {
        "original_export_dpi": ctx["export_dpi"],
        "lowered_export_dpi": lowered,
        "width_ceiling_px": D4_WIDTH_CEILING,
        "height_ceiling_px": D4_HEIGHT_CEILING,
        "native_ceiling_px": D4_WIDTH_CEILING,
        "pixel_size_at_lowered_dpi": int(round(lowered * ctx["paper_width_in"])),
        "accepted": (record.get("resolution") or {}).get("pixel_size"),
        # UNCONFIRMED: ImageExportOptions is driven purely through PixelSize.
        # No run has shown Revit exposing a DPI input that changes the raster
        # independently of it.
        "dpi_is_request_side_only": "UNCONFIRMED",
    }


def _case_d5(ctx, grid):
    """D5: re-tile the same rectangle into NxN crops cut on the pixel lattice.

    Each tile is selected by pinning the view's crop; production then derives
    that tile's bounds, grid, and pixel size from it the way it derives any
    view's, so a tile capture is a production capture of a smaller view.
    """
    doc, view = ctx["doc"], ctx["view"]
    tiles = snap_to_pixel_lattice(ctx["bounds_xy"], ctx["export_dpi"], ctx["view_scale"], grid)
    exports, tile_records = [], []
    for tile in tiles:
        label = "r{0}c{1}".format(tile["row"], tile["col"])
        applied = {}
        _mutate(doc, "d5_crop_{0}".format(label),
                lambda t=tile: applied.__setitem__("bounds", _set_view_crop(view, t["bounds_xy"])))
        # Hold each tile at the same density as the full-view capture rather
        # than letting FitToPage rescale it: the tile is narrower, so its
        # paper width is smaller and the same DPI yields proportionally fewer
        # pixels -- which is what "same density, smaller crop" means.
        record = ctx["capture"](ctx["cfg"], "d5_crop_tiles", label)
        record.update({"tile_row": tile["row"], "tile_col": tile["col"],
                       "seam_residual_px": tile["seam_residual_px"],
                       "requested_tile_bounds_xy": tile["bounds_xy"],
                       "crop_applied": applied.get("bounds") is not None})
        exports.append(record)
        tile_records.append({
            "row": tile["row"], "col": tile["col"],
            "planned_width_px": tile["requested_pixel_size"],
            "native_width_px": tile["native_width_px"],
            "seam_residual_px": tile["seam_residual_px"],
            "accepted": (record.get("resolution") or {}).get("pixel_size"),
            "actual_dimensions_px": record.get("dimensions_px"),
        })
    return exports, {"grid": int(grid), "tiles": tile_records}


# --------------------------------------------------------------------------
# Native driver
# --------------------------------------------------------------------------

def _run_native(raw_view, output_dir, selection="all", repetitions=DEFAULT_REPETITIONS,
                pixel_sizes=None, export_dpi=DEFAULT_EXPORT_DPI, d2_steps=DEFAULT_D2_STEPS,
                tile_grid=DEFAULT_TILE_GRID, max_elements=None, repo_root=None):
    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "source": source_records(),
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {}, "view": {}, "geometry": {}, "cases": {},
        "exports": [], "capture_directory": None,
        "capture_path": "pipeline.init_view_raster + revit.collection.collect_view_elements "
                        "+ color_id_buffer.export_color_id_buffer_view",
        "transaction_group": {"started": False, "rollback_attempted": False,
                              "rollback_succeeded": False},
        "state": {"before": {}, "after": {}, "restored": None, "differences": []},
        "unconfirmed_api_assumptions": [
            "ZoomFitType.FitToPage + FitDirectionType.Horizontal makes PixelSize the output width",
            "ImageExportOptions exposes no DPI input independent of PixelSize",
            "Revit clamps export aspect to 10:1 by symmetric padding of the short axis",
            "Revit's PixelSize ceiling is a fixed value rather than install/version dependent",
        ],
        "diagnostics": None, "exceptions": [], "warnings": [], "timings_ms": {},
    }
    doc = _get_current_document()
    group = None
    group_started = False
    diag = None
    try:
        _ensure_repo_import_path(output_dir, repo_root)
        from vop_interwoven.core.diagnostics import Diagnostics
        diag = Diagnostics()
        view = _unwrap_dynamo(raw_view)
        if view is None:
            raise ValueError("IN[0] did not resolve to a Revit view")
        cases = select_cases(selection)
        out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
        run = run_token(out_base)
        probe_dir = os.path.join(out_base, "drift_onset_probe")
        # Production writes into <cfg.output_dir>/color_id_buffer/; pointing it
        # at a probe-owned staging directory keeps a probe run from clobbering
        # a real Stage A output directory.
        staging_dir = os.path.join(probe_dir, "_staging")
        capture_dir = os.path.join(probe_dir, "captures")
        for path in (probe_dir, staging_dir, capture_dir):
            if not os.path.isdir(path):
                os.makedirs(path)
        report["capture_directory"] = capture_dir

        base_cfg = build_capture_config(staging_dir, export_dpi=export_dpi)
        geometry = plan_capture_geometry(doc, view, base_cfg, diag=diag)
        report["geometry"] = dict(geometry, bounds_xy=list(geometry["bounds_xy"]))
        report["inputs"] = {"output_directory": out_base, "cases": cases,
                            "repetitions": int(repetitions), "pixel_sizes": pixel_sizes,
                            "export_dpi": float(export_dpi), "d2_steps": int(d2_steps),
                            "tile_grid": int(tile_grid), "max_elements": max_elements}
        report["view"] = {
            "id": _safe_int_id(view.Id), "name": getattr(view, "Name", None),
            "scale": geometry["view_scale"],
            "bounds_xy": list(geometry["bounds_xy"]),
            "bounds_source": geometry["bounds_source"],
            "native_pixel_size": geometry["native_pixel_size"],
            "predicted_aspect": ((geometry["bounds_xy"][2] - geometry["bounds_xy"][0])
                                 / (geometry["bounds_xy"][3] - geometry["bounds_xy"][1])),
        }
        if max_elements:
            report["warnings"].append({
                "stage": "inputs",
                "message": "max_elements is ignored: captures are produced by "
                           "export_color_id_buffer_view, which resolves its own element "
                           "set. Limiting it would make the capture not a production one.",
            })

        _force_close_dynamo_transaction()
        report["state"]["before"] = _snapshot(doc, view)

        from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
        group = TransactionGroup(doc, "VOP Stage A Drift Onset Probe")
        group_started = group.Start() == TransactionStatus.Started
        report["transaction_group"]["started"] = group_started
        if not group_started:
            raise RuntimeError("TransactionGroup.Start did not start")

        def capture(cfg, case, label):
            return production_capture(doc, view, cfg, case, label, capture_dir,
                                      diag=diag, run=run)

        ctx = {"doc": doc, "view": view, "cfg": base_cfg, "capture": capture, "diag": diag,
               "cfg_output_dir": staging_dir, "export_dpi": float(export_dpi),
               "bounds_xy": geometry["bounds_xy"], "view_scale": geometry["view_scale"],
               "paper_width_in": geometry["paper_width_in"],
               "native_pixel_size": geometry["native_pixel_size"]}

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
                report["transaction_group"]["rollback_succeeded"] = \
                    status == TransactionStatus.RolledBack
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

        # Production's own diagnostics are evidence: pixel-size clamping, a
        # read-only phase filter, a crop that would not apply, a display style
        # that could not be set. Every one of those changes how a capture
        # should be read.
        if diag is not None:
            try:
                report["diagnostics"] = diag.to_dict()
            except Exception as ex:
                report["exceptions"].append(_exception_record("diagnostics_serialize", ex))

        try:
            out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
            probe_dir = os.path.join(out_base, "drift_onset_probe")
            if not os.path.isdir(probe_dir):
                os.makedirs(probe_dir)
            json_path = os.path.join(probe_dir, "{0}.drift_onset.json".format(
                _stem(run_token(out_base), report["view"].get("id"),
                      _safe_name(report["view"].get("name") or "view"))))
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
    artifacts = []
    for record in native.get("exports", []):
        artifacts.extend(path for path in (record.get("tiff_path"), record.get("sidecar_path"))
                         if path)
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
