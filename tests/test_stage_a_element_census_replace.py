"""Replace-in-place pairing in the census pair step
(tools/stage_a_element_census.py 2.1.0, ``replace_pairs``).

The rule is the one the Power BI summarizer applied before 2.1.0, reproduced
as stated: ``gone`` and ``created`` rows only; key = category + bbox extent
+ bbox centre as the census writes them, exact; no bbox value or no
category is never paired; one-to-one, min(n_gone, n_created) per key, each
side in ``sorted()`` order of element_key; source is not in the key.

File fixtures are the census's own (tests/test_stage_a_element_census.py):
views made by the real producers, registered and gridded. The rule's edges
are pinned on in-memory runs of the shape ``census_run`` and
``load_run_census`` return, through ``classify`` / ``transitions``.

Composition (CLAUDE.md, defect class 1): the pair summary's counts are
recomputed from the transitions FILE. Identities (defect class 2): the four
count identities over generated pairs, beside a control with no matching
boxes.
"""
import csv
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from tools import stage_a_element_census as census
from tools import stage_a_grid as grid

from tests.test_stage_a_element_census import _dated_run, _host_entry, _view

# BOX_A is the fixtures' default host bbox (tests/test_stage_a_element_census.BBOX).
BOX_B = {"min": [0.0, 0.0, 0.0], "max": [3.0, 0.25, 7.0]}


def _rows(path):
    with open(str(path), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _pair(run):
    f = census.RunFiles(run)
    summary = json.loads(f.pair_path("summary").read_text())
    rows = (dict((r["element_key"], r) for r in _rows(f.pair_path("csv")))
            if f.pair_path("csv").exists() else None)
    return summary, rows


def _model(tmp_path):
    folder = tmp_path / "KSRF" / "Model"
    folder.mkdir(parents=True)
    return folder


def _tie(model):
    """A (01-01): views 1-4 hold 5, 40 (Walls, BOX_A), 99 (Doors, BOX_B) and
    77 (Walls, BOX_A). B (02-01): views 1-3 hold 7, 100, 61 (Walls, BOX_A);
    view 4 holds 77 again, view 5 (new) holds 12 (Walls, BOX_A).

    So on key (Walls, BOX_A): gone host:5, host:40 and created host:7,
    host:61, host:100; host:77 persisted; host:12 is new_view. sorted()
    order is the string order: gone [host:40, host:5], created [host:100,
    host:61, host:7] -- not the numeric order, so the tie is visible."""
    a = _dated_run(model, "2025-01-01", "RUN_A")
    _view(a, 1, eid=5)
    _view(a, 2, eid=40)
    _view(a, 3, eid=99, host=_host_entry(category="Doors", bbox=BOX_B))
    _view(a, 4, eid=77)
    b = _dated_run(model, "2025-02-01", "RUN_B")
    _view(b, 1, eid=7)
    _view(b, 2, eid=100)
    _view(b, 3, eid=61)
    _view(b, 4, eid=77)
    _view(b, 5, eid=12)
    return a, b


def _from_file(rows):
    """Every count of the pair summary, recomputed from the transitions FILE
    alone: the class column for ``TRANSITIONS``, and the class with the
    ``replaced`` column for ``REPLACE_COUNTS``, split by ``source``."""
    counts = dict(("{0}{1}".format(c, s), 0) for c in census.TRANSITIONS
                  + census.REPLACE_COUNTS for s in ("", "_LINK"))
    for r in rows.values():
        sfx = "_LINK" if r["source"] == "LINK" else ""
        counts[r["class"] + sfx] += 1
        if r["class"] == "created":
            counts[("created_replaced" if r["replaced"] == "true" else "created_new") + sfx] += 1
        elif r["class"] == "gone":
            counts[("gone_replaced" if r["replaced"] == "true" else "gone_removed") + sfx] += 1
    return counts


# --- gate 1: the file composes with the summary --------------------------------------

def test_counts_recomputed_from_the_transitions_file_are_the_summarys(tmp_path):
    a, b = _tie(_model(tmp_path))
    census.drive(a.parent)
    summary, rows = _pair(b)
    assert summary["state"] == "value"
    assert _from_file(rows) == summary["counts"]
    # The fixture discriminates: a pair, a new and a removed are all present.
    assert (summary["counts"]["created_replaced"], summary["counts"]["created_new"],
            summary["counts"]["gone_replaced"], summary["counts"]["gone_removed"]) == (
                2, 1, 2, 1)
    # replaced_with names the partner on the other side, and back.
    for key, r in rows.items():
        if r["replaced"] == "true":
            other = rows[r["replaced_with"]]
            assert other["replaced_with"] == key and other["replaced"] == "true"
            assert {r["class"], other["class"]} == {"created", "gone"}
        else:
            assert r["replaced_with"] == ""


def test_the_existing_incremental_fixtures_compose_too(tmp_path):
    """The three-run fixture of the incremental tests (unmeasured, created,
    new_view, view_dropped, persisted; no pair): file == summary for both
    pairs, and the --out runs CSV carries the same replace figures."""
    from tests.test_stage_a_element_census_incremental import _three
    a, b, c = _three(_model(tmp_path))
    census.drive(a.parent)
    for run in (b, c):
        summary, rows = _pair(run)
        assert _from_file(rows) == summary["counts"]
    out = tmp_path / "out"
    census.main([str(a), str(b), str(c), "--out", str(out)])
    runs = dict((r["run_id"], r) for r in _rows(out / census.RUNS_CSV))
    for run, run_id in ((b, "RUN_B"), (c, "RUN_C")):
        counts = _pair(run)[0]["counts"]
        for col in census.REPLACE_COUNTS:
            for k in (col, col + "_LINK"):
                assert runs[run_id][k] == str(counts[k]), (run_id, k)
    assert runs["RUN_A"]["created_replaced"] == ""        # no predecessor: blank, not 0


def test_replaced_is_blank_on_every_class_but_created_and_gone(tmp_path):
    a, b = _tie(_model(tmp_path))
    census.drive(a.parent)
    _s, rows = _pair(b)
    assert rows["host:77"]["class"] == "persisted"
    assert rows["host:12"]["class"] == "new_view"
    for key in ("host:77", "host:12"):
        assert (rows[key]["replaced"], rows[key]["replaced_with"]) == ("", "")
    assert rows["host:99"]["class"] == "gone"
    assert (rows["host:99"]["replaced"], rows["host:99"]["replaced_with"]) == ("false", "")
    header = list(_rows(census.RunFiles(b).pair_path("csv"))[0].keys())
    assert header[-2:] == ["replaced", "replaced_with"]        # appended, at the end


# --- gate 5: the tie --------------------------------------------------------------------

def test_a_tie_pairs_min_of_each_side_in_sorted_order(tmp_path):
    a, b = _tie(_model(tmp_path))
    census.drive(a.parent)
    summary, rows = _pair(b)
    c = summary["counts"]
    assert (c["created_replaced"], c["gone_replaced"]) == (2, 2)
    # 1 new on the tied key; 99 (Doors) is the one removed.
    assert (c["created_new"], c["gone_removed"]) == (1, 1)
    pairs = dict((k, r["replaced_with"]) for k, r in rows.items()
                 if r["class"] == "gone" and r["replaced"] == "true")
    assert pairs == {"host:40": "host:100", "host:5": "host:61"}
    assert rows["host:7"]["replaced"] == "false"       # sorted() last, left new
    assert summary["replace_pairing"]["pairs"] == 2


# --- the rule's edges, on in-memory runs ---------------------------------------------------

def _el(source="HOST", category="Walls", views=("1",), box=(3.5, 0.5, 10.0, 2.75, 2.25, 5.0),
        state="value"):
    return {"source": source, "category": category, "views": list(views),
            "bbox_state": state, "bbox": tuple(box) if box is not None else None}


def _mem(elements, measured=("1",), unmeasured=()):
    return {"measured": list(measured), "unmeasured": dict((v, {}) for v in unmeasured),
            "elements": elements,
            "views": dict((v, {"host_keys": frozenset(
                k for k, e in elements.items() if v in e["views"] and e["source"] != "LINK")})
                for v in measured)}


def _counts(prev, cur):
    return census.transitions(prev, cur)[2]


def _pairs(prev, cur):
    return census.replace_pairs(prev, cur, census.classify(prev, cur))


def test_same_category_and_extent_elsewhere_is_not_replaced():
    box = (3.5, 0.5, 10.0, 2.75, 2.25, 5.0)
    moved = (3.5, 0.5, 10.0, 12.75, 2.25, 5.0)
    prev = _mem({"host:1": _el(box=box)})
    assert _pairs(prev, _mem({"host:2": _el(box=moved)})) == {}
    assert _pairs(prev, _mem({"host:2": _el(box=box)})) == {"host:1": "host:2"}   # control


def test_a_different_category_or_extent_is_not_replaced():
    prev = _mem({"host:1": _el()})
    assert _pairs(prev, _mem({"host:2": _el(category="Doors")})) == {}
    assert _pairs(prev, _mem({"host:2": _el(box=(3.5, 0.5, 9.0, 2.75, 2.25, 5.0))})) == {}


def test_the_match_is_exact_with_no_tolerance():
    prev = _mem({"host:1": _el(box=(3.5, 0.5, 10.0, 2.75, 2.25, 5.0))})
    cur = _mem({"host:2": _el(box=(3.5, 0.5, 10.0, 2.750001, 2.25, 5.0))})   # 1e-6 ft
    assert _pairs(prev, cur) == {}


def test_one_gone_is_never_paired_with_two_created():
    prev = _mem({"host:1": _el()})
    cur = _mem({"host:2": _el(), "host:3": _el()})
    assert _pairs(prev, cur) == {"host:1": "host:2"}
    c = _counts(prev, cur)
    assert (c["created_replaced"], c["created_new"], c["gone_replaced"]) == (1, 1, 1)


def test_two_gone_are_never_paired_with_one_created():
    prev = _mem({"host:1": _el(), "host:2": _el()})
    cur = _mem({"host:3": _el()})
    assert _pairs(prev, cur) == {"host:1": "host:3"}
    c = _counts(prev, cur)
    assert (c["gone_replaced"], c["gone_removed"], c["created_replaced"]) == (1, 1, 1)


def test_unmeasured_rows_never_take_part():
    """host:1's view is unmeasured in cur (unmeasured_out); host:9 is in a
    view unmeasured in prev (unmeasured_in). Both share host:2's / host:3's
    box, and neither may pair."""
    prev = _mem({"host:1": _el(views=("2",)), "host:3": _el(views=("1",))},
                measured=("1", "2"), unmeasured=("3",))
    cur = _mem({"host:2": _el(views=("1",)), "host:9": _el(views=("3",))},
               measured=("1", "3"), unmeasured=("2",))
    classes = census.classify(prev, cur)
    assert dict((k, r["word"]) for k, r in classes.items()) == {
        "host:1": "unmeasured_out", "host:3": "gone", "host:2": "created",
        "host:9": "unmeasured_in"}
    assert census.replace_pairs(prev, cur, classes) == {"host:3": "host:2"}


def test_new_view_and_view_dropped_rows_never_take_part():
    prev = _mem({"host:1": _el(views=("2",))}, measured=("1", "2"))
    cur = _mem({"host:2": _el(views=("3",))}, measured=("1", "3"))
    classes = census.classify(prev, cur)
    assert sorted(r["word"] for r in classes.values()) == ["new_view", "view_dropped"]
    assert census.replace_pairs(prev, cur, classes) == {}


def test_an_element_with_no_bbox_value_is_never_paired():
    for state in ("unavailable", "not_applicable", "unreadable"):
        prev = _mem({"host:1": _el(state=state, box=None)})
        cur = _mem({"host:2": _el(state=state, box=None)})
        assert _pairs(prev, cur) == {}, state
        c = _counts(prev, cur)
        assert (c["created_new"], c["gone_removed"]) == (1, 1), state
    # One side with a value, the other without: not paired either.
    assert _pairs(_mem({"host:1": _el()}), _mem({"host:2": _el(state="unavailable",
                                                                box=None)})) == {}


def test_an_element_with_no_category_is_never_paired():
    prev = _mem({"host:1": _el(category=None)})
    cur = _mem({"host:2": _el(category=None)})
    assert _pairs(prev, cur) == {}


def test_a_link_element_is_under_the_same_rule_and_source_is_not_in_the_key():
    """A LINK gone and a HOST created with one box pair (rule 6). Each is
    counted under its own source, so the per-split equality created_replaced
    == gone_replaced does not hold for such a pair; the total does, and the
    pair summary records the crossing (``cross_source_pairs``)."""
    prev = _mem({"link:11:500": _el(source="LINK")})
    cur = _mem({"host:2": _el()})
    assert _pairs(prev, cur) == {"link:11:500": "host:2"}
    c = _counts(prev, cur)
    assert (c["gone_replaced_LINK"], c["created_replaced"]) == (1, 1)
    assert (c["gone_replaced"], c["created_replaced_LINK"]) == (0, 0)
    classes = census.classify(prev, cur)
    _c, cross = census.replace_counts(classes, census.replace_pairs(prev, cur, classes))
    assert cross == {"gone_LINK": 1, "created_LINK": 0}
    # LINK with LINK: the plain per-split identity holds.
    c = _counts(_mem({"link:11:500": _el(source="LINK")}),
                _mem({"link:11:501": _el(source="LINK")}))
    assert c["created_replaced_LINK"] == c["gone_replaced_LINK"] == 1


def test_the_identity_check_refuses_counts_that_do_not_compose():
    prev = _mem({"host:1": _el()})
    counts = _counts(prev, _mem({"host:2": _el()}))
    census.check_replace_counts(counts, {"gone_LINK": 0, "created_LINK": 0})   # control
    for broken in ({"created_new": counts["created_new"] + 1},
                   {"gone_replaced": counts["gone_replaced"] + 1,
                    "gone_removed": counts["gone_removed"] - 1}):
        bad = dict(counts, **broken)
        try:
            census.check_replace_counts(bad, {"gone_LINK": 0, "created_LINK": 0})
        except AssertionError:
            continue
        raise AssertionError("accepted {0}".format(broken))


# --- gate 2: the identities over generated pairs -------------------------------------------

BOXES = [(3.5, 0.5, 10.0, 2.75, 2.25, 5.0),
         (3.5, 0.5, 10.0, 12.75, 2.25, 5.0),        # the first's extent, moved
         (1.0, 1.0, 1.0, 0.5, 0.5, 0.5)]
VIEWS = ("1", "2", "3")


@st.composite
def _pairs_of_runs(draw):
    def _side():
        status = draw(st.lists(st.sampled_from(("measured", "unmeasured", "absent")),
                               min_size=3, max_size=3))
        measured = [v for v, s in zip(VIEWS, status) if s == "measured"]
        unmeasured = [v for v, s in zip(VIEWS, status) if s == "unmeasured"]
        elements = {}
        # A run's elements come from its measured views only.
        for n in draw(st.lists(st.integers(1, 40), max_size=25 if measured else 0,
                               unique=True)):
            source = draw(st.sampled_from(("HOST", "HOST", "LINK")))
            key = "link:9:{0}".format(n) if source == "LINK" else "host:{0}".format(n)
            state = draw(st.sampled_from(("value", "value", "value", "unavailable")))
            elements[key] = _el(
                source=source, category=draw(st.sampled_from(("Walls", "Doors", None))),
                views=draw(st.lists(st.sampled_from(measured or VIEWS), min_size=1,
                                    max_size=2, unique=True)),
                state=state, box=draw(st.sampled_from(BOXES)) if state == "value" else None)
        return _mem(elements, measured=measured, unmeasured=unmeasured)
    return _side(), _side()


def _eligible(run, key):
    e = run["elements"][key]
    if e["bbox_state"] != "value" or e["category"] is None:
        return None
    return (e["category"],) + tuple(e["bbox"])


@settings(max_examples=300, deadline=None)
@given(_pairs_of_runs())
def test_the_replace_identities_hold_over_generated_pairs(runs):
    prev, cur = runs
    classes = census.classify(prev, cur)
    pairs = census.replace_pairs(prev, cur, classes)
    c = census.transitions(prev, cur)[2]
    total = dict((k, c[k] + c[k + "_LINK"]) for k in census.REPLACE_COUNTS
                 + ("created", "gone"))
    for split in (total, c, dict((k, c[k + "_LINK"]) for k in census.REPLACE_COUNTS
                                 + ("created", "gone"))):
        assert split["created_new"] + split["created_replaced"] == split["created"]
        assert split["gone_removed"] + split["gone_replaced"] == split["gone"]
    assert total["created_replaced"] == total["gone_replaced"] == len(pairs)
    cross = sum(1 for g, cr in pairs.items()
                if (classes[g]["source"] == "LINK") != (classes[cr]["source"] == "LINK"))
    if not cross:
        assert c["created_replaced"] == c["gone_replaced"]
        assert c["created_replaced_LINK"] == c["gone_replaced_LINK"]
    # One-to-one, gone -> created, on one eligible key.
    assert len(set(pairs.values())) == len(pairs)
    for g, cr in pairs.items():
        assert (classes[g]["word"], classes[cr]["word"]) == ("gone", "created")
        assert _eligible(prev, g) is not None and _eligible(prev, g) == _eligible(cur, cr)
    # min(n_gone, n_created) per key: no pair is left unmade.
    gone, created = {}, {}
    for key, r in classes.items():
        if r["word"] in ("gone", "created"):
            box = _eligible(prev if r["word"] == "gone" else cur, key)
            if box is not None:
                (gone if r["word"] == "gone" else created).setdefault(box, []).append(key)
    want = sum(min(len(g), len(created.get(box, ()))) for box, g in gone.items())
    assert len(pairs) == want


def test_control_a_pair_with_no_matching_boxes_replaces_nothing():
    prev = _mem({"host:{0}".format(n): _el(box=BOXES[0]) for n in range(1, 4)})
    cur = _mem(dict(("host:{0}".format(n), _el(box=BOXES[1])) for n in range(4, 9)))
    c = _counts(prev, cur)
    assert (c["created"], c["gone"]) == (5, 3)
    assert (c["created_replaced"], c["gone_replaced"]) == (0, 0)
    assert c["created_new"] == c["created"] and c["gone_removed"] == c["gone"]


# --- gate 4: a pair-only version change leaves the censuses alone ----------------------------

def _as_2_0_0(path):
    """Rewrite a summary as census 2.0.0 wrote it: ``tool_version`` 2.0.0
    and no step version."""
    rec = json.loads(path.read_text())
    rec.pop("census_version", None)
    rec.pop("pair_version", None)
    rec["tool_version"] = "2.0.0"
    path.write_text(json.dumps(rec, indent=2, sort_keys=True))


def test_a_pair_version_change_recomputes_the_pairs_and_no_census(tmp_path):
    model = _model(tmp_path)
    a, b = _tie(model)
    census.drive(model)
    for run in (a, b):
        f = census.RunFiles(run)
        _as_2_0_0(f.census_path("summary"))
        _as_2_0_0(f.pair_path("summary"))
    # A 2.0.0 pair: the replace columns absent, as 2.0.0 wrote it.
    f_b = census.RunFiles(b)
    before = dict((r.name, grid.sha256_file(census.RunFiles(r).census_path("summary")))
                  for r in (a, b))
    actions = census.drive(model)
    census_actions = dict((x["run"], x["action"]) for x in actions if x["step"] == "census")
    pair_actions = dict((x["run"], (x["action"], x["why"])) for x in actions
                        if x["step"] == "pair")
    assert census_actions == {a.name: "fresh", b.name: "fresh"}
    assert dict((k, v[0]) for k, v in pair_actions.items()) == {
        a.name: "computed", b.name: "computed"}
    assert "pair_version 2.0.0" in pair_actions[b.name][1]
    after = dict((r.name, grid.sha256_file(census.RunFiles(r).census_path("summary")))
                 for r in (a, b))
    assert after == before
    summary, rows = _pair(b)
    assert summary["pair_version"] == census.PAIR_VERSION
    assert "created_replaced" in summary["counts"]
    assert rows["host:40"]["replaced_with"] == "host:100"
    assert summary["census"]["cur"]["summary_sha256"] == before[b.name]
    assert summary["identity"]["census_content"]["cur"] == census.census_content(f_b)
    assert f_b.pair_path("csv").exists()


def test_control_a_census_of_another_output_version_is_recomputed(tmp_path):
    model = _model(tmp_path)
    a, _b = _tie(model)
    census.drive(model)
    path = census.RunFiles(a).census_path("summary")
    rec = json.loads(path.read_text())
    rec["census_version"] = "1.9.0"
    path.write_text(json.dumps(rec))
    actions = census.drive(model)
    # Recomputed, it writes what it wrote before (the census is a function of
    # its inputs), so its summary's sha256 and both pairs are as they were.
    assert [(x["step"], x["run"]) for x in actions if x["action"] == "computed"] == [
        ("census", a.name)]
    assert "census_version 1.9.0" in [x["why"] for x in actions if x["step"] == "census"
                                      and x["run"] == a.name][0]
    assert json.loads(path.read_text())["census_version"] == census.CENSUS_VERSION
