"""
View raster export: renders Revit views as-is to PNG for side-by-side comparison
with VOP output.

Unlike vop_raster PNGs (which visualize grid occupancy), these images capture
what Revit would render at the same pixel dimensions — the raw view bitmap with
no occupancy processing applied.

Output folder: view_raster/
Counterpart:   vop_raster/

Both folders use identical filenames ({safe_view_name}_{view_id}.png) and
identical pixel dimensions (grid_W * pixels_per_cell  x  grid_H * pixels_per_cell)
so images can be diffed or compared directly.
"""

import os
import time


def export_view_image(doc, view_id, output_path, width_px, height_px, diag=None):
    """Export a single Revit view to PNG at the specified pixel dimensions.

    Uses Document.ExportImage() with pixel-accurate sizing so the output matches
    the corresponding vop_raster PNG dimensions exactly.

    Revit's ExportImage appends view metadata to the filename it creates, so this
    function searches the output directory for the generated file and renames it to
    ``output_path``.

    Args:
        doc:         Revit Document (clr-accessible Autodesk.Revit.DB.Document).
        view_id:     Autodesk.Revit.DB.ElementId for the view.
        output_path: Desired output path including ".png" extension.
        width_px:    Image width in pixels  (= grid_W * pixels_per_cell).
        height_px:   Image height in pixels (= grid_H * pixels_per_cell).
        diag:        Optional Diagnostics instance for structured error recording.

    Returns:
        Absolute path of the saved PNG, or None on failure.
    """
    try:
        try:
            from Autodesk.Revit.DB import (
                ImageExportOptions,
                ExportRange,
                ZoomFitType,
                FitDirectionType,
                ImageFileType,
            )
        except ImportError:
            if diag is not None:
                diag.error(
                    phase="export",
                    callsite="export_view_image",
                    message="Revit API not available — view_raster export requires Revit/Dynamo",
                )
            else:
                print("[view_raster] Revit API not available; skipping view image export")
            return None

        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)

        # Revit appends view name and/or index to the base filename it creates,
        # so we use a stable temp prefix and locate the result afterward.
        base_name = os.path.splitext(os.path.basename(output_path))[0]
        out_base = os.path.join(out_dir, base_name)

        opts = ImageExportOptions()
        opts.ExportRange = ExportRange.SetOfViews
        opts.SetViewsAndSheets([view_id])
        opts.ZoomType = ZoomFitType.FitPage
        # Fit to width; height follows the view's natural aspect ratio.
        opts.FitDirection = FitDirectionType.Horizontal
        opts.PixelSize = width_px
        opts.FilePath = out_base
        opts.HLRandWFViewsFileType = ImageFileType.PNG

        doc.ExportImage(opts)

        # Locate the file Revit created (name includes view metadata).
        created = _find_exported_file(out_dir, base_name, ".png")
        if created is None:
            if diag is not None:
                diag.error(
                    phase="export",
                    callsite="export_view_image",
                    message="ExportImage completed but no PNG found with prefix '{}'".format(base_name),
                    extra={"out_dir": out_dir},
                )
            return None

        if created != output_path:
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(created, output_path)

        return output_path if os.path.exists(output_path) else None

    except Exception as e:
        if diag is not None:
            diag.error(
                phase="export",
                callsite="export_view_image",
                message="View image export failed: {}".format(e),
                exc=e,
                extra={"output_path": output_path},
            )
        else:
            print("[view_raster] Export failed for {}: {}: {}".format(
                output_path, type(e).__name__, e))
        return None


def _find_exported_file(out_dir, base_name, ext):
    """Return the first file in out_dir whose name starts with base_name and ends with ext."""
    try:
        ext_lower = ext.lower()
        for fname in os.listdir(out_dir):
            if fname.startswith(base_name) and fname.lower().endswith(ext_lower):
                return os.path.join(out_dir, fname)
    except Exception:
        pass
    return None


def export_pipeline_views_to_pngs(doc, pipeline_result, output_dir, pixels_per_cell=4, diag=None):
    """Export all views from a pipeline result as raw Revit view images.

    Produces one PNG per view in ``output_dir/`` using the same filename convention
    and pixel dimensions as the corresponding vop_raster PNGs, enabling direct
    side-by-side or diff comparison.

    Views without valid grid dimensions are skipped with a diagnostic warning.

    Args:
        doc:            Revit Document.
        pipeline_result: Full pipeline result dict from run_vop_pipeline() or
                         process_document_views().
        output_dir:     Directory to write PNGs into (typically ``<base>/view_raster``).
        pixels_per_cell: Pixels per grid cell — must match the vop_raster export value.
        diag:           Optional Diagnostics instance.

    Returns:
        List of saved PNG file paths, or None if the batch itself failed.
    """
    saved = []
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Resolve ElementId type once so we can coerce int view_ids.
        ElementId = None
        try:
            from Autodesk.Revit.DB import ElementId
        except ImportError:
            pass

        for view_data in pipeline_result.get("views", []):
            if not isinstance(view_data, dict):
                continue

            view_id_raw = view_data.get("view_id")
            view_name = view_data.get("view_name", "unknown")
            width = int(view_data.get("width", 0) or 0)
            height = int(view_data.get("height", 0) or 0)

            if not view_id_raw or width <= 0 or height <= 0:
                if diag is not None:
                    diag.warn(
                        phase="export",
                        callsite="export_pipeline_views_to_pngs",
                        message="Skipping view_raster export: missing id or zero dimensions",
                        extra={
                            "view_id": view_id_raw,
                            "view_name": view_name,
                            "width": width,
                            "height": height,
                        },
                    )
                continue

            width_px = width * pixels_per_cell
            height_px = height * pixels_per_cell

            safe_name = "".join(
                c if c.isalnum() or c in (' ', '-', '_') else '_'
                for c in view_name
            )
            filename = "{0}_{1}.png".format(safe_name, view_id_raw)
            output_path = os.path.join(output_dir, filename)

            # Coerce plain int to ElementId when Revit API is available.
            eid = view_id_raw
            if ElementId is not None and not hasattr(view_id_raw, 'IntegerValue'):
                try:
                    eid = ElementId(int(view_id_raw))
                except Exception:
                    eid = view_id_raw

            t0 = time.perf_counter()
            png_path = export_view_image(doc, eid, output_path, width_px, height_px, diag=diag)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if png_path:
                saved.append(png_path)
                try:
                    view_data.setdefault("timings", {})["view_raster_ms"] = elapsed_ms
                except Exception:
                    pass
                print("[view_raster] Saved: {}".format(os.path.basename(png_path)))

    except Exception as e:
        if diag is not None:
            diag.error(
                phase="export",
                callsite="export_pipeline_views_to_pngs",
                message="View raster batch export failed: {}".format(e),
                exc=e,
                extra={"output_dir": output_dir},
            )
        else:
            print("[view_raster] Batch export error: {}: {}".format(type(e).__name__, e))
        return None

    return saved
