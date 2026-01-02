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

    # Handle list
    if isinstance(views_input, list):
        view_ids = []
        for item in views_input:
            if isinstance(item, View):
                view_ids.append(item.Id)
            elif isinstance(item, ElementId):
                view_ids.append(item)
            elif isinstance(item, int):
                view_ids.append(item)
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


def run_pipeline_from_dynamo_input(
    views_input=None,
    output_dir=None,
    pixels_per_cell=4,
    config=None
):
    """Run VOP pipeline with Dynamo-friendly inputs.

    Args:
        views_input: Views from IN[0] (single view, list, or None for current)
        output_dir: Output directory (default: C:\\temp\\vop_output)
        pixels_per_cell: PNG resolution (default: 4)
        config: Config object (default: None, uses defaults)

    Returns:
        Dictionary with pipeline_result, json_path, png_files

    Usage in Dynamo Python node:
        >>> # Use views from IN[0], or current view if empty
        >>> result = run_pipeline_from_dynamo_input(IN[0] if len(IN) > 0 else None)
        >>> OUT = result['png_files']
    """
    # Get document
    doc = get_current_document()

    # Get views (from input or current)
    view_ids = get_views_from_input_or_current(views_input)

    # Use default config if not provided
    if config is None:
        config = Config()

    # Run pipeline
    result = run_vop_pipeline_with_png(
        doc,
        view_ids,
        cfg=config,
        output_dir=output_dir,
        pixels_per_cell=pixels_per_cell
    )

    return result
