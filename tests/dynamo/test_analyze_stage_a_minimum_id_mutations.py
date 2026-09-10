import json
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")
pytest.importorskip("numpy")

from tools.analyze_stage_a_probe import analyze_json


def test_analyzer_handles_minimum_id_mutations_probe(tmp_path):
    tiff = tmp_path / "View_1.dpi_150.detached_ASF.tiff"
    img = Image.new("RGB", (2, 2), (255, 255, 255))
    img.putpixel((0, 0), (10, 20, 30))
    img.save(tiff)
    report = {
        "probe": {"name": "stage_a_minimum_id_mutations"},
        "assignment_set": {"assigned": {"100": {"rgb": [10, 20, 30], "element_id": 100}}},
        "variants": [
            {
                "variant": "detached_ASF",
                "requested_mutations": ["detach_template"],
                "mutations": {"detach_template": {"requested": True, "status": "APPLIED"}},
                "diagnostic": False,
                "rollback_status": "PASS",
                "resolution": {"actual_width_px": 2},
                "image_analysis": {"path": str(tiff)},
            },
            {
                "variant": "diagnostic_neutral_phase_filter",
                "requested_mutations": ["phase_filter_neutralized"],
                "mutations": {"phase_filter_neutralized": {"requested": True, "status": "APPLIED"}},
                "diagnostic": True,
                "image_analysis": {"path": str(tiff)},
            },
        ],
    }
    jp = tmp_path / "View_1.dpi_150.minimum_id_mutations.json"
    jp.write_text(json.dumps(report), encoding="utf-8")

    out, msg = analyze_json(jp)
    analyzed = json.loads(out.read_text(encoding="utf-8"))

    assert out.name == "View_1.dpi_150.minimum_id_mutations.analyzed.json"
    assert "minimum detached_ASF" in msg
    variant = analyzed["variants"][0]
    assert variant["image_analysis"]["analysis_status"] == "complete" or variant["image_analysis"]["status"] == "analyzed_external"
    assert variant["image_analysis"]["off_palette_foreground_pixels"] == 0
    assert variant["image_analysis"]["pixel_data_sha256"]
    assert analyzed["recommendation"]["recommended_minimum"]["fidelity_status"] == "PASS"
    assert "diagnostic_neutral_phase_filter" in analyzed["recommendation"]["semantic_diagnostics_not_for_default"]


def test_analyzer_marks_failed_attestation_as_non_evidence(tmp_path):
    tiff = tmp_path / "View_1.dpi_150.detached_F.tiff"
    Image.new("RGB", (1, 1), (10, 20, 30)).save(tiff)
    report = {
        "probe": {"name": "stage_a_minimum_id_mutations"},
        "assignment_set": {"assigned": {"100": {"rgb": [10, 20, 30]}}},
        "variants": [{
            "variant": "detached_F",
            "requested_mutations": ["display_style_flat_colors"],
            "mutations": {"display_style_flat_colors": {"requested": True, "status": "UNSUPPORTED"}},
            "image_analysis": {"path": str(tiff)},
        }],
    }
    jp = tmp_path / "View_1.dpi_150.minimum_id_mutations.json"
    jp.write_text(json.dumps(report), encoding="utf-8")

    out, _ = analyze_json(jp)
    analyzed = json.loads(out.read_text(encoding="utf-8"))
    assert analyzed["variants"][0]["image_analysis"]["analysis_status"] == "not_analyzed"
    assert analyzed["recommendation"]["blocked_or_unsupported"][0]["mutation"] == "display_style_flat_colors"


def test_analyzer_does_not_recommend_without_real_metrics():
    from tools.analyze_stage_a_probe import recommend_minimum_mutations
    rec = recommend_minimum_mutations([{
        "variant": "attached_element_overrides_only",
        "requested_mutations": [],
        "mutations": {},
        "diagnostic": False,
        "image_analysis": {"analysis_status": "not_analyzed"},
    }])
    assert rec["recommended_minimum"]["fidelity_status"] == "FAIL"
    assert rec["recommended_minimum"]["mutations"] == []
    # No rollback evidence was supplied at all -- must read as INCONCLUSIVE,
    # never as a default FAIL manufactured from "no candidate selected".
    assert rec["recommended_minimum"]["rollback_status"] == "INCONCLUSIVE"


def test_recommend_minimum_reports_rollback_pass_without_a_selected_candidate():
    """Mirrors the elevation mutation-closure campaign evidence: rollback and
    state restoration passed for every variant, but no variant was eligible
    for the recommended minimum (pixel fidelity evidence was never collected).
    rollback_status must reflect the real PASS evidence, not FAIL-by-default."""
    from tools.analyze_stage_a_probe import recommend_minimum_mutations
    rec = recommend_minimum_mutations([
        {
            "variant": "attached_element_overrides_only",
            "requested_mutations": [],
            "mutations": {},
            "diagnostic": False,
            "rollback_status": "PASS",
            "image_analysis": {"analysis_status": "not_analyzed"},
        },
        {
            "variant": "detached_A",
            "requested_mutations": ["hide_annotation_categories"],
            "mutations": {"hide_annotation_categories": {"requested": True, "status": "APPLIED"}},
            "diagnostic": False,
            "rollback_status": "PASS",
            "image_analysis": {"analysis_status": "not_analyzed"},
        },
    ])
    assert rec["recommended_minimum"]["fidelity_status"] == "FAIL"
    assert rec["recommended_minimum"]["mutations"] == []
    assert rec["recommended_minimum"]["rollback_status"] == "PASS"


def test_recommend_minimum_rollback_fail_reflects_actual_failure():
    from tools.analyze_stage_a_probe import recommend_minimum_mutations
    rec = recommend_minimum_mutations([{
        "variant": "attached_element_overrides_only",
        "requested_mutations": [],
        "mutations": {},
        "diagnostic": False,
        "rollback_status": "FAIL",
        "image_analysis": {"analysis_status": "not_analyzed"},
    }])
    assert rec["recommended_minimum"]["rollback_status"] == "FAIL"
