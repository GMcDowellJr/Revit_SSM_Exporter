"""The element census (tools/stage_a_element_census.py).

Fixtures are the grid roll-up's (tests/test_stage_a_grid_rollup.py): views
made by the REAL producers -- registered by ``register_stage_a_annotation``
and gridded by ``stage_a_grid.grid_view`` -- with a ``near_face_w_map`` added
to the model sidecar BEFORE it is registered and gridded, so the hashes both
record hold.

The shared model element (id 7) fills u 8..12, v 6..9: 12 cells hold its
pixels and 10 its ink (the interior holds fill and no ink,
tests/test_stage_a_grid.py). A census that counted cells from the ``ec_``
rows without the grid's ink rule, or summed px, would disagree with the one
read through the roll-up's ``_key_sums``; that is what the first tests pin.

Assertions read the files the tool WROTE (CLAUDE.md, defect class 4).
"""
import csv
import json

from tools import register_stage_a_annotation as reg
from tools import stage_a_element_census as census
from tools import stage_a_grid as grid
from tools import stage_a_grid_rollup as rollup
from tools import stage_a_kinds as kinds
from tools import stage_a_sidecar_shapes as shapes

from tests.test_register_stage_a_annotation import MODEL_ELEMENT
from tests.test_stage_a_grid_rollup import _capture, _outcome, _run

EID = MODEL_ELEMENT[0]
BBOX = {"min": [1.0, 2.0, 0.0], "max": [4.5, 2.5, 10.0]}


def _host_entry(category="Walls", source="HOST", bbox=BBOX):
    return {"uv_rect": [8.0, 6.0, 12.0, 9.0], "near_face_w": 1.0, "category": category,
            "source": source, "bbox_3d": bbox}


def _link_entry(inst, elem, category="Walls"):
    return {"uv_rect": [20.0, 15.0, 21.0, 16.0], "near_face_w": 2.0, "category": category,
            "bbox_3d": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "link_inst_id": inst, "link_elem_id": elem}


def _view(run, n, eid=EID, host=None, link=None, date=None, status="success",
          register=True):
    """View ``n`` in ``run`` with model element ``eid`` (the shared element,
    re-numbered), its near_face_w_map, registered and gridded after the
    edit (both record the model sidecar's hash)."""
    model_path, anno_path = _capture(run, n, register=False, gridded=False)
    side = json.loads(model_path.read_text())
    if eid != EID:
        side["color_assignment_map"] = {str(eid): side["color_assignment_map"][str(EID)]}
    side["near_face_w_map"] = {"host": {str(eid): host if host is not None else _host_entry()},
                               "link": link or {}}
    model_path.write_text(json.dumps(side))
    if register:
        reg.register(anno_path)
    grid.grid_view(model_path, run / "analysis_grid")
    _outcome(run, n, status=status, reason="" if status == "success" else "annotation_failed",
             **({"date": date} if date else {}))
    return model_path


def _dated_run(tmp_path, date, run_id):
    run = _run(tmp_path, name="{0}__{1}".format(date, run_id), run_id=run_id)
    meta = json.loads((run / "run_meta.json").read_text())
    meta["date"] = date
    (run / "run_meta.json").write_text(json.dumps(meta))
    return run


def _rows(path):
    with open(str(path), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _out(out):
    summary = json.loads((out / census.SUMMARY_NAME).read_text())
    runs = dict((r["run_id"], r) for r in _rows(out / census.RUNS_CSV))
    series = dict((r["element_key"], r) for r in _rows(out / census.SERIES_CSV))
    return summary, runs, series


def _elements(out, date, run_id):
    return dict((r["element_key"], r) for r in _rows(
        out / "{0}__{1}{2}".format(date, run_id, census.ELEMENTS_SUFFIX)))


# --- 1. cells per element compose with the roll-up's reader -----------------------

def test_an_elements_px_is_the_rollups_own_key_sum(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    out = tmp_path / "out"
    assert census.main([str(run), "--out", str(out)]) == 0
    e = _elements(out, "2025-01-01", "RUN_A")["host:{0}".format(EID)]
    # The roll-up's per-key reader on the same grid file, not a copy of it.
    keys = rollup.row_keys(run / "analysis_grid" / "V_1.grid.json")
    model = [k for k in keys if k[0] == "model"]
    assert len(model) == 1
    assert (int(e["px"]), int(e["ink_px"])) == (model[0][3], model[0][4]) != (0, 0)


def test_cells_are_the_grids_ink_rule_where_a_row_count_disagrees(tmp_path):
    """12 cells hold the element's pixels, 10 its ink: a reimplementation
    counting ec_ rows would report 12, one summing px would report px."""
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    e = _elements(out, "2025-01-01", "RUN_A")["host:{0}".format(EID)]
    assert (int(e["cells_px"]), int(e["cells_ink"])) == (12, 10)
    rec, arr = kinds.load_grid(run / "analysis_grid" / "V_1.grid.json")
    n = [i for i, k in enumerate(rec["ec_keys"]) if k.get("element_id") == EID][0]
    assert int((arr["ec_key"] == n).sum()) == 12            # the row count disagrees
    assert int(e["cells_ink"]) != int(e["px"])
    # ... and the element's ink cells are the grid's own model occupancy here.
    occ = rec["occupancy"]["all_cells"]
    assert int(e["cells_ink"]) == occ["model_only"] + occ["overlap"]


def test_the_census_reads_through_kinds_key_sums(tmp_path, monkeypatch):
    """Mutate production's reader: the census must move with it."""
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    real = kinds._key_sums
    monkeypatch.setattr(kinds, "_key_sums", lambda a, n, f: real(a, n, f) * 3)
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    e = _elements(out, "2025-01-01", "RUN_A")["host:{0}".format(EID)]
    assert (int(e["cells_px"]), int(e["cells_ink"])) == (36, 30)


# --- 2. identity, not views -------------------------------------------------------

def test_one_element_in_two_views_is_one_element_drawn_twice(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    _view(run, 2)
    out = tmp_path / "out"
    assert census.main([str(run), "--out", str(out)]) == 0
    e = _elements(out, "2025-01-01", "RUN_A")["host:{0}".format(EID)]
    assert (e["source"], e["category"], e["views_collected"], e["views_with_px"]) == (
        "HOST", "Walls", "2", "2")
    assert (e["cells_ink"], e["first_cells"], e["repeat_cells"]) == ("20", "10", "10")
    assert e["first_view_id"] == "1"                     # tie: the lowest view id
    assert (float(e["bbox_dx_ft"]), float(e["bbox_dy_ft"]), float(e["bbox_dz_ft"])) == (
        3.5, 0.5, 10.0)
    _s, runs, _series = _out(out)
    r = runs["RUN_A"]
    assert (r["elements_distinct"], r["elements_HOST"], r["views_measured"]) == ("1", "1", "2")
    assert r["model_cells_occupancy"] == "20"           # a per-view sum doubles ...
    assert float(r["cells_per_element"]) == 20.0
    assert float(r["first_share"]) == 0.5               # ... the first appearance does not


def test_a_link_element_is_keyed_by_its_link_ids_not_its_category(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1, link={"11:500": _link_entry(11, 500), "11:501": _link_entry(11, 501),
                        "12:500": _link_entry(12, 500)})
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    els = _elements(out, "2025-01-01", "RUN_A")
    links = sorted(k for k in els if k.startswith("link:"))
    assert links == ["link:11:500", "link:11:501", "link:12:500"]   # one category, three
    e = els["link:11:501"]
    assert (e["source"], e["category"], e["cells_state"]) == (
        "LINK", "Walls", "link_category_only")
    assert (e["px"], e["cells_ink"], e["first_cells"]) == ("", "", ""), "not measured"
    assert (e["link_inst_id"], e["link_elem_id"]) == ("11", "501")
    _s, runs, _series = _out(out)
    assert (runs["RUN_A"]["elements_LINK"], runs["RUN_A"]["elements_HOST"]) == ("3", "1")


def test_a_dwg_source_is_read_through_the_shared_reader_in_either_shape(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1, host=_host_entry(source={"state": "value", "value": "DWG"}))  # pre-C2
    _view(run, 2, eid=8, host=_host_entry(source={"state": "unavailable", "reason": "x"}))
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    els = _elements(out, "2025-01-01", "RUN_A")
    assert els["host:{0}".format(EID)]["source"] == "DWG"
    assert els["host:8"]["source"] == "unrecorded"


def test_the_shapes_reader_composes_with_the_writers_reencoding():
    """Old-shape entry and its C2/C4 re-encoding read the same."""
    from tools.reencode_stage_a_sidecar import reencode_entry
    old = {"category": "Walls", "category_state": {"state": "value", "value": "Walls"},
           "source": {"state": "value", "value": "DWG"}}
    failed = {"category": None, "category_state": {"state": "unavailable", "reason": "r"},
              "source": {"state": "unavailable", "reason": "s"}}
    for entry in (old, failed):
        new = reencode_entry(entry)
        assert shapes.model_entry_source(new) == shapes.model_entry_source(entry)
        assert shapes.model_entry_category(new) == shapes.model_entry_category(entry)
    assert shapes.model_entry_source(reencode_entry(old)) == ("value", "DWG")
    assert shapes.model_entry_category(failed) == ("unavailable", "r")
    assert shapes.model_entry_source({})[0] == "unavailable"


# --- 3. unmeasured is never zero, never a deletion --------------------------------

def test_a_failed_view_contributes_no_element_and_is_counted_unmeasured(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    _view(run, 2, eid=8, status="annotation_failed")
    out = tmp_path / "out"
    assert census.main([str(run), "--out", str(out)]) == 1
    els = _elements(out, "2025-01-01", "RUN_A")
    assert "host:8" not in els and "host:{0}".format(EID) in els        # control
    summary, runs, _series = _out(out)
    r = runs["RUN_A"]
    assert (r["views_requested"], r["views_measured"], r["views_unmeasured"],
            r["views_capture_failed"]) == ("2", "1", "1", "1")
    unmeasured = summary["per_run"]["RUN_A"]["unmeasured_views"]
    assert [(u["view_id"], u["row_status"]) for u in unmeasured] == [("2", "not_this_run")]


def test_a_grid_made_before_ec_keys_is_a_named_refusal_not_a_zero(tmp_path):
    old = _dated_run(tmp_path, "2025-01-01", "RUN_OLD")
    _view(old, 1)
    path = old / "analysis_grid" / "V_1.grid.json"
    rec = json.loads(path.read_text())
    rec.pop("ec_keys")
    path.write_text(json.dumps(rec))
    new = _dated_run(tmp_path, "2025-02-01", "RUN_NEW")
    _view(new, 1)
    out = tmp_path / "out"
    assert census.main([str(old), str(new), "--out", str(out)]) == 1
    summary, runs, _series = _out(out)
    assert runs["RUN_OLD"]["refused"].startswith("grid_predates_ec_keys")
    assert runs["RUN_OLD"]["elements_distinct"] == ""                   # not 0
    assert runs["RUN_NEW"]["refused"] == "" and runs["RUN_NEW"]["elements_distinct"] == "1"
    assert summary["refused_runs"]["count"] == 1
    assert census.main([str(old), "--out", str(tmp_path / "o2")]) == 2   # all refused


# --- 4. the series ------------------------------------------------------------------

def _series_runs(tmp_path, b_view2_status):
    """A: views 1, 2 hold element 7. B: view 1 holds element 8 instead; view 2
    holds 8 too, its capture ``b_view2_status``; view 3 is new and holds 9."""
    a = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(a, 1)
    _view(a, 2)
    b = _dated_run(tmp_path, "2025-02-01", "RUN_B")
    _view(b, 1, eid=8)
    _view(b, 2, eid=8, status=b_view2_status)
    _view(b, 3, eid=9)
    out = tmp_path / "out"
    census.main([str(b), str(a), "--out", str(out)])     # order comes from the dates
    return _out(out)


def test_an_element_whose_views_were_all_measured_again_is_gone(tmp_path):
    _summary, runs, series = _series_runs(tmp_path, "success")
    s7, s8, s9 = (series["host:{0}".format(e)] for e in (EID, 8, 9))
    assert (s7["first_how"], s7["last_how"], s7["last_run_id"]) == (
        "series_start", "gone", "RUN_A")
    assert (s8["first_how"], s8["first_run_id"], s8["last_how"]) == (
        "created", "RUN_B", "series_end")
    assert s9["first_how"] == "new_view"                 # the view is new, not the element
    rb = runs["RUN_B"]
    assert (rb["gone"], rb["created"], rb["new_view"], rb["persisted"]) == ("1", "1", "1", "0")
    assert rb["prev_as_of_date"] == "2025-01-01"
    assert (rb["common_measured_views"], float(rb["host_id_jaccard_common_views"])) == (
        "2", 0.0)


def test_an_element_whose_view_failed_is_unmeasured_not_gone(tmp_path):
    _summary, runs, series = _series_runs(tmp_path, "annotation_failed")
    s7 = series["host:{0}".format(EID)]
    assert s7["last_how"] == "unmeasured"                # view 2 failed in B
    rb = runs["RUN_B"]
    assert (rb["gone"], rb["unmeasured_out"]) == ("0", "1")
    assert rb["created"] == "1"                          # 8 in view 1, measured in A


def test_unchanged_ids_overlap_fully_and_persist(tmp_path):
    a = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(a, 1)
    b = _dated_run(tmp_path, "2025-02-01", "RUN_B")
    _view(b, 1)
    out = tmp_path / "out"
    assert census.main([str(a), str(b), "--out", str(out)]) == 0
    _s, runs, series = _out(out)
    assert float(runs["RUN_B"]["host_id_jaccard_common_views"]) == 1.0
    assert runs["RUN_B"]["persisted"] == "1"
    assert series["host:{0}".format(EID)]["runs_seen"] == "2"


def test_two_runs_with_one_as_of_date_are_refused(tmp_path):
    a = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(a, 1)
    b = _run(tmp_path, name="other", run_id="RUN_B")
    _view(b, 1, date="2025-01-01")
    out = tmp_path / "out"
    assert census.main([str(a), str(b), "--out", str(out)]) == 2
    assert not (out / census.SUMMARY_NAME).exists()


# --- 5. the files ---------------------------------------------------------------------

def test_the_summary_is_written_last_and_names_every_csv(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    summary = json.loads((out / census.SUMMARY_NAME).read_text())
    assert set(summary["csv_sha256"]) == {"2025-01-01__RUN_A.elements.csv",
                                          census.RUNS_CSV, census.SERIES_CSV}
    for name, sha in summary["csv_sha256"].items():
        assert grid.sha256_file(out / name) == sha
    assert summary["tool_version"] == census.TOOL_VERSION
    header = next(csv.reader(open(str(out / census.RUNS_CSV), encoding="utf-8")))
    assert header == census.RUN_COLUMNS
    assert census.RUN_COLUMNS[:4] == ["as_of_date", "run_id", "run_dir", "refused"]


# --- 6. 1.1.0: progress, paths, memory, annotation total ---------------------------

def test_a_run_that_is_not_a_directory_is_a_bad_argument_before_anything_is_read(tmp_path):
    import pytest
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as ex:
        census.main([str(run), str(tmp_path / "<a run folder>"), "--out", str(out)])
    assert ex.value.code == 2
    assert not out.exists()                              # nothing written, not a refusal row


def test_runs_are_recorded_by_absolute_path_and_reported_as_read(tmp_path, monkeypatch,
                                                                 capsys):
    a = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(a, 1)
    b = _dated_run(tmp_path, "2025-02-01", "RUN_B")
    _view(b, 1)
    monkeypatch.chdir(tmp_path)
    assert census.main([a.name, b.name, "--out", "out"]) == 0
    _s, runs, _series = _out(tmp_path / "out")
    assert runs["RUN_A"]["run_dir"] == str(a.resolve())
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[")]
    assert [ln.split(" ")[0] for ln in lines] == ["[1/2]", "[2/2]"]
    assert "RUN_A" in lines[0] and "1 measured / 1 requested" in lines[0]


def test_a_runs_view_records_drop_their_elements_once_folded(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    _view(run, 2, eid=8)
    r = census.census_run(run)
    assert sorted(r["elements"]) == ["host:7", "host:8"]
    for vid, view in r["views"].items():
        assert "elements" not in view, vid
    assert r["views"]["2"]["host_keys"] == frozenset(["host:8"])


def test_a_model_only_view_does_not_blank_the_runs_annotation_cells(tmp_path):
    run = _dated_run(tmp_path, "2025-01-01", "RUN_A")
    _view(run, 1)
    _view(run, 2, register=False)                        # gridded, no annotation layer
    out = tmp_path / "out"
    census.main([str(run), "--out", str(out)])
    _s, runs, _series = _out(out)
    g = json.loads((run / "analysis_grid" / "V_1.grid.json").read_text())
    occ = g["occupancy"]["all_cells"]
    assert runs["RUN_A"]["views_measured"] == "2"
    assert runs["RUN_A"]["anno_cells_views"] == "1"
    assert int(runs["RUN_A"]["anno_cells_occupancy"]) == occ["anno_only"] + occ["overlap"] > 0
