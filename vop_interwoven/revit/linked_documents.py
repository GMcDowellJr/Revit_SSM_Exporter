"""
Linked document support for VOP interwoven pipeline.

Provides functions to collect elements from:
- Linked Revit files (RVT links)
- DWG/DXF imports

Handles spatial clipping, transform application, and visibility filtering.
"""

import logging


# Module-level logger
logger = logging.getLogger(__name__)


class LinkedElementProxy:
    """Lightweight proxy for linked/imported elements.

    Exposes minimal surface needed by the pipeline:
    - Id: Element ID (from link doc)
    - Category: Element category
    - LinkInstanceId: Owning link instance ID
    - get_BoundingBox(view): Returns host-space bbox
    - get_Geometry(options): Returns link-space geometry
    - transform: Link transform (link → host)
    - doc_key: Document key for metadata tracking
    """

    __slots__ = ("_bb", "_elem", "_link_trf", "Id", "Category",
                 "LinkInstanceId", "transform", "doc_key")

    def __init__(self, element, link_inst, host_min, host_max, link_trf, doc_key):
        """Initialize proxy with host-space bbox and link transform.

        Args:
            element: Element from link document
            link_inst: RevitLinkInstance or ImportInstance
            host_min: BBox minimum in host coordinates (XYZ)
            host_max: BBox maximum in host coordinates (XYZ)
            link_trf: Transform from link to host
            doc_key: Document key string (e.g., link title or import name)
        """
        # Create simple bbox wrapper
        class _BB:
            __slots__ = ("Min", "Max")
            def __init__(self, mn, mx):
                self.Min = mn
                self.Max = mx

        self._bb = _BB(host_min, host_max)
        self._elem = element
        self._link_trf = link_trf
        self.Id = getattr(element, "Id", None)
        self.Category = getattr(element, "Category", None)
        self.LinkInstanceId = getattr(link_inst, "Id", None)
        self.transform = link_trf
        self.doc_key = doc_key

    def get_BoundingBox(self, view):
        """Return host-space bounding box (view parameter ignored)."""
        return self._bb

    def get_Geometry(self, options):
        """Return element geometry in link-space coordinates.

        Note: Geometry is in link coordinates; apply self.transform to get host coords.
        """
        if self._elem is None:
            return None
        try:
            return self._elem.get_Geometry(options)
        except Exception:
            return None


def collect_all_linked_elements(doc, view, cfg):
    """Collect all elements from linked RVT files and DWG imports.

    Args:
        doc: Revit Document
        view: Revit View
        cfg: Config object with linked document settings

    Returns:
        List of LinkedElementProxy objects in host-space coordinates

    Commentary:
        ✔ Collects from both RVT links and DWG/DXF imports
        ✔ Applies spatial clipping based on view crop box
        ✔ Transforms all geometry to host coordinates
        ✔ Respects visibility settings
        ✘ Does NOT collect linked 2D elements (annotation from links)
    """
    elements = []

    # Check if linked document collection is enabled
    if not getattr(cfg, 'include_linked_rvt', False) and not getattr(cfg, 'include_dwg_imports', False):
        logger.debug("Linked document collection disabled in config")
        return elements

    # Collect from RVT links
    if getattr(cfg, 'include_linked_rvt', False):
        try:
            rvt_elements = _collect_from_revit_links(doc, view, cfg)
            elements.extend(rvt_elements)
            logger.info(f"Collected {len(rvt_elements)} elements from RVT links")
        except Exception as e:
            logger.error(f"Error collecting from RVT links: {e}", exc_info=True)

    # Collect from DWG/DXF imports
    if getattr(cfg, 'include_dwg_imports', False):
        try:
            dwg_elements = _collect_from_dwg_imports(doc, view, cfg)
            elements.extend(dwg_elements)
            logger.info(f"Collected {len(dwg_elements)} elements from DWG imports")
        except Exception as e:
            logger.error(f"Error collecting from DWG imports: {e}", exc_info=True)

    return elements


def _collect_from_revit_links(doc, view, cfg):
    """Collect elements from linked Revit files.

    Args:
        doc: Host Revit Document
        view: Host View
        cfg: Config object

    Returns:
        List of LinkedElementProxy objects

    Process:
        1. Find all RevitLinkInstance elements in view
        2. For each link, get linked document
        3. Build clip volume from host view
        4. Transform clip volume to link space
        5. Collect elements intersecting clip volume
        6. Create proxies with host-space bboxes
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        RevitLinkInstance,
        BuiltInCategory,
        CategoryType,
        Outline,
        BoundingBoxIntersectsFilter,
        XYZ,
    )

    proxies = []

    # Get all link instances visible in view
    try:
        collector = FilteredElementCollector(doc, view.Id)
        link_instances = collector.OfClass(RevitLinkInstance).ToElements()
    except Exception as e:
        logger.warning(f"Failed to collect RevitLinkInstance elements: {e}")
        return proxies

    if not link_instances:
        logger.debug("No RVT links found in view")
        return proxies

    logger.info(f"Found {len(link_instances)} RVT link instance(s) in view")

    # Build host view clip volume for spatial filtering
    clip_volume = _build_clip_volume(view, cfg)

    # Get host visible categories (for By Host View filtering)
    host_visible_cats = _get_host_visible_model_categories(view)

    # Process each link instance
    for link_inst in link_instances:
        try:
            # Get linked document
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                logger.warning(f"Link instance {link_inst.Id} has no linked document (unloaded?)")
                continue

            link_title = link_doc.Title
            logger.debug(f"Processing RVT link: {link_title}")

            # Get link transform
            link_trf = link_inst.GetTransform()
            if link_trf is None:
                logger.warning(f"Link {link_title} has no transform")
                continue

            # Collect elements from link with spatial clipping
            link_proxies = _collect_link_elements_with_clipping(
                link_inst=link_inst,
                link_doc=link_doc,
                link_trf=link_trf,
                view=view,
                clip_volume=clip_volume,
                host_visible_cats=host_visible_cats,
                doc_key=link_title,
                cfg=cfg
            )

            proxies.extend(link_proxies)
            logger.info(f"Collected {len(link_proxies)} elements from link '{link_title}'")

        except Exception as e:
            logger.error(f"Error processing RVT link instance {link_inst.Id}: {e}", exc_info=True)
            continue

    return proxies


def _collect_from_dwg_imports(doc, view, cfg):
    """Collect elements from DWG/DXF imports.

    Args:
        doc: Revit Document
        view: Revit View
        cfg: Config object

    Returns:
        List of LinkedElementProxy objects

    Commentary:
        DWG imports appear as ImportInstance elements with geometry.
        Only model-level (non-view-specific) imports contribute to 3D occupancy.
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        ImportInstance,
        XYZ,
    )

    proxies = []

    try:
        # Collect ImportInstance elements in view
        collector = FilteredElementCollector(doc, view.Id)
        import_instances = collector.OfClass(ImportInstance).ToElements()
    except Exception as e:
        logger.warning(f"Failed to collect ImportInstance elements: {e}")
        return proxies

    if not import_instances:
        logger.debug("No DWG/DXF imports found in view")
        return proxies

    logger.info(f"Found {len(import_instances)} import instance(s) in view")

    # Process each import
    for import_inst in import_instances:
        try:
            # Only include model-level imports (not view-specific)
            is_view_specific = getattr(import_inst, "ViewSpecific", False)
            if is_view_specific:
                logger.debug(f"Skipping view-specific import {import_inst.Id}")
                continue

            # Get import geometry bbox
            bbox = import_inst.get_BoundingBox(None)
            if bbox is None or bbox.Min is None or bbox.Max is None:
                logger.debug(f"Import {import_inst.Id} has no valid bbox")
                continue

            # Get import name/path for doc_key
            try:
                # Try to get CAD link type for name
                type_id = import_inst.GetTypeId()
                import_type = doc.GetElement(type_id)
                doc_key = getattr(import_type, "Name", "DWG_Import")
            except Exception:
                doc_key = "DWG_Import"

            # ImportInstance geometry is already in host coordinates
            # Create identity transform
            from Autodesk.Revit.DB import Transform
            identity_trf = Transform.Identity

            # Create proxy with host-space bbox
            proxy = LinkedElementProxy(
                element=import_inst,
                link_inst=import_inst,
                host_min=bbox.Min,
                host_max=bbox.Max,
                link_trf=identity_trf,
                doc_key=doc_key
            )

            proxies.append(proxy)

        except Exception as e:
            logger.error(f"Error processing import instance {import_inst.Id}: {e}", exc_info=True)
            continue

    logger.info(f"Collected {len(proxies)} DWG/DXF import elements")
    return proxies


def _collect_link_elements_with_clipping(link_inst, link_doc, link_trf, view,
                                          clip_volume, host_visible_cats, doc_key, cfg):
    """Collect elements from a link document with spatial clipping.

    Args:
        link_inst: RevitLinkInstance
        link_doc: Linked Document
        link_trf: Transform (link → host)
        view: Host view
        clip_volume: Clip volume dict from _build_clip_volume
        host_visible_cats: Set of visible category IDs in host view
        doc_key: Document key for metadata
        cfg: Config object

    Returns:
        List of LinkedElementProxy objects
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        Outline,
        BoundingBoxIntersectsFilter,
        XYZ,
        CategoryType,
    )

    proxies = []

    # Check if we have a valid clip volume
    if clip_volume is None or not clip_volume.get("is_valid", False):
        logger.warning("No valid clip volume; skipping spatial filtering")
        # Fall back to simple view-scoped collection
        try:
            collector = (
                FilteredElementCollector(link_doc)
                .WhereElementIsNotElementType()
            )
        except Exception as e:
            logger.error(f"Failed to create collector for link doc: {e}")
            return proxies
    else:
        # Build spatial filter in link coordinates
        corners_host = clip_volume.get("corners_host")
        if not corners_host or len(corners_host) < 8:
            logger.warning("Clip volume missing corners")
            return proxies

        # Transform clip volume corners to link space
        try:
            inv_trf = link_trf.Inverse
        except Exception as e:
            logger.error(f"Failed to invert link transform: {e}")
            return proxies

        corners_link = [inv_trf.OfPoint(p) for p in corners_host]

        # Build AABB in link space (broad-phase filter)
        xs = [p.X for p in corners_link]
        ys = [p.Y for p in corners_link]
        zs = [p.Z for p in corners_link]

        min_link = XYZ(min(xs), min(ys), min(zs))
        max_link = XYZ(max(xs), max(ys), max(zs))

        try:
            outline = Outline(min_link, max_link)
            bbox_filter = BoundingBoxIntersectsFilter(outline)

            collector = (
                FilteredElementCollector(link_doc)
                .WhereElementIsNotElementType()
                .WherePasses(bbox_filter)
            )
        except Exception as e:
            logger.error(f"Failed to create spatial filter: {e}")
            # Fall back to unfiltered collection
            collector = (
                FilteredElementCollector(link_doc)
                .WhereElementIsNotElementType()
            )

    # Get excluded category IDs (navigation noise + model suppression)
    excluded_cat_ids = _get_excluded_3d_category_ids(link_doc)

    # Collect and build proxies
    for elem in collector:
        try:
            # Skip nested links and imports
            from Autodesk.Revit.DB import RevitLinkInstance, ImportInstance
            if isinstance(elem, RevitLinkInstance) or isinstance(elem, ImportInstance):
                continue

            cat = elem.Category
            if cat is None:
                continue

            cat_id_val = cat.Id.IntegerValue

            # Global 3D exclusion (rooms, areas, grids, etc.)
            if cat_id_val in excluded_cat_ids:
                continue

            # Only model categories
            if cat.CategoryType != CategoryType.Model:
                continue

            # Host VG filter (if By Host View mode)
            if host_visible_cats is not None:
                if cat_id_val not in host_visible_cats:
                    continue

            # Get link-space bbox
            bbox_link = elem.get_BoundingBox(None)
            if bbox_link is None or bbox_link.Min is None or bbox_link.Max is None:
                continue

            # Transform bbox to host space
            host_min, host_max = _transform_bbox_to_host(bbox_link, link_trf)
            if host_min is None or host_max is None:
                continue

            # Create proxy
            proxy = LinkedElementProxy(
                element=elem,
                link_inst=link_inst,
                host_min=host_min,
                host_max=host_max,
                link_trf=link_trf,
                doc_key=doc_key
            )

            proxies.append(proxy)

        except Exception as e:
            logger.debug(f"Error processing link element {getattr(elem, 'Id', '?')}: {e}")
            continue

    return proxies


def _build_clip_volume(view, cfg):
    """Build clip volume for spatial filtering from view crop box.

    Args:
        view: Revit View
        cfg: Config object

    Returns:
        dict with keys:
            is_valid: bool
            kind: "plan"|"vertical"|"drafting"
            corners_host: [XYZ]*8 (host model coords) or None
            depth_mode: "model_z"|"view_dir"|"none"
            z_min, z_max: float or None

    Commentary:
        For plans: Uses ViewRange to determine Z slab
        For sections/elevations: Uses far clip distance along view direction
        For drafting: No 3D clip (XY only)
    """
    from Autodesk.Revit.DB import ViewType, XYZ

    clip = {
        "is_valid": False,
        "kind": "drafting",
        "corners_host": None,
        "depth_mode": "none",
        "z_min": None,
        "z_max": None,
    }

    # Drafting views: XY only (no 3D elements)
    try:
        if view.ViewType == ViewType.DraftingView:
            clip["is_valid"] = True
            clip["kind"] = "drafting"
            return clip
    except Exception:
        pass

    # Need CropBox for model views
    try:
        crop_box = view.CropBox
    except Exception:
        crop_box = None

    if crop_box is None or crop_box.Min is None or crop_box.Max is None:
        logger.warning(f"View {view.Name} has no valid CropBox")
        return clip

    # Plans/RCP: Vertical range from ViewRange
    z_min, z_max = _get_plan_view_vertical_range(view, cfg)
    if z_min is not None and z_max is not None:
        # Build prism corners in host model coords
        corners_host = _build_crop_prism_corners(view, z_min, z_max)
        if corners_host:
            clip["is_valid"] = True
            clip["kind"] = "plan"
            clip["depth_mode"] = "model_z"
            clip["corners_host"] = corners_host
            clip["z_min"] = z_min
            clip["z_max"] = z_max
        return clip

    # Vertical views (sections/elevations): Depth from far clip
    try:
        trf = crop_box.Transform
    except Exception:
        trf = None

    if trf is None:
        logger.warning(f"View {view.Name} CropBox has no Transform")
        return clip

    # Local crop extents
    try:
        min_local = crop_box.Min
        max_local = crop_box.Max
        min_x, max_x = min_local.X, max_local.X
        min_y, max_y = min_local.Y, max_local.Y
        near_z = min_local.Z
        far_z_default = max_local.Z
    except Exception:
        return clip

    # Try to get far clip distance
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p_far = view.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR)
        far_dist = p_far.AsDouble() if p_far else None
    except Exception:
        far_dist = None

    # Determine local Z span
    if far_dist is not None and far_dist > 0:
        z0 = near_z
        z1 = near_z + far_dist
    else:
        z0 = near_z
        z1 = far_z_default

    # Build 8 corners in local crop coords, transform to host
    try:
        local_corners = [
            XYZ(min_x, min_y, z0), XYZ(min_x, min_y, z1),
            XYZ(min_x, max_y, z0), XYZ(min_x, max_y, z1),
            XYZ(max_x, min_y, z0), XYZ(max_x, min_y, z1),
            XYZ(max_x, max_y, z0), XYZ(max_x, max_y, z1),
        ]
        corners_host = [trf.OfPoint(p) for p in local_corners]
    except Exception as e:
        logger.error(f"Failed to build vertical clip corners: {e}")
        return clip

    clip["is_valid"] = True
    clip["kind"] = "vertical"
    clip["depth_mode"] = "view_dir"
    clip["corners_host"] = corners_host
    return clip


def _get_plan_view_vertical_range(view, cfg):
    """Get vertical Z range for plan/ceiling/area views.

    Args:
        view: Revit View
        cfg: Config object

    Returns:
        (z_min, z_max) in model coordinates, or (None, None) if not a plan view

    Uses ViewRange to determine the effective vertical clip slab.
    """
    from Autodesk.Revit.DB import ViewType

    try:
        vtype = view.ViewType
    except Exception:
        return (None, None)

    if vtype not in (ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.AreaPlan):
        return (None, None)

    try:
        from Autodesk.Revit.DB import PlanViewPlane
        vr = view.GetViewRange()
    except Exception:
        return (None, None)

    if vr is None:
        return (None, None)

    def _plane_z(plane):
        try:
            lvl_id = vr.GetLevelId(plane)
            if lvl_id is None or lvl_id.IntegerValue == -1:
                return None
            lvl = view.Document.GetElement(lvl_id)
            base_z = lvl.Elevation
            off = vr.GetOffset(plane)
            return base_z + off
        except Exception:
            return None

    top_z = _plane_z(PlanViewPlane.TopClipPlane)
    cut_z = _plane_z(PlanViewPlane.CutPlane)
    bottom_z = _plane_z(PlanViewPlane.BottomClipPlane)
    depth_z = _plane_z(PlanViewPlane.ViewDepthPlane)

    zs = [z for z in (top_z, cut_z, bottom_z, depth_z) if z is not None]
    if not zs:
        return (None, None)

    # Conservative: use min/max of all planes
    z_min = min(zs)
    z_max = max(zs)

    return (z_min, z_max)


def _build_crop_prism_corners(view, z_min, z_max):
    """Build 8 prism corners from view CropBox XY and Z range.

    Args:
        view: Revit View
        z_min: Bottom Z in model coords
        z_max: Top Z in model coords

    Returns:
        List of 8 XYZ corners in host model coordinates
    """
    from Autodesk.Revit.DB import XYZ

    try:
        crop_box = view.CropBox
        trf = crop_box.Transform
        mn = crop_box.Min
        mx = crop_box.Max
    except Exception:
        return None

    # Local XY corners (Z ignored here)
    xs = (mn.X, mx.X)
    ys = (mn.Y, mx.Y)

    if trf is None:
        # Assume identity (already in model coords)
        p00 = XYZ(xs[0], ys[0], 0)
        p01 = XYZ(xs[0], ys[1], 0)
        p10 = XYZ(xs[1], ys[0], 0)
        p11 = XYZ(xs[1], ys[1], 0)
    else:
        # Transform local XY to model coords
        p00 = trf.OfPoint(XYZ(xs[0], ys[0], 0))
        p01 = trf.OfPoint(XYZ(xs[0], ys[1], 0))
        p10 = trf.OfPoint(XYZ(xs[1], ys[0], 0))
        p11 = trf.OfPoint(XYZ(xs[1], ys[1], 0))

    # Override Z with model Z range
    corners = [
        XYZ(p00.X, p00.Y, z_min), XYZ(p00.X, p00.Y, z_max),
        XYZ(p01.X, p01.Y, z_min), XYZ(p01.X, p01.Y, z_max),
        XYZ(p10.X, p10.Y, z_min), XYZ(p10.X, p10.Y, z_max),
        XYZ(p11.X, p11.Y, z_min), XYZ(p11.X, p11.Y, z_max),
    ]

    return corners


def _get_host_visible_model_categories(view):
    """Get set of model category IDs visible in host view.

    Args:
        view: Revit View

    Returns:
        Set of category integer IDs, or None if unavailable

    Uses view.GetCategoryHidden to check visibility per category.
    """
    from Autodesk.Revit.DB import CategoryType

    doc = view.Document
    if doc is None:
        return None

    try:
        categories = doc.Settings.Categories
    except Exception:
        return None

    visible_ids = set()

    for cat in categories:
        if cat is None:
            continue

        try:
            # Only model categories
            if cat.CategoryType != CategoryType.Model:
                continue

            cat_id_val = cat.Id.IntegerValue

            # Check if hidden in view
            is_hidden = view.GetCategoryHidden(cat.Id)
            if not is_hidden:
                visible_ids.add(cat_id_val)
        except Exception:
            continue

    return visible_ids if visible_ids else None


def _get_excluded_3d_category_ids(doc):
    """Get set of category IDs to exclude from 3D collection.

    Excludes:
    - Navigation/annotation mechanics (grids, levels, section heads, cameras)
    - Non-physical model elements (rooms, areas, spaces)
    - Detail components, model lines, point clouds

    Args:
        doc: Revit Document (link doc or host doc)

    Returns:
        Set of category integer IDs
    """
    from Autodesk.Revit.DB import BuiltInCategory

    excluded = set()

    # Navigation & annotation mechanics
    nav_names = [
        "OST_Grids", "OST_GridHeads", "OST_Levels", "OST_LevelHeads",
        "OST_SectionHeads", "OST_SectionMarks", "OST_ElevationMarks",
        "OST_CalloutHeads", "OST_ReferenceViewer", "OST_Viewers",
        "OST_Cameras", "OST_SunPath", "OST_SectionBox", "OST_AdaptivePoints",
        "OST_Reveals",
    ]

    # Non-physical model elements
    non_physical_names = [
        "OST_Rooms", "OST_Areas", "OST_MEPSpaces",
        "OST_DetailComponents", "OST_Lines", "OST_PointClouds",
    ]

    all_names = nav_names + non_physical_names

    for name in all_names:
        try:
            bic = getattr(BuiltInCategory, name, None)
            if bic is not None:
                # Get category from doc to get proper ID
                cat = doc.Settings.Categories.get_Item(bic)
                if cat is not None:
                    excluded.add(cat.Id.IntegerValue)
        except Exception:
            continue

    return excluded


def _transform_bbox_to_host(bbox_link, link_trf):
    """Transform link-space bounding box to host-space AABB.

    Args:
        bbox_link: BoundingBoxXYZ in link coordinates
        link_trf: Transform (link → host)

    Returns:
        (host_min, host_max) as XYZ objects, or (None, None) on error
    """
    from Autodesk.Revit.DB import XYZ

    try:
        mn = bbox_link.Min
        mx = bbox_link.Max
    except Exception:
        return None, None

    # Transform all 8 corners to host space
    try:
        corners = [
            XYZ(mn.X, mn.Y, mn.Z), XYZ(mn.X, mn.Y, mx.Z),
            XYZ(mn.X, mx.Y, mn.Z), XYZ(mn.X, mx.Y, mx.Z),
            XYZ(mx.X, mn.Y, mn.Z), XYZ(mx.X, mn.Y, mx.Z),
            XYZ(mx.X, mx.Y, mn.Z), XYZ(mx.X, mx.Y, mx.Z),
        ]
        host_corners = [link_trf.OfPoint(p) for p in corners]
    except Exception:
        return None, None

    # Compute AABB in host space
    xs = [p.X for p in host_corners]
    ys = [p.Y for p in host_corners]
    zs = [p.Z for p in host_corners]

    host_min = XYZ(min(xs), min(ys), min(zs))
    host_max = XYZ(max(xs), max(ys), max(zs))

    return host_min, host_max
