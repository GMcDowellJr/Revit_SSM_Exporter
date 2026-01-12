"""CSV export functionality for VOP Interwoven pipeline.

Exports pipeline results to CSV format matching the SSM exporter schema for
analytics integration and comparison workflows.
"""

import os
import hashlib
from datetime import datetime

def _is_from_cache(view_result):
    """Return True if the view_result represents any cache hit (legacy or root)."""
    try:
        if view_result.get("from_cache") is True:
            return True

        cache_info = view_result.get("cache", {})
        if isinstance(cache_info, dict):
            # Root cache
            if str(cache_info.get("cache_type", "")).lower() == "root":
                return True

            # Legacy per-view cache
            cache_status = cache_info.get("view_cache", "")
            if "HIT" in str(cache_status).upper():
                return True
    except Exception:
        pass
    return False

def compute_external_cell_metrics(raster):
    """Compute external-cell metrics for VOP CSV.

    Definitions:
        - DWG: cells with any model ink from elements whose source_type == "DWG"
        - RVT: cells with any model ink from elements whose source_type == "LINK" (link only)
        - Any: cells with any external model ink (DWG or LINK)
        - Only: cells with external model ink and NO HOST model ink

    Notes:
        - Uses model ink keys (edge/proxy). Annotation ink is ignored for ext-cell metrics.
        - Tolerates missing element_meta or key arrays by returning zeros.
    """
    def _get_source_type(key_index):
        if not key_index:
            return None
        meta = None
        em = getattr(raster, "element_meta", None)
        if em is None:
            return None
        # element_meta may be list-like (index == key_index) or dict-like.
        try:
            if isinstance(em, dict):
                meta = em.get(key_index, None)
                if meta is None:
                    meta = em.get(str(key_index), None)
            else:
                # Guard: some rasters reserve 0 for "none"
                if 0 <= int(key_index) < len(em):
                    meta = em[int(key_index)]
        except Exception:
            meta = None
        if isinstance(meta, dict):
            return meta.get("source_type")
        return None

    edge_keys = getattr(raster, "model_edge_key", None) or []
    proxy_keys = getattr(raster, "model_proxy_key", None) or []

    n = max(len(edge_keys), len(proxy_keys))
    if n == 0:
        return {"Ext_Cells_Any": 0, "Ext_Cells_Only": 0, "Ext_Cells_DWG": 0, "Ext_Cells_RVT": 0}

    ext_any = ext_only = ext_dwg = ext_rvt = 0

    for i in range(n):
        k_edge = edge_keys[i] if i < len(edge_keys) else 0
        k_proxy = proxy_keys[i] if i < len(proxy_keys) else 0

        src_edge = _get_source_type(k_edge)
        src_proxy = _get_source_type(k_proxy)

        host = (src_edge == "HOST") or (src_proxy == "HOST")
        dwg = (src_edge == "DWG") or (src_proxy == "DWG")
        rvt = (src_edge == "LINK") or (src_proxy == "LINK")

        ext = dwg or rvt
        if ext:
            ext_any += 1
            if not host:
                ext_only += 1
        if dwg:
            ext_dwg += 1
        if rvt:
            ext_rvt += 1

    return {"Ext_Cells_Any": ext_any, "Ext_Cells_Only": ext_only, "Ext_Cells_DWG": ext_dwg, "Ext_Cells_RVT": ext_rvt}


def compute_cell_metrics(raster, model_presence_mode="ink", diag=None):
    """Compute occupancy metrics from raster arrays.

    Args:
        raster: ViewRaster object
        model_presence_mode: "occ" | "edge" | "proxy" | "ink" | "any"
            - "occ"  : occlusion coverage (depth-tested interior fill)
            - "edge" : precise model ink edges only (model_edge_key)
            - "proxy": proxy ink only (model_proxy_key or proxy presence mask)
            - "ink"  : edge OR proxy (DEFAULT; ink-on-screen occupancy)
            - "any"  : occ OR edge OR proxy
        diag: optional diagnostics collector
    """
    total = raster.W * raster.H
    empty = 0
    model_only = 0
    anno_only = 0
    overlap = 0

    mode = (model_presence_mode or "ink").lower()

    # Pull arrays defensively (tests / reconstructed rasters may be partial)
    model_mask = getattr(raster, "model_mask", []) or []
    model_edge_key = getattr(raster, "model_edge_key", []) or []
    model_proxy_key = getattr(raster, "model_proxy_key", []) or []
    model_proxy_mask = getattr(raster, "model_proxy_mask", []) or getattr(raster, "model_proxy_presence", []) or []

    def _has_model(idx):
        if mode == "occ":
            return (idx < len(model_mask)) and bool(model_mask[idx])

        if mode == "edge":
            return (idx < len(model_edge_key)) and (model_edge_key[idx] != -1)

        if mode == "proxy":
            present = False
            if idx < len(model_proxy_key):
                present = present or (model_proxy_key[idx] != -1)
            if idx < len(model_proxy_mask):
                present = present or bool(model_proxy_mask[idx])
            return present

        if mode == "ink":
            present = False
            if idx < len(model_edge_key):
                present = present or (model_edge_key[idx] != -1)
            if idx < len(model_proxy_key):
                present = present or (model_proxy_key[idx] != -1)
            if idx < len(model_proxy_mask):
                present = present or bool(model_proxy_mask[idx])
            return present

        if mode == "any":
            present = False
            if idx < len(model_mask):
                present = present or bool(model_mask[idx])
            if idx < len(model_edge_key):
                present = present or (model_edge_key[idx] != -1)
            if idx < len(model_proxy_key):
                present = present or (model_proxy_key[idx] != -1)
            if idx < len(model_proxy_mask):
                present = present or bool(model_proxy_mask[idx])
            return present

        raise ValueError("Unknown model_presence_mode: {0}".format(mode))

    anno_over_model = getattr(raster, "anno_over_model", []) or []
    anno_key = getattr(raster, "anno_key", []) or []

    for idx in range(total):
        has_model = _has_model(idx)

        # "Any annotation ink" should be driven by anno_key presence.
        # Keep anno_over_model as a separate concept (overlap channel).
        has_anno = (idx < len(anno_key)) and (anno_key[idx] != -1)
        has_anno_over_model = (idx < len(anno_over_model)) and bool(anno_over_model[idx])

        if has_model and has_anno_over_model:
            overlap += 1
        elif has_model:
            model_only += 1
        elif has_anno:
            anno_only += 1
        else:
            empty += 1

    computed_total = empty + model_only + anno_only + overlap
    if total != computed_total:
        msg = (
            "CSV invariant failed: TotalCells ({0}) != "
            "Empty + ModelOnly + AnnoOnly + Overlap ({1})"
        ).format(total, computed_total)
        if diag is not None:
            diag.error(
                phase="export_csv",
                callsite="compute_cell_metrics",
                message=msg,
                extra={"model_presence_mode": mode},
            )
        raise AssertionError(msg)

    return {
        "TotalCells": total,
        "Empty": empty,
        "ModelOnly": model_only,
        "AnnoOnly": anno_only,
        "Overlap": overlap,
    }


def compute_annotation_type_metrics(raster):
    """Count annotation cells by type.

    Args:
        raster: ViewRaster object

    Returns:
        Dict with:
            - AnnoCells_TEXT: int
            - AnnoCells_TAG: int
            - AnnoCells_DIM: int
            - AnnoCells_DETAIL: int
            - AnnoCells_LINES: int
            - AnnoCells_REGION: int
            - AnnoCells_OTHER: int

    Commentary:
        ✔ Matches SSM classification: TEXT, TAG, DIM, DETAIL, LINES, REGION, OTHER
        ✔ Includes keynotes: Material Element Keynotes→TAG, User Keynotes→TEXT
    """
    counts = {
        "TEXT": 0,
        "TAG": 0,
        "DIM": 0,
        "DETAIL": 0,
        "LINES": 0,
        "REGION": 0,
        "OTHER": 0
    }

    # Count cells by annotation type
    for i, anno_idx in enumerate(raster.anno_key):
        if anno_idx >= 0:  # Cell has annotation
            if anno_idx < len(raster.anno_meta):
                meta = raster.anno_meta[anno_idx]

                # Base type from annotation pass
                anno_type = (meta.get("type") or "OTHER").upper()

                # Remap FilledRegion to REGION for CSV metrics
                try:
                    from Autodesk.Revit.DB import BuiltInCategory
                    cat_id = meta.get("cat_id", None)
                    if cat_id is not None and int(cat_id) == int(BuiltInCategory.OST_FilledRegion):
                        anno_type = "REGION"
                except Exception:
                    pass

                if anno_type in counts:
                    counts[anno_type] += 1
                else:
                    counts["OTHER"] += 1

    return {f"AnnoCells_{k}": v for k, v in counts.items()}


def extract_view_metadata(view, doc, diag=None):
    """Extract view metadata for CSV export.

    Args:
        view: Revit View
        doc: Revit Document

    Returns:
        Dict with:
            - ViewId: int
            - ViewUniqueId: str
            - ViewName: str
            - ViewType: str (FloorPlan, Section, etc.)
            - SheetNumber: str (if on sheet)
            - IsOnSheet: bool
            - Scale: int
            - Discipline: str
            - Phase: str
            - ViewTemplate_Name: str
            - IsTemplate: bool

    Commentary:
        ✔ Handles missing attributes gracefully
        ✔ Matches SSM metadata extraction
    """
    try:
        from Autodesk.Revit.DB import Viewport, FilteredElementCollector
    except ImportError:
        # Fallback for testing environments
        Viewport = None
        FilteredElementCollector = None

    metadata = {}

    # ViewId and UniqueId
    try:
        metadata["ViewId"] = view.Id.IntegerValue
    except Exception:
        metadata["ViewId"] = 0

    try:
        metadata["ViewUniqueId"] = view.UniqueId or ""
    except Exception:
        metadata["ViewUniqueId"] = ""

    # ViewName
    try:
        metadata["ViewName"] = view.Name or ""
    except Exception:
        metadata["ViewName"] = ""

    # ViewType
    try:
        metadata["ViewType"] = view.ViewType.ToString()
    except Exception:
        metadata["ViewType"] = ""

    # Sheet placement
    sheet_number = ""
    is_on_sheet = False

    if Viewport is not None and FilteredElementCollector is not None and doc is not None:
        try:
            view_id = view.Id
            vp_col = FilteredElementCollector(doc).OfClass(Viewport)
            for vp in vp_col:
                try:
                    if vp.ViewId.IntegerValue == view_id.IntegerValue:
                        sheet = doc.GetElement(vp.SheetId)
                        if sheet is not None:
                            sheet_number = getattr(sheet, "SheetNumber", "") or ""
                            is_on_sheet = True
                            break
                except Exception as e:
                    if diag is not None:
                        diag.warn(
                            phase="export_csv",
                            callsite="extract_view_metadata.viewport_scan",
                            message="Viewport scan failed for one viewport; continuing",
                            view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                            extra={"exc_type": type(e).__name__, "exc": str(e)},
                        )
                    continue
        except Exception as e:
            if diag is not None:
                diag.warn(
                    phase="export_csv",
                    callsite="extract_view_metadata.sheet_lookup",
                    message="Failed to determine sheet placement for view",
                    view_id=getattr(getattr(view, "Id", None), "IntegerValue", None),
                    extra={"exc_type": type(e).__name__, "exc": str(e)},
                )

    metadata["SheetNumber"] = sheet_number
    metadata["IsOnSheet"] = is_on_sheet

    # Scale
    try:
        scale_val = view.Scale
        metadata["Scale"] = int(scale_val) if isinstance(scale_val, int) else ""
    except Exception:
        metadata["Scale"] = ""

    # Discipline
    try:
        discipline = view.Discipline
        metadata["Discipline"] = discipline.ToString() if discipline is not None else ""
    except Exception:
        metadata["Discipline"] = ""

    # Phase
    try:
        phase_id = view.get_Parameter("Phase")
        if phase_id is not None and doc is not None:
            phase_elem = doc.GetElement(phase_id.AsElementId())
            metadata["Phase"] = phase_elem.Name if phase_elem is not None else ""
        else:
            metadata["Phase"] = ""
    except Exception:
        metadata["Phase"] = ""

    # View Template
    try:
        vt_id = view.ViewTemplateId
        if vt_id is not None and doc is not None:
            vt_elem = doc.GetElement(vt_id)
            metadata["ViewTemplate_Name"] = vt_elem.Name if vt_elem is not None else ""
        else:
            metadata["ViewTemplate_Name"] = ""
    except Exception:
        metadata["ViewTemplate_Name"] = ""

    # IsTemplate
    try:
        metadata["IsTemplate"] = bool(view.IsTemplate)
    except Exception:
        metadata["IsTemplate"] = False

    return metadata


def compute_config_hash(config):
    """Compute stable hash of config for reproducibility tracking.

    Args:
        config: Config object

    Returns:
        8-character hex hash (stable across runs)

    Commentary:
        ✔ Hashes relevant config parameters for reproducibility
        ✔ Stable: same config → same hash
    """
    # Build config payload string using actual Config attributes
    config_str = f"{config.tiny_max}|{config.thin_max}|" \
                 f"{config.adaptive_tile_size}|{config.proxy_mask_mode}|" \
                 f"{config.over_model_includes_proxies}|{config.tile_size}|" \
                 f"{config.depth_eps_ft}|{config.anno_crop_margin_in}|{config.anno_expand_cap_cells}|" \
                 f"{config.cell_size_paper_in}|{config.max_sheet_width_in}|{config.max_sheet_height_in}|" \
                 f"{config.bounds_buffer_in}"

    # Compute hash
    hash_obj = hashlib.sha256(config_str.encode('utf-8'))
    return hash_obj.hexdigest()[:8]


def compute_view_frame_hash(view_metadata):
    """Compute hash of view frame properties.

    Args:
        view_metadata: Dict from extract_view_metadata()

    Returns:
        8-character hex hash based on ViewType, Scale, Sheet, Discipline

    Commentary:
        ✔ Captures view "frame" - stable properties that define the view's identity
        ✔ Used for cache invalidation (if frame changes, view changed)
    """
    # Build frame payload
    frame_str = f"{view_metadata.get('ViewType', '')}|{view_metadata.get('Scale', '')}|" \
                f"{view_metadata.get('SheetNumber', '')}|{view_metadata.get('Discipline', '')}"

    # Compute hash
    hash_obj = hashlib.sha256(frame_str.encode('utf-8'))
    return hash_obj.hexdigest()[:8]


def build_core_csv_row(view, doc, metrics, config, run_info, view_metadata=None):
    """Build row for core CSV.

    Args:
        view: Revit View
        doc: Revit Document
        metrics: Dict from compute_cell_metrics()
        config: Config object
        run_info: Dict with date, run_id, exporter_version, elapsed_sec
        view_metadata: Optional dict from extract_view_metadata() (computed if not provided)

    Returns:
        List of values matching core_headers order:
        [Date, RunId, ViewId, ViewUniqueId, ViewName, ViewType, SheetNumber, IsOnSheet,
         Scale, Discipline, Phase, ViewTemplate_Name, IsTemplate, ExporterVersion,
         ConfigHash, ViewFrameHash, FromCache, ElapsedSec]

    Commentary:
        ✔ 18 columns matching SSM core CSV
        ✔ FromCache always False for now (no caching yet)
    """
    # Extract view metadata if not provided
    if view_metadata is None:
        view_metadata = extract_view_metadata(view, doc)

    # Compute hashes
    config_hash = compute_config_hash(config)
    view_frame_hash = compute_view_frame_hash(view_metadata)

    row = [
        run_info.get("date", ""),
        run_info.get("run_id", ""),
        view_metadata.get("ViewId", ""),
        view_metadata.get("ViewUniqueId", ""),
        view_metadata.get("ViewName", ""),
        view_metadata.get("ViewType", ""),
        view_metadata.get("SheetNumber", ""),
        view_metadata.get("IsOnSheet", False),
        view_metadata.get("Scale", ""),
        view_metadata.get("Discipline", ""),
        view_metadata.get("Phase", ""),
        view_metadata.get("ViewTemplate_Name", ""),
        view_metadata.get("IsTemplate", False),
        run_info.get("exporter_version", "VOP_v1.0"),
        config_hash,
        view_frame_hash,
        False,  # FromCache (always False for now)
        run_info.get("elapsed_sec", 0.0),
    ]

    return row


def build_vop_csv_row(view, metrics, anno_metrics, config, run_info, view_metadata=None, diag=None):
    """Build row for VOP extended CSV.

    Args:
        view: Revit View
        metrics: Dict from compute_cell_metrics()
        anno_metrics: Dict from compute_annotation_type_metrics()
        config: Config object
        run_info: Dict with date, run_id, exporter_version, elapsed_sec, cell_size_ft
        view_metadata: Optional dict from extract_view_metadata() (computed if not provided)

    Returns:
        List of values matching vop_headers order:
        [Date, RunId, ViewId, ViewName, ViewType, TotalCells, Empty, ModelOnly, AnnoOnly,
         Overlap, Ext_Cells_Any, Ext_Cells_Only, Ext_Cells_DWG, Ext_Cells_RVT,
         AnnoCells_TEXT, AnnoCells_TAG, AnnoCells_DIM, AnnoCells_DETAIL, AnnoCells_LINES,
         AnnoCells_REGION, AnnoCells_OTHER, CellSize_ft, RowSource, ExporterVersion,
         ConfigHash, FromCache, ElapsedSec]

    Commentary:
        ✔ 27 columns matching SSM VOP CSV
        ✔ External cells (DWG, RVT) all 0 for now (no link support yet)
        ✔ RowSource = "VOP_Interwoven_v1"
    """
    # Extract view metadata if not provided
    if view_metadata is None:
        from .entry_dynamo import get_current_document
        doc = get_current_document()
        view_metadata = extract_view_metadata(view, doc)

    # Compute config hash
    config_hash = compute_config_hash(config)

    row = [
        run_info.get("date", ""),
        run_info.get("run_id", ""),
        view_metadata.get("ViewId", ""),
        view_metadata.get("ViewName", ""),
        view_metadata.get("ViewType", ""),
        metrics.get("TotalCells", 0),
        metrics.get("Empty", 0),
        metrics.get("ModelOnly", 0),
        metrics.get("AnnoOnly", 0),
        metrics.get("Overlap", 0),
        metrics.get("Ext_Cells_Any", 0),
        metrics.get("Ext_Cells_Only", 0),
        metrics.get("Ext_Cells_DWG", 0),
        metrics.get("Ext_Cells_RVT", 0),
        anno_metrics.get("AnnoCells_TEXT", 0),
        anno_metrics.get("AnnoCells_TAG", 0),
        anno_metrics.get("AnnoCells_DIM", 0),
        anno_metrics.get("AnnoCells_DETAIL", 0),
        anno_metrics.get("AnnoCells_LINES", 0),
        anno_metrics.get("AnnoCells_REGION", 0),
        anno_metrics.get("AnnoCells_OTHER", 0),

        # Back-compat: actual (effective) cell size used
        run_info.get("cell_size_ft", 0.0),

        # Option 2 contract fields
        run_info.get("cell_size_ft_requested", run_info.get("cell_size_ft", 0.0)),
        run_info.get("cell_size_ft_effective", run_info.get("cell_size_ft", 0.0)),
        run_info.get("resolution_mode", "canonical"),
        bool(run_info.get("cap_triggered", False)),

        "VOP_Interwoven_v1",  # RowSource
        run_info.get("exporter_version", "VOP_v1.0"),
        config_hash,
        False,  # FromCache (always False for now)
        run_info.get("elapsed_sec", 0.0),
    ]


    return row

def export_pipeline_to_csv(pipeline_result, output_dir, config, doc=None, diag=None, date_override=None):
    """Export pipeline results to core + VOP CSV files.

    Args:
        pipeline_result: Dict with 'views' list from run_vop_pipeline()
        output_dir: Output directory path
        config: Config object
        doc: Revit Document (optional, for view metadata extraction)
        diag: Optional diagnostics sink
        date_override: Optional date/time override for filenames + Date column.
            Accepts:
                - "YYYY-MM-DD"
                - ISO datetime string (e.g. "YYYY-MM-DDTHH:MM:SS")
            If provided, the Date column and filename date will use this value.

    Returns:
        Dict with:
            - core_csv_path: str
            - vop_csv_path: str
            - rows_exported: int
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from export.csv import _append_csv_rows, _ensure_dir

    # Ensure output directory exists
    if not _ensure_dir(output_dir, None):
        os.makedirs(output_dir, exist_ok=True)

    # Resolve document (best-effort)
    if doc is None:
        try:
            from .entry_dynamo import get_current_document
            doc = get_current_document()
        except Exception:
            doc = None

    # Resolve run datetime / date string
    run_dt = datetime.now()
    tag = None

    if date_override:
        if isinstance(date_override, str):
            s = date_override.strip()
            # Try strict date / datetime parsing first
            try:
                if len(s) == 10:
                    run_dt = datetime.strptime(s, "%Y-%m-%d")
                else:
                    run_dt = datetime.fromisoformat(s)
            except Exception:
                # Treat as opaque tag (commit hash, label, etc.)
                tag = s
        else:
            tag = str(date_override)

    date_str = run_dt.strftime("%Y-%m-%d")

    # RunId: deterministic but tag-aware
    base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
    run_id = f"{base_run_id}_{tag}" if tag else base_run_id

    # Filenames: include tag if present
    core_filename = f"views_core_{date_str}{'_' + tag if tag else ''}.csv"
    vop_filename = f"views_vop_{date_str}{'_' + tag if tag else ''}.csv"

    core_path = os.path.join(output_dir, core_filename)
    vop_path = os.path.join(output_dir, vop_filename)

    core_headers = [
        "Date", "RunId", "ViewId", "ViewUniqueId", "ViewName", "ViewType",
        "SheetNumber", "IsOnSheet", "Scale", "Discipline", "Phase",
        "ViewTemplate_Name", "IsTemplate", "ExporterVersion", "ConfigHash",
        "ViewFrameHash", "FromCache", "ElapsedSec"
    ]

    vop_headers = [
        "Date", "RunId", "ViewId", "ViewName", "ViewType", "TotalCells",
        "Empty", "ModelOnly", "AnnoOnly", "Overlap", "Ext_Cells_Any",
        "Ext_Cells_Only", "Ext_Cells_DWG", "Ext_Cells_RVT", "AnnoCells_TEXT",
        "AnnoCells_TAG", "AnnoCells_DIM", "AnnoCells_DETAIL", "AnnoCells_LINES",
        "AnnoCells_REGION", "AnnoCells_OTHER",

        # Back-compat: actual (effective) cell size used to construct raster
        "CellSize_ft",

        # Option 2 contract fields
        "CellSizeRequested_ft",
        "CellSizeEffective_ft",
        "ResolutionMode",
        "CapTriggered",

        "RowSource",
        "ExporterVersion", "ConfigHash", "FromCache", "ElapsedSec"
    ]

    core_rows = []
    vop_rows = []

    views_data = pipeline_result.get("views", []) if isinstance(pipeline_result, dict) else pipeline_result

    for view_result in views_data:
        # Requirement: rejected/failed views must not show up in CSVs
        if view_result.get("success") is False:
            continue
        if view_result.get("view_mode") == "REJECTED":
            continue

        raster_dict = view_result.get("raster", {})
        if not raster_dict:
            continue

        # Reconstruct View for views_core metadata
        view = view_result.get("view")
        if view is None and doc is not None:
            try:
                from Autodesk.Revit.DB import ElementId
                vid = view_result.get("view_id", None)
                if isinstance(vid, int):
                    view = doc.GetElement(ElementId(vid))
            except Exception:
                view = None

        from .core.raster import ViewRaster
        from .core.math_utils import Bounds2D

        bounds_dict = raster_dict.get("bounds_xy", {})
        bounds = Bounds2D(
            bounds_dict.get("xmin", 0),
            bounds_dict.get("ymin", 0),
            bounds_dict.get("xmax", 100),
            bounds_dict.get("ymax", 100)
        )

        raster = ViewRaster(
            width=raster_dict.get("width", 0),
            height=raster_dict.get("height", 0),
            cell_size=raster_dict.get("cell_size_ft", 1.0),
            bounds=bounds,
            tile_size=16
        )

        raster.model_edge_key = raster_dict.get("model_edge_key", [])
        raster.model_proxy_mask = raster_dict.get("model_proxy_mask", raster_dict.get("model_proxy_presence", []))
        raster.model_proxy_key = raster_dict.get("model_proxy_key", [])
        raster.model_mask = raster_dict.get("model_mask", [])
        raster.anno_over_model = raster_dict.get("anno_over_model", [])
        raster.anno_key = raster_dict.get("anno_key", [])
        raster.anno_meta = raster_dict.get("anno_meta", [])
        raster.element_meta = raster_dict.get("element_meta", raster_dict.get("elements_meta", []))

        try:
            model_presence_mode = getattr(config, "model_presence_mode", "ink")
            metrics = compute_cell_metrics(raster, model_presence_mode=model_presence_mode, diag=diag)
            anno_metrics = compute_annotation_type_metrics(raster)
        except Exception as e:
            if diag is not None:
                try:
                    diag.error(
                        phase="export_csv",
                        callsite="export_pipeline_to_csv.metrics",
                        message="Failed to compute CSV metrics for view",
                        exc=e,
                        extra={
                            "view_id": view_result.get("view_id", 0),
                            "view_name": view_result.get("view_name", ""),
                            "width": raster_dict.get("width", raster_dict.get("W", 0)),
                            "height": raster_dict.get("height", raster_dict.get("H", 0)),
                            "model_presence_mode": getattr(config, "model_presence_mode", "ink"),
                        },
                    )
                except Exception:
                    pass
            raise

        try:
            metrics.update(compute_external_cell_metrics(raster))
        except Exception as e:
            if diag is not None:
                try:
                    diag.warn(
                        phase="export_csv",
                        callsite="export_pipeline_to_csv.ext_cells",
                        message="Failed to compute external-cell metrics; using zeros",
                        exc=e,
                        extra={"view_id": view_result.get("view_id", 0)},
                    )
                except Exception:
                    pass

        bounds_meta = raster_dict.get("bounds_meta") or {}

        cell_size_eff = raster_dict.get("cell_size_ft", 0.0)
        cell_size_req = bounds_meta.get("cell_size_ft_requested", cell_size_eff)
        cell_size_eff_meta = bounds_meta.get("cell_size_ft_effective", cell_size_eff)

        run_info = {
            "date": date_str,
            "run_id": run_id,
            "exporter_version": view_result.get("exporter_version", "VOP_v1.0"),
            "elapsed_sec": view_result.get("elapsed_sec", 0.0),

            # Back-compat: actual (effective) cell size used
            "cell_size_ft": cell_size_eff,

            # Option 2 contract fields
            "cell_size_ft_requested": cell_size_req,
            "cell_size_ft_effective": cell_size_eff_meta,
            "resolution_mode": bounds_meta.get("resolution_mode", "canonical"),
            "cap_triggered": bool(bounds_meta.get("cap_triggered", bounds_meta.get("capped", False))),
        }

        view_metadata = {}
        if view is not None:
            try:
                view_metadata = extract_view_metadata(view, doc, diag=diag)
            except Exception:
                view_metadata = {}

        if view is not None:
            core_rows.append(build_core_csv_row(view, doc, metrics, config, run_info, view_metadata=view_metadata))
        vop_rows.append(build_vop_csv_row(view, metrics, anno_metrics, config, run_info, view_metadata=view_metadata, diag=diag))

    # Simple logger stub (export/csv expects logger-like object)
    class SimpleLogger:
        def info(self, msg):
            print(f"CSV Export: {msg}")
        def warn(self, msg):
            print(f"CSV Export WARNING: {msg}")

    logger = SimpleLogger()

    try:
        if core_rows:
            _append_csv_rows(core_path, core_headers, core_rows, logger)
        if vop_rows:
            _append_csv_rows(vop_path, vop_headers, vop_rows, logger)
    except Exception as e:
        if diag is not None:
            try:
                diag.error(
                    phase="export_csv",
                    callsite="export_pipeline_to_csv.write",
                    message="Failed to write CSV files",
                    exc=e,
                    extra={"output_dir": output_dir},
                )
            except Exception:
                pass
        raise

    return {"core_csv_path": core_path, "vop_csv_path": vop_path, "rows_exported": len(vop_rows)}

# =============================================================================
# STREAMING SUPPORT - Append to end of csv_export.py
# =============================================================================
# These functions should be added at the very end of vop_interwoven/csv_export.py


def get_core_csv_header():
    """Get header for core CSV file."""
    return [
        "Date", "RunId", "ViewId", "ViewUniqueId", "ViewName", "ViewType",
        "SheetNumber", "IsOnSheet", "Scale", "Discipline", "Phase",
        "ViewTemplate_Name", "IsTemplate", "ExporterVersion", "ConfigHash",
        "ViewFrameHash", "FromCache", "ElapsedSec"
    ]


def get_vop_csv_header():
    """Get header for VOP CSV file."""
    return [
        "Date", "RunId", "ViewId", "ViewName", "ViewType", "TotalCells",
        "Empty", "ModelOnly", "AnnoOnly", "Overlap", "Ext_Cells_Any",
        "Ext_Cells_Only", "Ext_Cells_DWG", "Ext_Cells_RVT", "AnnoCells_TEXT",
        "AnnoCells_TAG", "AnnoCells_DIM", "AnnoCells_DETAIL", "AnnoCells_LINES",
        "AnnoCells_REGION", "AnnoCells_OTHER", "CellSize_ft", "CellSizeRequested_ft", "CellSizeEffective_ft", "ResolutionMode", "CapTriggered","RowSource",
        "ExporterVersion", "ConfigHash", "FromCache", "ElapsedSec"
    ]


def get_perf_csv_header():
    """Get header for performance CSV file."""
    return [
        "Date", "RunId", "view_id", "view_name", "success", "total_ms", "mode_ms",
        "raster_init_ms", "collect_ms", "raster_ms", "anno_ms",
        "finalize_ms", "export_ms", "png_ms", "width", "height",
        "total_elements", "filled_cells"
    ]


def view_result_to_core_row(view_result, config, doc, date_override=None, run_id=None):
    """Convert a single view result to a core CSV row dict.

    Args:
        view_result: View result dict from pipeline
        config: Config object
        doc: Revit Document
        date_override: Optional date/time override
        run_id: Optional pre-computed run_id for consistency across views

    Returns:
        Dict with core CSV fields, or None if view should be skipped
    """
    # Skip failed/rejected views
    if view_result.get("success") is False:
        return None
    if view_result.get("view_mode") == "REJECTED":
        return None

    raster_dict = view_result.get("raster", {}) or {}
    metrics_only = (not raster_dict) and isinstance(view_result.get("metrics"), dict) and bool(view_result.get("metrics"))
    
    # Allow metrics-only results for cache hits (root cache)
    if not raster_dict and not metrics_only:
        return None

    # Resolve run datetime and run_id (use provided run_id if available for consistency)
    if run_id is None:
        run_dt = datetime.now()
        tag = None

        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        run_dt = datetime.strptime(s, "%Y-%m-%d")
                    else:
                        run_dt = datetime.fromisoformat(s)
                except Exception:
                    tag = s
            else:
                tag = str(date_override)

        date_str = run_dt.strftime("%Y-%m-%d")
        base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
        run_id = f"{base_run_id}_{tag}" if tag else base_run_id
    else:
        # Extract date from run_id if provided
        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        date_str = datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
                    else:
                        date_str = datetime.fromisoformat(s).strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.now().strftime("%Y-%m-%d")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")
        else:
            # Extract date from run_id (format: YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSS_tag)
            try:
                date_part = run_id.split('_')[0].split('T')[0]
                date_str = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            except Exception:
                date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Get view object
    view = view_result.get("view")
    if view is None and doc is not None:
        try:
            from Autodesk.Revit.DB import ElementId
            vid = view_result.get("view_id", None)
            if isinstance(vid, int):
                view = doc.GetElement(ElementId(vid))
        except Exception:
            view = None
    
    # Extract view metadata
    view_metadata = extract_view_metadata(view, doc) if view else {}
    
    # Build row
    config_hash = compute_config_hash(config)
    view_frame_hash = compute_view_frame_hash(view_metadata)
    
    # FromCache flag (supports legacy + root)
    from_cache = "Y" if _is_from_cache(view_result) else "N"

    # Elapsed time (force 0 on cache hits)
    elapsed_sec = 0.0
    if from_cache != "Y":
        try:
            timings = view_result.get("timings", {})
            total_ms = timings.get("total_ms", 0.0)
            elapsed_sec = total_ms / 1000.0
        except Exception:
            pass
    
    row = {
        "Date": date_str,
        "RunId": run_id,
        "ViewId": view_metadata.get("ViewId", 0),
        "ViewUniqueId": view_metadata.get("ViewUniqueId", ""),
        "ViewName": view_metadata.get("ViewName", ""),
        "ViewType": view_metadata.get("ViewType", ""),
        "SheetNumber": view_metadata.get("SheetNumber", ""),
        "IsOnSheet": view_metadata.get("IsOnSheet", "N"),
        "Scale": view_metadata.get("Scale", 0),
        "Discipline": view_metadata.get("Discipline", ""),
        "Phase": view_metadata.get("Phase", ""),
        "ViewTemplate_Name": view_metadata.get("ViewTemplate_Name", ""),
        "IsTemplate": view_metadata.get("IsTemplate", "N"),
        "ExporterVersion": "vop_interwoven",
        "ConfigHash": config_hash,
        "ViewFrameHash": view_frame_hash,
        "FromCache": from_cache,
        "ElapsedSec": f"{elapsed_sec:.3f}"
    }
    
    return row


def view_result_to_vop_row(view_result, config, doc, date_override=None, run_id=None):
    """Convert a single view result to a VOP CSV row dict.

    Args:
        view_result: View result dict from pipeline
        config: Config object
        doc: Revit Document
        date_override: Optional date/time override
        run_id: Optional pre-computed run_id for consistency across views

    Returns:
        Dict with VOP CSV fields, or None if view should be skipped
    """
    # Skip failed/rejected views
    if view_result.get("success") is False:
        return None
    if view_result.get("view_mode") == "REJECTED":
        return None

    raster_dict = view_result.get("raster", {}) or {}
    metrics_only = (not raster_dict) and isinstance(view_result.get("metrics"), dict) and bool(view_result.get("metrics"))
    if not raster_dict and not metrics_only:
        return None

    # Resolve run datetime and run_id (use provided run_id if available for consistency)
    if run_id is None:
        run_dt = datetime.now()
        tag = None

        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        run_dt = datetime.strptime(s, "%Y-%m-%d")
                    else:
                        run_dt = datetime.fromisoformat(s)
                except Exception:
                    tag = s
            else:
                tag = str(date_override)

        date_str = run_dt.strftime("%Y-%m-%d")
        base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
        run_id = f"{base_run_id}_{tag}" if tag else base_run_id
    else:
        # Extract date from run_id if provided
        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        date_str = datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
                    else:
                        date_str = datetime.fromisoformat(s).strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.now().strftime("%Y-%m-%d")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")
        else:
            # Extract date from run_id (format: YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSS_tag)
            try:
                date_part = run_id.split('_')[0].split('T')[0]
                date_str = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            except Exception:
                date_str = datetime.now().strftime("%Y-%m-%d")
    
    if metrics_only:
        metrics = view_result.get("metrics") or {}
        anno_metrics = metrics  # anno counts already flattened in root_cache metrics
        ext_metrics = metrics   # ext counts already flattened in root_cache metrics
    else:
        # Reconstruct raster object for metrics computation (existing behavior)
        from .core.raster import ViewRaster
        from .core.math_utils import Bounds2D
    
    bounds_dict = raster_dict.get("bounds_xy", {})
    bounds = Bounds2D(
        bounds_dict.get("xmin", 0),
        bounds_dict.get("ymin", 0),
        bounds_dict.get("xmax", 100),
        bounds_dict.get("ymax", 100)
    )
    
    raster = ViewRaster(
        width=raster_dict.get("width", 0),
        height=raster_dict.get("height", 0),
        cell_size=raster_dict.get("cell_size_ft", 1.0),
        bounds=bounds,
        tile_size=16
    )
    
    raster.model_edge_key = raster_dict.get("model_edge_key", [])
    raster.model_proxy_mask = raster_dict.get("model_proxy_mask", raster_dict.get("model_proxy_presence", []))
    raster.model_proxy_key = raster_dict.get("model_proxy_key", [])
    raster.model_mask = raster_dict.get("model_mask", [])
    raster.anno_over_model = raster_dict.get("anno_over_model", [])
    raster.anno_key = raster_dict.get("anno_key", [])
    raster.anno_meta = raster_dict.get("anno_meta", [])
    raster.element_meta = raster_dict.get("element_meta", raster_dict.get("elements_meta", []))
    
    # Compute metrics
    model_presence_mode = getattr(config, "model_presence_mode", "ink")
    metrics = compute_cell_metrics(raster, model_presence_mode=model_presence_mode)
    anno_metrics = compute_annotation_type_metrics(raster)
    ext_metrics = compute_external_cell_metrics(raster)

    # Get view object
    view = view_result.get("view")
    if view is None and doc is not None:
        try:
            from Autodesk.Revit.DB import ElementId
            vid = view_result.get("view_id", None)
            if isinstance(vid, int):
                view = doc.GetElement(ElementId(vid))
        except Exception:
            view = None
    
    view_metadata = extract_view_metadata(view, doc) if view else {}
    
    config_hash = compute_config_hash(config)
    
    # Elapsed time
    elapsed_sec = 0.0
    try:
        timings = view_result.get("timings", {})
        total_ms = timings.get("total_ms", 0.0)
        elapsed_sec = total_ms / 1000.0
    except Exception:
        pass
    
    # FromCache flag (supports legacy + root)
    from_cache = "Y" if _is_from_cache(view_result) else "N"

    # Elapsed time (force 0 on cache hits)
    elapsed_sec = 0.0
    if from_cache != "Y":
        try:
            timings = view_result.get("timings", {})
            total_ms = timings.get("total_ms", 0.0)
            elapsed_sec = total_ms / 1000.0
        except Exception:
            pass
    
    bounds_meta = raster_dict.get("bounds_meta") or {}

    cell_size_ft = raster_dict.get("cell_size_ft", 0.0)
    cell_size_req = bounds_meta.get("cell_size_ft_requested", cell_size_ft)
    cell_size_eff = bounds_meta.get("cell_size_ft_effective", cell_size_ft)
    resolution_mode = bounds_meta.get("resolution_mode", "canonical")
    cap_triggered = bool(bounds_meta.get("cap_triggered", bounds_meta.get("capped", False)))

    row = {
        "Date": date_str,
        "RunId": run_id,
        "ViewId": view_result.get("view_id", 0),
        "ViewName": view_result.get("view_name", ""),
        "ViewType": view_metadata.get("ViewType", ""),
        "TotalCells": metrics.get("TotalCells", 0),
        "Empty": metrics.get("Empty", 0),
        "ModelOnly": metrics.get("ModelOnly", 0),
        "AnnoOnly": metrics.get("AnnoOnly", 0),
        "Overlap": metrics.get("Overlap", 0),
        "Ext_Cells_Any": ext_metrics.get("Ext_Cells_Any", 0),
        "Ext_Cells_Only": ext_metrics.get("Ext_Cells_Only", 0),
        "Ext_Cells_DWG": ext_metrics.get("Ext_Cells_DWG", 0),
        "Ext_Cells_RVT": ext_metrics.get("Ext_Cells_RVT", 0),
        "AnnoCells_TEXT": anno_metrics.get("AnnoCells_TEXT", 0),
        "AnnoCells_TAG": anno_metrics.get("AnnoCells_TAG", 0),
        "AnnoCells_DIM": anno_metrics.get("AnnoCells_DIM", 0),
        "AnnoCells_DETAIL": anno_metrics.get("AnnoCells_DETAIL", 0),
        "AnnoCells_LINES": anno_metrics.get("AnnoCells_LINES", 0),
        "AnnoCells_REGION": anno_metrics.get("AnnoCells_REGION", 0),
        "AnnoCells_OTHER": anno_metrics.get("AnnoCells_OTHER", 0),

        # Existing field (effective size actually used by raster)
        "CellSize_ft": cell_size_ft,

        # Option 2 contract fields
        "CellSizeRequested_ft": cell_size_req,
        "CellSizeEffective_ft": cell_size_eff,
        "ResolutionMode": resolution_mode,
        "CapTriggered": cap_triggered,

        "RowSource": "vop_interwoven",
        "ExporterVersion": "vop_interwoven",
        "ConfigHash": config_hash,
        "FromCache": from_cache,
        "ElapsedSec": f"{elapsed_sec:.3f}",
    }

    
    return row


def view_result_to_perf_row(view_result, date_override=None, run_id=None):
    """Convert a single view result to a performance CSV row dict.

    Args:
        view_result: View result dict from pipeline
        date_override: Optional date/time override
        run_id: Optional pre-computed run_id for consistency across views

    Returns:
        Dict with performance CSV fields
    """
    # Resolve run datetime and run_id (use provided run_id if available for consistency)
    if run_id is None:
        run_dt = datetime.now()
        tag = None

        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        run_dt = datetime.strptime(s, "%Y-%m-%d")
                    else:
                        run_dt = datetime.fromisoformat(s)
                except Exception:
                    tag = s
            else:
                tag = str(date_override)

        date_str = run_dt.strftime("%Y-%m-%d")
        base_run_id = run_dt.strftime("%Y%m%dT%H%M%S")
        run_id = f"{base_run_id}_{tag}" if tag else base_run_id
    else:
        # Extract date from run_id if provided
        if date_override:
            if isinstance(date_override, str):
                s = date_override.strip()
                try:
                    if len(s) == 10:
                        date_str = datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
                    else:
                        date_str = datetime.fromisoformat(s).strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.now().strftime("%Y-%m-%d")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")
        else:
            # Extract date from run_id (format: YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSS_tag)
            try:
                date_part = run_id.split('_')[0].split('T')[0]
                date_str = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            except Exception:
                date_str = datetime.now().strftime("%Y-%m-%d")

    timings = view_result.get("timings", {})
    
    if _is_from_cache(view_result):
        timings = {}

    row = {
        "Date": date_str,
        "RunId": run_id,
        "view_id": view_result.get("view_id", 0),
        "view_name": view_result.get("view_name", ""),
        "success": "Y" if view_result.get("success", True) else "N",
        "total_ms": timings.get("total_ms", 0.0),
        "mode_ms": timings.get("mode_ms", 0.0),
        "raster_init_ms": timings.get("raster_init_ms", 0.0),
        "collect_ms": timings.get("collect_ms", 0.0),
        "raster_ms": timings.get("raster_ms", 0.0),
        "anno_ms": timings.get("anno_ms", 0.0),
        "finalize_ms": timings.get("finalize_ms", 0.0),
        "export_ms": timings.get("export_ms", 0.0),
        "png_ms": timings.get("png_ms", 0.0),
        "width": view_result.get("width", 0),
        "height": view_result.get("height", 0),
        "total_elements": view_result.get("total_elements", 0),
        "filled_cells": view_result.get("filled_cells", 0)
    }

    return row
