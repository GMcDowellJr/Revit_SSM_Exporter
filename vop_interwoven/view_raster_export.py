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


def _is_annotation_only_view(view):
    """Return True for DraftingView and Legend view types (no model geometry)."""
    try:
        from Autodesk.Revit.DB import ViewType
        vt = getattr(view, "ViewType", None)
        if vt is None:
            return False
        if hasattr(ViewType, "DraftingView") and vt == ViewType.DraftingView:
            return True
        if hasattr(ViewType, "Legend") and vt == ViewType.Legend:
            return True
        # Numeric fallback — Dynamo sometimes stringifies ViewType as an int
        try:
            return int(str(vt)) in (9, 10)  # 9=DraftingView, 10=Legend
        except Exception:
            return False
    except Exception:
        return False


def _dot(a, b):
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def _grid_origin_uv(view_data):
    """(umin, vmin) of the VOP grid this view was rasterized on, or None.

    raster.bounds_xy is the grid's OWN rectangle, which is not the view's crop
    rectangle whenever a bounds buffer, an annotation expansion or the
    annotation-clip recentring moved it (view_basis.py:914, :1032-1036,
    :1056-1068). The model-view crop box has to be anchored to it, not to its
    own minimum -- see _prepare_view_for_export().

    Returns None rather than a guess when the result dict does not carry it: a
    fabricated origin would move every element in the exported image, silently
    and by a plausible-looking amount.
    """
    raster = view_data.get("raster")
    if raster is None:
        return None
    bounds = getattr(raster, "bounds_xy", None)
    if bounds is None and isinstance(raster, dict):
        bounds = raster.get("bounds_xy")
    if bounds is None:
        return None
    if isinstance(bounds, dict):
        xmin, ymin = bounds.get("xmin"), bounds.get("ymin")
    else:
        xmin, ymin = getattr(bounds, "xmin", None), getattr(bounds, "ymin", None)
    if xmin is None or ymin is None:
        return None
    return (float(xmin), float(ymin))


def _prepare_view_for_export(doc, view, W, H, cell_size_ft, grid_origin_uv=None):
    """Start a Transaction that:
      - Hides VOP-excluded categories on the view AND in visible linked models
      - Adjusts the crop box to cover exactly W*cell_size_ft × H*cell_size_ft
        ANCHORED AT THE VOP GRID'S OWN ORIGIN

    For annotation-only views (drafting/legend) the crop box is recomputed from
    annotation element extents, which is where that grid's origin comes from.

    For model views, ``grid_origin_uv`` is ``(umin, vmin)`` -- raster.bounds_xy's
    minimum corner, in the view's own U/V (RightDirection/UpDirection) space.
    The crop box is PLACED at it. Expanding the max corner alone, which is what
    this did before, silently assumes the grid's origin is the crop box's own
    minimum, and it is not:

      1. resolve_view_bounds() takes the crop box inflated by ``buffer_ft``
         (view_basis.py:914), so a non-zero buffer moves the minimum outward.
      2. Annotation expansion (view_basis.py:1032-1036) can move it again, on a
         model view as readily as on any other.
      3. The annotation-clip recentring (view_basis.py:1056-1068) recomputes
         ``new_xmin``/``new_ymin`` from a centre outright.

    In all three the grid's origin is not the crop's, and an expansion at the
    max corner leaves the two images translated by the difference while both
    measure the same WIDTH -- which is why no size check ever caught it.

    ``grid_origin_uv=None`` keeps the previous expand-only behaviour exactly,
    for a caller that cannot supply the origin. That case cannot be
    re-anchored: it is not that the origin is known to be the crop's, it is
    that it is unknown, and the function says so rather than assuming.

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
            print("[view_raster] WARNING: host category hide failed: {}".format(e))

        print("[view_raster] Hid {} VOP-excluded categories (host)".format(hidden_count))

        # ── 1b. Hide same categories in linked model instances ────────────────
        # view.SetCategoryHidden only applies to host model elements.
        # Linked instances require GetLinkOverrides/SetLinkOverrides.
        # This is best-effort: if the API is unavailable or a link has unsupported
        # settings, the link element stays visible but the crop box still matches
        # the VOP grid so coordinate space is correct.
        link_hidden_count = 0
        try:
            from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
            BuiltInCategory = _try_import_bic()
            link_insts = list(
                FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
            )
            for link_inst in link_insts:
                try:
                    link_settings = view.GetLinkOverrides(link_inst.Id)
                    if link_settings is None:
                        continue
                    changed = False
                    for bic_name in hide_bic_names:
                        bic = getattr(BuiltInCategory, bic_name, None)
                        if bic is None:
                            continue
                        try:
                            cat = doc.Settings.Categories.get_Item(bic)
                            if cat is None:
                                continue
                            # Try API variants across Revit versions
                            if hasattr(link_settings, 'SetCategoryHidden'):
                                link_settings.SetCategoryHidden(cat.Id, True)
                                changed = True
                            elif hasattr(link_settings, 'AddHiddenCategoryId'):
                                link_settings.AddHiddenCategoryId(cat.Id)
                                changed = True
                        except Exception:
                            pass
                    if changed:
                        view.SetLinkOverrides(link_inst.Id, link_settings)
                        link_hidden_count += 1
                except Exception:
                    pass
        except Exception as e:
            print("[view_raster] WARNING: linked category hide failed: {}".format(e))

        if link_hidden_count:
            print("[view_raster] Applied category hides to {} linked instance(s)".format(
                link_hidden_count))

        # ── 2. Align crop box to VOP grid extent ─────────────────────────────
        if W <= 0 or H <= 0 or cell_size_ft <= 0:
            return t  # No grid info; skip crop box adjustment

        try:
            if _is_annotation_only_view(view):
                # Annotation-only view (drafting/legend): VOP grid origin comes
                # from annotation element extents, not the existing crop box.
                # Recompute those extents here so the crop box starts at the
                # same origin VOP used (min_bbox - 1 cell pad on each side).
                from Autodesk.Revit.DB import FilteredElementCollector as _FEC
                min_x = min_y = float('inf')
                max_x = max_y = float('-inf')
                try:
                    for elem in _FEC(doc, view.Id).WhereElementIsNotElementType():
                        try:
                            bb = elem.get_BoundingBox(view)
                            if bb is None:
                                continue
                            min_x = min(min_x, bb.Min.X, bb.Max.X)
                            min_y = min(min_y, bb.Min.Y, bb.Max.Y)
                            max_x = max(max_x, bb.Min.X, bb.Max.X)
                            max_y = max(max_y, bb.Min.Y, bb.Max.Y)
                        except Exception:
                            continue
                except Exception:
                    pass

                if min_x == float('inf'):
                    return t  # No elements; leave crop box unchanged

                # Match VOP's 1-cell padding on each side
                pad = cell_size_ft
                origin_x = min_x - pad
                origin_y = min_y - pad

                cb = view.CropBox
                T = getattr(cb, "Transform", None)
                new_cb = BoundingBoxXYZ()
                if T is not None:
                    new_cb.Transform = T
                new_cb.Min = XYZ(origin_x, origin_y, cb.Min.Z)
                new_cb.Max = XYZ(origin_x + W * cell_size_ft,
                                 origin_y + H * cell_size_ft, cb.Max.Z)
                view.CropBox = new_cb
                view.CropBoxActive = True
                print("[view_raster] Annotation view crop set: origin=({:.3f},{:.3f}) "
                      "size={:.3f}×{:.3f} ft".format(
                          origin_x, origin_y, W * cell_size_ft, H * cell_size_ft))
            else:
                cb = view.CropBox
                T = getattr(cb, "Transform", None)
                U_vec = view.RightDirection
                V_vec = view.UpDirection

                u_vals, v_vals = [], []
                for lx in [cb.Min.X, cb.Max.X]:
                    for ly in [cb.Min.Y, cb.Max.Y]:
                        local_pt = XYZ(lx, ly, cb.Min.Z)
                        world_pt = T.OfPoint(local_pt) if T is not None else local_pt
                        u_vals.append(_dot(world_pt, U_vec))
                        v_vals.append(_dot(world_pt, V_vec))

                crop_umin, crop_vmin = min(u_vals), min(v_vals)
                crop_w_uv = max(u_vals) - crop_umin
                crop_h_uv = max(v_vals) - crop_vmin

                if grid_origin_uv is not None:
                    # Model view, origin known: PLACE the crop box on the VOP
                    # grid's own rectangle, both corners. Same thing the
                    # annotation branch above does, for the same reason -- the
                    # grid's origin is not derivable from the crop box.
                    grid_umin = float(grid_origin_uv[0])
                    grid_vmin = float(grid_origin_uv[1])
                    shift_u = grid_umin - crop_umin
                    shift_v = grid_vmin - crop_vmin
                    delta_u = W * cell_size_ft - crop_w_uv
                    delta_v = H * cell_size_ft - crop_h_uv

                    min_world = T.OfPoint(cb.Min) if T is not None else cb.Min
                    new_min_world = XYZ(
                        min_world.X + shift_u * U_vec.X + shift_v * V_vec.X,
                        min_world.Y + shift_u * U_vec.Y + shift_v * V_vec.Y,
                        min_world.Z + shift_u * U_vec.Z + shift_v * V_vec.Z,
                    )
                    # The max corner moves by the origin shift AND by the
                    # size delta, so the span lands on exactly W*cs x H*cs.
                    max_world = T.OfPoint(cb.Max) if T is not None else cb.Max
                    mu, mv = shift_u + delta_u, shift_v + delta_v
                    new_max_world = XYZ(
                        max_world.X + mu * U_vec.X + mv * V_vec.X,
                        max_world.Y + mu * U_vec.Y + mv * V_vec.Y,
                        max_world.Z + mu * U_vec.Z + mv * V_vec.Z,
                    )
                    T_inv = T.Inverse if T is not None else None
                    new_min_local = T_inv.OfPoint(new_min_world) if T_inv is not None else new_min_world
                    new_max_local = T_inv.OfPoint(new_max_world) if T_inv is not None else new_max_world

                    new_cb = BoundingBoxXYZ()
                    if T is not None:
                        new_cb.Transform = T
                    new_cb.Min = XYZ(new_min_local.X, new_min_local.Y, cb.Min.Z)
                    new_cb.Max = XYZ(new_max_local.X, new_max_local.Y, cb.Max.Z)
                    view.CropBox = new_cb
                    view.CropBoxActive = True
                    print("[view_raster] Model view crop anchored to VOP grid: "
                          "origin shift ({:.4f}, {:.4f}) ft, size delta "
                          "({:.4f}, {:.4f}) ft, size={:.3f}×{:.3f} ft".format(
                              shift_u, shift_v, delta_u, delta_v,
                              W * cell_size_ft, H * cell_size_ft))
                else:
                    # Origin NOT supplied, so it is unknown -- not known to be
                    # the crop's. Previous behaviour, unchanged: expand the max
                    # corner by the sub-cell delta and leave the origin alone.
                    # VOP uses ceil() so the grid may exceed the crop box by up
                    # to one cell; this path only ever expands, never shrinks,
                    # and cannot correct a translation.
                    delta_u = max(0.0, W * cell_size_ft - crop_w_uv)
                    delta_v = max(0.0, H * cell_size_ft - crop_h_uv)

                    if delta_u > 1e-6 or delta_v > 1e-6:
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
                        print("[view_raster] Expanded crop box by ({:.4f}, {:.4f}) ft "
                              "(no grid origin supplied; origin NOT re-anchored)".format(
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
                     used to align the crop box to the VOP grid extent, and
                     optionally ``origin_uv`` -- raster.bounds_xy's (umin,
                     vmin). Without ``origin_uv`` a model view's crop box can
                     only be resized, not re-anchored, so an image whose grid
                     origin differs from its crop origin stays translated
                     against its vop_raster counterpart.
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
        grid_origin_uv = None
        if vop_grid is not None:
            W = int(vop_grid.get("W", 0) or 0)
            H = int(vop_grid.get("H", 0) or 0)
            cell_size_ft = float(vop_grid.get("cell_size_ft", 0) or 0)
            origin = vop_grid.get("origin_uv")
            if origin is not None and len(origin) >= 2:
                grid_origin_uv = (float(origin[0]), float(origin[1]))

        t = None
        if view is not None:
            t = _prepare_view_for_export(
                doc, view, W, H, cell_size_ft, grid_origin_uv=grid_origin_uv)

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

    THAT PREMISE ONLY BECAME TRUE WITH A4.  Until the model-view branch of
    _prepare_view_for_export() was anchored to the VOP grid's own origin, the
    two images could be translated against each other by (crop_min -
    grid_min) -- and this function could not have detected it, because a
    translated image is exactly the right SIZE.  A size check is not an
    alignment check, and the no-op claim above is a claim about the crop box,
    not about this function.  It holds while ``vop_grid["origin_uv"]`` is
    supplied; on the fallback path that cannot re-anchor, it does not.

    NEVER CALL THIS ON A COLOR-ID RASTER.  Both backends RESAMPLE --
    Image.LANCZOS here, Graphics.DrawImage below -- and interpolation invents
    intermediate RGB values that map to no element ID, which silently
    destroys exact-match decoding.  On a view_raster comparison PNG that is
    cosmetic; on a Stage A capture it would be total.  Stage A does not reach
    this module (it goes through color_id_buffer.export_color_id_buffer_view),
    and nothing in this function's name or signature says so, which is the
    reason the prohibition is written down here.

    Tries Pillow first (CPython), then System.Drawing (IronPython/Revit).
    Both fallbacks swallow their exceptions, so a failure in each leaves the
    ORIGINAL mismatched image on disk and reports only through the warning at
    the end.  That is a No-Silent-Failure violation; it predates A4 and is
    left alone deliberately rather than mixed into a logic change.
    """
    try:
        from vop_interwoven.np_backend import PILLOW_AVAILABLE
        if PILLOW_AVAILABLE:
            from vop_interwoven.np_backend import Image
            img = Image.open(path)
            needs_resize = img.size != (width_px, height_px)
            if needs_resize:
                resized = img.resize((width_px, height_px), Image.LANCZOS)
            img.close()  # Release file handle before rename/overwrite (Windows lock)
            if needs_resize:
                resized.save(path)
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
            # Accept both pipeline naming ("width"/"height"/"cell_size") and
            # streaming naming ("grid_W"/"grid_H"/"cell_size_ft_effective").
            W = int(view_data.get("grid_W") or view_data.get("width") or 0)
            H = int(view_data.get("grid_H") or view_data.get("height") or 0)
            cell_size_ft = float(
                view_data.get("cell_size_ft_effective")
                or view_data.get("cell_size_ft")
                or view_data.get("cell_size")
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

            vop_grid = None
            if cell_size_ft > 0:
                vop_grid = {"W": W, "H": H, "cell_size_ft": cell_size_ft,
                            "origin_uv": _grid_origin_uv(view_data)}

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
