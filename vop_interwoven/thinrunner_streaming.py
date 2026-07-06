"""
VOP Interwoven Pipeline - Thin Runner for Dynamo (STREAMING VERSION)

Quick test runner with module reloading for development iteration.
Uses streaming pipeline to minimize memory usage.

Usage:
    IN[0] = List of views (or None/empty for current view)
    IN[1] = Optional tag override (e.g., commit hash, "baseline")
    IN[2] = Optional output directory
    IN[3] = Optional batch size (int)
    IN[4] = Export view raster PNGs — raw Revit view images for comparison (bool, default True)
    IN[5] = Enable Stage A color ID-buffer extraction (bool, default False)

Output:
    Summary string with view count, annotation count, CSV paths
"""

import sys
import os
import ctypes
import shutil

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




def _to_sequence(value):
    """Convert Dynamo/.NET collections to a flat Python list without exploding strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]

    items = []

    def _walk(v):
        if v is None:
            return
        if isinstance(v, str):
            items.append(v)
            return
        if isinstance(v, tuple):
            for x in v:
                _walk(x)
            return
        if isinstance(v, list):
            for x in v:
                _walk(x)
            return
        try:
            iterator = iter(v)
            for x in iterator:
                _walk(x)
            return
        except Exception:
            items.append(v)

    _walk(value)
    return items


def _resolve_view_object(doc, candidate):
    """Return a view object when possible; preserve candidate if resolution fails."""
    if candidate is None:
        return None

    try:
        if hasattr(candidate, "GenLevel") or hasattr(candidate, "ViewType"):
            return candidate
    except Exception:
        pass

    try:
        from Autodesk.Revit.DB import ElementId
    except Exception:
        ElementId = None

    try:
        if hasattr(candidate, "Id"):
            resolved = doc.GetElement(candidate.Id)
            if resolved is not None:
                return resolved
    except Exception:
        pass

    try:
        resolved = doc.GetElement(candidate)
        if resolved is not None:
            return resolved
    except Exception:
        pass

    try:
        if isinstance(candidate, int) and ElementId is not None:
            resolved = doc.GetElement(ElementId(int(candidate)))
            if resolved is not None:
                return resolved
    except Exception:
        pass

    try:
        if isinstance(candidate, str) and candidate.isdigit() and ElementId is not None:
            resolved = doc.GetElement(ElementId(int(candidate)))
            if resolved is not None:
                return resolved
    except Exception:
        pass

    return candidate


def _build_views_from_input(doc, views_input):
    """Normalize IN[0] into a list of view-like objects."""
    if views_input is None:
        current_view = get_current_view()
        return [current_view] if current_view else []

    raw_items = _to_sequence(views_input)
    views = []
    for item in raw_items:
        resolved = _resolve_view_object(doc, item)
        if resolved is not None:
            views.append(resolved)
    return views



def _coerce_view_id(value):
    """Best-effort coercion to an integer view id for pipeline compatibility."""
    if value is None:
        return None

    try:
        if hasattr(value, "IntegerValue"):
            return int(value.IntegerValue)
    except Exception:
        pass

    try:
        if hasattr(value, "Id") and hasattr(value.Id, "IntegerValue"):
            return int(value.Id.IntegerValue)
    except Exception:
        pass

    try:
        if isinstance(value, int):
            return int(value)
    except Exception:
        pass

    try:
        if isinstance(value, str) and value.isdigit():
            return int(value)
    except Exception:
        pass

    try:
        return int(value)
    except Exception:
        return None


def _safe_level_elevation(doc, view):
    """Best-effort elevation lookup for a view."""
    elevation = None
    level_name = None

    try:
        gen_level = getattr(view, "GenLevel", None)
        if gen_level is not None:
            elevation = float(getattr(gen_level, "Elevation", 0.0))
            level_name = getattr(gen_level, "Name", None)
            return elevation, level_name, "GenLevel"
    except Exception:
        pass

    try:
        from Autodesk.Revit.DB import BuiltInParameter, ElementId
        p = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
        if p is not None:
            ref_id = p.AsElementId()
            if ref_id is not None and ref_id != ElementId.InvalidElementId:
                ref_elem = doc.GetElement(ref_id)
                if ref_elem is not None and hasattr(ref_elem, "Elevation"):
                    elevation = float(getattr(ref_elem, "Elevation", 0.0))
                    level_name = getattr(ref_elem, "Name", None)
                    return elevation, level_name, "VOI"
    except Exception:
        pass

    return None, None, None


def sort_views_by_level(doc, views):
    """Sort views by level elevation (ascending) with graceful fallback."""
    if not views:
        return views

    try:
        decorated = []
        elevations_found = 0

        for index, view in enumerate(views):
            elev, level_name, source = _safe_level_elevation(doc, view)
            view_name = getattr(view, "Name", "") or ""
            has_elev = elev is not None
            if has_elev:
                elevations_found += 1

            decorated.append({
                "view": view,
                "index": index,
                "view_name": view_name,
                "has_elev": has_elev,
                "elev": elev if has_elev else 0.0,
                "level_name": level_name,
                "source": source,
            })

        if elevations_found == 0:
            print("[VOP] sort_views_by_level: no level elevations found; preserving input order.")
            return views

        sorted_decorated = sorted(
            decorated,
            key=lambda x: (
                0 if x["has_elev"] else 1,
                x["elev"],
                x["index"],
            ),
        )

        print("[VOP] Sorting {} views by level elevation...".format(len(views)))
        grouped = []
        current_key = None
        current_count = 0
        for item in sorted_decorated:
            if item["has_elev"]:
                key = (item["level_name"] or "(unnamed level)", item["elev"])
            else:
                key = ("(no level)", None)

            if key != current_key:
                if current_key is not None:
                    grouped.append((current_key, current_count))
                current_key = key
                current_count = 1
            else:
                current_count += 1
        if current_key is not None:
            grouped.append((current_key, current_count))

        group_lines = []
        for key, count in grouped:
            level_name = key[0]
            elev = key[1]
            if elev is None:
                group_lines.append("{} ({})".format(level_name, count))
            else:
                group_lines.append("{} (elev={:.3f}ft, {} views)".format(level_name, elev, count))
        print("[VOP] Sort order: {}".format(", ".join(group_lines)))

        return [x["view"] for x in sorted_decorated]
    except Exception as e:
        print("[VOP] sort_views_by_level failed: {}. Preserving input order.".format(e))
        return views


def _chunk_list(items, chunk_size):
    if not chunk_size or chunk_size <= 0:
        return [items]
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def _append_csv(source_path, target_path):
    if not source_path or not os.path.exists(source_path):
        return

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    if not os.path.exists(target_path):
        shutil.copyfile(source_path, target_path)
        return

    with open(source_path, "r", encoding="utf-8") as src:
        src_lines = src.readlines()
    if not src_lines:
        return
    payload = src_lines[1:] if len(src_lines) > 1 else []
    if not payload:
        return
    with open(target_path, "a", encoding="utf-8") as dst:
        dst.writelines(payload)


def _run_gc_between_chunks():
    try:
        import System
        System.GC.Collect()
        System.GC.WaitForPendingFinalizers()
        System.GC.Collect()
    except Exception:
        try:
            import gc
            gc.collect()
        except Exception:
            pass

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

    # Optional Stage A color ID-buffer path. This must be set on the same
    # Config instance passed into run_vop_pipeline_streaming(); otherwise the
    # pipeline falls through to the legacy occlusion/silhouette path.
    enable_color_id_buffer_stage_a = (
        bool(IN[5]) if len(IN) > 5 and IN[5] is not None else False
    )
    cfg.enable_color_id_buffer_stage_a = enable_color_id_buffer_stage_a
    if enable_color_id_buffer_stage_a:
        # Stage A mode should only emit color_id_buffer TIFF/JSON plus its
        # minimal diagnostics; suppress legacy cache/map side outputs that make
        # it look like the silhouette pipeline ran.
        cfg.use_element_cache = False
        cfg.element_cache_persist = False
        cfg.element_cache_export_csv = False
        cfg.element_cache_detect_changes = False
        cfg.view_cache_enabled = False

    print("="*60)
    print("DEBUG: About to call streaming")
    print("  cfg.view_cache_enabled = {}".format(cfg.view_cache_enabled))
    print("  cfg.view_cache_dir = {}".format(cfg.view_cache_dir))
    print("  cfg.use_element_cache = {}".format(cfg.use_element_cache))
    print("  cfg.enable_color_id_buffer_stage_a = {}".format(
        cfg.enable_color_id_buffer_stage_a
    ))
    print("="*60)

    # Optional batch size override
    batch_size = IN[3] if len(IN) > 3 and IN[3] else None

    # Export raw Revit view PNGs alongside VOP raster PNGs (view_raster/ folder)
    export_view_raster = bool(IN[4]) if len(IN) > 4 and IN[4] is not None else True
    if batch_size is not None:
        try:
            batch_size = int(batch_size)
            if batch_size <= 0:
                batch_size = None
        except Exception:
            print("[VOP] Invalid batch_size '{}'; ignoring batching.".format(batch_size))
            batch_size = None

    # Get view objects from input
    views = _build_views_from_input(doc, views_input)
    print("[VOP] Input view count: {}".format(len(views)))

    # Apply level sorting unless disabled
    if bool(getattr(cfg, "sort_views_by_level", True)) and views:
        views = sort_views_by_level(doc, views)
    else:
        print("[VOP] sort_views_by_level disabled; preserving input order.")

    # Assemble view IDs (existing shape remains unchanged)
    view_ids = [_coerce_view_id(v) for v in views]
    view_ids = [vid for vid in view_ids if vid is not None]

    # Use STREAMING pipeline (no cache, minimal memory)
    from vop_interwoven.streaming import run_vop_pipeline_streaming
    
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    try:
        batches = _chunk_list(view_ids, batch_size)
        result = None

        if batch_size:
            print("[VOP] Batch size requested: {}".format(batch_size))
            plan = [len(b) for b in batches]
            print("[VOP] Planned batch sizes: {}".format(plan))

        if len(batches) <= 1:
            result = run_vop_pipeline_streaming(
                doc=doc,
                view_ids=view_ids,
                cfg=cfg,
                output_dir=output_dir,
                export_png=not enable_color_id_buffer_stage_a,
                export_csv=not enable_color_id_buffer_stage_a,
                export_json=False,
                export_view_raster=(export_view_raster and not enable_color_id_buffer_stage_a),
                pixels_per_cell=10,
                date_override=tag_override,
            )
        else:
            print("[VOP] Streaming exporter rewrites CSVs per run; enabling batch CSV append merge.")
            merged = {
                "views_processed": 0,
                "views_failed": 0,
                "png_files": [],
                "view_raster_files": [],
                "csv_rows_written": 0,
                "view_summaries": [],
                "core_csv_path": None,
                "vop_csv_path": None,
                "occlusion_csv_path": None,
                "perf_csv_path": None,
            }

            for batch_index, batch_view_ids in enumerate(batches):
                start_idx = batch_index * batch_size + 1
                end_idx = start_idx + len(batch_view_ids) - 1
                print("[VOP] Batch {}/{}: views {}-{} ({} ids)".format(
                    batch_index + 1, len(batches), start_idx, end_idx, len(batch_view_ids)
                ))

                batch_output_dir = os.path.join(output_dir, "_batch_tmp_{}".format(batch_index + 1))
                os.makedirs(batch_output_dir, exist_ok=True)

                batch_result = run_vop_pipeline_streaming(
                    doc=doc,
                    view_ids=batch_view_ids,
                    cfg=cfg,
                    output_dir=batch_output_dir,
                    export_png=not enable_color_id_buffer_stage_a,
                    export_csv=not enable_color_id_buffer_stage_a,
                    export_json=False,
                    export_view_raster=(export_view_raster and not enable_color_id_buffer_stage_a),
                    pixels_per_cell=10,
                    date_override=tag_override,
                )

                merged["views_processed"] += batch_result.get("views_processed", 0)
                merged["views_failed"] += batch_result.get("views_failed", 0)
                merged["csv_rows_written"] += batch_result.get("csv_rows_written", 0)
                merged["view_summaries"].extend(batch_result.get("view_summaries", []))
                merged["png_files"].extend(batch_result.get("png_files", []))
                merged["view_raster_files"].extend(batch_result.get("view_raster_files", []))

                for key in ["core_csv_path", "vop_csv_path", "occlusion_csv_path", "perf_csv_path"]:
                    chunk_csv = batch_result.get(key)
                    if not chunk_csv:
                        continue
                    target_csv = os.path.join(output_dir, os.path.basename(chunk_csv))
                    _append_csv(chunk_csv, target_csv)
                    merged[key] = target_csv

                _run_gc_between_chunks()

            result = merged

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        raise

    print("="*60)
    print("DEBUG: After streaming call")
    print("  cfg.view_cache_enabled = {}".format(cfg.view_cache_enabled))
    print("  cfg.view_cache_dir = {}".format(cfg.view_cache_dir))
    print("  cfg.use_element_cache = {}".format(cfg.use_element_cache))
    print("  cfg.enable_color_id_buffer_stage_a = {}".format(
        cfg.enable_color_id_buffer_stage_a
    ))
    print("="*60)

    # Extract results
    view_summaries = result.get('view_summaries', [])
    
    # Build summary
    lines = []
    lines.append("=" * 60)
    lines.append("VOP INTERWOVEN PIPELINE - STREAMING MODE")
    lines.append("=" * 60)
    lines.append("")

    lines.append("Views processed: {}".format(result.get('views_processed', 0)))
    lines.append("Views failed: {}".format(result.get('views_failed', 0)))
    lines.append("")

    # Per-view summary (limited info from lightweight summaries)
    for view_data in view_summaries:
        view_name = view_data.get('view_name', 'Unknown')
        width = view_data.get('width', 0)
        height = view_data.get('height', 0)
        filled = view_data.get('filled_cells', 0)
        
        lines.append("  {}:".format(view_name))
        lines.append("    Grid: {}×{}".format(width, height))
        lines.append("    Filled cells: {}".format(filled))

    lines.append("")


    # Diagnostics summary block
    run_summary = None
    if view_summaries:
        try:
            run_summary = view_summaries[0].get("run_summary")
        except Exception as e:
            print("[thinrunner] failed to read run_summary: {}".format(e))

    if run_summary:
        lines.append("RUN SUMMARY:")
        lines.append(str(run_summary))
        slow3 = (run_summary.get("slowest_views") or [])[:3]
        lines.append("Top-3 slowest views:")
        for sv in slow3:
            lines.append("  - {view_name} ({view_id}): {total_ms} ms elems={element_count}".format(**sv))

    try:
        mem_marks = []
        if view_summaries:
            mem_marks = view_summaries[0].get("memory_tracker", []) or []
        if mem_marks:
            lines.append("Memory tracker marks: {}".format(len(mem_marks)))
            start = mem_marks[0].get("priv_mb")
            end = mem_marks[-1].get("priv_mb")
            if start is not None and end is not None:
                delta = float(end) - float(start)
                n = len(view_summaries)
                if delta > 500:
                    verdict = "[CRIT] Memory grew {:.0f} MB — pipeline at risk".format(delta)
                elif delta > 200:
                    verdict = "[WARN] Memory grew {:.0f} MB — check CLR GC calls".format(delta)
                else:
                    verdict = "[OK] Memory stable: +{:.0f} MB across {} views".format(delta, n)
                lines.append(verdict)
    except Exception as e:
        lines.append("Memory verdict unavailable: {}".format(e))

    try:
        if getattr(cfg, "export_perf_csv", False):
            perf_path = result.get('perf_csv_path', None)
            lines.append("Perf CSV: {}".format(perf_path))
    except Exception as e:
        lines.append("Perf CSV export failed: {}".format(e))

    lines.append("Stage A color ID-buffer: {}".format(
        "enabled" if getattr(cfg, "enable_color_id_buffer_stage_a", False) else "disabled"
    ))

    # File outputs
    png_files = result.get('png_files', [])
    lines.append("PNGs written: {}".format(len(png_files)))
    
    core_csv = result.get('core_csv_path', 'N/A')
    vop_csv = result.get('vop_csv_path', 'N/A')
    
    lines.append("")
    lines.append("CSV Output:")
    lines.append("  Core: {}".format(core_csv))
    lines.append("  VOP:  {}".format(vop_csv))
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
    error_lines.append("{}: {}".format(type(e).__name__, e))
    error_lines.append("")
    
    try:
        error_lines.append("Traceback:")
        error_lines.append(traceback.format_exc())
    except Exception as traceback_error:
        error_lines.append("(Traceback not available: {}: {})".format(
            type(traceback_error).__name__, traceback_error
        ))
    
    error_lines.append("")
    error_lines.append("=" * 60)

    OUT = "\n".join(error_lines)
