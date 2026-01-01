# ============================================================
# FINAL SKELETON (revised): Pass A / Pass B are incremental and lazy
#
# LOCKED POLICIES (from this thread)
# - Per-cell depth (w_min_by_cell) for BOTH occluders and candidates.
# - Unknown depth sorts to the back; unknown-depth candidates never early-out by occlusion.
# - EPS_DEPTH = 1/32" (in Revit internal feet units).
# - AREAL only writes occlusion (parity fill boundary+interior).
# - 3D occupancy is boundary-only.
# - D1: Selected symbolic curves are PROMOTED to 3D occupancy contributors,
#       are depth-masked by occlusion, and are removed from 2D output.
# - No category-specific rules yet.
#
# KEY SCHEDULING DECISION (this turn)
# - Build Envelope.uv_aabb for ALL model elements up front (cheap bbox projection, no loops).
# - Split candidates into Pass A vs Pass B using rect size derived from uv_aabb.
# ============================================================


# ----------------------------
# Core records (logical)
# ----------------------------

class Envelope:
    uv_aabb: tuple[float, float, float, float] | None   # (min_u, min_v, max_u, max_v); None => skip element
    depth_min: float | None                             # for SORT ONLY (may be None)
    depth_max: float | None
    elem_id: int
    category: str
    source: str  # HOST | RVT_LINK | etc.

class LoopSet:
    loops: list
    uv_aabb: tuple[float, float, float, float]

class Coverage:
    cells: set[tuple[int, int]]
    w_min_by_cell: dict[tuple[int, int], float]          # per-cell front-most depth for those cells

class DepthBuffer:
    w_nearest: dict[tuple[int, int], float]
    INF = float("inf")

    def __init__(self):
        self.w_nearest = {}

    def update(self, cov: Coverage):
        for c in cov.cells:
            w = cov.w_min_by_cell.get(c, None)
            if w is None:
                continue
            if w < self.w_nearest.get(c, self.INF):
                self.w_nearest[c] = w

    def rect_fully_occludes(self, rect, cand_wmin_by_cell: dict | None, eps: float, grid) -> bool:
        """
        Must NOT allocate full rect cell sets for large rects.
        grid.iter_rect_cells(rect) yields cells lazily.
        """
        if cand_wmin_by_cell is None:
            return False
        for c in grid.iter_rect_cells(rect):
            w_cand = cand_wmin_by_cell.get(c, None)
            if w_cand is None:
                return False
            w_occ = self.w_nearest.get(c, self.INF)
            if w_cand <= (w_occ + eps):
                return False
        return True

    def mask_cells(self, cov: Coverage, eps: float) -> set[tuple[int, int]]:
        out = set()
        for c in cov.cells:
            w_cand = cov.w_min_by_cell.get(c, None)
            if w_cand is None:
                out.add(c)  # cannot prove occluded
                continue
            w_occ = self.w_nearest.get(c, self.INF)
            if w_cand <= (w_occ + eps):
                out.add(c)
        return out


# 1/32" in feet (Revit internal)
EPS_DEPTH = (1.0 / 32.0) / 12.0  # 0.0026041666666666665 ft


# ----------------------------
# Grid helpers (logical)
# ----------------------------

def uv_aabb_to_cell_rect(uv_aabb, grid):
    return grid.uv_aabb_to_cell_rect(uv_aabb)

def classify_tier_by_rect(rect) -> str:
    # Locked: AREAL iff >2 in both directions
    if rect.width_cells > 2 and rect.height_cells > 2:
        return "AREAL"
    return "LINEAR" if max(rect.width_cells, rect.height_cells) > 2 else "TINY"

def depth_sort_key(env: Envelope):
    # Unknown depth sorts to back (INF), not front
    dmin = env.depth_min if env.depth_min is not None else float("inf")
    dmax = env.depth_max if env.depth_max is not None else float("inf")
    return (dmin, dmax, env.category, env.elem_id, env.source)


# ============================================================
# TOP LEVEL: export one view
# ============================================================

def export_view(view, doc, config):
    grid = build_view_grid(view, config)
    clip = build_view_clip_volume(view, config)

    # --------------------------------------------------------
    # Collect model elements (cheap; no loops computed here)
    # --------------------------------------------------------
    host_elems = collect_host_elements_for_view(doc, view, config)
    link_insts = collect_link_instances_for_view(doc, view, config)

    # --------------------------------------------------------
    # Stage 0: Build envelopes for ALL model elements up front
    # (MUST be bbox-based; NO loop/solid extraction)
    # --------------------------------------------------------
    envs = []

    for e in host_elems:
        env = make_envelope_host(e, view, clip, config)            # must compute env.uv_aabb
        if env and env.uv_aabb and (not env_outside_grid(env, grid)):
            envs.append(env)

    for env in make_envelopes_links(link_insts, view, clip, config):
        if env and env.uv_aabb and (not env_outside_grid(env, grid)):
            envs.append(env)

    # --------------------------------------------------------
    # Symbolic collection (view-contextual) and D1 promotion
    # - promoted symbolic becomes 3D contributors (masked) and removed from 2D output
    # --------------------------------------------------------
    sym_host = extract_2d_symbolic_curves_host(view, host_elems, config)  # primitives tied to source elem_id/category
    ann2d_host = extract_2d_annotations_host(view, config)

    promoted_sym3d, residual_sym2d = split_symbolic_promote_to_3d(sym_host, view, config)
    prim2d_residual = []
    prim2d_residual += residual_sym2d
    prim2d_residual += ann2d_host
    # (links symbolic later if enabled; apply same D1 split)

    # --------------------------------------------------------
    # Stage 1: Split envs into Pass A vs Pass B using uv_aabb->rect
    # --------------------------------------------------------
    areal_envs = []
    non_areal_envs = []

    for env in envs:
        rect = uv_aabb_to_cell_rect(env.uv_aabb, grid)
        tier = classify_tier_by_rect(rect)
        if tier == "AREAL":
            areal_envs.append((env, rect))
        else:
            non_areal_envs.append((env, rect, tier))

    # --------------------------------------------------------
    # Stage 2: Sort front->back (stable)
    # --------------------------------------------------------
    areal_envs.sort(key=lambda t: depth_sort_key(t[0]))
    non_areal_envs.sort(key=lambda t: depth_sort_key(t[0]))

    # Promoted symbolic should also be sorted front->back if it has depth;
    # if not, it naturally sorts to back.
    promoted_sym3d.sort(key=lambda p: depth_sort_key(p.env))

    depthbuf = DepthBuffer()

    # --------------------------------------------------------
    # PASS A: incremental occluder build (AREAL only, lazy loops)
    # --------------------------------------------------------
    if config.occlusion_enable:
        pass_a_occlusion_incremental(depthbuf, areal_envs, grid, clip, view, config)

    # --------------------------------------------------------
    # PASS B: incremental 3D occupancy build
    #   (B1) promoted symbolic curves (D1)
    #   (B2) other non-AREAL 3D candidates (LINEAR/TINY)
    # --------------------------------------------------------
    regions3d = []
    regions3d += pass_b_occupancy_promoted_symbolic(depthbuf, promoted_sym3d, grid, clip, view, config)
    regions3d += pass_b_occupancy_non_areal_3d(depthbuf, non_areal_envs, grid, clip, view, config)

    # --------------------------------------------------------
    # 2D regions (annotations + non-promoted symbolic; no masking, no occlusion updates)
    # --------------------------------------------------------
    regions2d = build_2d_regions(grid, view, prim2d_residual, config)

    raster = rasterize_regions_to_layers(grid, regions3d, regions2d, config)
    occ = compute_final_occupancy(raster, config)

    write_outputs(view, occ, raster, config)


# ============================================================
# PASS A: AREAL occluders — front->back, rect early-out, lazy loops
# ============================================================

def pass_a_occlusion_incremental(depthbuf: DepthBuffer, areal_envs, grid, clip, view, config):
    """
    areal_envs: list[(env, rect)] sorted front->back
    Process:
      - optional rect early-out ONLY if we can produce conservative per-cell depth over rect cheaply
      - if not skipped: project loops (expensive), parity-fill, compute per-cell occluder depth, update depthbuf
    """
    for (env, rect) in areal_envs:

        # Optional envelope early-out (requires per-cell depth estimate; otherwise skip early-out)
        cand_depth_rect = try_make_cand_wmin_by_cell_for_rect(env, rect, view, clip, grid, config)  # may be None

        if cand_depth_rect is not None:
            if depthbuf.rect_fully_occludes(rect, cand_depth_rect, EPS_DEPTH, grid):
                diagnostics_inc("num_areal_skipped_fully_occluded")
                continue

        # EXPENSIVE ONLY NOW:
        loopset = project_loops_view_contextual(env, view, clip, config)
        if not loopset or not loopset.loops:
            diagnostics_inc("num_areal_loop_extraction_failed")
            continue

        # Occlusion coverage: parity fill (boundary + interior)
        occ_cells = parity_fill_loops_to_cells(loopset.loops, grid) & grid.valid_cells
        if not occ_cells:
            continue

        # Per-cell depth for occluder coverage (required)
        occ_cov = compute_per_cell_depth_for_cells(env, loopset, occ_cells, view, clip, grid, config)
        if not occ_cov or not occ_cov.cells:
            diagnostics_inc("num_areal_depth_per_cell_failed")
            continue

        depthbuf.update(occ_cov)


# ============================================================
# PASS B1: Promoted symbolic curves as 3D occupancy contributors (D1)
# ============================================================

def pass_b_occupancy_promoted_symbolic(depthbuf: DepthBuffer, promoted_sym3d, grid, clip, view, config):
    regions = []

    for p in promoted_sym3d:
        # Rasterize curve to cells (boundary-only)
        cells = rasterize_curve_to_cells_line_through_cells(p.curve, grid) & grid.valid_cells
        if not cells:
            continue

        # Per-cell depth for curve coverage (sampling)
        cov = compute_per_cell_depth_for_curve(p.env, p.curve, cells, view, clip, grid, config)
        if not cov or not cov.cells:
            cov = Coverage(cells=cells, w_min_by_cell={})
            diagnostics_inc("num_promoted_symbolic_depth_unknown")

        # Depth-aware masking
        visible_cells = cells
        if config.occlusion_enable:
            visible_cells = depthbuf.mask_cells(cov, EPS_DEPTH)
            if not visible_cells:
                continue

        tier = classify_tier_by_cellset_bbox(visible_cells)
        meta = merge_meta(p.meta, {"layer": "3D", "tier": tier, "source": p.env.source, "elem_id": p.env.elem_id})
        regions += segment_cells_into_regions(visible_cells, tier=tier, layer="3D", meta=meta)

    return regions


# ============================================================
# PASS B2: Non-AREAL 3D occupancy (LINEAR/TINY) — front->back, lazy projection
# ============================================================

def pass_b_occupancy_non_areal_3d(depthbuf: DepthBuffer, non_areal_envs, grid, clip, view, config):
    regions = []

    for (env, rect, tier) in non_areal_envs:

        # Optional rect early-out (requires per-cell depth estimate; otherwise skip early-out)
        cand_depth_rect = try_make_cand_wmin_by_cell_for_rect(env, rect, view, clip, grid, config)  # may be None

        if config.occlusion_enable and cand_depth_rect is not None:
            if depthbuf.rect_fully_occludes(rect, cand_depth_rect, EPS_DEPTH, grid):
                diagnostics_inc("num_linear_tiny_skipped_fully_occluded")
                continue

        # Build representation lazily
        loopset = try_project_loops_view_contextual_fast(env, view, clip, config)
        if not loopset or not loopset.loops:
            loopset = loops_from_bbox_proxy(env)  # allowed fallback for LINEAR/TINY
            diagnostics_inc("num_linear_tiny_used_bbox_proxy")

        boundary_cells = boundary_raster_loops_to_cells(loopset.loops, grid) & grid.valid_cells
        if not boundary_cells:
            continue

        # Per-cell candidate depth for boundary coverage
        cov = compute_per_cell_depth_for_cells(env, loopset, boundary_cells, view, clip, grid, config)
        if not cov or not cov.cells:
            cov = Coverage(cells=boundary_cells, w_min_by_cell={})
            diagnostics_inc("num_linear_tiny_depth_unknown")

        visible_cells = boundary_cells
        if config.occlusion_enable:
            visible_cells = depthbuf.mask_cells(cov, EPS_DEPTH)
            if not visible_cells:
                continue

        meta = {
            "elem_id": env.elem_id,
            "category": env.category,
            "source": env.source,
            "layer": "3D",
            "tier": tier,
        }
        regions += segment_cells_into_regions(visible_cells, tier=tier, layer="3D", meta=meta)

    return regions


# ============================================================
# Envelope construction (must be cheap; no loops/solids)
# ============================================================

def make_envelope_host(elem, view, clip, config) -> Envelope | None:
    """
    MUST be cheap:
      - no solids
      - no faces
      - no loop extraction
      - no tessellation
    """

    # 1) Try view-contextual bounding box
    bb = elem.get_BoundingBox(view)

    # 2) Fallback to model-space bounding box if needed
    if bb is None:
        bb = elem.get_BoundingBox(None)
        if bb is None:
            return None  # element has no spatial extent we can reason about

    # 3) Enumerate the 8 corners in XYZ
    corners_xyz = [
        XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z),
        XYZ(bb.Min.X, bb.Min.Y, bb.Max.Z),
        XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z),
        XYZ(bb.Min.X, bb.Max.Y, bb.Max.Z),
        XYZ(bb.Max.X, bb.Min.Y, bb.Min.Z),
        XYZ(bb.Max.X, bb.Min.Y, bb.Max.Z),
        XYZ(bb.Max.X, bb.Max.Y, bb.Min.Z),
        XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z),
    ]

    # 4) Transform XYZ → view UVW
    # view_tf maps world XYZ into view coordinate system
    view_tf = get_view_world_to_view_transform(view)

    u_vals = []
    v_vals = []
    w_vals = []

    for p in corners_xyz:
        uvw = view_tf.OfPoint(p)
        u_vals.append(uvw.X)
        v_vals.append(uvw.Y)
        w_vals.append(uvw.Z)

    # 5) Build uv_aabb and depth range
    uv_aabb = (min(u_vals), min(v_vals), max(u_vals), max(v_vals))
    depth_min = min(w_vals)
    depth_max = max(w_vals)

    return Envelope(
        uv_aabb=uv_aabb,
        depth_min=depth_min,   # SORT ONLY
        depth_max=depth_max,
        elem_id=elem.Id.IntegerValue,
        category=elem.Category.Name if elem.Category else "<none>",
        source="HOST",
    )
    
def make_envelopes_links(link_insts, view, clip, config) -> list[Envelope]:
    """
    Must apply full chain: link XYZ -> host XYZ -> view UVW.
    Must also be bbox-based; no loops/solids.
    """
    envs = []

    view_tf = get_view_world_to_view_transform(view)

    for link_inst in link_insts:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue

        link_to_host = link_inst.GetTotalTransform()

        for elem in collect_elements_in_link(link_doc, view, config):

            bb = elem.get_BoundingBox(None)
            if bb is None:
                continue

            corners_xyz_link = [
                XYZ(bb.Min.X, bb.Min.Y, bb.Min.Z),
                XYZ(bb.Min.X, bb.Min.Y, bb.Max.Z),
                XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z),
                XYZ(bb.Min.X, bb.Max.Y, bb.Max.Z),
                XYZ(bb.Max.X, bb.Min.Y, bb.Min.Z),
                XYZ(bb.Max.X, bb.Min.Y, bb.Max.Z),
                XYZ(bb.Max.X, bb.Max.Y, bb.Min.Z),
                XYZ(bb.Max.X, bb.Max.Y, bb.Max.Z),
            ]

            u_vals, v_vals, w_vals = [], [], []

            for p_link in corners_xyz_link:
                p_host = link_to_host.OfPoint(p_link)
                uvw = view_tf.OfPoint(p_host)
                u_vals.append(uvw.X)
                v_vals.append(uvw.Y)
                w_vals.append(uvw.Z)

            uv_aabb = (min(u_vals), min(v_vals), max(u_vals), max(v_vals))
            depth_min = min(w_vals)
            depth_max = max(w_vals)

            envs.append(
                Envelope(
                    uv_aabb=uv_aabb,
                    depth_min=depth_min,
                    depth_max=depth_max,
                    elem_id=elem.Id.IntegerValue,
                    category=elem.Category.Name if elem.Category else "<none>",
                    source="RVT_LINK",
                )
            )

    return envs

# ============================================================
# Geometry projection hooks (view-contextual)
# ============================================================

def project_loops_view_contextual(env: Envelope, view, clip, config) -> LoopSet | None:
    """
    EXPENSIVE (relative) path: view-contextual footprint extraction -> UV loops.

    GOALS (structure, not policy):
      - Preserve current hybrid behavior:
          1) category API footprint (preferred)
          2) silhouette edges (from view-context GeometryElement)
          3) coarse tessellation / face sampling (from same GeometryElement)
          4) OBB/Envelope proxy (last resort)
      - Must apply full transforms for links: link XYZ -> host XYZ -> view UVW.
      - Must use Options.View=view for geometry fetch used by silhouette/tess fallbacks.
      - Must return normalized UV loops + hole classification suitable for parity fill.
    """

    # --------------------------------------------------------
    # 0) Resolve element + transforms
    # --------------------------------------------------------
    elem, link_to_host_tf = resolve_element_and_link_transform(env)  # Identity if HOST
    view_tf = get_view_world_to_view_transform(view)                # host world XYZ -> view UVW (crop-local)

    # Helper: map any XYZ point into view UV (host world assumed)
    def xyz_to_uv(p_host_xyz):
        uvw = view_tf.OfPoint(p_host_xyz)
        return (uvw.X, uvw.Y)

    # Helper: apply transforms in correct chain to bring points into host world
    def to_host_world(p_elem_xyz, inst_tf):
        # inst_tf: instance-local -> element-local (or vice versa depending on extraction);
        # contract: output is host-world XYZ
        p_world = inst_tf.OfPoint(p_elem_xyz)
        return link_to_host_tf.OfPoint(p_world)

    # --------------------------------------------------------
    # 1) Category API attempt (preferred; may bypass geom)
    # --------------------------------------------------------
    # Returns loops already in host world XYZ OR directly in view-local UV,
    # depending on your current implementation; normalize below.
    cat_loops = try_category_api_footprint(elem, view, clip, config)
    if cat_loops:
        loops_uv = normalize_loops_to_uv(cat_loops, link_to_host_tf, view_tf, clip, config)
        loops_uv = sanitize_and_classify_loops(loops_uv, config)
        if loops_uv:
            return LoopSet(loops=loops_uv, uv_aabb=compute_uv_aabb(loops_uv))

    # --------------------------------------------------------
    # 2) Fetch view-contextual geometry ONCE for hybrid fallbacks
    # --------------------------------------------------------
    opts = Options()
    opts.View = view
    opts.DetailLevel = view.DetailLevel
    opts.IncludeNonVisibleObjects = False
    opts.ComputeReferences = False

    geom = elem.get_Geometry(opts)
    if geom is None:
        # No geometry to derive from; last resort proxy
        obb_loops = loops_from_obb_proxy(env, view, clip, config)
        loops_uv = sanitize_and_classify_loops(obb_loops, config)
        return LoopSet(loops=loops_uv, uv_aabb=compute_uv_aabb(loops_uv)) if loops_uv else None

    # --------------------------------------------------------
    # 3) Silhouette edges fallback (uses geom)
    # --------------------------------------------------------
    # Output expectation: list of polylines in host world XYZ (each polyline is ordered XYZ pts)
    sil_loops_xyz = try_extract_silhouette_loops_from_geom(
        geom=geom,
        view=view,
        clip=clip,
        config=config,
        link_to_host_tf=link_to_host_tf,
    )

    if sil_loops_xyz:
        loops_uv = []
        for loop_xyz in sil_loops_xyz:
            loop_uv = [xyz_to_uv(p_host) for p_host in loop_xyz]
            loops_uv.append(loop_uv)
        loops_uv = sanitize_and_classify_loops(loops_uv, config)
        if loops_uv:
            return LoopSet(loops=loops_uv, uv_aabb=compute_uv_aabb(loops_uv))

    # --------------------------------------------------------
    # 4) Coarse tessellation / face boundary fallback (uses same geom)
    # --------------------------------------------------------
    # Output: list of boundary loops in host world XYZ
    tess_loops_xyz = try_extract_boundary_loops_from_geom_coarse(
        geom=geom,
        view=view,
        clip=clip,
        config=config,
        link_to_host_tf=link_to_host_tf,
    )

    if tess_loops_xyz:
        loops_uv = []
        for loop_xyz in tess_loops_xyz:
            loop_uv = [xyz_to_uv(p_host) for p_host in loop_xyz]
            loops_uv.append(loop_uv)
        loops_uv = sanitize_and_classify_loops(loops_uv, config)
        if loops_uv:
            return LoopSet(loops=loops_uv, uv_aabb=compute_uv_aabb(loops_uv))

    # --------------------------------------------------------
    # 5) Last resort: OBB / Envelope proxy
    # --------------------------------------------------------
    obb_loops = loops_from_obb_proxy(env, view, clip, config)  # should return UV loops or host-world XYZ loops
    loops_uv = sanitize_and_classify_loops(obb_loops, config)
    if loops_uv:
        return LoopSet(loops=loops_uv, uv_aabb=compute_uv_aabb(loops_uv))

    return None

# ============================================================
# Supporting pseudo-code (hybrid helpers)
# ============================================================

def normalize_loops_to_uv(raw_loops, link_to_host_tf, view_tf, clip, config):
    """
    Normalize raw loop output from category API to UV loops:
      - If raw_loops are XYZ: apply link_to_host_tf then view_tf
      - If already UV: pass through
      - Apply clipping if needed (optional, depending on how raw_loops were produced)
    """
    loops_uv = []
    for loop in raw_loops:
        if loop_is_uv(loop):
            loops_uv.append(loop)
        else:
            pts_host = [link_to_host_tf.OfPoint(p) for p in loop]  # safe even if Identity
            pts_host = clip_polyline_to_clip_volume(pts_host, clip)
            loops_uv.append([to_uv(view_tf, p) for p in pts_host])
    return loops_uv


def sanitize_and_classify_loops(loops_uv, config):
    """
    - Ensure closed loops (snap last to first within tolerance)
    - Remove degenerate loops (area ~ 0, too few points)
    - Simplify near-collinear points if allowed
    - Classify holes vs outers deterministically (containment in UV)
    Output format: list of {points:[(u,v)...], is_hole:bool}
    """
    cleaned = []
    for loop in loops_uv:
        loop = close_loop(loop, config)
        loop = simplify_loop(loop, config)
        if not is_valid_loop(loop, config):
            continue
        cleaned.append(loop)

    if not cleaned:
        return None

    return classify_outer_and_holes_by_containment(cleaned, config)


def try_extract_silhouette_loops_from_geom(geom, view, clip, config, link_to_host_tf):
    """
    Silhouette extraction (high-level):
      - Walk GeometryElement, expanding GeometryInstance
      - For Solids:
          * determine candidate edges forming silhouette wrt view direction
          * tessellate those edges coarsely into XYZ polylines
      - Transform into host world via link_to_host_tf and instance transforms
      - Optionally stitch edges into closed polylines
    """
    view_dir_host = get_view_direction_in_host_world(view)  # normalized XYZ

    polylines = []  # list[list[XYZ]]
    for (gobj, inst_tf) in iter_geom_with_instance_transform(geom):
        if is_solid(gobj) and gobj.Volume > 0:
            edge_polylines = solid_silhouette_edges_to_polylines(
                solid=gobj,
                view_dir=view_dir_host,
                config=config,
            )
            for pl in edge_polylines:
                pts_host = [link_to_host_tf.OfPoint(inst_tf.OfPoint(p)) for p in pl]
                pts_host = clip_polyline_to_clip_volume(pts_host, clip)
                polylines.append(pts_host)

        elif is_mesh(gobj):
            edge_polylines = mesh_silhouette_edges_to_polylines(
                mesh=gobj,
                view_dir=view_dir_host,
                config=config,
            )
            for pl in edge_polylines:
                pts_host = [link_to_host_tf.OfPoint(inst_tf.OfPoint(p)) for p in pl]
                pts_host = clip_polyline_to_clip_volume(pts_host, clip)
                polylines.append(pts_host)

    # Stitch open polylines into closed loops (tolerance-based endpoint welding)
    loops_xyz = stitch_polylines_into_loops(polylines, config)
    return loops_xyz if loops_xyz else None


def try_extract_boundary_loops_from_geom_coarse(geom, view, clip, config, link_to_host_tf):
    """
    Coarse tessellation fallback (high-level):
      - Walk GeometryElement, expanding GeometryInstance
      - For each Solid:
          * pick candidate faces relevant to view (policy-dependent)
          * take face edge loops (CurveLoops), tessellate coarsely
      - Transform to host world
      - Optionally take a 2D hull / merge loops to reduce noise (policy-dependent)
    """
    loops_xyz = []

    for (gobj, inst_tf) in iter_geom_with_instance_transform(geom):
        if is_solid(gobj) and gobj.Volume > 0:
            for face in gobj.Faces:
                if not face_relevant_to_view(face, view, config):
                    continue
                for curve_loop in face.GetEdgesAsCurveLoops():
                    pts = tessellate_curve_loop(curve_loop, config)
                    pts_host = [link_to_host_tf.OfPoint(inst_tf.OfPoint(p)) for p in pts]
                    pts_host = clip_polyline_to_clip_volume(pts_host, clip)
                    if len(pts_host) >= 3:
                        loops_xyz.append(pts_host)

    # Optional: reduce to outer boundary by projecting to UV and taking a hull/union
    # Keep as placeholder to preserve current behavior (if you already do this).
    loops_xyz = maybe_reduce_loops_to_outer_boundary(loops_xyz, view, config)

    return loops_xyz if loops_xyz else None


def iter_geom_with_instance_transform(geom):
    """
    Yield (gobj, inst_tf) pairs where inst_tf maps gobj points into the element's geometry space.
    For non-instance objects, inst_tf = Identity.
    """
    for gobj in geom:
        if is_geometry_instance(gobj):
            inst_tf = gobj.Transform
            inst_geom = gobj.GetInstanceGeometry()
            for child in inst_geom:
                yield (child, inst_tf)
        else:
            yield (gobj, Transform.Identity)
# ============================================================
# Depth construction helpers (per-cell)
# ============================================================

def compute_per_cell_depth_for_cells(env: Envelope, loopset: LoopSet, cells: set, view, clip, grid, config) -> Coverage | None:
    """
    Compute per-cell front-most depth for coverage cells.
    Must apply full transforms for links.
    """
    # ... implementation not shown ...
    return None

def compute_per_cell_depth_for_curve(env: Envelope, curve, cells: set, view, clip, grid, config) -> Coverage | None:
    """
    For promoted symbolic curves:
      - sample curve in view context
      - map samples to cells
      - store min(w) per cell
    """
    # ... implementation not shown ...
    return None

def try_make_cand_wmin_by_cell_for_rect(env: Envelope, rect, view, clip, grid, config) -> dict | None:
    """
    Optional optimization: cheap conservative per-cell depth estimate over rect.
    Safe default: None (no early-out).
    If implemented, must be conservative or it will incorrectly skip visible candidates.
    """
    return None


# ============================================================
# 2D lane (annotations + non-promoted symbolic) — unchanged
# ============================================================

def build_2d_regions(grid, view, prim2d, config):
    regions = []
    for p in prim2d:
        if p.kind == "CURVE":
            cells = rasterize_curve_to_cells_line_through_cells(p.payload, grid) & grid.valid_cells
            if not cells:
                continue
            regions += segment_cells_into_regions(cells, tier="LINEAR", layer="2D", meta=p.meta)
        elif p.kind.endswith("_RECT"):
            rect = p.payload
            # keep range-based iteration; shown as set for simplicity
            cells = set(grid.iter_rect_cells(rect)) & grid.valid_cells
            if not cells:
                continue
            tier = classify_tier_by_cellset_bbox(cells)
            regions += segment_cells_into_regions(cells, tier=tier, layer="2D", meta=p.meta)
    return regions