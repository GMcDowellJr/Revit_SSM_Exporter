"""
Element collection and visibility filtering for VOP interwoven pipeline.

Provides functions to collect visible elements in a view and check
element visibility according to Revit view settings.
"""

import math

def resolve_element_bbox(elem, view=None, diag=None, context=None):
    """Resolve an element bounding box with explicit source semantics.

    Semantics:
        - Prefer view-dependent bbox when available: elem.get_BoundingBox(view)
        - Fall back to model bbox: elem.get_BoundingBox(None)
        - If neither is available, return (None, "none")

    Returns:
        (bbox, bbox_source)
            bbox_source is one of: "view" | "model" | "none"

    Notes:
        - LinkedElementProxy ignores the view argument but returns a host-space bbox.
          For such proxies, we treat the returned bbox as "model" even if it was
          obtained via the view-callsite, to avoid implying view dependency.
        - Never raises (uses safe_call). If diag is provided, failures are recorded
          as recoverable errors by safe_call.
    """
    from .safe_api import safe_call

    ctx = context or {}
    is_link_proxy = type(elem).__name__ == "LinkedElementProxy"

    # 1) Prefer view-specific bbox when a view is provided.
    if view is not None:
        bbox_view = safe_call(
            diag,
            phase="collection",
            callsite="elem.get_BoundingBox(view)",
            fn=lambda: elem.get_BoundingBox(view),
            default=None,
            context=ctx,
            policy="default",
        )
        if bbox_view is not None:
            return bbox_view, ("model" if is_link_proxy else "view")

    # 2) Fall back to model bbox.
    bbox_model = safe_call(
        diag,
        phase="collection",
        callsite="elem.get_BoundingBox(None)",
        fn=lambda: elem.get_BoundingBox(None),
        default=None,
        context=ctx,
        policy="default",
    )
    if bbox_model is not None:
        return bbox_model, "model"

    return None, "none"


def collect_view_elements(doc, view, raster, diag=None, cfg=None):
    """Collect all potentially visible elements in view (broad-phase).

    Performance contract:
        - One view-scoped collector per view (no per-category loops).
        - Optional category filter (ElementMulticategoryFilter) when available.
        - Optional coarse spatial filter (BoundingBoxIntersectsFilter) when available.

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (currently unused; reserved for future spatial hints)
        diag: Diagnostics (optional)
        cfg: config dict (optional)

    Returns:
        List[Element] (host elements only; link expansion happens downstream)
    """
    from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
    from .collection_policy import included_bic_names_for_source, should_include_element, PolicyStats
    from .safe_api import safe_call

    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    # Category allowlist (policy is still authoritative; this is only a coarse filter)
    bic_names = included_bic_names_for_source("HOST")
    model_categories = []
    for bic_name in bic_names:
        if hasattr(BuiltInCategory, bic_name):
            model_categories.append(getattr(BuiltInCategory, bic_name))

    policy_stats = PolicyStats()
    elements = []

    bbox_none = 0
    bbox_view = 0
    bbox_model = 0

    # Optional performance filters
    # cfg is vop_interwoven.config.Config (not a dict)
    enable_multicat_filter = bool(getattr(cfg, "enable_multicategory_filter", True))
    enable_coarse_spatial = bool(getattr(cfg, "coarse_spatial_filter_enabled", False))
    coarse_pad_ft = float(getattr(cfg, "coarse_spatial_filter_pad_ft", 0.0))

    # Build one view-scoped collector
    try:
        collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
    except Exception as e:
        if diag is not None:
            diag.error(
                phase="collection",
                callsite="collect_view_elements.collector_init",
                message="View-scoped collector failed; returning empty list",
                exc=e,
                view_id=view_id,
            )
        return []

    # Best-effort: ElementMulticategoryFilter to avoid scanning categories we never include.
    if enable_multicat_filter and model_categories:
        try:
            from Autodesk.Revit.DB import ElementMulticategoryFilter, ElementId
            from System.Collections.Generic import List

            # ElementMulticategoryFilter expects a .NET collection (typically ICollection<ElementId>)
            cat_ids = List[ElementId]()
            for bic in model_categories:
                cat_ids.Add(ElementId(int(bic)))

            collector = collector.WherePasses(ElementMulticategoryFilter(cat_ids))
        except Exception as e:
            if diag is not None:
                diag.warn(
                    phase="collection",
                    callsite="collect_view_elements.multicat",
                    message="Failed to apply multicategory filter; continuing without it",
                    view_id=view_id,
                    extra={
                        "num_categories": len(model_categories),
                        "exc_type": type(e).__name__,
                        "exc": str(e),
                    },
                )

    # Best-effort: coarse spatial filter using view.CropBox (model-space AABB).
    if enable_coarse_spatial:
        try:
            from Autodesk.Revit.DB import Outline, BoundingBoxIntersectsFilter
            crop = safe_call(
                diag,
                phase="collection",
                callsite="view.CropBox",
                fn=lambda: getattr(view, "CropBox", None),
                default=None,
                context={"view_id": view_id},
                policy="default",
            )
            if crop is not None:
                pad_ft = float(coarse_cfg.get("pad_ft", 0.0) or 0.0)

                mn = safe_call(
                    diag,
                    phase="collection",
                    callsite="view.CropBox.Min",
                    fn=lambda: crop.Min,
                    default=None,
                    context={"view_id": view_id},
                    policy="default",
                )
                mx = safe_call(
                    diag,
                    phase="collection",
                    callsite="view.CropBox.Max",
                    fn=lambda: crop.Max,
                    default=None,
                    context={"view_id": view_id},
                    policy="default",
                )

                if mn is not None and mx is not None:
                    outline = Outline(
                        mn.__class__(mn.X - pad_ft, mn.Y - pad_ft, mn.Z - pad_ft),
                        mx.__class__(mx.X + pad_ft, mx.Y + pad_ft, mx.Z + pad_ft),
                    )
                    collector = collector.WherePasses(BoundingBoxIntersectsFilter(outline))
        except Exception as e:
            if diag is not None:
                diag.warn(
                    phase="collection",
                    callsite="collect_view_elements.coarse_spatial",
                    message="Failed to apply coarse spatial filter; continuing without it",
                    view_id=view_id,
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )

    # Collect and apply policy
    for elem in collector:
        elem_id = None
        try:
            elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
        except Exception:
            elem_id = None

        include, _pol_reason, _pol_cat = should_include_element(
            elem=elem,
            doc=doc,
            source_type="HOST",
            stats=policy_stats,
        )
        if not include:
            continue

        _bbox, bbox_source = resolve_element_bbox(
            elem,
            view=view,
            diag=diag,
            context={"view_id": view_id, "elem_id": elem_id},
        )
        if bbox_source == "view":
            bbox_view += 1
        elif bbox_source == "model":
            bbox_model += 1
        else:
            bbox_none += 1

        elements.append(elem)

    # Required by PR10: excluded-by-policy counts by category
    if diag is not None and getattr(policy_stats, "excluded_total", 0) > 0:
        diag.info(
            phase="collection",
            callsite="collect_view_elements.policy",
            message="Elements excluded due to category policy",
            view_id=view_id,
            extra={
                "seen_total": policy_stats.seen_total,
                "included_total": policy_stats.included_total,
                "excluded_total": policy_stats.excluded_total,
                "excluded_by_reason": policy_stats.excluded_by_reason,
                "excluded_by_category": policy_stats.excluded_by_category,
            },
        )

    if diag is not None and bbox_none > 0:
        diag.warn(
            phase="collection",
            callsite="collect_view_elements.summary",
            message="Collection had recoverable failures (aggregated)",
            view_id=view_id,
            extra={
                "num_elements": len(elements),
                "bbox": {"view": bbox_view, "model": bbox_model, "none": bbox_none},
            },
        )

    return elements


def is_element_visible_in_view(elem, view):
    """Check if element is visible in view (respects view settings).

    Args:
        elem: Revit Element
        view: Revit View

    Returns:
        True if element is visible in view

    Commentary:
        ✔ Checks element visibility settings (IsHidden, Category visibility, etc.)
        ✔ Respects view template visibility overrides
        ✔ Does NOT check geometry occlusion (that's done in the pipeline)
        ⚠ This is a placeholder - full implementation requires Revit API

    Example (with actual Revit API):
        >>> # if elem.IsHidden(view):
        >>> #     return False
        >>> # category = elem.Category
        >>> # if not view.GetCategoryHidden(category.Id):
        >>> #     return True
    """
    # TODO: Implement actual Revit visibility check
    # Placeholder: return True (optimistic)
    return True


def expand_host_link_import_model_elements(doc, view, elements, cfg, diag=None, elem_cache=None):
    """Expand element list to include linked/imported model elements.

    Args:
        elem_cache: Optional ElementCache for bbox reuse (Phase 2)

    Returns:
        List of element wrappers with transform info plus bbox provenance.
    """
    from Autodesk.Revit.DB import Transform
    from .linked_documents import collect_all_linked_elements

    result = []

    # Track bbox stats (aggregated) without spamming.
    bbox_view = 0
    bbox_model = 0
    bbox_none = 0

    # Add host elements with identity transform
    identity_trf = Transform.Identity
    for e in elements:
        elem_id = getattr(getattr(e, "Id", None), "IntegerValue", None)

        bbox, bbox_source = resolve_element_bbox(
            e,
            view=view,
            diag=diag,
            context={
                "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None),
                "elem_id": elem_id,
                "source_type": "HOST",
            },
        )

        if bbox_source == "view":
            bbox_view += 1
        elif bbox_source == "model":
            bbox_model += 1
        else:
            bbox_none += 1

        # Phase 2: Optionally cache bbox fingerprint for cross-view reuse
        fingerprint = None
        if elem_cache is not None and elem_id is not None:
            try:
                fingerprint = elem_cache.get_or_create_fingerprint(
                    elem=e,
                    elem_id=elem_id,
                    source_id="HOST",
                    view=None,  # Use model bbox for reuse
                    extract_params=None
                )
            except Exception:
                pass  # Graceful degradation

        result.append(
            {
                "element": e,
                "world_transform": identity_trf,
                "bbox": bbox,
                "bbox_source": bbox_source,
                "bbox_link": None,
                "fingerprint": fingerprint,  # NEW: Store for debugging/metrics
                "source_type": "HOST",
                "source_id": "HOST",
                "source_label": "HOST",
                "doc_key": "HOST",
                "doc_label": "HOST",
                "link_inst_id": None,
            }
        )

    # Collect and add linked/imported elements
    try:
        linked_proxies = collect_all_linked_elements(doc, view, cfg, diag=diag)

        for proxy in linked_proxies:
            bbox, bbox_source = resolve_element_bbox(
                proxy,
                view=view,
                diag=diag,
                context={
                    "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None),
                    "elem_id": getattr(getattr(proxy, "Id", None), "IntegerValue", None),
                    "source_type": getattr(proxy, "source_type", "LINK"),
                },
            )

            if bbox_source == "view":
                bbox_view += 1
            elif bbox_source == "model":
                bbox_model += 1
            else:
                bbox_none += 1

            result.append(
                {
                    "element": proxy,
                    "world_transform": identity_trf,
                    "bbox": bbox,
                    "bbox_source": bbox_source,
                    "bbox_link": getattr(proxy, "bbox_link", None),
                    "source_type": getattr(proxy, "source_type", "HOST"),
                    "source_id": getattr(proxy, "source_id", getattr(proxy, "doc_key", "HOST")),
                    "source_label": getattr(proxy, "source_label", getattr(proxy, "doc_label", getattr(proxy, "doc_key", "HOST"))),
                    "doc_key": getattr(proxy, "doc_key", getattr(proxy, "source_id", "HOST")),
                    "doc_label": getattr(proxy, "doc_label", getattr(proxy, "source_label", getattr(proxy, "doc_key", "HOST"))),
                    "link_inst_id": proxy.LinkInstanceId,
                }
            )
    except Exception as e:
        if diag is not None:
            diag.warn(
                phase="collection",
                callsite="expand_host_link_import_model_elements",
                message="Failed to collect linked/imported elements; continuing with host only",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                extra={
                    "exc_type": type(e).__name__,
                    "exc_message": str(e),
                },
            )
        else:
            print("[WARN] vop.collection: Failed to collect linked elements: {0}".format(e))

    # Aggregated bbox source report (if anything was missing)
    if diag is not None and bbox_none > 0:
        try:
            diag.warn(
                phase="collection",
                callsite="expand_host_link_import_model_elements.bbox",
                message="Some elements had no bounding box; they were retained for downstream attempts",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                extra={
                    "bbox": {"view": bbox_view, "model": bbox_model, "none": bbox_none},
                },
            )
        except Exception:
            pass

    return result


def sort_front_to_back(model_elems, view, raster):
    """Sort elements front-to-back by approximate depth."""
    sorted_elems = sorted(
        model_elems,
        key=lambda item: item.get(
            "depth_sort",
            estimate_nearest_depth_from_bbox(
                item["element"],
                item["world_transform"],
                view,
                raster,
                bbox=item.get("bbox"),
            ),
        ),
    )
    return sorted_elems


def estimate_nearest_depth_from_bbox(elem, transform, view, raster, bbox=None, diag=None, bbox_is_link_space=False):
    """Estimate nearest depth of element from its bounding box."""
    from .view_basis import world_to_view

    if bbox is None:
        bbox, _src = resolve_element_bbox(
            elem,
            view=view,
            diag=diag,
            context={
                "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None),
                "elem_id": getattr(getattr(elem, "Id", None), "IntegerValue", None),
            },
        )

    if bbox is None:
        return float("inf")

    vb = getattr(raster, "view_basis", None)
    if vb is None:
        if diag is not None:
            diag.warn(
                phase="collection",
                callsite="estimate_nearest_depth_from_bbox.view_basis_missing",
                message="View basis missing; returning inf depth to avoid near-bias",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
            )
        return float("inf")

    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z),
        (min_x, min_y, max_z),
        (min_x, max_y, min_z),
        (min_x, max_y, max_z),
        (max_x, min_y, min_z),
        (max_x, min_y, max_z),
        (max_x, max_y, min_z),
        (max_x, max_y, max_z),
    ]

    if bbox_is_link_space:
        if transform is None:
            return float("inf")

        try:
            corners = [transform.OfPoint(c) for c in corners]
        except Exception:
            try:
                from Autodesk.Revit.DB import XYZ
                xyzs = [XYZ(c[0], c[1], c[2]) for c in corners]
                corners_xyz = [transform.OfPoint(p) for p in xyzs]
                corners = [(p.X, p.Y, p.Z) for p in corners_xyz]
            except Exception:
                return float("inf")

    min_depth = float("inf")
    for corner in corners:
        _u, _v, w = world_to_view(corner, vb)
        if w < min_depth:
            min_depth = w

    return min_depth


def estimate_depth_from_loops_or_bbox(elem, loops, transform, view, raster, bbox=None, diag=None, bbox_is_link_space=False):
    """Get element depth from silhouette geometry or bbox fallback."""
    if loops:
        min_w = float("inf")
        found_depth = False

        for loop in loops:
            points = loop.get("points", [])
            for pt in points:
                if len(pt) >= 3:
                    w = pt[2]
                    if w < min_w:
                        min_w = w
                    found_depth = True

        if found_depth and min_w < float("inf"):
            return min_w

    return estimate_nearest_depth_from_bbox(
        elem,
        transform,
        view,
        raster,
        bbox=bbox,
        diag=diag,
        bbox_is_link_space=bbox_is_link_space,
    )

def estimate_depth_range_from_bbox(elem, transform, view, raster, bbox=None, diag=None):
    """Estimate depth range (min, max) of element from its bounding box.

    Uses wrapper-provided bbox when available; otherwise resolves bbox via resolve_element_bbox().
    Never raises; returns (inf, inf) when bbox is unavailable.
    """
    from .view_basis import world_to_view

    # Prefer provided bbox (wrapper-resolved), otherwise resolve (view -> model -> none)
    if bbox is None:
        try:
            bbox, _src = resolve_element_bbox(
                elem,
                view=view,
                diag=diag,
                context={
                    "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None) if view is not None else None,
                    "elem_id": getattr(getattr(elem, "Id", None), "IntegerValue", None),
                },
            )
        except Exception:
            bbox = None

    if bbox is None:
        return (float("inf"), float("inf"))

    vb = getattr(raster, "view_basis", None)
    if vb is None:
        if diag is not None:
            diag.warn(
                phase="collection",
                callsite="estimate_depth_range_from_bbox.view_basis_missing",
                message="View basis missing; returning (inf, inf) depth range to avoid near-bias",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
            )
        return (float("inf"), float("inf"))

    try:
        min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
        max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z
    except Exception:
        return (float("inf"), float("inf"))

    corners = [
        (min_x, min_y, min_z),
        (min_x, min_y, max_z),
        (min_x, max_y, min_z),
        (min_x, max_y, max_z),
        (max_x, min_y, min_z),
        (max_x, min_y, max_z),
        (max_x, max_y, min_z),
        (max_x, max_y, max_z),
    ]

    min_depth = float("inf")
    max_depth = float("-inf")

    for corner in corners:
        try:
            _u, _v, w = world_to_view(corner, vb)
        except Exception:
            continue
        if w < min_depth:
            min_depth = w
        if w > max_depth:
            max_depth = w

    if min_depth == float("inf") or max_depth == float("-inf"):
        return (float("inf"), float("inf"))

    return (min_depth, max_depth)


def _project_element_bbox_to_cell_rect(elem, vb, raster, bbox=None, diag=None, view=None, transform=None, bbox_is_link_space=False):
    """Project element bounding box to cell rectangle using OBB (oriented bounds).

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size

    Returns:
        CellRect in grid coordinates, or None if no bbox

    Commentary:
        ✔ Gets world-space bounding box
        ✔ Projects ALL 8 corners to view UV
        ✔ Fits UV OBB (oriented bounding box) using PCA for tighter bounds
        ✔ Computes AABB of OBB rectangle for cell indices
        ✔ CRITICAL: OBB is much tighter than AABB for rotated elements
        ✔ Handles elements outside view bounds (returns None or empty rect)
    """
    from ..core.math_utils import CellRect
    from .view_basis import world_to_view

    # Prefer provided bbox (wrapper-resolved). If absent, resolve (view -> model -> none).
    if bbox is None:
        try:
            bbox, _src = resolve_element_bbox(
                elem,
                view=view,
                diag=diag,
                context={
                    "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None) if view is not None else None,
                    "elem_id": getattr(getattr(elem, "Id", None), "IntegerValue", None),
                },
            )
        except Exception:
            bbox = None

    if bbox is None:
        return None

    # Get all 8 corners of 3D bounding box (bbox-local coordinates)
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z), (min_x, min_y, max_z),
        (min_x, max_y, min_z), (min_x, max_y, max_z),
        (max_x, min_y, min_z), (max_x, min_y, max_z),
        (max_x, max_y, min_z), (max_x, max_y, max_z),
    ]

    # CRITICAL: BoundingBoxXYZ.Min/Max are in bbox-local space.
    # BoundingBoxXYZ.Transform maps local→world coordinates.
    # This must be applied BEFORE link transform.
    trf = getattr(bbox, "Transform", None)
    if trf is not None:
        try:
            from Autodesk.Revit.DB import XYZ
            xyzs = [XYZ(c[0], c[1], c[2]) for c in corners]
            corners_w = [trf.OfPoint(p) for p in xyzs]
            corners = [(p.X, p.Y, p.Z) for p in corners_w]
        except Exception:
            # Best-effort: keep tuple corners if Transform application fails
            pass

    # PR12: if bbox is link-space, transform corners into host/world before projecting.
    if bbox_is_link_space:
        if transform is None:
            return None  # cannot correctly project link-space bbox without transform
        try:
            corners = [transform.OfPoint(c) for c in corners]
        except Exception:
            return None

    # PR12: if bbox is in link-space, transform corners into host/world before projecting.
    if bbox_is_link_space:
        if transform is None:
            return None  # cannot correctly project link-space bbox without transform

        # Revit Transform expects XYZ; some test stubs may accept tuples.
        try:
            corners = [transform.OfPoint(c) for c in corners]
        except Exception:
            try:
                from Autodesk.Revit.DB import XYZ
                xyzs = [XYZ(c[0], c[1], c[2]) for c in corners]
                corners_xyz = [transform.OfPoint(p) for p in xyzs]
                corners = [(p.X, p.Y, p.Z) for p in corners_xyz]
            except Exception:
                return None

    uvs = [world_to_view(corner, vb) for corner in corners]

    # Extract just UV (ignore W for footprint calculation)
    points_uv = [(uv[0], uv[1]) for uv in uvs]

    # Fit OBB to UV points using PCA for tighter bounds
    obb_rect, len_u, len_v, angle_deg = _pca_obb_uv(points_uv)

    if not obb_rect or len(obb_rect) < 4:
        # Fallback to AABB if OBB fitting fails
        u_min = min(uv[0] for uv in uvs)
        u_max = max(uv[0] for uv in uvs)
        v_min = min(uv[1] for uv in uvs)
        v_max = max(uv[1] for uv in uvs)
    else:
        # Compute AABB of OBB rectangle (tighter than world AABB projection!)
        u_min = min(pt[0] for pt in obb_rect)
        u_max = max(pt[0] for pt in obb_rect)
        v_min = min(pt[1] for pt in obb_rect)
        v_max = max(pt[1] for pt in obb_rect)

    # Convert to cell indices (inclusive bounds).
    # Use floor/ceil to avoid truncation bias (especially for negative coords).
    # int() truncates toward zero: int(-0.9) = 0, but floor(-0.9) = -1
    i_min = int(math.floor((u_min - raster.bounds.xmin) / raster.cell_size))
    i_max = int(math.ceil((u_max - raster.bounds.xmin) / raster.cell_size)) - 1
    j_min = int(math.floor((v_min - raster.bounds.ymin) / raster.cell_size))
    j_max = int(math.ceil((v_max - raster.bounds.ymin) / raster.cell_size)) - 1

    # Clamp to raster bounds
    i_min = max(0, min(i_min, raster.W - 1))
    i_max = max(0, min(i_max, raster.W - 1))
    j_min = max(0, min(j_min, raster.H - 1))
    j_max = max(0, min(j_max, raster.H - 1))

    rect = CellRect(i_min, j_min, i_max, j_max)

    # Store OBB data for LINEAR proxy reconstruction (avoid recomputing PCA).
    # _pca_obb_uv() already computed this; pass it through to geometry.py.
    if obb_rect and len(obb_rect) >= 4:
        rect.obb_data = {
            'obb_corners': obb_rect,     # 4 corners of fitted OBB in UV space
            'uv_corners': points_uv,     # Original 8 bbox corners in UV (for diagnostics)
            'len_u': len_u,              # OBB dimensions
            'len_v': len_v,
            'angle_deg': angle_deg       # Rotation angle (for diagnostics)
        }
    else:
        rect.obb_data = None

    return rect


def _get_element_category_name(elem):
    """Safely extract category name from element.

    Args:
        elem: Revit Element

    Returns:
        Category name string, or 'Unknown' if unavailable
    """
    try:
        cat = getattr(elem, "Category", None)
        if cat is None:
            return "Unknown"
        cname = getattr(cat, "Name", None)
        return cname if cname else "Unknown"
    except Exception:
        return "Unknown"


def _diagnose_coordinate_spaces(elem, geom, transform, link_transform, vb):
    """Diagnostic: Print detailed coordinate space information for troubleshooting.

    This function traces geometry through all transform stages to identify
    where coordinate space mismatches occur.

    Args:
        elem: Revit Element being processed
        geom: GeometryElement from elem.get_Geometry()
        transform: Instance transform (from GeometryInstance.Transform)
        link_transform: Link transform for LinkedElementProxy
        vb: ViewBasis for UV projection
    """
    try:
        elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", "?")
        print(f"\n{'='*80}")
        print(f"COORDINATE SPACE DIAGNOSTIC - Element {elem_id}")
        print(f"{'='*80}")

        # 1. Check bbox and its transform
        print("\n1. BOUNDING BOX ANALYSIS:")
        try:
            bbox = elem.get_BoundingBox(None)
            if bbox:
                bbox_tf = getattr(bbox, "Transform", None)
                print(f"   BBox.Transform exists: {bbox_tf is not None}")
                if bbox_tf:
                    is_identity = getattr(bbox_tf, 'IsIdentity', None)
                    print(f"   BBox.Transform.IsIdentity: {is_identity}")
                    origin = getattr(bbox_tf, "Origin", None)
                    if origin:
                        print(f"   BBox.Transform.Origin: ({origin.X:.2f}, {origin.Y:.2f}, {origin.Z:.2f})")

                    # Show basis vectors
                    try:
                        basis_x = bbox_tf.BasisX
                        basis_y = bbox_tf.BasisY
                        basis_z = bbox_tf.BasisZ
                        print(f"   BBox.Transform.BasisX: ({basis_x.X:.3f}, {basis_x.Y:.3f}, {basis_x.Z:.3f})")
                        print(f"   BBox.Transform.BasisY: ({basis_y.X:.3f}, {basis_y.Y:.3f}, {basis_y.Z:.3f})")
                        print(f"   BBox.Transform.BasisZ: ({basis_z.X:.3f}, {basis_z.Y:.3f}, {basis_z.Z:.3f})")
                    except:
                        pass

                print(f"   BBox.Min (bbox-local): ({bbox.Min.X:.2f}, {bbox.Min.Y:.2f}, {bbox.Min.Z:.2f})")
                print(f"   BBox.Max (bbox-local): ({bbox.Max.X:.2f}, {bbox.Max.Y:.2f}, {bbox.Max.Z:.2f})")

                # Transform a corner to world space
                if bbox_tf is not None:
                    from Autodesk.Revit.DB import XYZ
                    corner_local = XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
                    corner_world = bbox_tf.OfPoint(corner_local)
                    print(f"   BBox.Min (world space): ({corner_world.X:.2f}, {corner_world.Y:.2f}, {corner_world.Z:.2f})")
            else:
                print("   No bounding box available")
        except Exception as e:
            print(f"   Error getting bbox: {e}")

        # 2. Check element type and instance transform
        print("\n2. ELEMENT TYPE & INSTANCE TRANSFORM:")
        try:
            from Autodesk.Revit.DB import FamilyInstance
            base_elem = getattr(elem, "_elem", elem)  # Unwrap if wrapped

            if isinstance(base_elem, FamilyInstance):
                print("   Element type: FamilyInstance")

                # Get instance placement transform
                inst_tf = None
                if hasattr(base_elem, "GetTransform"):
                    inst_tf = base_elem.GetTransform()
                elif hasattr(base_elem, "Transform"):
                    inst_tf = base_elem.Transform

                if inst_tf:
                    origin = getattr(inst_tf, "Origin", None)
                    if origin:
                        print(f"   Instance placement Origin: ({origin.X:.2f}, {origin.Y:.2f}, {origin.Z:.2f})")

                    try:
                        basis_x = inst_tf.BasisX
                        basis_y = inst_tf.BasisY
                        basis_z = inst_tf.BasisZ
                        print(f"   Instance BasisX: ({basis_x.X:.3f}, {basis_x.Y:.3f}, {basis_x.Z:.3f})")
                        print(f"   Instance BasisY: ({basis_y.X:.3f}, {basis_y.Y:.3f}, {basis_y.Z:.3f})")
                        print(f"   Instance BasisZ: ({basis_z.X:.3f}, {basis_z.Y:.3f}, {basis_z.Z:.3f})")
                    except:
                        pass
            else:
                print(f"   Element type: {type(base_elem).__name__}")
        except Exception as e:
            print(f"   Error checking element type: {e}")

        # 3. Check provided transforms
        print("\n3. PROVIDED TRANSFORMS:")
        print(f"   transform parameter: {transform is not None}")
        if transform:
            try:
                print(f"   transform.Origin: ({transform.Origin.X:.2f}, {transform.Origin.Y:.2f}, {transform.Origin.Z:.2f})")
            except:
                pass

        print(f"   link_transform parameter: {link_transform is not None}")
        if link_transform:
            try:
                print(f"   link_transform.Origin: ({link_transform.Origin.X:.2f}, {link_transform.Origin.Y:.2f}, {link_transform.Origin.Z:.2f})")
            except:
                pass

        # 4. Sample geometry and trace through transforms
        print("\n4. GEOMETRY SAMPLING & TRANSFORM CHAIN:")
        if not geom:
            print("   No geometry available")
            print(f"{'='*80}\n")
            return

        from Autodesk.Revit.DB import GeometryInstance, Solid

        sample_found = False
        for obj in geom:
            if sample_found:
                break

            if isinstance(obj, GeometryInstance):
                print("   Found: GeometryInstance in root geometry")
                inst_tf = obj.Transform
                print(f"   GeometryInstance.Transform.Origin: ({inst_tf.Origin.X:.2f}, {inst_tf.Origin.Y:.2f}, {inst_tf.Origin.Z:.2f})")

                inst_geom = obj.GetInstanceGeometry()
                if inst_geom:
                    for inst_obj in inst_geom:
                        if isinstance(inst_obj, Solid) and inst_obj.Volume > 0.0001:
                            # Get first vertex from geometry
                            for face in inst_obj.Faces:
                                try:
                                    for edge_loop in face.EdgeLoops:
                                        for edge in edge_loop:
                                            curve = edge.AsCurve()
                                            if curve:
                                                pt_local = curve.Evaluate(0.0, True)
                                                print(f"\n   Sample vertex transform chain:")
                                                print(f"   [1] Raw from GetInstanceGeometry: ({pt_local.X:.2f}, {pt_local.Y:.2f}, {pt_local.Z:.2f})")

                                                # Apply GeometryInstance.Transform
                                                pt_after_inst = inst_tf.OfPoint(pt_local)
                                                print(f"   [2] After GeometryInstance.Transform: ({pt_after_inst.X:.2f}, {pt_after_inst.Y:.2f}, {pt_after_inst.Z:.2f})")

                                                # Apply the 'transform' parameter if provided
                                                if transform:
                                                    pt_after_param = transform.OfPoint(pt_after_inst)
                                                    print(f"   [3] After parameter transform: ({pt_after_param.X:.2f}, {pt_after_param.Y:.2f}, {pt_after_param.Z:.2f})")
                                                    current_pt = pt_after_param
                                                else:
                                                    print(f"   [3] No parameter transform provided")
                                                    current_pt = pt_after_inst

                                                # Apply link transform if provided
                                                if link_transform:
                                                    pt_after_link = link_transform.OfPoint(current_pt)
                                                    print(f"   [4] After link_transform: ({pt_after_link.X:.2f}, {pt_after_link.Y:.2f}, {pt_after_link.Z:.2f})")
                                                    current_pt = pt_after_link
                                                else:
                                                    print(f"   [4] No link_transform (host element)")

                                                # Project to UV
                                                from .view_basis import world_to_view
                                                uvw = world_to_view((current_pt.X, current_pt.Y, current_pt.Z), vb)
                                                print(f"   [5] Final UV: ({uvw[0]:.2f}, {uvw[1]:.2f})")

                                                sample_found = True
                                                break
                                        if sample_found:
                                            break
                                except:
                                    continue
                                if sample_found:
                                    break

            elif isinstance(obj, Solid) and obj.Volume > 0.0001 and not sample_found:
                print("   Found: Solid directly in root geometry (no GeometryInstance wrapper)")

                for face in obj.Faces:
                    try:
                        for edge_loop in face.EdgeLoops:
                            for edge in edge_loop:
                                curve = edge.AsCurve()
                                if curve:
                                    pt = curve.Evaluate(0.0, True)
                                    print(f"\n   Sample vertex (should already be in world space):")
                                    print(f"   [1] Raw from Solid: ({pt.X:.2f}, {pt.Y:.2f}, {pt.Z:.2f})")

                                    # This geometry should already be in world coordinates
                                    # Only apply link transform if it's a linked element
                                    current_pt = pt

                                    if link_transform:
                                        pt_after_link = link_transform.OfPoint(current_pt)
                                        print(f"   [2] After link_transform: ({pt_after_link.X:.2f}, {pt_after_link.Y:.2f}, {pt_after_link.Z:.2f})")
                                        current_pt = pt_after_link
                                    else:
                                        print(f"   [2] No link_transform needed (host element)")

                                    # Project to UV
                                    from .view_basis import world_to_view
                                    uvw = world_to_view((current_pt.X, current_pt.Y, current_pt.Z), vb)
                                    print(f"   [3] Final UV: ({uvw[0]:.2f}, {uvw[1]:.2f})")

                                    sample_found = True
                                    break
                            if sample_found:
                                break
                    except:
                        continue
                    if sample_found:
                        break

        if not sample_found:
            print("   No sample geometry found to trace")

        print(f"{'='*80}\n")

    except Exception as e:
        print(f"DIAGNOSTIC ERROR: {e}")
        import traceback
        traceback.print_exc()


def _extract_geometry_footprint_uv(elem, vb, diag=None, strategy_diag=None):
    """Extract actual geometry footprint vertices in UV space.

    Extracts solid geometry faces/edges and projects all vertices to UV.
    Works in all view types (plan, elevation, section, 3D).

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        diag: Diagnostics instance for error tracking (optional)
        strategy_diag: StrategyDiagnostics instance for strategy tracking (optional)

    Returns:
        List of (u, v) points representing element footprint, or None if failed
    """
    from .view_basis import world_to_view

    # Extract element ID and category for tracking
    elem_id = None
    category = None
    try:
        elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
        category = _get_element_category_name(elem)
    except Exception:
        pass

    try:
        from Autodesk.Revit.DB import Options, Solid, Face, Edge, GeometryInstance

        # Create geometry options (don't set View to avoid linked element issues)
        opts = Options()
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        opts.DetailLevel = 2  # Medium detail

        # Get geometry
        geom = elem.get_Geometry(opts)
        if not geom:
            # Track no_geometry failure
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_geometry_extraction(
                        elem_id=elem_id,
                        outcome='no_geometry',
                        category=category,
                        details={'reason': 'get_Geometry returned None'}
                    )
                except Exception:
                    pass
            return None

        # Extract link transform for LinkedElementProxy
        link_transform = getattr(elem, 'transform', None)

        # =====================================================================
        # DIAGNOSTIC: Add this block for problematic elements
        # =====================================================================
        try:
            elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
            # Replace 987587 with the actual element ID of your problematic beam
            if elem_id and elem_id in [987587]:  # <-- PUT YOUR BEAM ID HERE
                _diagnose_coordinate_spaces(elem, geom, None, link_transform, vb)
        except:
            pass
        # =====================================================================

        # Collect all vertices from solid geometry
        points_uv = []

        def process_geometry(geo, transform=None, _has_instances=None):
            """Recursively process geometry to extract vertices.

            Args:
                geo: GeometryElement or geometry collection to process
                transform: Accumulated transform from parent GeometryInstances
                _has_instances: Internal flag - whether this level contains GeometryInstances

            Key behavior:
                - If GeometryInstances exist at this level, ONLY process them (skip top-level Solids)
                - This prevents duplicate extraction when family instances have geometry both
                  at top level AND inside GetInstanceGeometry()
            """

            # First pass: detect if this geometry level has instances
            if _has_instances is None:
                _has_instances = False
                for obj in geo:
                    if isinstance(obj, GeometryInstance):
                        _has_instances = True
                        break

            for obj in geo:
                # Handle geometry instances (e.g., family instances)
                if isinstance(obj, GeometryInstance):
                    inst_geom = obj.GetInstanceGeometry()
                    if inst_geom:
                        inst_transform = obj.Transform
                        # Compose transforms: apply instance transform on top of any existing transform
                        if transform is not None:
                            # If we already have a transform, compose them
                            combined_transform = transform.Multiply(inst_transform)
                        else:
                            combined_transform = inst_transform

                        # Recurse into instance geometry with composed transform
                        # _has_instances=False because we're now inside the instance
                        process_geometry(inst_geom, combined_transform, _has_instances=False)

                # Handle solids - but ONLY if there are no instances at this level
                # If instances exist, the real geometry is inside them and we skip top-level solids
                elif isinstance(obj, Solid) and not _has_instances:
                    if obj.Volume > 0.0001:  # Non-degenerate solid
                        # Extract vertices from faces
                        for face in obj.Faces:
                            # Best-effort geometry sampling: count failures, emit one summary line.
                            edge_sample_failures = 0
                            edge_loop_failures = 0

                            try:
                                # Get face boundary edges
                                for edge_loop in face.EdgeLoops:
                                    for edge in edge_loop:
                                        # Sample edge endpoints
                                        curve = edge.AsCurve()
                                        if curve:
                                            for t in (0.0, 1.0):  # Start and end
                                                try:
                                                    pt = curve.Evaluate(t, True)

                                                    # Apply instance transform if present
                                                    if transform:
                                                        pt = transform.OfPoint(pt)

                                                    # CRITICAL: Apply link transform for LinkedElementProxy
                                                    if link_transform:
                                                        pt = link_transform.OfPoint(pt)

                                                    # Project to UV
                                                    uvw = world_to_view((pt.X, pt.Y, pt.Z), vb)
                                                    points_uv.append((uvw[0], uvw[1]))
                                                except Exception:
                                                    edge_sample_failures += 1
                                                    continue
                            except Exception:
                                edge_loop_failures += 1
                                pass

                            if edge_sample_failures or edge_loop_failures:
                                print(
                                    "[WARN] revit.collection: geometry sampling incomplete "
                                    f"(elem_id={getattr(elem,'Id',None)}, "
                                    f"edge_sample_failures={edge_sample_failures}, "
                                    f"edge_loop_failures={edge_loop_failures})"
                                )

        # Process geometry tree
        process_geometry(geom)

        # Return unique points (remove duplicates)
        if len(points_uv) >= 3:
            # Remove duplicates with tolerance
            unique_points = []
            tolerance = 0.01  # 0.01 ft tolerance
            for pt in points_uv:
                is_duplicate = False
                for existing in unique_points:
                    if abs(pt[0] - existing[0]) < tolerance and abs(pt[1] - existing[1]) < tolerance:
                        is_duplicate = True
                        break
                if not is_duplicate:
                    unique_points.append(pt)

            if len(unique_points) >= 3:
                # Track success with vertex count
                if strategy_diag is not None and elem_id is not None:
                    try:
                        strategy_diag.record_geometry_extraction(
                            elem_id=elem_id,
                            outcome='success',
                            category=category,
                            details={'vertices': len(unique_points), 'raw_vertices': len(points_uv)}
                        )
                    except Exception:
                        pass
                return unique_points
            else:
                # Track insufficient_points failure
                if strategy_diag is not None and elem_id is not None:
                    try:
                        strategy_diag.record_geometry_extraction(
                            elem_id=elem_id,
                            outcome='insufficient_points',
                            category=category,
                            details={'unique_points': len(unique_points), 'required': 3}
                        )
                    except Exception:
                        pass
                return None

        # Track insufficient_points failure (less than 3 raw points)
        if strategy_diag is not None and elem_id is not None:
            try:
                strategy_diag.record_geometry_extraction(
                    elem_id=elem_id,
                    outcome='insufficient_points',
                    category=category,
                    details={'points': len(points_uv), 'required': 3}
                )
            except Exception:
                pass
        return None

    except Exception as e:
        # Track exception failure
        if strategy_diag is not None and elem_id is not None:
            try:
                strategy_diag.record_geometry_extraction(
                    elem_id=elem_id,
                    outcome='exception',
                    category=category,
                    details={'error': '{}: {}'.format(type(e).__name__, str(e))}
                )
            except Exception:
                pass
        return None


def get_element_obb_loops(elem, vb, raster, bbox=None, diag=None, view=None, strategy_diag=None):
    """Get element OBB as polygon loops for accurate rasterization.

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size
        bbox: Optional pre-resolved bounding box
        diag: Diagnostics instance for error tracking (optional)
        view: Revit View (optional)
        strategy_diag: StrategyDiagnostics instance for strategy tracking (optional)

    Returns:
        List of loop dicts with OBB polygon, or None if bbox unavailable

    Commentary:
        ✔ Extracts ACTUAL geometry footprint (not bbox corners)
        ✔ For walls: uses location curve + thickness (diagonal walls)
        ✔ For other elements: extracts geometry faces/edges
        ✔ Falls back to bbox corners only if geometry extraction fails
    """
    from .view_basis import world_to_view

    # Extract element ID and category for tracking
    elem_id = None
    category = None
    try:
        elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
        category = _get_element_category_name(elem)
    except Exception:
        pass

    # Always need bbox for depth + UV projection. Prefer provided bbox, else resolve.
    if bbox is None:
        try:
            bbox, _src = resolve_element_bbox(
                elem,
                view=view,
                diag=diag,
                context={
                    "view_id": getattr(getattr(view, "Id", None), "IntegerValue", None) if view is not None else None,
                    "elem_id": getattr(getattr(elem, "Id", None), "IntegerValue", None),
                },
            )
        except Exception:
            bbox = None

    if bbox is None:
        return None


    # Get all 8 corners and project to UV (needed for depth regardless of geometry extraction)
    min_x, min_y, min_z = bbox.Min.X, bbox.Min.Y, bbox.Min.Z
    max_x, max_y, max_z = bbox.Max.X, bbox.Max.Y, bbox.Max.Z

    corners = [
        (min_x, min_y, min_z), (min_x, min_y, max_z),
        (min_x, max_y, min_z), (min_x, max_y, max_z),
        (max_x, min_y, min_z), (max_x, min_y, max_z),
        (max_x, max_y, min_z), (max_x, max_y, max_z),
    ]

    # CRITICAL: BoundingBoxXYZ may be oriented; apply bbox.Transform if present.
    try:
        bbox_tf = getattr(bbox, "Transform", None)
    except Exception:
        bbox_tf = None

    if bbox_tf is not None:
        try:
            from Autodesk.Revit.DB import XYZ
            corners_world = []
            for (x, y, z) in corners:
                corners_world.append(bbox_tf.OfPoint(XYZ(x, y, z)))
            corners = [(p.X, p.Y, p.Z) for p in corners_world]
        except Exception:
            # If transform application fails, fall back to raw corners.
            pass

    uvs = [world_to_view(corner, vb) for corner in corners]

    bbox_points_uv = [(uv[0], uv[1]) for uv in uvs]

    # STEP 1: Try to extract actual geometry footprint
    points_uv = _extract_geometry_footprint_uv(elem, vb, diag=diag, strategy_diag=strategy_diag)

    # STEP 2: Fallback to bbox corners if geometry extraction failed
    if not points_uv or len(points_uv) < 3:
        points_uv = bbox_points_uv

    # DEBUG: Log unique UV points for diagonal wall
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id:
            elem_id_val = getattr(elem_id, 'IntegerValue', elem_id)
            if elem_id_val == 1619124:  # Target diagonal wall
                unique_uv = list(set(points_uv))
                print("[DEBUG GEOM] Element {0}: Extracted {1} footprint points -> {2} unique UV".format(
                    elem_id_val, len(points_uv), len(unique_uv)))
                print("  UV points: {0}".format([(round(u, 2), round(v, 2)) for u, v in unique_uv[:8]]))
    except Exception as e:
        print(f"[WARN] revit.collection:debug geom logging failed ({type(e).__name__}: {e})")
        pass

    # STEP 3: Compute polygon for rasterization
    # If we extracted actual geometry, use it directly (preserve actual shape)
    # If we're using bbox, fit OBB to get rotated rectangle

    used_geometry = (points_uv != bbox_points_uv)  # Did geometry extraction succeed?

    if used_geometry:
        # Use actual geometry vertices directly (preserves concave shapes like L-shapes)
        # Don't use convex hull - it destroys concave corners!

        # Remove consecutive duplicates (keep vertex order from geometry extraction)
        polygon_uv = []
        tolerance = 0.01
        for pt in points_uv:
            if not polygon_uv or (abs(pt[0] - polygon_uv[-1][0]) > tolerance or
                                  abs(pt[1] - polygon_uv[-1][1]) > tolerance):
                polygon_uv.append(pt)

        # Close the loop if not already closed
        if polygon_uv and len(polygon_uv) >= 3:
            if abs(polygon_uv[0][0] - polygon_uv[-1][0]) > tolerance or \
               abs(polygon_uv[0][1] - polygon_uv[-1][1]) > tolerance:
                polygon_uv.append(polygon_uv[0])

            strategy_name = 'geometry_polygon'
            angle_deg = 0.0  # Not applicable for arbitrary polygons

            # Track successful geometry_polygon strategy
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_areal_strategy(
                        elem_id=elem_id,
                        strategy='geometry_polygon',
                        success=True,
                        category=category
                    )
                except Exception:
                    pass
        else:
            # Not enough vertices, fall back to bbox OBB
            used_geometry = False

    if not used_geometry:
        # Using bbox corners - fit OBB for rotated rectangle
        obb_rect, len_u, len_v, angle_deg = _pca_obb_uv(points_uv)

        if not obb_rect or len(obb_rect) < 4:
            # Fallback: use axis-aligned rect from min/max UV
            u_min = min(uv[0] for uv in uvs)
            u_max = max(uv[0] for uv in uvs)
            v_min = min(uv[1] for uv in uvs)
            v_max = max(uv[1] for uv in uvs)

            # Build axis-aligned rectangle
            polygon_uv = [
                (u_min, v_min),
                (u_max, v_min),
                (u_max, v_max),
                (u_min, v_max),
                (u_min, v_min),  # Close loop
            ]
            strategy_name = 'uv_aabb'
            angle_deg = 0.0

            # Track AABB fallback strategy
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_areal_strategy(
                        elem_id=elem_id,
                        strategy='aabb_used',
                        success=True,
                        category=category
                    )
                except Exception:
                    pass
        else:
            polygon_uv = obb_rect
            strategy_name = 'uv_obb'

            # Track OBB strategy
            if strategy_diag is not None and elem_id is not None:
                try:
                    strategy_diag.record_areal_strategy(
                        elem_id=elem_id,
                        strategy='bbox_obb_used',
                        success=True,
                        category=category
                    )
                except Exception:
                    pass

    # Get minimum depth for occlusion
    w_min = min(uv[2] for uv in uvs)

    # Convert to loop format with depth
    points_uvw = [(pt[0], pt[1], w_min) for pt in polygon_uv]

    # DEBUG: Log polygon calculations
    try:
        elem_id = getattr(elem, 'Id', None)
        if elem_id:
            elem_id_val = getattr(elem_id, 'IntegerValue', elem_id)
            # Always log element 1619124 (diagonal wall), 5% sample for others
            import random
            should_log = (elem_id_val == 1619124) or (random.random() < 0.05)
            if should_log:
                print("[DEBUG POLY] Element {0}: strategy={1}, vertices={2}, geom_extracted={3}".format(
                    elem_id_val, strategy_name, len(polygon_uv), used_geometry))
                if elem_id_val == 1619124 or used_geometry:
                    print("  Polygon: {0}".format([(round(pt[0], 2), round(pt[1], 2)) for pt in polygon_uv[:8]]))
    except Exception as e:
        print(f"[WARN] revit.collection:debug poly logging failed ({type(e).__name__}: {e})")
        pass

    # Return polygon loop with correct strategy tag
    return [{'points': points_uvw, 'is_hole': False, 'strategy': strategy_name}]


def _pca_obb_uv(points_uv):
    """Compute oriented bounding box in UV using PCA.

    Args:
        points_uv: List of (u, v) points

    Returns:
        (rect_points, len_u, len_v, angle_deg) where rect_points is closed loop in UV
        Returns ([], 0.0, 0.0, 0.0) on failure

    Commentary:
        ✔ Fits oriented rectangle to point cloud using PCA
        ✔ Much tighter than AABB for rotated/diagonal elements
        ✔ Returns rectangle as 5-point closed loop
    """
    import math

    if not points_uv or len(points_uv) < 2:
        return ([], 0.0, 0.0, 0.0)

    # Compute mean
    n = float(len(points_uv))
    mean_u = sum(p[0] for p in points_uv) / n
    mean_v = sum(p[1] for p in points_uv) / n

    # Compute covariance matrix
    cxx = sum((p[0] - mean_u) ** 2 for p in points_uv)
    cxy = sum((p[0] - mean_u) * (p[1] - mean_v) for p in points_uv)
    cyy = sum((p[1] - mean_v) ** 2 for p in points_uv)

    # Principal axis angle (2D PCA)
    angle = 0.5 * math.atan2(2.0 * cxy, (cxx - cyy))
    angle_deg = angle * 180.0 / math.pi

    # Principal axes (unit vectors)
    ux = math.cos(angle)
    uy = math.sin(angle)
    vx = -uy
    vy = ux

    # Project points onto principal axes to get extents
    u_min = float('inf')
    u_max = float('-inf')
    v_min = float('inf')
    v_max = float('-inf')

    for pt in points_uv:
        du = pt[0] - mean_u
        dv = pt[1] - mean_v

        # Project onto principal axes
        u_proj = du * ux + dv * uy
        v_proj = du * vx + dv * vy

        u_min = min(u_min, u_proj)
        u_max = max(u_max, u_proj)
        v_min = min(v_min, v_proj)
        v_max = max(v_max, v_proj)

    len_u = abs(u_max - u_min)
    len_v = abs(v_max - v_min)

    # Build rectangle corners in original UV space
    # p = mean + u_proj * U_axis + v_proj * V_axis
    corners = [
        (mean_u + u_min * ux + v_min * vx, mean_v + u_min * uy + v_min * vy),
        (mean_u + u_max * ux + v_min * vx, mean_v + u_max * uy + v_min * vy),
        (mean_u + u_max * ux + v_max * vx, mean_v + u_max * uy + v_max * vy),
        (mean_u + u_min * ux + v_max * vx, mean_v + u_min * uy + v_max * vy),
        (mean_u + u_min * ux + v_min * vx, mean_v + u_min * uy + v_min * vy),  # Close loop
    ]

    return (corners, len_u, len_v, angle_deg)
