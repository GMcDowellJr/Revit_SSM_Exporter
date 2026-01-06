"""
PNG export for VOP interwoven pipeline rasters.

Generates visual representations of raster data with color-coded cells.
"""

import os


def export_raster_to_png(view_result, output_path, pixels_per_cell=4, cut_vs_projection=False, diag=None):
    """Export VOP raster to PNG image with color-coded occupancy.

    Color Legend:
        - White: Empty (no model, no annotation)
        - Gray: Model geometry only
          - Light gray (192): Projection (if cut_vs_projection=True)
          - Dark gray (64): Cut (if cut_vs_projection=True)
          - Medium gray (128): Model (if cut_vs_projection=False)
        - Blue (cornflower): Annotation only (no model underneath)
        - Orange: Annotation over model (overlap)

    Args:
        view_result: View result dictionary from pipeline
        output_path: Path to save PNG file
        pixels_per_cell: Pixels per raster cell (default: 4)
        cut_vs_projection: If True, distinguish cut (dark gray) vs projection (light gray)
                          If False, all model elements are same color

    Returns:
        Path to saved PNG file, or None on error

    Example:
        >>> result = run_vop_pipeline(doc, [view.Id], cfg)
        >>> view_data = result['views'][0]
        >>> png_path = export_raster_to_png(view_data, r'C:\temp\vop_output.png')
    """
    try:
        # Import .NET drawing libraries (IronPython/CPython3 compatible)
        try:
            import clr
            clr.AddReference('System.Drawing')
            from System.Drawing import Bitmap, Color
            from System.Drawing.Imaging import ImageFormat
        except ImportError:
            print("Error: System.Drawing not available. Running outside Revit/Dynamo?")
            return None

        # Extract raster data
        # Accept either:
        #   - full view_result dict: {"width","height","raster",...}
        #   - raw raster dict: {"width","height",...} (common in Dynamo glue)
        if isinstance(view_result, dict) and ("raster" in view_result):
            raster_dict = view_result.get("raster") or {}
            width = view_result.get("width")
            height = view_result.get("height")
            cfg = view_result.get("config") or {}
        else:
            raster_dict = view_result if isinstance(view_result, dict) else {}
            width = None
            height = None
            cfg = {}

        # Fallback to raster_dict width/height (ViewRaster.to_dict may include them)
        def _pick_int(*vals):
            for v in vals:
                try:
                    iv = int(v)
                    if iv > 0:
                        return iv
                except Exception:
                    continue
            return 0

        width = _pick_int(
            width,
            raster_dict.get("width", None),
            view_result.get("grid_W", None) if isinstance(view_result, dict) else None,
            raster_dict.get("grid_W", None),
        )
        height = _pick_int(
            height,
            raster_dict.get("height", None),
            view_result.get("grid_H", None) if isinstance(view_result, dict) else None,
            raster_dict.get("grid_H", None),
        )

        if width <= 0 or height <= 0:
            vr_keys = list(view_result.keys()) if isinstance(view_result, dict) else [type(view_result).__name__]
            rd_keys = list(raster_dict.keys()) if isinstance(raster_dict, dict) else [type(raster_dict).__name__]
            raise KeyError(
                "width/height missing or invalid in PNG export input; "
                "view_result keys={0}; raster_dict keys={1}".format(vr_keys, rd_keys)
            )

        # Model presence mode (PR4): drives _has_model()
        model_presence_mode = None
        try:
            if isinstance(cfg, dict):
                model_presence_mode = cfg.get("model_presence_mode", None)
            else:
                model_presence_mode = getattr(cfg, "model_presence_mode", None)
        except Exception:
            model_presence_mode = None

        model_edge_key = raster_dict.get("model_edge_key", [])
        model_mask = raster_dict.get("model_mask", [])
        model_proxy_mask = raster_dict.get(
            "model_proxy_mask",
            raster_dict.get("model_proxy_presence", []),
        )

        def _has_model(idx):
            mode = (model_presence_mode or "occ").lower()

            if mode == "occ":
                return (idx < len(model_mask)) and bool(model_mask[idx])

            if mode == "edge":
                return (idx < len(model_edge_key)) and (model_edge_key[idx] != -1)

            if mode == "proxy":
                return (idx < len(model_proxy_mask)) and bool(model_proxy_mask[idx])

            if mode == "any":
                present = False
                if idx < len(model_mask):
                    present = present or bool(model_mask[idx])
                if idx < len(model_edge_key):
                    present = present or (model_edge_key[idx] != -1)
                if idx < len(model_proxy_mask):
                    present = present or bool(model_proxy_mask[idx])
                return present

            raise ValueError("Unknown model_presence_mode: {0}".format(mode))

        # Use model_edge_key (OCCUPANCY - boundary only) instead of model_mask (OCCLUSION - interior + boundary)
        model_edge_key = raster_dict.get('model_edge_key', [])

        # Get anno_over_model and anno_key for annotation visualization
        anno_over_model = raster_dict.get('anno_over_model', [])
        anno_key = raster_dict.get('anno_key', [])

        # Calculate bitmap size
        width_px = width * pixels_per_cell
        height_px = height * pixels_per_cell

        # Define colors
        col_empty = Color.White
        col_projection = Color.FromArgb(192, 192, 192)  # Light gray (RGB: 192, 192, 192)
        col_cut = Color.FromArgb(64, 64, 64)            # Dark gray (RGB: 64, 64, 64)
        col_model = Color.FromArgb(128, 128, 128)       # Medium gray (default if not distinguishing)
        col_anno_only = Color.FromArgb(100, 149, 237)   # Cornflower blue (annotations only)
        col_anno_over_model = Color.FromArgb(255, 165, 0)  # Orange (annotation over model)

        # Create bitmap
        bmp = Bitmap(width_px, height_px)

        # Initialize to white background
        for x in range(width_px):
            for y in range(height_px):
                bmp.SetPixel(x, y, col_empty)

        # Fill cells (flip j so origin is bottom-left visually)
        for i in range(width):
            for j in range(height):
                idx = j * width + i

                # Check if cell has model edge (occupancy - boundary only) or annotation
                has_model = _has_model(idx)
                has_anno = (anno_key[idx] >= 0) if idx < len(anno_key) else False

                # Skip completely empty cells
                if not has_model and not has_anno:
                    continue

                # Determine color based on what's present
                if has_anno and has_model:
                    # Annotation over model = orange
                    col = col_anno_over_model
                elif has_anno:
                    # Annotation only = blue
                    col = col_anno_only
                elif cut_vs_projection:
                    # If/when a real cut mask exists, wire it here; until then, fall back.
                    col = col_model
                else:
                    # Model only: default gray
                    col = col_model

                # Map cell (i, j) to pixel block
                x0 = i * pixels_per_cell
                # Flip vertically so j=0 is bottom row
                y0 = (height - 1 - j) * pixels_per_cell
                x1 = x0 + pixels_per_cell
                y1 = y0 + pixels_per_cell

                # Fill the block
                for px in range(x0, min(x1, width_px)):
                    for py in range(y0, min(y1, height_px)):
                        bmp.SetPixel(px, py, col)

        # Ensure output directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)

        # Save PNG
        bmp.Save(output_path, ImageFormat.Png)

        return output_path

    except Exception as e:
        # Recoverable per-view export failure; must be visible and counted
        if diag is not None:
            diag.error(
                phase="export_png",
                callsite="export_raster_to_png",
                message="PNG export failed",
                exc=e,
                extra={"output_path": output_path},
            )
        else:
            print("Error exporting PNG: {0}: {1}".format(type(e).__name__, e))

        return None


def export_pipeline_results_to_pngs(pipeline_result, output_dir, pixels_per_cell=4, cut_vs_projection=False, diag=None):
    """Export all views from pipeline result to PNG files.

    Args:
        pipeline_result: Full pipeline result dictionary
        output_dir: Directory to save PNG files
        pixels_per_cell: Pixels per raster cell
        cut_vs_projection: If True, distinguish cut vs projection with colors

    Returns:
        List of saved PNG file paths

    Example:
        >>> result = run_vop_pipeline(doc, view_ids, cfg)
        >>> png_files = export_pipeline_results_to_pngs(result, r'C:\temp\vop_output')
    """
    saved_files = []

    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        for view_data in pipeline_result.get('views', []):
            
            # Skip views that did not produce raster payload (e.g., per-view failure stub)
            # These have keys like: {'view_id','view_name','success','diag'} and cannot be exported to PNG.
            if not isinstance(view_data, dict) or ("raster" not in view_data):
                if diag is not None:
                    diag.warn(
                        phase="export_png",
                        callsite="export_pipeline_results_to_pngs",
                        message="Skipping PNG export for view without raster payload",
                        extra={
                            "view_keys": list(view_data.keys()) if isinstance(view_data, dict) else [type(view_data).__name__],
                            "view_id": (view_data.get("view_id") if isinstance(view_data, dict) else None),
                            "view_name": (view_data.get("view_name") if isinstance(view_data, dict) else None),
                            "success": (view_data.get("success") if isinstance(view_data, dict) else None),
                        },
                    )
                else:
                    print("Skipping PNG export for view without raster payload: {0}".format(
                        view_data.get("view_name", "<unknown>") if isinstance(view_data, dict) else type(view_data).__name__
                    ))
                continue

            view_name = view_data['view_name']
            view_id = view_data['view_id']

            # Sanitize filename
            safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in view_name)
            filename = f"{safe_name}_{view_id}.png"
            output_path = os.path.join(output_dir, filename)

            png_path = export_raster_to_png(
                view_data,
                output_path,
                pixels_per_cell,
                cut_vs_projection,
                diag=diag,
            )

            if png_path:
                saved_files.append(png_path)
                print(f"Saved: {png_path}")

    except Exception as e:
        # Recoverable batch failure; must be visible and counted
        if diag is not None:
            diag.error(
                phase="export_png",
                callsite="export_pipeline_results_to_pngs",
                message="PNG batch export failed",
                exc=e,
                extra={"output_dir": output_dir},
            )
        else:
            print("Error exporting PNGs: {0}: {1}".format(type(e).__name__, e))

        return None

    return saved_files
