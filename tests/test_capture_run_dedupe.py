"""capture_run.run_capture captures each listed view ONCE (2026-11-16).

Gate 4 of PR #258 (S_run1 / S_run2) listed view 18363657 twice in the
thinrunner's IN[0]: it was captured twice, the second capture overwriting the
first's files, and run_meta, views_core and the timing report held two
entries for one set of files (run 1 paid ~170 s for the discarded capture).

Asserted on what the capture loop is HANDED and on the run_meta FILE.
Mutation wired: returning the ids unchanged turns the first test red.
"""
import json

import pytest

from vop_interwoven import capture_run, output_layout
from vop_interwoven.config import Config


class _Doc(object):
    Title = "FVOT_ BLDG 1_detached"


def _run(monkeypatch, tmp_path, view_ids):
    handed = []

    def fake_run(doc, ids, cfg, output_dir, identity=None, **kw):
        handed.append(list(ids))
        run = tmp_path / "{0}__{1}".format(identity["as_of_date"], identity["run_id"])
        run.mkdir()
        (run / "run_meta_{0}.json".format(identity["as_of_date"])).write_text(json.dumps(
            {"run_id": identity["run_id"], "date": identity["as_of_date"],
             "finalized": True, "views_requested": list(ids),
             "views": [{"view_id": v, "capture_status": "success"} for v in ids]}))
        return {}, str(run), identity["run_id"]

    monkeypatch.setattr(capture_run, "_run_capture", fake_run)
    _res, run_dir, _rid = capture_run.run_capture(_Doc(), view_ids, Config(), str(tmp_path),
                                                  date_override="2026-11-16")
    path, _ = output_layout.find_run_meta(run_dir)
    with open(path) as handle:
        return handed, json.load(handle)


def test_a_view_listed_twice_is_captured_once_and_the_run_says_so(monkeypatch, tmp_path):
    handed, meta = _run(monkeypatch, tmp_path, [13521188, 18363657, 18363657, 14320979])
    assert handed == [[13521188, 18363657, 14320979]]
    assert meta["views_requested"] == [13521188, 18363657, 14320979]
    assert [v["view_id"] for v in meta["views"]].count(18363657) == 1
    assert meta["view_ids_deduplicated"] == {
        "schema": capture_run.DEDUPE_SCHEMA, "requested": 4, "unique": 3,
        "removed": [{"view_id": 18363657, "times_listed": 2}]}


def test_control_a_list_without_duplicates_is_unchanged_and_recorded(monkeypatch, tmp_path):
    handed, meta = _run(monkeypatch, tmp_path, [3, 1, 2])
    assert handed == [[3, 1, 2]]                       # order kept, nothing sorted
    assert meta["view_ids_deduplicated"]["removed"] == []
    assert meta["view_ids_deduplicated"]["requested"] == 3


@pytest.mark.parametrize("ids,unique", [
    ([5, "5", 5.0], [5]),             # one view however its id is spelled
    ([7, 8, 7, 9, 8], [7, 8, 9]),     # first position kept
    (["x", "x"], ["x"]),              # an id that is not an int is still one view
    ([], []),
])
def test_dedupe_view_ids(ids, unique):
    out, record = capture_run.dedupe_view_ids(ids)
    assert out == unique and record["unique"] == len(unique)
    assert record["requested"] == len(ids)


def test_a_run_meta_that_cannot_be_stamped_is_raised(monkeypatch, tmp_path):
    def fake_run(doc, ids, cfg, output_dir, identity=None, **kw):
        return {}, str(tmp_path / "no_such_run"), identity["run_id"]
    monkeypatch.setattr(capture_run, "_run_capture", fake_run)
    with pytest.raises(RuntimeError):
        capture_run.run_capture(_Doc(), [1, 1], Config(), str(tmp_path),
                                date_override="2026-11-16")
