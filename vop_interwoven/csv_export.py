"""CSV export functionality for VOP Interwoven pipeline.

Exports pipeline results to CSV format matching the SSM exporter schema for
analytics integration and comparison workflows.
"""

import os
import hashlib
from datetime import datetime


def compute_cell_metrics(raster):
    """Compute occupancy metrics from raster arrays.

    Args:
        raster: ViewRaster object

    Returns:
        Dict with:
            - TotalCells: int (W * H)
            - Empty: int (neither model nor anno)
            - ModelOnly: int (model but no anno)
            - AnnoOnly: int (anno but no model)
            - Overlap: int (both model and anno)

    Raises:
        AssertionError: If invariant fails (TotalCells != sum of categories)

    Commentary:
        ✔ Validates critical invariant: TotalCells = Empty + ModelOnly + AnnoOnly + Overlap
        ✔ Iterates once over all cells for efficiency
    """
    total = raster.W * raster.H
    empty = 0
    model_only = 0
    anno_only = 0
    overlap = 0

    for i in range(total):
        has_model = raster.model_mask[i]
        has_anno = raster.anno_over_model[i]

        if has_model and has_anno:
            overlap += 1
        elif has_model:
            model_only += 1
        elif has_anno:
            anno_only += 1
        else:
            empty += 1

    # Validate invariant
    computed_total = empty + model_only + anno_only + overlap
    assert total == computed_total, \
        f"CSV invariant failed: TotalCells ({total}) != Empty + ModelOnly + AnnoOnly + Overlap ({computed_total})"

    return {
        "TotalCells": total,
        "Empty": empty,
        "ModelOnly": model_only,
        "AnnoOnly": anno_only,
        "Overlap": overlap
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
                anno_type = meta.get("type", "OTHER").upper()

                if anno_type in counts:
                    counts[anno_type] += 1
                else:
                    counts["OTHER"] += 1

    return {f"AnnoCells_{k}": v for k, v in counts.items()}


def extract_view_metadata(view, doc):
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
    except:
        metadata["ViewId"] = 0

    try:
        metadata["ViewUniqueId"] = view.UniqueId or ""
    except:
        metadata["ViewUniqueId"] = ""

    # ViewName
    try:
        metadata["ViewName"] = view.Name or ""
    except:
        metadata["ViewName"] = ""

    # ViewType
    try:
        metadata["ViewType"] = view.ViewType.ToString()
    except:
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
                except:
                    continue
        except:
            pass

    metadata["SheetNumber"] = sheet_number
    metadata["IsOnSheet"] = is_on_sheet

    # Scale
    try:
        scale_val = view.Scale
        metadata["Scale"] = int(scale_val) if isinstance(scale_val, int) else ""
    except:
        metadata["Scale"] = ""

    # Discipline
    try:
        discipline = view.Discipline
        metadata["Discipline"] = discipline.ToString() if discipline is not None else ""
    except:
        metadata["Discipline"] = ""

    # Phase
    try:
        phase_id = view.get_Parameter("Phase")
        if phase_id is not None and doc is not None:
            phase_elem = doc.GetElement(phase_id.AsElementId())
            metadata["Phase"] = phase_elem.Name if phase_elem is not None else ""
        else:
            metadata["Phase"] = ""
    except:
        metadata["Phase"] = ""

    # View Template
    try:
        vt_id = view.ViewTemplateId
        if vt_id is not None and doc is not None:
            vt_elem = doc.GetElement(vt_id)
            metadata["ViewTemplate_Name"] = vt_elem.Name if vt_elem is not None else ""
        else:
            metadata["ViewTemplate_Name"] = ""
    except:
        metadata["ViewTemplate_Name"] = ""

    # IsTemplate
    try:
        metadata["IsTemplate"] = bool(view.IsTemplate)
    except:
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
    # Build config payload string
    config_str = f"{config.cell_size_paper_in}|{config.tiny_max}|{config.thin_max}|" \
                 f"{config.adaptive_tile_size}|{config.proxy_mask_mode}|{config.anno_proxies_in_overmodel}"

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


def build_vop_csv_row(view, metrics, anno_metrics, config, run_info, view_metadata=None):
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
        0,  # Ext_Cells_Any (no external support yet)
        0,  # Ext_Cells_Only
        0,  # Ext_Cells_DWG
        0,  # Ext_Cells_RVT
        anno_metrics.get("AnnoCells_TEXT", 0),
        anno_metrics.get("AnnoCells_TAG", 0),
        anno_metrics.get("AnnoCells_DIM", 0),
        anno_metrics.get("AnnoCells_DETAIL", 0),
        anno_metrics.get("AnnoCells_LINES", 0),
        anno_metrics.get("AnnoCells_REGION", 0),
        anno_metrics.get("AnnoCells_OTHER", 0),
        run_info.get("cell_size_ft", 0.0),
        "VOP_Interwoven_v1",  # RowSource
        run_info.get("exporter_version", "VOP_v1.0"),
        config_hash,
        False,  # FromCache (always False for now)
        run_info.get("elapsed_sec", 0.0),
    ]

    return row


def export_pipeline_to_csv(pipeline_result, output_dir, config, doc=None):
    """Export VOP pipeline results to CSV files.

    Args:
        pipeline_result: Dict with 'views' list from run_vop_pipeline()
        output_dir: Output directory path
        config: Config object
        doc: Revit Document (optional, for view metadata extraction)

    Returns:
        Dict with:
            - core_csv_path: str
            - vop_csv_path: str
            - rows_exported: int

    Creates:
        - views_core_YYYY-MM-DD.csv
        - views_vop_YYYY-MM-DD.csv

    Commentary:
        ✔ Uses export/csv._append_csv_rows() for proper header handling
        ✔ Date-based filenames
        ✔ Appends to existing files (multiple runs same day)
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from export.csv import _append_csv_rows, _ensure_dir

    # Ensure output directory exists
    if not _ensure_dir(output_dir, None):
        os.makedirs(output_dir, exist_ok=True)

    # Generate date string and run ID
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    run_id = now.strftime("%Y%m%dT%H%M%S")

    # CSV filenames
    core_filename = f"views_core_{date_str}.csv"
    vop_filename = f"views_vop_{date_str}.csv"

    core_path = os.path.join(output_dir, core_filename)
    vop_path = os.path.join(output_dir, vop_filename)

    # CSV headers
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
        "AnnoCells_REGION", "AnnoCells_OTHER", "CellSize_ft", "RowSource",
        "ExporterVersion", "ConfigHash", "FromCache", "ElapsedSec"
    ]

    # Build rows
    core_rows = []
    vop_rows = []

    views_data = pipeline_result.get("views", []) if isinstance(pipeline_result, dict) else pipeline_result

    for view_result in views_data:
        # Get view reference (from result or reconstruct)
        view = view_result.get("view")  # May not be present
        raster_dict = view_result.get("raster", {})

        # Reconstruct raster for metrics computation
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
            tile_size=16  # Default
        )

        # Restore raster arrays
        raster.model_mask = raster_dict.get("model_mask", [])
        raster.anno_over_model = raster_dict.get("anno_over_model", [])
        raster.anno_key = raster_dict.get("anno_key", [])
        raster.anno_meta = raster_dict.get("anno_meta", [])

        # Compute metrics
        metrics = compute_cell_metrics(raster)
        anno_metrics = compute_annotation_type_metrics(raster)

        # Build run_info
        run_info = {
            "date": date_str,
            "run_id": run_id,
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": view_result.get("elapsed_sec", 0.0),
            "cell_size_ft": raster_dict.get("cell_size_ft", 0.0),
        }

        # Extract view metadata (from result or view object)
        view_metadata = {
            "ViewId": view_result.get("view_id", 0),
            "ViewName": view_result.get("view_name", ""),
            "ViewType": "",
            "SheetNumber": "",
            "IsOnSheet": False,
            "Scale": "",
            "Discipline": "",
            "Phase": "",
            "ViewTemplate_Name": "",
            "IsTemplate": False,
            "ViewUniqueId": "",
        }

        # If we have the view object and doc, get full metadata
        if view is not None and doc is not None:
            view_metadata = extract_view_metadata(view, doc)

        # Build rows
        core_row = build_core_csv_row(view, doc, metrics, config, run_info, view_metadata)
        vop_row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info, view_metadata)

        core_rows.append(core_row)
        vop_rows.append(vop_row)

    # Simple logger stub
    class SimpleLogger:
        def info(self, msg):
            print(f"CSV Export: {msg}")
        def warn(self, msg):
            print(f"CSV Export WARNING: {msg}")

    logger = SimpleLogger()

    # Write CSVs
    if core_rows:
        _append_csv_rows(core_path, core_headers, core_rows, logger)
    if vop_rows:
        _append_csv_rows(vop_path, vop_headers, vop_rows, logger)

    return {
        "core_csv_path": core_path,
        "vop_csv_path": vop_path,
        "rows_exported": len(core_rows)
    }
