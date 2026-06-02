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
        opts.ZoomType = ZoomFitType.FitToPage
        # Fit to width first; we enforce exact height afterward via resize.
        opts.FitDirection = FitDirectionType.Horizontal
        opts.PixelSize = width_px
        opts.FilePath = out_base
        # Cover both wireframe/hidden-line views and shaded/realistic views.
        opts.HLRandWFViewsFileType = ImageFileType.PNG
        opts.ShadowViewsFileType = ImageFileType.PNG

        doc.ExportImage(opts)

        # Locate the file Revit created (name includes view metadata).
        # Exclude the canonical target path so a stale pre-existing PNG is
        # never mistaken for the freshly-exported file.
        created = _find_exported_file(out_dir, base_name, ".png", exclude=output_path)
        if created is None:
            if diag is not None:
                diag.error(
                    phase="export",
                    callsite="export_view_image",
                    message="ExportImage completed but no PNG found with prefix '{}'".format(base_name),
                    extra={"out_dir": out_dir},
                )
            return None

        # Resize to exact target dimensions so vop_raster and view_raster PNGs
        # are always pixel-identical in size regardless of Revit's aspect ratio.
        _resize_to_exact(created, width_px, height_px, diag=diag)

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


def _find_exported_file(out_dir, base_name, ext, exclude=None):
    """Return the first file in out_dir whose name starts with base_name and ends with ext.

    Files whose full path equals ``exclude`` are skipped so a pre-existing canonical
    target is never confused with the freshly-exported Revit-generated file.
    """
    try:
        ext_lower = ext.lower()
        exclude_name = os.path.basename(exclude) if exclude else None
        for fname in os.listdir(out_dir):
            if exclude_name and fname == exclude_name:
                continue
            if fname.startswith(base_name) and fname.lower().endswith(ext_lower):
                return os.path.join(out_dir, fname)
    except Exception:
        pass
    return None


def _resize_to_exact(path, width_px, height_px, diag=None):
    """Resize the image at ``path`` in-place to exactly ``width_px`` × ``height_px``.

    Tries Pillow first (CPython), then System.Drawing (IronPython/Revit).
    If neither is available the file is left unchanged and a warning is recorded.
    """
    try:
        from vop_interwoven.np_backend import PILLOW_AVAILABLE
        if PILLOW_AVAILABLE:
            from vop_interwoven.np_backend import Image
            img = Image.open(path)
            if img.size != (width_px, height_px):
                img = img.resize((width_px, height_px), Image.LANCZOS)
                img.save(path)
            return
    except Exception:
        pass

    try:
        import clr
        clr.AddReference('System.Drawing')
        from System.Drawing import Bitmap, Graphics, Size
        from System.Drawing.Imaging import ImageFormat
        bmp_src = Bitmap(path)
        if bmp_src.Width != width_px or bmp_src.Height != height_px:
            bmp_dst = Bitmap(width_px, height_px)
            g = Graphics.FromImage(bmp_dst)
            g.DrawImage(bmp_src, 0, 0, width_px, height_px)
            g.Dispose()
            bmp_src.Dispose()
            bmp_dst.Save(path, ImageFormat.Png)
            bmp_dst.Dispose()
        else:
            bmp_src.Dispose()
        return
    except Exception:
        pass

    if diag is not None:
        diag.warn(
            phase="export",
            callsite="_resize_to_exact",
            message="No image library available to enforce exact pixel dimensions; "
                    "view_raster PNG may not match vop_raster dimensions",
            extra={"path": path, "width_px": width_px, "height_px": height_px},
        )
    else:
        print("[view_raster] WARNING: cannot resize {}; install Pillow for exact dimensions".format(
            os.path.basename(path)))


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

        # Accept both process_document_views() (returns a list) and
        # run_vop_pipeline() (returns {"views": [...]}).
        if isinstance(pipeline_result, list):
            views = pipeline_result
        else:
            views = pipeline_result.get("views", [])

        for view_data in views:
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
