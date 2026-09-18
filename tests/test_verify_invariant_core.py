"""Tests for tools/verify_invariant_core.py.

WHAT THESE BIND, AND WHAT THEY DELIBERATELY DO NOT
--------------------------------------------------
Nothing here reimplements the tool's arithmetic. Every assertion is made
against the bundle the production entry point produced, so a test cannot pass
by running its own copy of the logic.

The fixtures are built from the PRODUCTION header functions in
``vop_interwoven.csv_export``, not from a hand-written column list. That
coupling is deliberate: when the classification surface is deleted, these
fixtures change with the emitters, and any invariant that quietly depended on
a deleted column stops being satisfiable instead of silently reading empty.

THE CONTROL MATTERS AS MUCH AS THE SCENARIOS. ``test_control_*`` assert that
the unmutated fixture is clean and does NOT refuse. Without them, every
refusal test below would also pass against a tool that refused everything,
and every violation test against a tool that reported every invariant
violated.

Two tests exist specifically to exercise a CALL SITE rather than a formula,
because extracting a formula binds the formula and not the arguments
production passes to it. Both are built so the two candidate arguments give
visibly different answers -- see their own docstrings.
"""
import csv
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import verify_invariant_core as V  # noqa: E402

from vop_interwoven.csv_export import (  # noqa: E402
    get_core_csv_header,
    get_occlusion_csv_header,
    get_perf_csv_header,
    get_vop_csv_header,
)

RUN_ID = "20260918T090000"
DATE = "2026-09-18"
TOOL_PATH = os.path.join(TOOLS_DIR, "verify_invariant_core.py")


def _view_rows(index, view_type):
    """One view's four rows. Adaptive/capped views are deliberately off-sheet."""
    view_id = 500000 + index * 137
    adaptive = (index % 3 == 0)
    common = {
        "Date": DATE, "RunId": RUN_ID, "ViewId": view_id,
        "ViewUniqueId": "uid-%d" % view_id, "ViewName": "View %d" % index,
        "ViewType": view_type,
    }
    core = dict(common)
    core.update({
        "SheetNumber": "" if adaptive else "A-10%d" % index,
        "IsOnSheet": "False" if adaptive else "True",
        "Scale": 96, "Discipline": "Architectural", "Phase": "New",
        "ViewTemplate_Name": "", "IsTemplate": "False",
        "ExporterVersion": "v1", "ConfigHash": "cfg-abc",
        "ViewFrameHash": "hash%04d" % index, "FromCache": "N",
        "ElapsedSec": 1.5, "CellSize_ft": 0.5,
        "CellSizeRequested_ft": 0.5,
        "CellSizeEffective_ft": 0.75 if adaptive else 0.5,
        "ResolutionMode": "adaptive" if adaptive else "canonical",
        "CapTriggered": "True" if adaptive else "False",
        "AnnoExpanded": "False",
    })
    vop = dict(common)
    vop.update({
        "TotalCells": 500, "Empty": 380,
        "ModelOnly": 100, "AnnoOnly": 5, "Overlap": 10,
        "Ext_Cells_Any": 0, "Ext_Cells_Only": 0,
        "Ext_Cells_DWG": 0, "Ext_Cells_RVT": 0,
        # TEXT is present in every view; REGION only in the first five. That
        # asymmetry is what the L2 call-site test discriminates on.
        "AnnoCells_TEXT": 40 + index,
        "AnnoCells_TAG": 30 + index,
        "AnnoCells_DIM": 20 + index,
        "AnnoCells_DETAIL": 10 + index,
        "AnnoCells_LINES": 5 + index,
        "AnnoCells_REGION": 3 + index if index < 5 else 0,
        "AnnoCells_OTHER": 0,
        "CellSize_ft": 0.5, "CellSizeRequested_ft": 0.5,
        "CellSizeEffective_ft": 0.75 if adaptive else 0.5,
        "ResolutionMode": "adaptive" if adaptive else "canonical",
        "CapTriggered": "True" if adaptive else "False",
        "RowSource": "vop_interwoven", "ExporterVersion": "v1",
        "ConfigHash": "cfg-abc", "FromCache": "N", "ElapsedSec": 1.5,
    })
    perf = dict(common)
    perf.update({
        "Success": "True", "TotalMs": 1500,
        "Width": 50 + index, "Height": 80 + index * 2,
        "TotalElements": 500,
        # 120, while ModelOnly+Overlap+AnnoOnly is 115. They MUST differ:
        # equal totals would let an I7 that summed the wrong columns pass.
        "FilledCells": 120,
    })
    occlusion = dict(common)
    occlusion.update({"coverage_pct": 55.0})
    return core, vop, perf, occlusion


def write_bundle(directory, n_views=12, view_types=None):
    os.makedirs(str(directory), exist_ok=True)
    buckets = {"views_core": [], "views_vop": [],
               "views_perf": [], "views_occlusion": []}
    for index in range(n_views):
        if view_types is None:
            view_type = "FloorPlan"
        else:
            view_type = view_types[index % len(view_types)]
        core, vop, perf, occlusion = _view_rows(index, view_type)
        buckets["views_core"].append(core)
        buckets["views_vop"].append(vop)
        buckets["views_perf"].append(perf)
        buckets["views_occlusion"].append(occlusion)
    headers = {
        "views_core": get_core_csv_header(),
        "views_vop": get_vop_csv_header(),
        "views_perf": get_perf_csv_header(),
        "views_occlusion": get_occlusion_csv_header(),
    }
    for role, rows in buckets.items():
        path = os.path.join(str(directory), "%s_%s.csv" % (role, DATE))
        with open(path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers[role],
                                    extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    return str(directory)


def role_path(directory, role):
    matches = [f for f in os.listdir(directory) if f.startswith(role + "_")]
    assert len(matches) == 1, matches
    return os.path.join(directory, matches[0])


def read_role(directory, role):
    with open(role_path(directory, role), newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames), list(reader)


def write_role(directory, role, fields, rows):
    # Written through a closed handle. An unflushed writer makes the file look
    # empty to a subprocess, and the tool then refuses for "no header row" --
    # which would let a refusal test pass while proving nothing about the
    # refusal it names.
    with open(role_path(directory, role), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def drop_column(directory, role, column):
    """Remove a column from the header AND from every row.

    A dedicated helper so ``write_role`` can stay strict: an extrasaction of
    "ignore" there would silently swallow a row key the test did not mean to
    leave behind, which is how a fixture stops describing what it claims to.
    """
    fields, rows = read_role(directory, role)
    assert column in fields, column
    for row in rows:
        row.pop(column, None)
    write_role(directory, role, [f for f in fields if f != column], rows)


def mutate(directory, role, mutator):
    fields, rows = read_role(directory, role)
    mutator(rows)
    write_role(directory, role, fields, rows)


def run_verify(directory, path="geometry", cache_mode="disabled", **kwargs):
    return V.verify(str(directory), path, cache_mode, **kwargs)


def violation_ids(bundle):
    return [entry["violation_id"]
            for entry in bundle["L4_violation_register"]["observed"]]


@pytest.fixture
def bundle_dir(tmp_path):
    return write_bundle(tmp_path / "bundle")


# ---------------------------------------------------------------------------
# Controls. Without these the scenarios below prove nothing.
# ---------------------------------------------------------------------------

def test_control_unmutated_bundle_reports_no_violations(bundle_dir):
    bundle = run_verify(bundle_dir)
    assert violation_ids(bundle) == []
    assert bundle["L4_violation_register"]["new_violation_count"] == 0


def test_control_unmutated_bundle_does_not_refuse(bundle_dir):
    run_verify(bundle_dir)  # a Refusal here fails the test by propagating


def test_control_asserting_invariants_hold_on_clean_data(bundle_dir):
    invariants = run_verify(bundle_dir)["invariants"]
    for key in ("I1", "I2", "I3", "I4", "I8"):
        assert invariants[key]["status"] == V.STATUS_HOLDS, key


def test_control_report_only_invariants_never_claim_to_hold(bundle_dir):
    invariants = run_verify(bundle_dir)["invariants"]
    for key in ("I5", "I6", "I7"):
        assert invariants[key]["status"] == V.STATUS_REPORT_ONLY, key


# ---------------------------------------------------------------------------
# I1
# ---------------------------------------------------------------------------

def test_i1_multiple_run_ids_violates(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[3].__setitem__("RunId", "20260918T999999"))
    bundle = run_verify(bundle_dir)
    assert "I1.MULTIPLE_RUN_IDS" in violation_ids(bundle)
    assert bundle["invariants"]["I1"]["status"] == V.STATUS_VIOLATED
    assert bundle["L0_manifest"]["run_id"] is None


# ---------------------------------------------------------------------------
# I2
# ---------------------------------------------------------------------------

def test_i2_duplicate_view_row_violates(bundle_dir):
    mutate(bundle_dir, "views_vop", lambda rows: rows.append(dict(rows[2])))
    bundle = run_verify(bundle_dir)
    assert "I2.DUPLICATE_VIEW_ROW" in violation_ids(bundle)


def test_i2_from_cache_rows_are_dropped_and_counted(bundle_dir):
    def flag(rows):
        for index in (1, 4, 7):
            rows[index]["FromCache"] = "Y"
    mutate(bundle_dir, "views_core", flag)
    record = run_verify(bundle_dir)["invariants"]["I2"]["per_role"]["views_core"]
    assert record["rows_dropped_from_cache"] == 3
    assert record["rows_retained"] == record["rows_read"] - 3


def test_i2_declares_no_expected_cache_count(bundle_dir):
    """The check reports on the cache; it must not encode its behaviour."""
    i2 = run_verify(bundle_dir, cache_mode="enabled")["invariants"]["I2"]
    assert i2["declared_cache_mode"] == "enabled"
    assert "NONE DECLARED" in i2["cache_expectation"]


def test_i2_unpopulated_from_cache_is_retained_not_dropped(bundle_dir):
    """An unwritten flag must not silently shrink the population."""
    mutate(bundle_dir, "views_core",
           lambda rows: rows[0].__setitem__("FromCache", ""))
    record = run_verify(bundle_dir)["invariants"]["I2"]["per_role"]["views_core"]
    assert record["rows_dropped_from_cache"] == 0
    assert record["rows_retained"] == record["rows_read"]


def test_i2_role_without_from_cache_column_reports_unknown_not_zero(bundle_dir):
    record = run_verify(bundle_dir)["invariants"]["I2"]["per_role"]["views_perf"]
    assert record["from_cache_column_present"] is False
    assert record["rows_dropped_from_cache"] is None
    assert "not zero" in record["from_cache_note"]


# ---------------------------------------------------------------------------
# I3
# ---------------------------------------------------------------------------

def test_i3_capped_view_on_sheet_violates(bundle_dir):
    def put_capped_view_on_a_sheet(rows):
        for row in rows:
            if row["CapTriggered"] == "True":
                row["IsOnSheet"] = "True"
                return
        raise AssertionError("fixture carries no capped row to mutate")
    mutate(bundle_dir, "views_core", put_capped_view_on_a_sheet)
    bundle = run_verify(bundle_dir)
    assert "I3.CAPPED_VIEW_ON_SHEET" in violation_ids(bundle)


def test_i3_not_exercised_when_no_row_is_on_a_sheet(bundle_dir):
    """A uniform IsOnSheet cannot distinguish 'not sheeted' from 'never set'."""
    def clear(rows):
        for row in rows:
            row["IsOnSheet"] = "False"
    mutate(bundle_dir, "views_core", clear)
    record = run_verify(bundle_dir)["invariants"]["I3"]["per_cap"]["CELL_SIZE_CAP"]
    assert record["status"] == V.STATUS_NOT_EXERCISED
    assert record["antecedent_rows"] > 0, (
        "the antecedent must be non-empty, or this passes for the wrong reason")


def test_i3_not_exercised_self_retires_on_the_first_true(bundle_dir):
    def clear_all_but_one(rows):
        for row in rows:
            row["IsOnSheet"] = "False"
        for row in rows:
            if row["CapTriggered"] != "True":
                row["IsOnSheet"] = "True"
                return
    mutate(bundle_dir, "views_core", clear_all_but_one)
    record = run_verify(bundle_dir)["invariants"]["I3"]["per_cap"]["CELL_SIZE_CAP"]
    assert record["status"] == V.STATUS_HOLDS


def test_i3_does_not_truthy_coerce_the_string_false(bundle_dir):
    """bool("False") is True. A sheet flag read that way inverts I3 silently."""
    def clear(rows):
        for row in rows:
            row["IsOnSheet"] = "False"
    mutate(bundle_dir, "views_core", clear)
    bundle = run_verify(bundle_dir)
    record = bundle["invariants"]["I3"]["per_cap"]["CELL_SIZE_CAP"]
    assert record["is_on_sheet_true_count"] == 0
    assert record["is_on_sheet_false_count"] == record["is_on_sheet_false_count"]
    assert "I3.CAPPED_VIEW_ON_SHEET" not in violation_ids(bundle)


def test_i3_adaptive_resolution_mode_alone_is_an_antecedent(bundle_dir):
    """Capped OR adaptive. Dropping either half narrows the invariant."""
    def adaptive_but_uncapped_and_sheeted(rows):
        rows[1]["CapTriggered"] = "False"
        rows[1]["ResolutionMode"] = "adaptive"
        rows[1]["IsOnSheet"] = "True"
    mutate(bundle_dir, "views_core", adaptive_but_uncapped_and_sheeted)
    assert "I3.CAPPED_VIEW_ON_SHEET" in violation_ids(run_verify(bundle_dir))


def test_i3_names_both_caps_and_never_conflates_them(bundle_dir):
    per_cap = run_verify(bundle_dir)["invariants"]["I3"]["per_cap"]
    assert per_cap["CELL_SIZE_CAP"]["cap_field"] == "CapTriggered"
    assert per_cap["EXPORT_PIXEL_CAP"]["cap_field"] == "cap_applied"
    assert per_cap["EXPORT_PIXEL_CAP"]["status"] == V.STATUS_NOT_EVALUABLE


def test_i3_never_asserts_the_converse(bundle_dir):
    record = run_verify(bundle_dir)["invariants"]["I3"]["per_cap"]["CELL_SIZE_CAP"]
    assert "NOT ASSERTED AND NOT INFERRED" in record["converse"]


# ---------------------------------------------------------------------------
# I4
# ---------------------------------------------------------------------------

def test_i4_requested_without_effective_violates(bundle_dir):
    drop_column(bundle_dir, "views_core", "CellSizeEffective_ft")
    assert "I4.REQUESTED_WITHOUT_EFFECTIVE" in violation_ids(run_verify(bundle_dir))


def test_i4_detects_the_class_not_the_known_incidents(bundle_dir):
    """A NEW Requested column with no partner must fire with no edit to I4."""
    fields, rows = read_role(bundle_dir, "views_core")
    for row in rows:
        row["ExportDpiRequested_x"] = 300
    write_role(bundle_dir, "views_core", fields + ["ExportDpiRequested_x"], rows)
    bundle = run_verify(bundle_dir)
    assert "I4.REQUESTED_WITHOUT_EFFECTIVE" in violation_ids(bundle)
    unpaired = bundle["invariants"]["I4"]["per_role"]["views_core"][
        "unpaired_requested_columns"]
    assert unpaired[0]["expected_effective"] == "ExportDpiEffective_x"


def test_i4_does_not_assert_requested_equals_effective(bundle_dir):
    """Requested != effective on the adaptive views is the MEASUREMENT."""
    bundle = run_verify(bundle_dir)
    assert bundle["invariants"]["I4"]["status"] == V.STATUS_HOLDS
    measured = bundle["invariants"]["I4"]["per_role"]["views_core"]["measured"][0]
    assert measured["ratio_summary"]["max"] == 1.5
    assert violation_ids(bundle) == []


def test_i4_emits_a_ratio_for_every_requested_value(bundle_dir):
    row = run_verify(bundle_dir)["L1_per_view"][0]
    assert row["cell_size_requested_ft"] is not None
    assert row["cell_size_effective_ft"] is not None
    assert row["cell_size_ratio_effective_over_requested"] is not None


# ---------------------------------------------------------------------------
# I5 -- n_min governs order statistics ONLY
# ---------------------------------------------------------------------------

def test_i5_order_statistics_suppressed_below_the_floor():
    result = V.summarize(list(range(V.N_MIN_ORDER_STATISTIC - 1)), "x")
    assert "order_statistics" not in result
    assert "suppressed" in result
    assert result["n"] == V.N_MIN_ORDER_STATISTIC - 1


def test_i5_order_statistics_present_at_the_floor():
    result = V.summarize(list(range(V.N_MIN_ORDER_STATISTIC)), "x")
    assert "order_statistics" in result
    assert "suppressed" not in result


def test_i5_counts_and_range_survive_below_the_floor():
    """A count is not an order statistic; suppressing it hides the sparseness."""
    result = V.summarize([7.0], "x")
    assert result["n"] == 1
    assert result["min"] == 7.0 and result["max"] == 7.0
    assert "suppressed" in result


def test_i5_suppression_is_visible_not_absent():
    result = V.summarize([1.0, 2.0], "x")
    assert "n=2" in result["suppressed"]["reason"]
    assert str(V.N_MIN_ORDER_STATISTIC) in result["suppressed"]["reason"]


def test_i5_median_binds_a_known_value():
    result = V.summarize([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "x")
    assert result["order_statistics"]["median"] == 5.5


def test_i5_is_declared_not_to_be_a_tolerance(bundle_dir):
    assert run_verify(bundle_dir)["invariants"]["I5"]["is_a_tolerance"] is False


# ---------------------------------------------------------------------------
# I6
# ---------------------------------------------------------------------------

def test_i6_checksums_every_input_file(bundle_dir):
    per_role = run_verify(bundle_dir)["invariants"]["I6"]["per_role"]
    for role in ("views_core", "views_vop", "views_perf", "views_occlusion"):
        assert len(per_role[role]["sha256"]) == 64
        assert per_role[role]["bytes"] > 0
        assert per_role[role]["rows_read"] == 12


def test_i6_checksum_tracks_the_file_contents(bundle_dir, tmp_path):
    before = run_verify(bundle_dir)["invariants"]["I6"]["per_role"]["views_core"]
    mutate(bundle_dir, "views_core",
           lambda rows: rows[0].__setitem__("ViewName", "renamed"))
    after = run_verify(bundle_dir)["invariants"]["I6"]["per_role"]["views_core"]
    assert before["sha256"] != after["sha256"]


# ---------------------------------------------------------------------------
# I7 -- report only, and the call site that decides WHICH columns are summed
# ---------------------------------------------------------------------------

def test_i7_reports_both_totals_and_the_ratio_without_asserting(bundle_dir):
    bundle = run_verify(bundle_dir)
    i7 = bundle["invariants"]["I7"]
    assert i7["status"] == V.STATUS_REPORT_ONLY
    assert i7["views_where_totals_are_equal"] == 0
    assert violation_ids(bundle) == [], "I7 must never emit a violation"


def test_i7_call_site_sums_the_declared_columns():
    """Exercises the CALL SITE, not a formula.

    The fixture is built so the candidate sums are all DIFFERENT:
    ModelOnly+Overlap+AnnoOnly = 115, FilledCells = 120, TotalCells-Empty = 120,
    ModelOnly+Overlap = 110, TotalCells = 500. Only the declared triple yields
    115, so a call site that summed a different set of columns changes this
    number instead of passing.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        write_bundle(directory)
        per_view = run_verify(directory)["L1_per_view"][0]
    assert per_view["cell_total_b_sum"] == 115.0
    assert per_view["cell_total_a_filled_cells"] == 120.0
    assert per_view["cell_total_ratio_b_over_a"] == round(115.0 / 120.0, 6)


def test_i7_records_unjoinable_views_rather_than_dropping_them(bundle_dir):
    fields, rows = read_role(bundle_dir, "views_perf")
    write_role(bundle_dir, "views_perf", fields, rows[:-1])
    i7 = run_verify(bundle_dir)["invariants"]["I7"]
    assert len(i7["views_in_vop_only"]) == 1
    assert i7["views_joined"] == 11


def test_i7_declares_that_it_asserts_nothing(bundle_dir):
    assert run_verify(bundle_dir)["invariants"]["I7"]["assertion"].startswith("NONE")


# ---------------------------------------------------------------------------
# I8
# ---------------------------------------------------------------------------

def test_i8_frame_hash_collision_violates(bundle_dir):
    def collide(rows):
        rows[5]["ViewFrameHash"] = rows[2]["ViewFrameHash"]
    mutate(bundle_dir, "views_core", collide)
    bundle = run_verify(bundle_dir)
    assert "I8.VIEW_FRAME_HASH_COLLISION" in violation_ids(bundle)
    collisions = bundle["invariants"]["I8"]["collisions"]
    assert len(list(collisions.values())[0]) == 2


def test_i8_collision_is_reported_even_when_grid_dims_differ(bundle_dir):
    """The baseline's collisions had DIFFERENT dims; a uniqueness check keyed
    on (hash, dims) rather than on hash alone would have missed them."""
    def collide_with_different_dims(rows):
        rows[5]["ViewFrameHash"] = rows[2]["ViewFrameHash"]
    mutate(bundle_dir, "views_core", collide_with_different_dims)
    bundle = run_verify(bundle_dir)
    entries = list(bundle["invariants"]["I8"]["collisions"].values())[0]
    assert entries[0]["cells"] != entries[1]["cells"]
    assert "I8.VIEW_FRAME_HASH_COLLISION" in violation_ids(bundle)


def test_i8_not_evaluable_without_grid_dimensions(bundle_dir):
    drop_column(bundle_dir, "views_perf", "Width")
    i8 = run_verify(bundle_dir)["invariants"]["I8"]
    assert i8["status"] == V.STATUS_NOT_EVALUABLE
    assert "views_perf.Width" in i8["reason"]


def test_i8_not_evaluable_is_not_holds(bundle_dir):
    """The distinction this whole tool exists for."""
    drop_column(bundle_dir, "views_perf", "Width")
    assert run_verify(bundle_dir)["invariants"]["I8"]["status"] != V.STATUS_HOLDS


# ---------------------------------------------------------------------------
# L0
# ---------------------------------------------------------------------------

def test_l0_records_the_declared_path_and_its_provenance(bundle_dir):
    l0 = run_verify(bundle_dir, path="color-id")["L0_manifest"]
    assert l0["path"] == "color-id"
    assert "RowSource is a hardcoded constant" in l0["path_provenance"]


def test_l0_states_the_bundle_is_lossy(bundle_dir):
    l0 = run_verify(bundle_dir)["L0_manifest"]
    assert "LOSSY" in l0["lossiness"]
    assert l0["source_artifacts"]["files"]["views_core"]["sha256"]


def test_l0_does_not_invent_a_document_identity(bundle_dir):
    l0 = run_verify(bundle_dir)["L0_manifest"]
    assert l0["document"] is None
    assert "NOT DECLARED" in l0["document_provenance"]
    declared = run_verify(bundle_dir, document="Hospital.rvt")["L0_manifest"]
    assert declared["document"] == "Hospital.rvt"


def test_l0_reuses_the_existing_configuration_drift_scheme(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[4].__setitem__("ConfigHash", "cfg-zzz"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "CONFIGURATION_DRIFT" in str(excinfo.value)


def test_l0_records_diagnostics_json_without_parsing_it(bundle_dir, tmp_path):
    diagnostics = tmp_path / "diag.json"
    diagnostics.write_text(json.dumps({"anything": 1}))
    l0 = run_verify(bundle_dir,
                    diagnostics_json=str(diagnostics))["L0_manifest"]
    record = l0["source_artifacts"]["diagnostics_json"]
    assert record["parsed"] is False
    assert len(record["sha256"]) == 64


# ---------------------------------------------------------------------------
# L2 -- the rollup, and the call site that decides what n counts
# ---------------------------------------------------------------------------

def test_l2_call_site_counts_contributing_views_not_rows():
    """Exercises the CALL SITE with two visibly different candidate arguments.

    The fixture has 12 views of one ViewType. TEXT is non-zero in all 12;
    REGION is non-zero in 5. n_min is 8.

      - n over CONTRIBUTING values (correct): TEXT n=12 keeps its own cell,
        REGION n=5 rolls into OTHER_ROLLUP.
      - n over ALL ROWS (the defect): both would be n=12 and NOTHING would
        roll up.

    The two answers differ in which keys exist, so this discriminates rather
    than merely re-deriving the count.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        write_bundle(directory, n_views=12, view_types=["FloorPlan"])
        cells = run_verify(directory)["L2_per_category"]["FloorPlan"]["cells"]
    assert cells["TEXT"]["n"] == 12
    assert "REGION" not in cells
    assert "REGION" in cells[V.L2_ROLLUP_KEY]["categories_rolled_in"]
    assert cells[V.L2_ROLLUP_KEY]["per_category_n"]["REGION"] == 5


def test_l2_rollup_retains_n(bundle_dir):
    rollup = run_verify(bundle_dir)["L2_per_category"]["FloorPlan"]["cells"][
        V.L2_ROLLUP_KEY]
    assert rollup["n"] == sum(rollup["per_category_n"].values())


def test_l2_rollup_key_is_distinct_from_the_other_category():
    """AnnoCells_OTHER is a real bucket. Two things must not share a name."""
    assert V.L2_ROLLUP_KEY != "OTHER"
    assert "OTHER" in V.ANNO_CATEGORIES


def test_l2_rollup_floor_is_the_same_constant_as_n_min():
    assert V.N_MIN_L2_ROLLUP == V.N_MIN_ORDER_STATISTIC


def test_l2_labels_an_unset_view_type(bundle_dir):
    def blank(rows):
        for row in rows:
            row["ViewType"] = ""
    mutate(bundle_dir, "views_core", blank)
    mutate(bundle_dir, "views_vop", blank)
    assert V.VIEWTYPE_UNSET in run_verify(bundle_dir)["L2_per_category"]


# ---------------------------------------------------------------------------
# L3
# ---------------------------------------------------------------------------

def test_l3_selects_by_rule_and_labels_every_exemplar(bundle_dir):
    l3 = run_verify(bundle_dir)["L3_exemplars"]
    assert l3["selected"] > 0
    for exemplar in l3["exemplars"]:
        assert exemplar["selection_reason"]


def test_l3_includes_every_view_named_by_a_violation(bundle_dir):
    def collide(rows):
        rows[5]["ViewFrameHash"] = rows[2]["ViewFrameHash"]
    mutate(bundle_dir, "views_core", collide)
    bundle = run_verify(bundle_dir)
    named = {e["view_id"] for e in bundle["L3_exemplars"]["exemplars"]
             if e["selection_reason"] == "named by a violation"}
    assert len(named) == 2


def test_l3_records_truncation_rather_than_hiding_it(bundle_dir):
    l3 = run_verify(bundle_dir)["L3_exemplars"]
    assert l3["truncated"] == 0
    assert l3["selected"] <= V.L3_TARGET_MAX


def test_l3_does_not_pad_to_reach_the_target(tmp_path):
    directory = write_bundle(tmp_path / "small", n_views=2)
    l3 = run_verify(directory)["L3_exemplars"]
    assert l3["below_target_min"] is True
    assert l3["selected"] < V.L3_TARGET_MIN


# ---------------------------------------------------------------------------
# L4 -- the register and its known-open baseline
# ---------------------------------------------------------------------------

def test_l4_classifies_a_baselined_violation_as_known_open(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[5].__setitem__("ViewFrameHash",
                                            rows[2]["ViewFrameHash"]))
    register = run_verify(bundle_dir)["L4_violation_register"]
    assert register["new_violation_count"] == 0
    observed = register["observed"][0]
    assert observed["baseline_status"] == "KNOWN_OPEN"
    assert observed["first_observed_run"] == "20260917T112500"


def test_l4_classifies_an_unbaselined_violation_as_new(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[3].__setitem__("RunId", "20260918T999999"))
    register = run_verify(bundle_dir)["L4_violation_register"]
    assert [e["violation_id"] for e in register["new_violations"]] == [
        "I1.MULTIPLE_RUN_IDS"]


def test_l4_separates_new_from_known_open_in_one_run(bundle_dir):
    """The post-deletion read is 'new violations only'. It must survive a run
    that carries both kinds at once, or the baseline buys nothing."""
    def both(rows):
        rows[5]["ViewFrameHash"] = rows[2]["ViewFrameHash"]
        rows[3]["RunId"] = "20260918T999999"
    mutate(bundle_dir, "views_core", both)
    register = run_verify(bundle_dir)["L4_violation_register"]
    assert register["new_violation_count"] == 1
    assert "I8.VIEW_FRAME_HASH_COLLISION" in [
        e["violation_id"] for e in register["observed"]]


def test_l4_carries_undetected_baseline_entries_forward(bundle_dir):
    carried = {e["violation_id"]: e for e in
               run_verify(bundle_dir)["L4_violation_register"][
                   "carried_from_baseline"]}
    entry = carried["I2.CACHE_REHYDRATION_PROVENANCE"]
    assert entry["baseline_status"] == "KNOWN_OPEN_UNDETECTED"
    assert entry["absence_means"].startswith("NOTHING")


def test_l4_marks_the_report_only_baseline_entry_as_unassertable(bundle_dir):
    carried = {e["violation_id"]: e for e in
               run_verify(bundle_dir)["L4_violation_register"][
                   "carried_from_baseline"]}
    entry = carried["I7.CELL_TOTAL_DISAGREEMENT"]
    assert entry["baseline_status"] == "KNOWN_OPEN_REPORT_ONLY"
    assert entry["absence_means"].startswith("NOTHING")


def test_l4_is_a_register_not_a_verdict(bundle_dir):
    register = run_verify(bundle_dir)["L4_violation_register"]
    assert register["reported_as"] == "register"
    assert register["not_reported_as"] == "pass/fail"


def test_l4_baseline_seed_is_marked_dirty(bundle_dir):
    register = run_verify(bundle_dir)["L4_violation_register"]
    assert register["baseline_seed_run"] == "20260917T112500"
    assert "DIRTY" in register["baseline_seed_warning"]


def test_baseline_entries_declare_a_detector_or_declare_they_have_none():
    for violation_id, entry in V.KNOWN_OPEN_BASELINE.items():
        assert "first_observed_run" in entry, violation_id
        assert "detector" in entry, violation_id
        if entry["detector"] is None:
            assert entry["why_no_detector"], violation_id


# ---------------------------------------------------------------------------
# Refusals. Each asserts on the MESSAGE: exit code 2 alone proves nothing,
# because every one of these would also "pass" against a tool that refused
# for an unrelated reason.
# ---------------------------------------------------------------------------

def test_refuses_a_missing_required_role(bundle_dir):
    os.remove(role_path(bundle_dir, "views_perf"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "views_perf" in str(excinfo.value)


def test_tolerates_the_optional_occlusion_role(bundle_dir):
    os.remove(role_path(bundle_dir, "views_occlusion"))
    bundle = run_verify(bundle_dir)
    assert "views_occlusion" in bundle["L0_manifest"]["source_artifacts"][
        "roles_absent"]


def test_refuses_a_missing_directory(tmp_path):
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(tmp_path / "nope")
    assert "does not exist" in str(excinfo.value)


def test_refuses_an_empty_scan(bundle_dir):
    """glob on a mistyped path yields nothing and raises nothing."""
    path = role_path(bundle_dir, "views_vop")
    with open(path) as handle:
        header = handle.readline()
    with open(path, "w") as handle:
        handle.write(header)
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "zero data rows" in str(excinfo.value)


def test_refuses_two_files_for_one_role(bundle_dir):
    import shutil
    shutil.copy(role_path(bundle_dir, "views_core"),
                os.path.join(bundle_dir, "views_core_2026-09-19.csv"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "matches 2 files" in str(excinfo.value)


def test_refuses_an_unknown_boolean_token(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[0].__setitem__("IsOnSheet", "MAYBE"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "declared boolean vocabulary" in str(excinfo.value)


def test_refuses_an_unparseable_number(bundle_dir):
    mutate(bundle_dir, "views_perf",
           lambda rows: rows[1].__setitem__("Width", "wide"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "is not a number" in str(excinfo.value)


def test_refuses_a_fractional_grid_dimension(bundle_dir):
    mutate(bundle_dir, "views_perf",
           lambda rows: rows[1].__setitem__("Height", "80.5"))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "not an integer" in str(excinfo.value)


def test_refuses_an_unkeyable_row(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[2].__setitem__("ViewId", ""))
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir)
    assert "cannot be keyed" in str(excinfo.value)


def test_refuses_an_undeclared_path(bundle_dir):
    with pytest.raises(V.Refusal) as excinfo:
        V.verify(str(bundle_dir), "sideways", "disabled")
    assert "--path" in str(excinfo.value)


def test_refuses_an_undeclared_cache_mode(bundle_dir):
    with pytest.raises(V.Refusal) as excinfo:
        V.verify(str(bundle_dir), "geometry", "sometimes")
    assert "--cache-mode" in str(excinfo.value)


def test_refuses_a_diagnostics_path_that_names_nothing(bundle_dir, tmp_path):
    with pytest.raises(V.Refusal) as excinfo:
        run_verify(bundle_dir, diagnostics_json=str(tmp_path / "absent.json"))
    assert "does not exist" in str(excinfo.value)


def test_refuses_an_unsupported_bundle_schema_version(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({"bundle_schema_version": "9.9"}))
    with pytest.raises(V.Refusal) as excinfo:
        V.load_verification_bundle(str(path))
    assert "UNSUPPORTED_BUNDLE_SCHEMA_VERSION" in str(excinfo.value)


def test_refuses_an_unversioned_bundle(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps({}))
    with pytest.raises(V.Refusal) as excinfo:
        V.load_verification_bundle(str(path))
    assert "no bundle_schema_version" in str(excinfo.value)


def test_round_trips_its_own_bundle(bundle_dir, tmp_path):
    path = tmp_path / "out.json"
    path.write_text(json.dumps(run_verify(bundle_dir)))
    assert V.load_verification_bundle(str(path))["bundle_schema_version"] == \
        V.BUNDLE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# CLI contract, as a subprocess. Exit codes cannot be observed in-process.
# ---------------------------------------------------------------------------

def _cli(directory, *extra):
    return subprocess.run(
        [sys.executable, TOOL_PATH, str(directory), "--path", "geometry",
         "--cache-mode", "disabled", "--quiet"] + list(extra),
        capture_output=True, text=True)


def test_cli_control_exits_zero_on_a_clean_bundle(bundle_dir):
    assert _cli(bundle_dir).returncode == 0


def test_cli_exits_two_on_a_refusal(bundle_dir):
    os.remove(role_path(bundle_dir, "views_perf"))
    result = _cli(bundle_dir)
    assert result.returncode == 2
    assert result.stderr.startswith("REFUSED:")


def test_cli_default_is_not_a_gate(bundle_dir):
    """A register read as pass/fail is the failure mode this guards."""
    mutate(bundle_dir, "views_core",
           lambda rows: rows[3].__setitem__("RunId", "20260918T999999"))
    assert _cli(bundle_dir).returncode == 0


def test_cli_fail_on_new_is_opt_in(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[3].__setitem__("RunId", "20260918T999999"))
    assert _cli(bundle_dir, "--fail-on-new").returncode == 1


def test_cli_fail_on_new_ignores_known_open_violations(bundle_dir):
    mutate(bundle_dir, "views_core",
           lambda rows: rows[5].__setitem__("ViewFrameHash",
                                            rows[2]["ViewFrameHash"]))
    assert _cli(bundle_dir, "--fail-on-new").returncode == 0


def test_cli_writes_a_readable_bundle(bundle_dir, tmp_path):
    out = tmp_path / "nested" / "bundle.json"
    assert _cli(bundle_dir, "--out", str(out)).returncode == 0
    assert V.load_verification_bundle(str(out))["L0_manifest"]["run_id"] == RUN_ID


def test_i7_totals_are_keyed_by_run_and_view_not_view_alone(bundle_dir):
    """Discriminates the two candidate keyings, rather than re-deriving one.

    The bundle is given a SECOND row for the SAME ViewId under a second RunId,
    carrying a different FilledCells. Keyed by (RunId, ViewId) the two L1 rows
    carry 120 and 999; keyed by ViewId alone they collapse and both carry
    whichever was written last. An earlier version of this test varied only
    the RunId on a view no other row shared, so both keyings agreed and it
    stayed green against the defect.
    """
    def add_second_run(rows, filled=None):
        clone = dict(rows[0])
        clone["RunId"] = "20260918T999999"
        if filled is not None:
            clone["FilledCells"] = filled
        rows.append(clone)
    mutate(bundle_dir, "views_core", add_second_run)
    mutate(bundle_dir, "views_vop", add_second_run)
    mutate(bundle_dir, "views_perf", lambda rows: add_second_run(rows, 999))

    by_run = {row["run_id"]: row for row in run_verify(bundle_dir)["L1_per_view"]
              if row["view_id"] == 500000}
    assert set(by_run) == {RUN_ID, "20260918T999999"}
    assert by_run[RUN_ID]["cell_total_a_filled_cells"] == 120.0
    assert by_run["20260918T999999"]["cell_total_a_filled_cells"] == 999.0


def test_l3_can_reach_the_views_a_duplication_names(bundle_dir):
    mutate(bundle_dir, "views_vop", lambda rows: rows.append(dict(rows[2])))
    bundle = run_verify(bundle_dir)
    named = {e["view_id"] for e in bundle["L3_exemplars"]["exemplars"]
             if e["selection_reason"] == "named by a violation"}
    assert named, "a duplicate-row violation must reach L3"
