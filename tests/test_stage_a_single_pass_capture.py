"""The registered capture runs only the passes a view's membership needs (S2).

Held to the S0 baseline (tests/stage_a_single_pass_fixtures.py): the
normalised sidecars each fixture wrote at 8e1c1fb, before this change.

  * GATE 1 -- a view with both memberships (``model``) writes sidecars equal
    to S0's in every value but the added ``capture_passes``.
  * GATE 2 -- the drafting view (``drafting``, model membership 0) writes NO
    model TIFF or sidecar, and its annotation sidecar equals S0's annotation
    sidecar once S0's tick records are taken out (``_without_ticks``) and the
    declared keys added. Its export SAW no tick in the document.
  * ``model_only`` (annotation membership 0) writes the model sidecar alone,
    equal to S0's model sidecar without its tick records.

Every assertion reads the FILES (CLAUDE.md, defect class 4).
"""
import copy
import os

import pytest

from vop_interwoven import capture_passes as cp
from vop_interwoven import stage_a_registration as registration

from tests import stage_a_single_pass_fixtures as fx
from tests import test_probe_anno_pass_run_variant as world

DECLARED = ("capture_passes", "skipped_pass", "single_pass_frame")


def _drop(d, *keys):
    d = copy.deepcopy(d)
    for k in keys:
        d.pop(k, None)
    return d


TICK_ONLY_KEYS = ("marks", "layout", "line_style", "mark_avoidance", "lines_unhidden",
                  "crop_shape", "ticks_outside_crop_loop", "reference_source",
                  "annotation_extent_prediction")
# What only the SKIPPED pass produces in the record of the pass that ran: the
# white suppression (it prepares the annotation pass of a view whose model was
# captured) and the read-back of the detail lines hidden for the model pass.
SKIPPED_PASS_KEYS = (("registration_marks", "model_suppression"),
                     ("registration_marks", "restore", "detail_lines"))


def _pop_path(d, path):
    for k in path[:-1]:
        d = d.get(k) or {}
    return d.pop(path[-1], None)


def _without_ticks(sidecar):
    """A sidecar with what the twelve ticks put in it taken out, so S0 can be
    compared with a capture that drew none. The ticks are identified by the
    sidecar's OWN record (``registration_marks.marks``), never by id range:

      * registration_marks: the tick list and the facts that exist only
        because ticks were drawn (TICK_ONLY_KEYS);
      * the annotation pass COLLECTS the ticks as view-owned lines, so each
        is an ``annotation_bbox_map`` entry and is counted in ``membership``
        and ``annotation_bbox_status``: the entries are removed and each count
        lowered by exactly the number of ticks;
      * palette_reservation.mark_ids_painted.

    The capture's two white frame-bound lines are temporary elements too, and
    their ids depend on how many lines were drawn before them, so they are
    keyed by ROLE (``bound:<key>``) in both records, never by id. Nothing else
    is touched."""
    s = copy.deepcopy(sidecar)
    marks = s.get("registration_marks") or {}
    ticks = set(str(m["id"]) for m in marks.get("marks") or [] if m.get("id") is not None)
    bounds = (marks.get("frame_bounds") or {}).get("created") or []
    role = dict((str(b["id"]), "bound:" + b["key"]) for b in bounds if "id" in b)
    for b in bounds:
        b.pop("id", None)
    for k in TICK_ONLY_KEYS:
        marks.pop(k, None)
    bbox = s.get("annotation_bbox_map")
    if isinstance(bbox, dict):
        s["annotation_bbox_map"] = dict((role.get(k, k), v) for k, v in bbox.items()
                                        if k not in ticks)
    n = len(ticks)
    for path in (("membership", "annotation_count"),
                 ("membership", "basis_counts", "owner_view"),
                 ("annotation_bbox_status", "count"),
                 ("annotation_bbox_status", "bbox_source_counts", "view"),
                 ("annotation_bbox_status", "membership_basis_counts", "owner_view")):
        d = s
        for k in path[:-1]:
            d = d.get(k) if isinstance(d, dict) else None
        if isinstance(d, dict) and path[-1] in d:
            d[path[-1]] -= n
    pr = s.get("palette_reservation") or {}
    pr.pop("mark_ids_painted", None)
    if "blank_ids_painted" in pr:
        pr["blank_ids_painted"] = sorted(role.get(str(i), i) for i in pr["blank_ids_painted"])
    return s


def _without_skipped_pass(sidecar):
    s = copy.deepcopy(sidecar)
    for path in SKIPPED_PASS_KEYS:
        _pop_path(s, path)
    return s


def _run(name, tmp_path):
    out = fx.run_fixture(name, tmp_path)[0]
    return out, fx.sidecars(out)


# --- GATE 1 ------------------------------------------------------------------

def test_gate1_a_two_pass_view_is_S0_plus_capture_passes(tmp_path):
    out, now = _run("model", tmp_path)
    base = fx.s0("model")
    assert out["capture_passes"] == ["model", "annotation"]
    assert out["skipped_pass"] is None
    for which in ("model", "annotation"):
        side = fx.normalise(now[which])
        assert side["capture_passes"] == ["model", "annotation"]
        assert "skipped_pass" not in side and "single_pass_frame" not in side
        assert _drop(side, "capture_passes") == base[which], which


def test_gate1_control_the_baseline_is_not_trivially_equal(tmp_path):
    """The comparison discriminates: the two-pass baseline's ticks are 12 in
    both files, so a capture that drew none would not equal it."""
    base = fx.s0("model")
    for which in ("model", "annotation"):
        assert len(base[which]["registration_marks"]["marks"]) == 12


# --- GATE 2 ------------------------------------------------------------------

def test_gate2_a_drafting_view_runs_the_annotation_pass_alone(tmp_path):
    out, view, _doc, exports, _diag = fx.run_fixture("drafting", tmp_path)
    reg = out["registration"]
    assert reg["faults"] == [], reg["faults"]
    assert out["success"] is True and out["annotation_pass_success"] is True
    assert out["capture_passes"] == ["annotation"]
    assert out["skipped_pass"]["skipped"] == "model"
    assert out["skipped_pass"]["model_count"] == 0
    assert out["skipped_pass"]["annotation_count"] == 3
    # No model files: none named, none on disk.
    assert out["tiff_path"] is None and out["sidecar_path"] is None
    folder = os.path.dirname(out["annotation_sidecar_path"])
    assert sorted(os.listdir(folder)) == ["TestPlan_77_anno.json",
                                          "TestPlan_77_anno.tiff"]
    # ONE export, and it saw no tick: only the two white frame bounds are the
    # capture's own lines.
    assert len(exports) == 1
    assert reg["marks"]["created_count"] == 0
    bound_ids = [b["id"] for b in reg["frame_bounds"]["created"]]
    assert sorted(i for i in exports[0] if i > world.MARK_ID_BASE) == sorted(bound_ids)
    assert all(exports[0][i] == (255, 255, 255) for i in bound_ids)
    assert registration.MARK_COLOUR not in exports[0].values()
    # Restored like any capture.
    assert reg["restore"]["rolled_back"] is True
    assert reg["restore"]["marks_still_in_project"] == []


def test_gate2_the_annotation_sidecar_is_S0s_without_the_ticks(tmp_path):
    out, now = _run("drafting", tmp_path)
    assert now["model"] is None
    base = fx.s0("drafting")
    side = fx.normalise(now["annotation"])
    # The declared keys, from the FILE.
    assert side["capture_passes"] == ["annotation"]
    assert side["skipped_pass"] == out["skipped_pass"]
    frame = side["single_pass_frame"]
    assert frame["frame_source"] == "frame_bounds"
    assert frame["sizing_frame"] == "crop_a"
    # The frame is the rectangle the S0 MODEL pass rendered: crop A.
    assert frame["crop_uv"] == base["model"]["frame"]["crop_uv"]
    assert frame["crop_px"] == base["model"]["frame"]["crop_px"]
    assert frame["achieved_fpp_ft"] == base["model"]["frame"]["achieved_fpp_ft"]
    # Value-identical to S0's annotation sidecar, ticks aside. The palette
    # included: every element keeps its S0 colour.
    expected = _without_ticks(base["annotation"])
    # The skipped model pass: no suppression ran, no detail line was hidden.
    assert side["registration_marks"]["model_suppression"] is None
    assert "detail_lines" not in side["registration_marks"]["restore"]
    got = _without_ticks(_drop(side, *DECLARED))
    assert _without_skipped_pass(got) == _without_skipped_pass(expected)
    # The S0 record really did hold the ticks as annotation members: 3 + 12 + 2.
    assert base["annotation"]["membership"]["annotation_count"] == 17
    assert side["membership"]["annotation_count"] == 5
    assert side["color_assignment_map"] == base["annotation"]["color_assignment_map"]
    assert side["palette_reservation"]["mark_ids_painted"] == []
    rm = side["registration_marks"]
    assert rm["marks"] == [] and rm["layout"] is None and rm["mark_avoidance"] is None
    assert rm["lines_unhidden"]["state"] == "not_needed"


def test_gate2_control_S0s_drafting_view_drew_twelve_ticks(tmp_path):
    base = fx.s0("drafting")
    assert len(base["annotation"]["registration_marks"]["marks"]) == 12
    assert len(base["annotation"]["palette_reservation"]["mark_ids_painted"]) == 12
    assert base["_membership"] == {"model": 0, "annotation": 3, "unresolved": 0}


# --- the model-only view -----------------------------------------------------

def test_a_view_with_no_annotation_runs_the_model_pass_alone(tmp_path):
    out, view, doc, exports, _diag = fx.run_fixture("model_only", tmp_path)
    assert out["registration"]["faults"] == []
    assert out["success"] is True and out["registration_success"] is True
    assert out["capture_passes"] == ["model"]
    assert out["skipped_pass"]["skipped"] == "annotation"
    assert "annotation_pass" not in out
    assert len(exports) == 1
    # No tick, no white suppression: the export saw the view's own members
    # in their palette colours.
    assert exports[0]["marks_in_doc"] == []
    assert (255, 255, 255) not in exports[0]["overrides"].values()
    now = fx.sidecars(out)
    assert now["annotation"] is None
    folder = os.path.dirname(out["sidecar_path"])
    assert not [n for n in os.listdir(folder) if "_anno" in n]
    side = fx.normalise(now["model"])
    assert side["capture_passes"] == ["model"]
    assert side["skipped_pass"]["annotation_count"] == 0
    assert "single_pass_frame" not in side
    assert side["registration_marks"]["model_suppression"] is None
    assert _without_skipped_pass(_without_ticks(_drop(side, *DECLARED))) == \
        _without_skipped_pass(_without_ticks(fx.s0("model_only")["model"]))


# --- the declared record, and the timing partition --------------------------

def test_the_skip_is_in_the_FILE_and_reads_back_through_the_one_reader(tmp_path):
    out, now = _run("drafting", tmp_path)
    passes, skipped, error = cp.declared_passes(now["annotation"])
    assert error is None and passes == ["annotation"] and skipped["skipped"] == "model"


@pytest.mark.parametrize("name,absent", [
    ("drafting", ("lines_unhide", "mark_layout", "mark_layout.avoid_rects",
                  "model_pass", "model_export", "sidecar_write_model", "suppression")),
    ("model_only", ("lines_unhide", "mark_layout", "mark_layout.avoid_rects",
                    "marks_create", "suppression", "annotation_pass",
                    "annotation_export", "sidecar_write_annotation")),
])
def test_skipped_phases_are_absent_from_the_timings_and_the_partition_holds(
        tmp_path, name, absent):
    out = fx.run_fixture(name, tmp_path)[0]
    t = out["registration"]["timings_ms"]
    for key in absent:
        assert key not in t, key
    from vop_interwoven.stage_a_registered_capture import TIMING_PARTITION
    timed = sum(t[k] for k in TIMING_PARTITION if t.get(k) is not None)
    assert abs(t["total"] - timed - t["unaccounted"]) < 1e-6
    assert t["unaccounted"] >= -1e-6


def test_the_two_pass_timings_keep_every_phase(tmp_path):
    t = fx.run_fixture("model", tmp_path)[0]["registration"]["timings_ms"]
    for key in ("lines_unhide", "mark_layout", "marks_create", "model_pass",
                "suppression", "annotation_pass"):
        assert key in t, key


# --- the lattice an annotation-only view is sized on -------------------------

def test_the_annotation_only_lattice_is_the_model_passs(tmp_path):
    """crop_a_export_geometry() against what the model pass PUBLISHED for the
    same view (geometry_out), composed rather than copied (defect class 1)."""
    from vop_interwoven import color_id_buffer as cib
    captured = {}
    real = cib.export_color_id_buffer_view

    def _spy(*a, **k):
        result = real(*a, **k)
        captured["geom"] = dict(k["geometry_out"])
        # Inside the capture, while the fake DB is installed.
        captured["helper"] = cib.crop_a_export_geometry(a[1], k["raster"], a[3])
        return result
    cib.export_color_id_buffer_view = _spy
    try:
        fx.run_fixture("model", tmp_path)
    finally:
        cib.export_color_id_buffer_view = real
    published = dict((k, v) for k, v in captured["geom"].items()
                     if not k.startswith("model_"))
    assert captured["helper"] == published


# --- every source of annotation ink keeps its pass ---------------------------

def test_a_view_whose_only_annotation_is_a_datum_keeps_the_annotation_pass(tmp_path):
    """A grid is ownerless: it reaches the annotation pass by CATEGORY, not by
    OwnerViewId. Mutation (c): count annotation membership by owner_view
    alone -> this view is skipped as annotation-empty, red."""
    out = fx.run_fixture("datum_only", tmp_path)[0]
    plan = out["registration"]["capture_pass_plan"]
    assert plan["counts"]["annotation_count"] == 1, plan
    assert out["capture_passes"] == ["model", "annotation"]
    assert out["skipped_pass"] is None
    assert out["annotation_pass_success"] is True
    anno = fx.sidecars(out)["annotation"]
    assert "4001" in anno["color_assignment_map"]
    assert anno["membership"]["basis_counts"]["datum_category"] == 1


def test_control_the_same_view_without_the_datum_is_single_pass(tmp_path):
    assert fx.run_fixture("model_only", tmp_path)[0]["capture_passes"] == ["model"]


def test_an_element_of_unknown_pass_runs_both(tmp_path):
    """Unknown is never empty: an element whose OwnerViewId will not read could
    be annotation ink."""
    out = fx.run_fixture("unresolved_only", tmp_path)[0]
    plan = out["registration"]["capture_pass_plan"]
    assert plan["counts"]["unresolved_count"] == 1
    assert plan["decision"] == "membership_unresolved"
    assert out["capture_passes"] == ["model", "annotation"]
