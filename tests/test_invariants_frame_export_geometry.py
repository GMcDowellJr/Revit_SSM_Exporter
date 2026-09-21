"""Property tests for frame_export_geometry's STATED invariants.

CITED BY CLAIM TEXT, NOT BY LINE NUMBER -- same discipline as
tests/test_invariants_resolution_cap.py, and for the same reason: a line
number cannot fail, so a citation that rots turns into confident misdirection.

THE CEILING CLAIM IS BOUNDED, NOT UNIVERSAL, AND THAT IS WHY THESE EXIST.
The first draft of the code asserted "neither axis of B exceeds the ceiling"
flatly and called the correction loop a float-last-bit backstop. A 200,000
case sweep falsified both before either reached a commit: the loop fires 687
times (up to 63 iterations, because cap_axes floors its derived prediction
while B's lattice is measured half-up), and the ceiling is breached 3 times
-- always on a frame whose aspect is steeper than the cap itself, where the
fitted axis has been driven to 1 px and there is nowhere left to go. That is
the corner cap_axes already documents for its own backoff, inherited rather
than introduced.

So the claim under test is the bounded one, and the corner beyond it is
pinned by a case of its own rather than quietly excluded by a strategy that
never generates it. An invariant test whose generator cannot reach the
failing region is a test that agrees with itself.
"""
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vop_interwoven.resolution_contract import (  # noqa: E402
    MAX_STAGE_A_AXIS_PX,
    effective_export_dpi,
    feet_per_pixel_at_dpi,
    frame_export_geometry,
)

EXTENT = st.floats(min_value=0.05, max_value=20000.0,
                   allow_nan=False, allow_infinity=False)
ORIGIN = st.floats(min_value=-2000.0, max_value=2000.0,
                   allow_nan=False, allow_infinity=False)
SCALE = st.sampled_from([1.0, 12.0, 24.0, 48.0, 96.0, 192.0, 384.0])
DPI = st.sampled_from([72.0, 150.0, 300.0, 600.0])
FIT = st.sampled_from(["horizontal", "vertical"])
CAP = st.sampled_from([64, 1000, 4096, MAX_STAGE_A_AXIS_PX])

# The aspect band the bounded ceiling claim is made over. Stage A's own frames
# sit far inside it; ExportImage is believed to clamp aspect at 10:1 (see the
# UNCONFIRMED note in resolution_contract.py).
SHALLOW = st.floats(min_value=0.05, max_value=20.0,
                    allow_nan=False, allow_infinity=False)


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, x0=ORIGIN, y0=ORIGIN,
       scale=SCALE, dpi=DPI, fit=FIT, cap=CAP)
def test_neither_axis_of_the_frame_exceeds_the_ceiling(u, aspect, x0, y0,
                                                       scale, dpi, fit, cap):
    """CEILING (bounded): "neither axis of B exceeds the ceiling WHILE the
    frame's aspect is shallower than the cap"."""
    v = u * aspect
    g = frame_export_geometry((x0, y0, x0 + u, y0 + v), None, scale, dpi,
                              fit_direction=fit, max_axis_px=cap)
    assert max(g["frame_px"]) <= cap
    # ... and what is actually handed to Revit is inside it too.
    assert g["requested_px"] <= cap
    assert g["predicted_derived_px"] <= cap


def test_the_ceiling_claim_fails_beyond_its_stated_boundary():
    """The corner the bounded claim excludes, pinned rather than assumed
    away. If this ever starts passing, the boundary in the code's comment has
    moved and the prose above it is stale."""
    # Aspect ~10,895:1 against a 10,000 px cap: the fitted axis bottoms out at
    # 1 px with the derived axis still over. One of the three cases the sweep
    # found.
    g = frame_export_geometry((0.0, 0.0, 0.8848, 9640.3137), None, 24.0, 72.0,
                              fit_direction="horizontal")
    assert g["frame_fit_px"] == 1
    assert max(g["frame_px"]) > MAX_STAGE_A_AXIS_PX


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, x0=ORIGIN, y0=ORIGIN,
       scale=SCALE, dpi=DPI, fit=FIT)
def test_the_frame_is_never_clipped_or_re_centred(u, aspect, x0, y0,
                                                  scale, dpi, fit):
    """FRAME: "B is not clipped and not re-centred: feet-per-pixel grows
    until B fits, so the frame stays whole and the RESOLUTION is what drops".

    This is the invariant the whole step exists for, so it is asserted over
    the capped and uncapped cases alike rather than at one oversized fixture.
    """
    v = u * aspect
    g = frame_export_geometry((x0, y0, x0 + u, y0 + v), None, scale, dpi,
                              fit_direction=fit)
    assert g["frame_extent_ft"][0] == pytest.approx(u, rel=1e-9)
    assert g["frame_extent_ft"][1] == pytest.approx(v, rel=1e-9)
    if g["cap_applied"]:
        # The drop landed on resolution, and it is recorded as such.
        assert g["achieved_fpp_ft"] > g["requested_fpp_ft"]
        assert g["achieved_export_dpi"] < g["requested_export_dpi"]


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, x0=ORIGIN, y0=ORIGIN,
       scale=SCALE, dpi=DPI, fit=FIT)
def test_forward_and_inverse_mappings_compose(u, aspect, x0, y0, scale, dpi, fit):
    """ROUND TRIP: "effective_export_dpi() above is its inverse ... and the
    two are composed back to back".

    The forward mapping's own output is fed to the inverse. Checking each
    against its own expected value would bind each to its own copy -- the
    defect class this repo has shipped twice."""
    v = u * aspect
    g = frame_export_geometry((x0, y0, x0 + u, y0 + v), None, scale, dpi,
                              fit_direction=fit)
    recovered = effective_export_dpi(g["crop_snapped_uv"],
                                     g["crop_px"][0], g["crop_px"][1], scale)
    assert recovered is not None
    assert recovered == pytest.approx(g["achieved_export_dpi"], rel=1e-6)


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, scale=SCALE, dpi=DPI)
def test_feet_per_pixel_is_the_inverse_of_dpi(u, aspect, scale, dpi):
    """FORWARD/INVERSE: "fpp = view_scale / (12 * dpi)" and
    "dpi = scale / (12 * fpp)" are the same statement."""
    fpp = feet_per_pixel_at_dpi(scale, dpi)
    # A rectangle of exactly n pixels at that fpp must read back as that dpi.
    n_u, n_v = 640, max(1, int(640 * aspect))
    rect = (0.0, 0.0, n_u * fpp, n_v * fpp)
    assert effective_export_dpi(rect, n_u, n_v, scale) == pytest.approx(dpi, rel=1e-9)


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, x0=ORIGIN, y0=ORIGIN,
       inset=st.floats(min_value=0.0, max_value=0.45,
                       allow_nan=False, allow_infinity=False),
       scale=SCALE, dpi=DPI, fit=FIT)
def test_snapping_is_outward_and_stays_inside_the_frame(u, aspect, x0, y0,
                                                        inset, scale, dpi, fit):
    """SNAP: "A's corners move OUTWARD to the nearest lattice lines" and
    "Snapping outward can only ever add crop, never clip it".

    Clipping here would silently drop rendered content off the capture, which
    no later stage could detect. Escaping B would make crop_offset_px address
    pixels the image does not contain. Both directions are asserted."""
    v = u * aspect
    frame = (x0, y0, x0 + u, y0 + v)
    crop = (x0 + inset * u, y0 + inset * v,
            x0 + (1.0 - inset) * u, y0 + (1.0 - inset) * v)
    g = frame_export_geometry(frame, crop, scale, dpi, fit_direction=fit)
    s = g["crop_snapped_uv"]
    fpp = g["achieved_fpp_ft"]
    tol = 1e-6 * max(1.0, abs(u) + abs(v))

    # outward: never inside the requested crop
    assert s[0] <= crop[0] + tol
    assert s[1] <= crop[1] + tol
    assert s[2] >= crop[2] - tol
    assert s[3] >= crop[3] - tol
    # inside the REALISED frame -- B rounded out to the lattice, which is what
    # the capture spans. Asserting against raw B here is wrong and was tried:
    # an image is a whole number of pixels, so an off-lattice B cannot be the
    # rendered rectangle, and requiring containment in it forces the lattice
    # to round DOWN and clip the frame's far edge.
    fs = g["frame_snapped_uv"]
    assert s[0] >= fs[0] - tol
    assert s[1] >= fs[1] - tol
    assert s[2] <= fs[2] + tol
    assert s[3] <= fs[3] + tol
    # ... and the realised frame contains B, growing only at the max corner.
    assert fs[0] == pytest.approx(frame[0], abs=tol)
    assert fs[1] == pytest.approx(frame[1], abs=tol)
    assert fs[2] >= frame[2] - tol
    assert fs[3] >= frame[3] - tol
    # by strictly less than one pixel, or the lattice is not tight
    assert fs[2] - frame[2] < fpp + tol
    assert fs[3] - frame[3] < fpp + tol
    # and the offset really is a whole number of pixels from B.min
    i0, j0 = g["crop_offset_px"]
    assert i0 >= 0 and j0 >= 0
    assert s[0] == pytest.approx(frame[0] + i0 * fpp, rel=1e-9, abs=tol)
    assert s[1] == pytest.approx(frame[1] + j0 * fpp, rel=1e-9, abs=tol)


@settings(max_examples=600, deadline=None)
@given(u=EXTENT, aspect=SHALLOW, x0=ORIGIN, y0=ORIGIN,
       inset=st.floats(min_value=0.0, max_value=0.45,
                       allow_nan=False, allow_infinity=False),
       scale=SCALE, dpi=DPI, fit=FIT)
def test_the_crop_pixel_rect_matches_its_snapped_rectangle(u, aspect, x0, y0,
                                                           inset, scale, dpi, fit):
    """EXACTNESS: "unlike the shipped aspect-ratio prediction that figure is
    EXACT, because both are whole pixel counts of the same snapped rectangle".

    So the snapped rectangle's extent must be its pixel count times fpp on
    both axes -- no residue. A prediction taken through a float aspect ratio
    would not satisfy this."""
    v = u * aspect
    frame = (x0, y0, x0 + u, y0 + v)
    crop = (x0 + inset * u, y0 + inset * v,
            x0 + (1.0 - inset) * u, y0 + (1.0 - inset) * v)
    g = frame_export_geometry(frame, crop, scale, dpi, fit_direction=fit)
    s = g["crop_snapped_uv"]
    fpp = g["achieved_fpp_ft"]
    px_u, px_v = g["crop_px"]
    assert px_u >= 1 and px_v >= 1
    assert (s[2] - s[0]) == pytest.approx(px_u * fpp, rel=1e-9)
    assert (s[3] - s[1]) == pytest.approx(px_v * fpp, rel=1e-9)
