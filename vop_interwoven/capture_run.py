"""One capture run of one document, batched: the loop the Dynamo thinrunner
ran as top-level script code, moved here VERBATIM (2026-10-12) so the pyRevit
batch runner (vop_interwoven/batch_capture.py) runs the same loop on each
document it opens. Behaviour is the thinrunner's: one run id minted once and
handed to every batch, the run directory refused before any view is
captured if it holds another run, each batch into its own _batch_tmp_<n>/
and merged into the run directory, the root run_meta.json written BEFORE a
batch's captures are moved under it.
"""
import os
import shutil


def chunk_list(items, chunk_size):
    if not chunk_size or chunk_size <= 0:
        return [items]
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def append_csv(source_path, target_path):
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


def run_gc_between_chunks():
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


def relocate_batch_stage_a_outputs(batch_output_dir, output_dir, batch_view_summaries):
    """Move one batch's Stage A TIFF/sidecar files out of its temp dir and
    into the final output tree, then rewrite the tiff_path/sidecar_path
    already recorded in batch_view_summaries so they point at the new
    location instead of the now-empty batch temp dir.

    batch_view_summaries entries are mutated in place (and are the same
    dict objects the caller already extended merged["view_summaries"]
    with), so this must run before anything downstream reads those paths.
    No-op if this batch produced no capture folder (Stage A disabled, or
    nothing exported). The folder keeps its name (output_layout: color_id
    for a v2 run, color_id_buffer for v1).
    """
    from vop_interwoven.output_layout import CAPTURE_DIR
    found = [name for name in (CAPTURE_DIR["v2"], CAPTURE_DIR["v1"])
             if os.path.isdir(os.path.join(batch_output_dir, name))]
    if not found:
        return
    if len(found) > 1:
        raise RuntimeError("batch folder {0} holds both capture folders {1}; a run "
                           "writes one layout".format(batch_output_dir, found))
    batch_stage_a_dir = os.path.join(batch_output_dir, found[0])
    final_stage_a_dir = os.path.join(output_dir, found[0])
    os.makedirs(final_stage_a_dir, exist_ok=True)
    for fname in os.listdir(batch_stage_a_dir):
        shutil.move(
            os.path.join(batch_stage_a_dir, fname),
            os.path.join(final_stage_a_dir, fname),
        )

    for view_data in batch_view_summaries:
        if view_data.get("stage") != "color_id_buffer_stage_a":
            continue
        # Stage A step 3: the annotation pass writes its own two files into
        # the same batch directory, so they move with everything else and
        # their recorded paths must be rewritten with everything else.
        # Rewriting only the model pair left the annotation paths pointing
        # into an emptied batch directory -- present in view_summaries, and
        # resolving to nothing. (PR #211 review.)
        for path_key in ("tiff_path", "sidecar_path",
                         "annotation_tiff_path", "annotation_sidecar_path"):
            old_path = view_data.get(path_key)
            if old_path:
                view_data[path_key] = os.path.join(final_stage_a_dir, os.path.basename(old_path))


def run_capture(doc, view_ids, cfg, output_dir, batch_size=None, date_override=None,
                export_view_raster=True, record_fingerprints=False):
    """_run_capture inside a provenance session: what executed is read once,
    here, unless an enclosing batch already pinned it (run_meta.provenance_fields).

    ``record_fingerprints``: hash-and-skip phase 1 (shadow,
    docs/DESIGN_CHANGE_SKIP.md). The views' change fingerprints are read from
    the document BEFORE any view is captured and written to the run's
    ``view_fingerprints_<d>.json`` once it is finalized; run_meta gains
    ``view_fingerprints``. Nothing captured changes. A failure here is
    recorded under ``view_fingerprints.error`` and never fails the capture."""
    from vop_interwoven.run_meta import provenance_session
    fingerprints = None
    if record_fingerprints:
        fingerprints = _fingerprint_before_capture(doc, view_ids)
    with provenance_session("run_capture"):
        out = _run_capture(doc, view_ids, cfg, output_dir, batch_size=batch_size,
                           date_override=date_override,
                           export_view_raster=export_view_raster)
    if record_fingerprints:
        _write_fingerprints_after_capture(out[1], fingerprints)
    return out


def _fingerprint_before_capture(doc, view_ids):
    """``(record, None)`` or ``(None, error)``: telemetry, never raised."""
    try:
        from vop_interwoven.view_fingerprint import fingerprint_views
        return fingerprint_views(doc, view_ids, "capture"), None
    except Exception as ex:  # noqa: BLE001 -- recorded in run_meta by the writer
        return None, "{0}: {1}".format(type(ex).__name__, ex)


def _write_fingerprints_after_capture(run_dir, fingerprints):
    """Write the record, or stamp the reason there is none."""
    from vop_interwoven.batch_capture import stamp_run_meta
    record, error = fingerprints
    if record is not None:
        try:
            from vop_interwoven.view_fingerprint import write_fingerprints
            _path, stamp_err = write_fingerprints(run_dir, record)
            if stamp_err is None:
                return
            error = "run_meta not stamped: {0}".format(stamp_err)
        except Exception as ex:  # noqa: BLE001 -- recorded below
            error = "{0}: {1}".format(type(ex).__name__, ex)
    stamp_run_meta(run_dir, "view_fingerprints", {"error": error})


def _run_capture(doc, view_ids, cfg, output_dir, batch_size=None, date_override=None,
                 export_view_raster=True):
    """Capture ``view_ids`` of ``doc`` as ONE run under the root ``output_dir``.

    Returns ``(result, run_dir, run_id)``: the streaming result (merged over
    the batches when ``batch_size`` splits the views), the run directory and
    the run id. ``date_override`` is the as-of date or tag
    (run_meta.run_identity). Raises RunDirectoryConflict before any view is
    captured when the run directory already holds another run.
    """
    from vop_interwoven.streaming import run_vop_pipeline_streaming
    # One run, one directory: output_dir is the ROOT, and the run writes
    # into <output_dir>/<as_of_date>__<run_id>. The identity is minted ONCE
    # here and handed to every batch, and the directory is refused BEFORE
    # any view is captured if it holds another run.
    from vop_interwoven.run_meta import (
        run_identity, run_directory, check_run_directory)
    run_identity_ = run_identity(date_override)
    shared_run_id = run_identity_["run_id"]
    run_dir = run_directory(output_dir, run_identity_)
    check_run_directory(run_dir, shared_run_id)
    if getattr(cfg, "enable_color_id_buffer_stage_a", False):
        # Before any view: a run too deep for its files is refused whole.
        from vop_interwoven.output_layout import check_run_budget
        check_run_budget(run_dir)
    print("[VOP] Run {} -> {}".format(shared_run_id, run_dir))
    batches = chunk_list(view_ids, batch_size)
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
            export_png=True,
            export_csv=True,  # Always export CSV (tag override just affects Date/RunId columns)
            export_json=False,
            export_view_raster=export_view_raster,
            pixels_per_cell=10,
            date_override=date_override,
            run_id=shared_run_id,
        )
    else:
        print("[VOP] Streaming exporter rewrites CSVs per run; enabling batch CSV append merge.")
        merged = {
            "views_processed": 0,
            "views_failed": 0,
            "annotation_passes_failed": 0,
            "png_files": [],
            "view_raster_files": [],
            "csv_rows_written": 0,
            "view_summaries": [],
            "core_csv_path": None,
            "vop_csv_path": None,
            "occlusion_csv_path": None,
            "perf_csv_path": None,
        }

        # C9: one run, one RunId, one run_meta.json -- the run's id is
        # handed to every batch, each batch writes into its own
        # _batch_tmp_<n>/ inside the run directory, and their run_meta
        # files are merged into the run directory.
        batch_meta_paths = []
        for batch_index, batch_view_ids in enumerate(batches):
            start_idx = batch_index * batch_size + 1
            end_idx = start_idx + len(batch_view_ids) - 1
            print("[VOP] Batch {}/{}: views {}-{} ({} ids)".format(
                batch_index + 1, len(batches), start_idx, end_idx, len(batch_view_ids)
            ))

            batch_result = run_vop_pipeline_streaming(
                doc=doc,
                view_ids=batch_view_ids,
                cfg=cfg,
                output_dir=output_dir,
                export_png=True,
                export_csv=True,
                export_json=False,
                export_view_raster=export_view_raster,
                pixels_per_cell=10,
                date_override=date_override,
                run_id=shared_run_id,
                run_subdir="_batch_tmp_{}".format(batch_index + 1),
            )
            if batch_result.get("run_dir") != run_dir:
                raise RuntimeError(
                    "batch {0} resolved run directory {1!r}, not the run's "
                    "{2!r}".format(batch_index + 1, batch_result.get("run_dir"), run_dir))
            batch_output_dir = batch_result["output_dir"]
            if batch_result.get("run_meta_path"):
                batch_meta_paths.append(batch_result["run_meta_path"])

            merged["views_processed"] += batch_result.get("views_processed", 0)
            merged["views_failed"] += batch_result.get("views_failed", 0)
            merged["annotation_passes_failed"] += batch_result.get(
                "annotation_passes_failed", 0)
            merged["csv_rows_written"] += batch_result.get("csv_rows_written", 0)
            merged["view_summaries"].extend(batch_result.get("view_summaries", []))
            merged["png_files"].extend(batch_result.get("png_files", []))
            merged["view_raster_files"].extend(batch_result.get("view_raster_files", []))

            for key in ["core_csv_path", "vop_csv_path", "occlusion_csv_path", "perf_csv_path"]:
                chunk_csv = batch_result.get(key)
                if not chunk_csv:
                    continue
                target_csv = os.path.join(run_dir, os.path.basename(chunk_csv))
                append_csv(chunk_csv, target_csv)
                merged[key] = target_csv

            # Stage A writes TIFF/sidecar files directly under
            # batch_output_dir/color_id_buffer/ (there is no CSV row to merge them
            # through); move them into the requested output tree so batched runs
            # don't strand the only copies under a temp batch folder, and keep
            # this batch's view_summaries entries pointed at the moved files.
            if getattr(cfg, "enable_color_id_buffer_stage_a", False):
                # The root record FIRST, so a capture moved below always
                # has a run_meta.json above it, even if a later batch
                # raises (finalized stays false until the loop ends).
                from vop_interwoven.run_meta import write_merged_run_meta
                write_merged_run_meta(batch_meta_paths, run_dir, run_complete=False)
                relocate_batch_stage_a_outputs(
                    batch_output_dir, run_dir, batch_result.get("view_summaries", [])
                )

            run_gc_between_chunks()

        if batch_meta_paths:
            from vop_interwoven.run_meta import write_merged_run_meta
            merged["run_meta_path"] = write_merged_run_meta(
                batch_meta_paths, run_dir, run_complete=True)
        merged["run_id"] = shared_run_id
        merged["run_dir"] = run_dir

        result = merged
    return result, run_dir, shared_run_id
