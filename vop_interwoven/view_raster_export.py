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
so files can be matched, diffed, or compared directly by name.
"""

import os
import time


def _snapshot(directory):
    """Return the set of filenames currently in directory, or empty set on error."""
    try:
        return set(os.listdir(directory))
    except Exception:
        return set()


def _canonical_name(view_name, view_id):
    """Return the shared filename used in both vop_raster/ and view_raster/."""
    safe = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in view_name)
    return "{0}_{1}.png".format(safe, view_id)


def export_view_image(doc, view_id, output_path, width_px, height_px, diag=None):
    """Export a single Revit view to PNG at the specified pixel dimensions.

    Uses Document.ExportImage() and a before/after directory snapshot to locate
    whatever file Revit created, then renames it to ``output_path`` so both
    vop_raster/ and view_raster/ always share the same canonical filename.

    Args:
        doc:         Revit Document.
        view_id:     Autodesk.Revit.DB.ElementId for the view.
        output_path: Desired output path — the canonical filename is derived
                     by the caller via _canonical_name().
        width_px:    Target width in pixels  (= grid_W * pixels_per_cell).
        height_px:   Target height in pixels (= grid_H * pixels_per_cell).
        diag:        Optional Diagnostics instance.

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

        # Snapshot directory contents before export so we can find exactly
        # what Revit creates regardless of its internal naming convention.
        before = _snapshot(out_dir)

        opts = ImageExportOptions()
        opts.ExportRange = ExportRange.SetOfViews
        opts.SetViewsAndSheets([view_id])
        opts.ZoomType = ZoomFitType.FitToPage
        opts.FitDirection = FitDirectionType.Horizontal
        opts.PixelSize = width_px
        # Use a temp base path so Revit's appended name doesn't collide with
        # the canonical target filename.
        opts.FilePath = os.path.join(out_dir, "_vr_tmp")
        opts.HLRandWFViewsFileType = ImageFileType.PNG
        opts.ShadowViewsFileType = ImageFileType.PNG

        doc.ExportImage(opts)

        # Find new PNG(s) created since the snapshot.
        after = _snapshot(out_dir)
        new_pngs = [f for f in (after - before) if f.lower().endswith(".png")]

        if not new_pngs:
            msg = "ExportImage produced no new PNG in '{}'; dir had {} files after call".format(
                out_dir, len(after))
            if diag is not None:
                diag.error(phase="export", callsite="export_view_image", message=msg,
                           extra={"output_path": output_path})
            else:
                print("[view_raster] " + msg)
            return None

        created = os.path.join(out_dir, new_pngs[0])

        # Resize to exact target dimensions before final rename.
        _resize_to_exact(created, width_px, height_px, diag=diag)

        # Rename to canonical path (overwrites any stale copy).
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


def _resize_to_exact(path, width_px, height_px, diag=None):
    """Resize the image at ``path`` in-place to exactly ``width_px`` x ``height_px``.

    Tries Pillow first (CPython), then System.Drawing (IronPython/Revit).
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
        from System.Drawing import Bitmap, Graphics
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

    msg = "No image library available to enforce exact pixel dimensions for {}".format(
        os.path.basename(path))
    if diag is not None:
        diag.warn(phase="export", callsite="_resize_to_exact", message=msg,
                  extra={"width_px": width_px, "height_px": height_px})
    else:
        print("[view_raster] WARNING: " + msg)


def export_pipeline_views_to_pngs(doc, pipeline_result, output_dir, pixels_per_cell=4, diag=None):
    """Export all views from a pipeline result as raw Revit view images.

    Produces one PNG per view using the same canonical filename as vop_raster/:
        {safe_view_name}_{view_id}.png

    Each view is exported individually so its PixelSize can be set to the correct
    width for that view. The before/after snapshot in export_view_image() handles
    Revit's internal filename mangling regardless of Revit version.

    Args:
        doc:             Revit Document.
        pipeline_result: List from process_document_views() or dict from
                         run_vop_pipeline() — both forms accepted.
        output_dir:      Directory to write PNGs into (typically ``<base>/view_raster``).
        pixels_per_cell: Must match the vop_raster export value.
        diag:            Optional Diagnostics instance.

    Returns:
        List of saved PNG file paths, or None if the batch itself failed.
    """
    saved = []
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        ElementId = None
        try:
            from Autodesk.Revit.DB import ElementId
        except ImportError:
            pass

        # Accept both process_document_views() (list) and run_vop_pipeline() (dict).
        views = pipeline_result if isinstance(pipeline_result, list) \
            else pipeline_result.get("views", [])

        for view_data in views:
            if not isinstance(view_data, dict):
                continue

            view_id_raw = view_data.get("view_id")
            view_name = view_data.get("view_name", "unknown")
            width = int(view_data.get("width", 0) or 0)
            height = int(view_data.get("height", 0) or 0)

            if not view_id_raw or width <= 0 or height <= 0:
                print("[view_raster] Skipping view '{}' (id={}, w={}, h={})".format(
                    view_name, view_id_raw, width, height))
                continue

            width_px = width * pixels_per_cell
            height_px = height * pixels_per_cell

            # Canonical filename — identical to vop_raster counterpart.
            filename = _canonical_name(view_name, view_id_raw)
            output_path = os.path.join(output_dir, filename)

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
                print("[view_raster] Saved: {}".format(filename))
            else:
                print("[view_raster] FAILED: {}".format(filename))

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
