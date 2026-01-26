"""
Silhouette extraction for VOP interwoven pipeline.

Provides simplified silhouette extraction with adaptive strategies:
- bbox: Axis-aligned bounding box (baseline)
- obb: Oriented bounding box (projected 3D corners)
- silhouette_edges: Edge silhouette (most accurate, most expensive)
- cad_curves: ImportInstance curve extraction (DWG/DXF, open polylines)

Falls back gracefully to bbox when strategies fail.
Compatible with IronPython (no logging module, no f-strings).
"""

import math

# -----------------------------------------------------------------------------
# Family-definition outline fallback (FilledRegion / 2D region edges)
#
# Motivation (evidence from this chat):
#   Some family "panel" graphics defined as FilledRegion do not surface as Curves
#   in FamilyInstance geometry (even with Options.View + symbol traversal).
#   We therefore optionally extract those boundaries from the family document.
#
# Guardrails:
#   - Per-symbol cache (avoid repeated EditFamily cost)
#   - Per-symbol time budget (avoid stalls)
#   - Bounded point sampling
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Family-definition outline fallback (FilledRegion / 2D region edges)
# -----------------------------------------------------------------------------

try:
    # core/cache.py in this repo provides a bounded LRU implementation
    from .cache import LRUCache
except Exception:
    LRUCache = None

# Conservative defaults; override via cfg.* if present.
_DEFAULT_FAMILY_REGION_CACHE_MAX_SYMBOLS = 2048
_DEFAULT_FAMILY_REGION_CACHE_MAX_FAMILIES = 2048

# Use bounded caches when available; fall back to dicts if import fails.
if LRUCache is not None:
    _FAMILY_REGION_OUTLINE_CACHE = LRUCache(max_items=_DEFAULT_FAMILY_REGION_CACHE_MAX_SYMBOLS)
    _FAMILY_FAMDOC_REGION_CACHE = LRUCache(max_items=_DEFAULT_FAMILY_REGION_CACHE_MAX_FAMILIES)
else:
    _FAMILY_REGION_OUTLINE_CACHE = {}  # symbol_id_int -> {"xyz_loops": [...], "ts": float}
    _FAMILY_FAMDOC_REGION_CACHE = {}   # family_id_int -> {"xyz_loops": [...], "ts": float}

_FAMILY_FAMDOC_REGION_CACHE = {}  # family_id_int -> {"xyz_loops": [...], "ts": float}

def _compose_transform(parent_T, child_T):
    if parent_T is None:
        return child_T
    if child_T is None:
        return parent_T
    try:
        return parent_T.Multiply(child_T)
    except Exception:
        try:
            return child_T.Multiply(parent_T)
        except Exception:
            return child_T

def _collect_regions_recursive(
    host_doc,
    fam,
    T_into_host_family,
    view,
    t0,
    budget_s,
    max_pts,
    depth,
    max_depth,
    visited_family_ids,
    diag=None,
):
    """
    Recursively collect FilledRegion boundary loops from a family and any nested families.
    Returns XYZ tuples in HOST FAMILY coordinates (i.e., after applying T_into_host_family).
    """
    import time
    xyz_loops = []

    fam_id = _safe_int_id(fam)
    if fam_id <= 0:
        return xyz_loops

    if (time.time() - t0) > budget_s:
        return xyz_loops
    if depth > max_depth:
        return xyz_loops

    # Cycle breaker (recursion stack), not a global seen-set:
    # allows the same family definition to be visited again via a different instance/transform.
    if fam_id in visited_family_ids:
        return xyz_loops
    visited_family_ids.add(fam_id)

    # Cache by family id (family-doc local coords) and then transform to host-family coords
    hit = _cache_get(_FAMILY_FAMDOC_REGION_CACHE, fam_id, default=None)
    fam_local_loops = None
    if hit and isinstance(hit, dict) and "xyz_loops" in hit:
        fam_local_loops = hit.get("xyz_loops") or []

    fam_doc = None
    try:
        fam_doc = host_doc.EditFamily(fam)

        if (time.time() - t0) > budget_s:
            return xyz_loops

        # If not cached, extract loops from THIS family doc (family-local coords)
        if fam_local_loops is None:
            fam_local_loops = []
            try:
                from Autodesk.Revit.DB import FilteredElementCollector, FilledRegion
            except Exception:
                return []

            # Collect ALL filled regions (normal + masking)
            regions = []
            try:
                regions = list(
                    FilteredElementCollector(fam_doc)
                    .OfClass(FilledRegion)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
                
                if diag is not None and hasattr(diag, "debug"):
                    diag.debug(
                        phase="silhouette",
                        callsite="family_region.collect",
                        message="Collected filled regions in family doc",
                        extra={
                            "family_id": fam_id,
                            "depth": depth,
                            "regions_found": len(regions),
                            "is_nested": depth > 0,
                        },
                    )

            except Exception:
                regions = []

            for fr in regions:
                if (time.time() - t0) > budget_s:
                    break
                    
                # Optional: identify masking vs normal (not required for outline extraction)
                is_masking = False
                try:
                    is_masking = bool(getattr(fr, "IsMasking", False))
                except Exception:
                    pass
                    
                try:
                    loops = fr.GetBoundaries()
                except Exception:
                    loops = None
                if not loops:
                    continue

                for cl in loops:
                    if (time.time() - t0) > budget_s:
                        break
                    pts = []
                    try:
                        for c in cl:
                            if (time.time() - t0) > budget_s:
                                break

                            cname = ""
                            try:
                                cname = c.__class__.__name__
                            except Exception:
                                cname = ""

                            if hasattr(c, "GetEndPoint") and cname in ("Line", "BoundLine"):
                                try:
                                    p0 = c.GetEndPoint(0)
                                    p1 = c.GetEndPoint(1)
                                    pts.append(_xyz_tuple(p0))
                                    pts.append(_xyz_tuple(p1))
                                except Exception:
                                    pass
                            else:
                                try:
                                    tess = c.Tessellate()
                                    n = min(len(tess), max_pts)
                                    for k in range(n):
                                        pts.append(_xyz_tuple(tess[k]))
                                except Exception:
                                    pass
                    except Exception:
                        continue

                    if len(pts) >= 2:
                        cleaned = [pts[0]]
                        for p in pts[1:]:
                            if p != cleaned[-1]:
                                cleaned.append(p)
                        if len(cleaned) >= 3 and cleaned[0] != cleaned[-1]:
                            cleaned.append(cleaned[0])
                        if len(cleaned) >= 3:
                            fam_local_loops.append(cleaned)

            # Cache extracted family-local loops (even if empty)
            _cache_set(_FAMILY_FAMDOC_REGION_CACHE, fam_id, {"xyz_loops": fam_local_loops, "ts": time.time()})

        # Transform family-local loops into HOST FAMILY coords
        for loop in fam_local_loops or []:
            if (time.time() - t0) > budget_s:
                break
            out_loop = []
            for xyz in loop:
                out_loop.append(_apply_transform_xyz_tuple(T_into_host_family, xyz))
            if len(out_loop) >= 3:
                xyz_loops.append(out_loop)

        # Recurse into nested family instances inside this family doc
        if (time.time() - t0) > budget_s:
            return xyz_loops

        try:
            from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance
        except Exception:
            FamilyInstance = None

        if FamilyInstance is not None:
            try:
                nested_insts = list(
                    FilteredElementCollector(fam_doc)
                    .OfClass(FamilyInstance)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            except Exception:
                nested_insts = []

            for inst in nested_insts:
                if (time.time() - t0) > budget_s:
                    break

                try:
                    sym = getattr(inst, "Symbol", None)
                    nested_fam = getattr(sym, "Family", None) if sym is not None else None
                except Exception:
                    nested_fam = None

                if nested_fam is None:
                    continue

                # Transform: nested family local -> this family local is inst.GetTransform()
                inst_T = None
                try:
                    if hasattr(inst, "GetTransform"):
                        inst_T = inst.GetTransform()
                    else:
                        inst_T = getattr(inst, "Transform", None)
                except Exception:
                    inst_T = None

                # nested local -> host family = T_into_host_family * inst_T
                T_nested_into_host = _compose_transform(T_into_host_family, inst_T)

                if diag is not None and hasattr(diag, "debug"):
                    diag.debug(
                        phase="silhouette",
                        callsite="family_region.recurse",
                        message="Recursing into nested family",
                        extra={
                            "parent_family_id": fam_id,
                            "nested_family_id": _safe_int_id(nested_fam),
                            "depth": depth + 1,
                        },
                    )

                xyz_loops.extend(
                    _collect_regions_recursive(
                        host_doc=host_doc,
                        fam=nested_fam,
                        T_into_host_family=T_nested_into_host,
                        view=view,
                        t0=t0,
                        budget_s=budget_s,
                        max_pts=max_pts,
                        depth=depth + 1,
                        max_depth=max_depth,
                        visited_family_ids=visited_family_ids,
                        diag=diag,
                    )
                )

        return xyz_loops

    finally:
        try:
            visited_family_ids.discard(fam_id)
        except Exception:
            pass
        try:
            if fam_doc is not None:
                fam_doc.Close(False)
        except Exception:
            pass

def _safe_int_id(x):
    try:
        return int(getattr(getattr(x, "Id", None), "IntegerValue", 0))
    except Exception:
        try:
            return int(x)
        except Exception:
            return 0

def _xyz_tuple(p):
    try:
        return (float(p.X), float(p.Y), float(p.Z))
    except Exception:
        return (float(p[0]), float(p[1]), float(p[2]))

def _apply_transform_xyz_tuple(T, xyz):
    # xyz is (x,y,z); T is Autodesk.Revit.DB.Transform or None
    if T is None:
        return xyz
    try:
        from Autodesk.Revit.DB import XYZ
        p = XYZ(xyz[0], xyz[1], xyz[2])
        q = T.OfPoint(p)
        return (float(q.X), float(q.Y), float(q.Z))
    except Exception:
        return xyz

def _cache_get(cache_obj, key, default=None):
    try:
        if hasattr(cache_obj, "get"):
            return cache_obj.get(key, default=default)
        return cache_obj.get(key, default)
    except Exception:
        return default

def _cache_set(cache_obj, key, value):
    try:
        if hasattr(cache_obj, "set"):
            cache_obj.set(key, value)
        else:
            cache_obj[key] = value
    except Exception:
        pass

def _maybe_resize_lru(cache_obj, max_items):
    # Best-effort: only affects LRUCache; dict fallback ignores.
    try:
        if hasattr(cache_obj, "max_items"):
            mi = int(max_items)
            if mi >= 0:
                cache_obj.max_items = mi
                # If downsizing, evict immediately by re-setting a no-op key pattern.
                # LRUCache evicts on set(); forcing eviction without storing a new item
                # isn't supported, so we accept that downsizing takes effect on next set.
    except Exception:
        pass

def _family_region_outlines_cached(base_elem, view, cfg=None, diag=None):
    """
    Return list of HOST-FAMILY-local XYZ loops representing FilledRegion boundaries
    extracted from the family document, including nested families (bounded recursion),
    cached per symbol.

    Returns:
        xyz_loops: list of loops, each loop is [(x,y,z), ...] (typically closed with last==first)
                  Coordinates are in the HOST FAMILY coordinate space.
    """
    # Enable gate (default True so it's testable; set False in cfg to disable)
    enable = getattr(cfg, "family_region_outline_enable", False) if cfg else False
    if not enable:
        return []

    # Budget (seconds) per symbol extraction (includes nested recursion)
    budget_s = getattr(cfg, "family_region_outline_budget_s", 0.25) if cfg else 0.25
    try:
        budget_s = float(budget_s)
    except Exception:
        budget_s = 0.25
    if budget_s <= 0:
        return []

    # Cap points per boundary curve tessellation
    max_pts = getattr(cfg, "family_region_outline_max_pts_per_curve", 50) if cfg else 50
    try:
        max_pts = int(max_pts)
    except Exception:
        max_pts = 50
    if max_pts < 2:
        max_pts = 2

    # Nested recursion cap
    max_depth = getattr(cfg, "family_region_outline_nested_max_depth", 3) if cfg else 3
    try:
        max_depth = int(max_depth)
    except Exception:
        max_depth = 3
    if max_depth < 0:
        max_depth = 0

    # Symbol key
    try:
        sym = getattr(base_elem, "Symbol", None)
    except Exception:
        sym = None
    sym_id = _safe_int_id(sym)
    if sym is None or sym_id <= 0:
        return []

    # Optional runtime cap overrides from cfg (no config dependency)
    try:
        max_syms = int(getattr(cfg, "family_region_outline_cache_max_symbols", _DEFAULT_FAMILY_REGION_CACHE_MAX_SYMBOLS))
    except Exception:
        max_syms = _DEFAULT_FAMILY_REGION_CACHE_MAX_SYMBOLS
    try:
        max_fams = int(getattr(cfg, "family_region_outline_cache_max_families", _DEFAULT_FAMILY_REGION_CACHE_MAX_FAMILIES))
    except Exception:
        max_fams = _DEFAULT_FAMILY_REGION_CACHE_MAX_FAMILIES

    _maybe_resize_lru(_FAMILY_REGION_OUTLINE_CACHE, max_syms)
    _maybe_resize_lru(_FAMILY_FAMDOC_REGION_CACHE, max_fams)

    # Cache hit (per symbol)
    hit = _cache_get(_FAMILY_REGION_OUTLINE_CACHE, sym_id, default=None)
    if hit and isinstance(hit, dict) and "xyz_loops" in hit:
        return hit.get("xyz_loops") or []

    import time
    t0 = time.time()

    try:
        doc = getattr(base_elem, "Document", None)
        if doc is None:
            return []

        fam = getattr(sym, "Family", None)
        if fam is None:
            return []

        visited = set()
        # Host family local space starts as identity (no extra transform)
        T0 = None

        xyz_loops = _collect_regions_recursive(
            host_doc=doc,
            fam=fam,
            T_into_host_family=T0,
            view=view,
            t0=t0,
            budget_s=budget_s,
            max_pts=max_pts,
            depth=0,
            max_depth=max_depth,
            visited_family_ids=visited,
            diag=diag,
        )

        _cache_set(_FAMILY_REGION_OUTLINE_CACHE, sym_id, {"xyz_loops": [], "ts": time.time()})
        return []

    except Exception as e:
        try:
            if diag is not None and hasattr(diag, "warn"):
                diag.warn(
                    phase="silhouette",
                    callsite="family_region_outline",
                    message="Family region outline extraction failed; ignoring",
                    elem_id=_safe_int_id(base_elem),
                    extra={"exc_type": type(e).__name__, "exc": str(e), "sym_id": sym_id},
                )
        except Exception:
            pass
        _cache_set(_FAMILY_REGION_OUTLINE_CACHE, sym_id, {"xyz_loops": xyz_loops, "ts": time.time()})
        return xyz_loops

def _bbox_corners_world(bbox):
    """
    Return 8 bbox corners in world coords, honoring bbox.Transform when present.
    bbox.Min/Max are in bbox-local coords.
    """
    try:
        from Autodesk.Revit.DB import XYZ, Transform
    except Exception:
        XYZ = None

    if not bbox or not bbox.Min or not bbox.Max:
        return []

    mn = bbox.Min
    mx = bbox.Max

    # local corners
    if XYZ is None:
        return []
    local = [
        XYZ(mn.X, mn.Y, mn.Z),
        XYZ(mx.X, mn.Y, mn.Z),
        XYZ(mx.X, mx.Y, mn.Z),
        XYZ(mn.X, mx.Y, mn.Z),
        XYZ(mn.X, mn.Y, mx.Z),
        XYZ(mx.X, mn.Y, mx.Z),
        XYZ(mx.X, mx.Y, mx.Z),
        XYZ(mn.X, mx.Y, mx.Z),
    ]

    trf = getattr(bbox, "Transform", None)
    if trf is None:
        return local

    try:
        return [trf.OfPoint(p) for p in local]
    except Exception:
        return local


def _pca_obb_uv(points_uv):
    """
    Compute an oriented rectangle in UV using PCA (good for “linear-ish” detection).
    Returns: (rect_points, len_u, len_v) where rect_points is a closed loop in UV.
    """
    if not points_uv or len(points_uv) < 2:
        return ([], 0.0, 0.0)

    # mean
    sx = 0.0
    sy = 0.0
    n = float(len(points_uv))
    for (x, y) in points_uv:
        sx += float(x)
        sy += float(y)
    mx = sx / n
    my = sy / n

    # covariance
    cxx = 0.0
    cxy = 0.0
    cyy = 0.0
    for (x, y) in points_uv:
        dx = float(x) - mx
        dy = float(y) - my
        cxx += dx * dx
        cxy += dx * dy
        cyy += dy * dy

    # principal axis angle (2D PCA)
    # angle = 0.5 * atan2(2*cxy, cxx - cyy)
    ang = 0.5 * math.atan2(2.0 * cxy, (cxx - cyy))
    ux = math.cos(ang)
    uy = math.sin(ang)
    vx = -uy
    vy = ux

    # project extents
    umin = 1e100
    umax = -1e100
    vmin = 1e100
    vmax = -1e100
    for (x, y) in points_uv:
        dx = float(x) - mx
        dy = float(y) - my
        u = dx * ux + dy * uy
        v = dx * vx + dy * vy
        if u < umin: umin = u
        if u > umax: umax = u
        if v < vmin: vmin = v
        if v > vmax: vmax = v

    len_u = (umax - umin)
    len_v = (vmax - vmin)

    # rectangle corners back in UV
    # p = mean + u*U + v*V
    a = (mx + umin * ux + vmin * vx, my + umin * uy + vmin * vy)
    b = (mx + umax * ux + vmin * vx, my + umax * uy + vmin * vy)
    c = (mx + umax * ux + vmax * vx, my + umax * uy + vmax * vy)
    d = (mx + umin * ux + vmax * vx, my + umin * uy + vmax * vy)
    rect = [a, b, c, d, a]

    return (rect, abs(len_u), abs(len_v))


def _uv_obb_rect_from_bbox(elem, view, view_basis):
    """
    Build a UV OBB rectangle loop from bbox corners (using bbox.Transform if present).
    Returns: (loop_points_uv, len_u, len_v)
    """
    try:
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return ([], 0.0, 0.0)

        corners_w = _bbox_corners_world(bbox)
        if not corners_w:
            return ([], 0.0, 0.0)

        pts_uv = []
        for p in corners_w:
            # NOTE: if you already added _to_host_point earlier, bbox for proxies is host already,
            # but this is harmless for normal elems.
            p2 = _to_host_point(elem, p) if "_to_host_point" in globals() else p
            uv = view_basis.transform_to_view_uv((p2.X, p2.Y, p2.Z))
            pts_uv.append((uv[0], uv[1]))

        rect, lu, lv = _pca_obb_uv(pts_uv)
        return (rect, lu, lv)

    except Exception:
        return ([], 0.0, 0.0)


def _determine_uv_mode(elem, view, view_basis, raster, cfg):
    """
    Classify element by UV mode (shape): TINY, LINEAR, or AREAL
    using an OBB in UV derived from bbox.Transform when possible.
    Falls back to the previous AABB behavior on failure.
    """
    try:
        # thresholds in cells
        tiny_max = cfg.tiny_max
        thin_max = cfg.thin_max

        rect, lu_ft, lv_ft = _uv_obb_rect_from_bbox(elem, view, view_basis)
        if lu_ft > 0.0 and lv_ft > 0.0:
            U = int(lu_ft / raster.cell_size_ft)
            V = int(lv_ft / raster.cell_size_ft)

            if U <= tiny_max and V <= tiny_max:
                return 'TINY'
            elif min(U, V) <= thin_max:
                return 'LINEAR'
            else:
                return 'AREAL'

        # Fallback: old AABB in UV
        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return 'AREAL'
        min_uv = view_basis.transform_to_view_uv((bbox.Min.X, bbox.Min.Y, bbox.Min.Z))
        max_uv = view_basis.transform_to_view_uv((bbox.Max.X, bbox.Max.Y, bbox.Max.Z))
        u_extent = abs(max_uv[0] - min_uv[0])
        v_extent = abs(max_uv[1] - min_uv[1])
        U = int(u_extent / raster.cell_size_ft)
        V = int(v_extent / raster.cell_size_ft)

        if U <= tiny_max and V <= tiny_max:
            return 'TINY'
        elif min(U, V) <= thin_max:
            return 'LINEAR'
        else:
            return 'AREAL'

    except Exception:
        return 'AREAL'

def _location_curve_obb_silhouette(elem, view, view_basis, cfg=None):
    """
    Build a thin oriented quad around the projected LocationCurve.
    Great for diagonal thin elements (beams/brace/pipe runs) so they rasterize as lines.
    """
    try:
        thin = getattr(cfg, "thin_max", 1.0) if cfg else 1.0  # in *view UV units*
        loc = getattr(elem, "Location", None)
        if loc is None or not hasattr(loc, "Curve"):
            return []

        c = loc.Curve
        p0 = _to_host_point(elem, c.GetEndPoint(0))
        p1 = _to_host_point(elem, c.GetEndPoint(1))

        u0, v0 = view_basis.transform_to_view_uv((p0.X, p0.Y, p0.Z))
        u1, v1 = view_basis.transform_to_view_uv((p1.X, p1.Y, p1.Z))

        du = (u1 - u0)
        dv = (v1 - v0)
        L = (du * du + dv * dv) ** 0.5
        if L <= 1e-9:
            return []

        # perpendicular unit
        nx = -dv / L
        ny = du / L

        # thickness = thin (half on each side)
        t = thin * 0.5

        a = (u0 + nx * t, v0 + ny * t)
        b = (u1 + nx * t, v1 + ny * t)
        c2 = (u1 - nx * t, v1 - ny * t)
        d = (u0 - nx * t, v0 - ny * t)

        return [{
            "points": [a, b, c2, d, a],
            "is_hole": False
        }]

    except Exception:
        return []


def _detail_line_band_silhouette(elem, view, view_basis, cfg=None, diag=None):
    """
    Create oriented band loops for detail/drafting lines (archive parity).

    Detail lines should render as thin rectangles oriented along the line,
    not as single-pixel Bresenham edges. This matches archive/refactor1 behavior.

    Archive reference: archive/refactor1/processing/projection.py (line 1030-1050)

    Args:
        elem: Revit element
        view: Revit View
        view_basis: ViewBasis for coordinate transformation
        cfg: Config object (for linear_band_thickness_cells)
        diag: Diagnostics (optional)

    Returns:
        List of loop dicts with oriented band rectangles, or [] if not applicable

    Strategy:
        1. Check category is "Lines" or "Detail Lines"
        2. Extract curve from elem.Location.Curve
        3. Get endpoints, transform to UV
        4. Compute line tangent and perpendicular normal
        5. Offset endpoints by band_half_width perpendicular to line
        6. Create 4-corner closed loop: [p0+off, p1+off, p1-off, p0-off, p0+off]
        7. Return as CLOSED loop (not "open": True)

    Commentary:
        ✔ Only applies to Lines/Detail Lines category
        ✔ Band thickness from config (default 1.0 cells)
        ✔ Returns CLOSED loops for proper fill rendering
        ✔ Archive parity: matches proven refactor1 behavior
    """
    try:
        # Check if this is a detail/drafting line by category
        cat = getattr(elem, 'Category', None)
        cat_name = cat.Name if cat else ""

        # DEBUG: Log category names for LINEAR elements (first 10)
        if not hasattr(_detail_line_band_silhouette, '_debug_count'):
            _detail_line_band_silhouette._debug_count = 0
        if _detail_line_band_silhouette._debug_count < 10:
            try:
                elem_id = getattr(getattr(elem, 'Id', None), 'IntegerValue', 'unknown')
                print("[DEBUG detail_line_band] Elem {}: category='{}'".format(elem_id, cat_name))
                _detail_line_band_silhouette._debug_count += 1
            except Exception:
                pass

        # Archive used: cat_name in ("Lines", "Detail Lines")
        if cat_name not in ("Lines", "Detail Lines"):
            return []  # Not a detail line - try other strategies

        # Extract curve from Location
        loc = getattr(elem, 'Location', None)
        if loc is None:
            return []

        curve = getattr(loc, 'Curve', None)
        if curve is None:
            return []

        # Get curve endpoints
        try:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
        except Exception:
            return []

        # Transform to view UV space
        # view_basis.transform_to_view_uv expects (x, y, z) tuple, returns (u, v, w)
        uv0 = view_basis.transform_to_view_uv((p0.X, p0.Y, p0.Z))
        uv1 = view_basis.transform_to_view_uv((p1.X, p1.Y, p1.Z))

        x0, y0 = uv0[0], uv0[1]
        x1, y1 = uv1[0], uv1[1]

        # Compute line tangent and perpendicular normal
        dx = x1 - x0
        dy = y1 - y0
        length_sq = dx*dx + dy*dy

        if length_sq < 1e-18:  # Degenerate line (< 1e-9 length)
            return []

        length = length_sq ** 0.5

        # Unit tangent vector
        ux = dx / length
        uy = dy / length

        # Unit normal vector (perpendicular, rotate 90° CCW)
        nx = -uy
        ny = ux

        # Band half-width from config
        # Archive default: linear_band_thickness_cells = 1.0 → half_width = 0.5
        band_cells = getattr(cfg, 'linear_band_thickness_cells', 1.0) if cfg else 1.0
        band_half_cells = band_cells * 0.5

        # Perpendicular offset in UV space (already in cell coordinates)
        offx = nx * band_half_cells
        offy = ny * band_half_cells

        # Four corners of oriented band rectangle
        # Archive ordering: [p0+offset, p1+offset, p1-offset, p0-offset, close]
        p0_plus = (x0 + offx, y0 + offy)
        p0_minus = (x0 - offx, y0 - offy)
        p1_plus = (x1 + offx, y1 + offy)
        p1_minus = (x1 - offx, y1 - offy)

        # Create closed loop (CRITICAL: must close for fill rasterization)
        band_loop = {
            "points": [p0_plus, p1_plus, p1_minus, p0_minus, p0_plus],
            "is_hole": False,
            "strategy": "detail_line_band"
        }

        # DEBUG: Log successful band creation (first 5)
        if not hasattr(_detail_line_band_silhouette, '_success_count'):
            _detail_line_band_silhouette._success_count = 0
        if _detail_line_band_silhouette._success_count < 5:
            try:
                elem_id = getattr(getattr(elem, 'Id', None), 'IntegerValue', 'unknown')
                print("[DEBUG detail_line_band] SUCCESS elem {}: band created, {} points, length={:.1f} cells".format(
                    elem_id, len(band_loop['points']), length))
                print("  UV endpoints: ({:.1f},{:.1f}) → ({:.1f},{:.1f})".format(x0, y0, x1, y1))
                print("  Band width: {:.2f} cells (half={:.2f})".format(band_cells, band_half_cells))
                _detail_line_band_silhouette._success_count += 1
            except Exception:
                pass

        # CRITICAL: Do NOT set "open": True
        # This is a CLOSED loop that should be filled, not a Bresenham edge

        return [band_loop]

    except Exception as e:
        # Fail gracefully - other strategies will be tried
        # Don't spam logs with exceptions from category check, etc.
        return []


def _symbolic_curves_silhouette(elem, view, view_basis, cfg=None, diag=None):
    """
    For FamilyInstance (and similar): extract curve primitives visible in the view.
    Returns OPEN polylines (edges only). Intended to show symbolic linework instead of extents rects.
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False

        # View-specific geometry (symbolic lines) often requires Options.View
        # CRITICAL: Never set opts.View for linked elements - causes geometry extraction failure
        if not hasattr(elem, 'transform'):  # Host element only
            try:
                opts.View = view
            except Exception:
                pass
        # For linked elements: leave opts.View = None (extract in link coordinates)

        try:
            opts.DetailLevel = ViewDetailLevel.Fine
        except Exception:
            pass

        base_elem = _unwrap_elem(elem)
        geom = base_elem.get_Geometry(opts)
        if geom is None:
            return []

        import time

        max_paths = getattr(cfg, "symbolic_max_paths", 500) if cfg else 500
        max_pts = getattr(cfg, "symbolic_max_pts_per_path", 200) if cfg else 200

        # Hard per-element budget so one pathological family can't stall the whole view.
        # Set to 0/None to disable.
        budget_s = getattr(cfg, "symbolic_time_budget_s", 0.10) if cfg else 0.10
        try:
            budget_s = float(budget_s) if budget_s is not None else None
        except Exception:
            budget_s = 0.10
        if budget_s is not None and budget_s <= 0:
            budget_s = None

        t0 = time.time()

        loops = []
        count = 0

        max_depth = getattr(cfg, "symbolic_curve_container_max_depth", 4) if cfg else 4
        for g in _iter_curve_primitives(geom, _depth=0, _max_depth=max_depth):
            if count >= max_paths:
                break

            if budget_s is not None and (time.time() - t0) > budget_s:
                try:
                    if diag is not None and hasattr(diag, "warn"):
                        diag.warn(
                            phase="silhouette",
                            callsite="_symbolic_curves_silhouette.budget",
                            message="Symbolic curve extraction exceeded time budget; stopping early",
                            elem_id=getattr(getattr(base_elem, "Id", None), "IntegerValue", None),
                            extra={"budget_s": budget_s, "paths_emitted": count, "max_paths": max_paths},
                        )
                except Exception:
                    pass
                break

            pts_uv = []

            # Endpoint-first sampling:
            # For LINE-like curves, avoid Tessellate() entirely (it can be very expensive).
            # For everything else, fall back to Tessellate() but still cap the point count.
            g_name = ""
            try:
                g_name = g.__class__.__name__
            except Exception:
                g_name = ""

            if g_name == "PolyLine":
                try:
                    coords = g.GetCoordinates()
                    ncoords = min(len(coords), max_pts)
                    for k in range(ncoords):
                        # periodic budget check inside long polylines
                        if budget_s is not None and (k % 64) == 0 and (time.time() - t0) > budget_s:
                            break
                        p = _to_host_point(elem, coords[k])
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))
                except Exception:
                    continue

            elif hasattr(g, "GetEndPoint"):
                # Try cheap 2-point sampling first.
                # This is correct for Autodesk.Revit.DB.Line and "good enough" for many segment boundaries.
                p0 = None
                p1 = None
                try:
                    p0 = g.GetEndPoint(0)
                    p1 = g.GetEndPoint(1)
                except Exception:
                    p0 = None
                    p1 = None

                if p0 is not None and p1 is not None and g_name in ("Line", "BoundLine"):
                    try:
                        # Apply transform only if points are in family-local space
                        should_transform = _should_apply_transform(p0, inst_transform, bbox_world_center)
                        
                        if should_transform:
                            p0_world = inst_transform.OfPoint(p0)
                            p1_world = inst_transform.OfPoint(p1)
                        else:
                            p0_world = p0
                            p1_world = p1
                        
                        p0h = _to_host_point(elem, p0_world)
                        p1h = _to_host_point(elem, p1_world)
                        uv0 = view_basis.transform_to_view_uv((p0h.X, p0h.Y, p0h.Z))
                        uv1 = view_basis.transform_to_view_uv((p1h.X, p1h.Y, p1h.Z))
                        pts_uv = [(uv0[0], uv0[1]), (uv1[0], uv1[1])]
                    except Exception:
                        pts_uv = []

                else:
                    # Non-line curve: tessellate (bounded). Note Tessellate() cost is outside our point cap,
                    # so the time budget above is the real protection against stalls.
                    if hasattr(g, "Tessellate"):
                        try:
                            tess = g.Tessellate()
                            nt = min(len(tess), max_pts)
                            for k in range(nt):
                                if budget_s is not None and (k % 64) == 0 and (time.time() - t0) > budget_s:
                                    break
                                p = _to_host_point(elem, tess[k])
                                uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                                pts_uv.append((uv[0], uv[1]))
                        except Exception:
                            continue

            elif hasattr(g, "Tessellate"):
                # Fallback tessellation path for odd primitives
                try:
                    tess = g.Tessellate()
                    nt = min(len(tess), max_pts)
                    for k in range(nt):
                        if budget_s is not None and (k % 64) == 0 and (time.time() - t0) > budget_s:
                            break
                        p = _to_host_point(elem, tess[k])
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))
                except Exception:
                    continue

            if len(pts_uv) >= 2:
                
                # DIAGNOSTIC: Track where this curve came from
                try:
                    elem_id = getattr(getattr(base_elem, 'Id', None), 'IntegerValue', None)
                    if elem_id == 987587:
                        print(f"[CURVE SOURCE] count={count}, g_name={g_name}, pts={len(pts_uv)}")
                        if len(pts_uv) >= 2:
                            print(f"  First point: ({pts_uv[0][0]:.2f}, {pts_uv[0][1]:.2f})")
                except:
                    pass
        
                loops.append({"points": pts_uv, "is_hole": False, "open": True})
                count += 1

        # If instance/symbol geometry yields only swing curves (no panel boundary),
        # optionally supplement with FilledRegion boundaries extracted from the family definition.
        # This adds *outline only* (open polylines with closure point repeated), not fill.
        try:
            # Heuristic: if we only got a tiny number of edge cells / paths, try supplement.
            # (We avoid trying to infer curve types here; the extractor is budgeted + cached.)
            xyz_loops = _family_region_outlines_cached(base_elem, view, cfg=cfg, diag=diag)

            if diag is not None and hasattr(diag, "debug"):
                diag.debug(
                    phase="silhouette",
                    callsite="family_region.emit",
                    message="Family region outlines returned",
                    extra={
                        "elem_id": getattr(getattr(base_elem, "Id", None), "IntegerValue", None),
                        "loops_returned": len(xyz_loops),
                    },
                )

            if xyz_loops:
                # Apply instance transform (family-local -> instance/world), then project to UV.
                inst_T = None
                try:
                    if hasattr(base_elem, "GetTransform"):
                        inst_T = base_elem.GetTransform()
                    else:
                        inst_T = getattr(base_elem, "Transform", None)
                except Exception:
                    inst_T = None

                for xyzs in xyz_loops:
                    pts_uv = []
                    for xyz in xyzs:
                        # family-local -> instance/world
                        xyz_w = _apply_transform_xyz_tuple(inst_T, xyz)
                        # host/link adjustment (no-op for host; preserves existing behavior)
                        try:
                            from Autodesk.Revit.DB import XYZ
                            p = XYZ(xyz_w[0], xyz_w[1], xyz_w[2])
                            p = _to_host_point(elem, p)
                            uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        except Exception:
                            uv = view_basis.transform_to_view_uv((xyz_w[0], xyz_w[1], xyz_w[2]))
                        pts_uv.append((uv[0], uv[1]))

                    # Keep as OPEN polyline but include closure point (last==first) so stroke closes.
                    if len(pts_uv) >= 3:
                        loops.append({"points": pts_uv, "is_hole": False, "open": True})
        except Exception:
            pass

        return loops

    except Exception:
        return []

def _iter_curve_primitives(geom, _depth=0, _max_depth=4):
    """Yield Curve/PolyLine-like primitives from a GeometryElement recursively.

    Critical behavior:
      - Recurse into GeometryInstance instance + symbol geometry.
      - ALSO recurse into CurveLoop / CurveArray-style containers that are not themselves Curves.
        (This is where family filled-region boundaries often live.)
      - Depth-bounded to avoid pathological geometry graphs.
    """
    if geom is None:
        return
    if _depth > _max_depth:
        return

    # Best-effort class name (safe in Dynamo)
    def _name(x):
        try:
            return x.__class__.__name__
        except Exception:
            return ""

    # Conservative "container" predicate:
    # Only recurse into known curve container names or objects exposing GetEnumerator but not being a Curve.
    def _is_curve_container(x):
        n = _name(x)
        if n in ("CurveLoop", "CurveArray", "CurveArrArray"):
            return True
        # Some API wrappers show up as generic lists/enumerables; only recurse if it looks enumerable.
        if hasattr(x, "GetEnumerator") and not hasattr(x, "GetEndPoint") and n != "PolyLine":
            return True
        return False

    # Prefer enumerator when available
    try:
        it = geom.GetEnumerator()
    except Exception:
        it = None

    if it:
        while it.MoveNext():
            g = it.Current
            if g is None:
                continue

            # GeometryInstance: recurse
            if hasattr(g, "GetInstanceGeometry") or hasattr(g, "GetSymbolGeometry"):
                if hasattr(g, "GetInstanceGeometry"):
                    try:
                        ig = g.GetInstanceGeometry()
                        for x in _iter_curve_primitives(ig, _depth=_depth + 1, _max_depth=_max_depth):
                            yield x
                    except Exception:
                        pass
                if hasattr(g, "GetSymbolGeometry"):
                    try:
                        sg = g.GetSymbolGeometry()
                        for x in _iter_curve_primitives(sg, _depth=_depth + 1, _max_depth=_max_depth):
                            yield x
                    except Exception:
                        pass
                continue

            # Direct curve primitives
            if _name(g) == "PolyLine" or hasattr(g, "GetEndPoint"):
                yield g
                continue

            # Curve containers (CurveLoop, CurveArray, etc.)
            if _is_curve_container(g):
                try:
                    for x in _iter_curve_primitives(g, _depth=_depth + 1, _max_depth=_max_depth):
                        yield x
                except Exception:
                    pass
                continue

    else:
        # Fallback direct iteration
        try:
            for g in geom:
                if g is None:
                    continue

                if hasattr(g, "GetInstanceGeometry") or hasattr(g, "GetSymbolGeometry"):
                    if hasattr(g, "GetInstanceGeometry"):
                        try:
                            ig = g.GetInstanceGeometry()
                            for x in _iter_curve_primitives(ig, _depth=_depth + 1, _max_depth=_max_depth):
                                yield x
                        except Exception:
                            pass
                    if hasattr(g, "GetSymbolGeometry"):
                        try:
                            sg = g.GetSymbolGeometry()
                            for x in _iter_curve_primitives(sg, _depth=_depth + 1, _max_depth=_max_depth):
                                yield x
                        except Exception:
                            pass
                    continue

                if _name(g) == "PolyLine" or hasattr(g, "GetEndPoint"):
                    yield g
                    continue

                if _is_curve_container(g):
                    try:
                        for x in _iter_curve_primitives(g, _depth=_depth + 1, _max_depth=_max_depth):
                            yield x
                    except Exception:
                        pass
                    continue
        except Exception:
            return

def _merge_paths_by_endpoints(paths, eps=1e-6, max_iters=500):
    """
    Merge polylines whose endpoints meet (within eps). Returns list of merged polylines.
    Designed for family symbolic geometry where filled regions often appear as multiple
    curve segments that should form a single closed loop.
    """
    def _dist2(a, b):
        du = a[0] - b[0]
        dv = a[1] - b[1]
        return du * du + dv * dv

    if not paths:
        return []

    eps2 = eps * eps
    out = [p[:] for p in paths if p and len(p) >= 2]

    iters = 0
    changed = True
    while changed and iters < max_iters:
        iters += 1
        changed = False

        i = 0
        while i < len(out):
            a = out[i]
            a0, a1 = a[0], a[-1]

            j = i + 1
            while j < len(out):
                b = out[j]
                b0, b1 = b[0], b[-1]

                # a1 connects to b0: append b (skip duplicate)
                if _dist2(a1, b0) <= eps2:
                    out[i] = a + b[1:]
                    out.pop(j)
                    changed = True
                    break

                # a1 connects to b1: append reversed b
                if _dist2(a1, b1) <= eps2:
                    out[i] = a + list(reversed(b[:-1]))
                    out.pop(j)
                    changed = True
                    break

                # a0 connects to b1: prepend b
                if _dist2(a0, b1) <= eps2:
                    out[i] = b[:-1] + a
                    out.pop(j)
                    changed = True
                    break

                # a0 connects to b0: prepend reversed b
                if _dist2(a0, b0) <= eps2:
                    out[i] = list(reversed(b[1:])) + a
                    out.pop(j)
                    changed = True
                    break

                j += 1

            if changed:
                # restart comparisons for this i with the newly merged polyline
                a = out[i]
                a0, a1 = a[0], a[-1]
                continue

            i += 1

    return out

def _cad_curves_silhouette(elem, view, view_basis, raster, cfg=None):
    """
    Extract curve primitives from DWG/DXF ImportInstance geometry and return OPEN polylines.
    Intended to avoid bbox/obb rectangles for imports.

    NOTE:
    - Revit may return different geometry representations depending on Options.View binding.
    - For ImportInstance (CAD), binding Options.View can sometimes suppress curve primitives
      (e.g., return display/tessellated geometry). We therefore try BOTH:
        1) Options without View binding
        2) Options with View binding (fallback)
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel
    except Exception:
        return []

    base_elem = _unwrap_elem(elem)

    # DWG imports can contain many thousands of segments.
    # Default caps must be high enough to avoid truncation artifacts.
    default_max_paths = 20000
    default_max_pts = 2000

    max_paths = getattr(cfg, "cad_max_paths", default_max_paths) if cfg else default_max_paths
    max_pts = getattr(cfg, "cad_max_pts_per_path", default_max_pts) if cfg else default_max_pts

    # Adaptive DWG budgeting: keep only what can affect the raster at the current cell size.
    # This prevents "magic number" caps from truncating meaningful content.
    cell = getattr(raster, "cell_size_ft", None)
    if cell is None or cell <= 0:
        cell = 1.0  # conservative fallback; should not happen

    # Drop segments shorter than this (in view UV units, feet). Tuned to raster resolution.
    min_seg_len = 0.35 * cell

    # Direction binning: treat near-parallel segments as redundant within a cell.
    # 8 bins across 180° (undirected); lines at theta and theta+pi map to same bin.
    dir_bins = 8

    # Per-cell directional occupancy: (cu, cv, bin) -> seen
    seen = {}

    def _extract_from_geom(geom):
        loops = []
        count = 0

        if geom is None:
            return loops

        # Also scan raw geometry (recursively) for non-curve objects with meaningful bounding boxes.
        # DWG text often appears as nested instance/display geometry, not curve primitives.
        def _iter_geom_objects(g):
            if g is None:
                return
            try:
                it2 = g.GetEnumerator()
            except Exception:
                it2 = None

            if it2:
                while it2.MoveNext():
                    o = it2.Current
                    if o is None:
                        continue
                    yield o

                    # Recurse into instance geometry if present
                    if hasattr(o, "GetInstanceGeometry"):
                        try:
                            ig = o.GetInstanceGeometry()
                            for x in _iter_geom_objects(ig):
                                yield x
                        except Exception:
                            pass
            else:
                try:
                    for o in g:
                        if o is None:
                            continue
                        yield o
                        if hasattr(o, "GetInstanceGeometry"):
                            try:
                                ig = o.GetInstanceGeometry()
                                for x in _iter_geom_objects(ig):
                                    yield x
                            except Exception:
                                pass
                except Exception:
                    return

        try:
            for obj in _iter_geom_objects(geom):
                try:
                    name = obj.__class__.__name__
                except Exception:
                    name = None

                # Skip obvious curve primitives (handled below)
                if name in ("Line", "Arc", "NurbSpline", "HermiteSpline", "PolyLine", "Curve"):
                    continue
                if hasattr(obj, "GetEndPoint") or name == "PolyLine":
                    continue

                bb = None
                try:
                    bb = getattr(obj, "BoundingBox", None)
                except Exception:
                    bb = None

                if bb is None:
                    try:
                        bb = obj.GetBoundingBox()
                    except Exception:
                        bb = None

                if bb is not None:
                    _try_add_bbox_fill(loops, bb)

        except Exception:
            pass

        for (g, g_trf) in _iter_curve_primitives_xform(geom, trf=None):
            if count >= max_paths:
                break

            pts_uv = []

            # PolyLine
            if g.__class__.__name__ == "PolyLine":
                try:
                    coords = g.GetCoordinates()
                except Exception:
                    coords = None

                if coords:
                    for k in range(min(len(coords), max_pts)):
                        p = coords[k]
                        if g_trf is not None:
                            try:
                                p = g_trf.OfPoint(p)
                            except Exception:
                                pass
                        p = _to_host_point(elem, p)
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))

            # Curve
            elif hasattr(g, "Tessellate"):
                try:
                    tess = g.Tessellate()
                except Exception:
                    tess = None

                if tess:
                    for k in range(min(len(tess), max_pts)):
                        p = tess[k]
                        if g_trf is not None:
                            try:
                                p = g_trf.OfPoint(p)
                            except Exception:
                                pass
                        p = _to_host_point(elem, p)
                        uv = view_basis.transform_to_view_uv((p.X, p.Y, p.Z))
                        pts_uv.append((uv[0], uv[1]))

            if len(pts_uv) >= 2:
                # Reduce consecutive points that fall in the same raster cell (no visible change).
                compact = []
                last_cell = None
                for (u, v) in pts_uv:
                    cu = int(u / cell) if cell else 0
                    cv = int(v / cell) if cell else 0
                    c = (cu, cv)
                    if c != last_cell:
                        compact.append((u, v))
                        last_cell = c

                # Keep only segments that are long enough to matter at this cell size
                # and that add new info in cell+direction space.
                kept = []
                for i in range(len(compact) - 1):
                    u0, v0 = compact[i]
                    u1, v1 = compact[i + 1]
                    du = (u1 - u0)
                    dv = (v1 - v0)
                    seg_len = math.sqrt(du * du + dv * dv)
                    if seg_len < min_seg_len:
                        continue

                    # Midpoint cell for bucketing
                    um = 0.5 * (u0 + u1)
                    vm = 0.5 * (v0 + v1)
                    cu = int(um / cell) if cell else 0
                    cv = int(vm / cell) if cell else 0

                    # Undirected angle bin in [0, pi)
                    ang = math.atan2(dv, du)
                    if ang < 0:
                        ang += math.pi
                    b = int((ang / math.pi) * dir_bins)
                    if b >= dir_bins:
                        b = dir_bins - 1

                    key = (cu, cv, b)
                    if key in seen:
                        continue
                    seen[key] = 1

                    # Keep this segment (as a 2-pt polyline chunk)
                    kept.append((u0, v0))
                    kept.append((u1, v1))

                # If anything survived, store as an OPEN polyline loop.
                if len(kept) >= 2:
                    loops.append({"points": kept, "is_hole": False, "open": True})
                    count += 1

        return loops

    def _try_add_bbox_fill(loops, bbox):
        """
        Heuristic: add a closed rect loop (fill) for non-curve CAD geometry (often text).
        Uses view UV projection of bbox corners.

        Guardrails:
        - Ignore degenerate bboxes
        - Ignore huge bboxes (likely the overall import extents)
        - Ignore tiny bboxes (sub-cell noise)
        """
        if bbox is None:
            return False

        try:
            mn = bbox.Min
            mx = bbox.Max
            if mn is None or mx is None:
                return False
        except Exception:
            return False

        # Project bbox corners to UV (axis-aligned in UV)
        try:
            uv_mn = view_basis.transform_to_view_uv((mn.X, mn.Y, mn.Z))
            uv_mx = view_basis.transform_to_view_uv((mx.X, mx.Y, mx.Z))
        except Exception:
            return False

        u0 = min(uv_mn[0], uv_mx[0])
        u1 = max(uv_mn[0], uv_mx[0])
        v0 = min(uv_mn[1], uv_mx[1])
        v1 = max(uv_mn[1], uv_mx[1])

        du = (u1 - u0)
        dv = (v1 - v0)
        if du <= 1e-9 or dv <= 1e-9:
            return False

        cell = getattr(raster, "cell_size_ft", None)
        if cell is None or cell <= 0:
            cell = 1.0

        diag = math.sqrt(du * du + dv * dv)

        # Tunables: sized to raster resolution (avoid “entire import bbox” and micro-fragments)
        min_diag = 0.75 * cell
        max_diag = 40.0 * cell

        if diag < min_diag or diag > max_diag:
            return False

        # Proxy-ink rectangle around likely text:
        # open=True routes to rasterize_open_polylines (non-occluding ink).
        pts = [(u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)]
        loops.append({"points": pts, "is_hole": False, "open": True, "strategy": "cad_text_bbox"})
        return True

    def _get_geom(bind_view):
        try:
            opts = Options()
            opts.ComputeReferences = False

            # Imports can hide curve primitives unless this is True in some cases
            try:
                opts.IncludeNonVisibleObjects = True
            except Exception:
                pass

            try:
                opts.DetailLevel = ViewDetailLevel.Fine
            except Exception:
                pass

            if bind_view:
                try:
                    opts.View = view
                except Exception:
                    pass

            return base_elem.get_Geometry(opts)
        except Exception:
            return None

    # Attempt 1: NO view binding (preferred for ImportInstance curve primitives)
    geom1 = _get_geom(bind_view=False)
    loops1 = _extract_from_geom(geom1)
    if loops1:
        return loops1

    # Attempt 2: WITH view binding (fallback)
    geom2 = _get_geom(bind_view=True)
    loops2 = _extract_from_geom(geom2)
    return loops2

def _to_host_point(elem, xyz):
    """
    If elem is a LinkedElementProxy, map link-space XYZ -> host-space XYZ.

    IMPORTANT: Do NOT apply transforms for arbitrary elements that merely expose
    a '.transform' attribute. In this pipeline, link-space geometry is modeled
    explicitly via LinkedElementProxy.
    """
    try:
        if type(elem).__name__ != "LinkedElementProxy":
            return xyz
    except Exception:
        return xyz

    trf = getattr(elem, "transform", None)
    if trf is None:
        return xyz
    try:
        return trf.OfPoint(xyz)
    except Exception:
        return xyz

def _unwrap_elem(elem):
    """
    Many parts of the pipeline may pass proxy objects (e.g. linked proxies)
    that wrap the real DB.Element at .element. Strategy type checks must
    use the underlying element when present.
    """
    try:
        inner = getattr(elem, "element", None)
        return inner if inner is not None else elem
    except Exception:
        return elem

def get_element_silhouette(elem, view, view_basis, raster, cfg=None, cache=None, cache_key=None, diag=None):
    """Extract element silhouette as 2D loops.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis object for coordinate transformation
        raster: ViewRaster (provides cell size for UV mode classification)
        cfg: Optional Config object (provides strategy settings)

    Returns:
        List of loop dicts, each with:
        {
            'points': [(u, v), ...],  # View-local UV coordinates
            'is_hole': False,          # Whether this loop is a hole
            'strategy': 'bbox'|'obb'|'uv_obb_rect'|'silhouette_edges'|'cad_curves'  # Which strategy was used
        }

   'strategy': 'bbox'|'obb'|'uv_obb_rect'|'silhouette_edges'|'cad_curves' Commentary:
        - Returns empty list if element has no geometry
        - Tries strategies in order based on element UV mode (TINY/LINEAR/AREAL)
        - bbox is the ultimate fallback (always succeeds if element has bbox)
    """

    # Precompute ids used by diagnostics / planar-face selection (safe in tests and in Revit).
    try:
        view_id = int(getattr(getattr(view, "Id", None), "IntegerValue", 0))
    except Exception:
        view_id = 0
    try:
        elem_id = int(getattr(getattr(elem, "Id", None), "IntegerValue", 0))
    except Exception:
        elem_id = 0

    # Best-effort face collection for planar-face selection strategy.
    # In non-Revit unit tests this will remain [], and the strategy can be monkeypatched.
    element_faces = []
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel
    except Exception:
        Options = None
        ViewDetailLevel = None

    if Options is not None and hasattr(elem, "get_Geometry"):
        try:
            opts = Options()
            opts.ComputeReferences = False
            opts.IncludeNonVisibleObjects = False
            try:
                if ViewDetailLevel is not None:
                    opts.DetailLevel = ViewDetailLevel.Medium
            except Exception:
                pass

            # Same linked-element guard as elsewhere: don't bind opts.View for linked proxies
            if not hasattr(elem, "transform"):
                try:
                    opts.View = view
                except Exception:
                    pass

            geom = elem.get_Geometry(opts)
            if geom is not None:
                # Collect faces from solids (PlanarFace filtering happens downstream in face_selection)
                for solid in _iter_solids(geom):
                    try:
                        faces = getattr(solid, "Faces", None)
                    except Exception:
                        faces = None
                    if not faces:
                        continue
                    try:
                        for f in faces:
                            if f is not None:
                                element_faces.append(f)
                    except Exception:
                        continue
        except Exception:
            # Best-effort only; keep element_faces empty on failure.
            element_faces = []


    # PR12: geometry cache (caller provides bounded LRU; this function treats it as optional).
    if cache is not None and cache_key is not None:
        try:
            cached = cache.get(cache_key, default=None)
            if cached is not None:
                return cached
        except Exception:
            pass

    # Special-cases first (DWG + family symbolic)
    base_elem = _unwrap_elem(elem)

    try:
        from Autodesk.Revit.DB import ImportInstance, FamilyInstance
    except Exception:
        ImportInstance = None
        FamilyInstance = None

    strategies = None

    # Prefer type-based detection (true ImportInstance)
    if ImportInstance is not None and isinstance(base_elem, ImportInstance):
        strategies = ['cad_curves', 'bbox']

    # Some collection paths may hand us a wrapped/proxied CAD element that isn't an ImportInstance.
    # In those cases, use a conservative heuristic: category name includes .dwg/.dxf.
    if strategies is None:
        try:
            cat = getattr(base_elem, "Category", None)
            cat_name = getattr(cat, "Name", None) if cat is not None else None
            if cat_name:
                ln = str(cat_name).lower()
                if (".dwg" in ln) or (".dxf" in ln):
                    strategies = ['cad_curves', 'bbox']
        except Exception:
            pass

    # Family instances: prefer symbolic curves where possible
    if strategies is None and FamilyInstance is not None and isinstance(base_elem, FamilyInstance):
        if cfg is None:
            strategies = ['symbolic_curves', 'silhouette_edges', 'obb', 'bbox']
        else:
            uv_mode = _determine_uv_mode(elem, view, view_basis, raster, cfg)
            if uv_mode == 'TINY':
                strategies = ['symbolic_curves', 'bbox', 'obb']
            elif uv_mode == 'LINEAR':
                # Strategy ordering for LINEAR elements (first successful strategy wins):
                # 1. detail_line_band: Detail/drafting lines → oriented band rectangles (archive parity)
                #    - Creates CLOSED 2-cell-wide bands along line tangent
                #    - Prevents invisible Bresenham single-pixel rendering
                # 2. symbolic_curves: Family instance symbolic edges → open polylines
                # 3. cad_curves: CAD/DWG curve primitives → open polylines
                # 4. silhouette_edges: 3D edge extraction → open/closed loops
                # 5. uv_obb_rect: Oriented bounding box fallback
                # 6. bbox: Axis-aligned bounding box (last resort)
                strategies = ['detail_line_band', 'symbolic_curves', 'cad_curves', 'silhouette_edges', 'uv_obb_rect', 'bbox']
            else:
                strategies = ['symbolic_curves', 'silhouette_edges', 'obb', 'bbox']

    # Default for all other elements
    if strategies is None:
        if cfg is None:
            strategies = ['silhouette_edges', 'obb', 'bbox']
        else:
            uv_mode = _determine_uv_mode(elem, view, view_basis, raster, cfg)

            # If config provides per-uv-mode strategy ordering, honor it.
            # This is the decision boundary the unit test is trying to validate.
            if hasattr(cfg, "get_silhouette_strategies"):
                try:
                    s = cfg.get_silhouette_strategies(uv_mode)
                    if s:
                        strategies = list(s)
                    else:
                        strategies = None
                except Exception:
                    strategies = None

            # Fallback to legacy defaults if cfg doesn't provide strategies (or failed).
            if strategies is None:
                if uv_mode == 'TINY':
                    strategies = ['bbox', 'obb']
                elif uv_mode == 'LINEAR':
                    strategies = ['uv_obb_rect', 'bbox']
                else:
                    strategies = ['silhouette_edges', 'obb', 'bbox']

    # Diagnostics: capture attempt order + outcomes.
    # Emitted only on planar success or bbox_fallback use to avoid noise.
    _silhouette_attempts = []

    # Try each strategy in order
    for strategy_name in strategies:
        try:
            if strategy_name == 'detail_line_band':
                loops = _detail_line_band_silhouette(elem, view, view_basis, cfg, diag)
            elif strategy_name == 'uv_obb_rect':
                loops = _uv_obb_rect_silhouette(elem, view, view_basis)
            elif strategy_name == 'bbox':
                loops = _bbox_silhouette(elem, view, view_basis)
            elif strategy_name == 'obb':
                loops = _obb_silhouette(elem, view, view_basis)
            elif strategy_name == 'planar_face_loops':
                loops = _planar_face_loops_silhouette(
                    element_faces,
                    view_basis,
                    elem=elem,
                    diag=diag,
                    view_id=view_id,
                    elem_id=elem_id,
                )
            elif strategy_name == 'silhouette_edges':
                loops = _silhouette_edges(elem, view, view_basis, cfg)
            elif strategy_name == 'front_face_loops':
                loops = _front_face_loops_silhouette(elem, view, view_basis, cfg)
            elif strategy_name == 'cad_curves':
                loops = _cad_curves_silhouette(elem, view, view_basis, raster, cfg)
            elif strategy_name == 'symbolic_curves':
                loops = _symbolic_curves_silhouette(elem, view, view_basis, cfg, diag=diag)
            else:
                continue

            if loops:
                # Tag loops with strategy used
                for loop in loops:
                    loop['strategy'] = strategy_name

                if diag is not None:
                    try:
                        # Mark last attempt as success
                        if _silhouette_attempts and _silhouette_attempts[-1].get("strategy") == str(strategy_name):
                            _silhouette_attempts[-1]["ok"] = True
                            _silhouette_attempts[-1]["loops"] = int(len(loops))
                        else:
                            _silhouette_attempts.append({"strategy": str(strategy_name), "ok": True, "loops": int(len(loops))})

                        # Only emit a success event when planar wins (keeps noise down)
                        if str(strategy_name) == "planar_face_loops":
                            diag.debug(
                                phase="silhouette",
                                callsite="get_element_silhouette.strategy_success",
                                message="Silhouette strategy succeeded",
                                view_id=view_id if 'view_id' in locals() else None,
                                elem_id=elem_id if 'elem_id' in locals() else None,
                                extra={
                                    "uv_mode": uv_mode if 'uv_mode' in locals() else None,
                                    "winner": str(strategy_name),
                                    "attempts": list(_silhouette_attempts) if '_silhouette_attempts' in locals() else None,
                                },
                            )
                    except Exception:
                        pass

                if cache is not None and cache_key is not None:
                    try:
                        cache.set(cache_key, [dict(loop) for loop in loops])
                    except Exception:
                        pass

                return loops

            # Record that we tried this strategy (success/failure appended below).
            if diag is not None:
                try:
                    _silhouette_attempts.append({"strategy": str(strategy_name), "ok": None})
                except Exception:
                    pass

        except Exception as e:
            # Strategy failed, try next
            if diag is not None:
                try:
                    _silhouette_attempts.append(
                        {
                            "strategy": str(strategy_name),
                            "ok": False,
                            "err_type": type(e).__name__,
                        }
                    )
                except Exception:
                    pass
            pass

    # Ultimate fallback: bbox
    try:
        loops = _bbox_silhouette(elem, view, view_basis)
        for loop in loops:
            loop['strategy'] = 'bbox_fallback'

        if diag is not None:
            try:
                # Mark last attempt (if any) as unsuccessful, then record bbox_fallback as winner.
                if _silhouette_attempts and _silhouette_attempts[-1].get("ok") is None:
                    _silhouette_attempts[-1]["ok"] = False

                diag.debug(
                    phase="silhouette",
                    callsite="get_element_silhouette.bbox_fallback",
                    message="Silhouette strategies fell through to bbox fallback",
                    view_id=view_id,
                    elem_id=elem_id,
                    extra={
                        "uv_mode": uv_mode if 'uv_mode' in locals() else None,
                        "strategies": list(strategies) if strategies is not None else None,
                        "attempts": list(_silhouette_attempts),
                    },
                )
            except Exception:
                pass

        return loops

    except Exception:

        if diag is not None:
            try:
                diag.debug(
                    phase="silhouette",
                    callsite="get_element_silhouette.strategy_failed",
                    message="All silhouette strategies failed; returning empty (downstream may bbox/obb fallback)",
                    view_id=view_id if 'view_id' in locals() else None,
                    elem_id=elem_id if 'elem_id' in locals() else None,
                    extra={
                        "uv_mode": uv_mode if 'uv_mode' in locals() else None,
                        "strategies": list(strategies) if strategies is not None else None,
                        "attempts": list(_silhouette_attempts) if '_silhouette_attempts' in locals() else None,
                    },
                )
            except Exception:
                pass

        if diag is not None:
            print(
                f"[DEBUG][silhouette] elem={elem_id} attempts="
                f"{_silhouette_attempts if '_silhouette_attempts' in locals() else None}"
            )

        return []

def _uv_obb_rect_silhouette(elem, view, view_basis):
    """
    Return a single closed loop representing the UV OBB rectangle from bbox corners.
    This is used for LINEAR classification so diagonals render as thin oriented boxes,
    not fat axis-aligned rectangles.
    """
    rect, lu, lv = _uv_obb_rect_from_bbox(elem, view, view_basis)
    if not rect or len(rect) < 4:
        return []
    return [{"points": rect, "is_hole": False}]


def _bbox_silhouette(elem, view, view_basis):
    """Extract axis-aligned bounding box silhouette.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis

    Returns:
        List with one loop (rectangle in view UVW coordinates, W for depth)
    """
    try:
        from vop_interwoven.revit.view_basis import world_to_view

        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return []

        # CRITICAL: LinkedElementProxy.get_BoundingBox(view) already returns host-space bbox
        # Do NOT apply transform - bbox coordinates are already in correct space
        min_pt_tuple = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
        max_pt_tuple = (bbox.Max.X, bbox.Max.Y, bbox.Max.Z)

        # Project to view UVW (with depth)
        min_uvw = world_to_view(min_pt_tuple, view_basis)
        max_uvw = world_to_view(max_pt_tuple, view_basis)

        # Build rectangle (use min depth for occlusion)
        u_min = min(min_uvw[0], max_uvw[0])
        u_max = max(min_uvw[0], max_uvw[0])
        v_min = min(min_uvw[1], max_uvw[1])
        v_max = max(min_uvw[1], max_uvw[1])
        w_min = min(min_uvw[2], max_uvw[2])  # Nearest depth

        if u_min >= u_max or v_min >= v_max:
            return []

        # Store (u, v, w) tuples - all points at same depth for bbox
        points = [
            (u_min, v_min, w_min),
            (u_max, v_min, w_min),
            (u_max, v_max, w_min),
            (u_min, v_max, w_min),
            (u_min, v_min, w_min)  # Close the loop
        ]

        return [{'points': points, 'is_hole': False}]

    except Exception:
        return []


def _obb_silhouette(elem, view, view_basis):
    """Extract oriented bounding box silhouette (all 8 bbox corners).

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis

    Returns:
        List with one loop (convex hull of projected bbox corners, with depth)
    """
    try:
        from Autodesk.Revit.DB import XYZ
        from vop_interwoven.revit.view_basis import world_to_view

        bbox = elem.get_BoundingBox(view)
        if not bbox or not bbox.Min or not bbox.Max:
            return []

        # Get all 8 corners of 3D bbox
        mn = bbox.Min
        mx = bbox.Max
        corners_3d = [
            XYZ(mn.X, mn.Y, mn.Z),
            XYZ(mx.X, mn.Y, mn.Z),
            XYZ(mn.X, mx.Y, mn.Z),
            XYZ(mx.X, mx.Y, mn.Z),
            XYZ(mn.X, mn.Y, mx.Z),
            XYZ(mx.X, mn.Y, mx.Z),
            XYZ(mn.X, mx.Y, mx.Z),
            XYZ(mx.X, mx.Y, mx.Z),
        ]

        # CRITICAL: bbox corners are already in host space for LinkedElementProxy
        # Use corner coordinates directly without transform
        corners_uvw = []
        min_w = float('inf')
        for corner in corners_3d:
            uvw = world_to_view((corner.X, corner.Y, corner.Z), view_basis)
            corners_uvw.append(uvw)
            if uvw[2] < min_w:
                min_w = uvw[2]

        if len(corners_uvw) < 3:
            return []

        # Compute convex hull in UV (ignoring W for hull computation)
        corners_uv = [(uvw[0], uvw[1]) for uvw in corners_uvw]
        hull_uv = _convex_hull_2d(corners_uv)

        if len(hull_uv) < 3:
            return []

        # Add minimum depth to hull points
        hull_uvw = [(u, v, min_w) for (u, v) in hull_uv]

        return [{'points': hull_uvw, 'is_hole': False}]

    except Exception:
        return []

def _front_face_loops_silhouette(elem, view, view_basis, cfg=None):
    """
    Extract loops from the most relevant front-facing planar face(s).
    Unlike _silhouette_edges(), this preserves multiple loops + holes without point-cloud ordering.
    Intended for AREAL elements (floors with openings, etc.).
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel, UV, PlanarFace
    except Exception:
        return []

    try:
        view_direction = view.ViewDirection
    except Exception:
        return []

    try:
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        try:
            opts.DetailLevel = ViewDetailLevel.Medium
        except Exception:
            pass

        # Same linked-element rule as elsewhere
        if not hasattr(elem, 'transform'):
            try:
                opts.View = view
            except Exception:
                pass

        geom = elem.get_Geometry(opts)
        if geom is None:
            return []
    except Exception:
        return []

    loops_out = []

    # Find candidate planar faces that are front-facing
    faces = []
    for solid in _iter_solids(geom):
        if not solid or getattr(solid, 'Volume', 0) <= 1e-9:
            continue
        try:
            for face in solid.Faces:
                if face is None:
                    continue
                # Prefer planar faces (stable normals + edge loops)
                try:
                    is_planar = isinstance(face, PlanarFace)
                except Exception:
                    is_planar = False
                if not is_planar:
                    continue

                try:
                    bbox_uv = face.GetBoundingBox()
                    if not bbox_uv:
                        continue
                    u_mid = (bbox_uv.Min.U + bbox_uv.Max.U) / 2.0
                    v_mid = (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0
                    normal = face.ComputeNormal(UV(u_mid, v_mid))
                except Exception:
                    continue

                try:
                    dot = normal.DotProduct(view_direction)
                except Exception:
                    continue

                # Front-facing in your convention: dot < 0
                if dot < -0.25:
                    # Use face area to pick dominant face when multiple exist
                    try:
                        area = float(getattr(face, "Area", 0.0))
                    except Exception:
                        area = 0.0
                    faces.append((area, face))
        except Exception:
            continue

    if not faces:
        return []

    # Sort by area descending; take a small cap to avoid crazy multi-face outputs
    faces.sort(key=lambda x: x[0], reverse=True)
    max_faces = getattr(cfg, "front_face_max_faces", 2) if cfg else 2

    from vop_interwoven.revit.view_basis import world_to_view

    for _, face in faces[:max_faces]:
        try:
            edge_loops = face.EdgeLoops
        except Exception:
            continue
        if not edge_loops:
            continue

        # Revit convention: first loop is outer; subsequent loops are holes (usually true for planar faces)
        for li, edge_loop in enumerate(edge_loops):
            pts = []
        # EdgeLoops is an EdgeArrayArray; use enumerators explicitly for IronPython safety
        try:
            loops_it = edge_loops.GetEnumerator()
        except Exception:
            loops_it = None

        li = 0
        while loops_it and loops_it.MoveNext():
            edge_loop = loops_it.Current
            pts = []

            try:
                edges_it = edge_loop.GetEnumerator()
            except Exception:
                edges_it = None

            while edges_it and edges_it.MoveNext():
                edge = edges_it.Current
                try:
                    curve = edge.AsCurve()
                    if curve is None:
                        continue
                    try:
                        tess = curve.Tessellate()
                        points_3d = list(tess)
                    except Exception:
                        points_3d = [curve.GetEndPoint(0), curve.GetEndPoint(1)]

                    for p in points_3d:
                        ph = _to_host_point(elem, p)
                        uvw = world_to_view((ph.X, ph.Y, ph.Z), view_basis)
                        pts.append(uvw)
                except Exception:
                    continue

            if len(pts) >= 3:
                cleaned = []
                last = None
                for p in pts:
                    key = (round(p[0], 6), round(p[1], 6))
                    if last is None or key != last:
                        cleaned.append(p)
                        last = key
                if cleaned and cleaned[0] != cleaned[-1]:
                    cleaned.append(cleaned[0])

                loops_out.append({
                    "points": cleaned,
                    "is_hole": True if li > 0 else False,
                })

            li += 1


    return loops_out


def _silhouette_edges(elem, view, view_basis, cfg):
    """Extract true silhouette edges based on view direction.

    This preserves concave shapes (L, U, C, etc.) by extracting actual
    visible edges rather than using convex hull approximation.

    Args:
        elem: Revit Element
        view: Revit View
        view_basis: ViewBasis
        cfg: Config object

    Returns:
        List with loops representing actual silhouette (preserves concavity)
    """
    try:
        from Autodesk.Revit.DB import Options, ViewDetailLevel, UV
    except Exception:
        return []

    if not hasattr(elem, 'get_Geometry'):
        return []

    # Get view direction for silhouette detection
    try:
        view_direction = view.ViewDirection
    except Exception:
        # Can't determine view direction, fall back
        return []

    # Get geometry with appropriate detail level
    try:
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        opts.DetailLevel = ViewDetailLevel.Medium

        # CRITICAL: Never set opts.View for linked elements - causes geometry extraction failure
        # Linked elements (LinkedElementProxy) have .transform attribute
        if not hasattr(elem, 'transform'):  # Host element only
            try:
                opts.View = view
            except Exception:
                pass
        # For linked elements: leave opts.View = None (extract in link coordinates)

        geom = elem.get_Geometry(opts)
        if geom is None:
            return []
    except Exception:
        return []

    # Collect silhouette edges
    silhouette_points = []

    for solid in _iter_solids(geom):
        if not solid or getattr(solid, 'Volume', 0) <= 1e-9:
            continue

        try:
            faces = solid.Faces
        except Exception:
            continue

        if not faces:
            continue

        # Build edge -> faces mapping to identify silhouette edges
        edge_face_map = {}

        for face in faces:
            try:
                # Get face normal at center
                bbox_uv = face.GetBoundingBox()
                if not bbox_uv:
                    continue

                u_mid = (bbox_uv.Min.U + bbox_uv.Max.U) / 2.0
                v_mid = (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0

                try:
                    uv = UV(u_mid, v_mid)
                    normal = face.ComputeNormal(uv)
                except Exception:
                    continue

                # Check if face is front-facing
                # View direction points INTO screen, so negative dot = facing us
                dot = normal.DotProduct(view_direction)
                is_front_facing = dot < 0

                # Get edges of this face
                try:
                    edge_loops = face.EdgeLoops
                except Exception:
                    continue

                if not edge_loops:
                    continue

                for edge_loop in edge_loops:
                    for edge in edge_loop:
                        try:
                            curve = edge.AsCurve()
                            if not curve:
                                continue

                            # Create edge key (order-independent)
                            p0 = curve.GetEndPoint(0)
                            p1 = curve.GetEndPoint(1)

                            key = tuple(sorted([
                                (round(p0.X, 6), round(p0.Y, 6), round(p0.Z, 6)),
                                (round(p1.X, 6), round(p1.Y, 6), round(p1.Z, 6))
                            ]))

                            if key not in edge_face_map:
                                edge_face_map[key] = []

                            edge_face_map[key].append((edge, is_front_facing))

                        except Exception:
                            continue

            except Exception:
                continue

        # Find silhouette edges (boundary or front/back transition)
        for edge_key, face_list in edge_face_map.items():
            is_silhouette = False

            if len(face_list) == 1:
                # Boundary edge - always silhouette
                is_silhouette = True
            elif len(face_list) == 2:
                # Check if front/back transition
                _, is_front_0 = face_list[0]
                _, is_front_1 = face_list[1]
                is_silhouette = (is_front_0 != is_front_1)

            if is_silhouette and len(face_list) > 0:
                edge_obj = face_list[0][0]
                try:
                    curve = edge_obj.AsCurve()
                    if not curve:
                        continue

                    # Tessellate edge
                    try:
                        tess = curve.Tessellate()
                        points_3d = list(tess)
                    except Exception:
                        # Fallback: use endpoints
                        points_3d = [curve.GetEndPoint(0), curve.GetEndPoint(1)]

                    # Project to view UVW (with depth)
                    from vop_interwoven.revit.view_basis import world_to_view
                    for pt in points_3d:
                        pt_h = _to_host_point(elem, pt)
                        uvw = world_to_view((pt_h.X, pt_h.Y, pt_h.Z), view_basis)
                        silhouette_points.append(uvw)

                except Exception:
                    continue

    if len(silhouette_points) < 3:
        return []

    # Build polygon from silhouette points
    # For now, use simplified approach: order by connectivity
    # TODO: Implement proper edge chaining for multiple loops
    loop_points = _order_points_by_connectivity(silhouette_points)

    if len(loop_points) >= 3:
        return [{'points': loop_points, 'is_hole': False}]
    else:
        return []

def _planar_face_loops_silhouette(
    element_faces,
    view_basis,
    *,
    elem=None,
    diag=None,
    view_id=None,
    elem_id=None,
):
    """
    Planar front-face projection source:
      - select planar front-facing faces
      - group by plane
      - dominant face per plane-group
      - top-2 plane-groups (hard-coded)
      - emit loops as dicts: {"points":[(u,v)...], "open":False, "is_hole":bool}

    NOTE: top_n is intentionally hard-coded to 2.
          This is the exact place to promote to Config once stabilized.
    """
    # Import face_selection in both packaged and flat layouts.
    # core/silhouette.py and core/face_selection.py are siblings inside vop_interwoven.core.
    try:
        from . import face_selection as fs  # type: ignore
    except Exception:
        try:
            from vop_interwoven.core import face_selection as fs  # type: ignore
        except Exception:
            import face_selection as fs  # type: ignore

    def _tessellated_xyz_points_from_curveloop(curveloop):
        pts = []
        try:
            for crv in curveloop:
                try:
                    tess = crv.Tessellate()
                    for p in tess:
                        try:
                            pts.append((float(p.X), float(p.Y), float(p.Z)))
                        except Exception:
                            pts.append((float(p[0]), float(p[1]), float(p[2])))
                except Exception:
                    continue
        except Exception:
            return []
        return pts

    def _xyz_to_host(xyz_tup):
        # xyz_tup is (x,y,z) floats
        try:
            trf = getattr(elem, "transform", None) if elem is not None else None
            if trf is None:
                return xyz_tup
            # _apply_transform_xyz_tuple(T, xyz)
            return _apply_transform_xyz_tuple(trf, xyz_tup)
        except Exception:
            return xyz_tup

    def _project_xyz_to_uv(points_xyz):
        out = []
        for p in points_xyz:
            try:
                ph = _xyz_to_host(p)
                u, v = view_basis.transform_to_view_uv(ph)
                out.append((float(u), float(v)))
            except Exception:
                continue
        return out

    def _dedupe_consecutive(points_uv, tol=1e-9):
        if not points_uv:
            return []
        out = [points_uv[0]]
        for (u, v) in points_uv[1:]:
            pu, pv = out[-1]
            if abs(u - pu) <= tol and abs(v - pv) <= tol:
                continue
            out.append((u, v))
        return out

    def _ensure_closed(points_uv):
        if len(points_uv) < 3:
            return points_uv
        if points_uv[0] != points_uv[-1]:
            return points_uv + [points_uv[0]]
        return points_uv

    # 1) front-facing planar faces
    front_faces = list(
        fs.iter_front_facing_planar_faces(
            element_faces,
            view_basis.forward,
            diag=diag,
            view_id=view_id,
            elem_id=elem_id,
            callsite="silhouette.planar_face_loops",
        )
    )
    if not front_faces:
        return []

    # 2) plane grouping
    plane_groups = fs.group_faces_by_plane(front_faces)
    if not plane_groups:
        return []

    # 3) dominant face per plane-group
    selections = fs.select_dominant_face_per_plane_group(plane_groups, view_basis)

    # 4) top-N plane-groups (HARD-CODED)
    # TODO: Promote `top_n` to Config once behavior is proven stable
    top = fs.select_top_plane_groups(selections, top_n=2)
    if not top:
        return []

    loops_dicts = []

    # 5) emit EdgeLoops directly (outer + holes) as UV point loops
    for sel in top:
        face = sel.get("face")
        if face is None:
            continue

        try:
            edge_loops = getattr(face, "EdgeLoops", None)
        except Exception:
            edge_loops = None

        if not edge_loops:
            continue

        # Convert each CurveLoop to UV polyline, compute signed area for hole labeling
        uv_loops = []
        for cl in edge_loops:
            xyz_pts = _tessellated_xyz_points_from_curveloop(cl)
            uv = _project_xyz_to_uv(xyz_pts)
            uv = _dedupe_consecutive(uv)
            uv = _ensure_closed(uv)
            if len(uv) < 4:
                continue
            a_signed = float(fs.signed_polygon_area_2d(uv))
            uv_loops.append((uv, a_signed))

        if not uv_loops:
            continue

        # Determine outer loop as the max-abs-area loop; everything else is treated as a hole.
        outer_idx = max(range(len(uv_loops)), key=lambda i: abs(uv_loops[i][1]))

        for i, (uv, _a_signed) in enumerate(uv_loops):
            loops_dicts.append(
                {
                    "points": list(uv),
                    "open": False,
                    "is_hole": (i != outer_idx),
                }
            )

    return loops_dicts


def _order_points_by_connectivity(points):
    """Order points by spatial connectivity (simple greedy approach).

    Args:
        points: List of (u, v) or (u, v, w) points

    Returns:
        Ordered list of points forming a closed loop
    """
    if len(points) < 3:
        return []

    # Remove duplicates (use UV for comparison, preserve W if present)
    unique_points = []
    seen = set()
    for p in points:
        key = (round(p[0], 6), round(p[1], 6))
        if key not in seen:
            seen.add(key)
            unique_points.append(p)

    if len(unique_points) < 3:
        return []

    # Greedy nearest-neighbor ordering (use UV distance only)
    ordered = [unique_points[0]]
    remaining = set(range(1, len(unique_points)))

    while remaining:
        current = ordered[-1]
        best_idx = None
        best_dist = float('inf')

        for idx in remaining:
            pt = unique_points[idx]
            # Distance in UV plane only
            dist = (pt[0] - current[0]) ** 2 + (pt[1] - current[1]) ** 2

            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx is not None:
            ordered.append(unique_points[best_idx])
            remaining.remove(best_idx)
        else:
            break

    # Close the loop
    if len(ordered) >= 3:
        if ordered[0] != ordered[-1]:
            ordered.append(ordered[0])

    return ordered


def _iter_solids(geom):
    """Iterate all solids in geometry (recursively).

    Args:
        geom: Geometry object

    Yields:
        Solid objects
    """
    try:
        from Autodesk.Revit.DB import Solid, GeometryInstance
    except Exception:
        return

    if geom is None:
        return

    try:
        for obj in geom:
            if obj is None:
                continue

            try:
                if isinstance(obj, GeometryInstance):
                    inst = obj.GetInstanceGeometry()
                    for s in _iter_solids(inst):
                        yield s
                    continue
            except Exception:
                pass

            try:
                if isinstance(obj, Solid) and getattr(obj, 'Volume', 0) > 1e-9:
                    yield obj
            except Exception:
                continue
    except Exception:
        return


def _convex_hull_2d(points):
    """Compute 2D convex hull using Andrew's monotone chain algorithm.

    Args:
        points: List of (u, v) tuples

    Returns:
        List of (u, v) points forming convex hull (closed loop)
    """
    if len(points) < 3:
        return []

    try:
        # Remove duplicates and sort
        pts = sorted(set((float(p[0]), float(p[1])) for p in points))

        if len(pts) < 3:
            return []

        def cross(o, a, b):
            """2D cross product."""
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        # Build lower hull
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)

        # Build upper hull
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)

        # Concatenate and close
        hull = lower[:-1] + upper[:-1]

        if len(hull) >= 3:
            # Close the loop
            if hull[0] != hull[-1]:
                hull.append(hull[0])
            return hull
        else:
            return []

    except Exception:
        return []
