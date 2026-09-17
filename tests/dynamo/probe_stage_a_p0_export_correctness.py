"""Stage A P0 export-correctness probe for Revit 2025 / Dynamo 3.3 CPython3.

Executes the P0 G5 checkpoint in Revit and emits per-view records that
tools/p0_export_comparators.py grades without a human reading numbers off a
table.

Captures through PRODUCTION (color_id_buffer.export_color_id_buffer_view) --
not a mirror of it. The whole point is to test the shipped cap, the shipped
dimension check and the shipped backoff, so anything this module
re-implements is something the run stops proving.

Cases:
    p0_select    Read-only. Enumerates plan views and reports, per view, the
                 pre-cap request, the fitted axis, the predicted w x h, and
                 whether the cap binds and on which axis. Produces the
                 candidate list a human picks p0_derived's view from; makes
                 no capture and mutates nothing.
    p0_default   One view, underlay disabled, default cap.
    p0_derived   One human-chosen view where the cap binds on the DERIVED
                 axis, underlay disabled, default cap.
    p0_override  One view, underlay disabled, sizing cap raised to 15000 so
                 the post-export check is forced to reject an over-ceiling
                 request. The verification ceiling does NOT move with it.

Every mutation happens inside a single TransactionGroup that is always
rolled back (the pattern probe_stage_a_drift_onset.py and
probe_stage_a_white_blend.py both use), and every probe-only override is
recorded with the value applied and whether it was restored -- a job that
leaves the document changed has contaminated every later job in the batch,
so "restored" is graded, not assumed.

Production defaults are never modified. The cap override is set on a Config
INSTANCE built per capture by build_capture_config(); nothing writes to
vop_interwoven.config's defaults.

Dynamo inputs:
    IN[0] = target Revit view (ignored by p0_select)
    IN[1] = output directory
    IN[2] = case selection: "all" or a comma-separated subset
    IN[3] = export DPI                                  (default production)
    IN[4] = sizing cap override for p0_override         (default 15000)
"""
import json
import os
import time

# Production is imported lazily inside the run functions so this module
# imports cleanly outside Revit for the offline comparator tests.

PROBE_NAME = "stage_a_p0_export_correctness"
PROBE_VERSION = "2026-09-17.1"

CASES = ("p0_select", "p0_default", "p0_derived", "p0_override")

# The cap p0_override raises the SIZING limit to. Above the verification
# ceiling on purpose: every export it admits is expected to be rejected.
DEFAULT_OVERRIDE_CAP_PX = 15000

# Plan-view types p0_select enumerates. Underlay is a plan-view concept and
# the P0 checkpoint is stated against plan views.
PLAN_VIEW_TYPE_NAMES = ("FloorPlan", "CeilingPlan", "AreaPlan", "EngineeringPlan")


def select_cases(selection):
    if selection is None or (isinstance(selection, str) and str(selection).strip().lower() == "all"):
        return list(CASES)
    requested = ([part.strip() for part in str(selection).replace(";", ",").split(",") if part.strip()]
                 if isinstance(selection, str) else [str(part).strip() for part in selection])
    unknown = [name for name in requested if name not in CASES]
    if unknown:
        raise ValueError("unknown p0 case(s): {0}; known: {1}".format(
            ", ".join(sorted(unknown)), ", ".join(CASES)))
    return [name for name in CASES if name in requested]


def predict_export(paper_width_in, paper_height_in, export_dpi, fit_direction, cap_axis_px=None):
    """What production would request and derive for this view, without Revit.

    Deliberately calls production's own cap_axes rather than restating the
    arithmetic: p0_select's candidate list has to agree with what the
    capture jobs will actually do, and two copies of the formula is exactly
    how the cap and the probe drifted apart the first time.
    """
    from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX, cap_axes

    vertical = str(fit_direction or "horizontal").strip().lower() == "vertical"
    paper_fit_in = float(paper_height_in if vertical else paper_width_in)
    paper_derived_in = float(paper_width_in if vertical else paper_height_in)
    if paper_fit_in <= 0 or paper_derived_in <= 0:
        raise ValueError("both paper dimensions must be positive")

    pre_cap_px = max(64, int(round(float(export_dpi) * paper_fit_in)))
    derived_uncapped = pre_cap_px * (paper_derived_in / paper_fit_in)
    cap = cap_axes(pre_cap_px, derived_uncapped, cap_axis_px or MAX_STAGE_A_AXIS_PX)

    fitted, derived = cap["accepted_px"], cap["accepted_derived_px"]
    # Which axis the cap was actually decided by: the fitted axis was over,
    # the derived axis was over, or neither. "derived" is the case a
    # fitted-axis-only cap would have waved through, so p0_derived's
    # candidate list is exactly the views reporting it.
    binding_axis = None
    if cap["cap_applied"]:
        binding_axis = "fitted" if cap["pre_cap_px"] >= cap["pre_cap_derived_px"] else "derived"
    return {
        "requested_axis": "height" if vertical else "width",
        "paper_fit_in": paper_fit_in,
        "paper_width_in": float(paper_width_in),
        "paper_height_in": float(paper_height_in),
        "export_dpi": float(export_dpi),
        "pre_cap_px": cap["pre_cap_px"],
        "pre_cap_derived_px": cap["pre_cap_derived_px"],
        "requested_px": fitted,
        "predicted_derived_px": derived,
        "predicted_w": derived if vertical else fitted,
        "predicted_h": fitted if vertical else derived,
        "cap_applied": bool(cap["cap_applied"]),
        "cap_binding_axis": binding_axis,
        "cap_axis_px": cap["max_axis_px"],
    }


def build_record(case, view_id, view_name, sidecar, decoded, metrics, overrides,
                 failure_reason=None, success=None, errors=None):
    """Assemble one per-view record from artifacts other code produced.

    Every field is copied, never recomputed -- see p0_export_comparators'
    module docstring for why.
    """
    resolution = dict((sidecar or {}).get("resolution") or {})
    decoded = decoded or {}
    metrics = metrics or {}
    edges = metrics.get("edges") or {}
    pixels = metrics.get("pixels") or {}
    return {
        "case": case,
        "view_id": view_id,
        "view_name": view_name,
        "resolution": resolution,
        "applied_smooth_edges": (sidecar or {}).get("applied_smooth_edges"),
        "smooth_edges_read_error": (sidecar or {}).get("smooth_edges_read_error"),
        "applied_display_style": (sidecar or {}).get("applied_display_style"),
        "reliability": {
            "capture_reliable": decoded.get("capture_reliable"),
            "reason": decoded.get("capture_unreliable_reason"),
        },
        "feet_per_pixel": decoded.get("feet_per_pixel"),
        "feet_per_pixel_basis": decoded.get("feet_per_pixel_basis"),
        "feet_per_pixel_unreliable_reason": decoded.get("feet_per_pixel_unreliable_reason"),
        "metrics": {
            "hard_edges": edges.get("hard_edge_count"),
            "blended_edges": edges.get("blended_edge_count"),
            # Carried through as-is, INCLUDING None. The comparator grades
            # None as a failure; converting it to 0.0 or 1.0 here would
            # manufacture the reading the checkpoint is supposed to catch.
            "hard_ratio": edges.get("hard_edge_ratio"),
            "off_px": pixels.get("off_palette_px"),
        },
        "probe_overrides": list(overrides or []),
        "failure_reason": failure_reason,
        "success": success,
        "errors": list(errors or []),
    }


class OverrideLedger(object):
    """Records every probe-only change and whether undoing it succeeded.

    Separate from the TransactionGroup rollback on purpose. The group is
    what makes the document changes temporary; this is the evidence that
    they were, reported per override so a partial restore names itself
    instead of hiding inside a group that returned without complaint.
    """

    def __init__(self):
        self.entries = []

    def record(self, name, value, applied=True, reason=None):
        """``applied=False`` records an override that was NOT needed.

        A view with no underlay configured needs no underlay change, and
        claiming one anyway would report a mutation that never happened and
        then grade it as successfully restored. The requirement the
        checkpoint actually has is "underlay is off during the capture",
        which a view without one already satisfies -- so it is recorded as
        satisfied-without-mutation rather than as an applied override.
        """
        entry = {"override": name, "value": value, "applied": bool(applied),
                 "reason": reason, "restored": not applied, "restore_error": None}
        self.entries.append(entry)
        return entry

    def mark_restored(self, entry, error=None):
        entry["restored"] = error is None
        if error is not None:
            entry["restore_error"] = "{0}: {1}".format(type(error).__name__, error)

    def as_list(self):
        return [dict(e) for e in self.entries]


def _plan_views(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, View
    out = []
    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate:
                continue
            if str(getattr(view, "ViewType", "")).split(".")[-1] not in PLAN_VIEW_TYPE_NAMES:
                continue
        except Exception:
            continue
        out.append(view)
    return out


def run_select(doc, cfg_dpi, fit_direction, cap_axis_px=None, diag=None):
    """Read-only enumeration. Opens no transaction and exports nothing."""
    from vop_interwoven.core.raster import ViewRaster  # noqa: F401  (contract anchor)
    from vop_interwoven.revit.view_basis import make_view_basis, resolve_view_bounds

    candidates = []
    for view in _plan_views(doc):
        entry = {"view_id": view.Id.IntegerValue, "view_name": getattr(view, "Name", None),
                 "view_type": str(getattr(view, "ViewType", "")).split(".")[-1],
                 "scale": float(getattr(view, "Scale", 1) or 1)}
        try:
            basis = make_view_basis(view, diag=diag)
            bounds = resolve_view_bounds(doc, view, basis, diag=diag)
            width_ft = float(bounds.xmax) - float(bounds.xmin)
            height_ft = float(bounds.ymax) - float(bounds.ymin)
            paper_w = width_ft * 12.0 / max(entry["scale"], 1.0e-6)
            paper_h = height_ft * 12.0 / max(entry["scale"], 1.0e-6)
            entry.update(predict_export(paper_w, paper_h, cfg_dpi, fit_direction, cap_axis_px))
            entry["eligible_for_derived_job"] = entry["cap_binding_axis"] == "derived"
        except Exception as ex:
            entry["error"] = "{0}: {1}".format(type(ex).__name__, ex)
            entry["eligible_for_derived_job"] = False
        candidates.append(entry)

    return {
        "case": "p0_select",
        "candidates": candidates,
        "derived_job_candidates": [c["view_id"] for c in candidates
                                   if c.get("eligible_for_derived_job")],
        "probe_overrides": [],
    }


def run_capture(doc, view, case, output_dir, export_dpi=None, cap_axis_px=None,
                disable_underlay=True, diag=None):
    """One capture through production, with probe-only overrides applied.

    The overrides live on a per-capture Config instance and on the view
    inside the enclosing TransactionGroup; neither touches a production
    default. Returns the per-view record.
    """
    from probe_stage_a_drift_onset import build_capture_config
    from probe_stage_a_white_blend import _clear_underlay, _underlay_report
    from vop_interwoven.color_id_buffer import export_color_id_buffer_view
    from Autodesk.Revit.DB import Transaction, TransactionStatus

    ledger = OverrideLedger()
    errors = []
    view_id = view.Id.IntegerValue

    underlay_entry = None
    if disable_underlay:
        # Ask first. b3_underlay_off in probe_stage_a_white_blend gates
        # itself the same way, and run 1b113822 is why: that job skipped
        # with "B1 found no underlay configured on this view". Calling
        # _clear_underlay regardless would log an override that changed
        # nothing and then grade it restored.
        underlay = _underlay_report(doc, view)
        configured = underlay.get("underlay_configured")
        if configured is False:
            underlay_entry = ledger.record(
                "underlay_disabled", True, applied=False,
                reason="no_underlay_configured")
        else:
            # None (could not be determined) is treated as configured: the
            # capture proceeds with the underlay cleared, which is correct
            # either way, rather than skipping on an unknown.
            underlay_entry = ledger.record("underlay_disabled", True)
            underlay_entry["underlay_configured"] = configured
            tx = Transaction(doc, "VOP P0: disable underlay")
            if tx.Start() != TransactionStatus.Started:
                raise RuntimeError("could not start the underlay transaction")
            try:
                underlay_entry["mechanism"] = _clear_underlay(view)
                tx.Commit()
            except Exception:
                tx.RollBack()
                raise
        underlay_entry["underlay_off_for_capture"] = True

    overrides = {}
    if cap_axis_px:
        ledger.record("color_id_buffer_cap_axis_px", int(cap_axis_px))
        overrides["color_id_buffer_cap_axis_px"] = int(cap_axis_px)

    cfg = build_capture_config(output_dir, export_dpi=export_dpi, overrides=overrides)
    out = export_color_id_buffer_view(doc, view, elements=[], cfg=cfg, diag=diag,
                                      raster=None, elem_cache=None)

    sidecar = out.get("metadata") or {}
    return {
        "record_seed": build_record(
            case, view_id, getattr(view, "Name", None), sidecar,
            decoded=None, metrics=None, overrides=ledger.as_list(),
            failure_reason=out.get("failure_reason"), success=out.get("success"),
            errors=errors),
        "ledger": ledger,
        "tiff_path": out.get("tiff_path"),
        "sidecar_path": out.get("sidecar_path"),
        "sidecar": sidecar,
    }


def empty_report(output_dir, cases, inputs=None):
    return {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION,
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": dict(inputs or {}, output_directory=output_dir, cases=list(cases)),
        "records": [],
        "rollback": {"group_started": False, "rolled_back": False},
        "errors": [], "warnings": [], "timings_ms": {},
        "started_at": None, "finished_at": None,
    }


def _now_ms():
    return time.time() * 1000.0


def _get_current_document(raw_view=None):
    if raw_view is not None and hasattr(raw_view, "Document"):
        return raw_view.Document
    from vop_interwoven.entry_dynamo import get_current_document
    return get_current_document()


def _unwrap_view(raw_view):
    """Dynamo hands over a wrapper; the Revit view is under InternalElement."""
    return getattr(raw_view, "InternalElement", raw_view)


def run_probe(raw_view=None, output_dir=None, selection="all", export_dpi=None,
              cap_axis_px=None, disable_underlay=True, **unused):
    """Entry point matching revit_probe_registry's generic adapter contract.

    Everything that mutates the document happens inside ONE TransactionGroup
    that is always rolled back, whatever the outcome -- including the
    p0_override job, whose whole purpose is to end in a rejected export. A
    job that leaves the document changed contaminates every later job in the
    batch, so the rollback is unconditional and its result is reported
    rather than assumed.
    """
    if not output_dir:
        raise ValueError("output_dir is required")
    cases = select_cases(selection)
    report = empty_report(output_dir, cases, inputs={
        "export_dpi": export_dpi, "cap_axis_px": cap_axis_px,
        "disable_underlay": bool(disable_underlay),
        "ignored_settings": sorted(unused) or None,
    })
    report["started_at"] = _now_ms()

    from vop_interwoven.core.diagnostics import Diagnostics
    from vop_interwoven.resolution_contract import DEFAULT_COLOR_ID_EXPORT_DPI

    diag = Diagnostics()
    view = _unwrap_view(raw_view) if raw_view is not None else None
    doc = _get_current_document(view)
    dpi = float(export_dpi or DEFAULT_COLOR_ID_EXPORT_DPI)
    fit_direction = "horizontal"

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    group = None
    try:
        from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
        needs_mutation = any(c != "p0_select" for c in cases)
        if needs_mutation:
            group = TransactionGroup(doc, "VOP Stage A P0 Export Correctness")
            if group.Start() != TransactionStatus.Started:
                raise RuntimeError("TransactionGroup.Start did not start")
            report["rollback"]["group_started"] = True

        for case in cases:
            try:
                if case == "p0_select":
                    report["records"].append(
                        run_select(doc, dpi, fit_direction, cap_axis_px=None, diag=diag))
                    continue
                if view is None:
                    raise ValueError("{0} requires a target view".format(case))
                capture = run_capture(
                    doc, view, case, os.path.join(output_dir, case),
                    export_dpi=dpi,
                    cap_axis_px=(cap_axis_px if case == "p0_override"
                                 else None),
                    disable_underlay=bool(disable_underlay), diag=diag)
                report["records"].append(capture["record_seed"])
                report.setdefault("capture_artifacts", []).append({
                    "case": case, "tiff_path": capture["tiff_path"],
                    "sidecar_path": capture["sidecar_path"]})
            except Exception as ex:
                # Recorded, never swallowed: a case that could not run is not
                # a case that passed, and the comparator grades a missing
                # record as a failure rather than an absence.
                report["errors"].append({
                    "case": case, "error": "{0}: {1}".format(type(ex).__name__, ex)})
    finally:
        if group is not None:
            try:
                from Autodesk.Revit.DB import TransactionStatus
                status = group.RollBack()
                report["rollback"]["rolled_back"] = (status == TransactionStatus.RolledBack)
                report["rollback"]["status"] = str(status)
            except Exception as ex:
                report["rollback"]["rolled_back"] = False
                report["rollback"]["error"] = "{0}: {1}".format(type(ex).__name__, ex)
            # The group rollback is what actually undoes the view changes, so
            # each override's restored flag is settled by it -- not by a
            # separate undo the ledger would have to perform itself.
            restored = bool(report["rollback"].get("rolled_back"))
            for record in report["records"]:
                for entry in record.get("probe_overrides") or []:
                    if not entry.get("applied", True):
                        # Nothing was changed, so the rollback has nothing to
                        # say about it; it stays restored=True regardless of
                        # how the group ended.
                        continue
                    entry["restored"] = restored
                    if not restored:
                        entry["restore_error"] = report["rollback"].get(
                            "error", "TransactionGroup.RollBack did not roll back")

    report["diagnostics"] = diag.to_dict() if hasattr(diag, "to_dict") else None
    report["finished_at"] = _now_ms()
    report["timings_ms"]["total"] = report["finished_at"] - report["started_at"]

    path = os.path.join(output_dir, "{0}.json".format(PROBE_NAME))
    with open(path, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
    report["report_path"] = path
    return report
