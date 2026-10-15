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
    IN[6] = Enable the Stage A REGISTERED capture (bool, default True): both
            passes in one rolled-back TransactionGroup, registration marks,
            membership white suppression, crop left alone (crop A on a
            crop-inactive view). Needs IN[5] True; runs the annotation pass
            itself. False selects the frame-B two-pass fallback. See
            tools/notes/HANDOFF_REGISTERED_CAPTURE.md.
    IN[7] = Optional path to a recapture_lists.json (tools/find_recapture_views.py):
            RECAPTURE IN PLACE. IN[0] is ignored; each listed run's listed
            views are captured again into that run's own directory, their
            files and rows replaced and stamped (vop_interwoven/recapture.py).
            The open document must be the archive the run was captured from.
            Needs IN[5] True.

Output:
    Summary string with view count, annotation count, CSV paths
"""

import sys
import os
import ctypes
import json

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
from vop_interwoven.capture_run import (  # noqa: F401  (the helpers' old names)
    chunk_list as _chunk_list, append_csv as _append_csv,
    run_gc_between_chunks as _run_gc_between_chunks,
    relocate_batch_stage_a_outputs as _relocate_batch_stage_a_outputs,
)




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

    # Value-first (Revit 2025's 64-bit property) via the shared reader. The
    # previous IntegerValue-first form did not crash -- every read here is
    # guarded -- but on an id outside the int32 range it fell through every
    # branch and returned None, silently losing the view id rather than
    # failing loudly.
    from vop_interwoven.revit.safe_api import element_id_value

    coerced = element_id_value(value)
    if coerced is not None:
        return coerced

    try:
        inner = getattr(value, "Id", None)
    except Exception:
        inner = None
    if inner is not None:
        coerced = element_id_value(inner)
        if coerced is not None:
            return coerced

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


def _describe_suppression(applied_value):
    """Translate an applied_show_shadows/applied_smooth_edges sidecar value
    into a human-readable yes/no status.

    The raw sidecar value names the value ASSIGNED to the Revit property
    (False means "we set it to False", i.e. suppression succeeded) -- not
    whether suppression happened. Printing it directly reads backwards
    (e.g. "Shadows suppressed: False" when shadows WERE suppressed).
    """
    if applied_value is False:
        return "yes"
    if applied_value == "unchanged (failed)":
        return "no (suppression attempt failed)"
    if applied_value == "read_failed":
        return "unknown (state could not be read)"
    if applied_value == "unchanged":
        return "no (already off, or unsupported on this Revit host)"
    return str(applied_value)


def _summarize_stage_a_sidecar(sidecar_path):
    """Read one Stage A view's JSON sidecar and return printable summary lines.

    The sidecar (written by export_color_id_buffer_view) is the only place
    shadow/smooth-edges suppression status, LINK category-filter coloring
    (PR #193), and near-face-W collection (PR #194) are recorded --
    view_summaries only carries view_id/view_name/success/stage/tiff_path/
    sidecar_path for Stage A views, and this runner disables export_json,
    so nothing richer ever reaches the in-memory result. Reading the
    sidecar back here is the only way to surface that data without
    changing streaming.py's or color_id_buffer.py's return shapes.
    """
    if not sidecar_path or not os.path.exists(sidecar_path):
        return ["    Sidecar: (not found: {})".format(sidecar_path)]

    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return ["    Sidecar: unreadable ({}: {})".format(type(e).__name__, e)]

    lines = []
    lines.append("    Shadows suppressed: {}".format(
        _describe_suppression(meta.get("applied_show_shadows"))
    ))
    # Normalised for smooth edges ONLY. A legacy sidecar's "unchanged" was
    # never reachable from a successful AA read, so printing it as "already
    # off, or unsupported" claims more than the capture established. That
    # reading IS correct for applied_show_shadows above, whose writer skips
    # the mutation when shadows are genuinely already off -- which is why
    # the normalisation lives here and not inside _describe_suppression.
    from vop_interwoven.color_id_buffer import normalize_applied_smooth_edges
    lines.append("    Smooth edges suppressed: {}".format(
        _describe_suppression(
            normalize_applied_smooth_edges(meta.get("applied_smooth_edges")))
    ))

    link_map = meta.get("link_category_color_map") or {}
    lines.append("    LINK categories colored: {}".format(len(link_map)))

    near_face_w = meta.get("near_face_w_map") or {}
    host_count = len((near_face_w.get("host") or {}))
    link_count = len((near_face_w.get("link") or {}))
    lines.append("    Near-face-W collected: host={} link={}".format(host_count, link_count))

    # P1 moved the count into capture_integrity; an archived (pre-P1)
    # sidecar still carries it top-level (review, PR #221).
    integrity = meta.get("capture_integrity")
    paint_failures = (integrity.get("paint_failures")
                      if isinstance(integrity, dict) and "paint_failures" in integrity
                      else meta.get("paint_failures"))
    if paint_failures:
        lines.append("    HOST paint failures: {}".format(paint_failures))

    return lines


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
    cfg.color_id_buffer_registered_capture = (
        bool(IN[6]) if len(IN) > 6 and IN[6] is not None else True
    )

    print("="*60)
    print("DEBUG: About to call streaming")
    print("  cfg.view_cache_enabled = {}".format(cfg.view_cache_enabled))
    print("  cfg.view_cache_dir = {}".format(cfg.view_cache_dir))
    print("  cfg.enable_color_id_buffer_stage_a = {}".format(
        cfg.enable_color_id_buffer_stage_a
    ))
    print("  cfg.color_id_buffer_registered_capture = {}".format(
        cfg.color_id_buffer_registered_capture
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

    # Recapture in place (IN[7]): the listed views of existing runs are
    # captured again into their own run directories; IN[0] is ignored. The
    # result has the ordinary shape, so the summary below is shared.
    recapture_lists = IN[7] if len(IN) > 7 and IN[7] else None
    recapture_lines = []
    if recapture_lists:
        if not enable_color_id_buffer_stage_a:
            raise ValueError("IN[7] (recapture) needs IN[5] (Stage A) True")
        from vop_interwoven.recapture import run_recapture
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        try:
            result = run_recapture(doc, recapture_lists, cfg, batch_size=batch_size)
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        run_dir = "; ".join(str(r.get("run_dir")) for r in result["runs"]
                            if not r.get("skipped")) or "(none)"
        for r in result["runs"]:
            recapture_lines.append(
                "  RUN {0}: {1} view(s), {2} chunk(s), {3} processed, {4} failed{5}".format(
                    r.get("run_id"), r.get("views"), r.get("chunks", 0),
                    r.get("views_processed", 0), r.get("views_failed", 0),
                    "  (" + r["skipped"] + ")" if r.get("skipped") else ""))
        for r in result["refused"]:
            recapture_lines.append("  REFUSED {0}: {1}".format(r.get("run_id"), r.get("reason")))
        recapture_lines.append(
            "Next: register_stage_a_annotation.py, stage_a_grid.py, stage_a_kinds.py "
            "and stage_a_grid_rollup.py over each recaptured run.")
    else:
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

        # One run, one directory, batched: vop_interwoven.capture_run holds
        # the loop (shared with the pyRevit batch runner).
        from vop_interwoven.capture_run import run_capture
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        try:
            result, run_dir, shared_run_id = run_capture(
                doc, view_ids, cfg, output_dir, batch_size=batch_size,
                date_override=tag_override, export_view_raster=export_view_raster)
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            raise

    print("="*60)
    print("DEBUG: After streaming call")
    print("  cfg.view_cache_enabled = {}".format(cfg.view_cache_enabled))
    print("  cfg.view_cache_dir = {}".format(cfg.view_cache_dir))
    print("  cfg.enable_color_id_buffer_stage_a = {}".format(
        cfg.enable_color_id_buffer_stage_a
    ))
    print("="*60)

    # Extract results
    view_summaries = result.get('view_summaries', [])
    
    # Build summary
    lines = []
    lines.append("=" * 60)
    lines.append("VOP INTERWOVEN PIPELINE - {}".format(
        "RECAPTURE IN PLACE" if recapture_lists else "STREAMING MODE"))
    lines.append("=" * 60)
    lines.append("")

    lines.append("Views processed: {}".format(result.get('views_processed', 0)))
    lines.append("Views failed: {}".format(result.get('views_failed', 0)))
    lines.extend(recapture_lines)
    lines.append("")

    # Per-view summary (limited info from lightweight summaries)
    for view_data in view_summaries:
        view_name = view_data.get('view_name', 'Unknown')

        if view_data.get('stage') == 'color_id_buffer_stage_a':
            lines.append("  {} (Stage A color ID buffer):".format(view_name))
            lines.append("    TIFF: {}".format(view_data.get('tiff_path')))
            lines.extend(_summarize_stage_a_sidecar(view_data.get('sidecar_path')))
            continue

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
    lines.append("Run directory: {}".format(run_dir))
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
