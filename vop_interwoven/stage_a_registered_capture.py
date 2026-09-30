"""Stage A REGISTERED capture: both passes, registration marks, one rollback.

The default Stage A capture (``Config.color_id_buffer_registered_capture``,
default ON; False selects the frame-B two-pass fallback). What it
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

What this does NOT do: fit the marks. Pixels are read after the capture,
outside Revit, by tools/registration_marks.py -- which
tools/register_stage_a_annotation.py uses to resample the annotation capture
onto the model lattice and tools/decode_stage_a_color_id.py uses to subtract
the ticks; the sidecars carry the marks' UV and colours for it.
"""

import copy
import hashlib
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


def annotation_avoid_rects(view, anno_elements, basis, diag=None, view_id=None):
    """``(avoid, record)``: each annotation element's view bbox in UV, for
    registration.relocate_marks_clear_of. An element whose bbox does not
    resolve is counted, not guessed; a read that raises is counted with its
    first error. Bboxes only -- no geometry (the Stage A rule).

    T2: an ImportInstance is NOT avoided. A view-specific DWG's bbox spans
    the plan, so every tick inside it was moved or marked "blocked" while
    all twelve still registered (pipeline_0930_1133, Plan_DWG: 7 false
    blocks). Excluded imports are counted as ``excluded_imports``; no other
    element is excluded."""
    from .color_id_buffer import _is_import_instance
    from .revit.collection import project_bbox_corners_uv
    avoid, no_bbox, errors, excluded_imports = [], 0, [], 0
    t0 = time.time()
    for elem in anno_elements or []:
        if _is_import_instance(elem):
            excluded_imports += 1
            continue
        try:
            bbox = elem.get_BoundingBox(view)
            corners = (project_bbox_corners_uv(bbox, basis, diag=diag, view_id=view_id)
                       if bbox is not None and basis is not None else None)
        except Exception as ex:
            errors.append("{0}: {1}".format(type(ex).__name__, ex))
            continue
        if not corners:
            no_bbox += 1
            continue
        us = [float(c[0]) for c in corners]
        vs = [float(c[1]) for c in corners]
        avoid.append((_element_id_int(getattr(elem, "Id", None)),
                      [min(us), min(vs), max(us), max(vs)]))
    return avoid, {"annotation_elements": len(anno_elements or []),
                   "avoid_rects": len(avoid), "no_bbox": no_bbox,
                   "excluded_imports": excluded_imports,
                   "read_errors": len(errors), "first_error": errors[0] if errors else None,
                   "elapsed_ms": round((time.time() - t0) * 1000.0, 3)}


def nominal_fpp_ft(view, cfg):
    """Feet per pixel at the REQUESTED dpi: view scale / (12 in x dpi). The
    marks are sized in pixels at this; the achieved lattice can only be coarser
    (a capped export), which makes the ticks shorter in pixels, not wrong."""
    return float(view.Scale) / (12.0 * float(getattr(cfg, "color_id_buffer_export_dpi")))


def mark_fpp_ft(view, raster, cfg):
    """``(fpp_ft, basis)`` the registration ticks are sized at: the MODEL
    lattice's achieved feet-per-pixel.

    The ticks are drawn before the model pass sizes itself, and were sized at
    the requested dpi (nominal_fpp_ft). When the axis cap fires, the achieved
    lattice is coarser, so a 32 px arm came out 32 * achieved/requested px --
    below the minimum registration_marks is proven to locate (Codex, PR #221).
    This is the model pass's own sizing, composed rather than copied: crop A
    from compute_model_crop() against the same frame, then
    resolution_contract.frame_export_geometry() on crop A with the same scale,
    dpi, fit direction and cap (export_color_id_buffer_view, sizing_frame
    "crop_a"). tests/test_stage_a_registered_capture.py asserts the two agree
    on a capped view. Unresolvable -> the requested dpi, with the reason as
    the basis.
    """
    from .color_id_buffer import MAX_STAGE_A_AXIS_PX, compute_model_crop
    from .core.math_utils import Bounds2D
    from .resolution_contract import frame_export_geometry
    try:
        frame = getattr(raster, "anno_frame_bounds", None) or raster.bounds_xy
        crop, _offset = compute_model_crop(
            getattr(raster, "model_clip_bounds", None),
            Bounds2D(float(frame.xmin), float(frame.ymin),
                     float(frame.xmax), float(frame.ymax)))
        crop_uv = (float(crop.xmin), float(crop.ymin), float(crop.xmax), float(crop.ymax))
        geom = frame_export_geometry(
            crop_uv, crop_uv, float(view.Scale),
            float(getattr(cfg, "color_id_buffer_export_dpi")),
            fit_direction=str(getattr(cfg, "color_id_buffer_fit_direction", "horizontal")
                              or "horizontal"),
            max_axis_px=(getattr(cfg, "color_id_buffer_cap_axis_px", None)
                         or MAX_STAGE_A_AXIS_PX))
        return float(geom["achieved_fpp_ft"]), "model_lattice"
    except (AttributeError, TypeError, ValueError) as ex:
        return nominal_fpp_ft(view, cfg), "requested_dpi ({0}: {1})".format(
            type(ex).__name__, ex)


# P2: the phases that PARTITION ``total`` -- sequential, non-overlapping.
# Every other key in timings_ms is nested inside one of these (model_export
# and sidecar_write_model inside model_pass, ...), so it is not summed again.
# What total holds beyond their sum -- the membership collection, detail-line
# hide/show, link-category discovery, the view-state and mark read-backs --
# is reported as ``unaccounted``, not hidden.
TIMING_PARTITION = ("authored_override_scan", "mark_layout", "marks_create",
                    "model_pass", "suppression", "annotation_pass", "rollback",
                    "restore_element_overrides", "view_membership_readback")


def unaccounted_ms(timings_ms):
    """PURE. ``total`` minus the partition phases that were timed."""
    total = timings_ms.get("total")
    if total is None:
        return None
    return round(float(total) - sum(float(timings_ms[k]) for k in TIMING_PARTITION
                                    if timings_ms.get(k) is not None), 3)


def view_membership_ids(doc, view):
    """Sorted ids FilteredElementCollector(doc, view) returns: the view's
    element membership as Revit reports it. Raises when it cannot be read."""
    from Autodesk.Revit.DB import FilteredElementCollector
    ids = (_element_id_int(getattr(e, "Id", None))
           for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType())
    return sorted(i for i in ids if i is not None)


def membership_fingerprint(ids):
    """PURE. ``{"count", "sha1"}`` of an id list (sorted here) -- written so one run's
    view membership can be compared with the next run's."""
    ids = sorted(int(i) for i in ids)
    digest = hashlib.sha1(",".join(str(i) for i in ids).encode("ascii"))
    return {"count": len(ids), "sha1": digest.hexdigest()}


def view_membership_verdict(before, after, limit=200):
    """PURE. What the view's membership read after the rollback adds to or
    removes from the membership read before the capture."""
    before_set, after_set = set(before), set(after)
    added = sorted(after_set - before_set)
    removed = sorted(before_set - after_set)
    return {"status": "changed" if (added or removed) else "unchanged",
            "before": membership_fingerprint(before),
            "after": membership_fingerprint(after),
            "added_count": len(added), "removed_count": len(removed),
            "added": added[:limit], "removed": removed[:limit]}


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
        # T1: the line style (and projection weight) the ticks were drawn at.
        # Chosen in create_registration_marks but, until this, never written
        # to a sidecar (pipeline_0930_0739: absent from all 16).
        "line_style": (record.get("marks") or {}).get("line_style"),
        # How many annotation bboxes the ticks were kept clear of, and the
        # cost of reading them. Per-tick placement rides on each mark.
        "mark_avoidance": record.get("mark_avoidance"),
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
        # P2: filled since the registered capture shipped, never written
        # (pipeline_0930_1133: {} on every sidecar). Complete here: the
        # payload is built after total. What it cannot hold is the duration
        # of the registration_marks / capture_integrity writes themselves,
        # which happen after it is built.
        "timings_ms": dict(record.get("timings_ms") or {}),
        "timing_partition": list(TIMING_PARTITION),
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
    membership_before = None
    detail_lines = {"ids": [], "hidden": []}

    def _fault(fault, message, exc=None):
        record["faults"].append({"fault": fault, "message": message,
                                 "error": (None if exc is None else "{0}: {1}".format(
                                     type(exc).__name__, exc))})
        if diag is not None:
            diag.error(phase="color_id_buffer", callsite="registered_capture",
                       message="{0}: {1}".format(fault, message),
                       view_id=view_id, exc=exc)

    def _pass_timings(name, out):
        # The pass's export and its own sidecar write, measured inside the
        # pass (nested in model_pass / annotation_pass, not additional).
        timings = (out or {}).get("timings") or {}
        for key, label in (("export_ms", "{0}_export"),
                           ("sidecar_write_ms", "sidecar_write_{0}")):
            if timings.get(key) is not None:
                record["timings_ms"][label.format(name)] = timings[key]

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
        # The view's membership BEFORE anything is written, for the
        # read-back after the rollback (pipeline_0930_1133 -> 1249:
        # Plan_RVTLink's collector returned 37 fewer elements at the start
        # of the next run, and nothing measured whether a run changed it).
        membership_before = sorted(
            i for i in (_element_id_int(getattr(e, "Id", None)) for e in collected)
            if i is not None)
        model_members, _anno, unresolved, _basis = split_stage_a_pass_membership(
            collected, capture_view_id_int=view_id, diag=diag)
        model_member_ids = [i for i in (_element_id_int(getattr(e, "Id", None))
                                        for e in model_members) if i is not None]
        record["membership"] = {"model": len(model_member_ids),
                                "annotation": len(_anno),
                                "unresolved": len(unresolved)}
        line_ids, lines_error = registration.detail_line_ids(_anno)
        if lines_error is not None:
            _fault("detail_lines_unresolved",
                   "the view's detail lines could not be identified, so they may "
                   "draw in the model capture: {0}".format(lines_error))
        detail_lines["ids"] = list(line_ids or [])
        _t = time.time()
        record["_authored_before"] = _non_blank_override_ids(view, model_member_ids)
        record["timings_ms"]["authored_override_scan"] = round(
            (time.time() - _t) * 1000.0, 3)

        # ---- 1: registration marks -----------------------------------------
        _t = time.time()
        reference, source = mark_reference_rectangle(view, raster, diag=diag,
                                                     view_id=view_id)
        record["mark_reference_source"] = source
        mark_fpp, record["mark_fpp_basis"] = mark_fpp_ft(view, raster, cfg)
        layout = (registration.registration_mark_segments(reference, mark_fpp)
                  if reference is not None else {"state": "unavailable",
                                                 "reason": source})
        # Ticks clear of annotation (Greg, 2026-09-30): a tick under a tag or
        # dimension does not show in the annotation capture.
        avoid, record["mark_avoidance"] = annotation_avoid_rects(
            view, _anno, getattr(raster, "view_basis", None), diag=diag,
            view_id=view_id)
        layout = registration.relocate_marks_clear_of(layout, avoid)
        # P2: reference rectangle, sizing, segments and avoidance together.
        record["timings_ms"]["mark_layout"] = round((time.time() - _t) * 1000.0, 3)
        _t = time.time()
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
        record["timings_ms"]["marks_create"] = round((time.time() - _t) * 1000.0, 3)
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

        # ---- 1b: the view's OTHER detail lines, hidden for the model pass --
        # OST_Lines stays visible there so the marks draw; without this every
        # other detail line -- annotation -- would draw into the model image
        # unpainted. Hidden only for the model pass, shown again before the
        # annotation pass, which is where they belong; the group's rollback
        # is the backstop, and the read-back below checks it.
        if detail_lines["ids"]:
            tx = Transaction(doc, "VOP Stage A hide detail lines for the model pass")
            tx.Start()
            try:
                hide = registration.hide_in_view(doc, view, detail_lines["ids"])
                if tx.Commit() != TransactionStatus.Committed:
                    raise RuntimeError("detail-line Transaction.Commit did not commit")
            except Exception:
                tx.RollBack()
                raise
            detail_lines["hidden"] = list(hide["hidden"])
            record["detail_lines"] = {
                "count": len(detail_lines["ids"]),
                "hidden_for_model_pass": len(hide["hidden"]),
                "already_hidden": len(hide["already_hidden"]),
                "unreadable": hide["unreadable"][:50],
                "hide_error": hide["error"],
            }
            if hide["error"] or hide["unreadable"]:
                _fault("detail_lines_may_draw_in_model_capture",
                       "not every detail line could be hidden for the model pass "
                       "({0}; {1} unreadable)".format(
                           hide["error"], len(hide["unreadable"])))
        else:
            record["detail_lines"] = {"count": 0}

        # ---- 2: MODEL pass, OST_Lines visible so the marks draw ------------
        model_cfg = copy.copy(cfg)
        model_cfg.color_id_buffer_model_lines_visible = True
        # C7: sized from crop A alone; frame B is not computed or recorded.
        model_cfg.color_id_buffer_model_frame = "crop_a"
        geom = {}
        _t = time.time()
        model_out = export_color_id_buffer_view(
            doc, view, elements, model_cfg, diag=diag, raster=raster,
            elem_cache=elem_cache, geometry_out=geom)
        record["timings_ms"]["model_pass"] = round((time.time() - _t) * 1000.0, 3)
        _pass_timings("model", model_out)
        if detail_lines["hidden"]:
            tx = Transaction(doc, "VOP Stage A show detail lines for the annotation pass")
            tx.Start()
            try:
                show_error = registration.show_in_view(view, detail_lines["hidden"])
                if tx.Commit() != TransactionStatus.Committed:
                    raise RuntimeError("detail-line Transaction.Commit did not commit")
            except Exception:
                tx.RollBack()
                raise
            if show_error is not None:
                _fault("detail_lines_missing_from_annotation_capture",
                       "the detail lines hidden for the model pass could not be "
                       "shown again before the annotation pass: {0}".format(show_error))
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
        # Anti-aliasing OFF, as the model pass already runs (Greg,
        # 2026-09-30): a colour-ID capture should not be blended, and the
        # registration tool's tick-blend handling is the FALLBACK for a host
        # that cannot turn it off, not the plan. pipeline_0930_0739's
        # annotation sidecars read applied_smooth_edges "not_attempted".
        anno_cfg.color_id_buffer_anno_smooth_edges_off = True
        _t = time.time()
        try:
            # The ticks are the capture's own lines, drawn with its own line
            # style: not an AUTHORED override (1249: "12 of N" on every view
            # was exactly the 12 ticks).
            anno_out = export_annotation_color_id_buffer_view(
                doc, view, anno_cfg, geom, diag=diag, raster=raster,
                authored_check_exclude_ids=[
                    m.get("id") for m in (record.get("marks") or {}).get("created") or []
                    if m.get("id") is not None])
        except Exception as ex:
            _fault("annotation_pass_raised", "the annotation pass raised", ex)
            anno_out = {"view_id": view_id, "success": False,
                        "failure_reason": "annotation_pass_raised",
                        "error": "{0}: {1}".format(type(ex).__name__, ex),
                        "stage": "color_id_buffer_stage_a_annotation",
                        "tiff_path": None, "sidecar_path": None}
        record["timings_ms"]["annotation_pass"] = round((time.time() - _t) * 1000.0, 3)
        _pass_timings("annotation", anno_out)
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
            record["timings_ms"]["rollback"] = restore["rollback_ms"]
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
        if detail_lines["hidden"]:
            left_hidden, unreadable_lines = registration.still_hidden(
                doc, view, detail_lines["hidden"])
            restore["detail_lines"] = {"left_hidden": left_hidden[:200],
                                       "unreadable": unreadable_lines[:200]}
            if left_hidden:
                _fault("detail_lines_left_hidden",
                       "{0} detail line(s) this capture hid are still hidden".format(
                           len(left_hidden)))
            elif unreadable_lines:
                _fault("detail_lines_unverified",
                       "{0} detail line(s) could not be read back".format(
                           len(unreadable_lines)))
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
            record["timings_ms"]["restore_element_overrides"] = (
                restore["element_overrides"]["elapsed_ms"])
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
        # ---- 6b: the view's MEMBERSHIP, read back -----------------------
        # A run must leave the view showing what it showed. None of the
        # read-backs above looks at which elements the view collector
        # returns; this does, against the list read at the start.
        if membership_before is not None:
            _t = time.time()
            try:
                restore["view_membership"] = view_membership_verdict(
                    membership_before, view_membership_ids(doc, view))
            except Exception as ex:
                restore["view_membership"] = {
                    "status": "unverified",
                    "before": membership_fingerprint(membership_before),
                    "reason": "{0}: {1}".format(type(ex).__name__, ex)}
                _fault("view_membership_unverified",
                       "the view's elements could not be read back after the "
                       "rollback", ex)
            record["timings_ms"]["view_membership_readback"] = round(
                (time.time() - _t) * 1000.0, 3)
            verdict = restore["view_membership"]
            if verdict.get("status") == "changed":
                _fault("view_membership_changed",
                       "after the rollback the view returns {0} element(s) it did "
                       "not before and lacks {1} it had; first added {2}, first "
                       "removed {3}".format(
                           verdict["added_count"], verdict["removed_count"],
                           verdict["added"][:5], verdict["removed"][:5]))
        record["restore"] = restore
        record["timings_ms"]["total"] = round((time.time() - t_total) * 1000.0, 3)
        record["timings_ms"]["unaccounted"] = unaccounted_ms(record["timings_ms"])
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
        # P1: the integrity record, completed with the rollback verdict and
        # the faults above -- still after the read-back, so it is final. A
        # write failure (registration_marks above, or a completion here) is a
        # fault of THIS capture, so every sidecar already completed is
        # completed AGAIN once one is known (the write is idempotent).
        # Otherwise the surviving sidecar called an incomplete registered
        # capture clean (Codex, PR #221).
        reported = set()

        def _record_write_failures():
            added = False
            for name, error in sorted(record["sidecar_writes"].items()):
                if error is not None and name not in reported:
                    reported.add(name)
                    _fault("registration_sidecar_write_failed",
                           "{0} sidecar: {1}".format(name, error))
                    record["success"] = False
                    added = True
            return added

        completed = []
        for name, out in (("model", model_out), ("annotation", anno_out)):
            if out and out.get("sidecar_path") and record["sidecar_writes"].get(name) is None:
                record["sidecar_writes"][name] = registration.complete_capture_integrity(
                    out["sidecar_path"], record)
                if record["sidecar_writes"][name] is None:
                    completed.append((name, out))
        if _record_write_failures():
            for name, out in completed:
                record["sidecar_writes"][name] = registration.complete_capture_integrity(
                    out["sidecar_path"], record)
            _record_write_failures()

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
