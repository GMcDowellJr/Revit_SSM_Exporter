"""
Depth convention contract: w = dot(element - origin, forward) must increase
with distance from the viewer so that smaller w = closer = wins the depth test.

This is view-type agnostic. Tests use representative forward vectors for each
major view type to confirm the ordering holds.
"""

from vop_interwoven.revit.view_basis import ViewBasis, world_to_view


def _w(point, vb):
    """Return only the depth component w."""
    _, _, w = world_to_view(point, vb)
    return w


# ── Floor plan (viewer above, looking down) ──────────────────────────────────

def test_floor_plan_closer_element_has_smaller_w():
    """For floor plan view, element at cut plane (high Z) must have smaller w
    than element far below (low Z)."""
    # ViewDirection for floor plan = (0, 0, -1); forward = vd (NOT negated)
    vb = ViewBasis(
        origin=(0.0, 0.0, 20.5),   # origin at cut plane
        right=(1.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        forward=(0.0, 0.0, -1.0),  # ViewDirection for floor plan
    )

    cut_plane_element  = (0.0, 0.0, 20.5)  # at cut plane — closest to viewer
    floor_slab         = (0.0, 0.0, 16.5)  # below cut — farther
    level_1_wall       = (0.0, 0.0,  6.0)  # far below — farthest

    w_cut   = _w(cut_plane_element, vb)
    w_floor = _w(floor_slab, vb)
    w_wall  = _w(level_1_wall, vb)

    assert w_cut < w_floor, "Cut-plane element must be closer (smaller w) than floor slab"
    assert w_floor < w_wall, "Floor slab must be closer (smaller w) than Level-1 wall"


def test_floor_plan_depth_test_floor_occludes_wall():
    """Simulated depth test: floor slab (w_occ) must block Level-1 wall (w_depth)."""
    vb = ViewBasis(
        origin=(0.0, 0.0, 0.0),
        right=(1.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        forward=(0.0, 0.0, -1.0),
    )
    w_floor = _w((0.0, 0.0, 16.5), vb)
    w_wall  = _w((0.0, 0.0,  6.0), vb)

    # Simulate the depth test: new element wins if w_depth < w_occ
    floor_wins_over_wall = w_floor < w_wall   # floor closer → should be True
    wall_blocked_by_floor = not (w_wall < w_floor)  # wall cannot overwrite floor

    assert floor_wins_over_wall, "Floor (w={}) must be < wall (w={})".format(w_floor, w_wall)
    assert wall_blocked_by_floor, "Wall depth test must fail against floor w_occ"


# ── Section (viewer in front, looking in +Y) ─────────────────────────────────

def test_section_closer_element_has_smaller_w():
    """For a section looking in +Y, element at Y=5 is closer than Y=20."""
    vb = ViewBasis(
        origin=(0.0, 0.0, 0.0),
        right=(1.0, 0.0, 0.0),
        up=(0.0, 0.0, 1.0),
        forward=(0.0, 1.0, 0.0),  # ViewDirection for section looking in +Y
    )
    near = (0.0,  5.0, 0.0)
    far  = (0.0, 20.0, 0.0)

    assert _w(near, vb) < _w(far, vb), "Nearer element must have smaller w in section view"


# ── RCP (viewer below, looking up) ───────────────────────────────────────────

def test_rcp_closer_element_has_smaller_w():
    """For RCP (looking up), element at Z=9 (ceiling, close) must have smaller w
    than element at Z=12 (farther above)."""
    vb = ViewBasis(
        origin=(0.0, 0.0, 9.0),    # cut plane for RCP
        right=(1.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        forward=(0.0, 0.0, 1.0),   # ViewDirection for RCP (looking up)
    )
    # Looking UP: lower Z (below cut) = farther from viewer; higher Z = farther up = farther
    # At cut plane (Z=9): w=0; below cut (Z=5): w<0 (closer); above cut (Z=12): w>0 (farther)
    below_cut  = (0.0, 0.0, 5.0)   # closer to viewer looking up
    at_cut     = (0.0, 0.0, 9.0)   # at cut
    above_cut  = (0.0, 0.0, 12.0)  # farther

    assert _w(below_cut, vb) < _w(at_cut, vb), "Element below cut must be closer (smaller w) in RCP"
    assert _w(at_cut, vb) < _w(above_cut, vb), "Cut-plane element must be closer than above-cut"
