"""Stage A ANNOTATION-PASS variant probe for Revit 2025 / Dynamo 3.3 CPython3.

Inputs:
    IN[0] = target view
    IN[1] = output directory
    IN[2] = optional variant selection ("all", or a comma-separated subset)
    IN[3] = optional export dpi (default 150)

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
A PROBE. It runs four candidate fixes for the annotation pass beside the
current pass on the same view, writes what each produced, and stops. It
changes no production default, it concludes nothing about which fix is right,
and it emits no pass/fail on the fixes themselves: Greg's read of the output
is the gate. The only PASS/FAIL this probe makes is about ITSELF -- whether
each variant ran and whether the view was put back -- because a variant that
silently failed to restore the document is not evidence, it is damage.

THE FOUR CANDIDATES (findings F1-F3 of run 20260922T085737)
------------------------------------------------------------
V0  the current annotation pass, unchanged. The control.
V0-offsets0
    the same, with the view's four ANNOTATION CROP OFFSETS zeroed first.
    Isolates Greg's competing hypothesis for F1: that those offsets (1" per
    side on the run's views) widen what ExportImage fits even though the
    annotation crop itself is inactive.
V1  model suppression by a WHITE view filter instead of hiding model
    categories. F2: hiding model categories takes dependent annotations
    with it -- tags, and most dimensions. Greg confirmed by hand that a
    filter setting model lines and fills to white leaves a clean
    annotation-only view.
V2  V1 plus SmoothEdges off. F3: the annotation pass never sets it, and
    ~0.2% of pixels are palette-to-white blends.
V3  V2 plus an expanded annotation frame B'. F1: B is too small because
    is_extent_driver_annotation admits only text, dimensions and a fixed tag
    list -- no grids, levels, viewers, ceiling tags, multi-category tags,
    detail items or revision clouds. RCP and section got no expansion at all
    (B == A). B' is B unioned with every annotation-pass member's
    get_BoundingBox(view) and every rendered viewer element's, plus a 0.5"
    paper margin.

WHAT IS NOT TOUCHED
-------------------
``revit/annotation.py``'s ``compute_annotation_extents`` (:123) and
``is_extent_driver_annotation`` (:47) -- the geometry path's frame-B
expansion -- are NOT modified and NOT called differently. That path is the
retained arbiter; B' is computed by THIS module's own
``expanded_frame_uv()``, which is a pure function and is unit-tested. A
probe that edited the arbiter it is measuring against would have nothing
left to compare to.

``dim_check`` (F4) is not modified either. Its call site carries a TODO
citing F4 and nothing more; the fix lands after Greg reads this probe's
output, because what the derived axis should be compared against is one of
the things this probe measures.

PRODUCTION SURFACE THIS USES
----------------------------
The annotation pass itself is PRODUCTION's
``export_annotation_color_id_buffer_view`` -- called, never reimplemented.
Two opt-in switches were added to it, both read with ``getattr`` off the cfg
object, both defaulting to the shipped behaviour and both absent from
``Config`` because they are not production settings:

    color_id_buffer_anno_model_suppression = "external"
        this pass hides no model category and disables no view filter, so
        the white filter THIS module applies survives into the export. Used
        by V1/V2/V3.
    color_id_buffer_anno_smooth_edges_off = True
        capture/clear/restore SmoothEdges inside the suppress transaction,
        AFTER the DisplayStyle change. Used by V2/V3.

Both switches exist in production rather than here for the same reason: the
behaviour they change happens inside that function's own transaction, where
this module has no window. Everything else a variant does -- the filter, the
crop offsets, B' -- this module does itself, before and after the call.

V3 needs no switch at all: the annotation pass is HANDED its frame geometry
by its caller, so V3 simply hands it a different one.

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
PROBE_VERSION = "2026-09-22.1"

V0 = "v0_control"
V0_OFFSETS0 = "v0_offsets0"
V1 = "v1_white_filter"
V2 = "v2_white_filter_smooth_edges_off"
V3 = "v3_white_filter_smooth_edges_off_expanded_frame"

SUPPORTED_VARIANTS = (V0, V0_OFFSETS0, V1, V2, V3)

# WHAT EACH VARIANT CHANGES, as three membership sets rather than an if/elif
# chain per property. A new variant is a row here, and a variant missing from
# every set is visibly a control rather than silently a no-op.
WHITE_FILTER_VARIANTS = frozenset((V1, V2, V3))
SMOOTH_EDGES_OFF_VARIANTS = frozenset((V2, V3))
EXPANDED_FRAME_VARIANTS = frozenset((V3,))
ZERO_ANNOTATION_CROP_OFFSET_VARIANTS = frozenset((V0_OFFSETS0,))

DEFAULT_EXPORT_DPI = 150.0
# B' adds this much PAPER margin on every side, per the probe brief.
DEFAULT_EXPANDED_FRAME_MARGIN_IN = 0.5
# Reading GetElementOverrides for every model element in a large view is
# thousands of API calls, so the scan is bounded and says so. A capped scan is
# recorded as capped with both counts -- never as a total.
DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX = 5000

# UNCONFIRMED. ViewCropRegionShapeManager's four annotation-crop offset
# properties, believed to exist on Revit 2025 under these names. Resolved by
# reflection; an absent name makes the whole V0-offsets0 variant "unavailable"
# for that view rather than recording a 0 that was never read.
ANNOTATION_CROP_OFFSET_PROPERTIES = (
    "LeftAnnotationCropOffset",
    "RightAnnotationCropOffset",
    "TopAnnotationCropOffset",
    "BottomAnnotationCropOffset",
)

# UNCONFIRMED, and a LIST rather than a guess at the one right category: the
# probe brief names "section, elevation and callout marks, viewers, view
# references" and Revit spreads those over several BuiltInCategory members
# whose presence varies by version. Every name is resolved by reflection and
# the ones that did NOT resolve are recorded, so a category that contributed
# nothing because it does not exist on this host is distinguishable from one
# that contributed nothing because the view holds none of it.
VIEWER_EXTENT_BIC_NAMES = (
    "OST_Viewers",
    "OST_Elev",
    "OST_ElevationMarks",
    "OST_Sections",
    "OST_SectionHeads",
    "OST_Callouts",
    "OST_CalloutHeads",
    "OST_CalloutBoundary",
    "OST_ReferenceViewer",
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
    """What this variant changes, as plain booleans.

    One place, so the probe's records, the production switches it sets and
    the PROBE md's table cannot drift into three different answers.
    """
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError("Unknown variant {0!r}. Supported: {1}".format(
            variant, list(SUPPORTED_VARIANTS)))
    return {
        "variant": variant,
        "white_filter": variant in WHITE_FILTER_VARIANTS,
        "smooth_edges_off": variant in SMOOTH_EDGES_OFF_VARIANTS,
        "expanded_frame": variant in EXPANDED_FRAME_VARIANTS,
        "zero_annotation_crop_offsets": (
            variant in ZERO_ANNOTATION_CROP_OFFSET_VARIANTS),
        # Which production suppression mode this variant asks for. A variant
        # that applies the white filter must ALSO tell the annotation pass not
        # to hide model categories and not to disable the filter, or it would
        # measure the two suppressions on top of each other.
        "model_suppression": (
            "external" if variant in WHITE_FILTER_VARIANTS else "hide_categories"),
    }


def variant_measurement_check(plan, annotation_metadata):
    """Did production actually apply what this variant ASKED FOR?

    PURE, and the answer this probe was missing. A TIFF existing proves an
    export happened; it does not prove the export measured the variant. Both
    switches can decline:

      * ``applied_smooth_edges`` comes back ``"read_failed"`` or
        ``"unchanged (failed)"`` when the ViewDisplayModel read or write fails.
        Production does not raise -- correctly, an unconfirmed AA state costs
        decode confidence, not the export -- so V2/V3 would return a TIFF that
        measured V1's behaviour under V2/V3's name.
      * ``model_suppression_mode`` is what production says it did. A V1-V3
        capture that came back ``"hide_categories"`` hid model categories and
        disabled the probe's own filter: it measured V0's suppression.

    Either way the variant is not evidence about its candidate, and the failure
    was previously invisible -- recorded in the sidecar, absent from the
    conclusion, and never rendered by the analyzer. Three places to look and no
    place that said so.

    Returns ``{"measured": bool, "unmet": [...], "checked": [...]}``. ``unmet``
    names each requested mutation production did not confirm, with what it
    reported instead, so "V2 did not measure AA" is readable without opening
    the sidecar.
    """
    metadata = annotation_metadata or {}
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
                                  "variant without it and cannot speak to F3",
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
                    "probe's white filter, so this capture measures V0's "
                    "suppression rather than the variant's"
                    if expected_mode == "external" else
                    "the annotation pass did not apply its own suppression"),
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


def paper_margin_ft(paper_in, view_scale):
    """Printed inches -> model feet at this view's scale."""
    scale = float(view_scale)
    if scale <= 0.0:
        raise ValueError("view_scale must be positive, got {0!r}".format(view_scale))
    return (float(paper_in) / 12.0) * scale


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


def expanded_frame_uv(frame_uv, driver_rects, margin_ft):
    """B' = B union every driver rectangle, then a uniform margin.

    ``driver_rects`` is ``[(key, (xmin, ymin, xmax, ymax)), ...]``. A driver
    with no rectangle is the CALLER's to record; this function only ever sees
    rectangles, and every one it sees participates.

    Returns ``(frame_prime_uv, per_side_delta, setters)``:

        per_side_delta  {"left","right","bottom","top"} in feet, each >= 0 --
                        how far B' extends past B on that side, margin
                        included.
        setters         {"left": key or None, ...} -- WHICH driver put the
                        edge where it is, before the margin was added. None
                        means no driver reached past B on that side and the
                        margin alone moved it.

    THE MARGIN IS APPLIED AFTER THE UNION, once, on all four sides. Applying
    it per driver would scale it by the number of drivers on the extreme,
    which is the "a quantity computed in two places" shape: the same 0.5"
    would land as 0.5" on a side with one driver and 0.5" on a side with
    twelve only by accident of which one won.
    """
    x0, y0, x1, y1 = (float(v) for v in frame_uv)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(
            "frame_uv is degenerate: {0}".format(tuple(float(v) for v in frame_uv)))
    margin = float(margin_ft)
    if margin < 0.0:
        raise ValueError("margin_ft must be >= 0, got {0!r}".format(margin_ft))

    setters = {"left": None, "right": None, "bottom": None, "top": None}
    ux0, uy0, ux1, uy1 = x0, y0, x1, y1
    for key, rect in driver_rects or []:
        if rect is None:
            continue
        rx0, ry0, rx1, ry1 = (float(v) for v in rect)
        if rx0 < ux0:
            ux0 = rx0
            setters["left"] = key
        if ry0 < uy0:
            uy0 = ry0
            setters["bottom"] = key
        if rx1 > ux1:
            ux1 = rx1
            setters["right"] = key
        if ry1 > uy1:
            uy1 = ry1
            setters["top"] = key

    prime = (ux0 - margin, uy0 - margin, ux1 + margin, uy1 + margin)
    delta = {
        "left": x0 - prime[0],
        "bottom": y0 - prime[1],
        "right": prime[2] - x1,
        "top": prime[3] - y1,
    }
    return (prime, delta, setters)


def bbox_excursion_past_frame(frame_uv, rects):
    """Per side, the FURTHEST any rectangle reaches past the frame, in feet.

    0.0 on a side means every rectangle stayed inside it. Reported beside the
    analyzer's fitted margins so "the boxes say the frame should be this much
    bigger" and "the image behaves as if it were" are two numbers a reader can
    put side by side -- which is how F1's per-side margins matched on 10 of 12
    sides in the first place.
    """
    x0, y0, x1, y1 = (float(v) for v in frame_uv)
    out = {"left": 0.0, "right": 0.0, "bottom": 0.0, "top": 0.0, "count": 0}
    for rect in rects or []:
        if rect is None:
            continue
        rx0, ry0, rx1, ry1 = (float(v) for v in rect)
        out["count"] += 1
        out["left"] = max(out["left"], x0 - rx0)
        out["bottom"] = max(out["bottom"], y0 - ry0)
        out["right"] = max(out["right"], rx1 - x1)
        out["top"] = max(out["top"], ry1 - y1)
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
    unavailable with the reason. It never reports 0: a 0 that was never read
    is indistinguishable from a view whose offsets really are 0, and
    V0-offsets0's entire content is the difference between those two.
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


def set_annotation_crop_offsets(view, values):
    """Write the four offsets. Must be called inside an open Transaction.

    Raises on the first failure rather than partially applying: three of four
    offsets zeroed is a state nothing asked for and nothing would recognise.
    """
    manager = view.GetCropRegionShapeManager()
    for name in ANNOTATION_CROP_OFFSET_PROPERTIES:
        setattr(manager, name, float(values[name]))


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
# THE WHITE MODEL FILTER (V1/V2/V3)
# ======================================================================

def white_filter_categories(doc, include_view_only_model=True):
    """The MODEL categories a ``ParameterFilterElement`` can be built over.

    Returns a record, not a bare list, because four different populations
    matter to reading V1 and three of them are easy to lose:

        filterable_model_ids      what the filter is actually built over
        rejected_non_filterable   MODEL categories Revit will not filter, so
                                  the white override CANNOT reach them --
                                  anything they draw stays visible in the
                                  annotation capture, which is the first
                                  thing to check if V1's image is not clean
        view_only_model_ids       categories carrying a Model label that are
                                  ANNOTATION content in every sense that
                                  matters here -- Detail Items and the shared
                                  model/detail Lines category. Production's
                                  ``_model_category_hidden_state`` deliberately
                                  EXCLUDES these. This function includes them
                                  by default because the probe brief says
                                  "all filterable MODEL categories", and
                                  whiting them out therefore ERASES real
                                  annotation ink. Named and counted so that
                                  reads as a known consequence rather than a
                                  surprise; ``include_view_only_model=False``
                                  runs it the other way in one re-run.
        unreadable                categories whose id or type would not read

    UNCONFIRMED: ``ParameterFilterUtilities.GetAllFilterableCategories()``.
    An absent surface is an explicit refusal (the caller stops before V1-V3),
    never an empty category list that would build a filter over nothing and
    render a capture that looks like V0.
    """
    from Autodesk.Revit.DB import BuiltInCategory, CategoryType
    try:
        from Autodesk.Revit.DB import ParameterFilterUtilities
        filterable = set()
        for eid in ParameterFilterUtilities.GetAllFilterableCategories():
            value = _element_id_int(eid)
            if value is not None:
                filterable.add(value)
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "ParameterFilterUtilities.GetAllFilterableCategories() "
                          "raised {0}: {1} (UNCONFIRMED API); V1-V3 cannot build "
                          "the white filter".format(type(ex).__name__, ex)}

    view_only_ids = set()
    for bic_name in ("OST_DetailComponents", "OST_Lines"):
        bic = getattr(BuiltInCategory, bic_name, None)
        if bic is not None:
            view_only_ids.add(int(bic))

    record = {
        "state": "value",
        "filterable_model_ids": [],
        "filterable_model_names": {},
        "rejected_non_filterable": [],
        "view_only_model_ids": [],
        "unreadable": [],
        "include_view_only_model": bool(include_view_only_model),
    }
    for cat in doc.Settings.Categories:
        try:
            cat_id = _element_id_int(cat.Id)
            is_model = cat.CategoryType == CategoryType.Model
            name = str(getattr(cat, "Name", "") or "")
        except Exception as ex:
            record["unreadable"].append(
                {"error": "{0}: {1}".format(type(ex).__name__, ex)})
            continue
        if cat_id is None:
            record["unreadable"].append({"error": "category id would not read as int",
                                         "name": name})
            continue
        if not is_model:
            continue
        if cat_id not in filterable:
            record["rejected_non_filterable"].append({"id": cat_id, "name": name})
            continue
        if cat_id in view_only_ids:
            record["view_only_model_ids"].append({"id": cat_id, "name": name})
            if not include_view_only_model:
                continue
        record["filterable_model_ids"].append(cat_id)
        record["filterable_model_names"][str(cat_id)] = name
    record["filterable_model_ids"].sort()
    record["category_count"] = len(record["filterable_model_ids"])
    return record


# Every ``OverrideGraphicSettings`` member the white override needs, as a flat
# list so a MISSING one is nameable rather than an AttributeError thrown from
# somewhere inside a loop. UNCONFIRMED as a set on this Revit host, which is
# exactly why it is preflighted.
WHITE_OVERRIDE_SETTERS = (
    "SetProjectionLineColor", "SetCutLineColor",
    "SetSurfaceForegroundPatternId", "SetSurfaceForegroundPatternColor",
    "SetSurfaceForegroundPatternVisible",
    "SetSurfaceBackgroundPatternId", "SetSurfaceBackgroundPatternColor",
    "SetSurfaceBackgroundPatternVisible",
    "SetCutForegroundPatternId", "SetCutForegroundPatternColor",
    "SetCutForegroundPatternVisible",
    "SetCutBackgroundPatternId", "SetCutBackgroundPatternColor",
    "SetCutBackgroundPatternVisible",
    "SetSurfaceTransparency", "SetHalftone",
)


def missing_override_setters(ogs_like, setters=None):
    """PURE. Which of ``setters`` ``ogs_like`` does not expose.

    Split out of the preflight below so the DECISION is testable without a
    Revit host -- the preflight's own value is that it decides correctly, and a
    function that can only be exercised inside Revit is a function whose
    decision is never checked.
    """
    return [name for name in (setters or WHITE_OVERRIDE_SETTERS)
            if getattr(ogs_like, name, None) is None]


def white_override_capability_record(
        ogs_like, solid_pattern_id, solid_pattern_error=None):
    """PURE. The capability record, from an OGS-like object and a pattern id.

    ``state`` is "value" only when every setter resolves AND a solid pattern id
    was supplied. Anything else LISTS what is missing, because "V1-V3 were
    skipped" is not actionable and "SetCutBackgroundPatternId is absent on this
    host" is.
    """
    missing = missing_override_setters(ogs_like)
    reason_parts = []
    if missing:
        reason_parts.append(
            "OverrideGraphicSettings is missing: " + ", ".join(missing))
    if solid_pattern_error:
        reason_parts.append(str(solid_pattern_error))
    elif solid_pattern_id is None:
        reason_parts.append(
            "no solid DRAFTING fill pattern exists in this project, so a white "
            "surface/cut pattern override cannot be built")
    if reason_parts:
        return {"state": "unavailable", "reason": "; ".join(reason_parts),
                "missing_setters": missing,
                "solid_pattern_reason": (
                    str(solid_pattern_error) if solid_pattern_error
                    else (None if solid_pattern_id is not None
                          else reason_parts[-1]))}
    return {"state": "value", "missing_setters": [],
            "solid_pattern_id": solid_pattern_id}


def white_override_capability(doc):
    """PREFLIGHT: can a white override actually be built on this host?

    Opens no transaction and touches no view -- an ``OverrideGraphicSettings``
    is a plain API object -- so this runs before the first variant and decides
    whether V1-V3 run at all.

    This exists because of what the alternative looks like. A missing pattern
    setter leaves that pattern UNCHANGED rather than raising at the point of
    use, so a filter built without it renders model fills in their authored
    colour while the sidecar says a white filter was applied: a variant that
    measured nothing and reported success, which is the single worst outcome a
    probe can produce. Likewise a project with no solid drafting pattern.

    Returns a record. ``state`` is "value" only when every setter in
    WHITE_OVERRIDE_SETTERS resolves AND a solid pattern was found. Otherwise it
    LISTS what is missing and V1-V3 are skipped with that list, which is the
    probe brief's "stop before V2/V3" rather than a best effort.
    """
    from Autodesk.Revit.DB import OverrideGraphicSettings
    from vop_interwoven.color_id_buffer import _get_solid_pattern_id

    try:
        probe_ogs = OverrideGraphicSettings()
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "OverrideGraphicSettings() raised {0}: {1}".format(
                    type(ex).__name__, ex),
                "missing_setters": list(WHITE_OVERRIDE_SETTERS)}
    solid_id = None
    solid_error = None
    try:
        solid_id = _get_solid_pattern_id(doc)
    except Exception as ex:
        solid_error = "_get_solid_pattern_id raised {0}: {1}".format(
            type(ex).__name__, ex)
    return white_override_capability_record(
        probe_ogs,
        None if solid_id is None else _element_id_int(solid_id),
        solid_pattern_error=solid_error)


def _white_override_settings(doc):
    """A white ``OverrideGraphicSettings``: lines, and all four patterns.

    Assumes ``white_override_capability(doc)`` already said "value"; it raises
    rather than degrading if that is not so, because a partially-applied white
    override is the failure mode the preflight exists to prevent.
    """
    from Autodesk.Revit.DB import Color, OverrideGraphicSettings
    # Production's lookup, CALLED. A second copy here would be a second answer
    # to "which pattern is the solid one", and this module's whole reason for
    # existing is that the annotation capture and its evidence agree.
    from vop_interwoven.color_id_buffer import _get_solid_pattern_id
    capability = white_override_capability(doc)
    if capability.get("state") != "value":
        raise RuntimeError(
            "the white override cannot be built on this host: {0}".format(
                capability.get("reason")))
    solid_id = _get_solid_pattern_id(doc)
    white = Color(255, 255, 255)
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(white)
    ogs.SetCutLineColor(white)
    for setter_id, setter_color, setter_visible in (
            ("SetSurfaceForegroundPatternId", "SetSurfaceForegroundPatternColor",
             "SetSurfaceForegroundPatternVisible"),
            ("SetSurfaceBackgroundPatternId", "SetSurfaceBackgroundPatternColor",
             "SetSurfaceBackgroundPatternVisible"),
            ("SetCutForegroundPatternId", "SetCutForegroundPatternColor",
             "SetCutForegroundPatternVisible"),
            ("SetCutBackgroundPatternId", "SetCutBackgroundPatternColor",
             "SetCutBackgroundPatternVisible")):
        getattr(ogs, setter_id)(solid_id)
        getattr(ogs, setter_color)(white)
        getattr(ogs, setter_visible)(True)
    ogs.SetSurfaceTransparency(0)
    ogs.SetHalftone(False)
    return ogs


def create_white_model_filter(doc, view, category_ids, name):
    """Create a RULE-LESS white filter and apply it to ``view``.

    Must be called inside an open Transaction. Returns
    ``(filter_element_id_int, applied_override)``.

    Rule-less on purpose: a ``ParameterFilterElement`` with no rules matches
    EVERY element of its categories, which is exactly "all model content" and
    needs no parameter that every category happens to share.
    """
    from Autodesk.Revit.DB import ElementId, ParameterFilterElement
    from System.Collections.Generic import List as NetList

    ids = NetList[ElementId]()
    for value in category_ids:
        ids.Add(ElementId(int(value)))
    filter_element = ParameterFilterElement.Create(doc, str(name), ids)
    view.AddFilter(filter_element.Id)
    ogs = _white_override_settings(doc)
    view.SetFilterOverrides(filter_element.Id, ogs)
    view.SetFilterVisibility(filter_element.Id, True)
    view.SetIsFilterEnabled(filter_element.Id, True)
    return (_element_id_int(filter_element.Id), True)


def delete_filter(doc, view, filter_id_int):
    """Remove the filter from the view and delete the element.

    Must be called inside an open Transaction. Both halves are attempted and
    the second runs even if the first raised: a filter element left in the
    project is project-wide contamination, which is worse than a stale
    view-filter association.
    """
    from Autodesk.Revit.DB import ElementId
    eid = ElementId(int(filter_id_int))
    errors = []
    try:
        view.RemoveFilter(eid)
    except Exception as ex:
        errors.append("RemoveFilter: {0}: {1}".format(type(ex).__name__, ex))
    try:
        doc.Delete(eid)
    except Exception as ex:
        errors.append("Delete: {0}: {1}".format(type(ex).__name__, ex))
    if errors:
        raise RuntimeError("; ".join(errors))


def filter_element_exists(doc, filter_id_int):
    """Does the ParameterFilterElement still exist in the PROJECT?

    ``None`` when there was no filter to check or the question could not be
    answered -- which ``restore_readback_verdict`` treats as ``unverified``, not
    as deleted. "Could not tell" is not "it is gone".

    Asked of the document rather than inferred from the view's filter list,
    because ``RemoveFilter`` succeeding and ``doc.Delete`` failing leaves the id
    absent from the view and the element alive in the project.
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
    annotation capture. Recorded per instance so a V1 image that is not clean
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


# ======================================================================
# B' -- THE EXPANDED ANNOTATION FRAME (V3)
# ======================================================================

def viewer_extent_elements(doc, view):
    """Rendered viewer elements whose extent B' must hold.

    Returns ``(elements, report)``. ``report`` names every
    ``BuiltInCategory`` that did NOT resolve on this host, so a category that
    contributed nothing because it does not exist is distinguishable from one
    the view simply has none of -- the "incomplete token set is the same
    defect as an incomplete pattern" lesson from CLAUDE.md, applied to a
    category list.

    These are collected SEPARATELY from the annotation-pass members because
    they are included for EXTENT ONLY: the annotation pass does not paint
    them, and this function does not make them painted.
    """
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    resolved = {}
    unresolved = []
    for name in VIEWER_EXTENT_BIC_NAMES:
        bic = getattr(BuiltInCategory, name, None)
        if bic is None:
            unresolved.append(name)
            continue
        resolved[int(bic)] = name
    report = {"resolved_categories": dict((str(k), v) for k, v in resolved.items()),
              "unresolved_category_names": unresolved,
              "per_category_counts": dict((v, 0) for v in resolved.values())}
    elements = []
    if not resolved:
        report["collection"] = _unavailable(
            "none of the viewer BuiltInCategory names resolved on this Revit "
            "host, so no viewer element contributes to B'")
        return (elements, report)
    try:
        candidates = FilteredElementCollector(
            doc, view.Id).WhereElementIsNotElementType().ToElements()
    except Exception as ex:
        report["collection"] = _unavailable(
            "FilteredElementCollector over the view raised {0}: {1}".format(
                type(ex).__name__, ex))
        return (elements, report)
    unreadable = 0
    for elem in candidates:
        try:
            cat = elem.Category
            cat_id = _element_id_int(cat.Id) if cat is not None else None
        except Exception:
            unreadable += 1
            continue
        if cat_id in resolved:
            elements.append(elem)
            report["per_category_counts"][resolved[cat_id]] += 1
    report["unreadable_category_count"] = unreadable
    report["collection"] = _value(len(elements))
    return (elements, report)


def frame_prime_drivers(doc, view, view_basis, anno_elements, viewer_elements,
                        diag=None, view_id=None):
    """Every driver rectangle for B', in absolute view UV, plus the misses.

    ``get_BoundingBox(view)`` and an AABB projection only -- no geometry API
    is touched, so this stays inside Stage A's geometry-free contract even
    though the probe is not itself scanned by
    ``tools/check_stage_a_no_geometry.py``.

    Returns ``(driver_rects, no_bbox)``. ``no_bbox`` lists every element that
    contributed nothing AND WHY. An element is never dropped silently: an
    unmeasurable driver is the single most likely way B' comes back too small
    while looking computed.
    """
    from vop_interwoven.revit.collection import (
        project_bbox_corners_uv, resolve_element_bbox,
    )

    driver_rects = []
    no_bbox = []
    for role, elements in (("annotation_member", anno_elements),
                           ("viewer", viewer_elements)):
        for elem in elements or []:
            elem_id = _element_id_int(getattr(elem, "Id", None))
            key = "{0}:{1}".format(role, elem_id)
            try:
                bbox, bbox_source = resolve_element_bbox(
                    elem, view=view, diag=diag,
                    context={"view_id": view_id, "elem_id": elem_id,
                             "source_type": "HOST"})
            except Exception as ex:
                no_bbox.append({"key": key, "role": role, "element_id": elem_id,
                                "reason": "resolve_element_bbox raised {0}: {1}".format(
                                    type(ex).__name__, ex)})
                continue
            if bbox is None:
                no_bbox.append({"key": key, "role": role, "element_id": elem_id,
                                "reason": "no bbox from get_BoundingBox(view) or "
                                          "get_BoundingBox(None)",
                                "bbox_source": bbox_source})
                continue
            if view_basis is None:
                no_bbox.append({"key": key, "role": role, "element_id": elem_id,
                                "reason": "bbox resolved but this run has no view "
                                          "basis to project it through",
                                "bbox_source": bbox_source})
                continue
            corners = project_bbox_corners_uv(
                bbox, view_basis, diag=diag, view_id=view_id, elem_id=elem_id)
            rect = rect_from_corners(corners)
            if rect is None:
                no_bbox.append({"key": key, "role": role, "element_id": elem_id,
                                "reason": "bbox and basis both present but the UV "
                                          "projection produced no rectangle",
                                "bbox_source": bbox_source})
                continue
            driver_rects.append((key, rect))
    return (driver_rects, no_bbox)


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

    The V1-V3 restore contract is that model category visibility is NEVER
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
    """The five properties the restore contract covers, read in one place.

    One function, so the "before", "after explicit restore" and "after
    rollback" readings are the SAME reading of the SAME properties. Three
    independently-written snapshot functions would let the diff pass because
    two of them agreed about a field the third never read.
    """
    return {
        "crop_box": _crop_box_record(view),
        "smooth_edges": _smooth_edges_record(view),
        "annotation_crop_offsets": annotation_crop_offsets(view),
        "annotation_crop_active": annotation_crop_active(view),
        "model_category_visibility": _model_category_visibility_record(doc, view),
        "view_filters": _view_filter_record(view),
        "view_template_id": _element_id_int(getattr(view, "ViewTemplateId", None)),
        "display_style": str(getattr(view, "DisplayStyle", None)),
    }


def restore_readback_verdict(before, after, expect_filter_absent=None,
                             filter_element_still_in_project=None,
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
    for name in ("crop_box", "smooth_edges", "annotation_crop_offsets",
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

    if expect_filter_absent is not None:
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
        if a_filters.get("state") != "value":
            verdict["probe_filter_deleted"] = {
                "status": "unverified",
                "reason": a_filters.get("reason") or "view filters unreadable"}
        elif str(int(expect_filter_absent)) in (a_filters.get("value") or {}):
            verdict["probe_filter_deleted"] = {
                "status": "not_restored",
                "reason": "the probe's white filter {0} is still on the view".format(
                    expect_filter_absent)}
        elif filter_element_still_in_project is True:
            verdict["probe_filter_deleted"] = {
                "status": "not_restored",
                "reason": "the probe's white filter {0} was removed from the view "
                          "but the ParameterFilterElement still exists in the "
                          "project; this capture left project-wide "
                          "contamination".format(expect_filter_absent)}
        elif filter_element_still_in_project is None:
            verdict["probe_filter_deleted"] = {
                "status": "unverified",
                "reason": "the filter is off the view, but whether the "
                          "ParameterFilterElement itself was deleted could not be "
                          "determined"}
        else:
            verdict["probe_filter_deleted"] = {"status": "restored"}

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
    """A Config for one variant, with the two probe-only switches SET.

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

def _run_variant(doc, view, variant, model_context, settings):
    """Run one variant end to end, and put the view back.

    Structure, in order, and every step is recorded whether or not it
    succeeded:

      1  snapshot BEFORE
      2  TransactionGroup.Start
      3  a committed child Transaction applies this variant's PRE-state
         (zeroed offsets, or the white filter)
      4  production's export_annotation_color_id_buffer_view runs, with no
         child transaction open
      5  a committed child Transaction reverses step 3
      6  snapshot, and read-back verdict -- THE REAL MEASUREMENT, taken
         before the rollback so it is a verdict on the explicit restore
      7  TransactionGroup.RollBack in ``finally``
      8  snapshot again, and a second read-back verdict -- the safety net

    Step 6 is the one the restore contract is judged on. Step 8 exists so
    that a failure at step 5 still leaves the document as found, and so the
    two can be told apart: an explicit restore that failed while the rollback
    saved it is a probe defect worth fixing, not a clean run.
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

    group = None
    started = False
    filter_id = None
    offsets_before = None
    offsets_zeroed = False
    snapshot_before = None

    try:
        _force_close_dynamo_transaction()
        snapshot_before = snapshot_view(doc, view)
        report["snapshot_before"] = snapshot_before

        # ---- variants that cannot run on this view ---------------------
        if plan["zero_annotation_crop_offsets"]:
            offsets_before = snapshot_before.get("annotation_crop_offsets") or {}
            if offsets_before.get("state") != "value":
                report["skipped"] = True
                report["skip_reason"] = (
                    "the annotation crop offsets could not be READ on this view, so "
                    "zeroing them would be writing over a state this run never "
                    "observed: {0}".format(offsets_before.get("reason")))
                report["conclusion"] = "UNAVAILABLE"
                return report

        if plan["white_filter"]:
            capability = model_context["white_override_capability"]
            if capability.get("state") != "value":
                report["skipped"] = True
                report["skip_reason"] = (
                    "the white override cannot be built on this Revit host, so "
                    "this variant would apply a filter that changes nothing and "
                    "render an image indistinguishable from V0's: {0}".format(
                        capability.get("reason")))
                report["white_override_capability"] = capability
                report["conclusion"] = "UNAVAILABLE"
                return report
            categories = model_context["white_filter_categories"]
            if categories.get("state") != "value":
                report["skipped"] = True
                report["skip_reason"] = (
                    "the filterable MODEL category set is unavailable, so no white "
                    "filter can be built: {0}".format(categories.get("reason")))
                report["conclusion"] = "UNAVAILABLE"
                return report
            if not categories.get("filterable_model_ids"):
                report["skipped"] = True
                report["skip_reason"] = (
                    "no filterable MODEL category resolved; a filter over an empty "
                    "category set would render exactly like V0 while the sidecar "
                    "said a white filter was applied")
                report["conclusion"] = "UNAVAILABLE"
                return report

        # ---- the frame this variant hands the annotation pass -----------
        if plan["expanded_frame"]:
            frame_record = model_context["frame_prime"]
            if frame_record.get("state") != "value":
                report["skipped"] = True
                report["skip_reason"] = (
                    "B' could not be computed for this view: {0}".format(
                        frame_record.get("reason")))
                report["conclusion"] = "UNAVAILABLE"
                return report
            geom = frame_record["geom"]
            report["frame"] = frame_record["record"]
        else:
            geom = model_context["geom"]
            report["frame"] = {"frame_source": "model_pass_geom",
                               "frame_snapped_uv": [float(v) for v in
                                                    geom["frame_snapped_uv"]],
                               "frame_px": [int(v) for v in geom["frame_px"]]}

        group = TransactionGroup(doc, "VOP Stage A anno variant: " + variant)
        status = group.Start()
        started = status == TransactionStatus.Started
        report["transaction_group"]["start_status"] = str(status)
        if not started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(status))

        # ---- 3: this variant's PRE-state, committed ---------------------
        pre_tx = Transaction(doc, "VOP Stage A anno variant pre-state: " + variant)
        pre_tx.Start()
        try:
            if plan["zero_annotation_crop_offsets"]:
                zeroed = dict((name, 0.0) for name in ANNOTATION_CROP_OFFSET_PROPERTIES)
                set_annotation_crop_offsets(view, zeroed)
                offsets_zeroed = True
                report["pre_state"]["annotation_crop_offsets_before"] = (
                    offsets_before.get("value"))
                report["pre_state"]["annotation_crop_offsets_requested"] = zeroed
            if plan["white_filter"]:
                categories = model_context["white_filter_categories"]
                filter_id, applied = create_white_model_filter(
                    doc, view, categories["filterable_model_ids"],
                    "VOP PROBE white model suppression {0} {1}".format(
                        variant, int(time.time())))
                report["pre_state"]["white_filter"] = {
                    "filter_element_id": filter_id,
                    "overrides_applied": bool(applied),
                    "category_count": categories["category_count"],
                    "rejected_non_filterable": categories["rejected_non_filterable"],
                    "view_only_model_categories_included": (
                        categories["view_only_model_ids"]
                        if categories["include_view_only_model"] else []),
                    "view_only_model_categories_excluded": (
                        [] if categories["include_view_only_model"]
                        else categories["view_only_model_ids"]),
                    "note": "Detail Items and the shared model/detail Lines category "
                            "carry a Model label but are ANNOTATION content. When "
                            "included, this filter whites them out and REAL "
                            "ANNOTATION INK IS LOST. Production's model-category "
                            "hiding deliberately excludes them. Listed either way "
                            "so the choice is visible in the record.",
                    "link_visibility": model_context["link_visibility"],
                    "authored_model_overrides": model_context["authored_model_overrides"],
                }
            commit = pre_tx.Commit()
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

        # ---- 4: production's annotation pass ---------------------------
        report["transaction_group"]["no_child_transaction_open_at_export"] = (
            not bool(doc.IsModifiable))
        variant_dir = os.path.join(settings["probe_dir"], variant)
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
                               else (anno_out.get("metadata") or {}).get(
                                   "capture_faults")),
            "applied_smooth_edges": (None if anno_out is None
                                     else (anno_out.get("metadata") or {}).get(
                                         "applied_smooth_edges")),
            "model_suppression_mode": (None if anno_out is None
                                       else (anno_out.get("metadata") or {}).get(
                                           "model_suppression_mode")),
        }
        if anno_out is not None and anno_out.get("tiff_path"):
            report["annotation_pass"]["tiff_sha256"] = _sha256(anno_out["tiff_path"])

        # ---- 5: reverse step 3, explicitly ----------------------------
        restore_errors = []
        restore_tx = Transaction(doc, "VOP Stage A anno variant restore: " + variant)
        restore_tx.Start()
        if filter_id is not None:
            try:
                delete_filter(doc, view, filter_id)
            except Exception as ex:
                restore_errors.append({"step": "delete_white_filter",
                                       "error": "{0}: {1}".format(
                                           type(ex).__name__, ex)})
        if offsets_zeroed and offsets_before.get("state") == "value":
            try:
                set_annotation_crop_offsets(view, offsets_before["value"])
            except Exception as ex:
                restore_errors.append({"step": "restore_annotation_crop_offsets",
                                       "error": "{0}: {1}".format(
                                           type(ex).__name__, ex)})
        try:
            commit = restore_tx.Commit()
            report["transaction_group"]["restore_commit_status"] = str(commit)
            if commit != TransactionStatus.Committed:
                restore_errors.append({
                    "step": "restore_commit",
                    "error": "Transaction.Commit returned {0}".format(commit)})
        except Exception as ex:
            try:
                restore_tx.RollBack()
            except Exception as inner:
                restore_errors.append({"step": "restore_rollback",
                                       "error": "{0}: {1}".format(
                                           type(inner).__name__, inner)})
            restore_errors.append({"step": "restore_commit",
                                   "error": "{0}: {1}".format(type(ex).__name__, ex)})
        report["restore"]["explicit_step_errors"] = restore_errors

        # ---- 6: THE MEASUREMENT --------------------------------------
        snapshot_after_restore = snapshot_view(doc, view)
        report["snapshot_after_explicit_restore"] = snapshot_after_restore
        element_still_there = filter_element_exists(doc, filter_id)
        report["restore"]["probe_filter_element_still_in_project"] = (
            element_still_there)
        report["restore"]["after_explicit_restore"] = restore_readback_verdict(
            snapshot_before, snapshot_after_restore, expect_filter_absent=filter_id,
            filter_element_still_in_project=element_still_there,
            explicit_step_errors=restore_errors)

    except Exception as ex:
        report["exceptions"].append(_exception_record("variant", ex))
    finally:
        # ---- 7: the safety net ---------------------------------------
        if started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                status = group.RollBack()
                report["transaction_group"]["rollback_status"] = str(status)
                report["transaction_group"]["rollback_succeeded"] = (
                    status == TransactionStatus.RolledBack)
            except Exception as ex:
                report["exceptions"].append(_exception_record("group_rollback", ex))
                report["transaction_group"]["rollback_succeeded"] = False

        # ---- 8: read-back after the rollback -------------------------
        if snapshot_before is not None and not report.get("skipped"):
            try:
                snapshot_after_rollback = snapshot_view(doc, view)
                report["snapshot_after_rollback"] = snapshot_after_rollback
                # No explicit_step_errors here: the rollback is the safety net,
                # and whether it rescued a failed explicit step is exactly the
                # distinction between the two verdicts. Passing them would make
                # the net report the failure it just undid.
                report["restore"]["after_rollback"] = restore_readback_verdict(
                    snapshot_before, snapshot_after_rollback,
                    expect_filter_absent=filter_id,
                    filter_element_still_in_project=filter_element_exists(
                        doc, filter_id))
            except Exception as ex:
                report["exceptions"].append(_exception_record("post_rollback_snapshot", ex))

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
            explicit = (report["restore"].get("after_explicit_restore") or {}).get(
                "overall")
            rolled_back = (report["restore"].get("after_rollback") or {}).get("overall")
            rollback_ok = report["transaction_group"].get("rollback_succeeded")

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
                and explicit not in ("not_restored", "unverified", None)
                and rolled_back not in ("not_restored", "unverified", None))
            report["document_safe_detail"] = {
                "rollback_succeeded": rollback_ok,
                "after_explicit_restore": explicit,
                "after_rollback": rolled_back,
            }

            # DID IT MEASURE WHAT IT CLAIMS? A TIFF existing proves an export
            # happened, not that the variant's mutation was applied -- and
            # both switches can decline without raising. See
            # variant_measurement_check.
            measurement = variant_measurement_check(
                plan, (report["annotation_pass"] or {}))
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


def _build_frame_prime(doc, view, geom, view_basis, margin_in, diag, view_id,
                       cap_axis_px, export_dpi, fit_direction, scale):
    """B', and the frame geometry V3 hands the annotation pass.

    Returns a record whose ``state`` is "value" only when B' could actually be
    computed AND sized. Everything about how it was reached is in ``record``:
    B, B', the per-side delta, which element set each side, every driver that
    contributed no rectangle and why.

    THE CAP IS NOT RE-IMPLEMENTED. B' goes through production's
    ``frame_export_geometry`` with the same dpi, the same fit direction and
    the same per-axis ceiling, so if B' exceeds the ceiling feet-per-pixel
    grows exactly as it does for B -- "the existing pixel cap on B applies
    unchanged", by calling the thing that applies it.
    """
    from vop_interwoven.resolution_contract import frame_export_geometry
    from vop_interwoven.revit.annotation import split_stage_a_pass_membership
    from Autodesk.Revit.DB import FilteredElementCollector

    try:
        elements = list(FilteredElementCollector(
            doc, view.Id).WhereElementIsNotElementType())
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "could not collect the view's elements for B': {0}: {1}".format(
                    type(ex).__name__, ex)}

    _model, anno_elements, _unresolved, _basis = split_stage_a_pass_membership(
        elements, capture_view_id_int=view_id, diag=diag)
    viewer_elements, viewer_report = viewer_extent_elements(doc, view)

    driver_rects, no_bbox = frame_prime_drivers(
        doc, view, view_basis, anno_elements, viewer_elements,
        diag=diag, view_id=view_id)

    frame_uv = tuple(float(v) for v in geom["frame_snapped_uv"])
    margin_ft = paper_margin_ft(margin_in, scale)
    try:
        prime_uv, delta, setters = expanded_frame_uv(
            frame_uv, driver_rects, margin_ft)
    except ValueError as ex:
        return {"state": "unavailable",
                "reason": "B' could not be formed: {0}".format(ex)}

    record = {
        "frame_source": "probe_expanded_frame_uv",
        "margin_paper_in": float(margin_in),
        "margin_ft": margin_ft,
        "frame_b_uv": list(frame_uv),
        "frame_b_prime_uv": list(prime_uv),
        "per_side_delta_ft": delta,
        "per_side_delta_paper_in": dict(
            (side, value * 12.0 / scale) for side, value in delta.items()),
        "per_side_set_by": setters,
        "driver_count": len(driver_rects),
        "annotation_member_count": len(anno_elements),
        "viewer_element_count": len(viewer_elements),
        "viewer_categories": viewer_report,
        "drivers_without_bbox": no_bbox,
        "drivers_without_bbox_count": len(no_bbox),
        "bbox_excursion_past_b_ft": bbox_excursion_past_frame(
            frame_uv, [rect for _key, rect in driver_rects]),
    }

    crop_uv = tuple(float(v) for v in geom["crop_snapped_uv"])
    try:
        prime_geom = frame_export_geometry(
            prime_uv, crop_uv, scale, export_dpi,
            fit_direction=fit_direction, max_axis_px=cap_axis_px)
    except ValueError as ex:
        return {"state": "unavailable",
                "reason": "frame_export_geometry refused B': {0}".format(ex),
                "record": record}

    prime_geom = dict(prime_geom)
    # DELIBERATELY None, and this is the one place V3 differs from a shipped
    # capture in a way a reader must not miss.
    #
    # Production's annotation pass raises the `annotation_lattice_mismatch`
    # fault when geom["model_accepted_px"] != geom["requested_px"] -- "the
    # model capture is off-lattice, so this annotation capture cannot register
    # against it". Under V3 those two numbers describe DIFFERENT FRAMES: the
    # model pass sized itself from B, this geometry is sized from B'. Copying
    # the model pass's accepted width in here would compare a measurement of
    # one frame against a request for another and report a fault that means
    # nothing. Setting it to None makes production skip that check, which is
    # correct: the question is NOT ANSWERED for V3, and "not answered" is not
    # "passed". The real relationship between the two lattices is recorded
    # below, in full, for Greg to read.
    prime_geom["model_accepted_px"] = None
    prime_geom["model_dim_check"] = None
    record["lattice_relationship"] = {
        "note": "V3 renders B', the model pass rendered from B. These are the two "
                "lattices; production's model-lattice fault check is passed None "
                "and therefore skipped, because it would be comparing different "
                "frames.",
        "model_frame_px": [int(v) for v in geom["frame_px"]],
        "model_achieved_fpp_ft": float(geom["achieved_fpp_ft"]),
        "model_achieved_export_dpi": float(geom["achieved_export_dpi"]),
        "model_accepted_px": geom.get("model_accepted_px"),
        "model_requested_px": int(geom["requested_px"]),
        "b_prime_frame_px": [int(v) for v in prime_geom["frame_px"]],
        "b_prime_achieved_fpp_ft": float(prime_geom["achieved_fpp_ft"]),
        "b_prime_achieved_export_dpi": float(prime_geom["achieved_export_dpi"]),
        "b_prime_requested_px": int(prime_geom["requested_px"]),
        "b_prime_cap_applied": bool(prime_geom["cap_applied"]),
        "shared_fpp": (abs(float(geom["achieved_fpp_ft"])
                           - float(prime_geom["achieved_fpp_ft"])) <= 1.0e-12),
    }
    record["frame_snapped_uv"] = [float(v) for v in prime_geom["frame_snapped_uv"]]
    record["frame_px"] = [int(v) for v in prime_geom["frame_px"]]
    return {"state": "value", "geom": prime_geom, "record": record}


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


def _run_native(raw_view, output_dir, selection="all", export_dpi=DEFAULT_EXPORT_DPI,
                expanded_frame_margin_in=DEFAULT_EXPANDED_FRAME_MARGIN_IN,
                authored_override_scan_max=DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX,
                white_filter_include_view_only_model_categories=True,
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
            "expanded_frame_margin_in": float(expanded_frame_margin_in),
            "authored_override_scan_max": int(authored_override_scan_max),
            "white_filter_include_view_only_model_categories": bool(
                white_filter_include_view_only_model_categories),
            "model_reexport_check": bool(model_reexport_check),
        },
        "model_pass": {},
        "variants": [],
        "unconfirmed_api_claims": [
            "ViewCropRegionShapeManager.Left/Right/Top/BottomAnnotationCropOffset "
            "exist and are readable/writable (V0-offsets0 depends entirely on it)",
            "ParameterFilterUtilities.GetAllFilterableCategories() exists "
            "(V1-V3 depend on it)",
            "a rule-less ParameterFilterElement matches every element of its "
            "categories, so white line and pattern overrides suppress all model "
            "content the filter reaches",
            "View.GetLinkOverrides(ElementId).LinkVisibilityType exists, so a link "
            "not displayed By Host View can be reported",
            "setting View.DisplayStyle does not replace the ViewDisplayModel and "
            "discard a SmoothEdges written before it -- the production switch "
            "writes SmoothEdges AFTER DisplayStyle so this does not matter, but "
            "the claim is recorded because the ordering is the mitigation",
            "ExportImage fits the crop box unioned with the extents of annotations "
            "drawn beyond it -- F1's primary hypothesis, which this probe measures "
            "rather than assumes",
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
        model_out = export_color_id_buffer_view(
            doc, view, elements, model_cfg, diag=diag, raster=raster,
            geometry_out=geom)
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
    fit_direction = str(getattr(model_cfg, "color_id_buffer_fit_direction",
                                "horizontal") or "horizontal").strip().lower()
    cap_axis_px = getattr(model_cfg, "color_id_buffer_cap_axis_px", None)
    if cap_axis_px is None:
        from vop_interwoven.color_id_buffer import MAX_STAGE_A_AXIS_PX
        cap_axis_px = MAX_STAGE_A_AXIS_PX

    view_basis = getattr(raster, "view_basis", None)
    if view_basis is None:
        try:
            from vop_interwoven.revit.view_basis import make_view_basis
            view_basis = make_view_basis(view, diag=diag)
        except Exception as ex:
            report.setdefault("warnings", []).append(
                "no view basis: {0}: {1}; B' cannot be computed".format(
                    type(ex).__name__, ex))

    white_capability = white_override_capability(doc)
    white_categories = white_filter_categories(
        doc, include_view_only_model=bool(
            white_filter_include_view_only_model_categories))
    model_element_ids = []
    for elem in elements or []:
        value = _element_id_int(getattr(elem, "Id", None))
        if value is not None:
            model_element_ids.append(value)
    try:
        authored = authored_override_scan(
            view, model_element_ids, authored_override_scan_max)
    except Exception as ex:
        authored = _unavailable("{0}: {1}".format(type(ex).__name__, ex))

    frame_prime = {"state": "unavailable",
                   "reason": "V3 was not requested for this run"}
    if V3 in selected:
        if view_basis is None:
            frame_prime = {"state": "unavailable",
                           "reason": "no view basis, so no driver bbox can be "
                                     "projected into UV and B' would be B"}
        else:
            try:
                frame_prime = _build_frame_prime(
                    doc, view, geom, view_basis,
                    expanded_frame_margin_in, diag, view_id, cap_axis_px,
                    float(export_dpi), fit_direction, scale)
            except Exception as ex:
                frame_prime = {"state": "unavailable",
                               "reason": "B' computation raised {0}: {1}".format(
                                   type(ex).__name__, ex)}
    report["frame_prime"] = frame_prime

    model_context = {
        "diag": diag, "raster": raster, "geom": geom,
        "model_tiff_path": model_tiff_path, "model_tiff_sha256": model_tiff_sha,
        "white_filter_categories": white_categories,
        "white_override_capability": white_capability,
        "link_visibility": link_visibility_report(doc, view),
        "authored_model_overrides": authored,
        "frame_prime": frame_prime,
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
    report["white_filter_categories"] = white_categories
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
              expanded_frame_margin_in=DEFAULT_EXPANDED_FRAME_MARGIN_IN,
              authored_override_scan_max=DEFAULT_AUTHORED_OVERRIDE_SCAN_MAX,
              white_filter_include_view_only_model_categories=True,
              model_reexport_check=True, repo_root=None):
    if repo_root:
        root = os.path.abspath(os.path.expanduser(str(repo_root)))
        if root not in sys.path:
            sys.path.insert(0, root)
    _ensure_contract_import_path(os.path.abspath(str(output_dir or os.getcwd())))
    started_at = _probe_contract().utc_now_iso()
    native = _run_native(
        raw_view, output_dir, selection, export_dpi, expanded_frame_margin_in,
        authored_override_scan_max,
        white_filter_include_view_only_model_categories, model_reexport_check)
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
    return _probe_contract().execution_envelope(
        PROBE_NAME,
        {"selection": [entry.get("variant") for entry in variants],
         "export_dpi": export_dpi,
         "expanded_frame_margin_in": expanded_frame_margin_in,
         "authored_override_scan_max": authored_override_scan_max,
         "white_filter_include_view_only_model_categories": (
             white_filter_include_view_only_model_categories),
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
