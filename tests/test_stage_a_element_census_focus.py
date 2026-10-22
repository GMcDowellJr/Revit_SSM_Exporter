"""The census driver's ``--focus`` (tools/stage_a_element_census.py, 2026-10-22):
only the focused runs' censuses and pairs and their neighbours' are checked.

Greg's analysis of 2025-10-25 (Hosp_Interior_AR, 49 runs) spent 58.7 s of
581.7 s proving 48 other runs' censuses fresh, and the cost grows with every
run kept. A focused run R needs R's census, its predecessor P's (R's pair
reads it) and its successor S's (S's pair reads R's), and the pairs R <- P
and S <- R; nothing else reads or is read by them.

Every case asserts which runs were HASHED (census_inputs is the cost), not
only which actions were reported, beside a control.
"""
import pytest

from tools import stage_a_element_census as census

from tests.test_stage_a_element_census import _dated_run, _view
from tests.test_stage_a_element_census_incremental import _computed, _files, _model


def _series(model, dates):
    runs = []
    for n, date in enumerate(dates):
        run = _dated_run(model, date, "RUN_{0}".format(chr(65 + n)))
        _view(run, 1, eid=7 + n % 2)
        runs.append(run)
    return runs


def _hashed(monkeypatch):
    """The run names census_inputs is called for, in order."""
    seen = []
    real = census.census_inputs

    def watched(files, *a, **kw):
        seen.append(files.name)
        return real(files, *a, **kw)
    monkeypatch.setattr(census, "census_inputs", watched)
    return seen


def _acted(actions):
    return sorted((a["step"], a["run"], a["action"]) for a in actions
                  if a["action"] != "skipped")


DATES = ["2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01", "2025-05-01"]


def test_a_focused_run_checks_itself_its_predecessor_and_its_successor(tmp_path,
                                                                        monkeypatch):
    model = _model(tmp_path)
    a, b, c, d, e = _series(model, DATES)
    census.drive(model)                               # everything computed once
    seen = _hashed(monkeypatch)
    actions = census.drive(model, focus=[c])
    assert seen == [b.name, c.name, d.name]
    assert _acted(actions) == [("census", b.name, "fresh"), ("census", c.name, "fresh"),
                               ("census", d.name, "fresh"), ("pair", c.name, "fresh"),
                               ("pair", d.name, "fresh")]
    del seen[:]
    census.drive(model)                                                   # control
    assert seen == [r.name for r in (a, b, c, d, e)]


def test_a_backfilled_run_gets_its_pair_and_its_successors(tmp_path, monkeypatch):
    """B is captured after A and C were analysed: focusing B computes B's
    census, B <- A and C <- B (C's predecessor changed); A's and C's censuses
    are only checked, D's not at all."""
    model = _model(tmp_path)
    a = _series(model, ["2025-01-01"])[0]
    c, d = (_dated_run(model, date, name) for date, name in
            (("2025-03-01", "RUN_C"), ("2025-04-01", "RUN_D")))
    _view(c, 1, eid=8)
    _view(d, 1, eid=7)
    census.drive(model)
    b = _dated_run(model, "2025-02-01", "RUN_B")
    _view(b, 1, eid=8)
    seen = _hashed(monkeypatch)
    actions = census.drive(model, focus=[b])
    assert seen == [a.name, b.name, c.name]
    assert _computed(actions) == [("census", b.name), ("pair", b.name), ("pair", c.name)]
    pair = census._load_json(_files(c).pair_path("summary"))
    assert pair["predecessor"]["run_dir_name"] == b.name
    # Control: the full sweep now finds nothing to do.
    assert _computed(census.drive(model)) == []


def test_a_predecessor_never_censused_is_computed_for_the_focused_pair(tmp_path,
                                                                       monkeypatch):
    model = _model(tmp_path)
    a, b, c = _series(model, DATES[:3])
    seen = _hashed(monkeypatch)
    actions = census.drive(model, focus=[b])
    assert seen == [a.name, b.name, c.name]
    assert ("census", a.name) in _computed(actions)
    assert census._load_json(_files(b).pair_path("summary"))["state"] == "value"
    # A's own pair (no_predecessor) is nobody's neighbour here: not written.
    assert not _files(a).pair_path("summary").exists()


def test_the_focused_drive_writes_what_the_full_sweep_would(tmp_path):
    """After a focused drive on the newest run, the full sweep computes
    nothing for it or its predecessor: their files are already current."""
    model = _model(tmp_path)
    runs = _series(model, DATES)
    census.drive(model)
    new = _dated_run(model, "2025-06-01", "RUN_F")
    _view(new, 1, eid=8)
    focused = census.drive(model, focus=[new])
    assert _computed(focused) == [("census", new.name), ("pair", new.name)]
    assert [a["run"] for a in focused if a["step"] == "census"] == [runs[-1].name, new.name]
    assert _computed(census.drive(model)) == []


def test_a_focus_outside_the_root_is_refused(tmp_path):
    model = _model(tmp_path)
    _series(model, DATES[:2])
    elsewhere = _dated_run(tmp_path / "Other", "2025-01-01", "RUN_Z")
    with pytest.raises(census.CensusRefusal, match="names no run"):
        census.drive(model, focus=[elsewhere])
    assert census.main(["--root", str(model), "--focus", str(elsewhere)]) == 2
    assert census.main(["--root", str(model)]) == 0                       # control


def test_focus_needs_root(tmp_path):
    with pytest.raises(SystemExit):
        census.main([str(tmp_path), "--focus", str(tmp_path)])
