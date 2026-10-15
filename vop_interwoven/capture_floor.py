"""The capture floor: views the registered Stage A capture cannot measure.

A view can be too small or too thin for the capture's own rules, and that is
a property of the view under this capture design, not a capture that went
wrong and should be run again. Before this module such a view was recorded
as a FAILED capture (``registration_failed`` or ``annotation_failed``), the
analysis never gridded it, and a series showed it as a measure lost in every
run -- 2025-02-03 to 2025-11-01, legends 4622 and 142022 in all 44 runs.

ONE place names the rules and classifies an outcome, so the capture (which
writes the status), the finder (which decides what to recapture) and the
roll-up (which reports the series) cannot each keep their own list:

* ``below_capture_floor`` -- the twelve registration ticks cannot be placed:
  ``stage_a_registration.registration_mark_segments`` refuses a reference
  rectangle whose short side is under ``mark_floor_px()`` (192 px: three
  times inset 24 + gap 8 + minimum arm 32). The registered capture refuses
  BEFORE it exports anything and the view has no files;
* ``annotation_export_degenerate`` -- the annotation export came back at
  Revit's 10:1 aspect clamp, or coarsened four times or more against the
  model capture (``color_id_buffer.annotation_export_degenerate``). Measured
  after the export, so the files exist; they are not a measure.

Both carry ``capture_status`` ``unmeasurable`` in run_meta.json and
``CaptureStatus`` ``unmeasurable`` in views_core (``csv_export.
stage_a_capture_status``), with the rule as the reason, so BI can filter
them out of "gone" and "lost" without reading a sidecar.

Runs written BEFORE this status existed recorded the tick rule as
``registration_failed`` with exactly the faults ``registration_marks_
incomplete;registration_marks_may_not_draw`` (an unavailable layout draws
0 of 0 ticks and never reads the Lines category). That shape alone is a
candidate: a view hiding its lines AND losing a tick records the same two
faults. The model sidecar's ``registration_marks`` record decides it: its
``registration_marks_incomplete`` fault carries the layout's "too small"
reason in its message (the only place the refusal was persisted), and
``floor_rule()`` takes that as evidence. Without it the outcome stays a
failed capture; the finder says the shape is a candidate.

Pure Python, standard library only: importable from the capture, the tools
and IronPython alike.
"""

BELOW_CAPTURE_FLOOR = "below_capture_floor"
ANNOTATION_EXPORT_DEGENERATE = "annotation_export_degenerate"
UNMEASURABLE = "unmeasurable"

# Fault name -> the rule it names, as the reason strings record it.
FLOOR_RULES = {
    BELOW_CAPTURE_FLOOR: "the reference rectangle is too small for the twelve "
                         "registration ticks (stage_a_registration.mark_floor_px)",
    ANNOTATION_EXPORT_DEGENERATE: "the annotation export is at Revit's 10:1 aspect clamp "
                                  "or coarsened >= 4x against the model capture "
                                  "(color_id_buffer.annotation_export_degenerate)",
}

# The shape runs before the ``unmeasurable`` status wrote for the tick rule.
OLD_SHAPE_FAULTS = frozenset(("registration_marks_incomplete",
                              "registration_marks_may_not_draw"))
# The one phrase registration_mark_segments' refusal has always carried.
TOO_SMALL_MARKER = "too small for"


def reason_faults(reason):
    """PURE. The fault names a recorded reason carries: ``"a;b"`` (a
    registration failure's joined faults) -> ``["a", "b"]``; ``"below_
    capture_floor: the reference ..."`` -> ``["below_capture_floor"]``."""
    names = []
    for part in str(reason or "").split(";"):
        name = part.split(":", 1)[0].strip()
        if name:
            names.append(name)
    return names


def floor_fault(reason):
    """PURE. The floor rule a failure reason names, or None. EVERY fault the
    reason names must be a floor rule: the FIRST is the one the capture
    reports as the failure (color_id_buffer's ``failure_reason =
    capture_faults[0]``), so a degenerate export behind an earlier fault is
    that earlier fault's failure, and a floor refusal joined with an
    independent fault (``below_capture_floor: ...;rollback_failed``, as
    csv_export writes it when the registered capture also faulted) is a
    failed capture that still has to be looked at, not a floor case."""
    names = reason_faults(reason)
    if names and all(n in FLOOR_RULES for n in names):
        return names[0]
    return None


def registration_record_below_floor(registration_marks):
    """PURE. True when a sidecar's ``registration_marks`` record says the
    tick layout was refused for size: a ``registration_marks_incomplete``
    fault whose message carries the reason the refusal has always had
    ("0 of 0 marks drawn: the reference rectangle is N px on its short
    side, too small for ..."). An in-memory layout record (``state``
    ``unavailable`` with ``below_floor``) is accepted too."""
    if not isinstance(registration_marks, dict):
        return False
    if (registration_marks.get("state") == "unavailable"
            and isinstance(registration_marks.get("below_floor"), dict)):
        return True
    for fault in registration_marks.get("faults") or []:
        if not isinstance(fault, dict):
            continue
        if (fault.get("fault") == "registration_marks_incomplete"
                and TOO_SMALL_MARKER in str(fault.get("message") or "")):
            return True
    return False


def old_shape_candidate(capture_status, capture_failure_reason):
    """PURE. True for the pre-``unmeasurable`` shape of the tick rule: a
    registration failure carrying exactly the two faults an unavailable
    layout produces. A candidate, not a verdict: see ``floor_rule``."""
    if capture_status != "registration_failed":
        return False
    names = reason_faults(capture_failure_reason)
    return bool(names) and set(names) == OLD_SHAPE_FAULTS


def floor_rule(capture_status, capture_failure_reason, registration_marks=None):
    """PURE. ``{"rule", "evidence"}`` when the outcome is a view below the
    capture floor, else None.

    * ``evidence`` ``"run_meta"``: the status is ``unmeasurable``, or the
      reason's first fault is a floor rule (a run written by a build that
      recorded the rule under a failed status);
    * ``evidence`` ``"sidecar"``: the old registration-failed shape, with
      ``registration_marks`` (the MODEL sidecar's record) refusing the
      layout for size.

    The old shape without a sidecar record is NOT a floor case here: a
    view that hides its lines and loses a tick records the same faults.
    """
    reason = capture_failure_reason
    if capture_status == UNMEASURABLE:
        names = reason_faults(reason)
        return {"rule": names[0] if names else "unrecorded", "evidence": "run_meta"}
    rule = floor_fault(reason)
    if rule is not None and capture_status in ("failed", "annotation_failed"):
        return {"rule": rule, "evidence": "run_meta"}
    if (old_shape_candidate(capture_status, reason)
            and registration_record_below_floor(registration_marks)):
        return {"rule": BELOW_CAPTURE_FLOOR, "evidence": "sidecar"}
    return None
