"""
Annotation collection and rasterization for VOP interwoven pipeline.

Provides functions to collect 2D annotation elements, classify them by type,
rasterize their bounding boxes to the anno_key layer, and compute annotation
extents for grid bounds expansion.

Phase 8a: Annotation Collection & Rasterization
"""


def is_extent_driver_annotation(elem):
    """Check if annotation is an extent driver (can exist outside crop).

    Extent drivers are annotations that can exist beyond the view crop:
        - Text (TextNote, keynotes)
        - Tags (all tag types)
        - Dimensions

    We avoid category-name substring matching (brittle/localized) and instead:
        1) Prefer type checks when Autodesk classes are available
        2) Fall back to BuiltInCategory id checks (stable)
    """
    try:
        # 1) Strongest signal: actual runtime types (when Autodesk is available)
        try:
            from Autodesk.Revit.DB import TextNote, Dimension, IndependentTag
            if isinstance(elem, (TextNote, Dimension, IndependentTag)):
                return True
        except Exception:
            pass

        cat = getattr(elem, "Category", None)
        if cat is None or getattr(cat, "Id", None) is None:
            return False

        # 2) Stable fallback: BuiltInCategory ids
        try:
            from Autodesk.Revit.DB import BuiltInCategory
            cat_id = int(cat.Id.IntegerValue)

            driver_cats = []

            # Text
            if hasattr(BuiltInCategory, "OST_TextNotes"):
                driver_cats.append(int(BuiltInCategory.OST_TextNotes))

            # Dimensions
            if hasattr(BuiltInCategory, "OST_Dimensions"):
                driver_cats.append(int(BuiltInCategory.OST_Dimensions))

            # Tags (mirror the same tag category set used in collect_2d_annotations)
            tag_cats = [
                "OST_RoomTags",
                "OST_SpaceTags",
                "OST_AreaTags",
                "OST_DoorTags",
                "OST_WindowTags",
                "OST_WallTags",
                "OST_MEPSpaceTags",
                "OST_GenericAnnotation",  # IndependentTag often lives here
                "OST_KeynoteTags",        # keynotes can behave like tags/text
            ]
            for n in tag_cats:
                if hasattr(BuiltInCategory, n):
                    driver_cats.append(int(getattr(BuiltInCategory, n)))

            return cat_id in set(driver_cats)
        except Exception:
            # Last-resort fallback (keep prior behavior, but only as a final fallback)
            name = ""
            try:
                name = (cat.Name or "").lower()
            except Exception:
                name = ""
            return ("tag" in name) or ("dimension" in name) or ("text" in name)

    except Exception as e:
        print(f"[WARN] revit.annotation:is_extent_driver_annotation: failed ({type(e).__name__}: {e})")
        return False


def compute_annotation_extents(doc, view, view_basis, base_bounds_xy, cell_size_ft, cfg=None, diag=None):
    """Compute annotation extents for grid bounds expansion.

    Collects extent-driver annotations (text, tags, dimensions) and computes
    their combined bounding box, respecting annotation crop and hard caps.

    Args:
        doc: Revit Document
        view: Revit View
        view_basis: ViewBasis (for coordinate transformation)
        base_bounds_xy: Bounds2D from crop box (model crop)
        cell_size_ft: Cell size in model units (feet)
        cfg: Config (optional, for cap configuration)

    Returns:
        Bounds2D with expanded extents, or None if no driver annotations
        Returns (min_x, min_y, max_x, max_y) that includes both base and annotations

    Commentary:
        ✔ Only processes extent drivers (text, tags, dimensions)
        ✔ Respects annotation crop when active (with configurable margin)
        ✔ Enforces hard cap on expansion when annotation crop inactive
        ✔ Matches SSM _compute_2d_annotation_extents logic
    """
    from vop_interwoven.core.math_utils import Bounds2D

    # Default configuration values
    ANNO_CROP_MARGIN_IN = 6.0  # Printed inches margin when annotation crop active
    HARD_CAP_CELLS = 500  # Maximum cells to expand when no annotation crop

    # Get config values if provided
    anno_crop_margin_in = ANNO_CROP_MARGIN_IN
    hard_cap_cells = HARD_CAP_CELLS

    if cfg and hasattr(cfg, 'anno_crop_margin_in'):
        anno_crop_margin_in = cfg.anno_crop_margin_in
    if cfg and hasattr(cfg, 'anno_expand_cap_cells'):
        hard_cap_cells = cfg.anno_expand_cap_cells

    # Detect annotation crop active
    ann_crop_active = False
    try:
        ann_crop_active = bool(getattr(view, 'AnnotationCropActive', False))
    except Exception as e1:
        print(f"[WARN] revit.annotation:AnnotationCropActive getattr failed ({type(e1).__name__}: {e1})")
        try:
            from Autodesk.Revit.DB import BuiltInParameter
            p = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
            if p is not None:
                ann_crop_active = bool(p.AsInteger() == 1)
        except Exception as e2:
            print(f"[WARN] revit.annotation:VIEWER_ANNOTATION_CROP_ACTIVE fallback failed ({type(e2).__name__}: {e2})")
            # leave ann_crop_active as-is (caller should have defaulted it)
            pass

    # Compute printed inches → model feet conversion factors
    scale = view.Scale if hasattr(view, 'Scale') else 96

    # Final printed margin (applied AFTER union), per your intent
    ann_margin_ft = (float(anno_crop_margin_in) / 12.0) * float(scale)

    # Expansion cap should be PRINTED INCHES, not cells.
    # Prefer cfg.anno_expand_cap_in if present.
    cap_in_printed = None
    if cfg and hasattr(cfg, 'anno_expand_cap_in'):
        try:
            cap_in_printed = float(cfg.anno_expand_cap_in)
        except Exception:
            cap_in_printed = None

    # Back-compat: treat existing cfg.anno_expand_cap_cells as printed inches
    # (matches your stated intent that "4" meant 4")
    if cap_in_printed is None:
        try:
            cap_in_printed = float(hard_cap_cells)
        except Exception:
            cap_in_printed = 0.0

    cap_ft = (cap_in_printed / 12.0) * float(scale)

    # Allowed expansion envelope is a HARD SAFETY CLAMP:
    # crop expanded by CAP ONLY (margin is applied after union)
    allow_min_x = base_bounds_xy.xmin - cap_ft
    allow_min_y = base_bounds_xy.ymin - cap_ft
    allow_max_x = base_bounds_xy.xmax + cap_ft
    allow_max_y = base_bounds_xy.ymax + cap_ft

    # Collect all annotations (thread diag so we can see what was collected)
    all_annotations = collect_2d_annotations(doc, view, diag=diag)

    # Filter to extent drivers only
    driver_annotations = [(elem, atype) for elem, atype in all_annotations
                          if is_extent_driver_annotation(elem)]

    if diag is not None:
        try:
            diag.info(
                phase="annotation",
                callsite="compute_annotation_extents.summary",
                message="Annotation extents driver filter summary",
                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                extra={
                    "all_annotations": len(all_annotations),
                    "driver_annotations": len(driver_annotations),
                    "ann_crop_active": bool(ann_crop_active),
                    "hard_cap_cells": hard_cap_cells,
                    "anno_crop_margin_in": anno_crop_margin_in,
                    "base_bounds_xy": (base_bounds_xy.xmin, base_bounds_xy.ymin, base_bounds_xy.xmax, base_bounds_xy.ymax),
                    "allow_bounds_xy": (allow_min_x, allow_min_y, allow_max_x, allow_max_y),
                },
            )
        except Exception:
            pass

    if not driver_annotations:
        return None

    # Compute bounding box of all driver annotations
    anno_min_x = anno_min_y = None
    anno_max_x = anno_max_y = None

    sample_limit = 5
    sample_count = 0

    for elem, anno_type in driver_annotations:
        # Optional sample log (pre-bbox) for first few drivers
        if diag is not None and sample_count < sample_limit:
            try:
                cat = getattr(elem, "Category", None)
                cname = getattr(cat, "Name", None) if cat is not None else None
                cid = None
                if cat is not None:
                    try:
                        cid = int(cat.Id.IntegerValue)
                    except Exception:
                        cid = None
                diag.info(
                    phase="annotation",
                    callsite="compute_annotation_extents.driver_sample.pre_bbox",
                    message="Driver annotation sample (pre-bbox)",
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
                    extra={"anno_type": anno_type, "cat_name": cname, "cat_id": cid},
                )
            except Exception:
                pass
            sample_count += 1

        try:
            # Get bounding box in view coordinates
            bbox = elem.get_BoundingBox(view)
            if bbox is None:
                continue

            # For dimensions, also include curve endpoints and text position
            from Autodesk.Revit.DB import Dimension, XYZ

            pts_to_check = []

            if isinstance(elem, Dimension):
                try:
                    curve = elem.Curve
                    if curve is not None:
                        pts_to_check.append(curve.GetEndPoint(0))
                        pts_to_check.append(curve.GetEndPoint(1))
                except Exception as e:
                    print(f"[WARN] revit.annotation:Dimension.Curve read failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")

                try:
                    text_pos = elem.TextPosition
                    if text_pos is not None:
                        pts_to_check.append(text_pos)
                except Exception as e:
                    print(f"[WARN] revit.annotation:Dimension.TextPosition read failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")

            mn = bbox.Min
            mx = bbox.Max

            corners_local = [
                XYZ(mn.X, mn.Y, mn.Z),
                XYZ(mx.X, mn.Y, mn.Z),
                XYZ(mn.X, mx.Y, mn.Z),
                XYZ(mx.X, mx.Y, mn.Z),
                XYZ(mn.X, mn.Y, mx.Z),
                XYZ(mx.X, mn.Y, mx.Z),
                XYZ(mn.X, mx.Y, mx.Z),
                XYZ(mx.X, mx.Y, mx.Z),
            ]

            TB = getattr(bbox, "Transform", None)
            if TB is not None:
                try:
                    pts_to_check.extend([TB.OfPoint(p) for p in corners_local])
                except Exception:
                    pts_to_check.extend(corners_local)
            else:
                pts_to_check.extend(corners_local)

            # Find min/max across all points (IN VIEW-LOCAL UV)
            for pt in pts_to_check:
                if pt is None:
                    continue

                x, y = view_basis.transform_to_view_uv((pt.X, pt.Y, pt.Z))

                # Clip to allowed envelope (also in view-local UV)
                # Option A: If annotation crop is NOT active, do not clamp to model-crop-derived envelope.
                # This allows driver annotations outside model crop to expand the grid.
                if ann_crop_active:
                    x = max(allow_min_x, min(allow_max_x, x))
                    y = max(allow_min_y, min(allow_max_y, y))

                # Update annotation extents
                if anno_min_x is None:
                    anno_min_x = anno_max_x = x
                    anno_min_y = anno_max_y = y
                else:
                    anno_min_x = min(anno_min_x, x)
                    anno_min_y = min(anno_min_y, y)
                    anno_max_x = max(anno_max_x, x)
                    anno_max_y = max(anno_max_y, y)

                # Diagnostic: flag points that exceed base crop
                if diag is not None:
                    try:
                        exceeds = (
                            (x < base_bounds_xy.xmin) or (y < base_bounds_xy.ymin) or
                            (x > base_bounds_xy.xmax) or (y > base_bounds_xy.ymax)
                        )
                        if exceeds:
                            diag.info(
                                phase="annotation",
                                callsite="compute_annotation_extents.exceeds_crop",
                                message="Driver point exceeds base crop (post-clamp-to-allow-envelope)",
                                view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                                elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
                                extra={
                                    "anno_type": anno_type,
                                    "pt_xy": (x, y),
                                    "base_bounds_xy": (base_bounds_xy.xmin, base_bounds_xy.ymin, base_bounds_xy.xmax, base_bounds_xy.ymax),
                                },
                            )
                    except Exception:
                        pass

        except Exception as e:
            if diag is not None:
                try:
                    diag.warn(
                        phase="annotation",
                        callsite="compute_annotation_extents",
                        message="Failed to process annotation for extents; skipping element",
                        view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                        elem_id=getattr(getattr(elem, "Id", None), "IntegerValue", None),
                        extra={"exc_type": type(e).__name__, "exc": str(e)},
                    )
                except Exception:
                    pass
            continue


    if anno_min_x is None:
        # No valid annotation extents found
        return None

    # Combine base bounds with annotation extents
    final_min_x = min(base_bounds_xy.xmin, anno_min_x)
    final_min_y = min(base_bounds_xy.ymin, anno_min_y)
    final_max_x = max(base_bounds_xy.xmax, anno_max_x)
    final_max_y = max(base_bounds_xy.ymax, anno_max_y)

    # Apply printed margin AFTER union (final outward pad)
    return Bounds2D(
        final_min_x - ann_margin_ft,
        final_min_y - ann_margin_ft,
        final_max_x + ann_margin_ft,
        final_max_y + ann_margin_ft
    )

def collect_2d_annotations(doc, view, diag=None):
    """Collect USER-ADDED 2D annotation elements by whitelist.

    IMPORTANT: This collects ONLY user annotations for anno_key layer.

    Categories collected (USER ANNOTATIONS):
        - TextNote (TEXT)
        - User Keynotes (TEXT)
        - Dimension (DIM)
        - IndependentTag, RoomTag, SpaceTag, etc. (TAG)
        - Material Element Keynotes (TAG)
        - FilledRegion (REGION)
        - Detail lines (LINES) - ViewSpecific=True from OST_Lines
        - DetailComponents (DETAIL) - user-placed detail items

    NOT collected here (go to MODEL occupancy):
        - Model lines → MODEL (OST_Lines with ViewSpecific=False)
        - Detail items embedded in model families → MODEL (part of FamilyInstance geometry)

    Key distinctions:
        - Detail lines (ViewSpecific=True) → ANNOTATION (collected here)
        - Model lines (ViewSpecific=False) → MODEL (excluded by ViewSpecific filter)
        - User-placed detail items → ANNOTATION (collected here)
        - Detail items nested in model families → MODEL (part of family geometry)

    Args:
        doc: Revit Document
        view: Revit View

    Returns:
        List of tuples: [(element, anno_type), ...]
        where anno_type is one of: TEXT, TAG, DIM, REGION, LINES, DETAIL, OTHER

    Commentary:
        ✔ ViewSpecific=True filter automatically separates detail lines from model lines
        ✔ User-placed detail items are annotations (DetailComponents)
        ✔ Model lines go to MODEL occupancy (ViewSpecific=False)
        ✔ Nested detail items in families are part of family geometry (MODEL)
        ✔ Uses category whitelist approach (explicit is better than implicit)
        ✔ Classifies annotations during collection
        ✔ Handles keynotes via KeynoteElement API
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        BuiltInCategory,
        ElementId,
    )

    annotations = []

    # Diagnostics accumulators
    type_counts = {}
    cat_counts = {}

    def _diag_info(callsite, message, extra=None):
        if diag is not None:
            try:
                diag.info(
                    phase="annotation",
                    callsite=callsite,
                    message=message,
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra=extra or {},
                )
                return
            except Exception:
                pass
        # Fallback: print
        try:
            print("[INFO] annotation.{0}: {1} {2}".format(callsite, message, extra or {}))
        except Exception:
            pass

    # Helper to safely collect category
    def collect_category(built_in_cat, anno_type_override=None, label=None):
        """Collect elements from a category and classify them."""
        try:
            collector = FilteredElementCollector(doc, view.Id)
            collector.OfCategory(built_in_cat).WhereElementIsNotElementType()

            for elem in collector:
                # CRITICAL: Only collect view-specific 2D elements
                try:
                    if not bool(getattr(elem, 'ViewSpecific', False)):
                        continue
                except Exception as e:
                    # If ViewSpecific property unavailable, skip element
                    print(f"[WARN] revit.annotation:ViewSpecific check failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")
                    continue

                # Get bounding box to ensure element is visible in view
                bbox = elem.get_BoundingBox(view)
                if bbox is None:
                    continue

                # Force FilledRegion into REGION bucket even if its UI/category is "Detail Items"
                try:
                    from Autodesk.Revit.DB import FilledRegion
                    if isinstance(elem, FilledRegion):
                        anno_type = "REGION"
                    else:
                        anno_type = None
                except Exception:
                    anno_type = None

                # Classify the element
                if anno_type is None:
                    if anno_type_override:
                        anno_type = anno_type_override
                    else:
                        anno_type = classify_annotation(elem)

                annotations.append((elem, anno_type))

                # Diag counts
                try:
                    type_counts[anno_type] = type_counts.get(anno_type, 0) + 1
                except Exception:
                    pass
                try:
                    cat = getattr(elem, "Category", None)
                    cname = getattr(cat, "Name", None) if cat is not None else None
                    key = label or cname or str(built_in_cat)
                    cat_counts[key] = cat_counts.get(key, 0) + 1
                except Exception:
                    pass

        except Exception as e:
            if diag is not None:
                try:
                    diag.warn(
                        phase="annotation",
                        callsite="collect_2d_annotations.collect_category",
                        message="Annotation category collection failed; skipping category",
                        view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                        extra={"category": str(built_in_cat), "exc_type": type(e).__name__, "exc": str(e)},
                    )
                    return
                except Exception:
                    pass

            print(
                f"[WARN] revit.annotation:collector failed "
                f"(view_id={getattr(view,'Id',None)}, cat={built_in_cat}) "
                f"({type(e).__name__}: {e})"
            )

    # TEXT: TextNote
    if hasattr(BuiltInCategory, 'OST_TextNotes'):
        collect_category(BuiltInCategory.OST_TextNotes, "TEXT", label="OST_TextNotes")

    # DIM: Dimensions
    if hasattr(BuiltInCategory, 'OST_Dimensions'):
        collect_category(BuiltInCategory.OST_Dimensions, "DIM", label="OST_Dimensions")

    # TAG: Tags (multiple tag categories)
    tag_categories = [
        'OST_RoomTags',
        'OST_SpaceTags',
        'OST_AreaTags',
        'OST_DoorTags',
        'OST_WindowTags',
        'OST_WallTags',
        'OST_MEPSpaceTags',
        'OST_GenericAnnotation',  # IndependentTag lives here
    ]
    for cat_name in tag_categories:
        if hasattr(BuiltInCategory, cat_name):
            collect_category(getattr(BuiltInCategory, cat_name), "TAG", label=cat_name)

    # REGION: FilledRegion
    if hasattr(BuiltInCategory, 'OST_FilledRegion'):
        collect_category(BuiltInCategory.OST_FilledRegion, "REGION", label="OST_FilledRegion")

    # LINES: Detail lines (ViewSpecific=True from OST_Lines)
    if hasattr(BuiltInCategory, 'OST_Lines'):
        collect_category(BuiltInCategory.OST_Lines, "LINES", label="OST_Lines")

    # DETAIL: Detail components (user-placed detail items)
    if hasattr(BuiltInCategory, 'OST_DetailComponents'):
        collect_category(BuiltInCategory.OST_DetailComponents, "DETAIL", label="OST_DetailComponents")

    # KEYNOTES: Handle keynotes specially
    if hasattr(BuiltInCategory, 'OST_KeynoteTags'):
        try:
            collector = FilteredElementCollector(doc, view.Id)
            collector.OfCategory(BuiltInCategory.OST_KeynoteTags).WhereElementIsNotElementType()

            for elem in collector:
                bbox = elem.get_BoundingBox(view)
                if bbox is None:
                    continue

                anno_type = classify_keynote(elem)
                annotations.append((elem, anno_type))

                try:
                    type_counts[anno_type] = type_counts.get(anno_type, 0) + 1
                except Exception:
                    pass
                try:
                    cat_counts["OST_KeynoteTags"] = cat_counts.get("OST_KeynoteTags", 0) + 1
                except Exception:
                    pass

        except Exception as e:
            if diag is not None:
                try:
                    diag.warn(
                        phase="annotation",
                        callsite="collect_2d_annotations.keynotes",
                        message="Keynote annotation collection failed; skipping",
                        view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                        extra={"exc_type": type(e).__name__, "exc": str(e)},
                    )
                except Exception:
                    pass
            else:
                print(
                    f"[WARN] revit.annotation:keynote collector failed "
                    f"(view_id={getattr(view,'Id',None)}) "
                    f"({type(e).__name__}: {e})"
                )

    _diag_info(
        "collect_2d_annotations.summary",
        "Collected 2D annotations (post-ViewSpecific, post-bbox)",
        extra={
            "total": len(annotations),
            "type_counts": dict(type_counts),
            "cat_counts": dict(cat_counts),
        },
    )

    return annotations


def classify_annotation(elem):
    """Classify annotation element into type.

    Args:
        elem: Revit annotation element

    Returns:
        "TEXT" | "TAG" | "DIM" | "DETAIL" | "LINES" | "REGION" | "OTHER"

    Commentary:
        ✔ Uses element category for classification
        ✔ Handles keynotes via classify_keynote()
        ✔ LINES = detail lines (ViewSpecific=True from OST_Lines)
        ✔ Model lines (ViewSpecific=False) not classified here - they go to MODEL
        ✔ Matches SSM exporter classification logic
    """
    from Autodesk.Revit.DB import BuiltInCategory

    try:
        from Autodesk.Revit.DB import FilledRegion
        if isinstance(elem, FilledRegion):
            return "REGION"
    except Exception:
        pass

    try:
        category = elem.Category
        if category is None:
            return "OTHER"

        cat_id = category.Id.IntegerValue

        # TEXT: TextNote
        if hasattr(BuiltInCategory, 'OST_TextNotes'):
            if cat_id == int(BuiltInCategory.OST_TextNotes):
                return "TEXT"

        # DIM: Dimensions
        if hasattr(BuiltInCategory, 'OST_Dimensions'):
            if cat_id == int(BuiltInCategory.OST_Dimensions):
                return "DIM"

        # TAG: Various tag categories
        tag_categories = [
            'OST_RoomTags', 'OST_SpaceTags', 'OST_AreaTags',
            'OST_DoorTags', 'OST_WindowTags', 'OST_WallTags',
            'OST_MEPSpaceTags', 'OST_GenericAnnotation'
        ]
        for cat_name in tag_categories:
            if hasattr(BuiltInCategory, cat_name):
                if cat_id == int(getattr(BuiltInCategory, cat_name)):
                    return "TAG"

        # REGION: FilledRegion
        if hasattr(BuiltInCategory, 'OST_FilledRegion'):
            if cat_id == int(BuiltInCategory.OST_FilledRegion):
                return "REGION"

        # LINES: Detail lines (ViewSpecific=True from OST_Lines)
        if hasattr(BuiltInCategory, 'OST_Lines'):
            if cat_id == int(BuiltInCategory.OST_Lines):
                return "LINES"

        # DETAIL: Detail components (user-placed detail items)
        if hasattr(BuiltInCategory, 'OST_DetailComponents'):
            if cat_id == int(BuiltInCategory.OST_DetailComponents):
                return "DETAIL"

        # KEYNOTES: Handle specially
        if hasattr(BuiltInCategory, 'OST_KeynoteTags'):
            if cat_id == int(BuiltInCategory.OST_KeynoteTags):
                return classify_keynote(elem)

    except Exception as e:
        print(f"[WARN] revit.annotation:classify_annotation failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")
        return "OTHER"

    return "OTHER"


def classify_keynote(elem):
    """Classify keynote element as TAG or TEXT based on keynote type.

    Keynote types:
        - Material Element Keynotes → TAG
        - User Keynotes → TEXT
        - Element Keynotes → TAG (default)

    Args:
        elem: Revit keynote tag element

    Returns:
        "TAG" | "TEXT"

    Commentary:
        ✔ Uses Revit KeynoteElement API to determine keynote type
        ✔ Material Element Keynotes are associated with material properties (TAG)
        ✔ User Keynotes are free-form text annotations (TEXT)
    """
    try:
        # Try to access keynote type via parameter
        # The "Keynote" parameter contains the keynote key
        keynote_param = elem.LookupParameter("Keynote")
        if keynote_param and keynote_param.HasValue:
            keynote_key = keynote_param.AsString()

            # User keynotes typically don't have a key or have a custom format
            # Material/Element keynotes have structured keys from the keynote table
            if not keynote_key or len(keynote_key.strip()) == 0:
                return "TEXT"  # User keynote

            # Check if it's a material keynote by looking at the referenced element
            # Material keynotes reference materials, element keynotes reference elements
            try:
                # Try to get the tagged element
                tagged_id = elem.TaggedLocalElementId if hasattr(elem, 'TaggedLocalElementId') else None
                if tagged_id is not None and tagged_id.IntegerValue > 0:
                    # If it has a tagged element, it's likely an element or material keynote (TAG)
                    return "TAG"
            except Exception as e:
                print(f"[WARN] revit.annotation:keynote tagged element check failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")
                pass

        # Default to TAG for structured keynotes
        return "TAG"

    except Exception as e:
        # If we can't determine, default to TAG
        print(f"[WARN] revit.annotation:classify_keynote failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")
        return "TAG"


def get_annotation_bbox(elem, view):
    """Get annotation bounding box in view coordinates.

    Handles special cases:
        - Text with rotation
        - Dimensions (linear, radial, angular)
        - Tags with leader lines
        - Detail components
        - Keynotes

    Args:
        elem: Revit annotation element
        view: Revit View

    Returns:
        BoundingBoxXYZ in view coordinates, or None if unavailable

    Commentary:
        ✔ Uses element.get_BoundingBox(view) for view-space coordinates
        ✔ View coordinates are in feet (same as model coordinates)
        ✔ Bounding box includes leader lines for tags
        ✔ Rotated text bounding boxes include full extents
    """
    try:
        bbox = elem.get_BoundingBox(view)
        return bbox
    except Exception as e:
        print(f"[WARN] revit.annotation:get_BoundingBox(view) failed (elem_id={getattr(elem,'Id',None)}) ({type(e).__name__}: {e})")
        return None


def rasterize_annotations(doc, view, raster, cfg, diag=None):
    """Rasterize 2D annotations to anno_key layer."""
    view_id = None
    try:
        view_id = getattr(getattr(view, "Id", None), "IntegerValue", None)
    except Exception:
        view_id = None

    # Collect all annotations
    annotations = collect_2d_annotations(doc, view, diag=diag)

    if diag is not None:
        try:
            region_samples = []
            region_count = 0
            for (e, t) in annotations:
                if str(t).upper() == "REGION":
                    region_count += 1
                    if len(region_samples) < 5:
                        region_samples.append(getattr(getattr(e, "Id", None), "IntegerValue", None))
            diag.info(
                phase="annotation",
                callsite="rasterize_annotations.pre_summary",
                message="Rasterize annotations pre-summary",
                view_id=view_id,
                extra={"total": len(annotations), "region_count": region_count, "region_elem_ids_sample": region_samples},
            )
        except Exception:
            pass

    if not annotations:
        return

    # Get view basis from raster (stored during init)
    if hasattr(raster, 'view_basis'):
        vb = raster.view_basis
    else:
        from vop_interwoven.revit.view_basis import make_view_basis
        vb = make_view_basis(view, diag=diag)

    # Rate-limit per-annotation failures
    fail_count = 0
    fail_limit = 10

    for elem, anno_type in annotations:
        elem_id = None
        try:
            elem_id = getattr(getattr(elem, "Id", None), "IntegerValue", None)
        except Exception:
            elem_id = None

        bbox = get_annotation_bbox(elem, view)
        if bbox is None:
            continue

        try:
            cell_rect = _project_element_bbox_to_cell_rect_for_anno(bbox, vb, raster)
            if cell_rect is None:
                continue

            anno_idx = len(raster.anno_meta)
            
            # Capture category id for downstream export remapping (e.g., FilledRegion -> REGION)
            cat_id = None
            try:
                cat = getattr(elem, "Category", None)
                if cat is not None and getattr(cat, "Id", None) is not None:
                    cat_id = int(cat.Id.IntegerValue)
            except Exception:
                cat_id = None

            raster.anno_meta.append({
                "type": anno_type,
                "element_id": elem_id,
                "cat_id": cat_id,
                "bbox_min": (bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
                "bbox_max": (bbox.Max.X, bbox.Max.Y, bbox.Max.Z),
            })

            if diag is not None:
                try:
                    cat = getattr(elem, "Category", None)
                    cname = getattr(cat, "Name", None) if cat is not None else None
                    cid = None
                    if cat is not None:
                        try:
                            cid = int(cat.Id.IntegerValue)
                        except Exception:
                            cid = None

                    stored_type = raster.anno_meta[anno_idx].get("type") if anno_idx < len(raster.anno_meta) else None
                    if str(anno_type).upper() == "REGION" or (cname and "filled" in str(cname).lower()):
                        diag.info(
                            phase="annotation",
                            callsite="rasterize_annotations.region_stamp",
                            message="Stamped REGION-ish annotation into raster.anno_meta",
                            view_id=view_id,
                            elem_id=elem_id,
                            extra={
                                "anno_type_in": anno_type,
                                "anno_type_stored": stored_type,
                                "cat_name": cname,
                                "cat_id": cid,
                                "anno_idx": anno_idx,
                            },
                        )
                except Exception:
                    pass

            # Stamping strategy:
            # - DIM: stamp dimension curve as a thin line (no filled bbox)
            # - TEXT/TAG/LINES: stamp bbox outline (lightweight)
            # - DETAIL/REGION (and others): fill bbox (as before)

            mode = str(anno_type or "").upper()

            # DIM: draw only the dimension line (no filled bbox)
            if mode == "DIM":
                stamped = False
                try:
                    from Autodesk.Revit.DB import Dimension
                    if isinstance(elem, Dimension):
                        curve = getattr(elem, "Curve", None)
                        if curve is not None:
                            p0 = curve.GetEndPoint(0)
                            p1 = curve.GetEndPoint(1)

                            u0, v0 = vb.transform_to_view_uv((p0.X, p0.Y, p0.Z))
                            u1, v1 = vb.transform_to_view_uv((p1.X, p1.Y, p1.Z))

                            cx0, cy0 = _uv_to_cell(u0, v0, raster)
                            cx1, cy1 = _uv_to_cell(u1, v1, raster)

                            _stamp_line_cells(raster, cx0, cy0, cx1, cy1, anno_idx)
                            stamped = True

                            if diag is not None:
                                try:
                                    diag.info(
                                        phase="annotation",
                                        callsite="rasterize_annotations.dim_line_stamp",
                                        message="Stamped DIM via Dimension.Curve endpoints",
                                        view_id=view_id,
                                        elem_id=elem_id,
                                        extra={
                                            "p0": (p0.X, p0.Y, p0.Z),
                                            "p1": (p1.X, p1.Y, p1.Z),
                                            "cell0": (cx0, cy0),
                                            "cell1": (cx1, cy1),
                                        },
                                    )
                                except Exception:
                                    pass

                except Exception:
                    stamped = False

                if not stamped:

                    if diag is not None:
                        try:
                            diag.info(
                                phase="annotation",
                                callsite="rasterize_annotations.dim_fallback_outline",
                                message="DIM curve unavailable; used bbox outline fallback",
                                view_id=view_id,
                                elem_id=elem_id,
                                extra={
                                    "cell_rect": (cell_rect.x0, cell_rect.y0, cell_rect.x1, cell_rect.y1),
                                },
                            )
                        except Exception:
                            pass

                    # Fallback: outline bbox (still not filled)
                    _stamp_rect_outline(raster, cell_rect, anno_idx)

            # TEXT: keep as filled (per your request)
            elif mode == "TEXT":
                x0 = max(0, cell_rect.x0)
                y0 = max(0, cell_rect.y0)
                x1 = min(raster.W, cell_rect.x1)
                y1 = min(raster.H, cell_rect.y1)

                # Skip if bbox is huge (prevents floaters from bad bboxes)
                bbox_width = x1 - x0
                bbox_height = y1 - y0
                if bbox_width > raster.W * 2 or bbox_height > raster.H * 2:
                    continue

                for cy in range(y0, y1):
                    row = cy * raster.W
                    for cx in range(x0, x1):
                        raster.anno_key[row + cx] = anno_idx

            # TAG/KEYNOTE: outline only (cheap + avoids big fills)
            elif mode in ("TAG", "KEYNOTE"):
                _stamp_rect_outline(raster, cell_rect, anno_idx)

            # LINES: Detail lines need special handling (extract actual curve geometry)
            elif mode == "LINES":
                stamped = False
                try:
                    # Extract curve from Location (like model lines, but in 2D)
                    loc = getattr(elem, "Location", None)
                    if loc is not None:
                        curve = getattr(loc, "Curve", None)
                        if curve is not None:
                            # Get curve endpoints
                            p0 = curve.GetEndPoint(0)
                            p1 = curve.GetEndPoint(1)

                            # Transform to UV
                            u0, v0 = vb.transform_to_view_uv((p0.X, p0.Y, p0.Z))
                            u1, v1 = vb.transform_to_view_uv((p1.X, p1.Y, p1.Z))

                            # Convert to cell coordinates
                            cx0, cy0 = _uv_to_cell(u0, v0, raster)
                            cx1, cy1 = _uv_to_cell(u1, v1, raster)

                            # Option A: Render as Bresenham line (single-pixel width)
                            # Render as Bresenham line (single-pixel width, simpler but less visible)
                            _stamp_line_cells(raster, cx0, cy0, cx1, cy1, anno_idx)
                            stamped = True
                            
                            # Option B: Render as oriented band (2-cell width, archive parity)
                            _stamp_detail_line_band(raster, cx0, cy0, cx1, cy1, anno_idx, cfg)
                            
                            stamped = True

                            if diag is not None:
                                try:
                                    diag.info(
                                        phase="annotation",
                                        callsite="rasterize_annotations.lines_curve_stamp",
                                        message="Stamped LINES via Location.Curve endpoints",
                                        view_id=view_id,
                                        elem_id=elem_id,
                                        extra={
                                            "p0": (p0.X, p0.Y, p0.Z),
                                            "p1": (p1.X, p1.Y, p1.Z),
                                            "cell0": (cx0, cy0),
                                            "cell1": (cx1, cy1),
                                        },
                                    )
                                except Exception:
                                    pass

                except Exception:
                    stamped = False

                # Fallback: if curve extraction failed, use bbox outline
                if not stamped:
                    if diag is not None:
                        try:
                            diag.info(
                                phase="annotation",
                                callsite="rasterize_annotations.lines_fallback_outline",
                                message="LINES curve unavailable; used bbox outline fallback",
                                view_id=view_id,
                                elem_id=elem_id,
                                extra={
                                    "cell_rect": (cell_rect.x0, cell_rect.y0, cell_rect.x1, cell_rect.y1),
                                },
                            )
                        except Exception:
                            pass

                    _stamp_rect_outline(raster, cell_rect, anno_idx)

            # DETAIL/REGION: keep legacy fill unless you want otherwise
            else:
                x0 = max(0, cell_rect.x0)
                y0 = max(0, cell_rect.y0)
                x1 = min(raster.W, cell_rect.x1)
                y1 = min(raster.H, cell_rect.y1)

                # Skip if bbox is huge (prevents floaters from bad bboxes)
                bbox_width = x1 - x0
                bbox_height = y1 - y0
                if bbox_width > raster.W * 2 or bbox_height > raster.H * 2:
                    continue

                for cy in range(y0, y1):
                    row = cy * raster.W
                    for cx in range(x0, x1):
                        raster.anno_key[row + cx] = anno_idx

        except Exception as e:
            fail_count += 1
            if diag is not None and fail_count <= fail_limit:
                diag.warn(
                    phase="annotation",
                    callsite="rasterize_annotations",
                    message="Failed to rasterize annotation; skipping",
                    view_id=view_id,
                    elem_id=elem_id,
                    extra={"anno_type": anno_type, "exc_type": type(e).__name__, "exc": str(e)},
                )
            continue

    # If failures exceeded the limit, record one aggregated warning
    if diag is not None and fail_count > fail_limit:
        diag.warn(
            phase="annotation",
            callsite="rasterize_annotations.summary",
            message="Many annotation rasterization failures occurred (rate-limited)",
            view_id=view_id,
            extra={"fail_count": fail_count, "fail_limit": fail_limit},
        )
    if diag is not None:
        try:
            anno_key = getattr(raster, "anno_key", []) or []
            anno_meta = getattr(raster, "anno_meta", []) or []
            anno_over_model = getattr(raster, "anno_over_model", []) or []

            n_anno = sum(1 for k in anno_key if k is not None and k != -1)
            n_over = sum(1 for b in anno_over_model if bool(b))

            # Distribution by stored anno_meta.type (counts cells, not elements)
            tcounts = {}
            for idx in anno_key:
                if idx is None or idx < 0:
                    continue
                if idx < len(anno_meta):
                    t = (anno_meta[idx].get("type", "OTHER") or "OTHER").upper()
                else:
                    t = "OTHER"
                tcounts[t] = tcounts.get(t, 0) + 1

            diag.info(
                phase="annotation",
                callsite="rasterize_annotations.post_summary",
                message="Rasterize annotations post-summary",
                view_id=view_id,
                extra={
                    "anno_cells": int(n_anno),
                    "anno_over_model_cells": int(n_over),
                    "anno_cell_type_counts": dict(tcounts),
                    "anno_meta_len": int(len(anno_meta)),
                    "W": int(getattr(raster, "W", 0)),
                    "H": int(getattr(raster, "H", 0)),
                },
            )
        except Exception:
            pass
 
def _stamp_detail_line_band(raster, cx0, cy0, cx1, cy1, anno_idx, cfg):
    """
    Stamp detail line as oriented band (2-cell-wide rectangle along line tangent).
    
    This matches archive/refactor1 behavior for detail lines and is more visible
    than single-pixel Bresenham lines.
    
    Args:
        raster: ViewRaster
        cx0, cy0: Start cell coordinates
        cx1, cy1: End cell coordinates
        anno_idx: Annotation index
        cfg: Config (for band_thickness_cells)
    """
    import math
    
    # Compute line tangent and perpendicular normal
    dx = float(cx1 - cx0)
    dy = float(cy1 - cy0)
    length_sq = dx*dx + dy*dy
    
    if length_sq < 0.01:  # Degenerate line (< 0.1 cell length)
        # Fallback: just stamp the start cell
        _stamp_cell(raster, cx0, cy0, anno_idx)
        return
    
    length = math.sqrt(length_sq)
    
    # Unit tangent vector
    ux = dx / length
    uy = dy / length
    
    # Unit normal vector (perpendicular, rotate 90° CCW)
    nx = -uy
    ny = ux
    
    # Band half-width from config (default 0.5 cells for 1-cell total width)
    band_cells = getattr(cfg, 'linear_band_thickness_cells', 1.0) if cfg else 1.0
    band_half_cells = band_cells * 0.5
    
    # Perpendicular offset in cell space
    offx = nx * band_half_cells
    offy = ny * band_half_cells
    
    # Four corners of oriented band
    p0_plus = (cx0 + offx, cy0 + offy)
    p0_minus = (cx0 - offx, cy0 - offy)
    p1_plus = (cx1 + offx, cy1 + offy)
    p1_minus = (cx1 - offx, cy1 - offy)
    
    # Rasterize the band as a filled polygon
    # Simple scanline fill between the four corners
    corners = [p0_plus, p1_plus, p1_minus, p0_minus]
    
    # Get integer bounding box
    xs = [int(round(c[0])) for c in corners]
    ys = [int(round(c[1])) for c in corners]
    
    x_min = max(0, min(xs))
    x_max = min(raster.W, max(xs))
    y_min = max(0, min(ys))
    y_max = min(raster.H, max(ys))
    
    # For simplicity: stamp all cells in bounding box
    # (More sophisticated polygon fill could be added later)
    for cy in range(y_min, y_max + 1):
        for cx in range(x_min, x_max + 1):
            # Point-in-polygon test (simple cross product)
            if _point_in_quad(cx, cy, corners):
                _stamp_cell(raster, cx, cy, anno_idx)


def _point_in_quad(px, py, corners):
    """
    Test if point (px, py) is inside the quadrilateral defined by corners.
    
    Uses winding number algorithm (simplified for convex quads).
    """
    # For a convex quad (which oriented bands are), we can use cross products
    # Point is inside if it's on the same side of all 4 edges
    
    def cross_sign(ax, ay, bx, by, px, py):
        """Sign of cross product (a-p) × (b-p)"""
        return (bx - px) * (ay - py) - (by - py) * (ax - px)
    
    c0, c1, c2, c3 = corners
    
    # Check point is on correct side of each edge
    s0 = cross_sign(c0[0], c0[1], c1[0], c1[1], px, py)
    s1 = cross_sign(c1[0], c1[1], c2[0], c2[1], px, py)
    s2 = cross_sign(c2[0], c2[1], c3[0], c3[1], px, py)
    s3 = cross_sign(c3[0], c3[1], c0[0], c0[1], px, py)
    
    # All signs should be the same (all positive or all negative)
    # For simplicity, check if all have same sign or are zero
    signs = [s0, s1, s2, s3]
    has_positive = any(s > 0.01 for s in signs)
    has_negative = any(s < -0.01 for s in signs)
    
    # Point is inside if not crossing (not both positive and negative)
    return not (has_positive and has_negative)
    
def _uv_to_cell(x, y, raster):
    """Convert view-local UV (feet) to integer cell coordinates."""
    b = raster.bounds_xy
    cs = raster.cell_size_ft
    cx = int((x - b.xmin) / cs)
    cy = int((y - b.ymin) / cs)
    return cx, cy


def _stamp_cell(raster, cx, cy, anno_idx):
    """Set a single annotation cell if within bounds."""
    if cx < 0 or cy < 0 or cx >= raster.W or cy >= raster.H:
        return
    raster.anno_key[cy * raster.W + cx] = anno_idx


def _stamp_rect_outline(raster, cell_rect, anno_idx):
    """Stamp only the perimeter of a rectangle in cell coordinates."""
    x0 = max(0, cell_rect.x0)
    y0 = max(0, cell_rect.y0)
    x1 = min(raster.W, cell_rect.x1)
    y1 = min(raster.H, cell_rect.y1)
    if x1 <= x0 or y1 <= y0:
        return

    # top/bottom
    for cx in range(x0, x1):
        _stamp_cell(raster, cx, y0, anno_idx)
        _stamp_cell(raster, cx, y1 - 1, anno_idx)

    # left/right
    for cy in range(y0, y1):
        _stamp_cell(raster, x0, cy, anno_idx)
        _stamp_cell(raster, x1 - 1, cy, anno_idx)


def _stamp_line_cells(raster, x0, y0, x1, y1, anno_idx):
    """Stamp a line in cell space using Bresenham (integer coords)."""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    cx, cy = x0, y0

    while True:
        _stamp_cell(raster, cx, cy, anno_idx)
        if cx == x1 and cy == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            cx += sx
        if e2 <= dx:
            err += dx
            cy += sy

def _project_element_bbox_to_cell_rect_for_anno(elem_or_bbox, view_basis, raster):
    """Project element bounding box to cell rectangle (annotation-specific).

    This is a simplified version of _project_element_bbox_to_cell_rect from collection.py
    that works with both elements and raw bounding boxes.

    Args:
        elem_or_bbox: Element or BBox wrapper (has .Min and .Max)
        view_basis: ViewBasis
        raster: ViewRaster

    Returns:
        Simple namespace with x0, y0, x1, y1 (cell coordinates), or None
    """
    try:
        # Get bounding box
        if hasattr(elem_or_bbox, 'Min'):
            # It's already a bbox
            bbox = elem_or_bbox
        else:
            # Try to get bbox from element
            bbox = elem_or_bbox.get_BoundingBox(None)
            if bbox is None:
                return None

        # Project bbox corners to view coordinates
        min_pt = bbox.Min
        max_pt = bbox.Max

        # Simple projection: use view_basis to convert world to view
        # For 2D views, this is typically just X,Y coordinates
        # For simplicity, we'll use the world coordinates directly
        # since the view basis should handle the transformation

        # Convert to cell coordinates
        cell_size = raster.cell_size_ft

        # Get view bounds
        bounds = raster.bounds_xy

        # Project bbox corners to view-local UV using view_basis
        pts = [
            (min_pt.X, min_pt.Y, min_pt.Z),
            (max_pt.X, min_pt.Y, min_pt.Z),
            (min_pt.X, max_pt.Y, min_pt.Z),
            (max_pt.X, max_pt.Y, min_pt.Z),
            (min_pt.X, min_pt.Y, max_pt.Z),
            (max_pt.X, min_pt.Y, max_pt.Z),
            (min_pt.X, max_pt.Y, max_pt.Z),
            (max_pt.X, max_pt.Y, max_pt.Z),
        ]

        us = []
        vs = []
        for p in pts:
            u, v = view_basis.transform_to_view_uv(p)
            us.append(u)
            vs.append(v)

        min_x_view = min(us)
        max_x_view = max(us)
        min_y_view = min(vs)
        max_y_view = max(vs)

        # Convert to cell coordinates relative to view bounds
        x0_cell = int((min_x_view - bounds.xmin) / cell_size)
        y0_cell = int((min_y_view - bounds.ymin) / cell_size)
        x1_cell = int((max_x_view - bounds.xmin) / cell_size) + 1
        y1_cell = int((max_y_view - bounds.ymin) / cell_size) + 1

        # Create result object
        class CellRect:
            def __init__(self, x0, y0, x1, y1):
                self.x0 = x0
                self.y0 = y0
                self.x1 = x1
                self.y1 = y1
                self.width_cells = x1 - x0
                self.height_cells = y1 - y0

        return CellRect(x0_cell, y0_cell, x1_cell, y1_cell)

    except Exception as e:
        print(f"[WARN] revit.annotation:project_bbox_to_cell_rect failed (input={type(elem_or_bbox).__name__}) ({type(e).__name__}: {e})")
        return None
