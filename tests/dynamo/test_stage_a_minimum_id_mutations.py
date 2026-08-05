import builtins
import csv
import json
from pathlib import Path

from tests.dynamo.probe_stage_a_minimum_id_mutations import (
    _add_repo_root_to_path,
    MUTATION_CATALOG,
    STATUS_APPLIED,
    STATUS_FAILED,
    backward_elimination_round,
    classify_mutation_evidence,
    dimensions_comparable,
    generate_stage1_variants,
    generate_stage2_variants,
    mutation_status_record,
    pixel_data_sha256,
    resolution_runs,
)


def test_stage1_factorial_complete():
    names = [v["name"] for v in generate_stage1_variants()]
    assert names[:2] == ["attached_element_overrides_only", "detached_preserve_original_display"]
    assert {"detached_none", "detached_A", "detached_S", "detached_F", "detached_AS", "detached_AF", "detached_SF", "detached_ASF"}.issubset(names)


def test_stage1_reduced_when_flat_colors_unsupported():
    names = [v["name"] for v in generate_stage1_variants(flat_colors_supported=False)]
    assert "detached_F" not in names
    assert "detached_ASF" not in names
    assert "detached_AS" in names


def test_stage2_has_single_additions_and_leave_one_outs():
    variants = generate_stage2_variants(("detach_template", "hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors"), ("category_halftone_neutralized", "shadows_off"))
    names = {v["name"] for v in variants}
    assert "baseline_plus_category_halftone_neutralized" in names
    assert "baseline_plus_shadows_off" in names
    assert "full_minus_category_halftone_neutralized" in names
    assert "full_minus_shadows_off" in names
    assert "full_suppression_reference" in names


def test_backward_elimination_bookkeeping():
    reduced, loo = backward_elimination_round(("a", "b", "c"), ("b",))
    assert reduced["mutations"] == ("a", "c")
    assert {v["name"] for v in loo} == {"reduced_minus_a", "reduced_minus_c"}


def test_mutation_status_serializes_all_required_fields():
    rec = mutation_status_record("smooth_edges_off", requested=True, status=STATUS_APPLIED, original=True, effective=False, applied=True)
    assert rec["mutation_id"] == "smooth_edges_off"
    assert rec["api_property_or_method"]
    json.dumps(rec)


def test_dimensions_comparison_invalidation():
    a = {"bounds_source": "active", "accepted_width_px": 10, "actual_width_px": 10, "actual_height_px": 5, "accepted_pixels_per_model_foot": 2.0}
    assert dimensions_comparable(a, dict(a))
    b = dict(a, actual_height_px=6)
    assert not dimensions_comparable(a, b)


def test_pixel_data_hashing_uses_decoded_rgb_order():
    rows = [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9)]]
    assert pixel_data_sha256(rows) == pixel_data_sha256(rows)
    assert pixel_data_sha256(rows) != pixel_data_sha256([[(1, 2, 3)]])


def test_classification_separates_fidelity_and_semantic():
    smooth = mutation_status_record("smooth_edges_off", requested=True, status=STATUS_APPLIED)
    phase = mutation_status_record("phase_filter_neutralized", requested=True, status=STATUS_APPLIED)
    failed = mutation_status_record("display_style_flat_colors", requested=True, status=STATUS_FAILED)
    assert classify_mutation_evidence(smooth, fidelity_failed_without=True) == "required_for_color_fidelity"
    assert classify_mutation_evidence(phase) == "semantic_diagnostics_not_for_default"
    assert classify_mutation_evidence(failed) == "blocked_or_unsupported"


def test_resolution_runs_default_to_paper_space_150():
    assert resolution_runs() == [{"policy": "paper_space_dpi", "target_dpi": 150.0, "fixed_pixel_width": 1600, "max_pixel_dimension": None}]


def test_json_and_csv_serialization(tmp_path):
    payload = {"mutation_inventory": MUTATION_CATALOG, "variants": [{"variant": "x", "requested_mutations": ["smooth_edges_off"], "mutations": {"smooth_edges_off": mutation_status_record("smooth_edges_off", True, STATUS_APPLIED)}, "image_analysis": {"unexpected_rgb_values": {}}, "visible_set_analysis": {}, "rollback_status": "PASS", "eligible_for_recommended_minimum": True}]}
    jp = tmp_path / "probe.json"
    cp = tmp_path / "matrix.csv"
    jp.write_text(json.dumps(payload), encoding="utf-8")
    with cp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["variant", "mutations_requested"])
        writer.writeheader(); writer.writerow({"variant": "x", "mutations_requested": "smooth_edges_off"})
    assert json.loads(jp.read_text())["variants"][0]["variant"] == "x"
    assert "smooth_edges_off" in cp.read_text()


def test_resolution_contract_fallback_does_not_require_dunder_file(monkeypatch):
    source = Path("tests/dynamo/probe_stage_a_minimum_id_mutations.py").read_text(encoding="utf-8")
    prefix = source.split("PROBE_NAME", 1)[0]
    namespace = {"__builtins__": builtins.__dict__, "__name__": "dynamo_string_probe"}
    exec(compile(prefix, "<string>", "exec"), namespace)
    assert "__file__" not in namespace
    assert namespace["_add_repo_root_to_path"](str(Path.cwd()), None) == str(Path.cwd())
    assert namespace["round_half_up_positive"](1.5) == 2


def test_explicit_bad_repo_root_is_rejected(tmp_path):
    bad_root = tmp_path / "not_repo"
    bad_root.mkdir()
    try:
        _add_repo_root_to_path(str(bad_root), None)
    except RuntimeError as ex:
        assert "IN[8] repository root" in str(ex)
    else:
        raise AssertionError("bad explicit repo root should fail")


def test_assignment_collection_uses_current_viewraster_signature():
    source = Path("tests/dynamo/probe_stage_a_minimum_id_mutations.py").read_text(encoding="utf-8")
    assert "from vop_interwoven.core.math_utils import Bounds2D" in source
    assert "ViewRaster(" in source
    assert "width=10" in source
    assert "height=10" in source
    assert "cell_size=1.0" in source
    assert 'tile_size=getattr(cfg, "tile_size", 16)' in source
    assert "cfg=cfg" in source
    assert "ViewRaster(10, 10, 1.0" not in source
