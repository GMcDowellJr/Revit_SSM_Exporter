"""
View raster export: renders Revit views as-is to PNG for side-by-side comparison
with VOP output.

Unlike vop_raster PNGs (which visualize grid occupancy), these images capture
what Revit would render at the same pixel dimensions — the raw view bitmap with
no occupancy processing applied.

Before exporting, two temporary view mutations are applied inside a single
rolled-back Transaction so the document is never permanently modified:

  1. VOP-excluded categories (grids, levels, section marks, rooms, etc.) are
     hidden so the rendered content matches what VOP analyzes.

  2. The view's crop box is expanded to cover exactly W * cell_size_ft ×
     H * cell_size_ft — the same spatial extent as the VOP grid — eliminating
     the sub-cell misalignment that arises from VOP's ceil() rounding.  Both
     PNGs then cover identical ground, so elements align pixel-for-pixel.

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


def _dot(a, b):
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def _prepare_view_for_export(doc, view, W, H, cell_size_ft):
    """Start a Transaction that:
      - Hides VOP-excluded categories on the view
      - Expands the crop box to cover exactly W*cell_size_ft × H*cell_size_ft

    The caller MUST call RollBack() on the returned transaction after
    ExportImage so the view is fully restored.  Returns None if the Revit
    API is unavailable or the transaction cannot be started.
    """
    try:
        from Autodesk.Revit.DB import Transaction, BoundingBoxXYZ, XYZ
        from vop_interwoven.revit.collection_policy import (
            excluded_bic_names_global,
            annotation_included_bic_names,
            _try_import_bic,
        )

        t = Transaction(doc, "vop_view_raster_tmp_state")
        t.Start()

        # ── 1. Hide VOP-excluded categories (minus annotation includes) ───────
        # Start with the model-layer exclude list then add back categories that
        # the VOP annotation pass collects, so view_raster shows the same
        # content as vop_raster (model + annotation, minus nav/analysis junk).
        anno_includes = set(annotation_included_bic_names())
        hide_bic_names = [n for n in excluded_bic_names_global() if n not in anno_includes]

        hidden_count = 0
        try:
            BuiltInCategory = _try_import_bic()
            for bic_name in hide_bic_names:
                bic = getattr(BuiltInCategory, bic_name, None)
                if bic is None:
                    continue
                try:
                    cat = doc.Settings.Categories.get_Item(bic)
                    if cat is not None and view.CanCategoryBeHidden(cat.Id):
                        view.SetCategoryHidden(cat.Id, True)
                        hidden_count += 1
                except Exception:
                    pass
        except Exception as e:
            print("[view_raster] WARNING: category hide failed: {}".format(e))

        print("[view_raster] Hid {} VOP-excluded categories".format(hidden_count))

        # ── 2. Expand crop box to exact VOP grid extent ───────────────────────
        # VOP computes W = ceil(crop_w_uv / cell_size_ft), so
        # W * cell_size_ft may exceed crop_w_uv by up to one cell.
        # We expand the crop box max corner by that delta so both images cover
        # the same spatial region.
        try:
            cb = view.CropBox
            T = getattr(cb, "Transform", None)
            U_vec = view.RightDirection   # world-space unit vector (U)
            V_vec = view.UpDirection      # world-space unit vector (V)

            # Project the 4 XY-plane corners of the crop box to view UV.
            # We use differences so the origin cancels out.
            u_vals, v_vals = [], []
            for lx in [cb.Min.X, cb.Max.X]:
                for ly in [cb.Min.Y, cb.Max.Y]:
                    local_pt = XYZ(lx, ly, cb.Min.Z)
                    world_pt = T.OfPoint(local_pt) if T is not None else local_pt
                    u_vals.append(_dot(world_pt, U_vec))
                    v_vals.append(_dot(world_pt, V_vec))

            crop_w_uv = max(u_vals) - min(u_vals)
            crop_h_uv = max(v_vals) - min(v_vals)

            delta_u = W * cell_size_ft - crop_w_uv  # ≥ 0, ≤ cell_size_ft
            delta_v = H * cell_size_ft - crop_h_uv

            if abs(delta_u) > 1e-6 or abs(delta_v) > 1e-6:
                # Move the max corner outward by (delta_u * U_vec + delta_v * V_vec)
                max_world = T.OfPoint(cb.Max) if T is not None else cb.Max
                new_max_world = XYZ(
                    max_world.X + delta_u * U_vec.X + delta_v * V_vec.X,
                    max_world.Y + delta_u * U_vec.Y + delta_v * V_vec.Y,
                    max_world.Z + delta_u * U_vec.Z + delta_v * V_vec.Z,
                )
                T_inv = T.Inverse if T is not None else None
                new_max_local = T_inv.OfPoint(new_max_world) if T_inv is not None else new_max_world

                new_cb = BoundingBoxXYZ()
                if T is not None:
                    new_cb.Transform = T
                new_cb.Min = cb.Min
                new_cb.Max = XYZ(new_max_local.X, new_max_local.Y, cb.Max.Z)
                view.CropBox = new_cb
                view.CropBoxActive = True
                print("[view_raster] Expanded crop box by ({:.4f}, {:.4f}) ft to match VOP grid".format(
                    delta_u, delta_v))

        except Exception as e:
            print("[view_raster] WARNING: crop box adjustment failed: {}".format(e))

        return t

    except Exception as e:
        print("[view_raster] WARNING: could not prepare view for export: {}".format(e))
        return None


def export_view_image(doc, view_id, output_path, width_px, height_px,
                      vop_grid=None, diag=None):
    """Export a single Revit view to PNG at the specified pixel dimensions.

    Temporarily hides VOP-excluded categories and adjusts the crop box to cover
    exactly the VOP grid extent (W*cell_size_ft x H*cell_size_ft) via a
    rolled-back Transaction so both vop_raster and view_raster cover the same
    spatial region and elements align pixel-for-pixel.

    Args:
        doc:         Revit Document.
        view_id:     Autodesk.Revit.DB.ElementId for the view.
        output_path: Desired output path — canonical filename set by caller.
        width_px:    Target width in pixels  (= grid_W * pixels_per_cell).
        height_px:   Target height in pixels (= grid_H * pixels_per_cell).
        vop_grid:    Optional dict with keys ``W``, ``H``, ``cell_size_ft``
                     used to align the crop box to the VOP grid extent.
                     If None, crop box is exported as-is (may have sub-cell
                     misalignment at edges).
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
                ElementId as _EId,
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

        view = doc.GetElement(view_id)

        # Extract VOP grid parameters for crop box alignment.
        W = H = cell_size_ft = 0
        if vop_grid is not None:
            W = int(vop_grid.get("W", 0) or 0)
            H = int(vop_grid.get("H", 0) or 0)
            cell_size_ft = float(vop_grid.get("cell_size_ft", 0) or 0)

        t = None
        if view is not None:
            t = _prepare_view_for_export(doc, view, W, H, cell_size_ft)

        try:
            before = _snapshot(out_dir)

            eid_list = NetList[_EId]()
            eid_list.Add(view_id)

            opts = ImageExportOptions()
            opts.ExportRange = ExportRange.SetOfViews
            opts.SetViewsAndSheets(eid_list)
            opts.ZoomType = ZoomFitType.FitToPage
            opts.FitDirection = FitDirectionType.Horizontal
            opts.PixelSize = width_px
            opts.FilePath = os.path.join(out_dir, "_vr_tmp")
            opts.HLRandWFViewsFileType = ImageFileType.PNG
            opts.ShadowViewsFileType = ImageFileType.PNG

            doc.ExportImage(opts)

        finally:
            if t is not None:
                try:
                    t.RollBack()
                except Exception as rb_e:
                    print("[view_raster] WARNING: transaction rollback failed: {}".format(rb_e))

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


def _resize_to_exact(path, width_px, height_px, diag=None):
    """Resize the image at ``path`` in-place to exactly ``width_px`` x ``height_px``.

    After the crop box is aligned to the VOP grid, the Revit export should
    already have the correct aspect ratio and this call is a no-op.  It guards
    against any residual floating-point discrepancy.

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

    Per view, a rolled-back Transaction temporarily:
      - Hides VOP-excluded categories
      - Expands the crop box to the exact VOP grid extent

    so the rendered image is spatially aligned with the vop_raster counterpart.

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

        views = pipeline_result if isinstance(pipeline_result, list) \
            else pipeline_result.get("views", [])

        for view_data in views:
            if not isinstance(view_data, dict):
                continue

            view_id_raw = view_data.get("view_id")
            view_name = view_data.get("view_name", "unknown")
            W = int(view_data.get("grid_W", 0) or 0)
            H = int(view_data.get("grid_H", 0) or 0)
            cell_size_ft = float(
                view_data.get("cell_size_ft_effective")
                or view_data.get("cell_size_ft")
                or 0
            )

            if not view_id_raw or W <= 0 or H <= 0:
                print("[view_raster] Skipping view '{}' (id={}, W={}, H={})".format(
                    view_name, view_id_raw, W, H))
                continue

            width_px = W * pixels_per_cell
            height_px = H * pixels_per_cell

            filename = _canonical_name(view_name, view_id_raw)
            output_path = os.path.join(output_dir, filename)

            eid = view_id_raw
            if ElementId is not None and not hasattr(view_id_raw, 'IntegerValue'):
                try:
                    eid = ElementId(int(view_id_raw))
                except Exception:
                    eid = view_id_raw

            vop_grid = {"W": W, "H": H, "cell_size_ft": cell_size_ft} \
                if cell_size_ft > 0 else None

            t0 = time.perf_counter()
            png_path = export_view_image(
                doc, eid, output_path, width_px, height_px,
                vop_grid=vop_grid, diag=diag,
            )
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
