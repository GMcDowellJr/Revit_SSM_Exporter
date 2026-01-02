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

    Non-drivers are crop-clipped annotations:
        - FilledRegion (clipped by crop)
        - DetailCurves (clipped by crop)
        - DetailComponents (clipped by crop)

    Args:
        elem: Revit annotation element

    Returns:
        True if element is an extent driver (should expand grid bounds)

    Commentary:
        ✔ Matches SSM _is_extent_driver_2d logic
        ✔ Uses category name matching for robustness
    """
    try:
        cat = elem.Category
        if cat is None:
            return False

        name = cat.Name.lower() if cat.Name else ""

        # Text, tags, and dimensions can extend beyond crop
        if "tag" in name:
            return True
        if "dimension" in name:
            return True
        if "text" in name:
            return True

    except:
        pass

    return False


def compute_annotation_extents(doc, view, view_basis, base_bounds_xy, cell_size_ft, cfg=None):
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
    except:
        try:
            from Autodesk.Revit.DB import BuiltInParameter
            p = view.get_Parameter(BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
            if p is not None:
                ann_crop_active = bool(p.AsInteger() == 1)
        except:
            pass

    # Compute allowed expansion envelope
    allow_min_x = allow_min_y = allow_max_x = allow_max_y = None

    if ann_crop_active:
        # When annotation crop is ACTIVE: expand model crop by fixed margin
        scale = view.Scale if hasattr(view, 'Scale') else 96
        ann_margin_ft = (anno_crop_margin_in / 12.0) * float(scale)

        allow_min_x = base_bounds_xy.min_x - ann_margin_ft
        allow_min_y = base_bounds_xy.min_y - ann_margin_ft
        allow_max_x = base_bounds_xy.max_x + ann_margin_ft
        allow_max_y = base_bounds_xy.max_y + ann_margin_ft
    else:
        # When annotation crop is NOT active: allow expansion up to hard cap
        cap_ft = float(hard_cap_cells) * float(cell_size_ft)

        allow_min_x = base_bounds_xy.min_x - cap_ft
        allow_min_y = base_bounds_xy.min_y - cap_ft
        allow_max_x = base_bounds_xy.max_x + cap_ft
        allow_max_y = base_bounds_xy.max_y + cap_ft

    # Collect all annotations
    all_annotations = collect_2d_annotations(doc, view)

    # Filter to extent drivers only
    driver_annotations = [(elem, atype) for elem, atype in all_annotations
                          if is_extent_driver_annotation(elem)]

    if not driver_annotations:
        # No driver annotations - return None (no expansion needed)
        return None

    # Compute bounding box of all driver annotations
    anno_min_x = anno_min_y = None
    anno_max_x = anno_max_y = None

    for elem, anno_type in driver_annotations:
        try:
            # Get bounding box in view coordinates
            bbox = elem.get_BoundingBox(view)
            if bbox is None:
                continue

            # For dimensions, also include curve endpoints and text position
            from Autodesk.Revit.DB import Dimension

            pts_to_check = []

            if isinstance(elem, Dimension):
                # Include dimension curve endpoints
                try:
                    curve = elem.Curve
                    if curve is not None:
                        pts_to_check.append(curve.GetEndPoint(0))
                        pts_to_check.append(curve.GetEndPoint(1))
                except:
                    pass

                # Include text position
                try:
                    text_pos = elem.TextPosition
                    if text_pos is not None:
                        pts_to_check.append(text_pos)
                except:
                    pass

            # Add bbox corners
            pts_to_check.append(bbox.Min)
            pts_to_check.append(bbox.Max)

            # Find min/max across all points
            for pt in pts_to_check:
                if pt is None:
                    continue

                # Project to view-local coordinates (X, Y)
                x, y = pt.X, pt.Y

                # Clip to allowed envelope
                if allow_min_x is not None:
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

        except:
            # Skip annotations that fail to process
            continue

    if anno_min_x is None:
        # No valid annotation extents found
        return None

    # Combine base bounds with annotation extents
    final_min_x = min(base_bounds_xy.min_x, anno_min_x)
    final_min_y = min(base_bounds_xy.min_y, anno_min_y)
    final_max_x = max(base_bounds_xy.max_x, anno_max_x)
    final_max_y = max(base_bounds_xy.max_y, anno_max_y)

    return Bounds2D(final_min_x, final_min_y, final_max_x, final_max_y)


def collect_2d_annotations(doc, view):
    """Collect view-specific 2D annotation elements by whitelist.

    Categories collected:
        - TextNote (TEXT)
        - User Keynotes (TEXT)
        - Dimension (DIM)
        - IndependentTag, RoomTag, SpaceTag, etc. (TAG)
        - Material Element Keynotes (TAG)
        - FilledRegion (REGION)
        - DetailCurve, CurveElement (LINES)
        - FamilyInstance (view-specific) (DETAIL)

    Args:
        doc: Revit Document
        view: Revit View

    Returns:
        List of tuples: [(element, anno_type), ...]
        where anno_type is one of: TEXT, TAG, DIM, DETAIL, LINES, REGION, OTHER

    Commentary:
        ✔ Only collects 2D view-specific elements
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

    # Helper to safely collect category
    def collect_category(built_in_cat, anno_type_override=None):
        """Collect elements from a category and classify them."""
        try:
            collector = FilteredElementCollector(doc, view.Id)
            collector.OfCategory(built_in_cat).WhereElementIsNotElementType()

            for elem in collector:
                # CRITICAL: Only collect view-specific 2D elements
                # This filters out model elements and ensures we get true annotations
                # including symbolic lines from families and nested family components
                try:
                    if not bool(getattr(elem, 'ViewSpecific', False)):
                        continue
                except:
                    # If ViewSpecific property unavailable, skip element
                    continue

                # Get bounding box to ensure element is visible in view
                bbox = elem.get_BoundingBox(view)
                if bbox is not None:
                    # Classify the element
                    if anno_type_override:
                        anno_type = anno_type_override
                    else:
                        anno_type = classify_annotation(elem)

                    annotations.append((elem, anno_type))
        except:
            # Skip categories that cause errors (not available in this view/version)
            pass

    # TEXT: TextNote
    if hasattr(BuiltInCategory, 'OST_TextNotes'):
        collect_category(BuiltInCategory.OST_TextNotes, "TEXT")

    # DIM: Dimensions
    if hasattr(BuiltInCategory, 'OST_Dimensions'):
        collect_category(BuiltInCategory.OST_Dimensions, "DIM")

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
            collect_category(getattr(BuiltInCategory, cat_name), "TAG")

    # REGION: FilledRegion
    if hasattr(BuiltInCategory, 'OST_FilledRegion'):
        collect_category(BuiltInCategory.OST_FilledRegion, "REGION")

    # LINES: Detail curves
    if hasattr(BuiltInCategory, 'OST_Lines'):
        collect_category(BuiltInCategory.OST_Lines, "LINES")

    # DETAIL: Detail components (view-specific family instances)
    if hasattr(BuiltInCategory, 'OST_DetailComponents'):
        collect_category(BuiltInCategory.OST_DetailComponents, "DETAIL")

    # KEYNOTES: Handle keynotes specially
    # Keynotes can be Material Element Keynotes (TAG) or User Keynotes (TEXT)
    if hasattr(BuiltInCategory, 'OST_KeynoteTags'):
        try:
            collector = FilteredElementCollector(doc, view.Id)
            collector.OfCategory(BuiltInCategory.OST_KeynoteTags).WhereElementIsNotElementType()

            for elem in collector:
                bbox = elem.get_BoundingBox(view)
                if bbox is not None:
                    # Classify keynote as TAG or TEXT based on type
                    anno_type = classify_keynote(elem)
                    annotations.append((elem, anno_type))
        except:
            pass

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
        ✔ Matches SSM exporter classification logic
    """
    from Autodesk.Revit.DB import BuiltInCategory

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

        # LINES: Detail curves
        if hasattr(BuiltInCategory, 'OST_Lines'):
            if cat_id == int(BuiltInCategory.OST_Lines):
                return "LINES"

        # DETAIL: Detail components
        if hasattr(BuiltInCategory, 'OST_DetailComponents'):
            if cat_id == int(BuiltInCategory.OST_DetailComponents):
                return "DETAIL"

        # KEYNOTES: Handle specially
        if hasattr(BuiltInCategory, 'OST_KeynoteTags'):
            if cat_id == int(BuiltInCategory.OST_KeynoteTags):
                return classify_keynote(elem)

    except:
        pass

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
            except:
                pass

        # Default to TAG for structured keynotes
        return "TAG"

    except:
        # If we can't determine, default to TAG
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
    except:
        return None


def rasterize_annotations(doc, view, raster, cfg):
    """Rasterize 2D annotations to anno_key layer.

    For each annotation:
        1. Get bounding box in view coordinates
        2. Project to cell rectangle
        3. Fill cells in anno_key with metadata index
        4. Track metadata in anno_meta

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (has anno_key, anno_meta arrays)
        cfg: Config (cell size, etc.)

    Returns:
        None (modifies raster in-place)

    Commentary:
        ✔ Rasterizes bounding boxes (not detailed geometry)
        ✔ Each annotation gets unique index in anno_meta
        ✔ anno_key cells point to anno_meta index
        ✔ anno_over_model computed later during finalization
        ✔ No occlusion handling (annotations are always visible)
    """
    # Collect all annotations
    annotations = collect_2d_annotations(doc, view)

    if not annotations:
        # No annotations to rasterize
        return

    # Get view basis from raster (stored during init)
    if hasattr(raster, 'view_basis'):
        vb = raster.view_basis
    else:
        # Fallback: compute view basis from view
        from vop_interwoven.revit.view_basis import make_view_basis
        vb = make_view_basis(view)

    # Rasterize each annotation
    for elem, anno_type in annotations:
        # Get bounding box
        bbox = get_annotation_bbox(elem, view)
        if bbox is None:
            continue

        # Project bbox to cell rectangle
        try:
            # Project bbox to cell rect
            cell_rect = _project_element_bbox_to_cell_rect_for_anno(
                bbox, vb, raster
            )

            if cell_rect is None:
                continue

            # Get metadata index for this annotation
            anno_idx = len(raster.anno_meta)

            # Store metadata
            metadata = {
                "type": anno_type,
                "element_id": elem.Id.IntegerValue,
                "bbox_min": (bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
                "bbox_max": (bbox.Max.X, bbox.Max.Y, bbox.Max.Z),
            }
            raster.anno_meta.append(metadata)

            # Rasterize cells
            x0 = max(0, cell_rect.x0)
            y0 = max(0, cell_rect.y0)
            x1 = min(raster.W, cell_rect.x1)
            y1 = min(raster.H, cell_rect.y1)

            for cy in range(y0, y1):
                for cx in range(x0, x1):
                    cell_idx = cy * raster.W + cx

                    # Set anno_key to this annotation's metadata index
                    raster.anno_key[cell_idx] = anno_idx

        except Exception as e:
            # Skip annotations that fail to rasterize
            continue


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

        # Project min/max to view space
        min_x_view = min_pt.X
        min_y_view = min_pt.Y
        max_x_view = max_pt.X
        max_y_view = max_pt.Y

        # Convert to cell coordinates relative to view bounds
        x0_cell = int((min_x_view - bounds.min_x) / cell_size)
        y0_cell = int((min_y_view - bounds.min_y) / cell_size)
        x1_cell = int((max_x_view - bounds.min_x) / cell_size) + 1
        y1_cell = int((max_y_view - bounds.min_y) / cell_size) + 1

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

    except:
        return None
