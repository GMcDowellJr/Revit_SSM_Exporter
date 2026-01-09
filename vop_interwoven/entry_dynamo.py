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
import time

import csv
from datetime import datetime

try:
    from .config import Config
    from .pipeline import process_document_views
except Exception:
    # Dynamo sometimes imports modules without package context; fall back to absolute.
    from vop_interwoven.config import Config
    from vop_interwoven.pipeline import process_document_views

import copy

def _prune_view_raster_for_json(view_result, detail):
    """Mutate one view_result dict in-place to reduce JSON size."""
    if not isinstance(view_result, dict):
        return

    r = view_result.get("raster")
    if not isinstance(r, dict):
        return

    d = (detail or "full").strip().lower()
    if d not in ("summary", "medium", "full"):
        d = "full"

    if d == "full":
        r["debug_detail"] = "full"
        return

    # Always keep these
    keep_keys = {"width", "height", "cell_size_ft", "bounds_xy"}
    pruned = {k: r.get(k) for k in keep_keys if k in r}
    pruned["debug_detail"] = d

    if d == "medium":
        # Keep meta + light stats if present
        for k in ("element_meta", "anno_meta", "depth_test_attempted", "depth_test_wins", "depth_test_rejects", "counts", "depth_test_stats"):
            if k in r:
                pruned[k] = r.get(k)

        # If counts aren't present (they usually aren't in raster dict), we can rely on view_result["diagnostics"]
        # so we don't compute anything here.

    view_result["raster"] = pruned


def _pipeline_result_for_json(pipeline_result, cfg):
    """Return a JSON-safe COPY of pipeline_result with raster payload pruned per cfg.debug_json_detail.

    IMPORTANT:
      - Avoid copy.deepcopy(): Dynamo/Revit objects in diagnostics/meta can throw during deepcopy.
      - This function must not mutate the in-memory pipeline_result (PNG/CSV need full raster payload).
    """
    # Resolve detail level
    detail = "full"
    try:
        detail = getattr(cfg, "debug_json_detail", "full")
    except Exception:
        detail = "full"

    d = (detail or "full").strip().lower()
    if d not in ("summary", "medium", "full"):
        d = "full"

    # Shallow copy top-level dict (no deepcopy of .NET objects)
    if not isinstance(pipeline_result, dict):
        return {"success": False, "views": [], "errors": ["pipeline_result not dict"], "summary": {}}

    pr = {}
    for k, v in pipeline_result.items():
        # We'll rebuild "views" below; everything else is shallow-copied
        if k == "views":
            continue
        pr[k] = v

    views = pipeline_result.get("views")
    if isinstance(views, list):
        pr_views = []
        for view_result in views:
            if not isinstance(view_result, dict):
                pr_views.append(view_result)
                continue

            # Shallow copy per-view dict
            vr = dict(view_result)

            # Prune raster payload on the COPY only
            try:
                _prune_view_raster_for_json(vr, d)
            except Exception:
                # Never block export; keep whatever raster shape exists
                pass

            pr_views.append(vr)

        pr["views"] = pr_views
    else:
        pr["views"] = views

    return pr

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


def _normalize_view_ids(view_ids):
    """Normalize Dynamo/Revit view inputs into a list of Revit ElementIds/ints.

    Accepts:
      - single view Element / View / ElementId / int
      - list/tuple/set of the above
      - Dynamo Revit wrapper elements (with .Id or .InternalElement/.InternalElementId)
    """
    if view_ids is None:
        return []

    if isinstance(view_ids, (list, tuple, set)):
        items = list(view_ids)
    else:
        items = [view_ids]

    normalized = []
    for v in items:
        if v is None:
            continue

        # Revit View/Element -> use .Id
        if hasattr(v, "Id"):
            try:
                normalized.append(v.Id)
                continue
            except Exception:
                pass

        # Dynamo wrapper: try InternalElement / InternalElementId
        for attr in ("InternalElementId", "InternalElement"):
            if hasattr(v, attr):
                try:
                    inner = getattr(v, attr)
                    if inner is None:
                        continue
                    if attr == "InternalElement":
                        # inner is a Revit element
                        if hasattr(inner, "Id"):
                            normalized.append(inner.Id)
                            break
                    else:
                        normalized.append(inner)
                        break
                except Exception:
                    pass
        else:
            # ElementId has IntegerValue; keep as-is
            normalized.append(v)

    return normalized


def run_vop_pipeline(doc, view_ids, cfg=None):
    """Run VOP interwoven pipeline on specified views.

    Args:
        doc: Revit Document (from __revit__.ActiveUIDocument.Document)
        view_ids: List of View ElementIds (or ints), or single View/ElementId/int
        cfg: Config object (optional, uses defaults if None)

    Returns:
        Dictionary with results:
        {
            'success': bool,
            'views': list of view results,
            'config': config dict,
            'errors': list of error messages,
            'summary': dict
        }
    """
    if doc is None:
        return {"success": False, "views": [], "config": {}, "errors": ["doc is None"], "summary": {}}

    # Default config if not provided
    if cfg is None:
        cfg = Config()

    # Normalize view_ids
    view_ids = _normalize_view_ids(view_ids)

    errors = []
    views = []

    try:
        views = process_document_views(doc, view_ids, cfg)
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

def run_vop_pipeline_with_png(doc, view_ids, cfg=None, output_dir=None, pixels_per_cell=4, export_json=True):
    """Run VOP pipeline and export both JSON and PNG files.

    Args:
        doc: Revit Document
        view_ids: List of View ElementIds (or ints)
        cfg: Config object (optional)
        output_dir: Directory for output files (default: C:\\temp\\vop_output)
        pixels_per_cell: Pixels per raster cell for PNG (default: 4)

    Returns:
        Dictionary with:
        {
            'pipeline_result': {...},
            'json_path': 'path/to/export.json',
            'png_files': ['path/to/view1.png', ...]
        }

    Example:
        >>> from vop_interwoven.entry_dynamo import run_vop_pipeline_with_png
        >>> result = run_vop_pipeline_with_png(doc, [view.Id], output_dir=r'C:\temp\vop')
        >>> OUT = f"Exported {len(result['png_files'])} PNGs"
    """
    import os
    import json
    from vop_interwoven.png_export import export_pipeline_results_to_pngs  # Absolute import for Dynamo

    # Default output directory
    if output_dir is None:
        output_dir = r"C:\temp\vop_output"

    # Default view-cache location colocated with outputs (persistent between runs)
    try:
        if cfg is not None and getattr(cfg, "view_cache_dir", None) in (None, ""):
            cfg.view_cache_dir = os.path.join(output_dir, ".vop_view_cache")
    except Exception:
        pass

    # Run pipeline
    pipeline_result = run_vop_pipeline(doc, view_ids, cfg)

    # Export JSON
    json_filename = "vop_export.json"
    json_path = os.path.join(output_dir, json_filename)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if export_json:
        json_payload = _pipeline_result_for_json(pipeline_result, cfg)
        with open(json_path, 'w') as f:
            json.dump(json_payload, f, indent=2, default=str)
    else:
        json_path = None

    # Export PNGs (with cut vs projection distinction)
    t0 = time.perf_counter()
    png_files = export_pipeline_results_to_pngs(
        pipeline_result,
        output_dir,
        pixels_per_cell=pixels_per_cell,
        cut_vs_projection=True
    )
    t1 = time.perf_counter()
    png_export_ms = (t1 - t0) * 1000.0

    return {
        'pipeline_result': pipeline_result,
        'json_path': json_path,
        'png_files': png_files
    }


def run_vop_pipeline_with_csv(doc, view_ids, cfg=None, output_dir=None, pixels_per_cell=4, export_json=False, export_png=True):
    """Run VOP pipeline and export JSON + PNG + CSV files.

    Args:
        doc: Revit Document
        view_ids: List of View ElementIds (or ints)
        cfg: Config object (optional)
        output_dir: Directory for output files (default: C:\\temp\\vop_output)
        pixels_per_cell: Pixels per raster cell for PNG (default: 4)
        export_json: Export JSON file (default: False - disabled for production due to large file size)
        export_png: Export PNG files (default: True)

    Returns:
        Dictionary with:
        {
            'pipeline_result': {...},
            'json_path': 'path/to/export.json' (if export_json=True),
            'png_files': ['path/to/view1.png', ...] (if export_png=True),
            'core_csv_path': 'path/to/views_core_YYYY-MM-DD.csv',
            'vop_csv_path': 'path/to/views_vop_YYYY-MM-DD.csv',
            'rows_exported': int
        }

    Example:
        >>> from vop_interwoven.entry_dynamo import run_vop_pipeline_with_csv
        >>> result = run_vop_pipeline_with_csv(doc, [view.Id])
        >>> print(f"Exported {result['rows_exported']} rows to CSV")
        >>> print(f"Core CSV: {result['core_csv_path']}")
        >>> print(f"VOP CSV: {result['vop_csv_path']}")

    Commentary:
        ✔ One-stop export for JSON + PNG + CSV
        ✔ Matches SSM exporter CSV format
        ✔ CSV invariant validated automatically
    """
    import os
    import json
    from vop_interwoven.png_export import export_pipeline_results_to_pngs
    from vop_interwoven.csv_export import export_pipeline_to_csv

    # Default output directory
    if output_dir is None:
        output_dir = r"C:\temp\vop_output"

    # Default view-cache location colocated with outputs (persistent between runs)
    try:
        if cfg is not None and getattr(cfg, "view_cache_dir", None) in (None, ""):
            cfg.view_cache_dir = os.path.join(output_dir, ".vop_view_cache")
    except Exception:
        pass

    # Ensure output directory exists
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Use default config if not provided
    if cfg is None:
        cfg = Config()

    # Run pipeline
    pipeline_result = run_vop_pipeline(doc, view_ids, cfg)

    result = {
        'pipeline_result': pipeline_result
    }

    # Export JSON (optional)
    if export_json:
        json_filename = "vop_export.json"
        json_path = os.path.join(output_dir, json_filename)

        json_payload = _pipeline_result_for_json(pipeline_result, cfg)
        with open(json_path, 'w') as f:
            json.dump(json_payload, f, indent=2, default=str)

        result['json_path'] = json_path

    # Export PNGs (optional)
    if export_png:
        png_files = export_pipeline_results_to_pngs(
            pipeline_result,
            output_dir,
            pixels_per_cell=pixels_per_cell,
            cut_vs_projection=True
        )
        result['png_files'] = png_files

    # Export CSVs (always)
    t0 = time.perf_counter()
    csv_result = export_pipeline_to_csv(pipeline_result, output_dir, cfg, doc)
    t1 = time.perf_counter()
    result["csv_export_ms"] = (t1 - t0) * 1000.0

    result['core_csv_path'] = csv_result['core_csv_path']
    result['vop_csv_path'] = csv_result['vop_csv_path']
    result['rows_exported'] = csv_result['rows_exported']

    # Export PERF CSV (additional file; per-view coarse timings + png_ms if available)
    perf_filename = "views_perf_{0}.csv".format(datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    perf_path = os.path.join(output_dir, perf_filename)

    perf_fields = [
        "view_id",
        "view_name",
        "success",
        "total_ms",
        "mode_ms",
        "raster_init_ms",
        "collect_ms",
        "model_ms",
        "anno_ms",
        "finalize_ms",
        "export_ms",
        "png_ms",
        "width",
        "height",
        "total_elements",
        "filled_cells",
    ]

    with open(perf_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=perf_fields)
        w.writeheader()

        for v in (pipeline_result.get("views", []) or []):
            if not isinstance(v, dict):
                continue

            timings = v.get("timings") or (v.get("diagnostics", {}) or {}).get("timings") or {}

            row = {
                "view_id": v.get("view_id"),
                "view_name": v.get("view_name"),
                "success": v.get("success", True) if "success" in v else True,
                "total_ms": timings.get("total_ms"),
                "mode_ms": timings.get("mode_ms"),
                "raster_init_ms": timings.get("raster_init_ms"),
                "collect_ms": timings.get("collect_ms"),
                "model_ms": timings.get("model_ms"),
                "anno_ms": timings.get("anno_ms"),
                "finalize_ms": timings.get("finalize_ms"),
                "export_ms": timings.get("export_ms"),
                "png_ms": timings.get("png_ms"),
                "width": v.get("width"),
                "height": v.get("height"),
                "total_elements": v.get("total_elements"),
                "filled_cells": v.get("filled_cells"),
            }
            w.writerow(row)

    result["perf_csv_path"] = perf_path

    return result


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
            from Autodesk.Revit.UI import TaskDialog  # type: ignore

            summary = result.get("summary", {}) or {}
            msg = "VOP Pipeline Test:\n\n"
            msg += f"Views requested: {summary.get('num_views_requested', 0)}\n"
            msg += f"Views processed: {summary.get('num_views_processed', 0)}\n"
            msg += f"Errors: {summary.get('num_errors', 0)}\n"

            views = result.get("views") or []
            if views:
                # NOTE: current code uses "diagnostics" numeric stats; keep as-is for now.
                diag = views[0].get("diagnostics", {}) or {}
                msg += f"\nElements: {diag.get('num_elements', 0)}\n"
                msg += f"Filled cells: {diag.get('num_filled_cells', 0)}"

            TaskDialog.Show("VOP Test Result", msg)
        except ImportError:
            # TaskDialog not available; ignore.
            pass
        except Exception as e:
            # Don't fail the pipeline test because UI failed; surface error in return payload.
            try:
                result.setdefault("errors", []).append(f"TaskDialog failed: {e}")
            except Exception:
                pass

        return result

    except Exception as e:
        return {"success": False, "errors": [str(e)], "summary": {}}


if __name__ == "__main__":
    # When run as script in Dynamo, test current view
    try:
        result = quick_test_current_view()
        OUT = result
    except Exception:
        OUT = {"success": False, "errors": ["Not running in Revit/Dynamo context"]}
