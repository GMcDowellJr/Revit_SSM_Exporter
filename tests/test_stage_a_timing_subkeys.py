"""C3: the next stall names its call afterwards.

Batch 2026-10-21: stall A spent 4,555 s in annotation_avoid_rects (the
record kept only the total over 462 rects) and stall B 2,432 s in
model_graphics_state (one lap, no breakdown). The parts are now timed as
sub-keys ``<parent>.<part>`` nested in their parent, and the avoidance keeps
its slowest elements. Asserted on the sidecar FILES (CLAUDE.md, defect
class 4); each sub-key is shown to receive a known injected delay and its
neighbours not to.
"""
import json
import time

from vop_interwoven import color_id_buffer as cib
from vop_interwoven import stage_a_registered_capture as rc
from tests.stage_a_capture_fakes import FakeElement
from tests.test_stage_a_registered_capture import ANNO_CAT, VIEW_ID, _BBox, _run

# The partition as it stood at C0 (f1843ad). Sub-keys never join it.
C0_PARTITION = ("authored_override_scan", "split_remove", "lines_unhide",
                "co_sizing_first_attempt", "mark_layout",
                "marks_create", "model_pass", "suppression", "annotation_pass",
                "attempt_rollback", "rollback",
                "restore_element_overrides", "view_membership_readback")

GRAPHICS_STATE_PARTS = ("view_phase", "phase_filter", "phase_status_presentation",
                        "category_overrides", "view_filters", "underlay",
                        "view_template_id", "detail_level", "display_style",
                        "link_instances")
EXPAND_PARTS = ("expand_elements", "expand_elements.host_loop",
                "expand_elements.host_loop.bbox", "expand_elements.host_loop.fingerprint",
                "expand_elements.linked", "split_expanded", "resolve_all",
                "partition_view_specific_imports", "scan_view_specific_imports")
DELAY_MS = 25.0


def _timings(path):
    with open(path) as handle:
        return json.load(handle)["registration_marks"]["timings_ms"]


def _children(timings):
    sep = rc.SUBKEY_SEPARATOR
    out = {}
    for key in timings:
        if sep in key:
            out.setdefault(key.rsplit(sep, 1)[0], []).append(key)
    return out


def test_no_sub_key_is_ever_in_the_partition():
    """Turns red if a sub-key -- or anything else -- is added to the partition."""
    assert rc.TIMING_PARTITION == C0_PARTITION
    assert not any(rc.SUBKEY_SEPARATOR in k for k in rc.TIMING_PARTITION)


def test_every_sub_key_sits_inside_its_parent_in_both_FILES(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        timings = _timings(path)
        for part in GRAPHICS_STATE_PARTS:
            assert "model_graphics_state." + part in timings, part
        for part in EXPAND_PARTS:
            assert "model_expand." + part in timings, part
        assert "mark_layout.avoid_rects" in timings
        for parent, kids in _children(timings).items():
            assert parent in timings, (parent, kids)
            assert sum(timings[k] for k in kids) <= timings[parent] + 1e-2, (parent, kids)
        # The arithmetic the partition was built for still holds.
        partition = sum(timings[k] for k in rc.TIMING_PARTITION)
        assert abs(timings["unaccounted"] - (timings["total"] - partition)) < 1e-2


def _slow_view_reads(view):
    real = view.GetFilters

    def slow():
        time.sleep(DELAY_MS / 1000.0)
        return real()
    object.__setattr__(view, "GetFilters", slow)


def test_a_slow_graphics_state_read_lands_in_its_own_sub_key(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path, view_setup=_slow_view_reads)
    timings = _timings(out["sidecar_path"])
    assert timings["model_graphics_state.view_filters"] >= DELAY_MS
    assert all(timings["model_graphics_state." + p] < DELAY_MS
               for p in GRAPHICS_STATE_PARTS if p != "view_filters")
    assert timings["model_graphics_state"] >= DELAY_MS


def test_a_slow_expand_part_lands_in_its_own_sub_key(tmp_path, monkeypatch):
    real = cib.resolve_all

    def slow(*a, **k):
        time.sleep(DELAY_MS / 1000.0)
        return real(*a, **k)
    monkeypatch.setattr(cib, "resolve_all", slow)
    out, _v, _d, _e, _diag = _run(tmp_path)
    timings = _timings(out["sidecar_path"])
    assert timings["model_expand.resolve_all"] >= DELAY_MS
    assert all(timings["model_expand." + p] < DELAY_MS
               for p in EXPAND_PARTS if p != "resolve_all")


class _SlowTag(FakeElement):
    def get_BoundingBox(self, view):
        time.sleep(DELAY_MS / 1000.0)
        return FakeElement.get_BoundingBox(self, view)


def test_the_slowest_avoidance_call_is_named_in_both_FILES(tmp_path, monkeypatch):
    """Mutation (b): drop the slowest-element record and this turns red."""
    monkeypatch.setattr(rc, "AVOID_SLOW_CALL_MS", DELAY_MS / 2.0)
    tag = _SlowTag(2009, ANNO_CAT, owner_view_id=VIEW_ID,
                   bbox=_BBox((1.0, 1.0, 0), (1.5, 1.5, 0)))
    out, _v, _d, _e, _diag = _run(tmp_path, extra_elements=[tag])
    for path in (out["sidecar_path"], out["annotation_sidecar_path"]):
        with open(path) as handle:
            avoidance = json.load(handle)["registration_marks"]["mark_avoidance"]
        first = avoidance["slowest"][0]
        assert first["id"] == 2009
        assert first["category_id"] == ANNO_CAT.Id.IntegerValue
        assert first["ms"] >= DELAY_MS
        assert avoidance["calls_over_threshold"] == 1
        assert avoidance["slow_call_threshold_ms"] == DELAY_MS / 2.0
        assert len(avoidance["slowest"]) <= rc.AVOID_SLOWEST_N
        assert avoidance["calls_timed"] == avoidance["annotation_elements"] - (
            avoidance["excluded_imports"])
        timings = _timings(path)
        assert timings["mark_layout.avoid_rects"] >= DELAY_MS


def test_the_slowest_list_is_bounded_ordered_and_keeps_the_avoid_list_as_it_was():
    """Unit, on annotation_avoid_rects: N kept of more, slowest first, and
    the avoid list identical to the elements' own bboxes."""
    class _Timed(FakeElement):
        def __init__(self, elem_id, ms):
            FakeElement.__init__(self, elem_id, ANNO_CAT, owner_view_id=VIEW_ID,
                                 bbox=_BBox((0.0, 0.0, 0), (1.0, 1.0, 0)))
            self._ms = ms

        def get_BoundingBox(self, view):
            time.sleep(self._ms / 1000.0)
            return FakeElement.get_BoundingBox(self, view)

    elems = [_Timed(3000 + i, ms) for i, ms in enumerate((1, 12, 2, 9, 1, 6, 3, 15))]
    avoid, record = rc.annotation_avoid_rects(object(), elems, None)
    assert [s["id"] for s in record["slowest"]] == [3007, 3001, 3003, 3005, 3006]
    assert len(record["slowest"]) == rc.AVOID_SLOWEST_N
    assert record["calls_over_threshold"] == 0
    assert record["calls_timed"] == len(elems)
    # basis None: no corners, so every element is no_bbox -- as before C3.
    assert avoid == [] and record["no_bbox"] == len(elems)
