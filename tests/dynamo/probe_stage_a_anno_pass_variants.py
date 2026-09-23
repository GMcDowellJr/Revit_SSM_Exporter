"""Stage A ANNOTATION-PASS variant probe for Revit 2025 / Dynamo 3.3 CPython3.

Inputs:
    IN[0] = target view
    IN[1] = output directory
    IN[2] = optional variant selection ("all", or a comma-separated subset)
    IN[3] = optional export dpi (default 150)

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
A PROBE. It runs candidate fixes for the annotation pass beside the current
pass on the same view, writes what each produced, and stops. It changes no
production default, it concludes nothing about which fix is right, and it
emits no pass/fail on the fixes themselves: Greg's read of the output is the
gate. The only PASS/FAIL this probe makes is about ITSELF -- whether each
variant ran and whether the view was put back -- because a variant that
silently failed to restore the document is not evidence, it is damage.

ROUND 2 (REVISED): THE CAPTURE DOES NOT MODIFY THE CROP
------------------------------------------------------
Round 1 measured that the shipped pass sets view.CropBox to frame B, which is
wider than the authored crop -- and datum extents clip to the crop, so every
capture taken so far LENGTHENED level and grid lines, walked their heads
outward and pulled in content from beyond the authored crop. So the
candidates no longer move the crop, and registration is MEASURED rather than
asserted:

V0  v0_control -- the current annotation pass, unchanged. Still sets the crop
    to B: it is the control for exactly the behaviour being removed.
V7  v7_no_crop -- membership white suppression (the model membership set
    goes white per element; nothing hidden), and production's
    color_id_buffer_anno_crop_mode = "authored_else_crop_a": on a view whose
    crop is ACTIVE, no CropBox write and no CropBoxActive write, in either
    direction; on a crop-INACTIVE view, crop A (the model pass's own) is
    applied and restored -- round 3b showed the untouched alternative exports
    the whole view at 2.9 px/ft.
V8  v8_no_crop_fiducials -- V7 plus
      F1  CropBoxVisible on for BOTH passes, so the crop boundary draws at
          the crop's own UV bounds (read from view.CropBox, never changed).
          V8 therefore takes its OWN model capture with the boundary
          visible; the shared model pass stays the shipped one.
      F2  a fiducial pair: two MODEL elements painted reserved colours
          instead of white, at known UV. Works with no crop at all, which F1
          does not.
      SmoothEdges off.

RETIRED: v0_offsets0 (falsified), v1-v3 (category suppression), v4/v5
(superseded by v7/v8: they still widened the crop to B), v6 (DROPPED: its
expanded frame B' widened the crop further still, which is the defect).
The B' code went with it.

PRODUCTION SURFACE THIS USES
----------------------------
The annotation pass itself is PRODUCTION's
``export_annotation_color_id_buffer_view`` -- called, never reimplemented.
Three probe-only switches, read with ``getattr`` off the cfg object, each
defaulting to the shipped behaviour and each absent from ``Config``:

    color_id_buffer_anno_model_suppression = "external"   (V7, V8)
    color_id_buffer_anno_smooth_edges_off = True           (V8)
    color_id_buffer_anno_crop_mode = "authored_else_crop_a" (V7-V10)

CropBoxVisible (F1) and the fiducial paint (F2) are NOT production switches:
a caller can set both from outside the pass's transaction, so this module
does, and restores and reads back both itself.

RESTORE
-------
Per variant, all of: a ``TransactionGroup`` rolled back in ``finally``;
explicit restore steps inside it; and a READ-BACK of every mutated property
against the pre-variant snapshot, recorded TWICE -- once after the explicit
restore and before the rollback (the real measurement), once after the
rollback (the safety net). Any read-back mismatch fails that view's capture
and is named in the sidecar. A mismatch on the FIRST view stops the run.

UNCONFIRMED API CLAIMS
----------------------
Marked ``UNCONFIRMED`` at every site and collected into the sidecar's
``unconfirmed_api_claims``. Nothing in this file was observed on a Revit
host in the session that wrote it. Every one of them is resolved by
reflection at runtime and recorded three-valued -- an absent property is
"unavailable" with a reason, never a zero and never a False.
"""

from __future__ import print_function

import hashlib
import json
import os
import re
import sys
import time
import traceback


_PROBE_CONTRACT = None


def _probe_contract():
    """Import the shared contract after the repository path is bootstrapped."""
    global _PROBE_CONTRACT
    if _PROBE_CONTRACT is None:
        try:
            import tests.dynamo.stage_a_probe_contract as contract
        except ImportError:
            import stage_a_probe_contract as contract
        _PROBE_CONTRACT = contract
    return _PROBE_CONTRACT


PROBE_NAME = "stage_a_anno_pass_variants"
PROBE_VERSION = "2026-09-23.3"

V0 = "v0_control"
V7 = "v7_no_crop"
V8 = "v8_no_crop_fiducials"
# Round 3. V9 is V8 plus registration marks drawn by the probe in BOTH passes;
# V10 is V7 without the category/subcategory white layer (element overrides
# and link filters only), so its residue and cost say what that layer buys.
V9 = "v9_registration_marks"
V10 = "v10_element_only_white"

SUPPORTED_VARIANTS = (V0, V7, V8, V9, V10)

# WHAT EACH VARIANT CHANGES, as membership sets rather than an if/elif chain per
# property. A new variant is a row here, and a variant missing from every set is
# visibly a control rather than silently a no-op.
WHITE_MEMBERSHIP_VARIANTS = frozenset((V7, V8, V9, V10))
UNTOUCHED_CROP_VARIANTS = frozenset((V7, V8, V9, V10))
# What those variants ask production for (see variant_plan).
CANDIDATE_CROP_MODE = "authored_else_crop_a"
SMOOTH_EDGES_OFF_VARIANTS = frozenset((V8, V9))
CROP_BOX_VISIBLE_VARIANTS = frozenset((V8, V9))
FIDUCIAL_VARIANTS = frozenset((V8, V9))
REGISTRATION_MARK_VARIANTS = frozenset((V9,))
# Mechanism 3 (category and subcategory white overrides) is OFF here. Round 2
# `.4` measured it at over 99 % of the suppression's cost (~390 writes at
# 54-67 ms each, against ~0.01 ms per element override), with nothing yet
# showing what it catches that the element overrides miss.
NO_CATEGORY_LAYER_VARIANTS = frozenset((V10,))

# VARIANTS THAT ARE GONE, and why. Named rather than deleted silently, because
# a reader comparing an earlier combined report against this one needs to know
# these were retired on evidence and not lost -- and so the registry REFUSES a
# campaign still asking for one instead of running nothing.
RETIRED_VARIANTS = {
    "v0_offsets0": "falsified in round 1: byte-identical to v0_control on both views",
    "v1_white_filter": "superseded (category-based suppression cannot separate "
                       "drafting lines from model lines; both are OST_Lines)",
    "v2_white_filter_smooth_edges_off": "superseded, as v1_white_filter",
    "v3_white_filter_smooth_edges_off_expanded_frame": "superseded, as "
                                                       "v1_white_filter",
    "v4_white_membership": "superseded by v7_no_crop: it still set the crop to "
                           "frame B, which lengthens datums and pulls in content "
                           "from beyond the authored crop",
    "v5_white_membership_smooth_edges_off": "superseded by v8_no_crop_fiducials, "
                                            "as v4_white_membership",
    "v6_white_membership_expanded_frame": "DROPPED: its expanded frame B' widened "
                                          "the crop further still, which is the "
                                          "defect round 2 removes. The variant "
                                          "and the B' code are deleted, not left "
                                          "un-runnable",
}

DEFAULT_EXPORT_DPI = 150.0
# Reading GetElementOverrides for every model element in a large view is
# thousands of API calls, so the scan is bounded and says so. A capped scan is
# recorded as capped with both counts -- never as a total.
# Raised from 5000 after round 1: the elevation has 6437 candidates and the scan
# was CAPPED, so "0 authored overrides" was a prefix rather than an answer. 8000
# clears both round-1 views outright.
DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX = 8000
# Model elements whose bbox is resolved when choosing the F2 fiducial pair.
# Bounded and recorded the same way; a capped pool is a smaller choice, not a
# wrong one, and the record says it was capped.
FIDUCIAL_CANDIDATE_SCAN_MAX = 8000

# F2's two RESERVED colours. Every one has a channel of 251, which is prime and
# so is not a multiple of any palette step from 2 to 8: build_palette() snaps
# every colour onto a step lattice, so these can collide with a palette colour
# only at step 1 (a view of millions of annotations). colour_on_lattice() says
# which, per capture, and the probe records it rather than assuming it.
FIDUCIAL_COLOURS = ((251, 11, 139), (11, 139, 251))

# REGISTRATION MARKS (V9) and the membership white suppression's element and
# link mechanisms LIVE IN PRODUCTION: vop_interwoven/stage_a_registration.py,
# promoted there after round 3 measured them. This module calls them through
# _reg(), lazily, because in Dynamo the repo is only importable once
# run_probe() has set the path -- a top-level import would fail on paste. One
# implementation, so the probe measures exactly what production runs.


def _reg():
    import vop_interwoven.stage_a_registration as registration
    return registration


# UNCONFIRMED. ViewCropRegionShapeManager's four annotation-crop offset
# properties, believed to exist on Revit 2025 under these names. Resolved by
# reflection; an absent name makes the offsets record "unavailable" rather than
# recording a 0 that was never read. READ-ONLY as of round 2 -- see
# annotation_crop_offsets().
ANNOTATION_CROP_OFFSET_PROPERTIES = (
    "LeftAnnotationCropOffset",
    "RightAnnotationCropOffset",
    "TopAnnotationCropOffset",
    "BottomAnnotationCropOffset",
)

# ======================================================================
# PURE HELPERS -- no Revit, no Dynamo, unit-tested in
# tests/dynamo/test_probe_stage_a_anno_pass_variants.py
# ======================================================================

def select_variants(selection="all"):
    """The requested variant subset, or every variant."""
    return _probe_contract().select_named(
        selection, SUPPORTED_VARIANTS, "annotation-pass variant(s)")


def variant_plan(variant):
    """What this variant changes, as plain values.

    One place, so the probe's records, the production switches it sets and
    the PROBE md's table cannot drift into three different answers.
    """
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError("Unknown variant {0!r}. Supported: {1}".format(
            variant, list(SUPPORTED_VARIANTS)))
    return {
        "variant": variant,
        "white_membership": variant in WHITE_MEMBERSHIP_VARIANTS,
        "smooth_edges_off": variant in SMOOTH_EDGES_OFF_VARIANTS,
        # Production's color_id_buffer_anno_crop_mode. "frame_b" is the shipped
        # crop-to-B behaviour. The candidates use "authored_else_crop_a": the
        # view's crop left as found where it is ACTIVE (no CropBox or
        # CropBoxActive write), crop A applied where it is not (round 3b: an
        # untouched crop-inactive plan exported its whole extent at 2.9 px/ft).
        "crop_mode": (CANDIDATE_CROP_MODE if variant in UNTOUCHED_CROP_VARIANTS
                      else "frame_b"),
        # F1. Set by THIS module, from outside the pass, and restored by it.
        "crop_box_visible": variant in CROP_BOX_VISIBLE_VARIANTS,
        # F2. Painted by THIS module, from outside the pass.
        "fiducials": variant in FIDUCIAL_VARIANTS,
        # F3 (round 3). Detail lines drawn by THIS module at known UV, in BOTH
        # passes; the model pass is told to leave OST_Lines visible for them.
        "registration_marks": variant in REGISTRATION_MARK_VARIANTS,
        # Mechanism 3 of the white suppression. Off only where the variant
        # exists to measure its absence.
        "category_layer": variant not in NO_CATEGORY_LAYER_VARIANTS,
        # F1 needs the boundary in BOTH passes, and the shared model pass is the
        # shipped one with the boundary hidden -- so a CropBoxVisible variant
        # takes its own model capture into its own directory. So does a marks
        # variant: the shared model pass hides OST_Lines.
        "own_model_pass": (variant in CROP_BOX_VISIBLE_VARIANTS
                           or variant in REGISTRATION_MARK_VARIANTS),
        # Which production suppression mode this variant asks for. A variant
        # that suppresses by membership must ALSO tell the annotation pass not
        # to hide model categories and not to disable filters, or it would
        # measure the two suppressions on top of each other.
        #
        # "external" covers membership-based suppression unchanged, and a second
        # mode name was deliberately NOT added: that mode's contract is "the
        # CALLER has already suppressed model content by some other means; do
        # not hide, do not disable filters", and nothing in it is specific to
        # how the caller did it. Two names for one behaviour is the "computed in
        # two places" shape, and the unknown-mode refusal still guards typos.
        "model_suppression": (
            "external" if variant in WHITE_MEMBERSHIP_VARIANTS
            else "hide_categories"),
    }


def variant_measurement_check(plan, annotation_metadata, probe_state=None):
    """Did the variant actually get what it ASKED FOR?

    PURE. A TIFF existing proves an export happened; it does not prove the
    export measured the variant. Every mutation can decline without raising:

      * ``applied_smooth_edges`` comes back ``"read_failed"`` or
        ``"unchanged (failed)"`` when the ViewDisplayModel read or write fails.
      * ``model_suppression_mode`` is what production says it did; a white
        variant that came back ``"hide_categories"`` measured V0's suppression.
      * ``crop_mode`` is what production says it did with the crop; an
        untouched variant that came back ``"frame_b"`` moved the crop, which is
        the one thing it exists not to do.
      * ``probe_state`` carries what THIS module applied from outside the pass:
        ``crop_box_visible`` (read back True while the pass ran) and
        ``fiducials`` (how many were painted). A V8 whose boundary was not on,
        or whose fiducials did not paint, has no F1 or F2 to measure.

    Returns ``{"measured": bool, "unmet": [...], "checked": [...]}``.
    """
    metadata = annotation_metadata or {}
    state = probe_state or {}
    unmet = []
    checked = []
    if plan.get("smooth_edges_off"):
        checked.append("applied_smooth_edges is False")
        applied = metadata.get("applied_smooth_edges")
        if applied is not False:
            unmet.append({
                "requested": "SmoothEdges off",
                "production_reported": applied,
                "why_it_matters": "anti-aliasing was NOT confirmed off, so this "
                                  "capture measures the same behaviour as the "
                                  "variant without it",
            })
    expected_mode = plan.get("model_suppression")
    if expected_mode is not None:
        checked.append("model_suppression_mode == {0!r}".format(expected_mode))
        reported = metadata.get("model_suppression_mode")
        if reported != expected_mode:
            unmet.append({
                "requested": "model suppression mode {0!r}".format(expected_mode),
                "production_reported": reported,
                "why_it_matters": (
                    "the annotation pass hid model categories and disabled the "
                    "view's filters, so this capture measures V0's suppression "
                    "rather than the variant's"
                    if expected_mode == "external" else
                    "the annotation pass did not apply its own suppression"),
            })
    expected_crop = plan.get("crop_mode")
    if expected_crop is not None:
        checked.append("crop_mode == {0!r}".format(expected_crop))
        reported_crop = metadata.get("crop_mode")
        if reported_crop != expected_crop:
            unmet.append({
                "requested": "crop mode {0!r}".format(expected_crop),
                "production_reported": reported_crop,
                "why_it_matters": (
                    "the annotation pass did not run the candidate crop mode, "
                    "so it may have written a crop this variant exists not to "
                    "write"
                    if expected_crop != "frame_b" else
                    "the annotation pass did not apply frame B, so this is not "
                    "the shipped control"),
            })
    if plan.get("crop_box_visible"):
        checked.append("CropBoxVisible read back True while the pass ran")
        visible = (state.get("crop_box_visible") or {})
        if visible.get("during_capture") is not True:
            unmet.append({
                "requested": "CropBoxVisible on (F1)",
                "production_reported": visible.get("during_capture"),
                "why_it_matters": "the crop boundary was not confirmed visible, so "
                                  "the capture has no F1 fiducial to measure",
            })
    if plan.get("fiducials"):
        checked.append("both F2 fiducials painted")
        painted = (state.get("fiducials") or {}).get("painted_count")
        if painted != len(FIDUCIAL_COLOURS):
            unmet.append({
                "requested": "{0} fiducials painted (F2)".format(
                    len(FIDUCIAL_COLOURS)),
                "production_reported": painted,
                "why_it_matters": "without both fiducials F2 has no pair to fit, "
                                  "and on a crop-less view nothing else registers "
                                  "the capture",
            })
    if plan.get("registration_marks"):
        checked.append("every registration mark created, OST_Lines visible in the "
                       "view, and the own model pass left OST_Lines visible")
        marks = state.get("registration_marks") or {}
        expected = marks.get("expected_count")
        if not expected or marks.get("created_count") != expected:
            unmet.append({
                "requested": "{0} registration marks drawn".format(expected),
                "production_reported": marks.get("created_count"),
                "why_it_matters": "a missing tick leaves an axis with fewer points "
                                  "than the fit's residual needs: {0}".format(
                                      marks.get("reason")),
            })
        if marks.get("lines_category_hidden_in_view") is not False:
            unmet.append({
                "requested": "OST_Lines visible in the view",
                "production_reported": marks.get("lines_category_hidden_in_view"),
                "why_it_matters": "the marks are detail lines; a view that hides "
                                  "Lines (or whose state could not be read) may "
                                  "draw none of them",
            })
        if state.get("own_model_lines_visible") is not True:
            unmet.append({
                "requested": "own model pass with OST_Lines visible",
                "production_reported": state.get("own_model_lines_visible"),
                "why_it_matters": "the model pass hides OST_Lines by default, so "
                                  "the marks would be in the annotation capture "
                                  "only and could not register the two passes "
                                  "against each other",
            })
    return {"measured": not unmet, "unmet": unmet, "checked": checked}


def variant_conclusion(document_safe, exceptions, tiff_path, measurement,
                       capture_success=None, capture_faults=None):
    """One variant's conclusion. PURE, and extracted because of a mutation.

    This lived inline in ``_run_variant`` as an if/elif chain over fields that
    are all plain values by the time it runs. Mutating the measurement branch
    there to ``elif False:`` left the whole suite green -- because the tests
    bound ``variant_measurement_check`` and nothing bound the CALL SITE that
    consumes it. That is the "extracting the formula does not bind the
    arguments production passes" corollary in CLAUDE.md, and the answer is the
    same one it gives: make the call site exercisable and exercise it.

    The order is causal, most disqualifying first:

      FAIL             the document is not as the variant found it. Stops the run.
      ERRORED          something raised; the document is safe.
      INCONCLUSIVE     no TIFF was produced.
      CAPTURE_FAILED   PRODUCTION declared the capture invalid -- success=False
                       with faults such as annotation_frame_not_applied, a
                       lattice or dimension mismatch, or an unverified restore.
                       These arise AFTER any switch was applied, so
                       variant_measurement_check cannot see them: it inspects
                       the two switches and nothing else. Discarding
                       production's own verdict here is how a capture it called
                       invalid came back RAN.
      DID_NOT_MEASURE  a real TIFF and a safe document, but production did not
                       apply what this variant asked for -- so it is not
                       evidence about its candidate.
      RAN              a capture that measured what it is named after.

    ``capture_success`` is production's ``success`` flag. ``None`` means the
    annotation pass returned nothing to read it from, which is not the same as
    True and is not treated as it: a capture whose validity is unknown is not a
    capture that passed.
    """
    if not document_safe:
        return "FAIL"
    if exceptions:
        return "ERRORED"
    if not tiff_path:
        return "INCONCLUSIVE"
    # PRODUCTION'S OWN VERDICT, before this module's. It knows things this
    # module cannot: whether frame B was actually applied as the crop, whether
    # the export landed on the shared lattice, whether its own restore verified.
    if capture_success is not True or capture_faults:
        return "CAPTURE_FAILED"
    if not (measurement or {}).get("measured"):
        return "DID_NOT_MEASURE"
    return "RAN"


def rect_from_corners(corners):
    """``(xmin, ymin, xmax, ymax)`` from a corner list, or None.

    Accepts both shapes this repository produces: ``project_bbox_corners_uv``'s
    four ``[u, v]`` pairs, and a bare 4-tuple. Returns None -- never a
    degenerate rectangle and never a silent zero -- for anything it cannot
    read, so a caller has to decide what an unreadable extent means rather
    than being handed one that looks measured.
    """
    if corners is None:
        return None
    try:
        if (len(corners) == 4
                and all(isinstance(c, (int, float)) for c in corners)):
            xs = (float(corners[0]), float(corners[2]))
            ys = (float(corners[1]), float(corners[3]))
            return (min(xs), min(ys), max(xs), max(ys))
        us = [float(c[0]) for c in corners]
        vs = [float(c[1]) for c in corners]
    except (TypeError, ValueError, IndexError):
        return None
    if not us or not vs:
        return None
    return (min(us), min(vs), max(us), max(vs))


# ---- F2: the fiducial pair -------------------------------------------------

# A fiducial smaller than this, at the capture's feet-per-pixel, is a blob too
# small to fit an extent to; larger than this fraction of the reference rect
# on either axis and it is a landmark, not a point.
FIDUCIAL_MIN_PX = 6.0
FIDUCIAL_MAX_FRACTION = 0.05
# Kept this far inside the reference rectangle, as a fraction of its size, so
# a fiducial is never the element the crop clips.
FIDUCIAL_INSET_FRACTION = 0.02


def colour_on_lattice(rgb, step):
    """PURE. Could ``build_palette(.., step)`` have produced ``rgb``?

    Every palette colour is snapped onto multiples of ``step`` on all three
    channels, so a colour with ANY channel off that lattice cannot collide
    with one. That is the whole reservation argument for FIDUCIAL_COLOURS,
    stated as a function so it is checked per capture rather than assumed.
    """
    step = int(step)
    if step <= 0:
        raise ValueError("step must be positive, got {0!r}".format(step))
    return all(int(channel) % step == 0 for channel in rgb)


def fiducial_colour_record(step, assigned_colours):
    """PURE. Per fiducial colour: can it collide with this capture's palette,
    and did it? ``assigned_colours`` is the annotation sidecar's
    ``color_assignment_map`` values. ``collides`` is the fact; ``on_lattice``
    is why it could."""
    assigned = set(tuple(int(c) for c in rgb) for rgb in (assigned_colours or []))
    out = []
    for rgb in FIDUCIAL_COLOURS:
        out.append({
            "rgb": list(rgb),
            "on_palette_lattice": (None if step is None
                                   else colour_on_lattice(rgb, step)),
            "collides_with_assigned_colour": tuple(rgb) in assigned,
        })
    return {"palette_step": step, "colours": out,
            "any_collision": any(entry["collides_with_assigned_colour"]
                                 for entry in out)}


def _rect_centre(rect):
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def _separated_pair_at(points, threshold):
    """PURE. A pair whose centres are at least ``threshold`` apart on BOTH
    axes, or None. ``points`` is ``[(u, v, id, candidate)]`` sorted by u.

    One sweep: for each point q, every p with ``p.u <= q.u - threshold`` is far
    enough on u, and among those the extreme v (max or min) is the only one
    worth testing on v. Deterministic: the first q in u order that works, and
    the lowest-id extreme.
    """
    best_hi = None   # the p with the largest v among those far enough on u
    best_lo = None   # ... and the smallest v
    j = 0
    for q in points:
        while j < len(points) and points[j][0] <= q[0] - threshold:
            p = points[j]
            if best_hi is None or (p[1], -p[2]) > (best_hi[1], -best_hi[2]):
                best_hi = p
            if best_lo is None or (-p[1], -p[2]) > (-best_lo[1], -best_lo[2]):
                best_lo = p
            j += 1
        if best_hi is not None and best_hi[1] >= q[1] + threshold:
            return (best_hi, q)
        if best_lo is not None and best_lo[1] <= q[1] - threshold:
            return (best_lo, q)
    return None


def _max_min_separation_pair(candidates, iterations=60):
    """PURE. The pair maximising ``min(|du|, |dv|)`` between rect centres.

    Exact to ``2**-iterations`` of the largest spread: "some pair is separated
    by >= t on both axes" is monotone in t, so t is bisected and each test is
    one O(n) sweep (_separated_pair_at) -- rather than the O(n^2) pair search
    a view of thousands of model elements cannot afford, or a top-k heuristic
    that would be a claim of optimality nothing asserted. Returns the two
    candidates, or None when no pair is separated on both axes at all.
    """
    points = sorted(
        ((_rect_centre(c["rect"])[0], _rect_centre(c["rect"])[1], int(c["id"]), c)
         for c in candidates), key=lambda p: (p[0], p[2]))
    if len(points) < 2:
        return None
    us = [p[0] for p in points]
    vs = [p[1] for p in points]
    hi = min(max(us) - min(us), max(vs) - min(vs))
    if hi <= 0.0:
        return None
    lo = 0.0
    found = None
    # Strictly positive separation on both axes first, so a pair on one row is
    # never returned as "separated by 0".
    tiny = hi * 1.0e-12
    found = _separated_pair_at(points, tiny)
    if found is None:
        return None
    lo = tiny
    for _ in range(int(iterations)):
        mid = (lo + hi) / 2.0
        pair = _separated_pair_at(points, mid)
        if pair is not None:
            lo, found = mid, pair
        else:
            hi = mid
    return (found[0][3], found[1][3])


def choose_fiducial_pair(candidates, reference_uv, fpp_ft,
                         min_px=FIDUCIAL_MIN_PX,
                         max_fraction=FIDUCIAL_MAX_FRACTION,
                         inset_fraction=FIDUCIAL_INSET_FRACTION):
    """PURE. Two model elements to paint as the F2 fiducial pair.

    ``candidates`` is ``[{"id": int, "rect": (u0, v0, u1, v1), ...}, ...]`` in
    absolute view UV. ``reference_uv`` is the rectangle they must sit inside:
    the authored crop when it is active, else the model pass's rendered crop.

    A fiducial is kept only when it is
      * inside the reference, inset by ``inset_fraction`` -- never the element
        the crop clips;
      * at least ``min_px`` on both axes at ``fpp_ft`` -- a blob big enough to
        fit an extent to;
      * at most ``max_fraction`` of the reference on both axes -- a point, not
        a landmark.

    THE PAIR maximises ``min(|du|, |dv|)`` between the two centres. Both axes,
    not the distance: a pair on one row determines nothing about v, which is
    the case a scale fit silently survives by reporting whatever the noise
    says. EXACT, not a heuristic: see _max_min_separation_pair.

    Returns a record whose ``state`` is "value" with ``pair`` (two candidates)
    only when a pair separated on BOTH axes exists; otherwise "unavailable"
    with the reason and the rejection counts -- never a degenerate pair.
    """
    x0, y0, x1, y1 = (float(v) for v in reference_uv)
    width, height = x1 - x0, y1 - y0
    if width <= 0.0 or height <= 0.0:
        return {"state": "unavailable",
                "reason": "reference rectangle is degenerate: {0}".format(
                    reference_uv), "rejections": {}}
    fpp = float(fpp_ft)
    if not fpp > 0.0:
        return {"state": "unavailable",
                "reason": "feet-per-pixel must be positive, got {0!r}".format(fpp_ft),
                "rejections": {}}
    ix0, iy0 = x0 + inset_fraction * width, y0 + inset_fraction * height
    ix1, iy1 = x1 - inset_fraction * width, y1 - inset_fraction * height
    rejections = {"no_rect": 0, "outside_reference": 0, "too_small": 0,
                  "too_large": 0}
    kept = []
    for candidate in candidates or []:
        rect = candidate.get("rect")
        if rect is None:
            rejections["no_rect"] += 1
            continue
        u0, v0, u1, v1 = (float(v) for v in rect)
        if u0 < ix0 or v0 < iy0 or u1 > ix1 or v1 > iy1:
            rejections["outside_reference"] += 1
            continue
        if (u1 - u0) / fpp < min_px or (v1 - v0) / fpp < min_px:
            rejections["too_small"] += 1
            continue
        if (u1 - u0) > max_fraction * width or (v1 - v0) > max_fraction * height:
            rejections["too_large"] += 1
            continue
        kept.append(candidate)

    best = _max_min_separation_pair(kept)
    record = {"kept_count": len(kept),
              "candidate_count": len(candidates or []),
              "rejections": rejections, "reference_uv": [x0, y0, x1, y1],
              "fpp_ft": fpp,
              "criteria": {"min_px": min_px, "max_fraction": max_fraction,
                           "inset_fraction": inset_fraction}}
    if best is None:
        record["state"] = "unavailable"
        record["reason"] = (
            "no pair of candidates is separated on BOTH axes ({0} kept of {1})".format(
                len(kept), len(candidates or [])))
        return record
    first, second = best
    cu1, cv1 = _rect_centre(first["rect"])
    cu2, cv2 = _rect_centre(second["rect"])
    record["state"] = "value"
    record["pair"] = [first, second]
    record["separation_u_ft"] = abs(cu1 - cu2)
    record["separation_v_ft"] = abs(cv1 - cv2)
    record["min_separation_ft"] = min(record["separation_u_ft"],
                                      record["separation_v_ft"])
    record["separation_fraction"] = [record["separation_u_ft"] / width,
                                     record["separation_v_ft"] / height]
    return record


# ---- F1: the crop region's own shape --------------------------------------

def registration_mark_segments(*args, **kwargs):
    """PURE; production's layout (vop_interwoven.stage_a_registration)."""
    return _reg().registration_mark_segments(*args, **kwargs)


def crop_loop_record(loops_uv, tolerance=1.0e-6):
    """PURE. The crop region's curve loops, described without assuming four
    straight edges.

    ``loops_uv`` is ``[[(u, v), ...], ...]``: each loop's vertices in order
    (every curve's start point), in absolute view UV. A NON-RECTANGULAR crop
    draws its SHAPE, so the decoder must not assume a rectangle -- which is why
    this reports every edge with its orientation, and the distinct u levels of
    the vertical edges and v levels of the horizontal ones. Those levels are
    what a decoder matches detected lines against; an oblique edge is named,
    and matches nothing.
    """
    loops = [[(float(u), float(v)) for u, v in loop] for loop in (loops_uv or [])]
    edges = []
    u_levels, v_levels = [], []
    all_u, all_v = [], []
    for loop_index, loop in enumerate(loops):
        for index, (u_a, v_a) in enumerate(loop):
            u_b, v_b = loop[(index + 1) % len(loop)]
            all_u.append(u_a)
            all_v.append(v_a)
            if abs(u_a - u_b) <= tolerance and abs(v_a - v_b) > tolerance:
                orientation = "vertical"
                u_levels.append(u_a)
            elif abs(v_a - v_b) <= tolerance and abs(u_a - u_b) > tolerance:
                orientation = "horizontal"
                v_levels.append(v_a)
            elif abs(u_a - u_b) <= tolerance and abs(v_a - v_b) <= tolerance:
                orientation = "degenerate"
            else:
                orientation = "oblique"
            edges.append({"loop": loop_index, "from": [u_a, v_a], "to": [u_b, v_b],
                          "orientation": orientation,
                          "length_ft": ((u_b - u_a) ** 2 + (v_b - v_a) ** 2) ** 0.5})

    def _distinct(values):
        out = []
        for value in sorted(values):
            if not out or abs(value - out[-1]) > tolerance:
                out.append(value)
        return out

    record = {
        "loop_count": len(loops),
        "edge_count": len(edges),
        "edges": edges,
        "oblique_edge_count": sum(1 for e in edges if e["orientation"] == "oblique"),
        "distinct_u_levels": _distinct(u_levels),
        "distinct_v_levels": _distinct(v_levels),
        "bounds_uv": ([min(all_u), min(all_v), max(all_u), max(all_v)]
                      if all_u else None),
    }
    record["is_rectangle"] = bool(
        len(loops) == 1 and len(edges) == 4
        and all(e["orientation"] in ("vertical", "horizontal") for e in edges)
        and len(record["distinct_u_levels"]) == 2
        and len(record["distinct_v_levels"]) == 2)
    return record


def per_side_delta(inner_uv, outer_uv):
    """PURE. How far ``outer`` reaches past ``inner`` on each side, in feet.

    Positive means ``outer`` is larger on that side. Used for "how far did V0
    widen the crop" (inner = authored crop, outer = frame B) and "how far does
    the model pass's crop A differ from the authored crop".
    """
    ix0, iy0, ix1, iy1 = (float(v) for v in inner_uv)
    ox0, oy0, ox1, oy1 = (float(v) for v in outer_uv)
    return {"left": ix0 - ox0, "bottom": iy0 - oy0,
            "right": ox1 - ix1, "top": oy1 - iy1}


def suppression_cost_record(suppression_ms, element_override_count,
                            model_pass_ms, model_member_count):
    """PURE. Membership white suppression's cost against the model pass's.

    The brief's stop-and-raise asks whether per-element white overrides cost
    MATERIALLY more than the model pass's own paint. Production does not time
    its paint separately -- only the whole model pass, export included -- so
    the ratio here is against the WHOLE pass and is therefore a LOWER bound on
    the ratio against the paint alone. Reported as numbers; the threshold is
    Greg's.
    """
    out = {"suppression_ms": suppression_ms,
           "element_override_count": element_override_count,
           "model_pass_total_ms": model_pass_ms,
           "model_member_count": model_member_count,
           "note": "model_pass_total_ms includes the export; production does not "
                   "time its paint separately, so the ratio is a LOWER bound on "
                   "suppression-vs-paint"}
    try:
        out["ratio_to_model_pass_total"] = (
            float(suppression_ms) / float(model_pass_ms)
            if model_pass_ms else None)
    except (TypeError, ValueError):
        out["ratio_to_model_pass_total"] = None
    try:
        out["suppression_ms_per_element"] = (
            float(suppression_ms) / float(element_override_count)
            if element_override_count else None)
    except (TypeError, ValueError):
        out["suppression_ms_per_element"] = None
    return out

def _sha256(path):
    """The file's SHA-256, or a reason. Never a None that reads as "same"."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return {"state": "value", "value": digest.hexdigest()}
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "{0}: {1}".format(type(ex).__name__, ex)}


def _value(value):
    return {"state": "value", "value": value}


def _unavailable(reason):
    return {"state": "unavailable", "reason": str(reason)}


def _safe_name(value):
    text = str(value or "view")
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", text).strip().replace(" ", "_") or "view"


def _exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex),
            "traceback": traceback.format_exc()}


def _diff(before, after, path=""):
    """Every leaf on which two snapshots disagree."""
    out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = (path + "." + str(key)) if path else str(key)
            if key not in before or key not in after:
                out.append({"path": child, "before": before.get(key),
                            "after": after.get(key)})
            else:
                out.extend(_diff(before[key], after[key], child))
    elif isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        if len(before) != len(after):
            out.append({"path": path, "before": list(before), "after": list(after)})
        else:
            for index, (b, a) in enumerate(zip(before, after)):
                out.extend(_diff(b, a, "{0}[{1}]".format(path, index)))
    elif isinstance(before, float) or isinstance(after, float):
        # Revit hands back doubles for crop-box corners and crop offsets. An
        # exact != on them would report a restore failure on the last bit of a
        # value that round-tripped through the API, which is a false alarm that
        # would stop the run. 1e-9 ft is 3e-7 inch: far below anything this
        # probe can act on and far above float noise.
        try:
            if abs(float(before) - float(after)) > 1.0e-9:
                out.append({"path": path, "before": before, "after": after})
        except (TypeError, ValueError):
            if before != after:
                out.append({"path": path, "before": before, "after": after})
    elif before != after:
        out.append({"path": path, "before": before, "after": after})
    return out


# ======================================================================
# REPOSITORY BOOTSTRAP -- same shape as probe_stage_a_external_sources.py
# ======================================================================

def _candidate_repo_roots(output_dir):
    roots = []
    mod = sys.modules.get("vop_interwoven")
    path = getattr(mod, "__file__", None)
    if path:
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    for env in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        if os.environ.get(env):
            roots.append(os.environ[env])
    for seed in (output_dir, os.getcwd()):
        if not seed:
            continue
        cur = os.path.abspath(str(seed))
        while cur and cur != os.path.dirname(cur):
            roots.append(cur)
            cur = os.path.dirname(cur)
        roots.append(cur)
    home = os.path.expanduser("~")
    roots.extend([
        os.path.join(home, "Documents", "Revit_SSM_Exporter"),
        os.path.join(home, "Documents", "GitHub", "Revit_SSM_Exporter"),
        os.path.join(home, "source", "repos", "Revit_SSM_Exporter"),
        os.path.join(home, "Revit_SSM_Exporter"),
        "/workspace/Revit_SSM_Exporter",
    ])
    seen = []
    for root in roots:
        if root and root not in seen:
            seen.append(root)
    return seen


def _ensure_contract_import_path(output_dir):
    checked = []
    for root in _candidate_repo_roots(output_dir):
        checked.append(root)
        if os.path.isfile(os.path.join(root, "tests", "dynamo",
                                       "stage_a_probe_contract.py")):
            for candidate in (root, os.path.join(root, "tests", "dynamo")):
                if candidate not in sys.path:
                    sys.path.insert(0, candidate)
            return root
    raise RuntimeError(
        "Could not locate tests/dynamo/stage_a_probe_contract.py. Set "
        "REVIT_SSM_EXPORTER_ROOT or place the output directory inside the "
        "checkout. Checked: {0}".format(checked))


def _ensure_repo_import_path(output_dir):
    """Put the checkout on ``sys.path``, or raise saying what was tried and why.

    Every rejected candidate carries its own REASON rather than being a silent
    ``continue``: "checked 14 roots and none worked" is not actionable, and the
    one that held ``vop_interwoven/`` but failed to import it is the
    interesting case -- a partial checkout, a stale ``.pyc``, a syntax error in
    the package -- which a bare list of paths hides completely.
    """
    checked = []
    try:
        import vop_interwoven  # noqa: F401
        return None
    except ImportError as ex:
        checked.append({"root": "<already on sys.path>",
                        "reason": "{0}: {1}".format(type(ex).__name__, ex)})
    for root in _candidate_repo_roots(output_dir):
        if not os.path.isdir(os.path.join(root, "vop_interwoven")):
            checked.append({"root": root, "reason": "no vop_interwoven/ directory"})
            continue
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import vop_interwoven as _vop_check  # noqa: F401
            return root
        except ImportError as ex:
            checked.append({"root": root,
                            "reason": "vop_interwoven/ present but importing it "
                                      "raised {0}: {1}".format(
                                          type(ex).__name__, ex)})
    raise RuntimeError(
        "Could not locate the Revit_SSM_Exporter repo root for vop_interwoven "
        "imports. Set REVIT_SSM_EXPORTER_ROOT. Checked: {0}".format(checked))


def _ensure_revit_api_reference():
    import clr
    for assembly in ("RevitAPI", "RevitServices"):
        try:
            clr.AddReference(assembly)
        except Exception as ex:
            # RECORDED, not discarded: on a host where the reference is
            # already loaded this is expected and harmless, and on one where
            # it genuinely cannot be loaded every Revit call below fails with
            # a less informative error. Printing is the only channel available
            # this early -- no diag object exists yet.
            print("[probe:{0}] clr.AddReference({1!r}) raised {2}: {3}".format(
                PROBE_NAME, assembly, type(ex).__name__, ex))


def _doc():
    _ensure_revit_api_reference()
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    _ensure_revit_api_reference()
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


def _unwrap(value):
    return getattr(value, "InternalElement", value)


def _element_id_int(value):
    """An ElementId as an int, via the shared contract's reader.

    DELEGATED, not re-implemented. ``stage_a_probe_contract.element_id_value``
    already carries the Revit 2025 rule -- read the 64-bit ``Value`` first,
    because ``IntegerValue`` is deprecated and its getter can RAISE for an id
    outside the int32 range -- along with the docstring explaining why. A local
    copy here would be a second answer to the same question in the same
    repository, which is the first defect class CLAUDE.md names.
    """
    return _probe_contract().element_id_value(value)


# ======================================================================
# ANNOTATION CROP OFFSETS -- UNCONFIRMED API, resolved by reflection
# ======================================================================

_MISSING = object()


def annotation_crop_offsets(view):
    """The view's four annotation-crop offsets, three-valued.

    UNCONFIRMED: ``View.GetCropRegionShapeManager()`` returning a
    ``ViewCropRegionShapeManager`` that exposes
    ``Left/Right/Top/BottomAnnotationCropOffset``. Nothing in this repository
    has observed those names on a Revit host.

    A missing manager or a missing property makes the WHOLE record
    unavailable with the reason. It never reports 0: a 0 that was never read is
    indistinguishable from a view whose offsets really are 0.

    READ-ONLY as of round 2. The v0_offsets0 variant that used to WRITE these was
    falsified -- it produced byte-identical TIFFs to v0_control on both round-1
    views -- so nothing sets them any more. They are still snapshotted, because
    "the capture did not change them" is a restore obligation and because the
    offsets remain a recorded property of the view a reader may want.
    """
    try:
        manager = view.GetCropRegionShapeManager()
    except Exception as ex:
        return _unavailable(
            "View.GetCropRegionShapeManager() raised {0}: {1} (UNCONFIRMED API)".format(
                type(ex).__name__, ex))
    if manager is None:
        return _unavailable(
            "View.GetCropRegionShapeManager() returned None (UNCONFIRMED API)")
    values = {}
    for name in ANNOTATION_CROP_OFFSET_PROPERTIES:
        try:
            raw = getattr(manager, name, _MISSING)
        except Exception as ex:
            return _unavailable(
                "reading {0} raised {1}: {2} (UNCONFIRMED API)".format(
                    name, type(ex).__name__, ex))
        if raw is _MISSING:
            return _unavailable(
                "ViewCropRegionShapeManager has no {0} on this Revit host; the "
                "annotation crop offsets cannot be read, so they are not "
                "reported as 0 (UNCONFIRMED API)".format(name))
        try:
            values[name] = float(raw)
        except (TypeError, ValueError) as ex:
            return _unavailable(
                "{0} is not a number ({1!r}): {2} (UNCONFIRMED API)".format(
                    name, raw, ex))
    return _value(values)


def annotation_crop_active(view):
    """``View.AnnotationCropActive``, three-valued.

    Read for the record only. F1's competing hypothesis is that the offsets
    contribute EVEN THOUGH the annotation crop is inactive, so which it is
    has to be in the sidecar beside the offsets themselves.
    """
    try:
        raw = getattr(view, "AnnotationCropActive", _MISSING)
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))
    if raw is _MISSING:
        return _unavailable("View has no AnnotationCropActive on this Revit host")
    return _value(bool(raw))


# ======================================================================
# THE WHITE OVERRIDE (V7, V8)
# ======================================================================

# Every ``OverrideGraphicSettings`` member the white override needs, as a flat
# list so a MISSING one is nameable rather than an AttributeError thrown from
# somewhere inside a loop. UNCONFIRMED as a set on this Revit host, which is
# exactly why it is preflighted.
def missing_override_setters(ogs_like, setters=None):
    return _reg().missing_override_setters(ogs_like, setters)


def white_override_capability_record(ogs_like, solid_pattern_id,
                                     solid_pattern_error=None):
    return _reg().white_override_capability_record(
        ogs_like, solid_pattern_id, solid_pattern_error=solid_pattern_error)


def white_override_capability(doc):
    return _reg().white_override_capability(doc)


WHITE = (255, 255, 255)


def _white_override_settings(doc, colour=WHITE):
    """Production's ``flat_colour_override``: projection + cut lines and all
    four patterns in ``colour``. Also paints the F2 fiducials."""
    return _reg().flat_colour_override(doc, colour=colour)


# ======================================================================
# MEMBERSHIP-BASED WHITE SUPPRESSION (V7, V8)
# ======================================================================
#
# MEMBERSHIP, NOT CATEGORY, DECIDES WHAT IS SUPPRESSED.
#
# "Annotation" is content visible only in the view it is placed in. A drafting
# line and a detail item are annotation even though their categories carry a
# Model label; a model line in the SAME OST_Lines category is not. No
# category-level mechanism can separate them -- which is why round 1's
# category-filter variants are retired -- so the suppression runs off the same
# split the painting already uses: split_stage_a_pass_membership, OwnerViewId
# plus datum categories.
#
#   every element in the MODEL set     -> element-level WHITE override
#   every element in the ANNOTATION set -> left alone for the pass to paint
#   nothing is hidden, in either set
#
# REVIT'S PRECEDENCE IS WHAT MAKES THE SHARED-CATEGORY CASE WORK, and it is
# worth stating because the design depends on it: Element > Filter > Category.
# So a white CATEGORY override on OST_Lines suppresses model lines while the
# annotation pass's own per-element palette paint still wins for the drafting
# lines in that same category. The separation is done by precedence, not by
# picking categories apart.
#
# FOUR MECHANISMS, because element overrides do not reach everything:
#
#   1. element override      every MODEL-membership element, including DWG
#                            ImportInstances (element-overridable, confirmed
#                            2026-09-21).
#   2. link category filter  LINKED RVT content, where a per-element override is
#                            impossible (ledger M1). Reuses PRODUCTION's
#                            _apply_link_category_filters with WHITE instead of
#                            a palette colour -- not a second link mechanism.
#   3. category + SUBcategory
#                            an element override does not govern the element's
#                            SUBCATEGORY linework. Greg observed roof fascia
#                            surviving a white filter and going white only when
#                            the subcategory was overridden alongside the
#                            parent, so cat.SubCategories is walked and each is
#                            overridden. Applied only where the current override
#                            is BLANK (see below).
#   4. nothing               whatever none of the above reached is a NAMED LIST,
#                            never an absence.


def _category_override_is_blank(view, cat_id):
    """``(state, blank, reason)`` for one category's override, read back.

    Mirrors production's ``_override_is_cleared`` for categories, checking
    exactly the members the white override sets.

    WHY IT MATTERS: mechanism 3 applies a category override ONLY where the
    current one is blank. Overwriting an authored category override would
    destroy graphics this probe cannot put back -- restoring a captured
    ``OverrideGraphicSettings`` across a transaction boundary is the pattern
    this module's history warns against, and it is how the curtain-panel bug
    happened. Writing only over blank means the explicit restore is a blank
    write, which IS the original state and is verifiable.
    """
    try:
        ogs = view.GetCategoryOverrides(cat_id)
    except Exception as ex:
        return ("unavailable", None,
                "GetCategoryOverrides raised {0}: {1}".format(
                    type(ex).__name__, ex))
    if ogs is None:
        return ("unavailable", None, "GetCategoryOverrides returned None")
    residue = []
    unreadable = []
    for name, reader in (
            ("projection_line_color", lambda o: getattr(o, "ProjectionLineColor", None)),
            ("cut_line_color", lambda o: getattr(o, "CutLineColor", None)),
            ("surface_foreground_pattern_color",
             lambda o: getattr(o, "SurfaceForegroundPatternColor", None)),
            ("cut_foreground_pattern_color",
             lambda o: getattr(o, "CutForegroundPatternColor", None))):
        try:
            value = reader(ogs)
            if value is not None and bool(getattr(value, "IsValid", False)):
                residue.append(name)
        except Exception as ex:
            unreadable.append("{0} ({1}: {2})".format(name, type(ex).__name__, ex))
    try:
        if bool(getattr(ogs, "Halftone", False)):
            residue.append("halftone")
    except Exception as ex:
        unreadable.append("halftone ({0}: {1})".format(type(ex).__name__, ex))
    if unreadable:
        return ("unavailable", None, "; ".join(unreadable))
    return ("value", not residue, None if not residue else ", ".join(residue))


def _subcategories_of(cat):
    """``(subcategories, error)``. A category whose SubCategories cannot be read
    yields an empty list AND a reason, never a silent zero -- an unwalked
    subcategory is exactly the roof-fascia case."""
    try:
        subs = cat.SubCategories
    except Exception as ex:
        return ([], "{0}: {1}".format(type(ex).__name__, ex))
    if subs is None:
        return ([], "SubCategories is None")
    try:
        return (list(subs), None)
    except Exception as ex:
        return ([], "SubCategories not iterable: {0}: {1}".format(
            type(ex).__name__, ex))


def apply_membership_white_suppression(doc, view, view_id, model_elements,
                                       link_categories=None, diag=None,
                                       subcategories=True, exclude_ids=None):
    """Suppress the MODEL membership set to white. Four mechanisms, one record.

    Must be called inside an open Transaction. Returns the record described at
    the top of this section: what each mechanism reached, and a NAMED list of
    what nothing reached.

    ``model_elements`` is the model half of split_stage_a_pass_membership -- the
    same split the painting uses, which is the whole point.

    ``link_categories`` is ``[(category, ...), ...]`` for linked RVT content, or
    None to skip mechanism 2.

    ``exclude_ids`` are model members deliberately left UNTOUCHED -- by
    mechanism 1 and as a source of categories for mechanism 3 -- and named in
    ``excluded_element_ids``. V8 uses it for the view's own crop-region element:
    round 2 found the boundary in V0 and in neither V7 nor V8, which is what
    whitening that element would do (see crop_region_elements).
    """
    from Autodesk.Revit.DB import ElementId

    # PER-MECHANISM TIMING, because round 2 measured the whole suppression at
    # 1.08-1.14x the ENTIRE model pass and one number cannot say which part to
    # make cheaper. Wall-clock ms per phase, plus the API call counts that
    # drive each, so "slow" can be told apart from "many".
    timings = {"build_override_ms": 0.0, "element_overrides_ms": 0.0,
               "link_category_filters_ms": 0.0,
               "category_collect_ms": 0.0, "subcategory_walk_ms": 0.0,
               "category_override_reads_ms": 0.0,
               "category_override_writes_ms": 0.0}
    counts = {"element_override_writes": 0, "category_override_reads": 0,
              "category_override_writes": 0, "subcategory_lists_read": 0}
    # ---- 1 + 2: PRODUCTION's element and link mechanisms ----------------
    # vop_interwoven.stage_a_registration.white_membership_suppression -- the
    # same function a registered production capture runs, so V7-V10 measure
    # it rather than a copy of it.
    _t = time.time()
    ogs = _white_override_settings(doc)
    timings["build_override_ms"] = (time.time() - _t) * 1000.0
    excluded = set(int(v) for v in (exclude_ids or ()))
    model_elements = [
        elem for elem in (model_elements or [])
        if _element_id_int(getattr(elem, "Id", None)) not in excluded]
    base = _reg().white_membership_suppression(
        doc, view, view_id, model_elements, link_categories=link_categories,
        diag=diag, exclude_ids=excluded, ogs=ogs)
    timings["element_overrides_ms"] = base["timings_ms"]["element_overrides_ms"]
    timings["link_category_filters_ms"] = base["timings_ms"][
        "link_category_filters_ms"]
    counts["element_override_writes"] = base["api_call_counts"][
        "element_override_writes"]
    record = {
        "excluded_element_ids": base["excluded_element_ids"],
        "mechanisms": (["element_override", "link_category_filter"]
                       + (["category_and_subcategory_override"] if subcategories
                          else [])),
        # False under V10: mechanism 3 was not RUN, which is not the same fact
        # as "ran and reached nothing". category_overrides stays empty.
        "category_layer": bool(subcategories),
        "element_overrides": base["element_overrides"],
        "dwg_import_instance_ids": base["dwg_import_instance_ids"],
        "link_category_filters": base["link_category_filters"],
        "category_overrides": {
            "parents_applied": [], "subcategories_applied": [],
            "skipped_authored": [], "refused": [], "failed": [],
            "subcategory_read_errors": []},
        # Deliberately excluded ids are NOT unreached: they were chosen, and
        # excluded_element_ids above names them.
        # NEVER AN ABSENCE. Every element, category and link category no
        # mechanism reached, with the reason. This list is the honest answer to
        # "is the annotation TIFF annotation on white"; an empty one is a claim
        # and a populated one is a bound on it.
        "unreached": list(base["unreached"]),
    }
    for entry in base.get("classification_errors") or []:
        record["category_overrides"]["subcategory_read_errors"].append(entry)

    # ---- 3: category and SUBcategory white overrides ------------------
    #
    # An element override does not govern the element's subcategory linework, so
    # this is a COMPLEMENT to mechanism 1 rather than a fallback for it. Revit's
    # Element > Category precedence means the annotation pass's own paint still
    # wins for annotation members in a shared category.
    if subcategories:
        _t_mech = time.time()
        model_cat_ids = {}
        for elem in model_elements or []:
            try:
                cat = elem.Category
                if cat is None:
                    continue
                cat_id_int = _element_id_int(cat.Id)
                if cat_id_int is not None:
                    model_cat_ids[cat_id_int] = cat
            except Exception as ex:
                # RECORDED, because the comment that used to sit here said
                # "nothing is silently dropped" and this branch dropped it. An
                # element whose Category will not read contributes no category to
                # mechanism 3, so its SUBCATEGORY linework goes unsuppressed --
                # which is the roof-fascia case, arriving by a different route.
                # The element override above may well have succeeded; that does
                # not cover its subcategories.
                record["unreached"].append({
                    "kind": "category_of_element",
                    "id": _element_id_int(getattr(elem, "Id", None)),
                    "reason": "the element's Category could not be read ({0}: {1}), "
                              "so no category or subcategory override was applied "
                              "for it".format(type(ex).__name__, ex)})
        timings["category_collect_ms"] = (time.time() - _t_mech) * 1000.0
        for cat_id_int, cat in sorted(model_cat_ids.items()):
            _t = time.time()
            subs, sub_error = _subcategories_of(cat)
            timings["subcategory_walk_ms"] += (time.time() - _t) * 1000.0
            counts["subcategory_lists_read"] += 1
            if sub_error is not None:
                record["category_overrides"]["subcategory_read_errors"].append(
                    {"category_id": cat_id_int,
                     "name": str(getattr(cat, "Name", "")), "reason": sub_error})
                record["unreached"].append({
                    "kind": "subcategories_of", "id": cat_id_int,
                    "reason": "SubCategories could not be read ({0}); any "
                              "subcategory linework in this category is "
                              "unsuppressed".format(sub_error)})
            for target, is_sub in ([(cat, False)] + [(sub, True) for sub in subs]):
                target_id_int = _element_id_int(getattr(target, "Id", None))
                if target_id_int is None:
                    continue
                target_id = ElementId(int(target_id_int))
                name = str(getattr(target, "Name", ""))
                _t = time.time()
                state, blank, reason = _category_override_is_blank(view, target_id)
                timings["category_override_reads_ms"] += (time.time() - _t) * 1000.0
                counts["category_override_reads"] += 1
                if state != "value":
                    record["category_overrides"]["failed"].append(
                        {"category_id": target_id_int, "name": name,
                         "is_subcategory": is_sub,
                         "error": "override unreadable: {0}".format(reason)})
                    record["unreached"].append({
                        "kind": "subcategory" if is_sub else "category",
                        "id": target_id_int,
                        "reason": "current override unreadable, so it was not "
                                  "overwritten: {0}".format(reason)})
                    continue
                if not blank:
                    # AUTHORED. Left alone -- overwriting it would destroy
                    # graphics this probe cannot put back.
                    record["category_overrides"]["skipped_authored"].append(
                        {"category_id": target_id_int, "name": name,
                         "is_subcategory": is_sub, "residue": reason})
                    record["unreached"].append({
                        "kind": "subcategory" if is_sub else "category",
                        "id": target_id_int,
                        "reason": "carries an AUTHORED override ({0}); not "
                                  "overwritten, so whatever it draws is "
                                  "unsuppressed".format(reason)})
                    continue
                _t = time.time()
                counts["category_override_writes"] += 1
                try:
                    view.SetCategoryOverrides(target_id, ogs)
                    bucket = ("subcategories_applied" if is_sub
                              else "parents_applied")
                    record["category_overrides"][bucket].append(
                        {"category_id": target_id_int, "name": name})
                except Exception as ex:
                    from vop_interwoven.color_id_buffer import (
                        _category_override_refused,
                    )
                    if _category_override_refused(ex):
                        record["category_overrides"]["refused"].append(
                            {"category_id": target_id_int, "name": name,
                             "is_subcategory": is_sub})
                        record["unreached"].append({
                            "kind": "subcategory" if is_sub else "category",
                            "id": target_id_int,
                            "reason": "Revit refuses category overrides on it, so "
                                      "whatever it draws is unsuppressed"})
                    else:
                        record["category_overrides"]["failed"].append(
                            {"category_id": target_id_int, "name": name,
                             "is_subcategory": is_sub,
                             "error": "{0}: {1}".format(type(ex).__name__, ex)})
                        record["unreached"].append({
                            "kind": "subcategory" if is_sub else "category",
                            "id": target_id_int,
                            "reason": "SetCategoryOverrides raised {0}: {1}".format(
                                type(ex).__name__, ex)})
                timings["category_override_writes_ms"] += (time.time() - _t) * 1000.0

    record["timings_ms"] = dict((k, round(v, 3)) for k, v in timings.items())
    record["api_call_counts"] = counts
    record["unreached_count"] = len(record["unreached"])
    record["element_override_count"] = record["element_overrides"]["applied"]
    record["category_override_count"] = (
        len(record["category_overrides"]["parents_applied"])
        + len(record["category_overrides"]["subcategories_applied"]))
    return record


def filter_element_exists(doc, filter_id_int):
    """Does the ParameterFilterElement still exist in the PROJECT?

    ``None`` when there was no filter to check or the question could not be
    answered -- which ``restore_readback_verdict`` treats as ``unverified``, not
    as deleted. "Could not tell" is not "it is gone".
    """
    if filter_id_int is None:
        return None
    try:
        from Autodesk.Revit.DB import ElementId
        return doc.GetElement(ElementId(int(filter_id_int))) is not None
    except Exception as ex:
        print("[probe:{0}] could not determine whether filter {1} still exists: "
              "{2}: {3}".format(PROBE_NAME, filter_id_int, type(ex).__name__, ex))
        return None


def link_visibility_report(doc, view):
    """Link instances whose graphics are NOT "By Host View".

    The white filter does not reach a link displayed "By Linked View" or
    "Custom": that link keeps drawing its own model content into the
    annotation capture. Recorded per instance so a V7 image that is not clean
    has somewhere to point.

    UNCONFIRMED: ``View.GetLinkOverrides(ElementId)`` returning a
    ``RevitLinkGraphicsSettings`` with ``LinkVisibilityType``.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
    out = {"state": "value", "instances": [], "not_by_host_view_count": 0}
    try:
        instances = FilteredElementCollector(doc, view.Id).OfClass(
            RevitLinkInstance).ToElements()
    except Exception as ex:
        return _unavailable(
            "could not collect RevitLinkInstances in the view: {0}: {1}".format(
                type(ex).__name__, ex))
    for instance in instances:
        instance_id = _element_id_int(getattr(instance, "Id", None))
        entry = {"link_instance_id": instance_id,
                 "name": str(getattr(instance, "Name", "") or "")}
        try:
            settings = view.GetLinkOverrides(instance.Id)
            visibility = getattr(settings, "LinkVisibilityType", _MISSING)
            if visibility is _MISSING:
                entry["link_visibility_type"] = _unavailable(
                    "RevitLinkGraphicsSettings has no LinkVisibilityType on this "
                    "Revit host (UNCONFIRMED API)")
            else:
                text = str(visibility)
                entry["link_visibility_type"] = _value(text)
                if text != "ByHostView":
                    out["not_by_host_view_count"] += 1
        except Exception as ex:
            entry["link_visibility_type"] = _unavailable(
                "View.GetLinkOverrides raised {0}: {1} (UNCONFIRMED API)".format(
                    type(ex).__name__, ex))
        out["instances"].append(entry)
    out["instance_count"] = len(out["instances"])
    return out


def authored_override_scan(view, element_ids, scan_max):
    """How many elements already carry an AUTHORED per-element override.

    Those OUTRANK a view filter in Revit's graphics precedence, so every one
    of them is a model element the white filter will not whiten. Bounded, and
    a bounded scan says so: ``capped`` with both counts, never a total that
    was really a prefix.

    Reuses production's ``_override_is_cleared`` rather than a second reading
    of what "blank" means.
    """
    from vop_interwoven.color_id_buffer import _override_is_cleared
    total = len(element_ids)
    limit = max(0, int(scan_max))
    scanned_ids = list(element_ids[:limit])
    out = {"state": "value", "candidate_count": total,
           "scanned_count": len(scanned_ids),
           "capped": bool(total > limit),
           "scan_max": limit,
           "authored_count": 0, "unreadable_count": 0,
           "authored_element_ids": []}
    for eid in scanned_ids:
        state, cleared, _reason = _override_is_cleared(view, int(eid))
        if state != "value":
            out["unreadable_count"] += 1
        elif not cleared:
            out["authored_count"] += 1
            if len(out["authored_element_ids"]) < 200:
                out["authored_element_ids"].append(int(eid))
    return out


def element_override_readback(view, model_context, report):
    """After the rollback: do the model members' element overrides read as
    they did before the variant? The obligation the explicit blank-write loop
    used to discharge, now judged on what the ROLLBACK left.

    The same bounded scan as the pre-variant one (``authored_override_scan``
    over the same ids, same cap), compared field for field: a white override
    the rollback left behind reads as an extra AUTHORED override. Three-valued,
    plus ``not_applicable`` for a variant that wrote no element override.
    """
    pre = report.get("pre_state") or {}
    wrote = (pre.get("white_membership_suppression") is not None
             or bool((pre.get("fiducials") or {}).get("painted")))
    if not wrote:
        return {"status": "not_applicable",
                "reason": "this variant wrote no element override"}
    before = model_context.get("authored_model_overrides") or {}
    if before.get("state") != "value":
        return {"status": "unverified",
                "reason": "no pre-variant override scan to compare against: "
                          "{0}".format(before.get("reason"))}
    ids = []
    for elem in model_context.get("model_members") or []:
        value = _element_id_int(getattr(elem, "Id", None))
        if value is not None:
            ids.append(value)
    t0 = time.time()
    after = authored_override_scan(
        view, ids, before.get("scan_max", DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX))
    keys = ("scanned_count", "authored_count", "unreadable_count",
            "authored_element_ids")
    differences = dict((k, {"before": before.get(k), "after": after.get(k)})
                       for k in keys if before.get(k) != after.get(k))
    if differences:
        status = "not_restored"
    elif after.get("unreadable_count"):
        status = "unverified"
    else:
        status = "restored"
    return {"status": status, "differences": differences,
            "after": dict((k, after.get(k)) for k in keys + ("capped",)),
            "elapsed_ms": round((time.time() - t0) * 1000.0, 3)}


# ======================================================================
# F1 / F2 -- REVIT SIDE (V8)
# ======================================================================

def crop_box_visible_record(view):
    """``View.CropBoxVisible``, three-valued. A NEW restore obligation under
    V8: this module turns it on and must put it back."""
    try:
        raw = getattr(view, "CropBoxVisible", _MISSING)
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))
    if raw is _MISSING:
        return _unavailable("View has no CropBoxVisible on this Revit host")
    return _value(bool(raw))


def _xyz_tuple(point):
    return (float(point.X), float(point.Y), float(point.Z))


def crop_shape_loops_world(view):
    """The crop region's curve loops as world-space vertex lists, three-valued.

    UNCONFIRMED: ``View.GetCropRegionShapeManager().GetCropShape()`` returning
    an iterable of CurveLoops. Read-only; this module never sets a crop shape.
    Each loop is its curves' start points in order, plus each curve's type
    name, so an ARC in a crop shape is named rather than flattened to a chord.
    """
    try:
        manager = view.GetCropRegionShapeManager()
    except Exception as ex:
        return _unavailable("GetCropRegionShapeManager raised {0}: {1} "
                            "(UNCONFIRMED API)".format(type(ex).__name__, ex))
    if manager is None:
        return _unavailable("GetCropRegionShapeManager returned None")
    getter = getattr(manager, "GetCropShape", None)
    if getter is None:
        return _unavailable("ViewCropRegionShapeManager has no GetCropShape on "
                            "this Revit host (UNCONFIRMED API)")
    try:
        loops = list(getter())
    except Exception as ex:
        return _unavailable("GetCropShape raised {0}: {1}".format(
            type(ex).__name__, ex))
    out = []
    curve_types = set()
    try:
        for loop in loops:
            vertices = []
            for curve in loop:
                curve_types.add(type(curve).__name__)
                vertices.append(_xyz_tuple(curve.GetEndPoint(0)))
            out.append(vertices)
    except Exception as ex:
        return _unavailable("reading the crop shape's curves raised {0}: {1}".format(
            type(ex).__name__, ex))
    shape_set = getattr(manager, "ShapeSet", _MISSING)
    return _value({"loops_world": out, "curve_types": sorted(curve_types),
                   "shape_set": (None if shape_set is _MISSING else bool(shape_set))})


def crop_region_record(view, view_basis, diag=None):
    """Where the crop boundary DRAWS, read from the view WITHOUT changing it.

    The F1 fiducial's known position: ``view.CropBox`` projected into view UV
    through the same basis the capture uses (transform-aware, via production's
    ``project_bbox_corners_uv``), plus the crop's SHAPE -- a non-rectangular
    crop draws its shape, and ``crop_loop_record`` says what that shape is so
    the decoder never assumes four straight edges.
    """
    from vop_interwoven.revit.collection import project_bbox_corners_uv
    record = {
        "crop_box_active": None,
        "crop_box_visible": crop_box_visible_record(view),
        "crop_box_uv": None,
        "shape": None,
    }
    try:
        record["crop_box_active"] = _value(bool(view.CropBoxActive))
    except Exception as ex:
        record["crop_box_active"] = _unavailable("{0}: {1}".format(
            type(ex).__name__, ex))
    try:
        box = view.CropBox
    except Exception as ex:
        box = None
        record["crop_box_uv"] = _unavailable("view.CropBox raised {0}: {1}".format(
            type(ex).__name__, ex))
    if box is not None and view_basis is not None:
        rect = rect_from_corners(project_bbox_corners_uv(box, view_basis, diag=diag))
        record["crop_box_uv"] = (_value(list(rect)) if rect is not None else
                                 _unavailable("the CropBox did not project into UV"))
    elif box is not None:
        record["crop_box_uv"] = _unavailable("no view basis to project it through")
    elif record["crop_box_uv"] is None:
        record["crop_box_uv"] = _unavailable("the view has no CropBox")

    loops = crop_shape_loops_world(view)
    if loops.get("state") != "value":
        record["shape"] = loops
    elif view_basis is None:
        record["shape"] = _unavailable("no view basis to project the shape through")
    else:
        loops_uv = []
        for loop in loops["value"]["loops_world"]:
            loops_uv.append([tuple(view_basis.transform_to_view_uv(p)) for p in loop])
        shape = crop_loop_record(loops_uv)
        shape["curve_types"] = loops["value"]["curve_types"]
        shape["shape_set"] = loops["value"]["shape_set"]
        record["shape"] = _value(shape)
    return record


def crop_region_elements(doc, view, model_members):
    """The view's own crop-region element(s) among the MODEL members.

    UNCONFIRMED API, recorded as such: the crop region is drawn by an element
    of category ``OST_Viewers`` that carries the view's own name (the Building
    Coder's documented lookup). Round 2 is the evidence for why this matters:
    the boundary drew in V0 -- whose sidecar says the probe never turned it on,
    so the views already show their crop -- and in NEITHER V7 NOR V8, including
    V8 with CropBoxVisible explicitly on. Membership suppression whitens every
    model member; if this element is one, it whitens F1's own ruler.

    Returns a record: every OST_Viewers model member with its name and owner
    view, and ``crop_element_ids`` -- the ones named like this view. An empty
    list is a finding (the lookup did not hold on this host), not a clean one.
    """
    # BOTH categories, and which one matched is recorded. Round 2's run shows
    # OST_Views (-2000278) elements ARE in the model set -- Revit refused a
    # category override on "Views" on both views -- so the lookup is not
    # allowed to assume the documented OST_Viewers is the only home.
    from Autodesk.Revit.DB import BuiltInCategory
    category_ids = {}
    unresolved = []
    for bic_name in ("OST_Viewers", "OST_Views"):
        bic = getattr(BuiltInCategory, bic_name, None)
        if bic is None:
            unresolved.append(bic_name)
        else:
            category_ids[int(bic)] = bic_name
    if not category_ids:
        return {"state": "unavailable",
                "reason": "neither OST_Viewers nor OST_Views resolved",
                "crop_element_ids": []}
    view_name = str(getattr(view, "Name", "") or "")
    viewers = []
    for elem in model_members or []:
        try:
            cat = elem.Category
            if cat is None or _element_id_int(cat.Id) not in category_ids:
                continue
            viewers.append({
                "id": _element_id_int(elem.Id),
                "category": category_ids[_element_id_int(cat.Id)],
                "name": str(getattr(elem, "Name", "") or ""),
                "owner_view_id": _element_id_int(getattr(elem, "OwnerViewId", None)),
            })
        except Exception as ex:
            viewers.append({"id": _element_id_int(getattr(elem, "Id", None)),
                            "error": "{0}: {1}".format(type(ex).__name__, ex)})
    crop_ids = [v["id"] for v in viewers
                if v.get("name") == view_name and v.get("id") is not None]
    return {"state": "value", "view_name": view_name,
            "viewers_in_model_set": viewers,
            "viewers_in_model_set_count": len(viewers),
            "crop_element_ids": crop_ids,
            "unresolved_categories": unresolved,
            "lookup": "model members of OST_Viewers or OST_Views named like "
                      "the view (UNCONFIRMED API)"}


def collect_fiducial_candidates(view, view_basis, model_members, scan_max,
                                diag=None, view_id=None):
    """Model members with a UV rectangle, bounded, for choose_fiducial_pair.

    ``get_BoundingBox(view)`` and an AABB projection only -- no geometry API.
    Returns ``(candidates, record)``; the record counts every member that
    contributed no rectangle and says whether the scan was capped.
    """
    from vop_interwoven.revit.collection import (
        project_bbox_corners_uv, resolve_element_bbox,
    )
    candidates = []
    record = {"member_count": len(model_members or []), "scan_max": int(scan_max),
              "capped": len(model_members or []) > int(scan_max),
              "no_bbox": 0, "no_projection": 0, "unreadable_id": 0}
    for elem in list(model_members or [])[:int(scan_max)]:
        elem_id = _element_id_int(getattr(elem, "Id", None))
        if elem_id is None:
            record["unreadable_id"] += 1
            continue
        try:
            bbox, source = resolve_element_bbox(
                elem, view=view, diag=diag,
                context={"view_id": view_id, "elem_id": elem_id,
                         "source_type": "HOST"})
        except Exception as ex:
            record["no_bbox"] += 1
            record.setdefault("bbox_errors", []).append(
                {"id": elem_id, "error": "{0}: {1}".format(type(ex).__name__, ex)})
            continue
        if bbox is None:
            record["no_bbox"] += 1
            continue
        rect = rect_from_corners(project_bbox_corners_uv(
            bbox, view_basis, diag=diag, view_id=view_id, elem_id=elem_id))
        if rect is None:
            record["no_projection"] += 1
            continue
        category = None
        try:
            category = str(elem.Category.Name) if elem.Category is not None else None
        except Exception as ex:
            category = "<unreadable: {0}>".format(type(ex).__name__)
        candidates.append({"id": elem_id, "rect": rect, "category": category,
                           "bbox_source": source})
    record["candidate_count"] = len(candidates)
    return (candidates, record)


def paint_fiducials(doc, view, pair):
    """Paint the chosen pair their RESERVED colours. Inside an open Transaction,
    AFTER the white suppression, so these two element overrides replace the
    white ones. Restore is the white suppression's own blank write over every
    model member, which covers both.

    The SAME override the white suppression uses, in the reserved colour:
    projection and cut lines and all four patterns. Anything less leaves the
    white CATEGORY cut override (mechanism 3) winning on a cut element, which is
    what round 2 measured on the plan (see _white_override_settings).
    """
    from Autodesk.Revit.DB import ElementId
    record = {"painted": [], "failed": [], "painted_count": 0,
              "override": "_white_override_settings(colour): projection + cut "
                          "lines, surface + cut patterns"}
    for candidate, rgb in zip(pair or [], FIDUCIAL_COLOURS):
        entry = {"id": int(candidate["id"]), "rgb": list(rgb),
                 "rect_uv": [float(v) for v in candidate["rect"]],
                 "category": candidate.get("category"),
                 "bbox_source": candidate.get("bbox_source")}
        try:
            view.SetElementOverrides(ElementId(int(candidate["id"])),
                                     _white_override_settings(doc, colour=rgb))
            record["painted"].append(entry)
        except Exception as ex:
            entry["error"] = "{0}: {1}".format(type(ex).__name__, ex)
            record["failed"].append(entry)
    record["painted_count"] = len(record["painted"])
    return record


def create_registration_marks(doc, view, view_basis, layout):
    return _reg().create_registration_marks(doc, view, view_basis, layout)


def marks_still_in_project(doc, mark_ids):
    return _reg().marks_still_in_project(doc, mark_ids)


def annotate_sidecar(path, key, payload):
    """Production's: add ``key`` to a sidecar, refusing to overwrite one."""
    return _reg().annotate_sidecar(path, key, payload)


def crop_boundary_sidecar_payload(drawn_at_uv, shape, pass_name):
    """What the sidecar says about the F1 boundary. PURE.

    The boundary is NOT documentation content. It is present in this capture
    because the probe turned it on, and a consumer must subtract it; the
    recovered pixel position is measured from the image by
    ``tools/notes/anno_pass_variant_report.py`` (its --json-out), not here,
    because this module runs inside Revit without reading pixels.
    """
    return {
        "present": True,
        "must_be_subtracted": True,
        "is_documentation_content": False,
        "pass": pass_name,
        "drawn_at_uv": drawn_at_uv,
        "shape": shape,
        "recovered_position": "measured from the pixels by "
                              "tools/notes/anno_pass_variant_report.py --json-out",
        "set_by": PROBE_NAME,
    }


# ======================================================================
# SNAPSHOT / READ-BACK
# ======================================================================

def _crop_box_record(view):
    """CropBox corners and CropBoxActive, three-valued."""
    try:
        box = view.CropBox
        return _value({
            "min": [float(box.Min.X), float(box.Min.Y), float(box.Min.Z)],
            "max": [float(box.Max.X), float(box.Max.Y), float(box.Max.Z)],
            "active": bool(view.CropBoxActive),
        })
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))


def _smooth_edges_record(view):
    """``ViewDisplayModel.SmoothEdges``, three-valued, disposed on the way out."""
    try:
        dm = view.GetViewDisplayModel()
    except Exception as ex:
        return _unavailable("GetViewDisplayModel raised {0}: {1}".format(
            type(ex).__name__, ex))
    try:
        raw = getattr(dm, "SmoothEdges", _MISSING)
        if raw is _MISSING:
            return _unavailable(
                "ViewDisplayModel has no SmoothEdges on this Revit host")
        return _value(bool(raw))
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))
    finally:
        try:
            dm.Dispose()
        except Exception as ex:
            # RECORDED to stdout rather than discarded; a leaked
            # ViewDisplayModel does not invalidate the reading above.
            print("[probe:{0}] ViewDisplayModel.Dispose raised {1}: {2}".format(
                PROBE_NAME, type(ex).__name__, ex))


def _model_category_visibility_record(doc, view):
    """Every MODEL category's hidden state in this view, three-valued.

    The V7/V8 restore contract is that model category visibility is NEVER
    WRITTEN, in either direction. That is only checkable against the whole
    map, so the whole map is what is recorded -- a sampled one would leave the
    categories it did not sample able to change unnoticed.
    """
    from Autodesk.Revit.DB import CategoryType
    try:
        out = {}
        unreadable = []
        for cat in doc.Settings.Categories:
            try:
                if cat.CategoryType != CategoryType.Model:
                    continue
                cat_id = _element_id_int(cat.Id)
                if cat_id is None:
                    continue
                if not view.CanCategoryBeHidden(cat.Id):
                    continue
                out[str(cat_id)] = bool(view.GetCategoryHidden(cat.Id))
            except Exception as ex:
                unreadable.append("{0}: {1}".format(type(ex).__name__, ex))
        return _value({"hidden_by_id": out, "unreadable": unreadable})
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))


def _view_filter_record(view):
    """Filter ids on the view with their enabled/visible state, three-valued."""
    try:
        out = {}
        for fid in view.GetFilters():
            value = _element_id_int(fid)
            out[str(value)] = {
                "enabled": bool(view.GetIsFilterEnabled(fid)),
                "visible": bool(view.GetFilterVisibility(fid)),
            }
        return _value(out)
    except Exception as ex:
        return _unavailable("{0}: {1}".format(type(ex).__name__, ex))


def snapshot_view(doc, view):
    """The properties the restore contract covers, read in one place.

    One function, so the "before", "after explicit restore" and "after
    rollback" readings are the SAME reading of the SAME properties. Three
    independently-written snapshot functions would let the diff pass because
    two of them agreed about a field the third never read.
    """
    return {
        "crop_box": _crop_box_record(view),
        # Round 2 (revised). CropBoxVisible is a NEW restore obligation -- V8
        # turns it on -- and the crop SHAPE is read so "the capture did not
        # touch the crop" is a read-back, not a claim.
        "crop_box_visible": crop_box_visible_record(view),
        "crop_region_shape": crop_shape_loops_world(view),
        "smooth_edges": _smooth_edges_record(view),
        "annotation_crop_offsets": annotation_crop_offsets(view),
        "annotation_crop_active": annotation_crop_active(view),
        "model_category_visibility": _model_category_visibility_record(doc, view),
        "view_filters": _view_filter_record(view),
        "view_template_id": _element_id_int(getattr(view, "ViewTemplateId", None)),
        "display_style": str(getattr(view, "DisplayStyle", None)),
    }


def restore_readback_verdict(before, after, expect_filters_absent=None,
                             filter_elements_still_in_project=None,
                             explicit_step_errors=None):
    """Which restore obligations this pair of snapshots satisfies.

    The five obligations are named INDIVIDUALLY rather than rolled into one
    boolean, because "the restore failed" is not actionable and "the crop box
    came back but the annotation crop offsets did not" is. Each is
    three-valued: a property that could not be READ in either snapshot is
    ``unverified``, which is not ``restored`` -- the same rule production's
    override read-back follows.
    """
    verdict = {}
    for name in ("crop_box", "crop_box_visible", "crop_region_shape",
                 "smooth_edges", "annotation_crop_offsets",
                 "model_category_visibility"):
        b = before.get(name) or {}
        a = after.get(name) or {}
        if b.get("state") != "value" or a.get("state") != "value":
            verdict[name] = {
                "status": "unverified",
                "reason": "before={0} after={1}".format(
                    b.get("reason") or b.get("state"),
                    a.get("reason") or a.get("state")),
            }
            continue
        differences = _diff(b.get("value"), a.get("value"))
        verdict[name] = ({"status": "restored"} if not differences
                         else {"status": "not_restored", "differences": differences})

    # Two plain scalars production also mutates. Cheap, and their absence
    # would let a capture leave the view detached from its template or in
    # FlatColors with every other obligation reading "restored".
    for name in ("view_template_id", "display_style"):
        b_value = before.get(name)
        a_value = after.get(name)
        verdict[name] = ({"status": "restored"} if b_value == a_value
                         else {"status": "not_restored",
                               "before": b_value, "after": a_value})

    b_filters = before.get("view_filters") or {}
    a_filters = after.get("view_filters") or {}
    if b_filters.get("state") != "value" or a_filters.get("state") != "value":
        verdict["view_filters"] = {
            "status": "unverified",
            "reason": "before={0} after={1}".format(
                b_filters.get("reason") or b_filters.get("state"),
                a_filters.get("reason") or a_filters.get("state")),
        }
    else:
        differences = _diff(b_filters.get("value"), a_filters.get("value"))
        verdict["view_filters"] = ({"status": "restored"} if not differences
                                   else {"status": "not_restored",
                                         "differences": differences})

    for filter_id in (expect_filters_absent or []):
        # The probe's OWN filter, checked by id rather than by the diff above.
        # A filter that was deleted from the project but left associated with
        # the view, or vice versa, can produce an identical filter MAP while
        # the element survives -- so the id is asked for directly.
        #
        # TWO QUESTIONS, and the first version of this asked only one. The view
        # membership test alone declares "restored" when RemoveFilter succeeded
        # and doc.Delete FAILED: the id is gone from the view and a project-wide
        # ParameterFilterElement the probe created is still there. That is
        # precisely the case the comment above promised to catch and did not --
        # an identity claimed in prose and never asserted. So the caller also
        # resolves whether the ELEMENT still exists, and both must be clear.
        key = "probe_filter_deleted[{0}]".format(filter_id)
        still_in_project = (filter_elements_still_in_project or {}).get(filter_id)
        if a_filters.get("state") != "value":
            verdict[key] = {
                "status": "unverified",
                "reason": a_filters.get("reason") or "view filters unreadable"}
        elif str(int(filter_id)) in (a_filters.get("value") or {}):
            verdict[key] = {
                "status": "not_restored",
                "reason": "the probe's white filter {0} is still on the view".format(
                    filter_id)}
        elif still_in_project is True:
            verdict[key] = {
                "status": "not_restored",
                "reason": "the probe's white filter {0} was removed from the view "
                          "but the ParameterFilterElement still exists in the "
                          "project; this capture left project-wide "
                          "contamination".format(filter_id)}
        elif still_in_project is None:
            verdict[key] = {
                "status": "unverified",
                "reason": "the filter is off the view, but whether the "
                          "ParameterFilterElement itself was deleted could not be "
                          "determined"}
        else:
            verdict[key] = {"status": "restored"}

    # A RESTORE STEP THAT RAISED IS NOT A RESTORE THAT SUCCEEDED, and this was
    # recorded and then not consulted: a failed doc.Delete landed in
    # explicit_step_errors while document_safe read only the read-back verdicts,
    # so a clean TransactionGroup rollback could still let the variant conclude
    # RAN -- falsely validating the explicit restore the rollback had actually
    # rescued.
    if explicit_step_errors:
        verdict["explicit_restore_steps"] = {
            "status": "not_restored",
            "reason": "{0} explicit restore step(s) raised".format(
                len(explicit_step_errors)),
            "errors": list(explicit_step_errors),
        }
    else:
        verdict["explicit_restore_steps"] = {"status": "restored"}

    statuses = [entry["status"] for entry in verdict.values()]
    verdict["overall"] = ("restored" if all(s == "restored" for s in statuses)
                          else ("not_restored" if "not_restored" in statuses
                                else "unverified"))
    return verdict


# ======================================================================
# CONFIG PER VARIANT
# ======================================================================

def _variant_config(base_output_dir, export_dpi, plan):
    """A Config for one variant, with the three probe-only switches SET.

    The switches are assigned as ATTRIBUTES after construction, not passed as
    constructor kwargs, because they are deliberately not Config parameters:
    they have no production default, no ``to_dict()`` representation and no
    way to reach a production run. Production reads them with ``getattr``.
    """
    from vop_interwoven.config import Config
    cfg = Config(
        debug_dump_path=base_output_dir,
        enable_color_id_buffer_stage_a=True,
        color_id_buffer_annotation_pass=True,
        color_id_buffer_export_dpi=float(export_dpi),
    )
    cfg.color_id_buffer_anno_model_suppression = plan["model_suppression"]
    cfg.color_id_buffer_anno_smooth_edges_off = bool(plan["smooth_edges_off"])
    cfg.color_id_buffer_anno_crop_mode = plan["crop_mode"]
    return cfg


def _model_config(base_output_dir, export_dpi):
    """The MODEL pass's Config: shipped behaviour, neither switch set.

    Runs once per view. Nothing about the variants reaches it, which is the
    property the per-variant model-TIFF hash in the report is there to check.
    """
    from vop_interwoven.config import Config
    return Config(
        debug_dump_path=base_output_dir,
        enable_color_id_buffer_stage_a=True,
        color_id_buffer_annotation_pass=False,
        color_id_buffer_export_dpi=float(export_dpi),
    )


# ======================================================================
# ONE VARIANT
# ======================================================================

_SHARED_GEOMETRY_KEYS = ("frame_snapped_uv", "frame_px", "crop_snapped_uv",
                         "crop_px", "crop_offset_px", "achieved_fpp_ft")


def _own_model_pass(doc, view, model_context, variant_dir, export_dpi,
                    lines_visible=False):
    """A CropBoxVisible variant's own MODEL capture, into ``<variant>/model``.

    F1 needs the boundary in BOTH passes. The shared model pass is the shipped
    one with the boundary hidden, and it has to stay that way -- it is the
    foundation every other variant registers against. So this variant takes
    its own, with the same inputs, and the record says whether its lattice
    matches the shared one. It does not replace the shared geometry: the
    annotation pass is still handed the shared model pass's geom, so V8 and V7
    differ only in what V8 adds.

    Note what the boundary MEANS here: the model pass sets the crop to A (its
    snapped model crop) for its own export and restores it, so in this capture
    the boundary draws at A, not at the authored crop.
    """
    from vop_interwoven.color_id_buffer import export_color_id_buffer_view
    record = {"output_directory": os.path.join(variant_dir, "model"),
              "crop_box_visible_before": crop_box_visible_record(view)}
    try:
        # NO _force_close_dynamo_transaction() here, unlike the other model
        # exports: this runs INSIDE the variant's TransactionGroup, and Dynamo's
        # transaction was already closed before that group started. Forcing a
        # close with the group open is an interaction nothing has observed.
        cfg = _model_config(record["output_directory"], export_dpi)
        if lines_visible:
            # V9's registration marks are detail lines; the shipped model pass
            # hides OST_Lines. Probe-only production switch, read by getattr.
            cfg.color_id_buffer_model_lines_visible = True
        record["lines_visible_requested"] = bool(lines_visible)
        own_geom = {}
        t0 = time.time()
        out = export_color_id_buffer_view(
            doc, view, model_context["elements"], cfg, diag=model_context["diag"],
            raster=model_context["raster"], geometry_out=own_geom)
        record["elapsed_ms"] = round((time.time() - t0) * 1000.0, 3)
        record["success"] = bool(out.get("success"))
        record["failure_reason"] = out.get("failure_reason")
        record["tiff_path"] = out.get("tiff_path")
        record["sidecar_path"] = out.get("sidecar_path")
        # What production SAYS it did with OST_Lines, not what was asked.
        record["model_lines_visible"] = (out.get("metadata") or {}).get(
            "model_lines_visible")
        record["tiff_sha256"] = (_sha256(out["tiff_path"]) if out.get("tiff_path")
                                 else _unavailable("no TIFF path"))
        shared = model_context["geom"]
        differences = {}
        for key in _SHARED_GEOMETRY_KEYS:
            if key not in own_geom:
                differences[key] = {"shared": shared.get(key), "own": None}
                continue
            if _diff(shared.get(key), own_geom.get(key)):
                differences[key] = {"shared": shared.get(key), "own": own_geom.get(key)}
        record["geometry"] = dict((k, own_geom.get(k)) for k in _SHARED_GEOMETRY_KEYS)
        record["geometry_matches_shared"] = not differences
        record["geometry_differences"] = differences
        # Where the boundary draws in THIS capture: crop A, as rendered.
        record["boundary_drawn_at_uv"] = (
            [float(v) for v in own_geom["crop_snapped_uv"]]
            if own_geom.get("crop_snapped_uv") is not None else None)
    except Exception as ex:
        record["success"] = False
        record["error"] = _exception_record("own_model_pass", ex)
    return record


def _annotate_variant_sidecars(plan, anno_out, metadata, model_context, report,
                               probe_state):
    """Write the F1/F2 facts into the sidecars a consumer reads.

    Returns ``{"<sidecar>:<key>": None or reason}`` -- a failed annotation is
    recorded, never silent, because a sidecar without ``probe_crop_boundary``
    reads as a capture without a boundary in it.
    """
    results = {}
    authored = model_context.get("authored_crop") or {}
    crop_uv = (authored.get("crop_box_uv") or {})
    shape = (authored.get("shape") or {})
    if plan["crop_box_visible"]:
        payload = crop_boundary_sidecar_payload(
            crop_uv.get("value") if crop_uv.get("state") == "value" else None,
            shape.get("value") if shape.get("state") == "value" else None,
            "annotation")
        payload["drawn_at"] = ("the view's authored crop, read from view.CropBox "
                               "without changing it (crop_mode 'untouched')")
        payload["crop_box_visible_during_capture"] = (
            probe_state.get("crop_box_visible") or {}).get("during_capture")
        if anno_out.get("sidecar_path"):
            results["annotation:probe_crop_boundary"] = annotate_sidecar(
                anno_out["sidecar_path"], "probe_crop_boundary", payload)
        own = report.get("own_model_pass") or {}
        if own.get("sidecar_path"):
            model_payload = crop_boundary_sidecar_payload(
                own.get("boundary_drawn_at_uv"), None, "model")
            model_payload["drawn_at"] = (
                "crop A -- the model pass sets the crop to its snapped model crop "
                "for its own export, so the boundary draws there, not at the "
                "authored crop")
            model_payload["crop_box_visible_before"] = own.get(
                "crop_box_visible_before")
            results["model:probe_crop_boundary"] = annotate_sidecar(
                own["sidecar_path"], "probe_crop_boundary", model_payload)
    if plan["fiducials"]:
        fiducials = probe_state.get("fiducials") or {}
        colour_check = fiducial_colour_record(
            metadata.get("palette_step"),
            (metadata.get("color_assignment_map") or {}).values())
        report["fiducial_colour_check"] = colour_check
        payload = {
            "present": True,
            "must_be_subtracted": True,
            "is_documentation_content": False,
            "fiducials": fiducials.get("painted", []),
            "failed": fiducials.get("failed", []),
            "colour_check": colour_check,
            "note": "two MODEL elements painted reserved colours instead of white; "
                    "rect_uv is get_BoundingBox(view) projected into view UV",
            "set_by": PROBE_NAME,
        }
        if anno_out.get("sidecar_path"):
            results["annotation:probe_fiducials"] = annotate_sidecar(
                anno_out["sidecar_path"], "probe_fiducials", payload)
    if plan.get("registration_marks"):
        marks = probe_state.get("registration_marks") or {}
        colour_map = metadata.get("color_assignment_map") or {}
        base = {
            "present": bool(marks.get("created")),
            "must_be_subtracted": True,
            "is_documentation_content": False,
            "layout": marks.get("layout"),
            "set_by": PROBE_NAME,
            "note": "detail lines drawn by the probe at KNOWN view UV inside the "
                    "crop and removed by rolling back its TransactionGroup. "
                    "level_uv is the constant coordinate (v for a horizontal "
                    "tick, u for a vertical one); readback_uv is the created "
                    "curve's own endpoints.",
        }
        if anno_out.get("sidecar_path"):
            payload = dict(base)
            payload["pass"] = "annotation"
            payload["marks"] = [
                dict(m, rgb=colour_map.get(str(m.get("id"))))
                for m in marks.get("created", [])]
            payload["colour_source"] = ("this capture's color_assignment_map: the "
                                        "marks are view-owned, so production "
                                        "painted them as annotation members")
            results["annotation:probe_registration_marks"] = annotate_sidecar(
                anno_out["sidecar_path"], "probe_registration_marks", payload)
        own = report.get("own_model_pass") or {}
        if own.get("sidecar_path"):
            payload = dict(base)
            payload["pass"] = "model"
            payload["marks"] = [dict(m, rgb=list(_reg().MARK_COLOUR))
                                for m in marks.get("created", [])]
            payload["colour_source"] = ("MARK_COLOUR, one reserved colour for all "
                                        "eight; each tick is its own connected "
                                        "component")
            payload["model_lines_visible"] = own.get("model_lines_visible")
            results["model:probe_registration_marks"] = annotate_sidecar(
                own["sidecar_path"], "probe_registration_marks", payload)
    return results


def _run_variant(doc, view, variant, model_context, settings):
    """Run one variant end to end, and put the view back.

    Structure, in order, and every step is recorded whether or not it
    succeeded:

      1   snapshot BEFORE
      2   TransactionGroup.Start
      3a  (V8, V9) a committed child Transaction turns CropBoxVisible ON
      3a' (V9) a committed child Transaction draws the registration marks
      3b  (V8, V9) this variant's OWN model capture, boundary visible (and for
          V9 with OST_Lines visible, so the marks draw), into
          ``<variant>/model``. BEFORE the white suppression, deliberately: the
          model pass paints every model element and restores each to a blank
          override, which would wipe white overrides applied before it.
      3c  a committed child Transaction applies the white membership
          suppression (V10: without its category layer), then (V8, V9)
          paints the F2 fiducial pair over it
      4   production's export_annotation_color_id_buffer_view runs, with no
          child transaction open
      5   TransactionGroup.RollBack in ``finally`` -- THE RESTORE
      6   snapshot, the read-back verdict, the marks' absence and the model
          members' element overrides -- all read AFTER the rollback

    ROUND 3: THE ROLLBACK IS THE RESTORE. Until `.4` a committed child
    transaction reversed 3c and 3a explicitly before the rollback, and round 2
    measured that reverse at 33-51 s per variant -- longer than the writes it
    undid. It existed only so a read-back could be taken before the rollback.
    What production would adopt is the rollback itself, so that is what is now
    measured, and read back: every obligation below is judged on the view as
    the rollback left it.

    NOTHING HERE WRITES THE CROP. CropBoxVisible is a display property of the
    crop region, not its extent; the extent is read (crop_region_record) and
    read back (the crop_box and crop_region_shape obligations), never set.
    """
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus

    plan = variant_plan(variant)
    report = {
        "variant": variant,
        "plan": plan,
        "transaction_group": {},
        "pre_state": {},
        "annotation_pass": {},
        "restore": {},
        "model_tiff": {},
        "exceptions": [],
        "skipped": False,
        "conclusion": "INCONCLUSIVE",
    }
    probe_state = {}

    group = None
    started = False
    # The link mechanism may create SEVERAL filters (one per linked category), so
    # the created ids live on the suppression record and the read-back checks
    # every one of them.
    created_filter_ids = []
    snapshot_before = None
    crop_box_visible_before = None

    try:
        _force_close_dynamo_transaction()
        snapshot_before = snapshot_view(doc, view)
        report["snapshot_before"] = snapshot_before

        # ---- variants that cannot run on this view ---------------------
        if plan["white_membership"]:
            capability = model_context["white_override_capability"]
            if capability.get("state") != "value":
                report["skipped"] = True
                report["skip_reason"] = (
                    "the white override cannot be built on this Revit host, so "
                    "this variant would apply overrides that change nothing and "
                    "render an image indistinguishable from V0's: {0}".format(
                        capability.get("reason")))
                report["white_override_capability"] = capability
                report["conclusion"] = "UNAVAILABLE"
                return report
            if not model_context.get("model_members"):
                report["skipped"] = True
                report["skip_reason"] = (
                    "the MODEL membership set is empty, so there is nothing to "
                    "suppress and this variant would render exactly like V0 while "
                    "the record said white suppression was applied")
                report["conclusion"] = "UNAVAILABLE"
                return report

        geom = model_context["geom"]
        report["frame"] = {"frame_source": "model_pass_geom",
                           "frame_snapped_uv": [float(v) for v in
                                                geom["frame_snapped_uv"]],
                           "frame_px": [int(v) for v in geom["frame_px"]],
                           "crop_mode": plan["crop_mode"]}
        variant_dir = os.path.join(settings["probe_dir"], variant)

        group = TransactionGroup(doc, "VOP Stage A anno variant: " + variant)
        status = group.Start()
        started = status == TransactionStatus.Started
        report["transaction_group"]["start_status"] = str(status)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(status))

        # ---- 3a: CropBoxVisible ON (F1) ---------------------------------
        if plan["crop_box_visible"]:
            crop_box_visible_before = crop_box_visible_record(view)
            visible_record = {"before": crop_box_visible_before}
            vis_tx = Transaction(doc, "VOP Stage A anno variant crop visible: " + variant)
            vis_tx.Start()
            try:
                view.CropBoxVisible = True
                commit = vis_tx.Commit()
                visible_record["commit_status"] = str(commit)
                if commit != TransactionStatus.Committed:
                    raise RuntimeError(
                        "CropBoxVisible Transaction.Commit returned {0}".format(commit))
            except Exception as ex:
                visible_record["error"] = "{0}: {1}".format(type(ex).__name__, ex)
                try:
                    vis_tx.RollBack()
                except Exception as inner:
                    report["exceptions"].append(
                        _exception_record("crop_box_visible_rollback", inner))
            visible_record["after_set"] = crop_box_visible_record(view)
            report["pre_state"]["crop_box_visible"] = visible_record
            probe_state["crop_box_visible"] = visible_record

        # ---- 3a': registration marks (V9) ---------------------------------
        if plan["registration_marks"]:
            authored = model_context.get("authored_crop") or {}
            active = (authored.get("crop_box_active") or {})
            crop_uv = (authored.get("crop_box_uv") or {})
            if active.get("value") is True and crop_uv.get("state") == "value":
                reference, reference_source = crop_uv["value"], "authored_crop"
            else:
                reference = geom.get("crop_snapped_uv")
                reference_source = "model_pass_crop_a (no active authored crop)"
            layout = registration_mark_segments(
                reference, geom.get("achieved_fpp_ft"))
            marks_tx = Transaction(doc, "VOP Stage A anno variant marks: " + variant)
            marks_tx.Start()
            try:
                marks = create_registration_marks(
                    doc, view, model_context["raster"].view_basis, layout)
                marks["reference_source"] = reference_source
                commit = marks_tx.Commit()
                marks["commit_status"] = str(commit)
                if commit != TransactionStatus.Committed:
                    raise RuntimeError(
                        "marks Transaction.Commit returned {0}".format(commit))
            except Exception:
                try:
                    marks_tx.RollBack()
                except Exception as ex:
                    report["exceptions"].append(
                        _exception_record("marks_rollback", ex))
                raise
            report["pre_state"]["registration_marks"] = marks
            probe_state["registration_marks"] = marks

        # ---- 3b: this variant's OWN model capture (V8, V9) ---------------
        if plan["own_model_pass"]:
            report["own_model_pass"] = _own_model_pass(
                doc, view, model_context, variant_dir, settings["export_dpi"],
                lines_visible=plan["registration_marks"])
            probe_state["own_model_lines_visible"] = report["own_model_pass"].get(
                "model_lines_visible")

        # ---- 3c: white suppression, then the fiducials -------------------
        pre_tx = Transaction(doc, "VOP Stage A anno variant pre-state: " + variant)
        pre_tx.Start()
        try:
            if plan["white_membership"]:
                t_suppress = time.time()
                # F1's ruler is left alone: the crop-region element is NOT
                # whitened when this variant turns the boundary on.
                exclude_ids = (
                    (model_context.get("crop_region_elements") or {}).get(
                        "crop_element_ids") or []
                    if plan["crop_box_visible"] else [])
                suppression = apply_membership_white_suppression(
                    doc, view, _element_id_int(view.Id),
                    model_context["model_members"],
                    link_categories=model_context.get("link_categories"),
                    diag=model_context["diag"], exclude_ids=exclude_ids,
                    subcategories=plan["category_layer"])
                suppression["elapsed_ms"] = round(
                    (time.time() - t_suppress) * 1000.0, 3)
                report["pre_state"]["white_membership_suppression"] = suppression
                report["pre_state"]["suppression_cost"] = suppression_cost_record(
                    suppression["elapsed_ms"], suppression.get("element_override_count"),
                    model_context.get("model_pass_ms"),
                    len(model_context["model_members"]))
                report["pre_state"]["suppression_cost"]["by_mechanism_ms"] = (
                    suppression.get("timings_ms"))
                report["pre_state"]["suppression_cost"]["api_call_counts"] = (
                    suppression.get("api_call_counts"))
                report["pre_state"]["membership"] = {
                    "model_count": len(model_context["model_members"]),
                    "annotation_count": len(model_context["annotation_members"]),
                    "unresolved_count": model_context["unresolved_count"],
                    "rule": "OwnerViewId+datum_category "
                            "(split_stage_a_pass_membership -- the SAME split the "
                            "painting uses)",
                }
                report["pre_state"]["link_visibility"] = model_context[
                    "link_visibility"]
                report["pre_state"]["authored_model_overrides"] = model_context[
                    "authored_model_overrides"]
            if plan["fiducials"]:
                choice = model_context.get("fiducial_choice") or {}
                if choice.get("state") == "value":
                    _t = time.time()
                    fiducials = paint_fiducials(doc, view, choice["pair"])
                    fiducials["elapsed_ms"] = round((time.time() - _t) * 1000.0, 3)
                else:
                    fiducials = {"painted": [], "failed": [], "painted_count": 0,
                                 "reason": "no fiducial pair was chosen: {0}".format(
                                     choice.get("reason"))}
                report["pre_state"]["fiducials"] = fiducials
                probe_state["fiducials"] = fiducials
            _t = time.time()
            commit = pre_tx.Commit()
            # Revit regenerates on commit; for thousands of overrides that may
            # be where the time goes rather than in the Set calls themselves.
            report["transaction_group"]["pre_state_commit_ms"] = round(
                (time.time() - _t) * 1000.0, 3)
            report["transaction_group"]["pre_state_commit_status"] = str(commit)
            if commit != TransactionStatus.Committed:
                raise RuntimeError(
                    "pre-state Transaction.Commit returned {0}".format(commit))
        except Exception:
            try:
                pre_tx.RollBack()
            except Exception as ex:
                report["exceptions"].append(_exception_record("pre_state_rollback", ex))
            raise

        if plan["crop_box_visible"]:
            # The last reading before the export. The pass detaches the view
            # template inside its own transaction and nothing is known to tie
            # CropBoxVisible to a template, but "on when we set it" is not "on
            # when the export ran", so the closest reading this module can
            # take is recorded as the one the measurement check uses.
            reading = crop_box_visible_record(view)
            probe_state["crop_box_visible"]["before_annotation_pass"] = reading
            probe_state["crop_box_visible"]["during_capture"] = (
                reading.get("state") == "value" and reading.get("value") is True)

        # ---- 4: production's annotation pass ---------------------------
        report["transaction_group"]["no_child_transaction_open_at_export"] = (
            not bool(doc.IsModifiable))
        cfg = _variant_config(variant_dir, settings["export_dpi"], plan)
        t0 = time.time()
        anno_out = None
        try:
            from vop_interwoven.color_id_buffer import (
                export_annotation_color_id_buffer_view,
            )
            anno_out = export_annotation_color_id_buffer_view(
                doc, view, cfg, geom, diag=model_context["diag"],
                raster=model_context["raster"])
        except Exception as ex:
            # GUARDED the same way pipeline.py guards it, and for the same
            # reason: an exception here must not cost this variant its
            # restore, which lives below and in ``finally``.
            report["exceptions"].append(_exception_record("annotation_pass", ex))
        metadata = (anno_out or {}).get("metadata") or {}
        registration = metadata.get("registration") or {}
        report["annotation_pass"] = {
            "elapsed_ms": round((time.time() - t0) * 1000.0, 3),
            "output_directory": variant_dir,
            "success": (None if anno_out is None else bool(anno_out.get("success"))),
            "failure_reason": (None if anno_out is None
                               else anno_out.get("failure_reason")),
            "tiff_path": (None if anno_out is None else anno_out.get("tiff_path")),
            "sidecar_path": (None if anno_out is None else anno_out.get("sidecar_path")),
            "color_assignment_count": (None if anno_out is None
                                       else anno_out.get("color_assignment_count")),
            "resolution": (None if anno_out is None else anno_out.get("resolution")),
            "capture_faults": (None if anno_out is None
                               else metadata.get("capture_faults")),
            "applied_smooth_edges": (None if anno_out is None
                                     else metadata.get("applied_smooth_edges")),
            "model_suppression_mode": (None if anno_out is None
                                       else metadata.get("model_suppression_mode")),
            # What production says it did with the crop. The measurement check
            # compares it to the plan: an untouched variant that came back
            # "frame_b" moved the crop.
            "crop_mode": (None if anno_out is None else registration.get("crop_mode")),
            "rendered_uv": registration.get("rendered_uv"),
            "rendered_uv_reason": registration.get("rendered_uv_reason"),
            "requested_px_source": registration.get("requested_px_source"),
        }
        if anno_out is not None and anno_out.get("tiff_path"):
            report["annotation_pass"]["tiff_sha256"] = _sha256(anno_out["tiff_path"])

        # ---- F1/F2 facts into the sidecars a consumer reads --------------
        if anno_out is not None and (plan["crop_box_visible"] or plan["fiducials"]
                                     or plan["registration_marks"]):
            report["sidecar_annotations"] = _annotate_variant_sidecars(
                plan, anno_out, metadata, model_context, report, probe_state)

        # ---- 5: NO explicit restore -- the rollback in ``finally`` is it ----
        report["restore"]["mode"] = "transaction_group_rollback"

    except Exception as ex:
        report["exceptions"].append(_exception_record("variant", ex))
    finally:
        # ---- 5: THE RESTORE --------------------------------------------
        created_filter_ids = list(
            ((report.get("pre_state", {}).get("white_membership_suppression") or {})
             .get("link_category_filters") or {}).get("created_filter_ids") or [])
        if started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                _t_rollback = time.time()
                status = group.RollBack()
                report["restore"]["rollback_ms"] = round(
                    (time.time() - _t_rollback) * 1000.0, 3)
                report["transaction_group"]["rollback_status"] = str(status)
                report["transaction_group"]["rollback_succeeded"] = (
                    status == TransactionStatus.RolledBack)
            except Exception as ex:
                report["exceptions"].append(_exception_record("group_rollback", ex))
                report["transaction_group"]["rollback_succeeded"] = False

        # ---- 6: read-back after the rollback -- THE MEASUREMENT -------
        if snapshot_before is not None and not report.get("skipped"):
            try:
                snapshot_after_rollback = snapshot_view(doc, view)
                report["snapshot_after_rollback"] = snapshot_after_rollback
                report["restore"]["after_rollback"] = restore_readback_verdict(
                    snapshot_before, snapshot_after_rollback,
                    expect_filters_absent=created_filter_ids,
                    filter_elements_still_in_project=dict(
                        (fid, filter_element_exists(doc, fid))
                        for fid in created_filter_ids))
            except Exception as ex:
                report["exceptions"].append(_exception_record("post_rollback_snapshot", ex))
            try:
                mark_ids = [m.get("id") for m in (
                    (report.get("pre_state", {}).get("registration_marks") or {})
                    .get("created") or []) if m.get("id") is not None]
                report["restore"]["probe_marks_still_in_project"] = (
                    marks_still_in_project(doc, mark_ids))
                report["restore"]["element_overrides_after_rollback"] = (
                    element_override_readback(view, model_context, report))
            except Exception as ex:
                report["exceptions"].append(_exception_record("post_rollback_readback", ex))

            # The model TIFF, re-hashed. This detects a variant CLOBBERING the
            # model artifact -- a path collision, a stray write. It does NOT
            # detect a variant changing how the model pass RENDERS, because the
            # model pass runs once per view and is not re-run here; the
            # re-export check in _run_native answers that one.
            model_tiff = model_context.get("model_tiff_path")
            if model_tiff:
                report["model_tiff"] = {
                    "path": model_tiff,
                    "sha256": _sha256(model_tiff),
                    "matches_pre_variant": None,
                }
                pre = model_context.get("model_tiff_sha256") or {}
                now = report["model_tiff"]["sha256"]
                if pre.get("state") == "value" and now.get("state") == "value":
                    report["model_tiff"]["matches_pre_variant"] = (
                        pre["value"] == now["value"])

        # ---- this variant's own PASS/FAIL, about ITSELF only ---------
        #
        # NOT about the candidate fix. A variant PASSES when it ran and the
        # view came back; what the image shows is the analyzer's to report and
        # Greg's to judge.
        if not report.get("skipped"):
            rolled_back = (report["restore"].get("after_rollback") or {}).get("overall")
            rollback_ok = report["transaction_group"].get("rollback_succeeded")
            marks_left = [mid for mid, present in (
                report["restore"].get("probe_marks_still_in_project") or {}).items()
                if present is not False]
            overrides = (report["restore"].get("element_overrides_after_rollback")
                         or {}).get("status")

            # TWO SEPARATE QUESTIONS, and conflating them was a defect in the
            # first draft of this function.
            #
            # `document_safe` is "is the view as this variant found it". It is
            # the ONLY thing that may stop the run, because continuing past a
            # document in an unknown state makes every later artifact evidence
            # about nothing.
            #
            # `conclusion` is "did this variant produce a capture". An
            # annotation pass that RAISED with the view verifiably restored is
            # a failed variant and a safe document: the run continues, because
            # the other four candidates are still worth measuring and stopping
            # would throw away the whole view's evidence over one of them.
            report["document_safe"] = bool(
                rollback_ok
                and rolled_back not in ("not_restored", "unverified", None)
                and not marks_left
                and overrides in ("restored", "not_applicable"))
            report["document_safe_detail"] = {
                "rollback_succeeded": rollback_ok,
                "after_rollback": rolled_back,
                "probe_marks_still_in_project": marks_left,
                "element_overrides_after_rollback": overrides,
            }

            # DID IT MEASURE WHAT IT CLAIMS? A TIFF existing proves an export
            # happened, not that the variant's mutation was applied -- and
            # both switches can decline without raising. See
            # variant_measurement_check.
            measurement = variant_measurement_check(
                plan, (report["annotation_pass"] or {}), probe_state=probe_state)
            report["measurement"] = measurement

            report["conclusion"] = variant_conclusion(
                report["document_safe"], report["exceptions"],
                report["annotation_pass"].get("tiff_path"), measurement,
                capture_success=report["annotation_pass"].get("success"),
                capture_faults=report["annotation_pass"].get("capture_faults"))
    return report


# ======================================================================
# ORCHESTRATION
# ======================================================================

def _reject_reason(view):
    """Why this view cannot be probed, or None.

    Reuses production's own view-capability resolution rather than a
    type-based allowlist (Refactor Rule #2: view support is capability-based).
    """
    try:
        from Autodesk.Revit.DB import ViewType
        if view is None:
            return "IN[0] target view is required"
        if bool(getattr(view, "IsTemplate", False)):
            return "View templates are unsupported; provide a view instance"
        if (getattr(view, "ViewType", None) == ViewType.ThreeD
                and bool(getattr(view, "IsPerspective", False))):
            return "Perspective 3D views are unsupported"
        unsupported = (ViewType.Schedule, ViewType.DrawingSheet, ViewType.Legend,
                       ViewType.ProjectBrowser, ViewType.SystemBrowser,
                       ViewType.Internal)
        if getattr(view, "ViewType", None) in unsupported:
            return "Unsupported view type: {0}".format(getattr(view, "ViewType", None))
        from vop_interwoven.revit.view_basis import (
            resolve_view_mode, VIEW_MODE_MODEL_AND_ANNOTATION,
        )
        mode, reason = resolve_view_mode(view)
        if mode != VIEW_MODE_MODEL_AND_ANNOTATION:
            return "Annotation-only or rejected view: {0}".format(reason.get("why"))
    except Exception as ex:
        return "Could not validate view capability: {0}: {1}".format(
            type(ex).__name__, ex)
    return None


def finalize_native_report(report, paths):
    """Set the run's conclusion and artifact paths. PURE, and required.

    This exists as its own function because the order mattered and got it
    wrong: the combined JSON was serialised BEFORE these two fields were
    written, so the persisted file -- the one the analyzer and Greg actually
    read -- permanently said ``INCONCLUSIVE`` and carried no ``paths``, while
    only the in-memory object returned to Dynamo was correct. Two
    representations of one run, disagreeing, with the durable one wrong.

    So the fields are computed here, ``report_finalized`` is stamped, and
    ``_write_combined`` REFUSES a report without that stamp. A future call site
    that writes before finalising fails loudly instead of quietly persisting an
    unfinished report.

    A variant that did not MEASURE its own candidate does not make the run
    fail: the document is safe and the TIFF is real. It is surfaced as its own
    conclusion so it cannot be read as a clean run.
    """
    report["paths"] = dict(paths or {})
    ran = [entry for entry in report.get("variants", []) if not entry.get("skipped")]
    if not ran:
        report["conclusion"] = "INCONCLUSIVE"
    elif not all(entry.get("document_safe") for entry in ran):
        report["conclusion"] = "FAIL"
    elif any(entry.get("conclusion") == "ERRORED" for entry in ran):
        report["conclusion"] = "ERRORED"
    elif any(entry.get("conclusion") == "CAPTURE_FAILED" for entry in ran):
        report["conclusion"] = "CAPTURE_FAILED"
    elif any(entry.get("conclusion") == "DID_NOT_MEASURE" for entry in ran):
        report["conclusion"] = "DID_NOT_MEASURE"
    else:
        report["conclusion"] = "RAN"
    report["variants_with_failed_captures"] = [
        {"variant": entry.get("variant"),
         "failure_reason": (entry.get("annotation_pass") or {}).get("failure_reason"),
         "capture_faults": (entry.get("annotation_pass") or {}).get("capture_faults")}
        for entry in ran if entry.get("conclusion") == "CAPTURE_FAILED"]
    report["variants_that_did_not_measure"] = [
        {"variant": entry.get("variant"),
         "unmet": (entry.get("measurement") or {}).get("unmet")}
        for entry in ran if entry.get("conclusion") == "DID_NOT_MEASURE"]
    report["report_finalized"] = True
    return report


def _write_combined(report, probe_dir, base):
    """Write the combined report, and record where -- on EVERY exit path.

    A failed model pass used to return without writing anything, so the one run
    most worth reading left nothing on disk: whatever Dynamo's OUT pane happened
    to show, and only until the session closed. A report is cheap and the
    failure is the evidence.

    REFUSES an unfinalised report rather than persisting one. See
    finalize_native_report for what that prevented.
    """
    if not report.get("report_finalized"):
        raise RuntimeError(
            "refusing to write the combined report before finalize_native_report() "
            "has set its conclusion and paths: the persisted file is what the "
            "analyzer and the reader consume, and an unfinalised one reports the "
            "wrong conclusion permanently")
    json_path = os.path.join(probe_dir, base + ".anno_pass_variants.json")
    try:
        with open(json_path, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, default=str)
    except Exception as ex:
        report["combined_json_write_error"] = "{0}: {1}".format(
            type(ex).__name__, ex)
        return None
    return json_path


def discover_link_categories(doc, view, diag=None, view_id=None):
    """Production's link-category discovery, ``(link_categories, report)``."""
    return _reg().discover_link_categories(doc, view, diag=diag, view_id=view_id)


def _run_native(raw_view, output_dir, selection="all", export_dpi=DEFAULT_EXPORT_DPI,
                authored_override_scan_max=DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX,
                model_reexport_check=True):
    out_dir = os.path.abspath(str(output_dir or os.getcwd()))
    _ensure_repo_import_path(out_dir)
    _ensure_revit_api_reference()
    doc = _doc()
    view = _unwrap(raw_view)
    reject = _reject_reason(view)
    if reject:
        return {"conclusion": "FAIL", "reason": reject, "variants": []}

    from vop_interwoven.core.diagnostics import Diagnostics
    from vop_interwoven.pipeline import init_view_raster
    from vop_interwoven.revit.collection import collect_view_elements
    from vop_interwoven.color_id_buffer import export_color_id_buffer_view

    probe_dir = os.path.join(out_dir, "anno_pass_variants_probe")
    model_dir = os.path.join(probe_dir, "model")
    for path in (probe_dir, model_dir):
        if not os.path.isdir(path):
            os.makedirs(path)

    view_id = _element_id_int(view.Id)
    scale = float(getattr(view, "Scale", 1) or 1)
    base = "{0}_{1}".format(_safe_name(getattr(view, "Name", "view")), view_id)
    diag = Diagnostics()
    selected = select_variants(selection)

    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION,
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {
            "view_id": view_id, "view_name": getattr(view, "Name", None),
            "view_type": str(getattr(view, "ViewType", None)),
            "view_scale": scale,
            "output_directory": out_dir, "probe_directory": probe_dir,
            "selection": list(selected), "export_dpi": float(export_dpi),
            "authored_override_scan_max": int(authored_override_scan_max),
            "fiducial_candidate_scan_max": FIDUCIAL_CANDIDATE_SCAN_MAX,
            "model_reexport_check": bool(model_reexport_check),
        },
        "model_pass": {},
        "variants": [],
        "unconfirmed_api_claims": [
            "ImageExportOptions draws the crop boundary when CropBoxVisible is on "
            "-- F1 depends on it entirely. ImageExportOptions has no "
            "hide-crop-boundaries flag (Print Setup and PDF export both do), which "
            "is suggestive, not evidence; the analyzer's crop-boundary detection is "
            "the evidence either way",
            "View.GetCropRegionShapeManager().GetCropShape() returns the crop's "
            "curve loops (F1's shape record)",
            "View.CropBoxVisible is settable from outside the annotation pass and "
            "is not reset by the pass detaching the view template",
            "a white ELEMENT override suppresses a model element's own graphics "
            "(V7, V8, mechanism 1)",
            "cat.SubCategories is walked and each subcategory takes a white "
            "override (mechanism 3) -- and whether that reaches LINKED content",
            "View.GetLinkOverrides(ElementId).LinkVisibilityType exists, so a link "
            "not displayed By Host View can be reported",
            "setting View.DisplayStyle does not replace the ViewDisplayModel and "
            "discard a SmoothEdges written before it -- the production switch "
            "writes SmoothEdges AFTER DisplayStyle so this does not matter, but "
            "the claim is recorded because the ordering is the mitigation",
            "with the crop untouched, ExportImage renders the authored crop (or the "
            "content union) at a stable scale -- which F1 and F2 MEASURE rather "
            "than assume",
        ],
        "conclusion": "INCONCLUSIVE",
    }

    # ---- the MODEL pass, once per view, shipped behaviour --------------
    try:
        _force_close_dynamo_transaction()
        model_cfg = _model_config(model_dir, export_dpi)
        raster = init_view_raster(doc, view, model_cfg, diag=diag)
        elements = collect_view_elements(doc, view, raster, diag=diag, cfg=model_cfg)
        geom = {}
        _model_t0 = time.time()
        model_out = export_color_id_buffer_view(
            doc, view, elements, model_cfg, diag=diag, raster=raster,
            geometry_out=geom)
        model_pass_ms = round((time.time() - _model_t0) * 1000.0, 3)
    except Exception as ex:
        report["model_pass"] = {"success": False,
                                "error": _exception_record("model_pass", ex)}
        report["reason"] = ("the model pass failed, so there is no frame geometry "
                            "and no annotation variant can register against "
                            "anything")
        finalize_native_report(report, {})
        report["conclusion"] = "FAIL"
        report["paths"]["combined_json"] = _write_combined(report, probe_dir, base)
        _write_combined(report, probe_dir, base)
        return report

    if not geom:
        report["model_pass"] = {
            "success": bool(model_out.get("success")),
            "failure_reason": model_out.get("failure_reason"),
            "geometry": None,
        }
        report["reason"] = ("the model pass resolved no frame geometry; the "
                            "annotation pass REFUSES to run without it and this "
                            "probe does not substitute one")
        finalize_native_report(report, {})
        report["conclusion"] = "FAIL"
        report["paths"]["combined_json"] = _write_combined(report, probe_dir, base)
        _write_combined(report, probe_dir, base)
        return report

    # THE MODEL PASS'S OWN VERDICT, and it gates the run. It can return a TIFF
    # and usable geometry with success=False -- an export_dim_mismatch that
    # survived the halving backoff is the reachable case -- and the status was
    # being recorded here and composed into no decision at all. Every variant
    # registers against this capture, so an invalid one makes all five
    # annotation captures evidence about a foundation production has already
    # rejected, and the run could still conclude RAN/completed.
    #
    # REFUSING rather than running and flagging, for the same reason the missing
    # -geometry branch above refuses: which model faults are tolerable is Greg's
    # call, not this module's, and five captures against a rejected foundation
    # cost a Revit session to produce and prove nothing. The fault is named so
    # the decision can be made. (PR #215 review, round 3.)
    if not model_out.get("success"):
        report["model_pass"] = {
            "success": False,
            "failure_reason": model_out.get("failure_reason"),
            "tiff_path": model_out.get("tiff_path"),
            "sidecar_path": model_out.get("sidecar_path"),
            "output_directory": model_dir,
        }
        report["reason"] = (
            "the model pass REJECTED ITS OWN CAPTURE ({0}); every annotation "
            "variant registers against it, so no variant was run. The model TIFF "
            "and sidecar are on disk as the evidence. Re-run once the model "
            "capture is sound, or say which model faults this probe should "
            "tolerate.".format(model_out.get("failure_reason")))
        finalize_native_report(report, {
            "model_tiff": model_out.get("tiff_path"),
            "model_sidecar": model_out.get("sidecar_path"),
        })
        report["conclusion"] = "FAIL"
        report["paths"]["combined_json"] = _write_combined(report, probe_dir, base)
        _write_combined(report, probe_dir, base)
        return report

    model_tiff_path = model_out.get("tiff_path")
    model_tiff_sha = _sha256(model_tiff_path) if model_tiff_path else _unavailable(
        "the model pass reported no TIFF path")
    report["model_pass"] = {
        "success": bool(model_out.get("success")),
        "failure_reason": model_out.get("failure_reason"),
        "tiff_path": model_tiff_path,
        "sidecar_path": model_out.get("sidecar_path"),
        "tiff_sha256": model_tiff_sha,
        "output_directory": model_dir,
        "elapsed_ms": model_pass_ms,
        "geometry": {
            "frame_snapped_uv": [float(v) for v in geom["frame_snapped_uv"]],
            "frame_px": [int(v) for v in geom["frame_px"]],
            "crop_snapped_uv": [float(v) for v in geom["crop_snapped_uv"]],
            "crop_px": [int(v) for v in geom["crop_px"]],
            "crop_offset_px": [int(v) for v in geom["crop_offset_px"]],
            "crop_is_frame": bool(geom["crop_is_frame"]),
            "achieved_fpp_ft": float(geom["achieved_fpp_ft"]),
            "achieved_export_dpi": float(geom["achieved_export_dpi"]),
            "requested_px": int(geom["requested_px"]),
            "model_accepted_px": geom.get("model_accepted_px"),
            "model_dim_check": geom.get("model_dim_check"),
            "cap_applied": bool(geom["cap_applied"]),
        },
    }

    # ---- everything the variants share, resolved once -----------------
    view_basis = getattr(raster, "view_basis", None)
    if view_basis is None:
        try:
            from vop_interwoven.revit.view_basis import make_view_basis
            view_basis = make_view_basis(view, diag=diag)
        except Exception as ex:
            report.setdefault("warnings", []).append(
                "no view basis: {0}: {1}; the authored crop and the fiducials "
                "cannot be placed in UV".format(
                    type(ex).__name__, ex))

    white_capability = white_override_capability(doc)

    # ---- THE MEMBERSHIP SPLIT, resolved ONCE ---------------------------
    #
    # The same call the annotation pass makes, on the same view, so the set this
    # suppresses and the set that gets painted cannot come apart. Resolved here
    # rather than per variant because four variants sharing one split is the
    # point: a per-variant split would let two variants suppress different sets
    # and be compared as if they had not.
    from vop_interwoven.revit.annotation import split_stage_a_pass_membership
    from Autodesk.Revit.DB import FilteredElementCollector as _FEC
    membership_error = None
    model_members, annotation_members, unresolved = [], [], []
    try:
        view_elements = list(
            _FEC(doc, view.Id).WhereElementIsNotElementType())
        model_members, annotation_members, unresolved, membership_basis = (
            split_stage_a_pass_membership(
                view_elements, capture_view_id_int=view_id, diag=diag))
    except Exception as ex:
        membership_error = "{0}: {1}".format(type(ex).__name__, ex)
        membership_basis = {}
    report["membership"] = {
        "rule": "OwnerViewId+datum_category (split_stage_a_pass_membership)",
        "model_count": len(model_members),
        "annotation_count": len(annotation_members),
        "unresolved_count": len(unresolved),
        "basis_counts": dict(
            (k, v) for k, v in (membership_basis or {}).items()
            if not isinstance(v, (list, dict))),
        "datum_category_counts": (membership_basis or {}).get(
            "datum_category_counts"),
        "error": membership_error,
    }

    # ---- LINKED RVT categories, for mechanism 2 ------------------------
    #
    # Per-element override is impossible for linked content (ledger M1), so this
    # is the one set that needs the category-filter route. Discovered through
    # production's own linked-doc category policy rather than a second opinion
    # about which categories a link contributes.
    link_categories, link_report = discover_link_categories(
        doc, view, diag=diag, view_id=view_id)
    report["link_categories"] = link_report

    model_element_ids = []
    for elem in model_members or elements or []:
        value = _element_id_int(getattr(elem, "Id", None))
        if value is not None:
            model_element_ids.append(value)
    try:
        authored = authored_override_scan(
            view, model_element_ids, authored_override_scan_max)
    except Exception as ex:
        authored = _unavailable("{0}: {1}".format(type(ex).__name__, ex))

    # ---- THE AUTHORED CROP, read once and never written ------------------
    #
    # Read AFTER the model pass, which sets the crop to A for its own export and
    # restores it -- so this is the view as authored. It is F1's known position,
    # the reference rectangle F2's fiducials are chosen inside, and the
    # reference the analyzer measures an untouched capture's margins against.
    authored_crop = crop_region_record(view, view_basis, diag=diag)
    report["authored_crop"] = authored_crop
    authored_uv = ((authored_crop.get("crop_box_uv") or {}).get("value")
                   if (authored_crop.get("crop_box_uv") or {}).get("state") == "value"
                   else None)
    authored_active = ((authored_crop.get("crop_box_active") or {}).get("value")
                       if (authored_crop.get("crop_box_active") or {}).get(
                           "state") == "value" else None)
    # How far the crop MOVES, for the record. V0 widens it to B; the model pass
    # sets it to A. Both are reported against the authored crop, because "the
    # capture does not modify the crop" is only true of the ANNOTATION pass
    # under V7/V8 -- the model pass still writes A (production is unchanged
    # there), and on a crop-INACTIVE view it ACTIVATES one.
    report["crop_relationships"] = {
        "authored_crop_active": authored_active,
        "authored_crop_uv": authored_uv,
        "frame_b_uv": [float(v) for v in geom["frame_snapped_uv"]],
        "model_crop_a_uv": [float(v) for v in geom["crop_snapped_uv"]],
        "v0_widens_crop_by_ft": (per_side_delta(authored_uv, geom["frame_snapped_uv"])
                                 if authored_uv else None),
        "model_pass_crop_a_minus_authored_ft": (
            per_side_delta(authored_uv, geom["crop_snapped_uv"])
            if authored_uv else None),
        "model_pass_activates_a_crop": (authored_active is False),
    }

    # ---- F1's ruler: the view's own crop-region element ------------------
    crop_elements = crop_region_elements(doc, view, model_members)
    report["crop_region_elements"] = crop_elements
    crop_element_ids = set(crop_elements.get("crop_element_ids") or [])

    # ---- F2: the fiducial pair, chosen once --------------------------------
    fiducial_choice = {"state": "unavailable",
                       "reason": "{0} was not requested for this run".format(V8)}
    if V8 in selected:
        if view_basis is None:
            fiducial_choice = {"state": "unavailable",
                               "reason": "no view basis, so no model element can be "
                                         "placed in UV"}
        else:
            # Inside the authored crop when it is active; otherwise inside the
            # model pass's rendered crop A -- the only rectangle known to be on
            # screen when there is no crop.
            reference_uv = (authored_uv if (authored_active and authored_uv)
                            else [float(v) for v in geom["crop_snapped_uv"]])
            try:
                candidates, candidate_record = collect_fiducial_candidates(
                    view, view_basis,
                    [m for m in model_members
                     if _element_id_int(getattr(m, "Id", None)) not in crop_element_ids],
                    FIDUCIAL_CANDIDATE_SCAN_MAX, diag=diag, view_id=view_id)
                fiducial_choice = choose_fiducial_pair(
                    candidates, reference_uv, float(geom["achieved_fpp_ft"]))
                fiducial_choice["candidate_collection"] = candidate_record
                fiducial_choice["reference_source"] = (
                    "authored_crop" if (authored_active and authored_uv)
                    else "model_crop_a")
            except Exception as ex:
                fiducial_choice = {"state": "unavailable",
                                   "reason": "fiducial selection raised {0}: {1}".format(
                                       type(ex).__name__, ex)}
    report["fiducial_choice"] = fiducial_choice

    model_context = {
        "diag": diag, "raster": raster, "geom": geom,
        "model_tiff_path": model_tiff_path, "model_tiff_sha256": model_tiff_sha,
        "white_override_capability": white_capability,
        "model_members": model_members,
        "annotation_members": annotation_members,
        "unresolved_count": len(unresolved),
        "link_categories": link_categories,
        "link_visibility": link_visibility_report(doc, view),
        "authored_model_overrides": authored,
        "elements": elements,
        "model_pass_ms": model_pass_ms,
        "authored_crop": authored_crop,
        "fiducial_choice": fiducial_choice,
        "crop_region_elements": crop_elements,
    }
    settings = {"probe_dir": probe_dir, "export_dpi": float(export_dpi)}

    # THE CONTROL, taken before any variant touches the view: are two exports
    # of the untouched view byte-identical on this host at all? Without this
    # the post-variant comparison below is a decoration. See
    # model_reexport_verdict.
    model_repeat_control = {"performed": False,
                            "skipped_reason": "model_reexport_check is off"}
    if model_reexport_check and model_tiff_path:
        model_repeat_control = _model_repeat_export(
            doc, view, elements, raster, export_dpi, probe_dir, diag, "control")
    report["model_repeat_control"] = model_repeat_control
    report["white_override_capability"] = white_capability
    report["link_visibility"] = model_context["link_visibility"]
    report["authored_model_overrides"] = authored

    # ---- the variants ------------------------------------------------
    #
    # STOPS on the first view whose restore read-back fails, which is the
    # probe brief's hard requirement. Continuing would run four more variants
    # against a document already known to be in an unexpected state, and
    # every artifact after that point would be evidence about nothing.
    for variant in selected:
        entry = _run_variant(doc, view, variant, model_context, settings)
        report["variants"].append(entry)
        if not entry.get("skipped") and not entry.get("document_safe"):
            report["stopped_early"] = {
                "after_variant": variant,
                "reason": "this variant's restore read-back did not verify the view "
                          "was returned to the state it was found in; the run stops "
                          "rather than exporting further captures from a document in "
                          "an unknown state",
                "document_safe_detail": entry.get("document_safe_detail"),
                "remaining_variants": [name for name in selected
                                       if name not in [v["variant"]
                                                       for v in report["variants"]]],
            }
            break

    # ---- the stop-and-raise number, lifted to the top of the report ------
    #
    # If per-element white overrides cost MATERIALLY more than the model pass's
    # own paint, the brief says report the number before running more views.
    # It lives on each white variant's pre_state; this is the first one, where a
    # reader looks first. No threshold is applied here -- see
    # suppression_cost_record for why the ratio is a lower bound.
    report["suppression_cost"] = next(
        (entry["pre_state"]["suppression_cost"] for entry in report["variants"]
         if (entry.get("pre_state") or {}).get("suppression_cost")),
        {"state": "unavailable",
         "reason": "no white-suppression variant ran on this view"})

    # ---- did the variants change how the MODEL pass renders? ---------
    #
    # The per-variant hash above catches a variant CLOBBERING the model file.
    # It cannot catch a variant leaving the view in a state that makes the
    # model pass render differently, because the model pass is not re-run --
    # so it is re-run HERE, once, into a throwaway path, and the hash
    # compared. The throwaway file is deleted, which is why the artifact count
    # stays at one model pair per view.
    if model_reexport_check and model_tiff_path and not report.get("stopped_early"):
        after = _model_repeat_export(
            doc, view, elements, raster, export_dpi, probe_dir, diag,
            "after_variants")
        report["model_reexport_after_variants"] = {
            "control": model_repeat_control,
            "after_variants": after,
            "verdict": model_reexport_verdict(
                model_tiff_sha, model_repeat_control, after),
        }
    elif model_reexport_check:
        report["model_reexport_after_variants"] = {
            "control": model_repeat_control,
            "after_variants": None,
            "verdict": {"status": "not_applicable",
                        "reason": "the run stopped early or the model pass produced "
                                  "no TIFF, so no post-variant re-export was taken"},
        }

    # FINALIZE, THEN WRITE. The persisted file is what the analyzer and Greg
    # read; writing first left it saying INCONCLUSIVE with no paths forever.
    finalize_native_report(report, {
        "model_tiff": model_tiff_path,
        "model_sidecar": model_out.get("sidecar_path"),
        "annotation_tiffs": [entry.get("annotation_pass", {}).get("tiff_path")
                             for entry in report["variants"]
                             if entry.get("annotation_pass", {}).get("tiff_path")],
        "annotation_sidecars": [entry.get("annotation_pass", {}).get("sidecar_path")
                                for entry in report["variants"]
                                if entry.get("annotation_pass", {}).get("sidecar_path")],
        # V8's own model pair, boundary visible. Its own directory, so it never
        # collides with the shared model pair above.
        "variant_model_tiffs": [(entry.get("own_model_pass") or {}).get("tiff_path")
                                for entry in report["variants"]
                                if (entry.get("own_model_pass") or {}).get("tiff_path")],
        "variant_model_sidecars": [
            (entry.get("own_model_pass") or {}).get("sidecar_path")
            for entry in report["variants"]
            if (entry.get("own_model_pass") or {}).get("sidecar_path")],
    })
    # combined_json is the one path that cannot be known before the write, so
    # it is added after and the file is rewritten once with it present -- rather
    # than persisting a report whose own location is missing from it.
    json_path = _write_combined(report, probe_dir, base)
    report["paths"]["combined_json"] = json_path
    if json_path is not None:
        _write_combined(report, probe_dir, base)
    return report


def _model_repeat_export(doc, view, elements, raster, export_dpi, probe_dir, diag,
                         label):
    """Re-run the MODEL pass with the same inputs, into a throwaway directory.

    Returns a hash record. The throwaway directory is deleted, so this costs
    one export and leaves no artifact -- the run still publishes exactly one
    model pair per view.
    """
    import shutil
    from vop_interwoven.color_id_buffer import export_color_id_buffer_view
    scratch = os.path.join(probe_dir, "_model_repeat_" + label)
    record = {"label": label, "performed": False}
    try:
        _force_close_dynamo_transaction()
        cfg = _model_config(scratch, export_dpi)
        # THE SAME raster AND THE SAME element list as the first export. A
        # re-export with different inputs is a different render, and comparing
        # its bytes would answer a question nobody asked.
        out = export_color_id_buffer_view(
            doc, view, elements, cfg, diag=diag, raster=raster, geometry_out=None)
        path = out.get("tiff_path")
        record["performed"] = True
        record["success"] = bool(out.get("success"))
        record["failure_reason"] = out.get("failure_reason")
        record["sha256"] = _sha256(path) if path else _unavailable("no TIFF path")
    except Exception as ex:
        record["error"] = _exception_record("model_repeat_export_" + label, ex)
        record["sha256"] = _unavailable("the repeat export raised")
    finally:
        try:
            if os.path.isdir(scratch):
                shutil.rmtree(scratch)
        except Exception as ex:
            record["scratch_cleanup_error"] = "{0}: {1}".format(
                type(ex).__name__, ex)
    return record


def model_reexport_verdict(original_sha, control, after_variants):
    """Did the variants change how the MODEL pass renders?

    THIS NEEDS A CONTROL AND THE CONTROL IS NOT OPTIONAL. Two exports of the
    same view could differ byte-for-byte on this host for reasons that have
    nothing to do with the variants -- a timestamp in the TIFF header, a
    non-deterministic collector order feeding the palette. Without knowing
    that, a differing hash after the variants proves nothing and an identical
    one proves nothing either.

    So the first thing measured is a repeat export taken BEFORE any variant
    runs, with the view untouched. Only if that comes back identical to the
    original does the post-variant comparison mean anything, and when it does
    not, this says ``not_applicable`` and why -- it does not fall back to
    reporting the comparison anyway.

    Three-valued, and the three are different facts:
        "unchanged"       the control held AND the post-variant hash matches
        "changed"         the control held AND it does not -- a variant left
                          the view in a state the model pass renders
                          differently, which the per-variant re-hash cannot
                          see because nothing rewrote that file
        "not_applicable"  the control did not hold, or a hash is missing; the
                          question is NOT ANSWERED
    """
    def _sha_of(record):
        value = (record or {}).get("sha256") or {}
        return value.get("value") if value.get("state") == "value" else None

    original = (original_sha or {}).get("value") if (
        original_sha or {}).get("state") == "value" else None
    control_sha = _sha_of(control)
    after_sha = _sha_of(after_variants)

    out = {"original_sha256": original, "control_sha256": control_sha,
           "after_variants_sha256": after_sha}
    if original is None or control_sha is None:
        out["status"] = "not_applicable"
        out["reason"] = ("the original or the control hash is unavailable, so "
                         "repeatability was never established")
        return out
    out["control_repeatable"] = (original == control_sha)
    if original != control_sha:
        out["status"] = "not_applicable"
        out["reason"] = ("two exports of the UNTOUCHED view already differ on this "
                         "host, so a post-variant hash difference would say nothing "
                         "about the variants. The model TIFF is not byte-comparable "
                         "here; read the analyzer's pixel evidence instead.")
        return out
    if after_sha is None:
        out["status"] = "not_applicable"
        out["reason"] = "the post-variant re-export produced no readable hash"
        return out
    out["status"] = "unchanged" if after_sha == original else "changed"
    if out["status"] == "changed":
        out["reason"] = ("the model pass renders differently after the variants ran, "
                         "so at least one variant left the view in a state its "
                         "restore read-back did not cover")
    return out


def run_probe(raw_view, output_dir, selection="all", export_dpi=DEFAULT_EXPORT_DPI,
              authored_override_scan_max=DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX,
              model_reexport_check=True, repo_root=None):
    if repo_root:
        root = os.path.abspath(os.path.expanduser(str(repo_root)))
        if root not in sys.path:
            sys.path.insert(0, root)
    _ensure_contract_import_path(os.path.abspath(str(output_dir or os.getcwd())))
    started_at = _probe_contract().utc_now_iso()
    native = _run_native(
        raw_view, output_dir, selection, export_dpi,
        authored_override_scan_max,
        model_reexport_check)
    variants = native.get("variants", [])
    executed = [entry for entry in variants if not entry.get("skipped")]
    rollback_ok = bool(executed) and all(
        entry.get("transaction_group", {}).get("rollback_succeeded")
        for entry in executed)
    restored = bool(executed) and all(
        entry.get("document_safe") for entry in executed)
    errors = [error for entry in executed for error in entry.get("exceptions", [])]
    paths = native.get("paths", {})
    artifacts = ([paths.get("combined_json")] if paths.get("combined_json") else [])
    for key in ("model_tiff", "model_sidecar"):
        if paths.get(key):
            artifacts.append(paths[key])
    artifacts.extend([p for p in paths.get("annotation_tiffs", []) if p])
    artifacts.extend([p for p in paths.get("annotation_sidecars", []) if p])
    artifacts.extend([p for p in paths.get("variant_model_tiffs", []) if p])
    artifacts.extend([p for p in paths.get("variant_model_sidecars", []) if p])
    return _probe_contract().execution_envelope(
        PROBE_NAME,
        {"selection": [entry.get("variant") for entry in variants],
         "export_dpi": export_dpi,
         "authored_override_scan_max": authored_override_scan_max,
         "model_reexport_check": model_reexport_check},
        _probe_contract().view_identity(raw_view), native, artifacts,
        "succeeded" if rollback_ok else ("not_started" if not executed else "failed"),
        "restored" if restored else ("not_checked" if not executed else "not_restored"),
        started_at,
        # A variant that did not MEASURE its candidate makes the run
        # INCONCLUSIVE, not completed: the captures exist and the document is
        # safe, but at least one of them is not evidence about the thing it is
        # named after. "completed" here would be the same silence the
        # per-variant conclusion just stopped telling.
        execution_status=("inconclusive" if not executed else
                          ("failed" if errors or not rollback_ok or not restored
                           else ("inconclusive" if any(
                               entry.get("conclusion")
                               in ("DID_NOT_MEASURE", "CAPTURE_FAILED")
                               for entry in executed) else "completed"))),
        errors=errors,
        warnings=[
            "variant {0} did not measure its candidate: {1}".format(
                entry.get("variant"),
                (entry.get("measurement") or {}).get("unmet"))
            for entry in executed
            if entry.get("conclusion") == "DID_NOT_MEASURE"
        ] + [
            "variant {0} produced a capture PRODUCTION declared invalid: {1}".format(
                entry.get("variant"),
                (entry.get("annotation_pass") or {}).get("failure_reason"))
            for entry in executed
            if entry.get("conclusion") == "CAPTURE_FAILED"])


def dynamo_main(inputs):
    return run_probe(
        inputs[0] if len(inputs) > 0 else None,
        inputs[1] if len(inputs) > 1 else None,
        inputs[2] if len(inputs) > 2 and inputs[2] else "all",
        inputs[3] if len(inputs) > 3 and inputs[3] else DEFAULT_EXPORT_DPI,
    )


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821
    except Exception as _ex:
        OUT = {"conclusion": "FAIL", "error": str(_ex),
               "error_type": type(_ex).__name__,
               "traceback": traceback.format_exc()}
