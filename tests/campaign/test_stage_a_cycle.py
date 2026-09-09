import copy
import json
import os
import time
from pathlib import Path

import pytest

from tools import campaign_planner as p
from tools import stage_a_cycle as cycle
from tests.campaign.test_campaign_planner import compact, example, get

# --------------------------------------------------------------------------
# Fakes and helpers. Per the task's own instruction ("Use fakes and compact
# synthetic evidence. Do not require Revit for wrapper tests.") most tests
# monkeypatch tools.analyze_stage_a_probe.analyze_json with a fast, fully
# deterministic fake that honors the exact same file contract (read the
# manifest, write a sibling `<stem>.analyzed.json` with one acceptance record
# per envelope-bearing job) without requiring Pillow/NumPy or real TIFFs. A
# handful of tests call the REAL analyzer end-to-end (see the "real analyzer"
# section) to prove the wrapper is actually wired to it, not just to the fake.
# --------------------------------------------------------------------------


def install_fake_analyzer(monkeypatch, calls=None):
    def _fake(json_path):
        json_path = Path(json_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if calls is not None:
            calls.append(str(json_path))
        records = []
        for job in data.get("jobs", []):
            envelope = job.get("raw_result_envelope")
            if not isinstance(envelope, dict):
                continue
            records.append({
                "analysis_schema_version": "1.0",
                "campaign_id": data.get("campaign_id"), "batch_id": data.get("batch_id"),
                "run_id": data.get("run_id"), "job_id": job.get("job_id"),
                "probe_id": job.get("probe_id"), "probe_schema_version": "1.0",
                "analyzer_version": "fake-test",
                "source_report": {"path": str(json_path), "sha256": "fake"},
                "artifact_references": envelope.get("artifact_paths", []),
                "analysis_status": "COMPLETED",
                "acceptance_status": envelope.get("_test_acceptance_status", "PASS"),
                "execution_status": job.get("execution_status"),
                "reason_codes": envelope.get("_test_reason_codes", []),
                "checks": [], "limitations": [], "errors": [], "warnings": [],
                "analyzed_at": "2024-01-01T00:00:00Z",
            })
        out = json_path.with_name(json_path.stem + ".analyzed.json")
        out.write_text(json.dumps({"analysis_schema_version": "1.0", "source_manifest": str(json_path),
                                   "acceptance_records": records}, indent=2, sort_keys=True), encoding="utf-8")
        return out, "fake analyzed {0} job(s)".format(len(records))
    monkeypatch.setattr(cycle.analyzer, "analyze_json", _fake)


def raising_analyzer(monkeypatch):
    def _fake(json_path):
        raise ValueError("UNKNOWN_PROBE_TYPE: simulated analyzer failure")
    monkeypatch.setattr(cycle.analyzer, "analyze_json", _fake)


def init(tmp_path, campaign):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    campaign_path = campaign_dir / "campaign.json"
    state_path = campaign_dir / "campaign_state.json"
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    state = p.initialize_state(campaign)
    p.atomic_write(state_path, state)
    return campaign_path, state_path


def read_state(state_path):
    return json.loads(state_path.read_text(encoding="utf-8"))


def job_manifest_entry(job_id, batch_id, state, probe_id, status="completed",
                       acceptance_status="PASS", reason_codes=None, envelope=True):
    fingerprint = state["jobs"][job_id]["execution_fingerprints"][batch_id]
    raw_envelope = None
    if envelope and status in ("completed", "inconclusive", "failed"):
        raw_envelope = {"artifact_paths": ["raw/{0}.tiff".format(job_id)],
                        "_test_acceptance_status": acceptance_status,
                        "_test_reason_codes": reason_codes or []}
    return {"job_id": job_id, "probe_id": probe_id, "requested_view_identity": {}, "resolved_view_identity": {},
            "requested_settings": {}, "configuration_fingerprint": fingerprint,
            "output_directory": "raw/{0}".format(job_id), "output_directory_resolved": "/raw/{0}".format(job_id),
            "execution_status": status, "raw_result_envelope": raw_envelope, "artifact_paths": [],
            "rollback_status": "succeeded", "state_restoration_status": "restored",
            "errors": [], "warnings": [], "started_at": "x", "completed_at": "y"}


def write_manifest(manifest_root, campaign_id, batch_id, run_id, jobs, execution_status="completed",
                   started_at="2024-01-01T00:00:00Z", validation_only=False, campaign_id_override=None):
    manifest = {"schema_version": "1.0", "campaign_id": campaign_id_override or campaign_id,
                "batch_id": batch_id, "run_id": run_id,
                "document_identity": {"title": "doc", "path": None, "revit_version": None},
                "environment": {"python": "3.11"}, "batch_source": "batch.json",
                "started_at": started_at, "completed_at": "2024-01-01T00:05:00Z",
                "execution_status": execution_status, "validation_only": validation_only,
                "jobs": jobs, "jobs_not_attempted": [], "errors": [], "warnings": []}
    directory = manifest_root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "revit_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory / "revit_run_manifest.json"


def run_job_to_completion(monkeypatch, tmp_path, campaign_path, state_path, job_key, acceptance="PASS",
                          reason_codes=None, run_id=None):
    """Advance once to batch a job, write its manifest, advance again to
    ingest+analyze+ingest-acceptance. Returns the final advance() result."""
    install_fake_analyzer(monkeypatch)
    r1 = cycle.advance(str(campaign_path), str(state_path))
    assert r1["action"] == "RUN_DYNAMO"
    state = read_state(state_path)
    job = get(state, job_key)
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], run_id or job["job_id"],
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"],
                                       acceptance_status=acceptance, reason_codes=reason_codes)])
    return cycle.advance(str(campaign_path), str(state_path))


# --------------------------------------------------------------------------
# 1-3: basic batch lifecycle
# --------------------------------------------------------------------------

def test_first_invocation_generates_initial_batch(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "RUN_DYNAMO"
    assert result["batch_id"] is not None
    assert len(result["job_ids"]) == 2
    assert Path(result["batch_path"]).exists()
    assert (state_path.parent / "batches" / (result["batch_id"] + ".json")).exists()


def test_pending_batch_returns_run_dynamo_without_generating_new_batch(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    first = cycle.advance(str(campaign_path), str(state_path))
    second = cycle.advance(str(campaign_path), str(state_path))
    assert second["action"] == "RUN_DYNAMO"
    assert second["batch_id"] == first["batch_id"]
    assert second["job_ids"] == first["job_ids"]


def test_repeated_invocation_before_dynamo_is_a_no_op(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    cycle.advance(str(campaign_path), str(state_path))
    before = state_path.read_text(encoding="utf-8")
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["manifests_ingested"] == 0
    assert result["analyses_created"] == 0
    assert result["analyses_ingested"] == 0
    assert state_path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# 4-6: manifest discovery/classification
# --------------------------------------------------------------------------

def test_validation_only_manifests_are_ignored(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "validation-run",
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"], status="validated_only")],
                   execution_status="validation_only", validation_only=True)
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["validation_manifests_ignored"] == 1
    assert result["manifests_ingested"] == 0
    assert get(read_state(state_path), "align")["status"] == "BATCHED"


def test_unrelated_campaign_manifests_are_ignored(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, "some-other-campaign", r1["batch_id"], "other-run",
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"])],
                   campaign_id_override="some-other-campaign")
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["manifests_ingested"] == 0
    assert result["warnings"]
    assert get(read_state(state_path), "align")["status"] == "BATCHED"


def test_unknown_job_manifest_surfaces_as_conflict(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    manifest_root = state_path.parent / "manifests"
    bogus_job = {"job_id": "not-a-real-job-id", "probe_id": "stage_a_image_alignment",
                "requested_view_identity": {}, "resolved_view_identity": {}, "requested_settings": {},
                "configuration_fingerprint": "0" * 64, "output_directory": "raw/bogus",
                "output_directory_resolved": "/raw/bogus", "execution_status": "completed",
                "raw_result_envelope": {"artifact_paths": [], "_test_acceptance_status": "PASS", "_test_reason_codes": []},
                "artifact_paths": [], "rollback_status": "succeeded", "state_restoration_status": "restored",
                "errors": [], "warnings": [], "started_at": "x", "completed_at": "y"}
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "bogus-run", [bogus_job])
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "CONFLICT"
    assert any(c["code"] == "UNKNOWN_JOB" for c in result["conflicts"])
    # A second call re-surfaces the same unresolved conflict rather than
    # silently retrying past it or crashing.
    second = cycle.advance(str(campaign_path), str(state_path))
    assert second["action"] == "CONFLICT"


# --------------------------------------------------------------------------
# 7-9: ingestion / analysis / acceptance, each exactly once
# --------------------------------------------------------------------------

def test_valid_completed_manifest_is_ingested(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], job["job_id"],
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"])])
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["manifests_ingested"] == 1
    assert get(read_state(state_path), "align")["status"] == "PASSED"


def test_outstanding_raw_evidence_is_analyzed_once(tmp_path, monkeypatch):
    calls = []
    install_fake_analyzer(monkeypatch, calls)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], job["job_id"],
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"])])
    r2 = cycle.advance(str(campaign_path), str(state_path))
    assert r2["analyses_created"] == 1
    assert len(calls) == 1
    r3 = cycle.advance(str(campaign_path), str(state_path))
    assert r3["analyses_created"] == 0
    assert len(calls) == 1  # never re-invoked for already-analyzed evidence


def test_normalized_acceptance_is_ingested_once(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], job["job_id"],
                   [job_manifest_entry(job["job_id"], r1["batch_id"], state, job["probe_id"])])
    r2 = cycle.advance(str(campaign_path), str(state_path))
    assert r2["analyses_ingested"] == 1
    r3 = cycle.advance(str(campaign_path), str(state_path))
    assert r3["analyses_ingested"] == 0


# --------------------------------------------------------------------------
# 10: full idempotence across a longer sequence
# --------------------------------------------------------------------------

def test_repeating_the_command_is_fully_idempotent(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    align = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], align["job_id"],
                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])])
    cycle.advance(str(campaign_path), str(state_path))
    stable = state_path.read_text(encoding="utf-8")
    for _ in range(3):
        result = cycle.advance(str(campaign_path), str(state_path))
        assert state_path.read_text(encoding="utf-8") == stable
        assert result["manifests_ingested"] == 0
        assert result["analyses_created"] == 0
        assert result["analyses_ingested"] == 0


# --------------------------------------------------------------------------
# 11-12: deterministic manifest processing order
# --------------------------------------------------------------------------

def test_discovery_orders_by_recorded_time_not_file_mtime(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    c = compact()
    c["execution_defaults"]["batch_size"] = 1
    campaign_path, state_path = init(tmp_path, c)
    manifest_root = state_path.parent / "manifests"
    r1 = cycle.advance(str(campaign_path), str(state_path))  # batches "align" only (batch_size=1)
    state = read_state(state_path)
    align = get(state, "align")
    older = write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "run-newer-file-older-time",
                           [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])],
                           started_at="2024-01-01T00:00:00Z")
    time.sleep(0.05)
    newer_mtime_but_older_content = write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "run-older-file-newer-time",
                                                    [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])],
                                                    started_at="2023-01-01T00:00:00Z")
    # The file with the *later* mtime records the *earlier* started_at; make
    # this unambiguous by reversing filesystem write order relative to the
    # embedded identity, then confirm discovery/ordering follows started_at.
    discovery = cycle._discover_manifests(manifest_root, state["campaign_id"])
    ordered_run_ids = [c["run_id"] for c in discovery["candidates"]]
    assert ordered_run_ids == ["run-older-file-newer-time", "run-newer-file-older-time"]


def test_multiple_partial_run_manifests_processed_deterministically(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    c = compact()
    campaign_path, state_path = init(tmp_path, c)
    r1 = cycle.advance(str(campaign_path), str(state_path))
    assert len(r1["job_ids"]) == 2
    state = read_state(state_path)
    align, independent = get(state, "align"), get(state, "independent")
    manifest_root = state_path.parent / "manifests"
    # Two separate Dynamo invocations against the same batch (a legitimate
    # partial batch spanning multiple runs), each with its own manifest.
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "partial-run-a",
                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])],
                   started_at="2024-01-01T00:00:00Z")
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "partial-run-b",
                   [job_manifest_entry(independent["job_id"], r1["batch_id"], state, independent["probe_id"])],
                   started_at="2024-01-01T00:01:00Z")
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["manifests_ingested"] == 2
    final = read_state(state_path)
    assert get(final, "align")["status"] == "PASSED"
    assert get(final, "independent")["status"] == "PASSED"


# --------------------------------------------------------------------------
# 13: conflicting manifests stop the cycle
# --------------------------------------------------------------------------

def test_conflicting_manifests_stop_the_cycle(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    align = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], "dup-run",
                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])])
    # A second, differently-content manifest claiming the SAME run_id: create
    # it by writing directly into the same run directory won't collide (one
    # file per dir), so instead simulate this via two manifests recorded with
    # identical run_id but different environments through direct planner call
    # semantics (RUN_CONFLICT), matching campaign_planner's own contract.
    conflicting_dir = manifest_root / "dup-run-conflict"
    conflicting_dir.mkdir()
    manifest = json.loads((manifest_root / "dup-run" / "revit_run_manifest.json").read_text())
    manifest["run_id"] = "dup-run"
    manifest["environment"] = {"different": True}
    (conflicting_dir / "revit_run_manifest.json").write_text(json.dumps(manifest))
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "CONFLICT"
    assert any(c["code"] == "RUN_CONFLICT" for c in result["conflicts"])


# --------------------------------------------------------------------------
# 14-16: analyzer failure / crash-resume granularity
# --------------------------------------------------------------------------

def test_analyzer_failure_leaves_state_resumable(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    align = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], align["job_id"],
                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])])
    raising_analyzer(monkeypatch)
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "ERROR"
    assert result["errors"][0]["code"] == "ANALYZER_INVOCATION_FAILED"
    resumed_state = read_state(state_path)
    assert get(resumed_state, "align")["status"] == "EXECUTED"  # run ingestion survived
    # Fix the analyzer and confirm the same command now proceeds normally.
    install_fake_analyzer(monkeypatch)
    result2 = cycle.advance(str(campaign_path), str(state_path))
    assert result2["action"] in ("RUN_DYNAMO", "CAMPAIGN_COMPLETE")
    assert get(read_state(state_path), "align")["status"] == "PASSED"


def test_crash_after_run_ingestion_resumes_at_analysis(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    align = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], align["job_id"],
                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])])
    # Simulate "run ingestion happened, then the process died" by directly
    # driving the planner's own ingestion (exactly what phase 3 does) and
    # persisting - without ever reaching analysis.
    campaign = p.validate_campaign(p.load_json(campaign_path))
    working = p.migrate_state(p.load_json(state_path))
    manifest_data = json.loads((manifest_root / align["job_id"] / "revit_run_manifest.json").read_text())
    p.ingest_manifests(campaign, working, [manifest_data])
    p.atomic_write(state_path, working)
    assert get(read_state(state_path), "align")["status"] == "EXECUTED"
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["analyses_created"] == 1
    assert get(read_state(state_path), "align")["status"] == "PASSED"


def test_crash_after_analysis_write_resumes_at_analysis_ingestion(tmp_path, monkeypatch):
    calls = []
    install_fake_analyzer(monkeypatch, calls)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    align = get(state, "align")
    manifest_root = state_path.parent / "manifests"
    manifest_path = write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], align["job_id"],
                                   [job_manifest_entry(align["job_id"], r1["batch_id"], state, align["probe_id"])])
    # Ingest the run for real (as phase 3 would), then pre-create the
    # analyzed.json exactly as the analyzer would - simulating a crash that
    # happened between analyze_json() returning and ingest_analysis() running.
    campaign = p.validate_campaign(p.load_json(campaign_path))
    working = p.migrate_state(p.load_json(state_path))
    p.ingest_manifests(campaign, working, [json.loads(manifest_path.read_text())])
    p.atomic_write(state_path, working)
    cycle.analyzer.analyze_json(manifest_path)  # real fake call, pre-creates the sibling file
    calls.clear()
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["analyses_created"] == 0  # reused the existing analyzed.json, never re-invoked
    assert len(calls) == 0
    assert result["analyses_ingested"] == 1
    assert get(read_state(state_path), "align")["status"] == "PASSED"


# --------------------------------------------------------------------------
# 17-18: batch snapshot / publication crash-resume
# --------------------------------------------------------------------------

def test_crash_after_batch_snapshot_resumes_without_generating_another_batch_id(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    c = compact()
    campaign_path, state_path = init(tmp_path, c)
    campaign = p.validate_campaign(p.load_json(campaign_path))
    working = p.migrate_state(p.load_json(state_path))
    batch = p.generate_next_batch(campaign, working)
    p.atomic_write(state_path, working)  # state persisted with jobs BATCHED...
    # ...but the immutable snapshot file was never written (simulated crash).
    batches_root = state_path.parent / "batches"
    assert not (batches_root / (batch["batch_id"] + ".json")).exists()
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "RUN_DYNAMO"
    assert result["batch_id"] == batch["batch_id"]
    assert (batches_root / (batch["batch_id"] + ".json")).exists()
    assert Path(result["batch_path"]).exists()
    # No second batch_id was ever minted for this still-pending work.
    all_batch_ids = {b for j in read_state(state_path)["jobs"].values() for b in j["batch_ids"]}
    assert all_batch_ids == {batch["batch_id"]}


def test_existing_pending_next_batch_mismatch_is_detected(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    batch_path = Path(r1["batch_path"])
    tampered = json.loads(batch_path.read_text())
    tampered["jobs"][0]["job_configuration_fingerprint"] = "0" * 64
    batch_path.write_text(json.dumps(tampered))
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "ERROR"
    assert result["errors"][0]["code"] == "NEXT_BATCH_MISMATCH"


# --------------------------------------------------------------------------
# 19-20: conditional fallback, via the REAL analyzer (not the fake)
# --------------------------------------------------------------------------

def _real_analyzer_envelope(blocked_by_template):
    variant = {
        "variant": "attached_AS", "requested_mutations": ["hide_annotation_categories", "smooth_edges_off"],
        "mutations": {
            "hide_annotation_categories": {"status": "BLOCKED_BY_TEMPLATE" if blocked_by_template else "FAILED",
                                           "template_controlled": blocked_by_template},
            "smooth_edges_off": {"status": "APPLIED", "template_controlled": False},
        },
        "mutation_attestation_passed": False,
        "image_analysis": {"analysis_status": "not_exported_failed_attestation"},
    }
    return {"probe_id": "stage_a_minimum_id_mutations", "probe_schema_version": "1.0",
            "execution_status": "completed", "rollback_status": "succeeded", "state_restoration_status": "restored",
            "requested_settings": {}, "artifact_paths": [], "errors": [], "warnings": [],
            "native_report": {"variants": [variant]}}


def _run_attached_as_with_real_analyzer(tmp_path, blocked_by_template):
    campaign_path, state_path = init(tmp_path, example())
    r1 = cycle.advance(str(campaign_path), str(state_path))
    assert r1["action"] == "RUN_DYNAMO"
    assert r1["job_ids"] == [get(read_state(state_path), "s1.attached_as")["job_id"]]
    state = read_state(state_path)
    job = get(state, "s1.attached_as")
    manifest_root = state_path.parent / "manifests"
    manifest_entry = {"job_id": job["job_id"], "probe_id": job["probe_id"], "requested_view_identity": {},
                      "resolved_view_identity": {}, "requested_settings": {},
                      "configuration_fingerprint": job["execution_fingerprints"][r1["batch_id"]],
                      "output_directory": "raw/x", "output_directory_resolved": "/raw/x",
                      "execution_status": "completed", "raw_result_envelope": _real_analyzer_envelope(blocked_by_template),
                      "artifact_paths": [], "rollback_status": "succeeded", "state_restoration_status": "restored",
                      "errors": [], "warnings": [], "started_at": "x", "completed_at": "y"}
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], job["job_id"], [manifest_entry])
    return campaign_path, state_path, cycle.advance(str(campaign_path), str(state_path))


def test_conditional_fallback_produces_exactly_one_fallback_batch(tmp_path):
    campaign_path, state_path, result = _run_attached_as_with_real_analyzer(tmp_path, blocked_by_template=True)
    state = read_state(state_path)
    assert state["closures"]["elevation_mutation_closure"]["status"] == "AWAITING_FALLBACK_EXECUTION"
    fallback = next(j for j in state["jobs"].values() if j["job_key"] == "s1.detached_as")
    # By the time advance() returns RUN_DYNAMO, it has already generated and
    # batched the fallback job in this same call - not merely unlocked it.
    assert fallback["status"] == "BATCHED"
    assert result["action"] == "RUN_DYNAMO"
    assert result["job_ids"] == [fallback["job_id"]]
    assert result["fallback_context"] and result["fallback_context"][0]["job_id"] == fallback["job_id"]
    # Exactly one fallback batch: repeating does not mint another.
    second = cycle.advance(str(campaign_path), str(state_path))
    assert second["batch_id"] == result["batch_id"]
    assert len([b for b in (state_path.parent / "batches").glob("*.json")]) == 2  # primary batch + fallback batch


def test_unrelated_attached_as_failure_does_not_trigger_fallback(tmp_path):
    campaign_path, state_path, result = _run_attached_as_with_real_analyzer(tmp_path, blocked_by_template=False)
    state = read_state(state_path)
    assert state["closures"]["elevation_mutation_closure"]["status"] == "RESOLVED_FAILED"
    assert not any(j["job_key"] == "s1.detached_as" for j in state["jobs"].values())
    assert state["stage_status"]["02_elevation_alignment"] == "BLOCKED"
    # No fallback job means no fallback annotation, whatever the top-level
    # action turns out to be (the campaign's Stage 7 is explicitly configured
    # to proceed independently of this failure - see the dedicated Stage 7
    # test below - so RUN_DYNAMO for that unrelated work is expected here).
    assert not result.get("fallback_context")


# --------------------------------------------------------------------------
# 21-22: manual review and diagnostics remain explicit stops
# --------------------------------------------------------------------------

def test_manual_review_stops_automatic_advancement(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    c = compact()
    # Drop the "independent" sibling job (it has no dependency on "align" and
    # would keep generating unrelated eligible batches, masking the
    # MANUAL_REVIEW_REQUIRED outcome this test is checking for).
    c["stages"][1]["jobs"] = [j for j in c["stages"][1]["jobs"] if j["job_key"] != "independent"]
    c["stages"][0]["jobs"][0]["manual_review_required"] = True
    campaign_path, state_path = init(tmp_path, c)
    result = run_job_to_completion(monkeypatch, tmp_path, campaign_path, state_path, "align",
                                   acceptance="INCONCLUSIVE", reason_codes=["MANUAL_SEMANTIC_REVIEW_REQUIRED"])
    assert result["action"] == "MANUAL_REVIEW_REQUIRED"
    state = read_state(state_path)
    assert get(state, "align")["status"] == "INCONCLUSIVE"
    assert get(state, "mutate")["status"] == "BLOCKED"
    # The wrapper never records a review outcome itself.
    assert state["manual_review_requirements"][get(state, "align")["job_id"]]["status"] == "PENDING"
    again = cycle.advance(str(campaign_path), str(state_path))
    assert again["action"] == "MANUAL_REVIEW_REQUIRED"


def test_unauthorized_diagnostics_stop_automatic_advancement(tmp_path, monkeypatch):
    c = compact()
    c["stages"][1]["jobs"] = [j for j in c["stages"][1]["jobs"] if j["job_key"] != "independent"]
    campaign_path, state_path = init(tmp_path, c)
    result = run_job_to_completion(monkeypatch, tmp_path, campaign_path, state_path, "align", acceptance="FAIL",
                                   reason_codes=["MUTATION_ATTESTATION_FAILED"])
    assert result["action"] == "DIAGNOSTIC_AUTHORIZATION_REQUIRED"
    state = read_state(state_path)
    align = get(state, "align")
    assert align["status"] == "FAILED"  # never auto-moved to NEEDS_DIAGNOSTIC
    assert align["job_id"] not in state["diagnostic_requests"]
    assert not any(j["job_key"].startswith("diagnostic.") for j in state["jobs"].values())
    assert align["job_id"] in result["job_ids"]


# --------------------------------------------------------------------------
# 23-24: stage cascading and completion pass through the planner verbatim
# --------------------------------------------------------------------------

def test_global_prerequisite_failure_does_not_expose_unrelated_stage7_work(tmp_path):
    campaign_path, state_path = init(tmp_path, example())
    envelope = {"probe_id": "stage_a_minimum_id_mutations", "probe_schema_version": "1.0",
                "execution_status": "completed", "rollback_status": "succeeded", "state_restoration_status": "restored",
                "requested_settings": {}, "artifact_paths": [], "errors": [], "warnings": [],
                "native_report": {"variants": [{
                    "variant": "attached_AS", "requested_mutations": ["hide_annotation_categories"],
                    "mutations": {"hide_annotation_categories": {"status": "FAILED", "template_controlled": False}},
                    "mutation_attestation_passed": False, "image_analysis": {"analysis_status": "error"}}]}}
    r1 = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    job = get(state, "s1.attached_as")
    manifest_root = state_path.parent / "manifests"
    entry = {"job_id": job["job_id"], "probe_id": job["probe_id"], "requested_view_identity": {},
            "resolved_view_identity": {}, "requested_settings": {},
            "configuration_fingerprint": job["execution_fingerprints"][r1["batch_id"]],
            "output_directory": "raw/x", "output_directory_resolved": "/raw/x", "execution_status": "completed",
            "raw_result_envelope": envelope, "artifact_paths": [], "rollback_status": "succeeded",
            "state_restoration_status": "restored", "errors": [], "warnings": [], "started_at": "x", "completed_at": "y"}
    write_manifest(manifest_root, state["campaign_id"], r1["batch_id"], job["job_id"], [entry])
    result = cycle.advance(str(campaign_path), str(state_path))
    state = read_state(state_path)
    for stage_id in ("02_elevation_alignment", "03_elevation_linework", "04_alignment_matrix",
                     "05_reduced_mutation", "06_linework_validation"):
        assert state["stage_status"][stage_id] == "BLOCKED"
    # Stage 7 is explicitly configured to proceed once the independent
    # matrix has *concluded* (pass, fail, or blocked) - this failure must
    # unlock exactly that pre-authorized work, nothing more and nothing less.
    assert result["action"] == "RUN_DYNAMO"
    assert set(result["job_ids"]) == {get(state, "s7.rvt_link.fixed")["job_id"], get(state, "s7.dwg.fixed")["job_id"]}
    assert get(state, "s7.rvt_link.150")["status"] == "PLANNED"


def test_campaign_completion_is_reported_correctly(tmp_path, monkeypatch):
    c = compact()
    c["stages"] = c["stages"][:1]
    c["dependencies"] = []
    campaign_path, state_path = init(tmp_path, c)
    result = run_job_to_completion(monkeypatch, tmp_path, campaign_path, state_path, "align", acceptance="PASS")
    assert result["action"] == "CAMPAIGN_COMPLETE"
    assert cycle.EXIT_CODES[result["action"]] == 0


# --------------------------------------------------------------------------
# 25-26: drift and pre-existing conflicts stop safely
# --------------------------------------------------------------------------

def test_configuration_drift_stops_safely(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    c = compact()
    campaign_path, state_path = init(tmp_path, c)
    drifted = copy.deepcopy(c)
    drifted["description"] = "changed after init"
    campaign_path.write_text(json.dumps(drifted), encoding="utf-8")
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "ERROR"
    assert result["errors"][0]["code"] == "CONFIGURATION_DRIFT"
    on_disk = read_state(state_path)
    assert all(j["status"] == "SUPERSEDED" for j in on_disk["jobs"].values())


def test_preexisting_state_conflicts_stop_safely(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    state = read_state(state_path)
    state["conflicts"].append({"code": "RUN_CONFLICT", "identity": "x", "existing": "a", "incoming": "b"})
    p.atomic_write(state_path, state)
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "CONFLICT"
    assert result["manifests_ingested"] == 0  # never progressed past the pre-existing conflict


# --------------------------------------------------------------------------
# 27-28: path resolution independent of cwd, Windows-style separators
# --------------------------------------------------------------------------

def test_paths_resolve_relative_to_state_directory_not_cwd(tmp_path):
    state_path = tmp_path / "nested" / "campaign_state.json"
    resolved = cycle.resolve_paths(state_path)
    assert resolved["manifest_root"] == state_path.parent / "manifests"
    assert resolved["analysis_root"] == state_path.parent / "analysis"
    assert resolved["artifact_root"] == state_path.parent / "raw"
    assert resolved["batches_root"] == state_path.parent / "batches"
    assert resolved["batch_path"] == state_path.parent / "next_batch.json"


def test_behavior_independent_of_process_cwd(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(elsewhere))
        result = cycle.advance(str(campaign_path), str(state_path))
    finally:
        os.chdir(old_cwd)
    assert result["action"] == "RUN_DYNAMO"
    assert Path(result["batch_path"]) == state_path.parent / "next_batch.json"


def test_windows_style_state_path_resolves_under_its_own_directory():
    resolved = cycle.resolve_paths(r"C:\campaigns\stage_a\campaign_state.json".replace("\\", os.sep))
    assert resolved["state_dir"].name == "stage_a"
    assert resolved["manifest_root"].name == "manifests"
    assert resolved["manifest_root"].parent == resolved["state_dir"]


# --------------------------------------------------------------------------
# 29-30: dry-run and --json contracts
# --------------------------------------------------------------------------

def test_dry_run_performs_no_writes(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    before = state_path.read_text(encoding="utf-8")
    result = cycle.advance(str(campaign_path), str(state_path), dry_run=True)
    assert result["dry_run"] is True
    assert result["action"] == "RUN_DYNAMO"
    assert state_path.read_text(encoding="utf-8") == before
    assert not Path(result["batch_path"]).exists()
    assert not (state_path.parent / "batches").exists()


def test_json_output_conforms_to_stable_contract(tmp_path, monkeypatch, capsys):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    exit_code = cycle.main(["advance", str(campaign_path), str(state_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    required = {"action", "campaign_id", "state_path", "batch_id", "batch_path", "job_ids",
               "manifests_discovered", "manifests_ingested", "validation_manifests_ignored",
               "analyses_created", "analyses_ingested", "conflicts", "warnings", "errors"}
    assert required <= set(out)
    assert out["action"] == "RUN_DYNAMO"
    assert exit_code == 0


def test_console_output_is_not_required_to_determine_action(tmp_path, monkeypatch, capsys):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    exit_code = cycle.main(["advance", str(campaign_path), str(state_path)])
    assert exit_code == cycle.EXIT_CODES["RUN_DYNAMO"]
    out = capsys.readouterr().out
    assert out.startswith("ACTION: RUN_DYNAMO")


# --------------------------------------------------------------------------
# 31-32: import boundary and coexistence with the individual CLIs
# --------------------------------------------------------------------------

def test_wrapper_imports_no_revit_api_modules():
    source = Path(cycle.__file__).read_text(encoding="utf-8")
    assert "Autodesk.Revit" not in source
    assert "revit_probe_registry" not in source
    assert "run_probe(" not in source
    assert "import Revit" not in source


def test_existing_planner_and_analyzer_clis_remain_functional(tmp_path, capsys):
    campaign_path, state_path = init(tmp_path, compact())
    assert p.main(["status", str(campaign_path), str(state_path)]) == 0
    out_dir = tmp_path / "raw_probe"
    out_dir.mkdir()
    report = out_dir / "raw.json"
    report.write_text(json.dumps({"probe_id": "stage_a_image_alignment", "probe_schema_version": "1.0",
                                  "execution_status": "completed", "rollback_status": "succeeded",
                                  "state_restoration_status": "restored", "requested_settings": {},
                                  "artifact_paths": [], "errors": [], "warnings": [],
                                  "native_report": {"exports": {}}}), encoding="utf-8")
    assert analyze_cli_main([str(report)]) == 0
    assert report.with_name("raw.analyzed.json").exists()


def analyze_cli_main(argv):
    from tools import analyze_stage_a_probe as a
    return a.main(argv)


# --------------------------------------------------------------------------
# Concurrency lock
# --------------------------------------------------------------------------

def test_concurrent_advance_is_rejected_while_a_live_cycle_holds_the_lock(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    lock = cycle.CycleLock(state_path.parent)
    lock.acquire()
    try:
        result = cycle.advance(str(campaign_path), str(state_path))
        assert result["action"] == "ERROR"
        assert result["errors"][0]["code"] == "CYCLE_LOCK_HELD"
    finally:
        lock.release()
    # Once released, the next call proceeds normally.
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "RUN_DYNAMO"


def test_stale_lock_from_a_dead_process_is_reclaimed(tmp_path, monkeypatch):
    install_fake_analyzer(monkeypatch)
    campaign_path, state_path = init(tmp_path, compact())
    lock_path = state_path.parent / cycle.LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 999999999, "hostname": "nonexistent-host-for-test",
                                     "acquired_at": "2000-01-01T00:00:00Z", "acquired_epoch": 0}))
    result = cycle.advance(str(campaign_path), str(state_path))
    assert result["action"] == "RUN_DYNAMO"
    assert not lock_path.exists()  # released after the cycle completes
