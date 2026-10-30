"""tools/compare_views_timing.py on hand-written views_timing CSVs.

Expected numbers are arithmetic on the fixture, not calls into the tool."""
import csv

import pytest

from tools import compare_views_timing as cvt

FIELDS = ["run_id", "view_id", "capture_total_ms", "phase_marks_create_ms",
          "phase_model_export_ms"]


def _csv(path, run_id, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for vid, total, marks, export in rows:
            w.writerow({"run_id": run_id, "view_id": vid, "capture_total_ms": total,
                        "phase_marks_create_ms": marks, "phase_model_export_ms": export})
    return path


def test_ratios_are_per_view_on_shared_views_and_skip_uncaptured(tmp_path):
    ref = _csv(tmp_path / "a.csv", "R1", [(1, 100, 10, 50), (2, 200, 20, 60), (3, 300, 30, 70),
                                          (9, "", "", "")])        # 9: not captured
    other = _csv(tmp_path / "b.csv", "R2", [(1, 110, 30, 50), (2, 220, 60, 60), (3, 330, 90, 70),
                                            (4, 999, 999, 999)])  # 4: not in the reference
    steps = ("phase_marks_create_ms", "phase_model_export_ms")
    rec = cvt.compare([cvt.load(ref), cvt.load(other)], steps)
    r0, r1 = rec["runs"]
    assert r0["views"] == 3 and r1["views"] == 4 and r1["common_views"] == 3
    assert r1["ratio_to_reference"]["phase_marks_create_ms"]["median"] == pytest.approx(3.0)
    assert r1["ratio_to_reference"]["phase_model_export_ms"]["median"] == pytest.approx(1.0)
    assert r0["median"]["phase_marks_create_ms"] == 20
    # The other run's median counts its own captured views, view 4 included.
    assert r1["median"]["phase_marks_create_ms"] == pytest.approx((60 + 90) / 2.0)


def test_a_zero_or_missing_step_gives_no_ratio(tmp_path):
    ref = _csv(tmp_path / "a.csv", "R1", [(1, 100, 0, 50)])
    other = _csv(tmp_path / "b.csv", "R2", [(1, 100, 5, 50)])
    rec = cvt.compare([cvt.load(ref), cvt.load(other)], ("phase_marks_create_ms",))
    assert rec["runs"][1]["ratio_to_reference"]["phase_marks_create_ms"]["n"] == 0


def test_main_needs_two_files(tmp_path, capsys):
    ref = _csv(tmp_path / "a.csv", "R1", [(1, 100, 10, 50)])
    with pytest.raises(SystemExit):
        cvt.main([str(ref)])
