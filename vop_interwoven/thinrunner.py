"""
VOP Interwoven Pipeline - Thin Runner for Dynamo

Quick test runner with module reloading for development iteration.

Usage:
    IN[0] = List of views (or None/empty for current view)

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
from vop_interwoven.dynamo_helpers import run_pipeline_from_dynamo_input
from vop_interwoven.entry_dynamo import get_current_document

# ============================================================================
# RUN PIPELINE
# ============================================================================

try:
    doc = get_current_document()

    # Get views from IN[0] or use current view
    views_input = IN[0] if len(IN) > 0 and IN[0] else None

    # Optional date override (for deterministic exports / backdated runs)
    date_override = IN[1] if len(IN) > 1 and IN[1] else None

    # Build a config so we can reuse it if we export CSV manually
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.debug_json_detail="summary"

    # Run pipeline (export CSV directly only when no override is requested)
    do_export_csv = False if date_override else True

    result = run_pipeline_from_dynamo_input(
        views_input=views_input,
        output_dir=r'C:\temp\vop_output',
        pixels_per_cell=10,
        config=cfg,
        export_csv=do_export_csv,
        export_json=True,
        export_png=True,  # Skip PNG for speed
        verbose=False
    )

    # If caller requested date override, export CSV here so filenames + Date column match.
    if date_override:
        from vop_interwoven.csv_export import export_pipeline_to_csv
        doc = get_current_document()
        pipeline_result = result.get('pipeline_result', {})
        csv_info = export_pipeline_to_csv(
            pipeline_result=pipeline_result,
            output_dir=r'C:\temp\vop_output',
            config=cfg,
            doc=doc,
            diag=None,
            date_override=str(date_override),
        )
        # Mirror keys expected by downstream consumers
        result['core_csv_path'] = csv_info.get('core_csv_path')
        result['vop_csv_path'] = csv_info.get('vop_csv_path')
        result['rows_exported'] = csv_info.get('rows_exported', 0)

    # Extract results
    pipeline_result = result.get('pipeline_result', {})
    views = pipeline_result.get('views', [])

    # Build summary
    lines = []
    lines.append("=" * 60)
    lines.append("VOP INTERWOVEN PIPELINE - THIN RUNNER")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"Views processed: {len(views)}")
    lines.append("")

    # Per-view summary
    total_annos = 0
    total_model = 0

    for view_data in views:
        view_name = view_data.get('view_name', 'Unknown')
        diag = view_data.get('diagnostics', {})

        num_annos = diag.get('num_annotations', 0)
        num_elems = diag.get('num_elements', 0)

        total_annos += num_annos
        total_model += num_elems

        lines.append(f"  {view_name}:")
        lines.append(f"    Model elements: {num_elems}")
        lines.append(f"    Annotations: {num_annos}")

    lines.append("")
    lines.append(f"Total model elements: {total_model}")
    lines.append(f"Total annotations: {total_annos}")
    lines.append("")

    # CSV paths
    core_csv = result.get('core_csv_path', 'N/A')
    vop_csv = result.get('vop_csv_path', 'N/A')

    lines.append("CSV Output:")
    lines.append(f"  Core: {core_csv}")
    lines.append(f"  VOP:  {vop_csv}")
    lines.append("")

    lines.append("=" * 60)
    lines.append("STATUS: SUCCESS")
    lines.append("=" * 60)

    OUT = "\n".join(lines)

except Exception as e:
    error_lines = []
    error_lines.append("=" * 60)
    error_lines.append("ERROR")
    error_lines.append("=" * 60)
    error_lines.append("")
    error_lines.append(f"{type(e).__name__}: {e}")
    error_lines.append("")
    error_lines.append("NOTE: Dynamo CPython host may not include stdlib traceback; no stack trace available.")
    error_lines.append("If pipeline_result contains per-view 'diag', inspect that for callsite + exc_message.")
    error_lines.append("")
    error_lines.append("=" * 60)

    OUT = "\n".join(error_lines)

