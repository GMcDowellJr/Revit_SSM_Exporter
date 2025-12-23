# -*- coding: utf-8 -*-
"""
Revit element collection for SSM/VOP exporter.
Handles collection of 3D/2D elements and linked model proxies.
"""

from core.config import CONFIG
from geometry import transforms
from geometry import grid

# ------------------------------------------------------------
# Revit API context (set by main file)
# ------------------------------------------------------------

DOC = None
View = None
ViewType = None
CategoryType = None
ImportInstance = None
FilteredElementCollector = None
BuiltInCategory = None
BuiltInParameter = None
RevitLinkInstance = None
VisibleInViewFilter = None
Dimension = None
LinearDimension = None
TextNote = None
IndependentTag = None
RoomTag = None
FilledRegion = None
DetailCurve = None
CurveElement = None
FamilyInstance = None
XYZ = None


def set_revit_context(doc, view_cls, view_type_cls, category_type_cls, import_instance_cls,
                      filtered_element_collector_cls, built_in_category_cls, built_in_parameter_cls,
                      revit_link_instance_cls, visible_in_view_filter_cls,
                      dimension_cls, linear_dimension_cls, text_note_cls, independent_tag_cls,
                      room_tag_cls, filled_region_cls, detail_curve_cls, curve_element_cls,
                      family_instance_cls, xyz_cls):
    """Set the Revit API context for this module."""
    global DOC, View, ViewType, CategoryType, ImportInstance, FilteredElementCollector
    global BuiltInCategory, BuiltInParameter, RevitLinkInstance, VisibleInViewFilter
    global Dimension, LinearDimension, TextNote, IndependentTag, RoomTag, FilledRegion
    global DetailCurve, CurveElement, FamilyInstance, XYZ

    DOC = doc
    View = view_cls
    ViewType = view_type_cls
    CategoryType = category_type_cls
    ImportInstance = import_instance_cls
    FilteredElementCollector = filtered_element_collector_cls
    BuiltInCategory = built_in_category_cls
    BuiltInParameter = built_in_parameter_cls
    RevitLinkInstance = revit_link_instance_cls
    VisibleInViewFilter = visible_in_view_filter_cls
    Dimension = dimension_cls
    LinearDimension = linear_dimension_cls
    TextNote = text_note_cls
    IndependentTag = independent_tag_cls
    RoomTag = room_tag_cls
    FilledRegion = filled_region_cls
    DetailCurve = detail_curve_cls
    CurveElement = curve_element_cls
    FamilyInstance = family_instance_cls
    XYZ = xyz_cls


def _build_navigation_noise_cat_ids():
    """
    Navigation / symbolic / view-mechanic / modifier categories that must NOT
    contribute 3D occupancy. These do not own meaningful geometry regions:
    grids, section heads, cameras, reveals, etc.
    Applies to both host and linked models.
    """
    ids = set()
    if BuiltInCategory is None:
        return ids

    names = [
        # Navigation & annotation mechanics
        "OST_Grids",
        "OST_GridHeads",
        "OST_Levels",
        "OST_LevelHeads",
        "OST_SectionHeads",
        "OST_SectionMarks",
        "OST_ElevationMarks",
        "OST_CalloutHeads",
        "OST_ReferenceViewer",
        "OST_Viewers",
        "OST_Cameras",

        # Scene/view symbolic controls
        "OST_SunPath",
        "OST_SectionBox",
        "OST_AdaptivePoints",

        # Geometry modifiers that do *not* own AREAL area and whose effects
        # are already baked into host solids (e.g., wall cuts)
        "OST_Reveals",
    ]

    for name in names:
        try:
            bic = getattr(BuiltInCategory, name, None)
            if bic is not None:
                ids.add(int(bic))
        except Exception:
            continue

    return ids

def _build_model_suppression_cat_ids():
    """
    Build a set of category Ids to suppress from the 3D pass
    (host + linked). These are categories that should never
    contribute to 3D occupancy in VOP v47.

    Note: 2D pass (host-only) is unaffected.
    """
    ids = set()

    if BuiltInCategory is None or DOC is None:
        return ids

    # Names must match Autodesk.Revit.DB.BuiltInCategory enum names
    names = [
        # Volumetric / analytical “non-geometry” we never want in 3D
        "OST_Rooms",
        "OST_Areas",
        "OST_MEPSpaces",

        "OST_DetailComponents",  # Detail Items
        "OST_Lines",             # Model Lines
        "OST_PointClouds",       # Point cloud instances
    ]

    for name in names:
        try:
            bic = getattr(BuiltInCategory, name, None)
            if bic is None:
                continue
            cat = DOC.Settings.Categories.get_Item(bic)
            if cat is not None:
                ids.add(cat.Id.IntegerValue)
        except Exception:
            # Defensive: bad categories shouldn't break exporter
            continue

    return ids

def _get_excluded_3d_cat_ids():
    """
    Union of navigation noise + model suppression categories.
    This is the authoritative exclusion set for 3D occupancy
    (host AND linked).
    """
    out = set()
    out.update(_build_navigation_noise_cat_ids())
    out.update(_build_model_suppression_cat_ids())
    return out

def _build_occupancy_preview_rects(view, grid_data, occupancy, config, logger):
    """Build PolyCurve rectangles for occupancy cells, grouped by layer.

    Returns (rects_3d_only, rects_2d_only, rects_2d_over_3d).

    Controlled by:
        debug.enable_region_previews
        debug.max_region_areal_cells
    """
    debug_cfg = config.get("debug", {}) if isinstance(config, dict) else {}
    enable_region_previews = bool(debug_cfg.get("enable_region_previews", False))
    if not enable_region_previews:
        return [], [], []

    if DSPoint is None or DSPolyCurve is None:
        return [], [], []

    if not isinstance(occupancy, dict):
        return [], [], []

    occ_map = occupancy.get("occupancy_map") or {}
    if not occ_map:
        return [], [], []

    # Basic grid info
    if not isinstance(grid_data, dict):
        return [], [], []
    if grid_data.get("origin_model_xy") is None or grid_data.get("cell_size_model") is None:
        return [], [], []

    max_cells = debug_cfg.get("max_region_areal_cells", 512)
    if max_cells is None or max_cells <= 0:
        max_cells = 512

    occ_cfg = config.get("occupancy", {}) if isinstance(config, dict) else {}
    code_3d = occ_cfg.get("code_3d_only", 0)
    code_2d = occ_cfg.get("code_2d_only", 1)
    code_2d_over_3d = occ_cfg.get("code_2d_over_3d", 2)

    rects_3d = []
    rects_2d = []
    rects_2d_over_3d = []

    count_total = 0
    for cell, code in occ_map.items():
        if count_total >= max_cells:
            break
        try:
            i, j = cell
        except Exception:
            continue

        pc = grid._make_cell_rect_polycurve(view, grid_data, i, j)
        if pc is None:
            continue

        if code == code_3d:
            rects_3d.append(pc)
        elif code == code_2d:
            rects_2d.append(pc)
        elif code == code_2d_over_3d:
            rects_2d_over_3d.append(pc)
        else:
            # Unknown code; ignore for preview
            continue

        count_total += 1

    logger.info(
        "Debug: built {0} occupancy preview cell(s) (3D={1}, 2D={2}, 2D-over-3D={3})".format(
            count_total, len(rects_3d), len(rects_2d), len(rects_2d_over_3d)
        )
    )

    return rects_3d, rects_2d, rects_2d_over_3d

def _build_occupancy_png(view, grid_data, occupancy_map, config, logger):
    """
    Render a PNG from occupancy_map.

    occupancy_map: dict[(i,j)] -> occupancy_code
    codes come from config["occupancy"]:
        code_empty, code_3d_only, code_2d_only, code_2d_over_3d

    Uses config["occupancy_png"] for:
        - enabled (bool, default True)
        - pixels_per_cell (int, default 4)

    Returns full file path as string, or None on failure/disabled.
    """
    # Guard on drawing support
    if Bitmap is None or Drawing is None or ImageFormat is None:
        logger.warn("Occupancy: System.Drawing not available; PNG export skipped.")
        return None

    # Basic grid dims
    try:
        n_i = int(grid_data.get("grid_n_i") or 0)
        n_j = int(grid_data.get("grid_n_j") or 0)
    except Exception:
        return None

    if n_i <= 0 or n_j <= 0:
        return None
    if not occupancy_map:
        return None

    # Config / debug flags
    debug_cfg = {}
    occ_cfg = {}
    export_cfg = {}
    occ_png_cfg = {}

    if isinstance(config, dict):
        debug_cfg   = config.get("debug", {}) or {}
        occ_cfg     = config.get("occupancy", {}) or {}
        export_cfg  = config.get("export", {}) or {}
        occ_png_cfg = config.get("occupancy_png", {}) or {}

    # Optional flags (both must allow PNG)
    enable_png_debug = bool(debug_cfg.get("enable_occupancy_png", True))
    enable_png_cfg   = bool(occ_png_cfg.get("enabled", True))
    if not (enable_png_debug and enable_png_cfg):
        return None

    # pixels_per_cell from occupancy_png config
    try:
        pixels_per_cell = int(occ_png_cfg.get("pixels_per_cell", 4))
    except Exception:
        pixels_per_cell = 4

    if pixels_per_cell < 1:
        pixels_per_cell = 1
    if pixels_per_cell > 50:
        pixels_per_cell = 50  # sanity cap

    # bitmap size in pixels
    width_px  = n_i * pixels_per_cell
    height_px = n_j * pixels_per_cell

    # Occupancy codes (fall back to the defaults if missing)
    def _int_or(d, key, default):
        try:
            return int(d.get(key, default))
        except Exception:
            return default

    #code_empty = _int_or(occ_cfg, "code_empty", 0)
    code_3d    = _int_or(occ_cfg, "code_3d_only", 0)
    code_2d    = _int_or(occ_cfg, "code_2d_only", 1)
    code_2d3d  = _int_or(occ_cfg, "code_2d_over_3d", 2)

    # Colors: 3D-only, 2D-only, 2D-over-3D (3 colors)
    col_empty = Drawing.Color.White
    col_3d    = Drawing.Color.FromArgb(192, 192, 192)  # light gray
    col_2d    = Drawing.Color.FromArgb(0, 0, 255)      # blue
    col_2d3d  = Drawing.Color.FromArgb(255, 0, 255)    # magenta

    # Create bitmap (scaled by pixels_per_cell)
    bmp = Bitmap(width_px, height_px)

    # Initialize background
    for x in range(width_px):
        for y in range(height_px):
            bmp.SetPixel(x, y, col_empty)

    # Fill cells; flip j so origin is bottom-left visually
    for i in range(n_i):
        for j in range(n_j):
            code = occupancy_map.get((i, j))
            if code is None:
                continue

            if code == code_3d:
                col = col_3d
            elif code == code_2d:
                col = col_2d
            elif code == code_2d3d:
                col = col_2d3d
            else:
                # empty / unknown -> already background
                continue

            # map cell (i, j) to pixel block
            x0 = i * pixels_per_cell
            # flip vertically so j=0 is bottom row
            y0 = (n_j - 1 - j) * pixels_per_cell
            x1 = x0 + pixels_per_cell
            y1 = y0 + pixels_per_cell

            # fill the block
            for px in range(x0, min(x1, width_px)):
                for py in range(y0, min(y1, height_px)):
                    bmp.SetPixel(px, py, col)

    # Resolve output directory
    out_dir = export_cfg.get("output_dir")
    if not out_dir:
        # fallback to %TEMP%
        try:
            temp_root = System.IO.Path.GetTempPath() if System is not None else None
        except Exception:
            temp_root = None
        out_dir = temp_root or r"C:\Temp"

    # Optionally tuck into an "occupancy" subfolder
    if System is not None:
        occ_dir = System.IO.Path.Combine(out_dir, "occupancy")
    else:
        occ_dir = os.path.join(out_dir, "occupancy")

    try:
        if not os.path.isdir(occ_dir):
            os.makedirs(occ_dir)
    except Exception:
        # if we can't create the subfolder, fall back to out_dir
        occ_dir = out_dir
        try:
            if not os.path.isdir(occ_dir):
                os.makedirs(occ_dir)
        except Exception as ex:
            logger.warn(
                "Occupancy: could not create PNG output dir '{0}': {1}"
                .format(occ_dir, ex)
            )
            return None

    # Build a safe filename
    try:
        view_id_val = int(view.Id.IntegerValue)
    except Exception:
        view_id_val = 0

    try:
        view_name = view.Name or ""
    except Exception:
        view_name = ""

    try:
        import re
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", view_name)
    except Exception:
        safe_name = "view"

    file_name = "VOP_occ_{0}_{1}.png".format(view_id_val, safe_name)
    full_path = os.path.join(occ_dir, file_name)

    # Save PNG
    try:
        bmp.Save(full_path, ImageFormat.Png)
    except Exception as ex:
        logger.warn("Occupancy: failed to save PNG '{0}': {1}".format(full_path, ex))
        return None

    logger.info("Occupancy: PNG saved to '{0}'".format(full_path))
    return full_path

# ------------------------------------------------------------
# CLIP VOLUME
# ------------------------------------------------------------

def build_clip_volume_for_view(view, config, logger):
    """
    v47 Stage-2 clip volume authority.

    Returns:
      {
        "is_valid": bool,
        "kind": "plan"|"vertical"|"drafting",
        "corners_host": [XYZ]*8 (host model coords) or None,
        "obb_host": {center, axes, extents} (dict) or None,
        "depth_mode": "model_z"|"view_dir"|"none",
        "z_min": float|None,
        "z_max": float|None,
        "far": float|None
      }
    """
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    logger.info("ClipVolume: building clip volume for view Id={0}".format(view_id_val))

    clip = {
        "is_valid": False,
        "kind": "drafting",
        "corners_host": None,
        "obb_host": None,
        "depth_mode": "none",
        "z_min": None,
        "z_max": None,
        "far": None,
        "z0_local": None,
        "z1_local": None,
    }

    # Drafting views: XY only
    try:
        if hasattr(ViewType, "DraftingView") and getattr(view, "ViewType", None) == ViewType.DraftingView:
            clip["is_valid"] = True
            clip["kind"] = "drafting"
            clip["depth_mode"] = "none"
            return clip
    except Exception:
        pass

    # Need CropBox for any model view clip volume
    try:
        crop_bb = getattr(view, "CropBox", None)
    except Exception:
        crop_bb = None
    if crop_bb is None or getattr(crop_bb, "Min", None) is None or getattr(crop_bb, "Max", None) is None:
        return clip

    # Plans/RCP: depth is model Z slab from ViewRange
    z_min, z_max = _get_plan_view_vertical_range(view, config, logger)
    if z_min is not None and z_max is not None:
        corners_host = transforms.host_crop_prism_corners_model(view, z_min, z_max, XYZ)
        if corners_host:
            clip["is_valid"] = True
            clip["kind"] = "plan"
            clip["depth_mode"] = "model_z"
            clip["corners_host"] = corners_host
            clip["z_min"] = z_min
            clip["z_max"] = z_max
            try:
                clip["obb_host"] = transforms.obb_from_ordered_corners(corners_host)
            except Exception:
                clip["obb_host"] = None
        return clip

    # Vertical views (sections/elevations): depth is along view direction (CropBox local Z).
    # We use CropBox local min/max for XY, and depth from far clip when available.
    # If far clip is unavailable/off, we fall back to CropBox local Z span.
    try:
        trf = crop_bb.Transform
    except Exception:
        trf = None
    if trf is None:
        return clip

    # Local crop extents
    try:
        minL = crop_bb.Min
        maxL = crop_bb.Max
        min_x, max_x = float(minL.X), float(maxL.X)
        min_y, max_y = float(minL.Y), float(maxL.Y)
        near_z = float(minL.Z)
        far_z_default = float(maxL.Z)
    except Exception:
        return clip

    # Try to get far clip distance (preferred)
    far_dist = None
    try:
        # Many vertical views expose this parameter
        p_far = view.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR)
        if p_far is not None:
            far_dist = float(p_far.AsDouble())
    except Exception:
        far_dist = None

    # Determine local Z span
    if far_dist is not None and far_dist > 0:
        z0 = near_z
        z1 = near_z + far_dist
        clip["far"] = far_dist
    else:
        z0 = near_z
        z1 = far_z_default
        clip["far"] = None

    clip["z0_local"] = z0
    clip["z1_local"] = z1

    # Build 8 corners in *local crop coords*, then transform to host model coords
    try:
        local_corners = [
            XYZ(min_x, min_y, z0),
            XYZ(min_x, min_y, z1),
            XYZ(min_x, max_y, z0),
            XYZ(min_x, max_y, z1),
            XYZ(max_x, min_y, z0),
            XYZ(max_x, min_y, z1),
            XYZ(max_x, max_y, z0),
            XYZ(max_x, max_y, z1),
        ]
        corners_host = [trf.OfPoint(p) for p in local_corners]
    except Exception:
        return clip

    clip["is_valid"] = True
    clip["kind"] = "vertical"
    clip["depth_mode"] = "view_dir"
    clip["corners_host"] = corners_host
    try:
        clip["obb_host"] = _obb_from_ordered_corners(corners_host)
    except Exception:
        clip["obb_host"] = None
    return clip

def _get_plan_view_vertical_range(host_view, config, logger):
    """
    Return (z_min, z_max) in model coords representing the host view's
    effective vertical clip range for plan/ceiling/area views, based on
    ViewRange. Returns (None, None) if not available.

    Optional refinement (flagged):
      - Plans/Area: CUT_DOWN => [min(bottom, depth) .. cut]
      - RCP:        CUT_UP   => [cut .. max(top, cut)]
    """
    if ViewType is None:
        return (None, None)

    vtype = getattr(host_view, "ViewType", None)
    if vtype not in (
        getattr(ViewType, "FloorPlan", None),
        getattr(ViewType, "CeilingPlan", None),
        getattr(ViewType, "AreaPlan", None),
    ):
        return (None, None)

    if DOC is None:
        return (None, None)

    try:
        import Autodesk.Revit.DB as RevitDB
    except Exception:
        RevitDB = None
    if RevitDB is None:
        return (None, None)

    try:
        vr = host_view.GetViewRange()
    except Exception:
        vr = None
    if vr is None:
        return (None, None)

    PlanViewPlane = getattr(RevitDB, "PlanViewPlane", None)
    if PlanViewPlane is None:
        return (None, None)

    def _plane_z(plane):
        try:
            lvl_id = vr.GetLevelId(plane)
            if lvl_id is None or lvl_id == RevitDB.ElementId.InvalidElementId:
                return None
            lvl = DOC.GetElement(lvl_id)
            base_z = getattr(lvl, "Elevation", None)
            off = vr.GetOffset(plane)
            if base_z is None or off is None:
                return None
            return base_z + off
        except Exception:
            return None

    top_z    = _plane_z(PlanViewPlane.TopClipPlane)
    cut_z    = _plane_z(PlanViewPlane.CutPlane)
    bottom_z = _plane_z(PlanViewPlane.BottomClipPlane)
    depth_z  = _plane_z(PlanViewPlane.ViewDepthPlane)

    zs = [z for z in (top_z, cut_z, bottom_z, depth_z) if z is not None]
    if not zs:
        return (None, None)

    # FULL conservative slab
    clip_cfg = config.get("clip_volume", {}) if isinstance(config, dict) else {}
    use_cut_slab = bool(clip_cfg.get("use_cut_slab", False))

    slab_mode = "FULL"
    if use_cut_slab:
        try:
            if getattr(host_view, "ViewType", None) == getattr(ViewType, "CeilingPlan", None):
                slab_mode = "CUT_UP"    # RCP
            else:
                slab_mode = "CUT_DOWN"  # plans/area
        except Exception:
            slab_mode = "FULL"

    low_candidates  = [z for z in (bottom_z, depth_z) if z is not None]
    high_candidates = [z for z in (top_z, cut_z) if z is not None]

    if slab_mode == "CUT_DOWN" and cut_z is not None and low_candidates:
        z_min = min(low_candidates)
        z_max = cut_z
    elif slab_mode == "CUT_UP" and cut_z is not None and high_candidates:
        z_min = cut_z
        z_max = max(high_candidates)
    else:
        if not low_candidates or not high_candidates:
            z_min = min(zs)
            z_max = max(zs)
        else:
            z_min = min(low_candidates)
            z_max = max(high_candidates)

    if z_min > z_max:
        z_min, z_max = z_max, z_min

    try:
        logger.info(
            "Projection: plan view Id={0} vertical clip mode={1} z_min={2:.3f}, z_max={3:.3f}".format(
                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                slab_mode,
                z_min,
                z_max,
            )
        )
    except Exception:
        pass

    return (z_min, z_max)

# ------------------------------------------------------------
# PROJECTION: COLLECTION HELPERS
# ------------------------------------------------------------

def _summarize_elements_by_type(elems):
    counts = {}
    for e in elems:
        try:
            tname = e.GetType().Name
        except Exception:
            tname = "Element"
        counts[tname] = counts.get(tname, 0) + 1
    return counts

def _summarize_elements_by_category(elems):
    counts = {}
    for e in elems:
        cat = getattr(e, "Category", None)
        name = getattr(cat, "Name", None) if cat is not None else "<No Category>"
        counts[name] = counts.get(name, 0) + 1
    return counts

def _get_host_visible_model_cat_ids(host_view, logger):
    """
    Return a set of IntegerValue ids for *model* categories that are
    visible in the given host view, based on VG (view.GetCategoryHidden).

    If anything goes wrong, returns None and we skip host-VG filtering.
    """
    doc = getattr(host_view, "Document", None)
    if doc is None or CategoryType is None:
        logger.warn("Link3D: host view has no Document/CategoryType; skipping host VG filter.")
        return None

    settings = getattr(doc, "Settings", None)
    categories = getattr(settings, "Categories", None) if settings is not None else None
    if categories is None:
        logger.warn("Link3D: cannot read Settings.Categories; skipping host VG filter.")
        return None

    get_hidden = getattr(host_view, "GetCategoryHidden", None)
    visible_ids = set()

    for cat in categories:
        if cat is None:
            continue
        try:
            if getattr(cat, "CategoryType", None) != CategoryType.Model:
                continue
        except Exception:
            continue

        cat_id = getattr(cat, "Id", None)
        try:
            cat_id_val = getattr(cat_id, "IntegerValue", None)
        except Exception:
            cat_id_val = None

        if cat_id_val is None:
            continue

        # If GetCategoryHidden isn't callable, or throws, fail-open = visible.
        is_hidden = False
        try:
            if callable(get_hidden):
                is_hidden = bool(host_view.GetCategoryHidden(cat_id))
        except Exception:
            is_hidden = False

        if not is_hidden:
            visible_ids.add(cat_id_val)

    view_id_val = getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown")
    logger.info(
        "Link3D: host view Id={0} has {1} visible model categories (VG).".format(
            view_id_val, len(visible_ids)
        )
    )

    return visible_ids if visible_ids else None

class _LinkedElementProxy(object):
    """
    Lightweight proxy that exposes the minimal surface of a Revit element:
    - Id          -> link element's ElementId
    - Category    -> link element's Category
    - LinkInstanceId -> owning RevitLinkInstance Id
    - get_BoundingBox(view) -> host-space BoundingBox (view is ignored)
    - get_Geometry(options) -> underlying link element geometry (link space)
    """
    __slots__ = ("_bb", "_elem", "_link_trf", "Id", "Category", "LinkInstanceId")

    def __init__(self, element, link_inst, host_min, host_max, link_trf):
        class _BB(object):
            __slots__ = ("Min", "Max")
            def __init__(self, mn, mx):
                self.Min = mn
                self.Max = mx

        self._bb = _BB(host_min, host_max)
        self._elem = element                 # underlying element in link doc
        self._link_trf = link_trf            # Transform link → host
        self.Id = getattr(element, "Id", None)
        self.Category = getattr(element, "Category", None)
        self.LinkInstanceId = getattr(link_inst, "Id", None)

    def get_BoundingBox(self, view):
        # view ignored – bounding box is already in host coords
        return self._bb

    def get_Geometry(self, options):
        """
        Delegate to the underlying link element. Geometry comes back in
        *link* coordinates; we apply the link transform later when
        converting points to XY.
        """
        elem = self._elem
        if elem is None:
            return None
        try:
            return elem.get_Geometry(options)
        except Exception:
            return None

# ------------------------------------------------------------
# GEOMETRIC TRANSFORMS
# ------------------------------------------------------------
# Geometric transform and vector math functions imported from transforms.py:
# - transform_bbox_to_host, obb_from_ordered_corners, aabb_center_extents_from_bboxxyz
# - obb_intersects_aabb, host_crop_prism_corners_model
# - Vector math: v3, v_add, v_sub, v_mul, v_dot, v_cross, v_len, v_norm, xyz_to_v

def _build_link_proxies_from_collector(
    link_inst,
    collector,
    host_view,
    logger,
    host_visible_model_cat_ids=None,
    obb_link=None,
):
    """
    Given a FilteredElementCollector in the link document, build per-element
    host-space BBox proxies for model elements.

    If host_visible_model_cat_ids is not None, we treat this link as
    'By Host View' and drop any categories that are hidden in the host view.
    """
    proxies = []

    if collector is None or CategoryType is None:
        return proxies

    try:
        trf = link_inst.GetTransform()
    except Exception:
        trf = None

    if trf is None:
        return proxies

    excluded_ids = _get_excluded_3d_cat_ids()

    cats_included = {}
    cats_excl_host_hidden = {}
    cats_excl_global = {}

    for e in collector:
        # Skip nested links and imports for now
        if RevitLinkInstance is not None and isinstance(e, RevitLinkInstance):
            continue
        if ImportInstance is not None and isinstance(e, ImportInstance):
            continue

        cat = getattr(e, "Category", None)
        cat_type = getattr(cat, "CategoryType", None) if cat is not None else None
        cat_name = getattr(cat, "Name", None) if cat is not None else "<No Category>"

        # Get category id value (if any)
        cat_id_val = None
        if cat is not None:
            try:
                cat_id_val = getattr(getattr(cat, "Id", None), "IntegerValue", None)
            except Exception:
                cat_id_val = None

        # Global 3D exclusion (rooms, grids, levels, etc.)
        if cat_id_val in excluded_ids:
            cats_excl_global[cat_name] = cats_excl_global.get(cat_name, 0) + 1
            continue

        # Keep only model categories
        if cat_type != CategoryType.Model:
            continue

        # Host-VG filter for By Host View / Custom modes
        if host_visible_model_cat_ids is not None:
            if cat_id_val is None or cat_id_val not in host_visible_model_cat_ids:
                cats_excl_host_hidden[cat_name] = cats_excl_host_hidden.get(cat_name, 0) + 1
                continue

        try:
            bb_link = e.get_BoundingBox(None)
        except Exception:
            bb_link = None

        if bb_link is None or bb_link.Min is None or bb_link.Max is None:
            continue

        # Narrow-phase: enforce true transformed host crop volume (OBB in link coords)
        if obb_link is not None:
            try:
                aabb_c, aabb_e = transforms.aabb_center_extents_from_bboxxyz(bb_link)
                if not transforms.obb_intersects_aabb(obb_link, aabb_c, aabb_e):
                    continue
            except Exception:
                # If the test fails unexpectedly, keep the element (underfilter preferred)
                pass

        host_min, host_max = transforms.transform_bbox_to_host(bb_link, trf, XYZ)
        if host_min is None or host_max is None:
            continue

        proxies.append(_LinkedElementProxy(e, link_inst, host_min, host_max, trf))
        cats_included[cat_name] = cats_included.get(cat_name, 0) + 1

    # ----------------------------------------------------------------------
    # Diagnostics per link instance
    # ----------------------------------------------------------------------
    view_id_val = getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown")
    link_inst_id_val = getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown")

    link_doc = None
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None

    link_title = getattr(link_doc, "Title", "<link>") if link_doc is not None else "<link>"

    logger.info(
        "Link3D: view Id={0}, link '{1}' (InstId={2}): collected {3} 3D proxy element(s).".format(
            view_id_val, link_title, link_inst_id_val, len(proxies)
        )
    )

    if cats_included:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_included.items())]
        logger.info("Link3D: included link categories: {0}".format(", ".join(parts)))

    if cats_excl_host_hidden:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_excl_host_hidden.items())]
        logger.info(
            "Link3D: excluded link categories by host VG (hidden): {0}".format(", ".join(parts))
        )

    if cats_excl_global:
        parts = ["{0} ({1})".format(name, count) for name, count in sorted(cats_excl_global.items())]
        logger.info(
            "Link3D: excluded link categories by global 3D exclusion: {0}".format(", ".join(parts))
        )

    return proxies

def _collect_link_proxies_by_linked_view(host_view, link_inst, logger):
    """
    Option 1: if the host view displays the link 'By Linked View', use that
    linked view as the visibility filter.

    This path is cheap when available and respects the link's own view settings.
    """
    if FilteredElementCollector is None:
        return []

    link_doc = None
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None

    if link_doc is None:
        return []

    # Try to read the linked view from link overrides on the host view
    linked_view_id = None
    try:
        overrides = host_view.GetLinkOverrides(link_inst.Id)
    except Exception:
        overrides = None

    if overrides is not None:
        try:
            linked_view_id = overrides.LinkedViewId
        except Exception:
            linked_view_id = None

    # If there's no linked view selected, this path is a no-op
    if linked_view_id is None:
        return []

    try:
        linked_view = link_doc.GetElement(linked_view_id)
    except Exception:
        linked_view = None

    if linked_view is None:
        return []

    try:
        col = (
            FilteredElementCollector(link_doc, linked_view.Id)
            .WhereElementIsNotElementType()
        )
    except Exception as ex:
        logger.warn(
            "Projection: linked-view collector failed for host view {0}, link {1}: {2}".format(
                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                ex,
            )
        )
        return []

    # --- Intersect linked-view contents with host crop volume (crop box) ---
    # Only if we have the geometry types available and a valid CropBox.
    if BoundingBoxIntersectsFilter is not None and XYZ is not None:
        try:
            crop_bb = getattr(host_view, "CropBox", None)
        except Exception:
            crop_bb = None

        if crop_bb is not None and crop_bb.Min is not None and crop_bb.Max is not None:
            try:
                trf = link_inst.GetTransform()
                inv = trf.Inverse
            except Exception:
                inv = None

            if inv is not None:
                # Transform host CropBox corners into link-doc coordinates
                xs = [crop_bb.Min.X, crop_bb.Max.X]
                ys = [crop_bb.Min.Y, crop_bb.Max.Y]
                zs = [crop_bb.Min.Z, crop_bb.Max.Z]
                min_x = min_y = min_z = float("inf")
                max_x = max_y = max_z = float("-inf")

                for x in xs:
                    for y in ys:
                        for z in zs:
                            pt = inv.OfPoint(XYZ(x, y, z))
                            if pt.X < min_x: min_x = pt.X
                            if pt.Y < min_y: min_y = pt.Y
                            if pt.Z < min_z: min_z = pt.Z
                            if pt.X > max_x: max_x = pt.X
                            if pt.Y > max_y: max_y = pt.Y
                            if pt.Z > max_z: max_z = pt.Z

                if min_x <= max_x and min_y <= max_y and min_z <= max_z:
                    try:
                        outline = Outline(XYZ(min_x, min_y, min_z), XYZ(max_x, max_y, max_z))
                        bb_filter = BoundingBoxIntersectsFilter(outline)
                        col = col.WherePasses(bb_filter)
                        logger.info(
                            "Projection: ByLinkedView link {0} clipped to host CropBox for view Id={1}".format(
                                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                                getattr(getattr(host_view, "Id", None), "IntegerValue", "Unknown"),
                            )
                        )
                    except Exception as ex_clip:
                        logger.warn(
                            "Projection: failed to clip ByLinkedView link {0} to host CropBox: {1}".format(
                                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                                ex_clip,
                            )
                        )

    # NOTE: host_visible_model_cat_ids=None here → no host VG filtering in ByLinkedView mode.
    return _build_link_proxies_from_collector(
        link_inst,
        col,
        host_view,
        logger,
        host_visible_model_cat_ids=None,
    )

def _collect_link_proxies_by_instance_bbox(host_view, link_inst, host_visible_model_cat_ids, logger):
    """
    Host-clip driven spatial clipping for link 3D:
      - Build host clip prism (plans: ViewRange slab; vertical: far/depth).
      - Transform prism corners into link coords => true OBB.
      - Broad-phase: OBB -> enclosing AABB (full XYZ) for collector filter.
      - Narrow-phase: candidate link AABB vs true OBB (in _build_link_proxies_from_collector).
    """
    if FilteredElementCollector is None or XYZ is None:
        return []

    # Resolve link doc
    try:
        link_doc = link_inst.GetLinkDocument()
    except Exception:
        link_doc = None
    if link_doc is None:
        return []

    # Build host clip volume once
    clip = build_clip_volume_for_view(host_view, None, logger)
    if not clip.get("is_valid", False):
        return []
    corners_host = clip.get("corners_host", None)
    if not corners_host:
        return []

    # Transform host corners into link space
    try:
        trf = link_inst.GetTransform()
        inv = trf.Inverse
    except Exception:
        inv = None
    if inv is None:
        return []

    try:
        corners_link = [inv.OfPoint(p) for p in corners_host]
    except Exception:
        return []

    # True OBB in link coords (for narrow phase)
    obb_link = None
    try:
        obb_link = transforms.obb_from_ordered_corners(corners_link)
    except Exception:
        obb_link = None

    # Broad-phase AABB in link coords (min/max of all 8 corners)
    try:
        xs = [p.X for p in corners_link]
        ys = [p.Y for p in corners_link]
        zs = [p.Z for p in corners_link]
        min_link = XYZ(min(xs), min(ys), min(zs))
        max_link = XYZ(max(xs), max(ys), max(zs))
    except Exception:
        return []
        
    if Outline is None or BoundingBoxIntersectsFilter is None:
        logger.warn("Outline/BoundingBoxIntersectsFilter unavailable")
        return []
    outline = Outline(min_link, max_link)
    
    try:
        outline = Outline(min_link, max_link)
        bb_filter = BoundingBoxIntersectsFilter(outline)
        col = (
            FilteredElementCollector(link_doc)
            .WhereElementIsNotElementType()
            .WherePasses(bb_filter)
        )
    except Exception as ex:
        logger.warn(
            "Projection: linked-BBox collector failed for link {0}: {1}".format(
                getattr(getattr(link_inst, "Id", None), "IntegerValue", "Unknown"),
                ex,
            )
        )
        return []

    return _build_link_proxies_from_collector(
        link_inst,
        col,
        host_view,
        logger,
        host_visible_model_cat_ids=host_visible_model_cat_ids,
        obb_link=obb_link,
    )

def collect_link_3d_proxies(view, link_instance, config, logger):
    """
    v47 link 3D collection (CURRENT behavior):

    - All links (ByHostView, Custom, ByLinkedView) use the SAME host-driven spatial logic:
        * Stage-2 host clip volume -> transformed into link space
        * Broad-phase collector by enclosing AABB
        * Narrow-phase OBB vs element AABB (SAT)
        * Host view VG category visibility (model categories) is respected

    - Custom and ByLinkedView are kept as FUTURE stubs (logging only) until the API
      exposes reliable per-link custom/linked-view visibility data.
    - Linked 2D is not collected in v47.
    """
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")
    link_id_val = getattr(getattr(link_instance, "Id", None), "IntegerValue", "Unknown")

    # Read LinkVisibilityType (diagnostics only)
    link_vis_type = None
    rlg_settings = None
    RvtLinkVisibility = None
    try:
        from Autodesk.Revit.DB import LinkVisibility as _RvtLinkVisibility
        RvtLinkVisibility = _RvtLinkVisibility
    except Exception:
        RvtLinkVisibility = None

    if RvtLinkVisibility is not None:
        try:
            rlg_settings = view.GetLinkOverrides(link_instance.Id)
        except Exception:
            rlg_settings = None

    if rlg_settings is not None and RvtLinkVisibility is not None:
        try:
            link_vis_type = rlg_settings.LinkVisibilityType
        except Exception:
            link_vis_type = None

    # LinkVisibilityType is logged for diagnostics only (future API work).
    try:
        if link_vis_type is None:
            logger.info(
                "Link3D: view {0}, link {1}: LinkVisibilityType=None (unavailable); treating as ByHostView (v47 placeholder)".format(
                    view_id_val, link_id_val
                )
            )
        else:
            logger.info(
                "Link3D: view {0}, link {1}: LinkVisibilityType={2} (diagnostics only); treating as ByHostView (v47 placeholder)".format(
                    view_id_val, link_id_val, link_vis_type
                )
            )
    except Exception:
        pass


    # FUTURE stubs (no behavior change today)
    if RvtLinkVisibility is not None and link_vis_type == RvtLinkVisibility.Custom:
        try:
            logger.info(
                "Link3D: link {0} is Custom in view {1} (stubbed); using host-driven link logic.".format(
                    link_id_val, view_id_val
                )
            )
        except Exception:
            pass

    if RvtLinkVisibility is not None and link_vis_type == RvtLinkVisibility.ByLinkView:
        try:
            logger.info(
                "Link3D: link {0} is ByLinkedView in view {1} (stubbed); using host-driven link logic.".format(
                    link_id_val, view_id_val
                )
            )
        except Exception:
            pass

    # Host view VG model-category visibility is the only view-visibility signal
    host_visible_model_ids = _get_host_visible_model_cat_ids(view, logger)

    return _collect_link_proxies_by_instance_bbox(
        view,
        link_instance,
        host_visible_model_cat_ids=host_visible_model_ids,
        logger=logger,
    )

def collect_3d_elements_for_view(view, config, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None or CategoryType is None:
        logger.info(
            "Projection: DOC/collector unavailable; 3D empty for view Id={0}".format(
                view_id_val
            )
        )
        return []

    vtype = getattr(view, "ViewType", None)
    is_drafting = (vtype == getattr(ViewType, "DraftingView", None))
    is_legend = hasattr(ViewType, "Legend") and (vtype == getattr(ViewType, "Legend"))
    if is_drafting or is_legend:
        logger.info(
            "Projection: view Id={0} is Drafting/Legend; skipping 3D".format(
                view_id_val
            )
        )
        return []

    proj_cfg = (config or {}).get("projection") or {}
    include_link_3d = bool(proj_cfg.get("include_link_3d", True))

    elems3d = []
    link_insts = []

    # Canonical exclusion set for navigation + non-physical model cats
    excluded_ids = _get_excluded_3d_cat_ids()

    # ----------------------------------------------------------------------
    # Host 3D elements visible in view
    # No VisibleInViewFilter (it is active-view sensitive in Dynamo).
    # ----------------------------------------------------------------------
    try:
        try:
            col = (
                FilteredElementCollector(DOC, view.Id)
                .WhereElementIsNotElementType()
            )
        except Exception as ex:
            logger.warn(
                "Projection: 3D view-scoped collector failed for view Id={0}: {1} "
                "-- returning empty (no doc-wide fallback)".format(view_id_val, ex)
            )
            return []

        for e in col:
            cat = getattr(e, "Category", None)
            cat_id_obj = getattr(cat, "Id", None) if cat is not None else None
            try:
                cat_id_val = getattr(cat_id_obj, "IntegerValue", None)
            except Exception:
                cat_id_val = None
            cat_type = getattr(cat, "CategoryType", None) if cat is not None else None
            is_view_specific = bool(getattr(e, "ViewSpecific", False))

            # Skip navigation / non-occupancy cats (rooms, areas, grids, cameras, etc.)
            if cat_id_val is not None and cat_id_val in excluded_ids:
                continue

            # Linked RVT: record the instance, but don't treat its big BBox as geometry
            if RevitLinkInstance is not None and isinstance(e, RevitLinkInstance):
                if include_link_3d:
                    link_insts.append(e)
                continue

            # Imports: only model-level (non view-specific)
            if ImportInstance is not None and isinstance(e, ImportInstance):
                if not is_view_specific:
                    elems3d.append(e)
                continue

            # Regular model categories, non view-specific
            if cat_type == CategoryType.Model and not is_view_specific:
                elems3d.append(e)

    except Exception as ex:
        logger.warn(
            "Projection: error collecting 3D elements for view Id={0}: {1}".format(
                view_id_val, ex
            )
        )
        elems3d = []
        link_insts = []


    # ----------------------------------------------------------------------
    # Linked RVT expansion (ByLinkedView → ByHostView with VG filtering)
    # ----------------------------------------------------------------------
    link_proxies = []
    if include_link_3d and link_insts:
        try:
            for link_inst in link_insts:
                link_proxies.extend(
                    collect_link_3d_proxies(view, link_inst, config, logger)
                )
        except Exception as ex:
            logger.warn(
                "Projection: error collecting linked 3D elements for view Id={0}: {1}".format(
                    view_id_val, ex
                )
            )
            link_proxies = []


    all_elems = list(elems3d) + list(link_proxies)

    logger.info(
        "Projection: collected {0} 3D candidate element(s) for view Id={1}".format(
            len(all_elems), view_id_val
        )
    )
    return all_elems

def collect_2d_elements_for_view(view, config, logger):
    view_id_val = getattr(getattr(view, "Id", None), "IntegerValue", "Unknown")

    if DOC is None or FilteredElementCollector is None:
        logger.info("Projection: DOC/collector unavailable; 2D empty for view Id={0}".format(view_id_val))
        return []

    # Strict whitelist
    # Text, Dimensions, Tags (IndependentTag + RoomTag), Filled Regions, Detail Components, Detail Lines/Curves
    elems2d = []

    try:
        collector = FilteredElementCollector(DOC, view.Id).WhereElementIsNotElementType()
    except Exception as ex:
        logger.warn("Projection: 2D view-scoped collector failed for view Id={0}: {1}".format(view_id_val, ex))
        return []

    # Resolve types if available
    TextNote_cls = globals().get("TextNote", None)
    Dimension_cls = globals().get("Dimension", None)
    IndependentTag_cls = globals().get("IndependentTag", None)
    RoomTag_cls = globals().get("RoomTag", None)
    FilledRegion_cls = globals().get("FilledRegion", None)
    FamilyInstance_cls = globals().get("FamilyInstance", None)
    DetailCurve_cls = globals().get("DetailCurve", None)
    CurveElement_cls = globals().get("CurveElement", None)

    for e in collector:
        try:
            # Only view-specific 2D items should count
            if not bool(getattr(e, "ViewSpecific", False)):
                continue

            # Class-based whitelist where possible
            if TextNote_cls is not None and isinstance(e, TextNote_cls):
                elems2d.append(e); continue
            if Dimension_cls is not None and isinstance(e, Dimension_cls):
                elems2d.append(e); continue
            if IndependentTag_cls is not None and isinstance(e, IndependentTag_cls):
                elems2d.append(e); continue
            if RoomTag_cls is not None and isinstance(e, RoomTag_cls):
                elems2d.append(e); continue
            if FilledRegion_cls is not None and isinstance(e, FilledRegion_cls):
                elems2d.append(e); continue
            if DetailCurve_cls is not None and isinstance(e, DetailCurve_cls):
                elems2d.append(e); continue
            if CurveElement_cls is not None and isinstance(e, CurveElement_cls):
                # detail lines/curves are CurveElements that are view-specific
                # (model lines are not view-specific)
                elems2d.append(e); continue

            # Detail components are FamilyInstance + view-specific (covers many detail items)
            if FamilyInstance_cls is not None and isinstance(e, FamilyInstance_cls):
                elems2d.append(e); continue

            # No other 2D categories contribute (per whitelist).
            continue

        except Exception:
            continue

    logger.info(
        "Projection: collected {0} 2D candidate element(s) for view Id={1}".format(
            len(elems2d), view_id_val
        )
    )
    return elems2d


