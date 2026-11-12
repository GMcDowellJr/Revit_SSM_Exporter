"""A journal left with a torn LAST line by an interrupted run must not poison
the next run that appends to the same file (Codex, PR #256).

Both run journals append to a file named by the output folder and date
(views_diagnostics_<d>.journal.jsonl, vop_view_element_map_<d>.journal.jsonl).
A crash mid-append leaves a fragment with no newline. Before this fix the next
run's first line was written straight onto that fragment, so it became an
unreadable MIDDLE line once a second line followed, and the torn-tail rule
(element_cache.read_journal_lines) refused the whole journal: the new run's
file was never compacted.

Now every append goes through element_cache.append_journal_lines, which first
looks at the file's last byte (one byte read on the ordinary path):

  * a complete JSON line missing only its newline is TERMINATED -- nothing
    lost;
  * a fragment that does not parse is TRUNCATED, and a repair line is written
    in its place, recording how many bytes were dropped. The readers count
    repair lines (``report["torn_repaired"]``) and never take one for a record.

Both journals are tested, because both reach the same helper.
"""
import json
import os

import pytest

from vop_interwoven.core import element_cache as ec
from vop_interwoven.core.element_cache import (
    ViewElementMapJournal, journal_path, read_journal)
from vop_interwoven.views_diagnostics import (
    ViewsDiagnosticsJournal, compact_views_diagnostics_file, read_views_diagnostics_journal)

TORN = '{"schema":"x","run_id":"OLD","view_id":"9","ent'


def _tear(jpath, fragment=TORN):
    with open(jpath, "a") as f:
        f.write(fragment)


# ---- views_diagnostics -----------------------------------------------------------

def _diag_old_run_crashed(p):
    old = ViewsDiagnosticsJournal("OLD")
    old.append(p, {"exporter_run_id": "OLD"}, {"1": {"view_id": 1}})
    _tear(journal_path(p))


def test_a_new_run_appends_cleanly_after_a_crashed_runs_torn_tail(tmp_path):
    """Codex's reproduction: an old run's torn tail, then two views of a new
    run in the same journal."""
    p = str(tmp_path / "views_diagnostics_d.json")
    _diag_old_run_crashed(p)
    new = ViewsDiagnosticsJournal("NEW")
    new.append(p, {"exporter_run_id": "NEW"}, {"2": {"view_id": 2}})
    new.append(p, {"exporter_run_id": "NEW"}, {"3": {"view_id": 3}})
    lines, report = read_views_diagnostics_journal(journal_path(p), "NEW")
    assert [v for v, _e, _m in lines] == ["2", "3"]
    assert report["torn_repaired"] == 1 and report["torn_tail"] == 0
    assert report["other_run"] == 1                  # the old run's whole line
    report = new.finalize()[p]
    assert report["ok"] is True and report["views"] == 2
    assert json.load(open(p)) == {"metadata": {"exporter_run_id": "NEW"},
                                  "views": {"2": {"view_id": 2}, "3": {"view_id": 3}}}


def test_the_repair_line_records_the_dropped_bytes(tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    _diag_old_run_crashed(p)
    ViewsDiagnosticsJournal("NEW").append(p, {}, {"2": {"view_id": 2}})
    raw = open(journal_path(p)).read().split("\n")
    assert TORN not in "\n".join(raw)
    repair = json.loads(raw[1])
    assert repair == {"journal_repair": {"torn_tail_bytes": len(TORN.encode("utf-8"))}}


def test_an_unterminated_but_complete_line_is_kept_not_dropped(tmp_path):
    """A crash between the record and its newline leaves a WHOLE record:
    terminated, never truncated."""
    p = str(tmp_path / "views_diagnostics_d.json")
    whole = json.dumps({"schema": "x", "run_id": "NEW", "view_id": "1",
                        "entry": {"view_id": 1}, "metadata": {"n": 1}},
                       separators=(",", ":"))
    _tear(journal_path(p), whole)
    new = ViewsDiagnosticsJournal("NEW")
    new.append(p, {"n": 2}, {"2": {"view_id": 2}})
    lines, report = read_views_diagnostics_journal(journal_path(p), "NEW")
    assert [v for v, _e, _m in lines] == ["1", "2"] and report["torn_repaired"] == 0
    ok, _r = compact_views_diagnostics_file(p, "NEW")
    assert ok is True and sorted(json.load(open(p))["views"]) == ["1", "2"]


def test_control_an_ordinary_journal_gets_no_repair_line(tmp_path):
    p = str(tmp_path / "views_diagnostics_d.json")
    j = ViewsDiagnosticsJournal("R")
    j.append(p, {}, {"1": {"view_id": 1}})
    j.append(p, {}, {"2": {"view_id": 2}})
    raw = open(journal_path(p)).read()
    assert "journal_repair" not in raw and raw.count("\n") == 2
    _lines, report = read_views_diagnostics_journal(journal_path(p), "R")
    assert report["torn_repaired"] == 0


def test_a_torn_tail_with_no_append_after_it_is_still_dropped_at_read(tmp_path):
    """The read-time rule is unchanged: a torn LAST line is dropped and
    counted; a repair only happens when something appends after it."""
    p = str(tmp_path / "views_diagnostics_d.json")
    ViewsDiagnosticsJournal("R").append(p, {}, {"1": {"view_id": 1}})
    _tear(journal_path(p))
    lines, report = read_views_diagnostics_journal(journal_path(p), "R")
    assert len(lines) == 1 and report["torn_tail"] == 1 and report["torn_repaired"] == 0


# ---- the view-element map (E2), same helper --------------------------------------

def test_the_element_map_journal_recovers_the_same_way(tmp_path):
    p = str(tmp_path / "vop_view_element_map_d.json")
    ViewElementMapJournal("OLD").append(p, {1: [(10, "HOST")]})
    _tear(journal_path(p), '{"schema":"vop.view_element_map_journal.v1","run_id":"OLD","view_id":9,"elem')
    new = ViewElementMapJournal("NEW")
    new.append(p, {2: [(20, "HOST")]})
    new.append(p, {3: [(30, "HOST")]})
    elements, report = read_journal(journal_path(p), "NEW")
    assert list(elements) == [2, 3]
    assert report["torn_repaired"] == 1 and report["other_run"] == 1
    out = new.finalize()[p]
    assert out["ok"] is True
    assert [v["view_id"] for v in json.load(open(p))["views"]] == [2, 3]


# ---- the helper itself -----------------------------------------------------------

@pytest.mark.parametrize("existing,expected_dropped", [
    ("", 0),                                   # empty file
    ('{"a":1}\n', 0),                          # ordinary
    ('{"a":1}', 0),                            # whole record, no newline
    ('{"a":1}\n{"b":', 5),                     # torn after a good line
    ('{"b":', 5),                              # torn first line
])
def test_append_journal_lines_repairs_only_a_torn_tail(tmp_path, existing, expected_dropped):
    path = str(tmp_path / "j.journal.jsonl")
    with open(path, "w") as f:
        f.write(existing)
    dropped = ec.append_journal_lines(path, ['{"c":3}'])
    assert dropped == expected_dropped
    lines = open(path).read().split("\n")
    assert lines[-1] == "" and lines[-2] == '{"c":3}'
    for line in lines[:-1]:
        json.loads(line)                       # every line is whole now
