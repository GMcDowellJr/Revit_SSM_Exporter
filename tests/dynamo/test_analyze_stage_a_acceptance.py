import json

import pytest

import tools.analyze_stage_a_probe as analyzer


def envelope(probe_id, native, **overrides):
    value = {
        "probe_id": probe_id,
        "probe_schema_version": "1.0",
        "execution_status": "completed",
        "rollback_status": "succeeded",
        "state_restoration_status": "restored",
        "requested_settings": {},
        "artifact_paths": ["raw/example.tiff"],
        "errors": [],
        "warnings": [],
        "native_report": native,
        "campaign_id": "campaign", "batch_id": "batch", "run_id": "run", "job_id": "job",
    }
    value.update(overrides)
    return value


def run(tmp_path, report):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    out, _ = analyzer.analyze_json(path)
    return json.loads(out.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("probe_id,native", [
    ("stage_a_image_alignment", {"exports": {}}),
    ("stage_a_minimum_id_mutations", {"variants": []}),
    ("stage_a_model_linework", {"modes": []}),
    ("stage_a_external_sources", {"variants": []}),
    ("stage_a_graphics_semantics", {"variants": []}),
])
def test_every_dispatch_branch_returns_common_schema(tmp_path, monkeypatch, probe_id, native):
    monkeypatch.setattr(analyzer, "Image", None)
    record, raw = run(tmp_path, envelope(probe_id, native))
    required = {"analysis_schema_version", "campaign_id", "batch_id", "run_id", "job_id",
                "probe_id", "probe_schema_version", "analyzer_version", "source_report",
                "artifact_references", "analysis_status", "acceptance_status", "execution_status",
                "reason_codes", "summary", "metrics", "checks", "limitations", "errors",
                "warnings", "analyzed_at"}
    assert required <= set(record)
    assert record["probe_id"] == probe_id
    assert record["acceptance_status"] == "INCONCLUSIVE"
    assert record["artifact_references"] == ["raw/example.tiff"]
    assert raw.read_text(encoding="utf-8") == json.dumps(envelope(probe_id, native))


def test_unknown_and_unsupported_schema_are_structured_errors(tmp_path):
    with pytest.raises(ValueError, match="UNKNOWN_PROBE_TYPE"):
        run(tmp_path, envelope("stage_a_unknown", {}))
    with pytest.raises(ValueError, match="UNSUPPORTED_PROBE_SCHEMA_VERSION"):
        run(tmp_path, envelope("stage_a_model_linework", {}, probe_schema_version="99"))


def test_explicit_identity_wins_over_filename_and_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    record, _ = run(tmp_path, envelope("stage_a_external_sources", {"variants": []}))
    assert record["summary"]["probe_family"] == "external_sources"
    bad = envelope("stage_a_external_sources", {"probe": {"name": "stage_a_image_alignment"}})
    with pytest.raises(ValueError, match="INCONSISTENT_PROBE_ID"):
        run(tmp_path, bad)


def test_gate_precedence_execution_rollback_and_restoration(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    for field, value, reason in [
        ("execution_status", "failed", "RAW_EXECUTION_FAILED"),
        ("rollback_status", "failed", "ROLLBACK_FAILED"),
        ("state_restoration_status", "not_restored", "STATE_RESTORATION_FAILED"),
    ]:
        record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", {"variants": []}, **{field: value}))
        assert record["acceptance_status"] == "FAIL"
        assert reason in record["reason_codes"]


def test_aggregate_rollback_comes_from_all_executed_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"variants": [
        {"variant": "a", "rollback_status": "PASS", "state": {"captured_state_equal_after_rollback": True}},
        {"variant": "b", "rollback_status": "FAIL", "state": {"captured_state_equal_after_rollback": True}},
    ]}
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", native,
                                      rollback_status="unknown", state_restoration_status="unknown"))
    assert record["acceptance_status"] == "FAIL"
    assert "ROLLBACK_FAILED" in record["reason_codes"]


def test_partial_run_and_attestation_failure_have_stable_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"variants": [{"variant": "ran", "requested_mutations": ["x"],
                            "mutations": {"x": {"status": "FAILED"}}, "image_analysis": {}}]}
    report = envelope("stage_a_minimum_id_mutations", native,
                      requested_settings={"selection": ["ran", "missing"]})
    record, _ = run(tmp_path, report)
    assert record["acceptance_status"] == "FAIL"
    assert "REQUESTED_CASE_NOT_ANALYZED" in record["reason_codes"]
    assert "MUTATION_ATTESTATION_FAILED" in record["reason_codes"]
    coverage = next(c for c in record["checks"] if c["check_id"] == "requested_case_coverage")
    assert coverage["evidence"]["not_analyzed"] == ["missing"]


def test_actual_dimensions_override_null_redundant_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"variants": [{"variant": "v", "requested_mutations": [], "mutations": {},
                            "resolution": {"actual_width_px": None, "accepted_width_px": 2,
                                           "predicted_height_px": 3},
                            "image_analysis": {"actual_dimensions": [2, 3]}}]}
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", native))
    dimensions = next(c for c in record["checks"] if c["check_id"] == "dimensions")
    assert dimensions["status"] == "PASS"


def test_dimension_and_repeatability_failures_are_not_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"modes": [{"mode": "wire", "images": [
        {"analysis": {"dimensions": [2, 2]}}, {"analysis": {"dimensions": [2, 2]}}],
        "classification": {"dimension_alignment": "fail", "fill_contamination": "acceptable"},
        "sequential_export_repeatability": {"available": True, "same_sha256": False,
                                             "same_dimensions": True}}]}
    record, _ = run(tmp_path, envelope("stage_a_model_linework", native))
    assert record["acceptance_status"] == "FAIL"
    assert {"DIMENSION_MISMATCH", "REPEATABILITY_MISMATCH"} <= set(record["reason_codes"])


def test_missing_dependency_is_limitation_not_rollback_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    record, _ = run(tmp_path, envelope("stage_a_image_alignment", {"exports": {}}))
    assert record["analysis_status"] == "LIMITED"
    assert record["limitations"][0]["code"] == "IMAGE_ANALYSIS_DEPENDENCY_UNAVAILABLE"
    rollback = next(c for c in record["checks"] if c["check_id"] == "rollback")
    assert rollback["status"] == "PASS"


def test_complete_alignment_evidence_can_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", object())
    monkeypatch.setattr(analyzer, "np", object())
    monkeypatch.setattr(analyzer, "_enrich_report", lambda *args: [])
    image = {"actual_width": 2, "actual_height": 3, "effective_pixel_size": 2,
             "resolution": {"accepted_width_px": 2, "predicted_height_px": 3},
             "marker_analysis_available": True, "missing_markers": []}
    native = {"exports": {"original": {"images": [image], "sequential_export_equality": True}},
              "model_to_canvas_placement": {"lossless_padding_sufficient": True}}
    record, _ = run(tmp_path, envelope("stage_a_image_alignment", native))
    assert record["analysis_status"] == "COMPLETED"
    assert record["acceptance_status"] == "PASS"


def test_analyzer_exception_cannot_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", object())
    monkeypatch.setattr(analyzer, "np", object())
    def explode(*args):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(analyzer, "_enrich_report", explode)
    record, _ = run(tmp_path, envelope("stage_a_image_alignment", {"exports": {}}))
    assert record["analysis_status"] == "ERROR"
    assert record["acceptance_status"] == "INCONCLUSIVE"
    assert "ANALYZER_EXCEPTION" in record["reason_codes"]


def test_cli_returns_failure_for_malformed_report(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert analyzer.main([str(bad)]) == 1
    assert "JSONDecodeError" in capsys.readouterr().out


def test_run_manifest_produces_one_record_per_raw_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    raw = envelope("stage_a_external_sources", {"variants": []})
    manifest = {"schema_version": "1.0", "campaign_id": "c", "batch_id": "b", "run_id": "r",
                "jobs": [{"job_id": "j", "raw_result_envelope": raw}]}
    analyzed, _ = run(tmp_path, manifest)
    assert analyzed["acceptance_records"][0]["campaign_id"] == "c"
    assert analyzed["acceptance_records"][0]["job_id"] == "j"


def test_manifest_fan_out_preserves_comparison_reference_from_job_record(tmp_path, monkeypatch):
    # The envelope's own requested_settings reflects only what the adapter
    # forwarded to run_probe() (comparison_reference correctly stripped
    # before dispatch); the outer manifest job record's requested_settings
    # is where the executor preserves the full as-configured settings, and
    # that is what must survive into the normalized record for provenance.
    monkeypatch.setattr(analyzer, "Image", None)
    raw = envelope("stage_a_minimum_id_mutations", {"variants": []},
                   requested_settings={"selection": "attached_AS", "target_dpi": 150})
    manifest = {"schema_version": "1.0", "campaign_id": "c", "batch_id": "b", "run_id": "r",
                "jobs": [{"job_id": "j", "raw_result_envelope": raw,
                          "requested_settings": {"dpi": 150, "selection": "attached_AS",
                                                 "comparison_reference": "detached_AS"}}]}
    analyzed, _ = run(tmp_path, manifest)
    assert analyzed["acceptance_records"][0]["comparison_reference"] == "detached_AS"


def test_alignment_coverage_normalizes_resolution_qualified_export_keys():
    native = {"exports": {"original.dpi_150": {"images": []},
                          "original.fixed_1600": {"images": []}}}
    assert analyzer._actual_names(native, "image_alignment") == ["original"]
    checks = analyzer._family_checks(
        {"requested_settings": {"mode": "original"}}, native, "image_alignment")
    coverage = next(check for check in checks if check["check_id"] == "requested_case_coverage")
    assert coverage["status"] == "PASS"
    assert coverage["evidence"]["not_analyzed"] == []


def test_alignment_coverage_requires_every_requested_resolution_case():
    native = {"exports": {"original.dpi_150": {"images": [{}]}}}
    checks = analyzer._family_checks({"requested_settings": {
        "mode": "original", "resolution_cases": ["dpi_150", "dpi_300"]}},
        native, "image_alignment")
    coverage = next(check for check in checks if check["check_id"] == "requested_case_coverage")
    assert coverage["status"] == "FAIL"
    assert coverage["evidence"]["requested"] == ["original.dpi_150", "original.dpi_300"]
    assert coverage["evidence"]["not_analyzed"] == ["original.dpi_300"]
    assert "REQUESTED_CASE_NOT_ANALYZED" in coverage["reason_codes"]


def test_alignment_all_mode_requires_each_mode_at_each_resolution():
    native = {"exports": {
        "original.dpi_150": {"images": [{}]}, "model_bounds.dpi_150": {"images": [{}]},
        "canvas_bounds.dpi_150": {"images": [{}]}, "original.dpi_300": {"images": [{}]},
    }}
    coverage = analyzer._alignment_coverage({"requested_settings": {
        "mode": "all", "resolution_cases": ["dpi_150", "dpi_300"]}}, native)
    assert coverage["status"] == "FAIL"
    assert coverage["evidence"]["not_analyzed"] == [
        "canvas_bounds.dpi_300", "model_bounds.dpi_300"]


def test_alignment_enrichment_uses_qualified_model_export_for_placement(tmp_path, monkeypatch):
    image_path = tmp_path / "model.tiff"
    image_path.write_bytes(b"synthetic")
    monkeypatch.setattr(analyzer, "analyze_alignment_image", lambda *args: {
        "status": "analyzed_external", "actual_width": 100, "actual_height": 50,
        "affine_fit_residual": {"available": False}, "marker_analysis_available": True,
        "missing_markers": [], "content_rect_px": [0, 0, 99, 49],
    })
    native = {
        "exports": {"model_bounds.dpi_150": {
            "target_bounds_uv": [0, 0, 10, 5],
            "images": [{"path": str(image_path), "effective_pixel_size": 100,
                        "resolution": {"accepted_width_px": 100,
                                       "predicted_height_px": 50}}],
        }},
        "bounds": {"pre_annotation_model_uv": [0, 0, 10, 5],
                   "canvas_uv": [-1, -1, 11, 6]},
    }
    analyzer._enrich_report(tmp_path / "raw.json", native, "stage_a_image_alignment")
    assert native["model_to_canvas_placement"]["available"] is True
    assert native["model_to_canvas_placement"]["tiff_actual_width_px"] == 100
    assert native["exports"]["model_bounds.dpi_150"]["images"][0]["resolution"] == {
        "accepted_width_px": 100, "predicted_height_px": 50}


def test_alignment_enrichment_preserves_native_placement_without_analyzed_export(tmp_path):
    placement = {"available": True, "lossless_padding_sufficient": True,
                 "source": "native_resolution_contract"}
    native = {"exports": {}, "model_to_canvas_placement": placement.copy()}
    analyzer._enrich_report(tmp_path / "raw.json", native, "stage_a_image_alignment")
    assert native["model_to_canvas_placement"] == placement


def test_alignment_dimensions_validate_predicted_height():
    image = {"actual_width": 100, "actual_height": 49, "effective_pixel_size": 100,
             "resolution": {"accepted_width_px": 100, "predicted_height_px": 50}}
    native = {"exports": {"original.dpi_150": {
        "images": [image], "sequential_export_equality": True}}}
    checks = analyzer._family_checks({}, native, "image_alignment")
    dimensions = next(check for check in checks if check["check_id"] == "dimensions")
    assert dimensions["status"] == "FAIL"
    assert dimensions["reason_codes"] == ["DIMENSION_MISMATCH"]
    assert dimensions["evidence"]["mismatches"] == [{
        "case": "original.dpi_150", "required": [100, 50], "actual": [100, 49]}]


def test_alignment_repeatability_requires_explicit_comparison():
    image = {"actual_width": 100, "actual_height": 50,
             "resolution": {"accepted_width_px": 100, "predicted_height_px": 50}}
    native = {"exports": {"original.dpi_150": {
        "images": [image], "sequential_export_equality": None}}}
    checks = analyzer._family_checks({}, native, "image_alignment")
    repeatability = next(check for check in checks if check["check_id"] == "repeatability")
    assert repeatability["status"] == "INCONCLUSIVE"
    assert repeatability["reason_codes"] == ["REPEATABILITY_EVIDENCE_MISSING"]
    assert repeatability["evidence"]["incomplete_cases"] == ["original.dpi_150"]


def test_linework_modes_match_their_per_resolution_references(tmp_path, monkeypatch):
    paths = {}
    for name in ("ref150", "ref300", "mode150", "mode300"):
        paths[name] = tmp_path / (name + ".tiff")
        paths[name].write_bytes(b"synthetic")

    dimensions = {"ref150": [100, 50], "mode150": [100, 50],
                  "ref300": [200, 100], "mode300": [200, 100]}
    def fake_analysis(path, reference_dims=None):
        dims = dimensions[path.stem]
        result = {"status": "analyzed_external", "dimensions": dims,
                  "dark_line_pixel_count": 10, "unexpected_color_pixel_count": 0,
                  "non_dark_gray_foreground_pixel_count": 0, "foreground_pixel_count": 10,
                  "sha256": path.stem}
        if reference_dims:
            result["reference_dimension_agreement"] = {
                "reference_dimensions": reference_dims, "matches": dims == reference_dims}
        return result
    monkeypatch.setattr(analyzer, "analyze_linework_image", fake_analysis)

    def image(name, dpi):
        return {"path": str(paths[name]), "resolution": {
            "policy": "paper_space_dpi", "target_dpi": dpi,
            "requested_width_px": dimensions[name][0],
            "accepted_width_px": dimensions[name][0],
            "predicted_height_px": dimensions[name][1],
        }}
    native = {
        "reference_runs": [
            {"images": [image("ref150", 150)]},
            {"images": [image("ref300", 300)]},
        ],
        "modes": [
            {"mode": "wire", "images": [image("mode150", 150)]},
            {"mode": "wire", "images": [image("mode300", 300)]},
        ],
    }
    analyzer._enrich_report(tmp_path / "raw.json", native, "stage_a_model_linework")
    assert [mode["classification"]["dimension_alignment"] for mode in native["modes"]] == ["pass", "pass"]
    assert [mode["images"][0]["analysis"]["reference_dimension_agreement"]["reference_dimensions"]
            for mode in native["modes"]] == [[100, 50], [200, 100]]


def _external_variant(name, source, analyzed=True, rendered=True):
    assignment = {"assignment_key": name, "source_type": source, "paint_success": True}
    analysis = ({"status": "analyzed_external", "actual_dimensions": [2, 2],
                 "expected_color_pixel_counts": {name: 1 if rendered else 0}}
                if analyzed else {"status": "pillow_unavailable"})
    return {"variant": name, "assignments": [assignment], "image_analysis": analysis,
            "transaction_group": {"rollback_succeeded": True}, "state": {}}


def test_external_false_raw_boolean_remains_inconclusive_without_image_evidence():
    variants = [
        _external_variant("host_reference_coloring", "HOST", analyzed=False),
        _external_variant("linked_per_element_linkelementid_coloring", "LINK", analyzed=False),
        {**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK", analyzed=False),
         "hidden_link_fallback": [123]},
        _external_variant("dwg_importinstance_coloring", "DWG", analyzed=False),
    ]
    native = {"variants": variants, "required_source_family_status": {
        source: {"ran": True, "passed": False} for source in ("HOST", "LINK", "DWG")}}
    checks = analyzer._family_checks({}, native, "external_sources")
    source_checks = [check for check in checks if check["check_id"].startswith("external_source_")]
    assert {check["status"] for check in source_checks} == {"INCONCLUSIVE"}
    assert all("EXTERNAL_SOURCE_VISUAL_FAILURE" not in check["reason_codes"] for check in source_checks)


def test_external_refreshed_variant_metrics_override_stale_family_boolean():
    variants = [
        _external_variant("host_reference_coloring", "HOST"),
        _external_variant("linked_per_element_linkelementid_coloring", "LINK"),
        {**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK"),
         "hidden_link_fallback": [123]},
        _external_variant("dwg_importinstance_coloring", "DWG"),
    ]
    native = {"variants": variants, "required_source_family_status": {
        source: {"ran": True, "passed": False} for source in ("HOST", "LINK", "DWG")}}
    checks = analyzer._family_checks({}, native, "external_sources")
    source_checks = [check for check in checks if check["check_id"].startswith("external_source_")]
    assert {check["status"] for check in source_checks} == {"PASS"}


def _link_variant(name, link_override_status, rendered=True, extra_assignment=None):
    assignment = {"assignment_key": name, "source_type": "LINK", "link_override_status": link_override_status,
                  "paint_success": link_override_status == "APPLIED"}
    analysis = {"status": "analyzed_external", "actual_dimensions": [2, 2],
                "expected_color_pixel_counts": {name: 1 if rendered else 0}}
    assignments = [assignment] + ([extra_assignment] if extra_assignment else [])
    return {"variant": name, "assignments": assignments, "image_analysis": analysis,
            "transaction_group": {"rollback_succeeded": True}, "state": {}}


def test_external_rvt_link_only_job_not_penalized_for_missing_host_or_dwg():
    # An RVT-link capability job's requested_settings restrict selection to
    # the two LINK variants; HOST and DWG must not appear as required checks
    # at all, let alone as failures, just because this job never ran them.
    variants = [
        _link_variant("linked_per_element_linkelementid_coloring", "UNSUPPORTED", rendered=False),
        {**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK"),
         "hidden_link_fallback": [123]},
    ]
    native = {"variants": variants}
    data = {"requested_settings": {"selection": [
        "linked_per_element_linkelementid_coloring", "forced_linked_override_failure_hide_instance_fallback"]}}
    checks = analyzer._family_checks(data, native, "external_sources")
    source_checks = {check["check_id"]: check for check in checks if check["check_id"].startswith("external_source_")}
    assert set(source_checks) == {"external_source_link"}
    assert source_checks["external_source_link"]["status"] == "PASS"


def test_external_dwg_only_job_not_penalized_for_missing_link_or_host():
    variants = [_external_variant("dwg_importinstance_coloring", "DWG")]
    native = {"variants": variants}
    data = {"requested_settings": {"selection": ["dwg_importinstance_coloring"]}}
    checks = analyzer._family_checks(data, native, "external_sources")
    source_checks = {check["check_id"]: check for check in checks if check["check_id"].startswith("external_source_")}
    assert set(source_checks) == {"external_source_dwg"}
    assert source_checks["external_source_dwg"]["status"] == "PASS"


def test_external_unsupported_link_override_is_explicit_capability_not_generic_fail():
    variants = [
        _link_variant("linked_per_element_linkelementid_coloring", "UNSUPPORTED", rendered=False),
        {**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK"),
         "hidden_link_fallback": [123]},
    ]
    native = {"variants": variants}
    checks = analyzer._family_checks({}, native, "external_sources")
    link_check = next(c for c in checks if c["check_id"] == "external_source_link")
    assert link_check["status"] == "PASS"
    assert "EXTERNAL_SOURCE_VISUAL_FAILURE" not in link_check["reason_codes"]
    assert link_check["evidence"]["variant_statuses"]["linked_per_element_linkelementid_coloring"] == "UNSUPPORTED"


def test_external_unexpected_link_override_exception_is_a_real_failure():
    variants = [
        _link_variant("linked_per_element_linkelementid_coloring", "FAILED", rendered=False),
        {**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK"),
         "hidden_link_fallback": [123]},
    ]
    native = {"variants": variants}
    checks = analyzer._family_checks({}, native, "external_sources")
    link_check = next(c for c in checks if c["check_id"] == "external_source_link")
    assert link_check["status"] == "FAIL"
    assert "EXTERNAL_SOURCE_VISUAL_FAILURE" in link_check["reason_codes"]


def test_external_whole_link_fallback_passes_independently_of_per_element_override():
    # A job requesting only the forced whole-link-suppression fallback (not
    # the per-element override variant) must be able to PASS on its own -
    # the existing supported fallback behavior must remain independently
    # testable after the redesign.
    variants = [{**_external_variant("forced_linked_override_failure_hide_instance_fallback", "LINK"),
                 "hidden_link_fallback": [123]}]
    native = {"variants": variants}
    data = {"requested_settings": {"selection": ["forced_linked_override_failure_hide_instance_fallback"]}}
    checks = analyzer._family_checks(data, native, "external_sources")
    source_checks = {check["check_id"]: check for check in checks if check["check_id"].startswith("external_source_")}
    assert set(source_checks) == {"external_source_link"}
    assert source_checks["external_source_link"]["status"] == "PASS"


def test_external_host_only_selection_unaffected_by_redesign():
    variants = [_external_variant("host_reference_coloring", "HOST")]
    native = {"variants": variants}
    data = {"requested_settings": {"selection": ["host_reference_coloring"]}}
    checks = analyzer._family_checks(data, native, "external_sources")
    source_checks = {check["check_id"]: check for check in checks if check["check_id"].startswith("external_source_")}
    assert set(source_checks) == {"external_source_host"}
    assert source_checks["external_source_host"]["status"] == "PASS"


def test_external_dwg_ineligible_fixture_is_not_applicable_not_a_silent_fail():
    # Every requested DWG variant was skipped (e.g. the supplied ImportInstance
    # was excluded because ViewSpecific == True) - this must read as an
    # explicit, non-blocking NOT_APPLICABLE, never a generic INCONCLUSIVE/FAIL.
    variants = [{"variant": "dwg_importinstance_coloring", "skipped": True,
                 "reason": "Supplied DWG ImportInstance(s) ineligible under current production discovery "
                           "policy: [{'exclusion_reason': 'EXCLUDED_VIEW_SPECIFIC_IMPORT'}]"}]
    native = {"variants": variants}
    data = {"requested_settings": {"selection": ["dwg_importinstance_coloring"]}}
    checks = analyzer._family_checks(data, native, "external_sources")
    dwg_check = next(c for c in checks if c["check_id"] == "external_source_dwg")
    assert dwg_check["status"] == "NOT_APPLICABLE"
    assert "EXTERNAL_SOURCE_CASE_INELIGIBLE" in dwg_check["reason_codes"]


# --- Stage 1 mutation-closure evidence: template-blocked attestation failure ---

def _attached_as_blocked_by_template_variant():
    return {
        "variant": "attached_AS", "requested_mutations": ["hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "hide_annotation_categories": {"status": "BLOCKED_BY_TEMPLATE", "template_controlled": True},
            "smooth_edges_off": {"status": "APPLIED", "template_controlled": False},
        },
        "mutation_attestation_passed": False,
        "image_analysis": {"analysis_status": "not_exported_failed_attestation"},
    }


def test_blocked_by_template_gets_distinct_reason_code_and_tiffs_are_not_applicable(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"variants": [_attached_as_blocked_by_template_variant()]}
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", native))
    assert record["acceptance_status"] == "FAIL"
    assert "MUTATION_ATTESTATION_FAILED" in record["reason_codes"]
    assert "MUTATION_BLOCKED_BY_TEMPLATE_ONLY" in record["reason_codes"]
    checks_by_id = {c["check_id"]: c for c in record["checks"]}
    # A TIFF was never exported because attestation failed first - that must
    # never read as a vacuous PASS on empty evidence.
    assert checks_by_id["required_tiffs"]["status"] == "NOT_APPLICABLE"
    assert checks_by_id["dimensions"]["status"] == "NOT_APPLICABLE"
    assert checks_by_id["palette_fidelity"]["status"] == "NOT_APPLICABLE"


def test_unrelated_mutation_failure_does_not_claim_template_block(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    variant = {
        "variant": "attached_AS", "requested_mutations": ["hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "hide_annotation_categories": {"status": "FAILED", "template_controlled": False},
            "smooth_edges_off": {"status": "APPLIED", "template_controlled": False},
        },
        "image_analysis": {"analysis_status": "not_exported_failed_attestation"},
    }
    native = {"variants": [variant]}
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", native))
    assert record["acceptance_status"] == "FAIL"
    assert "MUTATION_ATTESTATION_FAILED" in record["reason_codes"]
    assert "MUTATION_BLOCKED_BY_TEMPLATE_ONLY" not in record["reason_codes"]


def test_mixed_template_and_unrelated_failure_is_not_a_clean_template_block(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    variant = {
        "variant": "attached_AS", "requested_mutations": ["hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "hide_annotation_categories": {"status": "BLOCKED_BY_TEMPLATE", "template_controlled": True},
            "smooth_edges_off": {"status": "FAILED", "template_controlled": False},
        },
        "image_analysis": {"analysis_status": "not_exported_failed_attestation"},
    }
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", {"variants": [variant]}))
    assert "MUTATION_ATTESTATION_FAILED" in record["reason_codes"]
    assert "MUTATION_BLOCKED_BY_TEMPLATE_ONLY" not in record["reason_codes"]


def test_rollback_variant_evidence_not_overridden_by_stale_recommendation(tmp_path, monkeypatch):
    # Regression lock: `recommendation.recommended_minimum.rollback_status` is a
    # separate, sometimes-stale aggregate the raw probe also reports; the
    # `rollback` check must be derived from the envelope/variant evidence only.
    monkeypatch.setattr(analyzer, "Image", None)
    native = {
        "variants": [{"variant": "attached_AS", "rollback_status": "PASS",
                      "state": {"captured_state_equal_after_rollback": True}}],
        "recommendation": {"recommended_minimum": {"rollback_status": "FAIL"}},
    }
    record, _ = run(tmp_path, envelope("stage_a_minimum_id_mutations", native, rollback_status="succeeded"))
    rollback = next(c for c in record["checks"] if c["check_id"] == "rollback")
    assert rollback["status"] == "PASS"
    assert "ROLLBACK_FAILED" not in record["reason_codes"]


def test_comparison_reference_not_resolved_in_this_report_is_not_applicable(tmp_path, monkeypatch):
    # Stage 1's attached_AS -> detached_AS closure runs as two separate probe
    # invocations; comparison_reference cannot resolve within one report, and
    # that must not be silently ignored nor gate this job's own acceptance -
    # it is a campaign-level closure decision (see campaign_planner.py).
    monkeypatch.setattr(analyzer, "Image", None)
    native = {"variants": [_attached_as_blocked_by_template_variant()]}
    report = envelope("stage_a_minimum_id_mutations", native,
                      requested_settings={"comparison_reference": "detached_AS"})
    record, _ = run(tmp_path, report)
    assert record["comparison_reference"] == "detached_AS"
    resolution = next(c for c in record["checks"] if c["check_id"] == "comparison_reference_resolution")
    assert resolution["status"] == "NOT_APPLICABLE"
    assert resolution["evidence"]["resolved_in_this_report"] is False
    # Not resolvable in-report must not, by itself, force the record inconclusive.
    assert "COMPARISON_REFERENCE_NOT_RESOLVED" not in record["reason_codes"]


def test_comparison_reference_resolved_when_reference_variant_present(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    reference = {"variant": "detached_AS", "requested_mutations": ["smooth_edges_off"],
                 "mutations": {"smooth_edges_off": {"status": "APPLIED"}},
                 "image_analysis": {"actual_dimensions": [2, 2], "path": "raw/detached_AS.tiff", "sha256": "abc"}}
    native = {"variants": [_attached_as_blocked_by_template_variant(), reference]}
    report = envelope("stage_a_minimum_id_mutations", native,
                      requested_settings={"comparison_reference": "detached_AS"})
    record, _ = run(tmp_path, report)
    resolution = next(c for c in record["checks"] if c["check_id"] == "comparison_reference_resolution")
    assert resolution["status"] == "PASS"
    assert resolution["evidence"]["resolved_in_this_report"] is True
    assert resolution["evidence"]["reference_artifact_sha256"] == "abc"


def test_comparison_reference_incomplete_evidence_cannot_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "Image", None)
    reference = {"variant": "detached_AS", "requested_mutations": ["smooth_edges_off"],
                 "mutations": {"smooth_edges_off": {"status": "FAILED"}}, "image_analysis": {}}
    native = {"variants": [_attached_as_blocked_by_template_variant(), reference]}
    report = envelope("stage_a_minimum_id_mutations", native,
                      requested_settings={"comparison_reference": "detached_AS"})
    record, _ = run(tmp_path, report)
    resolution = next(c for c in record["checks"] if c["check_id"] == "comparison_reference_resolution")
    assert resolution["status"] == "INCONCLUSIVE"
    assert record["acceptance_status"] != "PASS"
