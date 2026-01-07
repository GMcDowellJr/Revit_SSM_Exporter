"""
Linked document support for VOP interwoven pipeline.

Provides functions to collect elements from:
- Linked Revit files (RVT links)
- DWG/DXF imports

Handles spatial clipping, transform application, and visibility filtering.
"""

# Optional Revit API bindings (allow pytest outside Revit)
try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        CategoryType,
        RevitLinkInstance,
        ImportInstance,
    )
except Exception:
    FilteredElementCollector = None
    CategoryType = None
    RevitLinkInstance = None
    ImportInstance = None


# Logging helper for IronPython compatibility (no logging module)
def _log(level, msg):
    """Simple logging function compatible with IronPython."""
    print("[{0}] vop.linked_docs: {1}".format(level, msg))


class LinkedElementProxy:
    """Lightweight proxy for linked/imported elements.

    Exposes minimal surface needed by the pipeline:
    - Id: Element ID (from link doc)
    - Category: Element category
    - LinkInstanceId: Owning link instance ID
    - get_BoundingBox(view): Returns host-space bbox
    - get_Geometry(options): Returns link-space geometry
    - transform: Link transform (link → host)
    - source_type: One of {HOST, LINK, DWG}
    - source_id: Stable unique source identifier (includes instance ID)
    - source_label: Human-friendly label for logging/display
    - doc_key/doc_label: Legacy aliases (deprecated)
    """

    __slots__ = ("_bb", "_elem", "_link_trf", "Id", "Category",
                 "LinkInstanceId", "transform", "source_type", "source_id", "source_label", "doc_key", "doc_label")

    def __init__(self, element, link_inst, host_min, host_max, link_trf, source_type, source_id, source_label=None, doc_key=None, doc_label=None):
        """Initialize proxy with host-space bbox and link transform."""
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

        self.source_type = source_type
        self.source_id = source_id
        self.source_label = source_label if source_label is not None else source_id

        # Legacy aliases (deprecated): keep in sync for downstream callers not yet migrated
        self.doc_key = doc_key if doc_key is not None else source_id
        self.doc_label = doc_label if doc_label is not None else self.source_label

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
        except Exception as e:
            elem_id = getattr(self._elem, 'Id', '?')
            try:
                _log("DEBUG", "Geometry extraction failed for link element {0}: {1}".format(elem_id, e))
            except Exception as log_e:
                print(f"[WARN] revit.linked_documents: _log failed for geometry extraction failure (elem_id={elem_id}) ({type(log_e).__name__}: {log_e})")
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
        _log("DEBUG", "Linked document collection disabled in config")
        return elements

    # Collect from RVT links
    if getattr(cfg, 'include_linked_rvt', False):
        try:
            rvt_elements = _collect_from_revit_links(doc, view, cfg)
            elements.extend(rvt_elements)
            _log("INFO", "Collected {0} elements from RVT links".format(len(rvt_elements)))
        except Exception as e:
            _log("ERROR", "Error collecting from RVT links: {0}".format(e))

    # Collect from DWG/DXF imports
    if getattr(cfg, 'include_dwg_imports', False):
        try:
            dwg_elements = _collect_from_dwg_imports(doc, view, cfg)
            elements.extend(dwg_elements)
            _log("INFO", "Collected {0} elements from DWG imports".format(len(dwg_elements)))
        except Exception as e:
            _log("ERROR", "Error collecting from DWG imports: {0}".format(e))

    return elements


def _has_revit_2024_link_collector(doc, view):
    """Detect if Revit 2024+ FilteredElementCollector(doc, viewId, linkId) is available.

    Uses reflection without executing the overload (no dependency on loaded/valid links).
    """
    try:
        import clr
        import System
        from Autodesk.Revit.DB import FilteredElementCollector, Document, ElementId

        # Robust pythonnet: get the CLR Type directly
        try:
            t = clr.GetClrType(FilteredElementCollector)
        except Exception:
            t = None

        if t is None:
            _log("WARN", "Could not resolve CLR type for FilteredElementCollector; defaulting to legacy path")
            return False

        for c in t.GetConstructors():
            ps = c.GetParameters()
            if ps is None or len(ps) != 3:
                continue
            p0 = ps[0].ParameterType.FullName
            p1 = ps[1].ParameterType.FullName
            p2 = ps[2].ParameterType.FullName
            if p0 == "Autodesk.Revit.DB.Document" and p1 == "Autodesk.Revit.DB.ElementId" and p2 == "Autodesk.Revit.DB.ElementId":

                _log("INFO", "[Revit 2024+] FilteredElementCollector(doc, viewId, linkId) overload DETECTED (reflection)")
                _log("INFO", "[Revit 2024+] Using new collector path for linked elements")
                return True

        _log("INFO", "[Revit <2024] FilteredElementCollector 3-arg overload NOT FOUND (reflection)")
        _log("INFO", "[Revit <2024] Using legacy clip volume path for linked elements")
        return False

    except Exception as e:
        _log("WARN", "Failed to check Revit 2024+ collector availability (reflection): {0}".format(e))
        _log("INFO", "Defaulting to legacy clip volume path for safety")
        return False


def _collect_visible_link_elements_2024_plus(doc, view, link_inst, link_doc, link_trf, cfg, diag=None):
    """Collect visible elements from link using Revit 2024+ collector.

    Args:
        doc: Host Revit Document
        view: Host View
        link_inst: RevitLinkInstance
        link_doc: Linked Document
        link_trf: Transform (link → host)
        cfg: Config object

    Returns:
        Tuple of (proxies list, source_key, source_label)

    Commentary:
        Uses FilteredElementCollector(doc, viewId, linkId) overload added in Revit 2024.
        This directly enumerates elements visible from the link in the host view,
        eliminating the need for bbox clipping approximations.
    """
    
    from .collection_policy import should_include_element, PolicyStats
    
    policy_stats = PolicyStats()
    excluded_by_category = {}

    # Build unique source key (includes instance ID for uniqueness)
    link_inst_id = link_inst.Id.IntegerValue
    try:
        link_doc_uid = link_doc.UniqueId
    except Exception:
        link_doc_uid = link_doc.Title  # Fallback if UniqueId not available

    source_key = "RVT_LINK:{0}:{1}".format(link_doc_uid, link_inst_id)
    source_label = "RVT_LINK:{0}".format(link_doc.Title)

    _log("DEBUG", "Using Revit 2024+ collector for link '{0}'".format(link_doc.Title))

    proxies = []
    
    if FilteredElementCollector is None:
        raise RuntimeError("FilteredElementCollector unavailable (not running inside Revit/Dynamo)")
    
    # Diagnostics: count what we collect, what we skip, and why.
    fec_total = 0
    candidates = 0
    created = 0
    skip = {
        "skip_nested_link": 0,
        "skip_import_instance": 0,
        "skip_no_category": 0,
        "skip_excluded_category": 0,
        "skip_non_model_categorytype": 0,
        "skip_no_bbox": 0,
        "skip_bad_bbox": 0,
        "skip_transform_failed": 0,
        "skip_exception": 0,
        "skip_excluded_by_policy": 0,
    }
    by_category = {}

    try:
        # Revit 2024+ overload: collect visible elements from link instance in view
        fec = FilteredElementCollector(doc, view.Id, link_inst.Id)
        fec.WhereElementIsNotElementType()
        
        # Apply same category hygiene as legacy clipping path (exclude rooms/areas/grids etc.)
        excluded_cat_ids = _get_excluded_3d_category_ids(link_doc)

        for elem in fec:
            fec_total += 1
            try:
                # Skip nested links and imports (avoid recursion/noise)
                # NOTE: In pytest (outside Revit), these symbols may be None due to optional imports.
                if RevitLinkInstance is not None and isinstance(elem, RevitLinkInstance):
                    skip["skip_nested_link"] += 1
                    continue
                if ImportInstance is not None and isinstance(elem, ImportInstance):
                    skip["skip_import_instance"] += 1
                    continue

                cat = elem.Category
                if cat is None:
                    skip["skip_no_category"] += 1
                    continue

                include, pol_reason, pol_cat = should_include_element(
                    elem=elem,
                    doc=link_doc,
                    source_type="LINK",
                    stats=policy_stats,
                )
                if not include:
                    skip["skip_excluded_by_policy"] += 1
                    excluded_by_category[pol_cat] = excluded_by_category.get(pol_cat, 0) + 1
                    continue

                cat_id_val = cat.Id.IntegerValue

                # Global 3D exclusion (rooms, areas, grids, etc.)
                if cat_id_val in excluded_cat_ids:
                    skip["skip_excluded_category"] += 1
                    continue

                # Only model categories (ignore annotations, analytical, etc.)
                if cat.CategoryType != CategoryType.Model:
                    skip["skip_non_model_categorytype"] += 1
                    continue

                candidates += 1

                # Get element bbox in link space
                bbox_link = elem.get_BoundingBox(None)
                if bbox_link is None:
                    skip["skip_no_bbox"] += 1
                    continue
                if bbox_link.Min is None or bbox_link.Max is None:
                    skip["skip_bad_bbox"] += 1
                    continue

                # Transform bbox to host space
                host_min, host_max = _transform_bbox_to_host(bbox_link, link_trf)
                if host_min is None or host_max is None:
                    skip["skip_transform_failed"] += 1
                    continue

                # Create proxy
                proxy = LinkedElementProxy(
                    element=elem,
                    link_inst=link_inst,
                    host_min=host_min,
                    host_max=host_max,
                    link_trf=link_trf,
                    source_type="LINK",
                    source_id=source_key,
                    source_label=source_label,
                    doc_key=source_key,
                    doc_label=source_label,
                )

                proxies.append(proxy)
                created += 1

                # Category histogram (created only)
                try:
                    cname = cat.Name if cat else "?"
                except Exception:
                    cname = "?"
                by_category[cname] = by_category.get(cname, 0) + 1

            except Exception as e:
                skip["skip_exception"] += 1
                _log("DEBUG", "Error processing link element {0}: {1}".format(getattr(elem, 'Id', '?'), e))
                continue

    except Exception as e:
        _log("ERROR", "Revit 2024+ collector failed for link '{0}': {1}".format(link_doc.Title, e))
        return [], source_key, source_label

    # Summarize collection outcome (high signal, low spam)
    _log(
        "INFO",
        "Revit 2024+ link collector summary for '{0}': fec_total={1}, model_candidates={2}, proxies_created={3}".format(
            link_doc.Title, fec_total, candidates, created
        )
    )

    # Skip reason breakdown (ordered for readability)
    _log(
        "INFO",
        "Revit 2024+ link skips for '{0}': {1}".format(
            link_doc.Title,
            ", ".join(["{0}={1}".format(k, skip[k]) for k in sorted(skip.keys())])
        )
    )

    # Top categories (created proxies only)
    try:
        top = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)[:10]
        _log(
            "INFO",
            "Revit 2024+ link top categories for '{0}': {1}".format(
                link_doc.Title, ", ".join(["{0}={1}".format(n, c) for n, c in top])
            )
        )
    except Exception as e:
        _log("DEBUG", "Failed to summarize category histogram: {0}".format(e))

    if diag is not None and policy_stats.excluded_total > 0:
        diag.info(
            phase="collection",
            callsite="linked_documents._collect_visible_link_elements_2024_plus.policy",
            message="Link elements excluded due to category policy",
            view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
            extra={
                "source_key": source_key,
                "link_title": getattr(link_doc, "Title", ""),
                "seen_total": policy_stats.seen_total,
                "included_total": policy_stats.included_total,
                "excluded_total": policy_stats.excluded_total,
                "excluded_by_reason": policy_stats.excluded_by_reason,
                "excluded_by_category": excluded_by_category,
            },
        )

    return proxies, source_key, source_label


def _collect_from_revit_links(doc, view, cfg):
    """Collect elements from linked Revit files.

    Args:
        doc: Host Revit Document
        view: Host View
        cfg: Config object

    Returns:
        List of LinkedElementProxy objects

    Process (Revit 2024+):
        1. Find all RevitLinkInstance elements in view
        2. For each link, use FilteredElementCollector(doc, viewId, linkId)
        3. Create proxies with unique source keys

    Process (Revit < 2024):
        1. Find all RevitLinkInstance elements in view
        2. Build clip volume from host view
        3. Transform clip volume to link space
        4. Collect elements intersecting clip volume
        5. Create proxies with host-space bboxes
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
        _log("WARN", "Failed to collect RevitLinkInstance elements: {0}".format(e))
        return proxies

    if not link_instances:
        _log("DEBUG", "No RVT links found in view")
        return proxies

    _log("INFO", "Found {0} RVT link instance(s) in view".format(len(link_instances)))

    # Detect if Revit 2024+ collector is available
    use_2024_collector = _has_revit_2024_link_collector(doc, view)

    # Build clip volume for fallback (Revit < 2024)
    clip_volume = None
    host_visible_cats = None
    if not use_2024_collector:
        clip_volume = _build_clip_volume(view, cfg)
        host_visible_cats = _get_host_visible_model_categories(view)

    # Process each link instance
    for link_inst in link_instances:
        try:
            # Get linked document
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                _log("WARN", "Link instance {0} has no linked document (unloaded?)".format(link_inst.Id))
                continue

            link_title = link_doc.Title
            _log("DEBUG", "Processing RVT link: {0}".format(link_title))

            # Get link transform
            try:
                link_trf = link_inst.GetTotalTransform()
            except Exception:
                link_trf = link_inst.GetTransform()
            if link_trf is None:
                _log("WARN", "Link {0} has no transform".format(link_title))
                continue

            # Try Revit 2024+ collector first
            link_proxies = []
            if use_2024_collector:
                link_proxies, source_key, source_label = _collect_visible_link_elements_2024_plus(
                    doc, view, link_inst, link_doc, link_trf, cfg
                )
            else:
                # Fallback: Use clip volume approach for Revit < 2024
                # Build unique source key even for older versions
                link_inst_id = link_inst.Id.IntegerValue
                try:
                    link_doc_uid = link_doc.UniqueId
                except Exception:
                    link_doc_uid = link_title

                source_key = "RVT_LINK:{0}:{1}".format(link_doc_uid, link_inst_id)
                source_label = "RVT_LINK:{0}".format(link_title)

                link_proxies = _collect_link_elements_with_clipping(
                    link_inst=link_inst,
                    link_doc=link_doc,
                    link_trf=link_trf,
                    view=view,
                    clip_volume=clip_volume,
                    host_visible_cats=host_visible_cats,
                    doc_key=source_key,
                    doc_label=source_label,
                    cfg=cfg
                )

            proxies.extend(link_proxies)
            _log("INFO", "Collected {0} elements from link '{1}'".format(len(link_proxies), link_title))

        except Exception as e:
            _log("ERROR", "Error processing RVT link instance {0}: {1}".format(link_inst.Id, e))
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
        _log("WARN", "Failed to collect ImportInstance elements: {0}".format(e))
        return proxies

    if not import_instances:
        _log("DEBUG", "No DWG/DXF imports found in view")
        return proxies

    _log("INFO", "Found {0} import instance(s) in view".format(len(import_instances)))

    # Process each import
    for import_inst in import_instances:
        try:
            # Only include model-level imports (not view-specific)
            is_view_specific = getattr(import_inst, "ViewSpecific", False)
            if is_view_specific:
                _log("DEBUG", "Skipping view-specific import {0}".format(import_inst.Id))
                continue

            # Get import geometry bbox (prefer view-specific bbox so crop/section is respected)
            bbox = import_inst.get_BoundingBox(view)
            if bbox is None or bbox.Min is None or bbox.Max is None:
                # Fallback to model bbox
                bbox = import_inst.get_BoundingBox(None)
                _log("DEBUG", "Import {0} has no valid bbox".format(import_inst.Id))
                continue

            # Get import name/path for doc_key and label
            try:
                # Try to get CAD link type for name
                type_id = import_inst.GetTypeId()
                import_type = doc.GetElement(type_id)
                import_name = getattr(import_type, "Name", "DWG_Import")
            except Exception:
                import_name = "DWG_Import"

            # Build unique source key (includes instance ID)
            import_inst_id = import_inst.Id.IntegerValue
            source_key = "DWG_IMPORT:{0}:{1}".format(import_name, import_inst_id)
            source_label = "DWG_IMPORT:{0}".format(import_name)

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
                source_type="DWG",
                source_id=source_key,
                source_label=source_label,
                doc_key=source_key,
                doc_label=source_label,
            )

            proxies.append(proxy)

        except Exception as e:
            _log("ERROR", "Error processing import instance {0}: {1}".format(import_inst.Id, e))
            continue

    _log("INFO", "Collected {0} DWG/DXF import elements".format(len(proxies)))
    return proxies


def _collect_link_elements_with_clipping(link_inst, link_doc, link_trf, view,
                                          clip_volume, host_visible_cats, doc_key, doc_label, cfg):
    """Collect elements from a link document with spatial clipping.

    Args:
        link_inst: RevitLinkInstance
        link_doc: Linked Document
        link_trf: Transform (link → host)
        view: Host view
        clip_volume: Clip volume dict from _build_clip_volume
        host_visible_cats: Set of visible category IDs in host view
        doc_key: Unique document key for metadata indexing
        doc_label: Human-friendly document label for logging
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
        _log("WARN", "No valid clip volume; skipping spatial filtering")
        # Fall back to simple view-scoped collection
        try:
            collector = (
                FilteredElementCollector(link_doc)
                .WhereElementIsNotElementType()
            )
        except Exception as e:
            _log("ERROR", "Failed to create collector for link doc: {0}".format(e))
            return proxies
    else:
        # Build spatial filter in link coordinates
        corners_host = clip_volume.get("corners_host")
        if not corners_host or len(corners_host) < 8:
            _log("WARN", "Clip volume missing corners")
            return proxies

        # Transform clip volume corners to link space
        try:
            inv_trf = link_trf.Inverse
        except Exception as e:
            _log("ERROR", "Failed to invert link transform: {0}".format(e))
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
            _log("ERROR", "Failed to create spatial filter: {0}".format(e))
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
                source_type="LINK",
                source_id=doc_key,
                source_label=doc_label,
                doc_key=doc_key,
                doc_label=doc_label,
            )

            proxies.append(proxy)

        except Exception as e:
            _log("DEBUG", "Error processing link element {0}: {1}".format(getattr(elem, 'Id', '?'), e))
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
        _log("WARN", "View {0} has no valid CropBox".format(view.Name))
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
        _log("WARN", "View {0} CropBox has no Transform".format(view.Name))
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
        _log("ERROR", "Failed to build vertical clip corners: {0}".format(e))
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
    """Compatibility wrapper for legacy code; delegates to collection_policy.

    Returns a set of integer CategoryIds resolved in the given doc.
    """
    from .collection_policy import resolve_category_ids, excluded_bic_names_global
    return resolve_category_ids(doc, excluded_bic_names_global())


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
