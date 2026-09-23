"""Stage A REGISTERED capture: both passes, registration marks, one rollback.

Behind ``Config.color_id_buffer_registered_capture`` (default OFF). What it
changes against the shipped two-pass capture, and the measurement behind each
(tools/notes/ROUND2_ and ROUND3_ANNO_PASS_VARIANTS_FINDINGS.md):

  * THE AUTHORED CROP IS NOT WRITTEN by the annotation pass (crop_mode
    "authored_else_crop_a"). Widening it to frame B lengthened datums and
    pulled in content from beyond the authored crop (round 1). A crop-INACTIVE
    view gets crop A instead -- the rectangle the model pass itself activates
    there -- because left untouched it exports its whole extent (round 3b:
    2942 x 6986 px at 2.9 px/ft, refused as annotation_lattice_mismatch).
  * MODEL CONTENT IS SUPPRESSED BY MEMBERSHIP, not by hiding categories: a
    white element override on every model member and a white view filter per
    linked RVT category. Hiding a category also hides its dependent annotation
    -- the plan's dimensions went 2 -> 135 visible once it stopped (round 2).
    No category/subcategory white layer: its images were byte-identical
    without it on all three round-3 views, at 23-86 s per view.
  * REGISTRATION MARKS in BOTH captures. With the crop untouched the
    annotation export fits its own extent, so its scale is NOT the model
    lattice (6.3 % and 15.1 % off on round 3's elevation and plan). The marks
    are detail lines at known view UV; their pixels give each capture's
    UV -> pixel map, and composing the two registers the annotation capture
    onto the model one. The crop boundary Revit draws cannot: it lands on the
    image border and is clipped (round 2).
  * ONE TransactionGroup, ROLLED BACK. It is the restore: the marks, the white
    overrides and the link filters all go with it. The explicit reverse this
    replaces in the probe took 33-51 s per capture; the rollback 0.5-5.5 s.
    The view is READ BACK afterwards, and a view that did not come back is a
    fault, never a silent success.

Sequence: snapshot -> group -> marks -> MODEL pass (OST_Lines left visible, so
the marks draw) -> white membership suppression -> ANNOTATION pass (external
suppression, authored crop kept or crop A applied) -> rollback -> read-back -> the registration record
into BOTH sidecars, under ``registration_marks`` (the annotation sidecar's own
``registration`` block is production's and is never overwritten). The record is
written LAST, after the read-back, so the
file a consumer reads carries the restore verdict (CLAUDE.md defect class 4: a
record serialised before it is finished).

What this does NOT do: fit the marks. Pixels are read after the capture, by
whatever decodes it (today tools/notes/anno_pass_variant_report.py's
registration_mark_fit); the sidecars carry the marks' UV and colours for it.
"""

import copy
import time

from .revit.safe_api import element_id_value as _element_id_int

REGISTERED_CAPTURE_SCHEMA = "vop.stage_a.registered_capture.v1"


def _read(fn):
    """Three-valued read of one view property."""
    try:
        return {"state": "value", "value": fn()}
    except Exception as ex:
        return {"state": "unavailable",
                "reason": "{0}: {1}".format(type(ex).__name__, ex)}


def _xyz(point):
    return [round(float(point.X), 9), round(float(point.Y), 9),
            round(float(point.Z), 9)]


def view_state(view):
    """What the capture writes and the rollback must put back, read."""
    def _crop_box():
        box = view.CropBox
        return {"min": _xyz(box.Min), "max": _xyz(box.Max)}
    return {
        "view_template_id": _read(lambda: _element_id_int(view.ViewTemplateId)),
        "display_style": _read(lambda: str(view.DisplayStyle)),
        "crop_box_active": _read(lambda: bool(view.CropBoxActive)),
        "crop_box_visible": _read(lambda: bool(view.CropBoxVisible)),
        "crop_box": _read(_crop_box),
    }


def view_state_verdict(before, after):
    """PURE. Per property: restored / not_restored / unverified."""
    out = {}
    for name in sorted(before):
        b, a = before.get(name) or {}, after.get(name) or {}
        if b.get("state") != "value" or a.get("state") != "value":
            out[name] = {"status": "unverified",
                         "reason": "before={0} after={1}".format(
                             b.get("reason") or b.get("state"),
                             a.get("reason") or a.get("state"))}
        elif b.get("value") != a.get("value"):
            out[name] = {"status": "not_restored", "before": b.get("value"),
                         "after": a.get("value")}
        else:
            out[name] = {"status": "restored"}
    return out


def _non_blank_override_ids(view, element_ids):
    """``(ids whose element override is NOT blank, unreadable ids)``, via the
    annotation pass's own reader of "blank"."""
    from .color_id_buffer import _override_is_cleared
    non_blank, unreadable = [], []
    for eid in element_ids:
        state, cleared, _reason = _override_is_cleared(view, int(eid))
        if state != "value":
            unreadable.append(int(eid))
        elif not cleared:
            non_blank.append(int(eid))
    return sorted(non_blank), sorted(unreadable)


def mark_reference_rectangle(view, raster, diag=None, view_id=None):
    """``(uv_rect, source)`` the marks are placed inside: the view's authored
    crop when it is active, else the model pass's crop A
    (``raster.model_clip_bounds``), else the raster frame. Never a guess: a
    view with none of them returns ``(None, reason)``."""
    basis = getattr(raster, "view_basis", None)
    try:
        if bool(view.CropBoxActive) and basis is not None:
            from .revit.collection import project_bbox_corners_uv
            corners = project_bbox_corners_uv(view.CropBox, basis, diag=diag,
                                              view_id=view_id)
            if corners:
                us = [float(c[0]) for c in corners]
                vs = [float(c[1]) for c in corners]
                return (min(us), min(vs), max(us), max(vs)), "authored_crop"
    except Exception as ex:
        if diag is not None:
            diag.warn(phase="color_id_buffer", callsite="registered_marks_reference",
                      message="the authored crop could not be projected; the marks "
                              "fall back to the model crop ({0}: {1})".format(
                                  type(ex).__name__, ex),
                      view_id=view_id)
    for attr, source in (("model_clip_bounds", "model_crop_a"),
                         ("bounds_xy", "raster_frame")):
        bounds = getattr(raster, attr, None)
        if bounds is not None:
            return ((float(bounds.xmin), float(bounds.ymin),
                     float(bounds.xmax), float(bounds.ymax)), source)
    return None, "no authored crop, model crop or raster frame to place marks in"


def nominal_fpp_ft(view, cfg):
    """Feet per pixel at the REQUESTED dpi: view scale / (12 in x dpi). The
    marks are sized in pixels at this; the achieved lattice can only be coarser
    (a capped export), which makes the ticks shorter in pixels, not wrong."""
    return float(view.Scale) / (12.0 * float(getattr(cfg, "color_id_buffer_export_dpi")))


def _registration_payload(pass_name, record, colours_by_id=None, shared_colour=None):
    marks = []
    for mark in (record.get("marks") or {}).get("created") or []:
        entry = dict(mark)
        entry["rgb"] = (list(shared_colour) if shared_colour is not None
                        else (colours_by_id or {}).get(str(mark.get("id"))))
        marks.append(entry)
    return {
        "schema": REGISTERED_CAPTURE_SCHEMA,
        "pass": pass_name,
        "marks": marks,
        "layout": (record.get("marks") or {}).get("layout"),
        "reference_source": record.get("mark_reference_source"),
        "colour_source": ("MARK_COLOUR, one reserved colour for every tick; each "
                          "tick is its own connected component"
                          if shared_colour is not None else
                          "this capture's color_assignment_map: the marks are "
                          "view-owned, so the annotation pass painted them"),
        "must_be_subtracted": True,
        "is_documentation_content": False,
        "annotation_crop_mode": "authored_else_crop_a",
        "model_suppression": record.get("suppression_summary"),
        "restore": record.get("restore"),
        "faults": list(record.get("faults") or []),
    }


def export_registered_stage_a_view(doc, view, elements, cfg, diag=None,
                                   raster=None, elem_cache=None):
    """Both Stage A passes, registered by marks, restored by one rollback.

    Returns the MODEL pass's result dict, with the annotation pass nested under
    ``annotation_pass`` exactly as pipeline.py nests it, and the capture's own
    record under ``registration``. Never raises for a failure inside the
    capture: every one is recorded, diag.error'd and reflected in
    ``registration["success"]``; the rollback runs regardless.
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector, Transaction, TransactionGroup, TransactionStatus,
    )
    from .color_id_buffer import (
        export_annotation_color_id_buffer_view, export_color_id_buffer_view,
    )
    from .revit.annotation import split_stage_a_pass_membership
    from . import stage_a_registration as registration

    view_id = _element_id_int(getattr(view, "Id", None))
    record = {"schema": REGISTERED_CAPTURE_SCHEMA, "view_id": view_id,
              "faults": [], "timings_ms": {}}
    model_out = None
    anno_out = None
    model_member_ids = []

    def _fault(fault, message, exc=None):
        record["faults"].append({"fault": fault, "message": message,
                                 "error": (None if exc is None else "{0}: {1}".format(
                                     type(exc).__name__, exc))})
        if diag is not None:
            diag.error(phase="color_id_buffer", callsite="registered_capture",
                       message="{0}: {1}".format(fault, message),
                       view_id=view_id, exc=exc)

    before = view_state(view)
    group = TransactionGroup(doc, "VOP Stage A registered capture")
    started = False
    t_total = time.time()
    try:
        started = group.Start() == TransactionStatus.Started
        if not started:
            raise RuntimeError("TransactionGroup.Start did not start")

        # ---- the model members, and what was authored on them BEFORE ------
        collected = list(FilteredElementCollector(doc, view.Id)
                         .WhereElementIsNotElementType())
        model_members, _anno, unresolved, _basis = split_stage_a_pass_membership(
            collected, capture_view_id_int=view_id, diag=diag)
        model_member_ids = [i for i in (_element_id_int(getattr(e, "Id", None))
                                        for e in model_members) if i is not None]
        record["membership"] = {"model": len(model_member_ids),
                                "annotation": len(_anno),
                                "unresolved": len(unresolved)}
        _t = time.time()
        record["_authored_before"] = _non_blank_override_ids(view, model_member_ids)
        record["timings_ms"]["authored_override_scan"] = round(
            (time.time() - _t) * 1000.0, 3)

        # ---- 1: registration marks -----------------------------------------
        reference, source = mark_reference_rectangle(view, raster, diag=diag,
                                                     view_id=view_id)
        record["mark_reference_source"] = source
        layout = (registration.registration_mark_segments(
                      reference, nominal_fpp_ft(view, cfg))
                  if reference is not None else {"state": "unavailable",
                                                 "reason": source})
        tx = Transaction(doc, "VOP Stage A registration marks")
        tx.Start()
        try:
            marks = registration.create_registration_marks(
                doc, view, getattr(raster, "view_basis", None), layout)
            if tx.Commit() != TransactionStatus.Committed:
                raise RuntimeError("marks Transaction.Commit did not commit")
        except Exception:
            tx.RollBack()
            raise
        record["marks"] = marks
        if marks.get("created_count") != marks.get("expected_count") or not marks.get(
                "expected_count"):
            _fault("registration_marks_incomplete",
                   "{0} of {1} marks drawn: {2}".format(
                       marks.get("created_count"), marks.get("expected_count"),
                       marks.get("reason")))
        if marks.get("lines_category_hidden_in_view") is not False:
            _fault("registration_marks_may_not_draw",
                   "the view hides OST_Lines, or its state could not be read "
                   "({0})".format(marks.get("lines_category_hidden_in_view")))

        # ---- 2: MODEL pass, OST_Lines visible so the marks draw ------------
        model_cfg = copy.copy(cfg)
        model_cfg.color_id_buffer_model_lines_visible = True
        geom = {}
        _t = time.time()
        model_out = export_color_id_buffer_view(
            doc, view, elements, model_cfg, diag=diag, raster=raster,
            elem_cache=elem_cache, geometry_out=geom)
        record["timings_ms"]["model_pass"] = round((time.time() - _t) * 1000.0, 3)
        if not (model_out or {}).get("success"):
            raise RuntimeError("the model pass reported failure: {0}".format(
                (model_out or {}).get("failure_reason")))

        # ---- 3: white membership suppression ------------------------------
        capability = registration.white_override_capability(doc)
        if capability.get("state") != "value":
            raise RuntimeError("the white override cannot be built on this host: "
                               "{0}".format(capability.get("reason")))
        link_categories, link_report = registration.discover_link_categories(
            doc, view, diag=diag, view_id=view_id)
        record["link_categories"] = link_report
        tx = Transaction(doc, "VOP Stage A membership white suppression")
        tx.Start()
        try:
            _t = time.time()
            suppression = registration.white_membership_suppression(
                doc, view, view_id, model_members, link_categories=link_categories,
                diag=diag)
            if tx.Commit() != TransactionStatus.Committed:
                raise RuntimeError("suppression Transaction.Commit did not commit")
            record["timings_ms"]["suppression"] = round((time.time() - _t) * 1000.0, 3)
        except Exception:
            tx.RollBack()
            raise
        record["suppression_summary"] = {
            "element_override_count": suppression.get("element_override_count"),
            "link_category_filters": suppression.get("link_category_filters"),
            "unreached_count": len(suppression.get("unreached") or []),
            "unreached": list(suppression.get("unreached") or [])[:200],
            "timings_ms": suppression.get("timings_ms"),
        }
        if suppression.get("unreached"):
            _fault("model_suppression_incomplete",
                   "{0} model member(s) or link categories were not suppressed; "
                   "their ink may appear in the annotation capture".format(
                       len(suppression["unreached"])))

        # ---- 4: ANNOTATION pass, external suppression, crop untouched -----
        anno_cfg = copy.copy(cfg)
        anno_cfg.color_id_buffer_anno_model_suppression = "external"
        anno_cfg.color_id_buffer_anno_crop_mode = "authored_else_crop_a"
        _t = time.time()
        try:
            anno_out = export_annotation_color_id_buffer_view(
                doc, view, anno_cfg, geom, diag=diag, raster=raster)
        except Exception as ex:
            _fault("annotation_pass_raised", "the annotation pass raised", ex)
            anno_out = {"view_id": view_id, "success": False,
                        "failure_reason": "annotation_pass_raised",
                        "error": "{0}: {1}".format(type(ex).__name__, ex),
                        "stage": "color_id_buffer_stage_a_annotation",
                        "tiff_path": None, "sidecar_path": None}
        record["timings_ms"]["annotation_pass"] = round((time.time() - _t) * 1000.0, 3)
    except Exception as ex:
        _fault("registered_capture_raised", "the registered capture stopped", ex)
    finally:
        # ---- 5: THE RESTORE ------------------------------------------------
        restore = {"mode": "transaction_group_rollback", "rolled_back": False}
        if started:
            _t = time.time()
            try:
                status = group.RollBack()
                restore["rolled_back"] = status == TransactionStatus.RolledBack
                restore["status"] = str(status)
            except Exception as ex:
                _fault("rollback_raised", "TransactionGroup.RollBack raised", ex)
            restore["rollback_ms"] = round((time.time() - _t) * 1000.0, 3)
            if not restore["rolled_back"]:
                _fault("rollback_failed", "the capture's TransactionGroup did not "
                                          "roll back; the view may keep marks, "
                                          "white overrides and filters")
        # ---- 6: READ BACK what the rollback left --------------------------
        restore["view_state"] = view_state_verdict(before, view_state(view))
        bad = sorted(k for k, v in restore["view_state"].items()
                     if v["status"] != "restored")
        if bad:
            _fault("view_state_not_restored",
                   "after the rollback these did not read back as before: "
                   "{0}".format(bad))
        mark_ids = [m.get("id") for m in (record.get("marks") or {}).get("created") or []
                    if m.get("id") is not None]
        still = registration.marks_still_in_project(doc, mark_ids)
        restore["marks_still_in_project"] = sorted(
            k for k, v in still.items() if v is not False)
        if restore["marks_still_in_project"]:
            _fault("registration_marks_left_in_project",
                   "{0} mark(s) survived the rollback".format(
                       len(restore["marks_still_in_project"])))
        authored_before = record.pop("_authored_before", None)
        if authored_before is not None:
            _t = time.time()
            authored_after = _non_blank_override_ids(view, model_member_ids)
            restore["element_overrides"] = {
                "non_blank_before": len(authored_before[0]),
                "non_blank_after": len(authored_after[0]),
                "unreadable_after": len(authored_after[1]),
                "elapsed_ms": round((time.time() - _t) * 1000.0, 3),
            }
            left = sorted(set(authored_after[0]) - set(authored_before[0]))
            restore["element_overrides"]["left_behind"] = left[:200]
            if left:
                _fault("element_overrides_left_behind",
                       "{0} model member(s) carry an override they did not have "
                       "before the capture".format(len(left)))
            elif authored_after[1]:
                _fault("element_overrides_unverified",
                       "{0} model member override(s) could not be read back".format(
                           len(authored_after[1])))
        record["restore"] = restore
        record["timings_ms"]["total"] = round((time.time() - t_total) * 1000.0, 3)
        record["success"] = bool(
            model_out and model_out.get("success")
            and anno_out and anno_out.get("success") and not record["faults"])

        # ---- 7: the record into BOTH sidecars, LAST -------------------------
        record["sidecar_writes"] = {}
        if model_out and model_out.get("sidecar_path"):
            record["sidecar_writes"]["model"] = registration.annotate_sidecar(
                model_out["sidecar_path"], "registration_marks",
                _registration_payload("model", record,
                                      shared_colour=registration.MARK_COLOUR))
        if anno_out and anno_out.get("sidecar_path"):
            colours = ((anno_out.get("metadata") or {}).get("color_assignment_map")
                       or {})
            record["sidecar_writes"]["annotation"] = registration.annotate_sidecar(
                anno_out["sidecar_path"], "registration_marks",
                _registration_payload("annotation", record, colours_by_id=colours))
        for name, error in record["sidecar_writes"].items():
            if error is not None:
                _fault("registration_sidecar_write_failed",
                       "{0} sidecar: {1}".format(name, error))
                record["success"] = False

    if not isinstance(model_out, dict):
        model_out = {"view_id": view_id, "view_name": getattr(view, "Name", None),
                     "success": False, "failure_reason": "registered_capture_raised",
                     "stage": "color_id_buffer_stage_a",
                     "tiff_path": None, "sidecar_path": None}
    if isinstance(anno_out, dict):
        model_out["annotation_pass"] = anno_out
        model_out["annotation_pass_success"] = bool(anno_out.get("success"))
        model_out["annotation_pass_failure_reason"] = anno_out.get("failure_reason")
        model_out["annotation_tiff_path"] = anno_out.get("tiff_path")
        model_out["annotation_sidecar_path"] = anno_out.get("sidecar_path")
    model_out["registration"] = record
    model_out["registration_success"] = record["success"]
    return model_out
