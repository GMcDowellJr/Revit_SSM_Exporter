"""
Dynamo-friendly entry points for VOP interwoven pipeline.

These functions handle Dynamo IN[0] inputs and provide sensible defaults.
"""

from vop_interwoven.entry_dynamo import (
    run_vop_pipeline_with_png,
    get_current_document,
    get_current_view
)
from vop_interwoven.config import Config


def get_views_from_input_or_current(views_input=None):
    """Get views from Dynamo IN[0] or current view if None.

    Args:
        views_input: Views from IN[0] (can be single view, list of views, or None)
                    Handles nested lists (e.g., [[views...]] from Dynamo)

    Returns:
        List of View ElementIds

    Example in Dynamo Python node:
        >>> views = get_views_from_input_or_current(IN[0] if len(IN) > 0 else None)
    """
    from Autodesk.Revit.DB import View, ElementId

    # If no input, use current view
    if views_input is None:
        current_view = get_current_view()
        return [current_view.Id]

    # Handle single view
    if isinstance(views_input, View):
        return [views_input.Id]

    # Handle single ElementId
    if isinstance(views_input, ElementId):
        return [views_input]

    # Handle single int
    if isinstance(views_input, int):
        return [views_input]

    # Handle list (including nested lists from Dynamo)
    if isinstance(views_input, list):
        # Flatten nested lists (Dynamo sometimes wraps in extra list)
        def flatten_views(items):
            result = []
            for item in items:
                if isinstance(item, list):
                    result.extend(flatten_views(item))
                elif isinstance(item, View):
                    result.append(item.Id)
                elif isinstance(item, ElementId):
                    result.append(item)
                elif isinstance(item, int):
                    result.append(item)
            return result

        view_ids = flatten_views(views_input)
        if view_ids:
            return view_ids

    # Fallback to current view
    current_view = get_current_view()
    return [current_view.Id]


def get_all_views_in_model(doc=None, include_templates=False):
    """Get all views in the model (all types).

    Args:
        doc: Revit Document (uses current if None)
        include_templates: Include view templates (default: False)

    Returns:
        List of View objects

    Example:
        >>> all_views = get_all_views_in_model()
        >>> # Returns all non-template views in model
    """
    from Autodesk.Revit.DB import FilteredElementCollector, View

    if doc is None:
        doc = get_current_document()

    collector = FilteredElementCollector(doc).OfClass(View)
    views = list(collector)

    # Filter out templates unless requested
    if not include_templates:
        views = [v for v in views if not v.IsTemplate]

    return views


def get_all_floor_plans(doc=None, include_templates=False):
    """Get all floor plan views in the model.

    Args:
        doc: Revit Document (uses current if None)
        include_templates: Include view templates (default: False)

    Returns:
        List of ViewPlan objects (floor plans only)
    """
    from Autodesk.Revit.DB import FilteredElementCollector, ViewPlan, ViewFamily

    if doc is None:
        doc = get_current_document()

    collector = FilteredElementCollector(doc).OfClass(ViewPlan)
    plans = list(collector)

    # Filter to floor plans only (not ceiling plans)
    floor_plans = [v for v in plans if v.ViewType.ToString() == "FloorPlan"]

    # Filter out templates unless requested
    if not include_templates:
        floor_plans = [v for v in floor_plans if not v.IsTemplate]

    return floor_plans


def get_all_sections(doc=None, include_templates=False):
    """Get all section views in the model.

    Args:
        doc: Revit Document (uses current if None)
        include_templates: Include view templates (default: False)

    Returns:
        List of ViewSection objects
    """
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSection

    if doc is None:
        doc = get_current_document()

    collector = FilteredElementCollector(doc).OfClass(ViewSection)
    sections = list(collector)

    # Filter out templates unless requested
    if not include_templates:
        sections = [v for v in sections if not v.IsTemplate]

    return sections


def filter_supported_views(views_input):
    """Filter views to only supported types and provide feedback.

    Args:
        views_input: Views from IN[0] (after get_views_from_input_or_current)

    Returns:
        Dictionary with:
            - 'view_ids': List of supported view ElementIds
            - 'total': Total views provided
            - 'supported': Number of supported views
            - 'skipped': Number of skipped views
            - 'skipped_types': List of (view_name, view_type) that were skipped

    Supported view types:
        - FloorPlan, CeilingPlan, Elevation, Section, AreaPlan, EngineeringPlan, Detail

    Unsupported (will be skipped):
        - 3D views (AxonometricView)
        - Schedules
        - Legends
        - DraftingViews
        - Sheets
    """
    from Autodesk.Revit.DB import ViewType

    doc = get_current_document()
    view_ids = get_views_from_input_or_current(views_input)

    supported_types = [
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.Elevation,
        ViewType.Section,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan,
        ViewType.Detail,
    ]

    supported_ids = []
    skipped_info = []

    from Autodesk.Revit.DB import ElementId

    for view_id in view_ids:
        vid = ElementId(view_id) if isinstance(view_id, int) else view_id
        view = doc.GetElement(vid)
        if view is None:
            continue

        try:
            if view.ViewType in supported_types:
                supported_ids.append(view_id)
            else:
                skipped_info.append((view.Name, view.ViewType.ToString()))
        except Exception:
            # Preserve behavior: treat as skipped/unknown without failing the whole filter.
            try:
                name = view.Name
            except Exception:
                name = "<UnknownView>"
            skipped_info.append((name, "Unknown"))

    return {
        'view_ids': supported_ids,
        'total': len(view_ids),
        'supported': len(supported_ids),
        'skipped': len(skipped_info),
        'skipped_types': skipped_info
    }


def run_pipeline_from_dynamo_input(
    views_input=None,
    output_dir=None,
    pixels_per_cell=4,
    config=None,
    verbose=False,
    export_csv=True,
    export_json=False,
    export_perf_csv=True,
    export_png=True
):

    """Run VOP pipeline with Dynamo-friendly inputs.

    Args:
        views_input: Views from IN[0] (single view, list, or None for current)
        output_dir: Output directory (default: C:\\temp\\vop_output)
        pixels_per_cell: PNG resolution (default: 4)
        config: Config object (default: None, uses defaults)
        verbose: If True, include filtering info in result
        export_csv: Export CSV files (default: True)
        export_json: Export JSON file (default: False - disabled for production due to large file size)
        export_png: Export PNG files (default: True)

    Returns:
        Dictionary with:
            - pipeline_result: Full pipeline result
            - json_path: Path to JSON (if export_json=True)
            - png_files: List of PNG paths (if export_png=True)
            - core_csv_path: Path to core CSV (if export_csv=True)
            - vop_csv_path: Path to VOP CSV (if export_csv=True)
            - rows_exported: Number of rows (if export_csv=True)
            - filter_info: View filtering details (if verbose=True)

    Usage in Dynamo Python node:
        >>> # Use views from IN[0], or current view if empty
        >>> result = run_pipeline_from_dynamo_input(IN[0] if len(IN) > 0 else None)
        >>> OUT = result['vop_csv_path']  # Or png_files, json_path, etc.

    Commentary:
        ✔ One-stop function for Dynamo users
        ✔ Handles IN[0] input automatically
        ✔ Exports JSON + PNG + CSV by default
    """
    from vop_interwoven.entry_dynamo import run_vop_pipeline_with_csv, run_vop_pipeline_with_png

    # Get document
    doc = get_current_document()

    # Get views (from input or current)
    view_ids = get_views_from_input_or_current(views_input)

    # Use default config if not provided
    if config is None:
        config = Config()

    # Default output directory (keep consistent with entry_dynamo defaults)
    if output_dir in (None, ""):
        output_dir = r"C:\temp\vop_output"

    # Run pipeline with appropriate exporter
    if export_csv:
        # Use CSV exporter (also exports JSON and PNG)
        result = run_vop_pipeline_with_csv(
            doc,
            view_ids,
            cfg=config,
            output_dir=output_dir,
            pixels_per_cell=pixels_per_cell,
            export_json=export_json,
            export_png=export_png,
            export_perf_csv=export_perf_csv,
        )

    else:
        # Use PNG-only exporter (legacy)
        result = run_vop_pipeline_with_png(
            doc,
            view_ids,
            cfg=config,
            output_dir=output_dir,
            pixels_per_cell=pixels_per_cell,
            export_json=export_json,
        )

    # Add filtering info if verbose
    if verbose:
        filter_info = filter_supported_views(views_input)
        result['filter_info'] = filter_info

    return result
