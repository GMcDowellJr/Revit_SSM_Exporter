"""
Element collection and visibility filtering for VOP interwoven pipeline.

Provides functions to collect visible elements in a view and check
element visibility according to Revit view settings.
"""


def collect_view_elements(doc, view, raster):
    """Collect all potentially visible elements in view (broad-phase).

    Args:
        doc: Revit Document
        view: Revit View
        raster: ViewRaster (provides bounds_xy for spatial filtering)

    Returns:
        List of Revit elements visible in view

    Commentary:
        ✔ Uses FilteredElementCollector with view.Id scope
        ✔ Filters to 3D model categories (Walls, Floors, etc.)
        ✔ Excludes element types (only instances)
        ✔ Requires valid bounding box
        ✔ Broad-phase only - keeps collection cheap
    """
    from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory

    # Define model categories to collect (3D model + symbolic lines)
    # Using category name strings to handle different Revit versions gracefully
    category_names = [
        # 3D Model elements
        'OST_Walls',
        'OST_Floors',
        'OST_Roofs',
        'OST_Doors',
        'OST_Windows',
        'OST_Columns',
        'OST_StructuralFraming',
        'OST_StructuralColumns',
        'OST_Stairs',
        'OST_Railings',
        'OST_Ceilings',
        'OST_GenericModel',
        'OST_Furniture',
        'OST_Casework',  # Note: some versions use OST_Casework, others OST_CaseworkWall
        'OST_MechanicalEquipment',
        'OST_ElectricalEquipment',
        'OST_PlumbingFixtures',
        'OST_DuctCurves',
        'OST_PipeCurves',
        # Symbolic geometry (contributes to MODEL occupancy, TINY/LINEAR classification)
        'OST_Lines',  # DetailCurves, CurveElements (symbolic lines in families)
        # NOTE: DetailComponents NOT included here - user-placed ones go to ANNOTATION
        # Detail items embedded in model families are part of family geometry (collected via FamilyInstance)
    ]

    # Convert category names to BuiltInCategory enums (skip if not available in this Revit version)
    model_categories = []
    for cat_name in category_names:
        if hasattr(BuiltInCategory, cat_name):
            model_categories.append(getattr(BuiltInCategory, cat_name))

    elements = []

    try:
        # Collect elements visible in view
        for cat in model_categories:
            try:
                collector = FilteredElementCollector(doc, view.Id)
                collector.OfCategory(cat).WhereElementIsNotElementType()

                # Filter to elements with valid bounding boxes
                for elem in collector:
                    # For OST_Lines: Only collect MODEL lines (ViewSpecific=False)
                    # Detail lines (ViewSpecific=True) go to ANNOTATION
                    if cat == getattr(BuiltInCategory, 'OST_Lines', None):
                        try:
                            if bool(getattr(elem, 'ViewSpecific', False)):
                                continue  # Skip detail lines (they're annotations)
                        except:
                            pass

                    bbox = elem.get_BoundingBox(None)  # World coordinates
                    if bbox is not None:
                        elements.append(elem)
            except:
                # Skip categories that cause errors
                continue

    except Exception as e:
        # If collection fails, return empty list (graceful degradation)
        pass

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


def expand_host_link_import_model_elements(doc, view, elements, cfg):
    """Expand element list to include linked/imported model elements.

    Args:
        doc: Revit Document
        view: Revit View
        elements: List of host document elements
        cfg: Config object with linked document settings

    Returns:
        List of element wrappers with transform info:
        Each item: {
            'element': Element,
            'world_transform': Transform (identity for host, link transform for linked),
            'doc_key': str (host doc path or link doc path),
            'link_inst_id': ElementId or None
        }

    Commentary:
        ✔ Includes host elements (identity transform)
        ✔ Expands RevitLinkInstance and ImportInstance to access linked elements
        ✔ Uses linked_documents module for production-ready link handling
    """
    from Autodesk.Revit.DB import Transform
    from .linked_documents import collect_all_linked_elements

    result = []

    # Add host elements with identity transform
    identity_trf = Transform.Identity
    for e in elements:
        result.append(
            {
                "element": e,
                "world_transform": identity_trf,
                "doc_key": "HOST",
                "link_inst_id": None,
            }
        )

    # Collect and add linked/imported elements
    try:
        linked_proxies = collect_all_linked_elements(doc, view, cfg)

        for proxy in linked_proxies:
            result.append(
                {
                    "element": proxy,  # LinkedElementProxy
                    "world_transform": proxy.transform,
                    "doc_key": proxy.doc_key,
                    "link_inst_id": proxy.LinkInstanceId,
                }
            )
    except Exception as e:
        # Log warning but don't fail the whole export
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Failed to collect linked elements: {0}".format(e), exc_info=True)

    return result


def sort_front_to_back(model_elems, view, raster):
    """Sort elements front-to-back by approximate depth.

    Args:
        model_elems: List of element wrappers (from expand_host_link_import_model_elements)
        view: Revit View
        raster: ViewRaster (provides view basis for depth calculation)

    Returns:
        Sorted list (nearest elements first)

    Commentary:
        ✔ Uses bbox minimum depth as sorting key (fast approximation)
        ✔ Front-to-back order enables early-out occlusion testing
        ⚠ This is a placeholder - full implementation requires:
           - View basis extraction
           - BBox min depth calculation in view space
           - Stable sort for deterministic output

    Example:
        >>> # sorted_elems = sorted(
        >>> #     model_elems,
        >>> #     key=lambda item: estimate_nearest_depth_from_bbox(item['element'], item['world_transform'], view, raster)
        >>> # )
    """
    # TODO: Implement front-to-back sorting
    # Placeholder: return unsorted (no-op)
    return model_elems


def estimate_nearest_depth_from_bbox(elem, transform, view, raster):
    """Estimate nearest depth of element from its bounding box.

    Args:
        elem: Revit Element
        transform: World transform (identity for host, link transform for linked)
        view: Revit View
        raster: ViewRaster

    Returns:
        Float depth value (distance from view plane)

    Commentary:
        ✔ Computes minimum depth across all 8 bbox corners
        ✔ Used for front-to-back sorting
        ⚠ Placeholder returns 0.0
    """
    # TODO: Implement depth estimation
    return 0.0


def _project_element_bbox_to_cell_rect(elem, vb, raster):
    """Project element bounding box to cell rectangle (helper for classification).

    Args:
        elem: Revit Element
        vb: ViewBasis for coordinate transformation
        raster: ViewRaster with bounds and cell size

    Returns:
        CellRect in grid coordinates, or None if no bbox

    Commentary:
        ✔ Gets world-space bounding box
        ✔ Transforms min/max corners to view coordinates
        ✔ Projects to cell indices
        ✔ Handles elements outside view bounds (returns None or empty rect)
    """
    from ..core.math_utils import CellRect
    from .view_basis import world_to_view

    # Get world-space bounding box
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return None

    # Transform bbox corners to view space
    min_pt_world = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z)
    max_pt_world = (bbox.Max.X, bbox.Max.Y, bbox.Max.Z)

    min_view = world_to_view(min_pt_world, vb)
    max_view = world_to_view(max_pt_world, vb)

    # Get UV range (ignore W/depth for now)
    u_coords = [min_view[0], max_view[0]]
    v_coords = [min_view[1], max_view[1]]

    u_min = min(u_coords)
    u_max = max(u_coords)
    v_min = min(v_coords)
    v_max = max(v_coords)

    # Convert to cell indices
    # Cell i = floor((u - u_min) / cell_size)
    i_min = int((u_min - raster.bounds.xmin) / raster.cell_size)
    i_max = int((u_max - raster.bounds.xmin) / raster.cell_size)
    j_min = int((v_min - raster.bounds.ymin) / raster.cell_size)
    j_max = int((v_max - raster.bounds.ymin) / raster.cell_size)

    # Clamp to raster bounds
    i_min = max(0, min(i_min, raster.W - 1))
    i_max = max(0, min(i_max, raster.W - 1))
    j_min = max(0, min(j_min, raster.H - 1))
    j_max = max(0, min(j_max, raster.H - 1))

    return CellRect(i_min, j_min, i_max, j_max)
