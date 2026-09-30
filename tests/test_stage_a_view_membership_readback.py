"""A run leaves the view showing what it showed -- measured, in the FILE.

pipeline_0930_1133 -> 1249: Plan_RVTLink's view collector returned 37 fewer
elements at the start of the second run, before any capture work, and no
check could say whether a run had changed it. The registered capture now
reads the view's membership (FilteredElementCollector(doc, view)) before it
writes anything and again after the rollback, records both fingerprints, and
faults on any difference.

Also here: the registered capture's own ticks are not "authored overrides"
(1249: "12 of N annotation element(s) carried an AUTHORED graphics override"
on every view was exactly the 12 ticks).
"""
import json

import vop_interwoven.stage_a_registered_capture as rc

from tests.stage_a_capture_fakes import FakeOGS
from tests.test_stage_a_registered_capture import _run


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def _membership(out):
    return _file(out["sidecar_path"])["registration_marks"]["restore"]["view_membership"]


def _faults(out):
    return [f["fault"] for f in
            _file(out["sidecar_path"])["registration_marks"]["faults"]]


def test_a_clean_capture_reads_the_same_membership_back_in_the_FILE(tmp_path):
    out, _v, doc, _e, _diag = _run(tmp_path)
    verdict = _membership(out)
    assert verdict["status"] == "unchanged"
    assert verdict["before"] == verdict["after"]
    assert verdict["before"]["count"] == len(doc._by_id)
    assert len(verdict["before"]["sha1"]) == 40
    assert "view_membership_changed" not in _faults(out)
    timings = _file(out["sidecar_path"])["registration_marks"]["timings_ms"]
    assert "view_membership_readback" in timings


def test_an_element_the_capture_removed_and_did_not_restore_is_a_fault(tmp_path, monkeypatch):
    """The document is NOT rolled back here; an element removed during the
    capture stays removed. The read-back names it."""
    from vop_interwoven import color_id_buffer as cib
    real_anno = cib.export_annotation_color_id_buffer_view
    holder = {}

    def _leaky(doc, *a, **k):
        result = real_anno(doc, *a, **k)
        doc._by_id.pop(1003, None)
        holder["doc"] = doc
        return result
    monkeypatch.setattr("vop_interwoven.color_id_buffer."
                        "export_annotation_color_id_buffer_view", _leaky)
    out, _v, _d, _e, _diag = _run(tmp_path, rollback_restores=("view",))
    verdict = _membership(out)
    assert verdict["status"] == "changed"
    assert 1003 in verdict["removed"] and verdict["removed_count"] >= 1
    assert "view_membership_changed" in _faults(out)
    integrity = _file(out["annotation_sidecar_path"])["capture_integrity"]
    assert "view_membership_changed" in [f["fault"] for f in integrity["capture_faults"]]
    assert out["registration_success"] is False


def test_ticks_left_in_the_document_are_the_added_elements(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path, rollback_restores=("view",))
    verdict = _membership(out)
    mark_ids = sorted(m["id"] for m in out["registration"]["marks"]["created"])
    assert verdict["added"] == mark_ids and verdict["removed"] == []


def test_an_unreadable_read_back_is_unverified_not_unchanged(tmp_path, monkeypatch):
    def _boom(doc, view):
        raise RuntimeError("collector gone")
    monkeypatch.setattr(rc, "view_membership_ids", _boom)
    out, _v, _d, _e, _diag = _run(tmp_path)
    verdict = _membership(out)
    assert verdict["status"] == "unverified" and "collector gone" in verdict["reason"]
    assert "view_membership_unverified" in _faults(out)


def test_the_verdict_is_pure_and_exact():
    v = rc.view_membership_verdict([1, 2, 3], [2, 3, 4])
    assert (v["status"], v["added"], v["removed"]) == ("changed", [4], [1])
    same = rc.view_membership_verdict([3, 1], [1, 3])
    assert same["status"] == "unchanged" and same["before"] == same["after"]
    assert rc.membership_fingerprint([1, 2]) != rc.membership_fingerprint([1, 3])
