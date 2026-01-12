"""
VOP Interwoven Pipeline - Thin Runner for Dynamo (STREAMING VERSION)

Quick test runner with module reloading for development iteration.
Uses streaming pipeline to minimize memory usage.

Usage:
    IN[0] = List of views (or None/empty for current view)
    IN[1] = Optional tag override (e.g., commit hash, "baseline")
    IN[2] = Optional output directory

Output:
    Summary string with view count, annotation count, CSV paths
"""

import sys
import os

# Add project to path
PROJECT_PATH = r'C:\Users\gmcdowell\Documents\Revit_SSM_Exporter'
if PROJECT_PATH not in sys.path:
    sys.path.insert(0, PROJECT_PATH)

# Module reloading for development (ensures latest code is used)
RELOAD_MODULES = True

if RELOAD_MODULES:
    # Remove all vop_interwoven modules to force reload
    modules_to_remove = [key for key in sys.modules.keys() if key.startswith('vop_interwoven')]
    for mod in modules_to_remove:
        del sys.modules[mod]

# Now import after cleanup
from vop_interwoven.entry_dynamo import get_current_document, get_current_view

# ============================================================================
# RUN PIPELINE
# ============================================================================

try:
    doc = get_current_document()

    # Get views from IN[0] or use current view
    views_input = IN[0] if len(IN) > 0 and IN[0] else None

    # Optional custom tag override (for deterministic exports / run labeling)
    tag_override = IN[1] if len(IN) > 1 and IN[1] else None

    # Optional output directory override
    output_dir = IN[2] if len(IN) > 2 and IN[2] else r'C:\temp\vop_output'

    # Build config
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.debug_json_detail = "summary"

    print("="*60)
    print("DEBUG: About to call streaming")
    print(f"  cfg.view_cache_enabled = {cfg.view_cache_enabled}")
    print(f"  cfg.view_cache_dir = {cfg.view_cache_dir}")
    print("="*60)

    # Get view IDs
    if views_input is None:
        # Use current view
        current_view = get_current_view()
        view_ids = [current_view.Id] if current_view else []
    elif isinstance(views_input, list):
        view_ids = [v.Id if hasattr(v, 'Id') else v for v in views_input]
    else:
        view_ids = [views_input.Id if hasattr(views_input, 'Id') else views_input]

    # Use STREAMING pipeline (no cache, minimal memory)
    from vop_interwoven.streaming import run_vop_pipeline_streaming
    
    result = run_vop_pipeline_streaming(
        doc=doc,
        view_ids=view_ids,
        cfg=cfg,
        output_dir=output_dir,
        export_png=True,
        export_csv=True,  # Always export CSV (tag override just affects Date/RunId columns)
        export_json=False,
        pixels_per_cell=10,
        date_override=tag_override,
    )

    print("="*60)
    print("DEBUG: After streaming call")
    print(f"  cfg.view_cache_enabled = {cfg.view_cache_enabled}")
    print(f"  cfg.view_cache_dir = {cfg.view_cache_dir}")
    print("="*60)

    # Extract results
    view_summaries = result.get('view_summaries', [])
    
    # Build summary
    lines = []
    lines.append("=" * 60)
    lines.append("VOP INTERWOVEN PIPELINE - STREAMING MODE")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"Views processed: {result.get('views_processed', 0)}")
    lines.append(f"Views failed: {result.get('views_failed', 0)}")
    lines.append("")

    # Per-view summary (limited info from lightweight summaries)
    for view_data in view_summaries:
        view_name = view_data.get('view_name', 'Unknown')
        width = view_data.get('width', 0)
        height = view_data.get('height', 0)
        filled = view_data.get('filled_cells', 0)
        
        lines.append(f"  {view_name}:")
        lines.append(f"    Grid: {width}×{height}")
        lines.append(f"    Filled cells: {filled}")

    lines.append("")

    # File outputs
    png_files = result.get('png_files', [])
    lines.append(f"PNGs written: {len(png_files)}")
    
    core_csv = result.get('core_csv_path', 'N/A')
    vop_csv = result.get('vop_csv_path', 'N/A')
    
    lines.append("")
    lines.append("CSV Output:")
    lines.append(f"  Core: {core_csv}")
    lines.append(f"  VOP:  {vop_csv}")
    lines.append("")
    
    # Memory benefit
    lines.append("Memory: Streaming mode (minimal footprint)")
    lines.append("")

    lines.append("=" * 60)
    lines.append("STATUS: SUCCESS")
    lines.append("=" * 60)

    OUT = "\n".join(lines)

except Exception as e:
    import traceback
    error_lines = []
    error_lines.append("=" * 60)
    error_lines.append("ERROR")
    error_lines.append("=" * 60)
    error_lines.append("")
    error_lines.append(f"{type(e).__name__}: {e}")
    error_lines.append("")
    
    try:
        error_lines.append("Traceback:")
        error_lines.append(traceback.format_exc())
    except:
        error_lines.append("(Traceback not available)")
    
    error_lines.append("")
    error_lines.append("=" * 60)

    OUT = "\n".join(error_lines)
