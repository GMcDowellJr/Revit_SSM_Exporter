"""Sanitized, committed regression fixture for the Stage 1 mutation-closure
recovery flow.

This replaces an ephemeral, interactive dry-run performed against a real
operator's supplied runtime evidence (never committed - it carried the
operator's real model title and file paths). It reproduces the same shape of
evidence with placeholder identities against the repository's own
`examples/stage_a_campaign.json`, and runs it through the *real* analyzer
(`tools/analyze_stage_a_probe.py`), not a pre-baked acceptance record, so the
proof survives independent of any scratch/session state:

  raw (sanitized) revit_run_manifest.json
    -> tools.analyze_stage_a_probe.analyze_json()      (real analyzer)
    -> tools.campaign_planner.ingest_manifests/ingest_analysis (real planner)
    -> exactly one detached_AS fallback job scheduled, ELIGIBLE
    -> Stage 2 / Stage 7 remain gated
    -> detached_AS resolves the closure -> Stage 2 opens

It also reproduces the drift-supersede-then-recovery path: an older, sanitized
campaign document (no conditional_fallbacks - the shape state files predating
this feature would have) reaches a FAILED attached_AS, then adopting the
corrected `examples/stage_a_campaign.json` is legitimate CONFIGURATION_DRIFT
that supersedes (never deletes) prior evidence.
"""
import copy
import json

import pytest
from PIL import Image

from tools import campaign_planner as p
import tools.analyze_stage_a_probe as analyzer
from tests.dynamo.revit_batch_contract import job_fingerprint
from tests.dynamo.revit_batch_executor import resolve_output_directory

from tests.campaign.test_campaign_planner import EXAMPLE, get


def sanitized_campaign():
    return json.loads(EXAMPLE.read_text())


def pre_fallback_campaign():
    """The shape of campaign.json before conditional_fallbacks existed:
    same Stage 1 job, but s2.* depends directly on s1.attached_as PASS, and
    there is no fallback rule at all - reproducing the exact state shape the
    supplied evidence's campaign_state.json was bound to."""
    c = sanitized_campaign()
    c["conditional_fallbacks"] = []
    for dep in c["dependencies"]:
        if dep.get("requires_closure") == "elevation_mutation_closure":
            dep["requires_job"] = "s1.attached_as"
            del dep["requires_closure"]
    return c


def attached_as_blocked_by_template_envelope(requested_settings):
    """A sanitized raw_result_envelope reproducing the supplied evidence's
    shape: hide_annotation_categories BLOCKED_BY_TEMPLATE, smooth_edges_off
    APPLIED - a clean template-only block, rollback/restoration both clean."""
    variant = {
        "variant": "attached_AS",
        "requested_mutations": ["hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "hide_annotation_categories": {"status": "BLOCKED_BY_TEMPLATE", "template_controlled": True, "requested": True},
            "smooth_edges_off": {"status": "APPLIED", "template_controlled": False, "requested": True},
        },
        "mutation_attestation_passed": False,
        "rollback_status": "PASS",
        "state": {"captured_state_equal_after_rollback": True},
        "image_analysis": {"analysis_status": "not_exported_failed_attestation"},
    }
    native = {"probe": {"name": "stage_a_minimum_id_mutations", "version": "sanitized-fixture.1"},
              "probe_schema_version": "1.0", "variants": [variant]}
    return {
        "probe_id": "stage_a_minimum_id_mutations", "probe_schema_version": "1.0",
        "execution_status": "completed", "rollback_status": "succeeded", "state_restoration_status": "restored",
        "requested_settings": requested_settings, "native_report": native,
        "artifact_paths": ["raw/sanitized/attached_AS.json", "raw/sanitized/attached_AS.csv"],
        "errors": [], "warnings": [], "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:05:00Z",
    }


def detached_as_passed_envelope(requested_settings, tiff_path):
    """`tiff_path` must be an actual, existing image file (relative to where
    the manifest JSON is written) - the analyzer re-derives image_analysis
    from real pixels via analyze_graphics_image(), it does not trust
    pre-supplied dimension/contamination fields on the raw variant."""
    variant = {
        "variant": "detached_AS",
        "requested_mutations": ["detach_template", "hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "detach_template": {"status": "APPLIED", "template_controlled": False, "requested": True},
            "hide_annotation_categories": {"status": "APPLIED", "template_controlled": False, "requested": True},
            "smooth_edges_off": {"status": "APPLIED", "template_controlled": False, "requested": True},
        },
        "mutation_attestation_passed": True,
        "rollback_status": "PASS",
        "state": {"captured_state_equal_after_rollback": True},
        "image_analysis": {"path": tiff_path},
    }
    native = {"probe": {"name": "stage_a_minimum_id_mutations", "version": "sanitized-fixture.1"},
              "probe_schema_version": "1.0", "variants": [variant],
              "assignment_set": {"assigned": {}}}
    return {
        "probe_id": "stage_a_minimum_id_mutations", "probe_schema_version": "1.0",
        "execution_status": "completed", "rollback_status": "succeeded", "state_restoration_status": "restored",
        "requested_settings": requested_settings, "native_report": native,
        "artifact_paths": ["raw/sanitized/detached_AS.tiff"],
        "errors": [], "warnings": [], "started_at": "2026-01-01T01:00:00Z", "completed_at": "2026-01-01T01:05:00Z",
    }


def _manifest_for(campaign_id, batch, job_index, run_id, envelope):
    batch_job = batch["jobs"][job_index]
    return {
        "schema_version": "1.0", "campaign_id": campaign_id, "batch_id": batch["batch_id"], "run_id": run_id,
        "document_identity": {"title": "SANITIZED_MODEL", "path": "SANITIZED_PATH", "revit_version": "2025"},
        "environment": {"revit_build": "sanitized", "dynamo_host": "sanitized"},
        "batch_source": "sanitized-fixture", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:10:00Z",
        "execution_status": "completed", "validation_only": False,
        "jobs": [{
            "job_id": batch_job["job_id"], "probe_id": batch_job["probe_id"],
            "requested_view_identity": dict(batch_job["view"]), "resolved_view_identity": dict(batch_job["view"]),
            "requested_settings": dict(batch_job["settings"]),
            "configuration_fingerprint": job_fingerprint(batch_job),
            "execution_status": "completed", "raw_result_envelope": envelope,
            "artifact_paths": envelope["artifact_paths"],
            "rollback_status": envelope["rollback_status"], "state_restoration_status": envelope["state_restoration_status"],
            "errors": [], "warnings": [], "started_at": envelope["started_at"], "completed_at": envelope["completed_at"],
        }],
        "jobs_not_attempted": [], "errors": [], "warnings": [],
    }


def test_template_blocked_attached_as_schedules_exactly_one_detached_as_fallback(tmp_path):
    campaign = p.validate_campaign(sanitized_campaign())
    state = p.initialize_state(campaign)
    eligible = [j["job_key"] for j in state["jobs"].values() if j["status"] == "ELIGIBLE"]
    assert eligible == ["s1.attached_as"]

    batch = p.generate_next_batch(campaign, state)
    attached_job = get(state, "s1.attached_as")

    manifest_path = tmp_path / "revit_run_manifest.json"
    manifest = _manifest_for(campaign["campaign_id"], batch, 0, "sanitized-run-0001",
                             attached_as_blocked_by_template_envelope(dict(batch["jobs"][0]["settings"])))
    manifest_path.write_text(json.dumps(manifest))

    p.ingest_manifests(campaign, state, [manifest])
    assert attached_job["status"] == "EXECUTED"

    # Run the REAL analyzer against the sanitized raw manifest - not a
    # pre-baked acceptance record.
    out_path, _ = analyzer.analyze_json(manifest_path)
    analyzed = json.loads(out_path.read_text())
    record = analyzed["acceptance_records"][0]
    assert "MUTATION_BLOCKED_BY_TEMPLATE_ONLY" in record["reason_codes"]
    assert record["comparison_reference"] == "detached_AS"
    tiffs = next(c for c in record["checks"] if c["check_id"] == "required_tiffs")
    assert tiffs["status"] == "NOT_APPLICABLE"

    p.ingest_analysis(campaign, state, [record])
    assert attached_job["status"] == "FAILED"

    fallback_jobs = [j for j in state["jobs"].values() if j["job_key"] == "s1.detached_as"]
    assert len(fallback_jobs) == 1
    fallback = fallback_jobs[0]
    assert fallback["status"] == "ELIGIBLE"
    assert fallback["provenance"]["fallback_of_job_id"] == attached_job["job_id"]
    assert fallback["provenance"]["trigger_reason_codes_matched"] == ["MUTATION_BLOCKED_BY_TEMPLATE_ONLY"]

    # Nothing else opened up: attached_AS is not rerun, and every downstream
    # job gated on the elevation closure - elevation determinism as well as
    # the cross-view alignment/resolution confirmation matrix - remains
    # gated.
    next_batch = p.generate_next_batch(campaign, state)
    assert [j["job_id"] for j in next_batch["jobs"]] == [fallback["job_id"]]
    assert get(state, "s2.align.r1")["status"] == "PLANNED"
    assert get(state, "s2.align.r2")["status"] == "PLANNED"
    assert get(state, "s2.floor_active.confirm")["status"] == "PLANNED"
    assert get(state, "s2.callout.confirm")["status"] == "PLANNED"

    resolved = resolve_output_directory(next_batch["jobs"][0]["output_directory"], str(manifest_path))
    assert resolved.endswith(fallback["job_id"])
    import os
    assert os.path.isabs(resolved)


def test_detached_as_resolution_unlocks_stage2_and_not_others(tmp_path):
    campaign = p.validate_campaign(sanitized_campaign())
    state = p.initialize_state(campaign)
    batch1 = p.generate_next_batch(campaign, state)
    attached_job = get(state, "s1.attached_as")
    m1 = tmp_path / "m1.json"
    manifest1 = _manifest_for(campaign["campaign_id"], batch1, 0, "sanitized-run-0001",
                              attached_as_blocked_by_template_envelope(dict(batch1["jobs"][0]["settings"])))
    m1.write_text(json.dumps(manifest1))
    p.ingest_manifests(campaign, state, [manifest1])
    out1, _ = analyzer.analyze_json(m1)
    record1 = json.loads(out1.read_text())["acceptance_records"][0]
    p.ingest_analysis(campaign, state, [record1])

    fallback = next(j for j in state["jobs"].values() if j["job_key"] == "s1.detached_as")
    batch2 = p.generate_next_batch(campaign, state)
    assert [j["job_id"] for j in batch2["jobs"]] == [fallback["job_id"]]
    m2 = tmp_path / "m2.json"
    tiff_dir = tmp_path / "raw" / "sanitized"; tiff_dir.mkdir(parents=True)
    Image.new("RGB", (4, 4), (255, 255, 255)).save(tiff_dir / "detached_AS.tiff")
    manifest2 = _manifest_for(campaign["campaign_id"], batch2, 0, "sanitized-run-0002",
                              detached_as_passed_envelope(dict(batch2["jobs"][0]["settings"]), "raw/sanitized/detached_AS.tiff"))
    m2.write_text(json.dumps(manifest2))
    p.ingest_manifests(campaign, state, [manifest2])
    out2, _ = analyzer.analyze_json(m2)
    record2 = json.loads(out2.read_text())["acceptance_records"][0]
    p.ingest_analysis(campaign, state, [record2])

    # A raw color-comparison probe alone cannot attest semantic preservation
    # (this family never supplies rendered_semantic_preservation) - a clean
    # mutation attestation + clean pixel evidence still lands INCONCLUSIVE on
    # that one gate-scoped reason, and only an authorized manual review
    # resolves it, exercising the gate-scoped-review mechanism end to end.
    assert fallback["status"] == "INCONCLUSIVE"
    requirement = state["manual_review_requirements"][fallback["job_id"]]
    assert requirement["gate"] == "rendered_semantic_preservation"
    p.record_manual_review(campaign, state, fallback["job_id"], "ACCEPTED", "sanitized-reviewer")
    assert fallback["status"] == "PASSED"

    closure = state["closures"]["elevation_mutation_closure"]
    assert closure["status"] == "RESOLVED_FALLBACK_PASS"
    assert closure["candidate_job_id"] == fallback["job_id"]

    assert get(state, "s2.align.r1")["status"] == "ELIGIBLE"
    assert get(state, "s2.align.r2")["status"] == "ELIGIBLE"
    # The cross-view alignment/resolution confirmation matrix opens up too
    # (it is gated on the same closure, not on the elevation determinism
    # jobs specifically) - but the cross-view host raster matrix behind it
    # still has not been unlocked, since its own per-view confirmation job
    # has not run yet.
    assert get(state, "s2.floor_active.confirm")["status"] == "ELIGIBLE"
    assert get(state, "s3.floor_active.attached_AS")["status"] == "PLANNED"


def test_drift_supersede_then_recovery_preserves_evidence_linkage(tmp_path):
    """Reproduces the supplied evidence's exact recovery shape with sanitized
    data: an older campaign (no conditional_fallbacks) reaches a FAILED
    attached_AS; adopting the corrected campaign is legitimate
    CONFIGURATION_DRIFT that supersedes without discarding evidence, and a
    freshly-initialized state bound to the corrected campaign gets the exact
    same job_id for s1.attached_as (identity is unaffected by adding an
    unrelated conditional_fallbacks rule elsewhere in the document)."""
    old_campaign = p.validate_campaign(pre_fallback_campaign())
    old_state = p.initialize_state(old_campaign)
    batch = p.generate_next_batch(old_campaign, old_state)
    attached_job = get(old_state, "s1.attached_as")
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest_for(old_campaign["campaign_id"], batch, 0, "sanitized-run-0001",
                             attached_as_blocked_by_template_envelope(dict(batch["jobs"][0]["settings"])))
    manifest_path.write_text(json.dumps(manifest))
    p.ingest_manifests(old_campaign, old_state, [manifest])
    out, _ = analyzer.analyze_json(manifest_path)
    record = json.loads(out.read_text())["acceptance_records"][0]
    p.ingest_analysis(old_campaign, old_state, [record])
    assert attached_job["status"] == "FAILED"
    run_ids, analysis_record_ids = list(attached_job["run_ids"]), list(attached_job["analysis_record_ids"])
    assert run_ids and analysis_record_ids

    # Adopt the corrected campaign against the OLD state: must fail as
    # ConfigurationDriftError, superseding (never discarding) every job.
    corrected_campaign = p.validate_campaign(sanitized_campaign())
    with pytest.raises(p.ConfigurationDriftError):
        p.generate_next_batch(corrected_campaign, old_state)
    assert all(j["status"] == "SUPERSEDED" for j in old_state["jobs"].values())
    superseded_attached = old_state["jobs"][attached_job["job_id"]]
    assert superseded_attached["run_ids"] == run_ids
    assert superseded_attached["analysis_record_ids"] == analysis_record_ids

    # Fresh init against the corrected campaign: same job_id, same
    # execution-identity fingerprint for s1.attached_as as the old campaign
    # produced (identity fields for this job are untouched by adding
    # conditional_fallbacks elsewhere), so the preserved manifest replays
    # cleanly without needing to re-execute in Revit.
    new_state = p.initialize_state(corrected_campaign)
    assert attached_job["job_id"] in new_state["jobs"]
    new_batch = p.generate_next_batch(corrected_campaign, new_state)
    assert new_batch["jobs"][0]["job_id"] == attached_job["job_id"]
    replay_fingerprint = new_state["jobs"][attached_job["job_id"]]["execution_fingerprints"][new_batch["batch_id"]]
    assert replay_fingerprint == manifest["jobs"][0]["configuration_fingerprint"]

    p.ingest_manifests(corrected_campaign, new_state, [manifest])
    assert new_state["jobs"][attached_job["job_id"]]["status"] == "EXECUTED"
    p.ingest_analysis(corrected_campaign, new_state, [record])
    assert new_state["jobs"][attached_job["job_id"]]["status"] == "FAILED"
    fallback = next(j for j in new_state["jobs"].values() if j["job_key"] == "s1.detached_as")
    assert fallback["status"] == "ELIGIBLE"
