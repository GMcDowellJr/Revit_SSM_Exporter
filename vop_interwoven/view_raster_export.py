"""
View raster export: renders Revit views as-is to PNG for side-by-side comparison
with VOP output.

Unlike vop_raster PNGs (which visualize grid occupancy), these images capture
what Revit would render at the same pixel dimensions — the raw view bitmap with
no occupancy processing applied.

Before exporting, the same categories VOP excludes (grids, levels, section marks,
rooms, etc.) are temporarily hidden on the view via a rolled-back Transaction so
the rendered content aligns with what VOP analyzes, not just pixel dimensions.

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


def _hide_vop_excluded_categories(doc, view):
    """Start a Transaction that hides VOP-excluded categories on ``view``.

    The caller must call RollBack() on the returned transaction after export
    to restore the view to its original visibility state. Returns None if the
    Revit API is unavailable or the transaction cannot be started.

    Categories hidden are exactly ``collection_policy.excluded_bic_names_global()``:
    grids, levels, section marks, rooms, etc. — the same set VOP skips during
    element collection so the rendered image matches VOP's analysis scope.
    """
    try:
        from Autodesk.Revit.DB import Transaction
        from vop_interwoven.revit.collection_policy import (
            excluded_bic_names_global,
            _try_import_bic,
        )

        BuiltInCategory = _try_import_bic()

        t = Transaction(doc, "vop_view_raster_tmp_visibility")
        t.Start()

        hidden = 0
        for bic_name in excluded_bic_names_global():
            bic = getattr(BuiltInCategory, bic_name, None)
            if bic is None:
                continue
            try:
                cat = doc.Settings.Categories.get_Item(bic)
                if cat is not None and view.CanCategoryBeHidden(cat.Id):
                    view.SetCategoryHidden(cat.Id, True)
                    hidden += 1
            except Exception:
                pass

        print("[view_raster] Hid {} VOP-excluded categories for export".format(hidden))
        return t

    except Exception as e:
        print("[view_raster] WARNING: could not hide excluded categories: {}".format(e))
        return None


def export_view_image(doc, view_id, output_path, width_px, height_px, diag=None):
    """Export a single Revit view to PNG at the specified pixel dimensions.

    Before exporting, temporarily hides VOP-excluded categories (grids, levels,
    section marks, rooms, etc.) via a rolled-back Transaction so the rendered
    content matches what VOP analyzes.

    Uses a before/after directory snapshot to locate whatever file Revit creates,
    then renames it to ``output_path`` so both vop_raster/ and view_raster/ share
    the same canonical filename.

    Args:
        doc:         Revit Document.
        view_id:     Autodesk.Revit.DB.ElementId for the view.
        output_path: Desired output path — canonical filename set by caller.
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
            import System.Collections.Generic as SCG
            NetList = SCG.List
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

        # Resolve view object for category visibility manipulation.
        view = doc.GetElement(view_id)

        # Temporarily hide VOP-excluded categories so the render matches VOP scope.
        # Transaction is rolled back after export — view state is fully restored.
        t = _hide_vop_excluded_categories(doc, view) if view is not None else None

        try:
            # Snapshot before export to locate whatever file Revit creates.
            before = _snapshot(out_dir)

            # SetViewsAndSheets requires a .NET List[ElementId], not a Python list.
            from Autodesk.Revit.DB import ElementId as _EId
            eid_list = NetList[_EId]()
            eid_list.Add(view_id)

            opts = ImageExportOptions()
            opts.ExportRange = ExportRange.SetOfViews
            opts.SetViewsAndSheets(eid_list)
            opts.ZoomType = ZoomFitType.FitToPage
            opts.FitDirection = FitDirectionType.Horizontal
            opts.PixelSize = width_px
            # Temp base path — Revit appends view metadata to the name it creates.
            opts.FilePath = os.path.join(out_dir, "_vr_tmp")
            opts.HLRandWFViewsFileType = ImageFileType.PNG
            opts.ShadowViewsFileType = ImageFileType.PNG

            doc.ExportImage(opts)

        finally:
            # Always roll back — restores original category visibility.
            if t is not None:
                try:
                    t.RollBack()
                except Exception as rb_e:
                    print("[view_raster] WARNING: transaction rollback failed: {}".format(rb_e))

        # Find new PNG(s) created since the snapshot.
        after = _snapshot(out_dir)
        new_pngs = [f for f in (after - before) if f.lower().endswith(".png")]

        if not new_pngs:
            msg = "ExportImage produced no new PNG in '{}'; {} files present after call".format(
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

    VOP-excluded categories are hidden per-view via a rolled-back Transaction
    before each export so the rendered content matches VOP's analysis scope.

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
