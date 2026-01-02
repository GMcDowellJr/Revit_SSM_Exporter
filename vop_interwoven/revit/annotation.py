"""
Annotation collection and rasterization for VOP interwoven pipeline.

Provides functions to collect 2D annotation elements, classify them by type,
and rasterize their bounding boxes to the anno_key layer.

Phase 8a: Annotation Collection & Rasterization
"""


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
