"""
Dynamo entry point for VOP Interwoven Pipeline.

Provides a simple interface for testing the pipeline from Dynamo Python nodes.

Compatible with both IronPython (Dynamo 2.x) and CPython3 (Dynamo 3.3+).

Usage in Dynamo CPython3 (3.3+):
    import sys
    sys.path.append(r'C:\path\to\Revit_SSM_Exporter')

    from vop_interwoven.entry_dynamo import run_vop_pipeline, get_current_document
    from vop_interwoven.config import Config

    # Get current Revit document (CPython3-compatible)
    doc = get_current_document()
    view = IN[0]  # Pass view as input, or use get_current_view()

    # Configure pipeline
    cfg = Config(
        tile_size=16,
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=2,
        thin_max=2
    )

    # Run pipeline on current view
    result = run_vop_pipeline(doc, [view.Id], cfg)

    # Output result for Dynamo
    OUT = result

Usage in Dynamo IronPython (2.x - legacy):
    # Same as above, but __revit__ global is available
    doc = __revit__.ActiveUIDocument.Document
    view = __revit__.ActiveUIDocument.ActiveView
"""

import json
from .config import Config
from .pipeline import process_document_views


# ============================================================
# REVIT CONTEXT HELPERS (CPython3-compatible)
# ============================================================


def get_current_document():
    """Get current Revit document (works in both IronPython and CPython3).

    Returns:
        Revit Document object

    Raises:
        RuntimeError: If not running in Revit/Dynamo context
    """
    # Try CPython3 approach first (Dynamo 3.3+)
    try:
        from Autodesk.Revit.DB import Document
        # Import Revit services for CPython3
        import RevitServices
        from RevitServices.Persistence import DocumentManager

        doc = DocumentManager.Instance.CurrentDBDocument
        if doc is not None:
            return doc
    except ImportError:
        pass  # Fall through to IronPython approach

    # Try IronPython approach (Dynamo 2.x)
    try:
        doc = __revit__.ActiveUIDocument.Document
        return doc
    except NameError:
        pass

    raise RuntimeError(
        "Not running in Revit/Dynamo context. "
        "Use get_current_document() in Dynamo Python node, "
        "or pass Document directly to run_vop_pipeline()."
    )


def get_current_view():
    """Get current active view (works in both IronPython and CPython3).

    Returns:
        Revit View object

    Raises:
        RuntimeError: If not running in Revit/Dynamo context
    """
    # Try CPython3 approach first
    try:
        import RevitServices
        from RevitServices.Persistence import DocumentManager

        doc = DocumentManager.Instance.CurrentDBDocument
        if doc is not None:
            # Get active view from document
            active_view = doc.ActiveView
            if active_view is not None:
                return active_view
    except (ImportError, AttributeError):
        pass

    # Try IronPython approach
    try:
        view = __revit__.ActiveUIDocument.ActiveView
        return view
    except NameError:
        pass

    raise RuntimeError(
        "Not running in Revit/Dynamo context. "
        "Pass view as input (IN[0]) or get from Document."
    )


def run_vop_pipeline(doc, view_ids, cfg=None):
    """Run VOP interwoven pipeline on specified views.

    Args:
        doc: Revit Document (from __revit__.ActiveUIDocument.Document)
        view_ids: List of Revit View ElementIds (or ints), or single ElementId/int
        cfg: Config object (optional, uses defaults if None)

    Returns:
        Dictionary with results:
        {
            'success': bool,
            'views': list of view results,
            'config': config dict,
            'errors': list of error messages
        }

    Example (in Dynamo Python node):
        >>> doc = __revit__.ActiveUIDocument.Document
        >>> view = __revit__.ActiveUIDocument.ActiveView
        >>> result = run_vop_pipeline(doc, [view.Id])
        >>> OUT = result
    """
    # Default config if not provided
    if cfg is None:
        cfg = Config()

    # Normalize view_ids to list
    if not isinstance(view_ids, list):
        view_ids = [view_ids]

    errors = []
    views = []

    try:
        # Run pipeline
        results = process_document_views(doc, view_ids, cfg)
        views = results

    except Exception as e:
        errors.append(f"Pipeline error: {str(e)}")

    return {
        "success": len(errors) == 0,
        "views": views,
        "config": cfg.to_dict(),
        "errors": errors,
        "summary": {
            "num_views_requested": len(view_ids),
            "num_views_processed": len(views),
            "num_errors": len(errors),
        },
    }


def run_vop_pipeline_json(doc, view_ids, cfg=None, output_path=None):
    """Run VOP pipeline and export results to JSON file.

    Args:
        doc: Revit Document
        view_ids: List of View ElementIds (or ints)
        cfg: Config object (optional)
        output_path: Path to output JSON file (optional)

    Returns:
        Dictionary with results (same as run_vop_pipeline)
        Also writes JSON file if output_path is provided

    Example:
        >>> result = run_vop_pipeline_json(
        ...     doc,
        ...     [view.Id],
        ...     output_path=r'C:\temp\vop_export.json'
        ... )
    """
    result = run_vop_pipeline(doc, view_ids, cfg)

    if output_path:
        try:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
            result["json_export_path"] = output_path
        except Exception as e:
            result["errors"].append(f"JSON export error: {str(e)}")
            result["success"] = False

    return result


def get_test_config_tiny():
    """Get config optimized for testing with TINY elements (doors, windows).

    Returns:
        Config with tiny_max=3, thin_max=2
    """
    return Config(
        tile_size=16,
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=3,  # Slightly larger threshold for testing
        thin_max=2,
    )


def get_test_config_linear():
    """Get config optimized for testing with LINEAR elements (walls).

    Returns:
        Config with larger thin_max for linear classification
    """
    return Config(
        tile_size=16,
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=2,
        thin_max=5,  # Allow thicker elements to be LINEAR
    )


def get_test_config_areal_heavy():
    """Get config optimized for testing with AREAL elements (floors, roofs).

    Returns:
        Config with strict thresholds -> more AREAL classification
    """
    return Config(
        tile_size=32,  # Larger tiles for big elements
        over_model_includes_proxies=True,
        proxy_mask_mode="minmask",
        depth_eps_ft=0.01,
        tiny_max=1,  # Strict TINY threshold
        thin_max=1,  # Strict LINEAR threshold -> most things are AREAL
    )


# Convenience function for quick testing
def quick_test_current_view():
    """Quick test on current active view (CPython3-compatible).

    Usage in Dynamo Python console:
        >>> from vop_interwoven.entry_dynamo import quick_test_current_view
        >>> result = quick_test_current_view()
        >>> print(result['summary'])

    Returns:
        Result dictionary from run_vop_pipeline
    """
    try:
        # Get current document and view (CPython3-compatible)
        doc = get_current_document()
        view = get_current_view()

        # Run with default config
        result = run_vop_pipeline(doc, [view.Id])

        # Try to show summary dialog (works in both IronPython and CPython3)
        try:
            from Autodesk.Revit.UI import TaskDialog

            summary = result["summary"]
            msg = "VOP Pipeline Test:\n\n"
            msg += f"Views requested: {summary['num_views_requested']}\n"
            msg += f"Views processed: {summary['num_views_processed']}\n"
            msg += f"Errors: {summary['num_errors']}\n"

            if result["views"]:
                diag = result["views"][0].get("diagnostics", {})
                msg += f"\nElements: {diag.get('num_elements', 0)}\n"
                msg += f"Filled cells: {diag.get('num_filled_cells', 0)}"

            TaskDialog.Show("VOP Test Result", msg)
        except ImportError:
            # TaskDialog not available, just return result
            pass

        return result

    except Exception as e:
        return {"success": False, "errors": [str(e)], "summary": {}}


if __name__ == "__main__":
    # When run as script in Dynamo, test current view
    try:
        result = quick_test_current_view()
        OUT = result
    except:
        OUT = {"success": False, "errors": ["Not running in Revit/Dynamo context"]}
