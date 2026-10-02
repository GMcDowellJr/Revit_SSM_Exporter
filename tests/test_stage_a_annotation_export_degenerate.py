"""Item 3 (Greg, 2026-10-02): a degenerate annotation export is FLAGGED.

1-G400 (run 1001-1950) exported its annotation pass at 1543 x 154 against a
1543 x 1677 model capture: Revit's 10:1 export clamp. It is a view to fix in
Revit, and erroring on it is right -- but before this the capture reported no
fault and the failure surfaced only as a refused registration downstream.

The nine views of views.zip are the fixture: 1-G400 must fault, the other
eight must not (5100330, whose annotation reaches far along a wall section,
is the closest at a 2.33 coarsening bound). Mutations: dropping either
criterion in annotation_export_degenerate turns its case red; dropping the
fault row in the annotation pass turns the FILE test red.
"""
import pytest

from vop_interwoven.color_id_buffer import annotation_export_degenerate

from tests import test_stage_a_annotation_pass_probe_switches as switches
from tests.test_stage_a_registered_capture import _run, _sidecar

# (name, model w x h, annotation w x h); every one fitted horizontally.
VIEWS = [
    ("1-G400", (1543, 1677), (1543, 154)),
    ("CABINET TYPES", (3766, 1357), (3766, 1357)),
    ("HIGH ROOF PLAN", (4095, 3158), (4095, 3308)),
    ("LEVEL 1 WALL SECTION CALLOUTS", (4818, 1623), (4818, 2092)),
    ("LEVEL 2 LIFE SAFETY PLAN", (5166, 1749), (5166, 1891)),
    ("LEVEL 4 LIFE SAFETY PLAN", (5166, 1749), (5166, 1891)),
    ("WALL SECTION 1C", (673, 2873), (673, 1233)),
    ("WEST ELEVATION B_W", (2672, 1306), (2672, 1499)),
]


@pytest.mark.parametrize("name,model,anno", VIEWS)
def test_only_1_G400_is_degenerate_among_the_run_views(name, model, anno):
    rec = annotation_export_degenerate(anno[0], anno[1], model, vertical=False)
    if name == "1-G400":
        assert rec is not None and rec["reason"] == "aspect_clamp+coarsening"
    else:
        assert rec is None, (name, rec)


def test_each_criterion_alone_faults():
    # The clamp alone: a long, thin view whose model capture is as thin.
    rec = annotation_export_degenerate(2000, 200, (2000, 200), vertical=False)
    assert rec["reason"] == "aspect_clamp"
    # Coarsening alone: an ordinary aspect, a quarter of the model's height.
    rec = annotation_export_degenerate(1000, 250, (1000, 1000), vertical=False)
    assert rec["reason"] == "coarsening" and rec["coarsening_lower_bound"] == 4.0
    # The fitted axis decides which side is "derived".
    assert annotation_export_degenerate(250, 1000, (1000, 1000), vertical=True)[
        "reason"] == "coarsening"
    assert annotation_export_degenerate(250, 1000, (1000, 1000), vertical=False) is None


def test_unread_dimensions_decide_nothing():
    assert annotation_export_degenerate(None, 154, (1543, 1677), False) is None
    assert annotation_export_degenerate(1543, 0, (1543, 1677), False)[
        "reason"] == "empty_export"


def _degenerate_fault(path):
    return [f for f in _sidecar(path)["capture_integrity"]["capture_faults"]
            if f.get("fault") == "annotation_export_degenerate"]


def test_a_degenerate_annotation_export_is_a_fault_in_the_FILE(tmp_path, monkeypatch):
    real = switches._write_tiff_header

    def _clamped(path, w, h):
        if "_anno" in path:
            h = max(1, w // 10)
        real(path, w, h)
    monkeypatch.setattr(switches, "_write_tiff_header", _clamped)
    out, _v, _d, _e, _diag = _run(tmp_path)
    faults = _degenerate_fault(out["annotation_sidecar_path"])
    assert len(faults) == 1
    assert faults[0]["detail"]["reason"].startswith("aspect_clamp")
    assert out["annotation_pass_success"] is False


def test_control_an_ordinary_annotation_export_has_no_such_fault(tmp_path):
    out, _v, _d, _e, _diag = _run(tmp_path)
    assert _degenerate_fault(out["annotation_sidecar_path"]) == []
    assert out["annotation_pass_success"] is True
