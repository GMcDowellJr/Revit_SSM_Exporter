"""Which Stage A capture passes a view needs: the one place the rule is named.

The registered capture (stage_a_registered_capture.py) runs a MODEL pass and an
ANNOTATION pass and registers the two by twelve ticks drawn into both. A view
whose membership proves one pass would capture nothing has nothing to register:
that pass is not run, its TIFF and sidecar are not written, and the view is a
SINGLE-PASS view. The skip is DECLARED -- ``capture_passes`` and
``skipped_pass`` in the sidecar of the pass that ran and in run_meta -- never
left for a reader to infer from a missing file (Greg, 2026-10-22).

THE PREDICATE reads the counts ``revit.annotation.split_stage_a_pass_membership``
produces over ``FilteredElementCollector(doc, view.Id)`` -- the membership rule
(OwnerViewId; datums by category; view markers by category; imports by
``collection_policy.import_pass``):

  * ``model_count``      -- elements placed in the MODEL pass: ownerless model
    elements, RVT link instances, imports not owned by the view (D2, an
    unreadable owner included), a view's own crop-region element and markers
    whose name would not read;
  * ``annotation_count`` -- elements placed in the ANNOTATION pass: everything
    owned by the view (text, tags, dimensions, detail lines and components,
    filled regions, view-owned imports), datums (grids, levels and their
    heads) and view markers (elevation, section, callout, reference viewers);
  * ``unresolved_count`` -- elements whose pass could not be decided;
  * the two category resolutions the rule depends on, and their errors;
  * ``pipeline_model_elements`` -- how many model elements the pipeline handed
    the capture (collection.py's own collector);
  * ``has_crop_region`` -- ``view_basis.supports_crop_region(view)``.

UNKNOWN IS NEVER EMPTY. Both passes run when any count is unreadable, when any
element is unresolved, when either category resolution failed (a datum the rule
could not recognise is counted as model, so annotation could read 0 with grids
drawn), or when the pipeline collected model elements the membership did not.

THE MODEL PASS IS SKIPPED ONLY ON A VIEW WITH NO CROP REGION (a drafting view, a
legend: ``supports_crop_region``, the capability, not the view type). The
annotation pass on such a view is framed by the capture's two white
frame-bound lines, so its extent is crop A without a tick fit (run 1005_0947:
0.52 px). On a view WITH a crop region the annotation export's extent is
FitToPage's -- the crop plus whatever annotation reaches past it -- and only
the ticks measure it, so a cropped view with an empty model membership still
runs both passes, and says why (``model_empty_frame_needs_ticks``).
"""

PASS_MODEL = "model"
PASS_ANNOTATION = "annotation"
BOTH_PASSES = (PASS_MODEL, PASS_ANNOTATION)
SKIP_REASON = "membership_empty"
MEMBERSHIP_RULE = "OwnerViewId+datum_category+view_reference_category"

# Why a view ran both passes. Recorded in run_meta (never in the sidecars of a
# two-pass view, which change only by ``capture_passes``).
BOTH_REASONS = (
    "both_have_members",              # the ordinary view
    "membership_unreadable",          # a count is missing or not a count
    "membership_unresolved",          # an element's pass is unknown
    "membership_rule_incomplete",     # a category resolution failed
    "membership_disagrees",           # pipeline model elements, model count 0
    "model_empty_frame_needs_ticks",  # model 0 on a view with a crop region
    "both_memberships_empty",         # nothing in either pass: run as before
)


def _count(value):
    """``value`` as a count, or None when it is not one (a bool is not)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def plan_capture_passes(counts):
    """PURE. ``{"passes", "decision", "skipped_pass", "counts"}`` for one view.

    ``passes`` is a list in capture order; ``skipped_pass`` is None for a
    two-pass view, else the declared record
    ``{"skipped", "reason", "model_count", "annotation_count", ...}``.
    ``counts`` is a dict (see the module docstring); anything missing or
    unreadable runs both passes.
    """
    counts = counts if isinstance(counts, dict) else {}
    model = _count(counts.get("model_count"))
    annotation = _count(counts.get("annotation_count"))
    unresolved = _count(counts.get("unresolved_count"))
    pipeline = _count(counts.get("pipeline_model_elements"))
    record = {"model_count": model, "annotation_count": annotation,
              "unresolved_count": unresolved, "pipeline_model_elements": pipeline,
              "datum_resolution_error": counts.get("datum_resolution_error"),
              "view_reference_resolution_error": counts.get(
                  "view_reference_resolution_error"),
              "has_crop_region": counts.get("has_crop_region"),
              "membership_rule": MEMBERSHIP_RULE}

    def _both(reason):
        return {"passes": list(BOTH_PASSES), "decision": reason,
                "skipped_pass": None, "counts": record}

    if model is None or annotation is None or unresolved is None or pipeline is None:
        return _both("membership_unreadable")
    if unresolved:
        return _both("membership_unresolved")
    if record["datum_resolution_error"] or record["view_reference_resolution_error"]:
        return _both("membership_rule_incomplete")
    if pipeline and not model:
        return _both("membership_disagrees")
    if not model and not annotation:
        return _both("both_memberships_empty")
    if not model:
        if record["has_crop_region"] is not False:
            return _both("model_empty_frame_needs_ticks")
        return {"passes": [PASS_ANNOTATION], "decision": SKIP_REASON,
                "skipped_pass": skip_record(PASS_MODEL, record), "counts": record}
    if not annotation:
        return {"passes": [PASS_MODEL], "decision": SKIP_REASON,
                "skipped_pass": skip_record(PASS_ANNOTATION, record), "counts": record}
    return _both("both_have_members")


def skip_record(skipped, counts):
    """The declared record of one skipped pass, with the counts that proved it
    empty."""
    return {"skipped": skipped, "reason": SKIP_REASON,
            "model_count": counts["model_count"],
            "annotation_count": counts["annotation_count"],
            "unresolved_count": counts["unresolved_count"],
            "pipeline_model_elements": counts["pipeline_model_elements"],
            "membership_rule": MEMBERSHIP_RULE}


def declared_passes(sidecar):
    """``(passes, skipped_pass, error)`` a sidecar DECLARES. ``(None, None,
    None)`` for one that declares nothing (every capture before the
    single-pass change: both passes). A declaration that is not a list of
    known passes, or a single pass with no matching skip record, is
    ``(None, None, reason)``: a reader must not take a malformed declaration
    as a single pass."""
    if not isinstance(sidecar, dict) or "capture_passes" not in sidecar:
        return None, None, None
    passes = sidecar.get("capture_passes")
    if (not isinstance(passes, list) or not passes
            or any(p not in BOTH_PASSES for p in passes) or len(set(passes)) != len(passes)):
        return None, None, "capture_passes is {0!r}, not a list of passes".format(passes)
    if len(passes) == len(BOTH_PASSES):
        return list(passes), None, None
    skipped = sidecar.get("skipped_pass")
    other = [p for p in BOTH_PASSES if p not in passes][0]
    if (not isinstance(skipped, dict) or skipped.get("skipped") != other
            or skipped.get("reason") != SKIP_REASON):
        return None, None, ("capture_passes is {0!r} but skipped_pass does not declare "
                            "the {1} pass skipped ({2!r})".format(passes, other, skipped))
    return list(passes), skipped, None


def is_single_pass(sidecar, kept):
    """True only when ``sidecar`` DECLARES a single-pass capture that kept
    ``kept`` ("model" or "annotation")."""
    passes, _skipped, _error = declared_passes(sidecar)
    return passes == [kept]
