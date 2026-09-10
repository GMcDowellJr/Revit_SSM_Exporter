"""Idempotent external cycle wrapper for the Stage A campaign.

This composes the existing deterministic planner (``tools/campaign_planner.py``)
and the existing external analyzer (``tools/analyze_stage_a_probe.py``). It runs
outside Revit, imports no Revit API modules, executes no probes, and never
assigns acceptance, satisfies manual review, or authorizes diagnostics on its
own: it only discovers manifests, calls the planner's own ingestion functions,
calls the analyzer's own ``analyze_json()``, and asks the planner what to do
next. Every gate, transition, and fallback decision remains the planner's.

Normal operation:

    python tools/stage_a_cycle.py advance campaign/campaign.json campaign/campaign_state.json
    (run Dynamo against the printed NEXT_BATCH)
    python tools/stage_a_cycle.py advance campaign/campaign.json campaign/campaign_state.json
    ...repeat until CAMPAIGN_COMPLETE or a stop action...

See docs/CAMPAIGN_PLANNER.md for the full operator workflow.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import campaign_planner as planner  # noqa: E402
from tools import analyze_stage_a_probe as analyzer  # noqa: E402
from tests.dynamo import revit_batch_contract as executor_contract  # noqa: E402

MANIFEST_FILENAME = "revit_run_manifest.json"
LOCK_FILENAME = ".stage_a_cycle.lock"
LOCK_STALE_SECONDS = 6 * 3600

# Human-readable exit codes. RUN_DYNAMO and CAMPAIGN_COMPLETE are the only
# "everything is fine, keep going / done" outcomes; every other action is a
# deliberate stop that requires an operator decision.
EXIT_CODES = {
    "RUN_DYNAMO": 0,
    "CAMPAIGN_COMPLETE": 0,
    "MANUAL_REVIEW_REQUIRED": 3,
    "DIAGNOSTIC_AUTHORIZATION_REQUIRED": 4,
    "BLOCKED": 5,
    "CONFLICT": 6,
    "AWAITING_EXTERNAL_ANALYSIS": 5,
    "AWAITING_REVIT_EXECUTION": 5,
    "ERROR": 1,
}

_TEMPLATE_CODE_HINTS = {
    "MUTATION_BLOCKED_BY_TEMPLATE_ONLY": "view-template control",
    "MUTATION_ATTESTATION_FAILED": "mutation attestation failure",
}


class StageACycleError(Exception):
    """Raised only for conditions that must abort before any result can be built."""


class CycleLockHeld(StageACycleError):
    pass


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def resolve_paths(state_path: str | Path, batch: str | None = None,
                  manifest_root: str | None = None, analysis_root: str | None = None,
                  artifact_root: str | None = None) -> dict[str, Path]:
    """Resolve every working path deterministically from the state file's
    directory, absolutely, independent of the process cwd."""
    state_path = Path(os.path.abspath(str(state_path)))
    state_dir = state_path.parent
    return {
        "state_path": state_path,
        "state_dir": state_dir,
        "batch_path": Path(os.path.abspath(batch)) if batch else state_dir / "next_batch.json",
        "manifest_root": Path(os.path.abspath(manifest_root)) if manifest_root else state_dir / "manifests",
        "analysis_root": Path(os.path.abspath(analysis_root)) if analysis_root else state_dir / "analysis",
        "artifact_root": Path(os.path.abspath(artifact_root)) if artifact_root else state_dir / "raw",
        "batches_root": state_dir / "batches",
    }


# --------------------------------------------------------------------------
# Concurrency lock
# --------------------------------------------------------------------------

def _pid_alive(pid: Any) -> bool | None:
    """Best-effort liveness check. Returns True/False, or None if unknown."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    except AttributeError:
        return None
    else:
        return True


def _lock_is_stale(payload: dict[str, Any]) -> bool:
    started_at = payload.get("acquired_epoch")
    age = (time.time() - started_at) if isinstance(started_at, (int, float)) else None
    if age is not None and age > LOCK_STALE_SECONDS:
        return True
    alive = _pid_alive(payload.get("pid")) if payload.get("hostname") == socket.gethostname() else None
    if alive is False:
        return True
    return False


class CycleLock:
    """A bounded, self-describing lock file guarding one state directory.

    Not a strict distributed lock (there is no fcntl/portable file-lock
    primitive that works identically on Windows and POSIX without extra
    dependencies); it is a best-effort, bounded mechanism: a lock older than
    ``LOCK_STALE_SECONDS`` or owned by a confirmed-dead local pid is treated
    as abandoned and reclaimed, so a crashed process never permanently blocks
    recovery. It never bypasses the planner's own atomic state writes.
    """

    def __init__(self, state_dir: Path):
        self.path = state_dir / LOCK_FILENAME
        self._owned = False

    def acquire(self) -> None:
        payload = {"pid": os.getpid(), "hostname": socket.gethostname(),
                   "acquired_at": planner.utc_now(), "acquired_epoch": time.time()}
        if self._try_create(payload):
            self._owned = True
            return
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if _lock_is_stale(existing):
            try:
                self.path.unlink()
            except OSError:
                pass
            if self._try_create(payload):
                self._owned = True
                return
        raise CycleLockHeld(
            "another advance cycle owns the lock at {0} (pid={1}, host={2}, acquired_at={3}); "
            "if that process is no longer running the lock will be reclaimed automatically "
            "after it goes stale".format(self.path, existing.get("pid"), existing.get("hostname"),
                                          existing.get("acquired_at")))

    def _try_create(self, payload: dict[str, Any]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        return True

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self._owned = False

    def __enter__(self) -> "CycleLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# --------------------------------------------------------------------------
# Manifest discovery
# --------------------------------------------------------------------------

def _discover_manifests(manifest_root: Path, campaign_id: str) -> dict[str, Any]:
    """Recursively find every ``revit_run_manifest.json``, validate its schema,
    and classify it. Never orders or selects by file mtime - only by the
    manifest's own recorded ``started_at``/``run_id`` identity, so discovery
    never guesses evidence identity from "newest file"."""
    result = {"discovered": 0, "candidates": [], "validation_only": 0, "unrelated_campaign": 0,
              "configuration_failed": 0, "invalid_schema": [], "manifest_by_run_id": {}}
    if not manifest_root.is_dir():
        return result
    paths = sorted(p for p in manifest_root.rglob(MANIFEST_FILENAME) if p.is_file())
    for path in paths:
        result["discovered"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result["invalid_schema"].append({"path": str(path), "error": "{0}: {1}".format(type(exc).__name__, exc)})
            continue
        try:
            executor_contract.validate_manifest(data)
        except executor_contract.ContractError as exc:
            result["invalid_schema"].append({"path": str(path), "error": str(exc)})
            continue
        run_id = data.get("run_id")
        if run_id:
            result["manifest_by_run_id"][run_id] = path
        if data.get("campaign_id") != campaign_id:
            result["unrelated_campaign"] += 1
            continue
        if data.get("execution_status") == "validation_only":
            result["validation_only"] += 1
            continue
        if data.get("execution_status") == "configuration_failed":
            result["configuration_failed"] += 1
            continue
        result["candidates"].append({"path": path, "data": data, "run_id": run_id,
                                     "started_at": data.get("started_at") or ""})
    result["candidates"].sort(key=lambda e: (e["started_at"], e["run_id"] or ""))
    return result


# --------------------------------------------------------------------------
# Phase: analyze outstanding evidence
# --------------------------------------------------------------------------

def _outstanding_runs(state: dict[str, Any], manifest_by_run_id: dict[str, Path]) -> list[tuple[str, Path]]:
    """Every run_id backing a currently-EXECUTED job (evidence ingested, no
    normalized acceptance yet), deterministically ordered."""
    run_ids: dict[str, Path] = {}
    for job in state["jobs"].values():
        if job["status"] != "EXECUTED":
            continue
        details = (job.get("status_reason") or {}).get("details") or {}
        run_id = details.get("run_id")
        if not run_id:
            continue
        path = manifest_by_run_id.get(run_id)
        if path is not None:
            run_ids[run_id] = path
    return sorted(run_ids.items(), key=lambda item: (str(item[1]), item[0]))


def _load_analyzed(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mirror_analysis(canonical: Path, analysis_root: Path, run_id: str, warnings: list[str]) -> None:
    dest = analysis_root / run_id / "revit_run_manifest.analyzed.json"
    if dest == canonical:
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(canonical), str(dest))
    except OSError as exc:
        warnings.append("could not mirror analysis for run {0} into analysis_root: {1}".format(run_id, exc))


# --------------------------------------------------------------------------
# Phase: decide next action from current (already-evaluated) state
# --------------------------------------------------------------------------

def _decide(campaign: dict[str, Any], state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Read-only: derive the next action from current state without mutating
    scheduling state (mirrors campaign_planner.status's own guarantee)."""
    planner.evaluate(campaign, state)
    batched = [j for j in state["jobs"].values() if j["status"] == "BATCHED"]
    if batched:
        return "RUN_DYNAMO_PENDING", {"jobs": batched}
    rec = planner.compute_next_recommendation(state)
    code = rec["code"]
    if code == "GENERATE_NEXT_BATCH":
        return "GENERATE_NEXT_BATCH", rec
    if code == "CAMPAIGN_COMPLETE":
        return "CAMPAIGN_COMPLETE", rec
    if code == "AWAITING_MANUAL_REVIEW":
        return "MANUAL_REVIEW_REQUIRED", rec
    if code == "INVALID_CAMPAIGN_STATE":
        return "CONFLICT", rec
    if code == "BLOCKED_BY_FAILURE":
        if rec.get("reason") in ("MANUAL_REVIEW_REJECTED", "MANUAL_REVIEW_EVIDENCE_MISSING"):
            return "BLOCKED", rec
        failed_jobs = sorted(j["job_id"] for j in state["jobs"].values() if j["status"] == "FAILED")
        if failed_jobs:
            return "DIAGNOSTIC_AUTHORIZATION_REQUIRED", {**rec, "job_ids": failed_jobs}
        return "BLOCKED", rec
    # AWAITING_REVIT_EXECUTION / AWAITING_EXTERNAL_ANALYSIS: only reachable
    # here in --dry-run when analysis was deliberately skipped, or as a
    # transient reflection of state compute_next_recommendation() itself
    # defines. Passed through verbatim rather than guessed at.
    return code, rec


def _fallback_annotations(state: dict[str, Any], job_ids: list[str]) -> list[dict[str, Any]]:
    notes = []
    for job_id in job_ids:
        job = state["jobs"].get(job_id) or {}
        provenance = job.get("provenance") or {}
        if not provenance.get("closure_id"):
            continue
        primary = state["jobs"].get(provenance.get("fallback_of_job_id")) or {}
        codes = provenance.get("trigger_reason_codes_matched") or []
        hint = ", ".join(_TEMPLATE_CODE_HINTS.get(c, c) for c in codes) or "an unspecified condition"
        notes.append({
            "job_id": job_id, "job_key": job.get("job_key"), "variant": job.get("variant"),
            "closure_id": provenance.get("closure_id"),
            "primary_job_id": primary.get("job_id"), "primary_variant": primary.get("variant"),
            "reason_codes": codes,
            "reason": "{0} blocked by {1}".format(primary.get("variant") or provenance.get("fallback_of_job_id"), hint),
        })
    return notes


# --------------------------------------------------------------------------
# Batch snapshot helpers
# --------------------------------------------------------------------------

def _rebuild_batch_snapshot(campaign: dict[str, Any], state: dict[str, Any], batch_id: str) -> dict[str, Any] | None:
    """Deterministically re-derive an already-committed batch's JSON shape
    from state alone. Used only for self-healing when a prior invocation
    crashed after persisting BATCHED job state but before the immutable
    snapshot file was written. This re-derives presentation formatting for
    jobs the planner already selected and committed to BATCHED - it makes no
    new gating/acceptance/dependency decision."""
    jobs = sorted((j for j in state["jobs"].values() if batch_id in j.get("batch_ids", [])),
                 key=lambda j: (j["stage_order"], j["job_order"]))
    if not jobs:
        return None
    defaults = campaign["execution_defaults"]
    batch = {"schema_version": "1.0", "campaign_id": campaign["campaign_id"], "batch_id": batch_id,
             "created_at": planner.utc_now(), "campaign_configuration_fingerprint": state["campaign_configuration_fingerprint"],
             "document": copy.deepcopy(campaign["document_expectations"]),
             "execution_policy": {"max_jobs_per_run": defaults["batch_size"], "on_job_error": defaults.get("on_job_error", "continue"),
                                  "resume": True, "allow_rerun": False},
             "jobs": []}
    for item in jobs:
        view = campaign["view_registry"][item["view_key"]]
        batch["jobs"].append({"job_id": item["job_id"], "probe_id": item["probe_id"], "variant": item["variant"],
            "view": {"unique_id": view["unique_id"], "name": view["expected_name"], "view_type": view["expected_view_type"],
                     "crop_active": view["expected_crop_active"]},
            "settings": copy.deepcopy(item["settings"]), "output_directory": item.get("output_directory", "raw/{0}".format(item["job_id"])),
            "job_configuration_fingerprint": item["configuration_fingerprint"]})
    return batch


def _batch_matches(published: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    if published.get("batch_id") != snapshot.get("batch_id"):
        return False
    if published.get("campaign_id") != snapshot.get("campaign_id"):
        return False
    if published.get("campaign_configuration_fingerprint") != snapshot.get("campaign_configuration_fingerprint"):
        return False
    pj, sj = published.get("jobs", []), snapshot.get("jobs", [])
    if [j.get("job_id") for j in pj] != [j.get("job_id") for j in sj]:
        return False
    return all(a.get("job_configuration_fingerprint") == b.get("job_configuration_fingerprint") for a, b in zip(pj, sj))


# --------------------------------------------------------------------------
# Result shaping
# --------------------------------------------------------------------------

def _base_result(paths: dict[str, Path], dry_run: bool) -> dict[str, Any]:
    return {"action": None, "campaign_id": None, "state_path": str(paths["state_path"]),
            "batch_id": None, "batch_path": str(paths["batch_path"]), "job_ids": [],
            "manifests_discovered": 0, "manifests_ingested": 0, "validation_manifests_ignored": 0,
            "analyses_created": 0, "analyses_ingested": 0, "conflicts": [], "warnings": [], "errors": [],
            "dry_run": dry_run, "manifest_root": str(paths["manifest_root"]),
            "analysis_root": str(paths["analysis_root"]), "artifact_root": str(paths["artifact_root"]),
            "batches_root": str(paths["batches_root"])}


def _error_result(paths: dict[str, Path], dry_run: bool, code: str, message: str) -> dict[str, Any]:
    result = _base_result(paths, dry_run)
    result["action"] = "ERROR"
    result["errors"] = [{"code": code, "message": message}]
    return result


# --------------------------------------------------------------------------
# Main orchestration
# --------------------------------------------------------------------------

def advance(campaign_path: str | Path, state_path: str | Path, *, batch: str | None = None,
           manifest_root: str | None = None, analysis_root: str | None = None,
           artifact_root: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    campaign_path = Path(campaign_path)
    paths = resolve_paths(state_path, batch, manifest_root, analysis_root, artifact_root)

    if not paths["state_path"].exists():
        return _error_result(paths, dry_run, "STATE_NOT_INITIALIZED",
            "no campaign state at {0}. Initialize it first: python tools/campaign_planner.py init {1} {2}".format(
                paths["state_path"], campaign_path, paths["state_path"]))

    lock = None
    try:
        if not dry_run:
            lock = CycleLock(paths["state_dir"])
            lock.acquire()
        return _advance_locked(campaign_path, paths, dry_run)
    except CycleLockHeld as exc:
        return _error_result(paths, dry_run, "CYCLE_LOCK_HELD", str(exc))
    except planner.CampaignError as exc:
        return _error_result(paths, dry_run, type(exc).__name__.upper(), str(exc))
    except executor_contract.ContractError as exc:
        return _error_result(paths, dry_run, "MANIFEST_CONTRACT_ERROR", str(exc))
    except (OSError, ValueError) as exc:
        return _error_result(paths, dry_run, type(exc).__name__.upper(), str(exc))
    finally:
        if lock is not None:
            lock.release()


def _advance_locked(campaign_path: Path, paths: dict[str, Path], dry_run: bool) -> dict[str, Any]:
    campaign = planner.validate_campaign(planner.load_json(campaign_path))
    state = planner.migrate_state(planner.load_json(paths["state_path"]))
    result = _base_result(paths, dry_run)
    result["campaign_id"] = campaign.get("campaign_id")

    working_state = copy.deepcopy(state) if dry_run else state

    # --- Phase 1: validate binding -----------------------------------
    try:
        planner._assert_binding(campaign, working_state)  # noqa: SLF001 - canonical binding check, not duplicated
    except planner.ConfigurationDriftError as exc:
        if not dry_run:
            planner.atomic_write(paths["state_path"], working_state)
        result["action"] = "ERROR"
        result["errors"] = [{"code": "CONFIGURATION_DRIFT", "message": str(exc)}]
        result["conflicts"] = list(working_state.get("conflicts", []))
        return result

    if working_state.get("conflicts"):
        # A prior cycle already left unresolved conflicts; surface them again
        # every time rather than silently retrying past them.
        result["action"] = "CONFLICT"
        result["conflicts"] = list(working_state["conflicts"])
        return result

    # --- Phase 2: discover manifests -----------------------------------
    discovery = _discover_manifests(paths["manifest_root"], campaign["campaign_id"])
    result["manifests_discovered"] = discovery["discovered"]
    result["validation_manifests_ignored"] = discovery["validation_only"]
    if discovery["unrelated_campaign"]:
        result["warnings"].append("{0} manifest(s) ignored: unrelated campaign_id".format(discovery["unrelated_campaign"]))
    if discovery["configuration_failed"]:
        result["warnings"].append("{0} manifest(s) ignored: executor configuration_failed".format(discovery["configuration_failed"]))
    for bad in discovery["invalid_schema"]:
        result["warnings"].append("manifest schema invalid at {0}: {1}".format(bad["path"], bad["error"]))

    # --- Phase 3: ingest execution evidence -----------------------------------
    # Skip manifests already ingested with an identical fingerprint (reusing
    # the planner's own canonical_fingerprint, not a reimplementation of its
    # identity check): this keeps a no-op repeat call from calling
    # ingest_manifests() at all, which - besides being wasted work - would
    # otherwise bump state["updated_at"] via the planner's own unconditional
    # evaluate() every single time, defeating byte-for-byte idempotency. A
    # manifest whose content actually changed for the same run_id is never
    # skipped here; it still reaches ingest_manifests() and is still
    # correctly surfaced as a RUN_CONFLICT.
    before_runs = set(working_state["ingested_revit_runs"])
    conflicts_before = len(working_state["conflicts"])
    to_ingest = []
    for candidate in discovery["candidates"]:
        prior = working_state["ingested_revit_runs"].get(candidate["run_id"])
        if prior and prior.get("fingerprint") == planner.canonical_fingerprint(candidate["data"]):
            continue
        to_ingest.append(candidate["data"])
    if to_ingest:
        planner.ingest_manifests(campaign, working_state, to_ingest)
    result["manifests_ingested"] = len(set(working_state["ingested_revit_runs"]) - before_runs)
    if not dry_run:
        planner.atomic_write(paths["state_path"], working_state)
    if len(working_state["conflicts"]) > conflicts_before:
        result["action"] = "CONFLICT"
        result["conflicts"] = list(working_state["conflicts"])
        return result

    # --- Phase 4 + 5: analyze outstanding evidence, ingest acceptance -----------------------------------
    outstanding = _outstanding_runs(working_state, discovery["manifest_by_run_id"])
    analyzer_failed = False
    for run_id, manifest_path in outstanding:
        analyzed_path = manifest_path.with_name(manifest_path.stem + ".analyzed.json")
        if analyzed_path.exists():
            analyzed = _load_analyzed(analyzed_path)
        elif dry_run:
            result["warnings"].append("run {0} has outstanding evidence that would be analyzed (skipped in dry-run)".format(run_id))
            continue
        else:
            try:
                analyzed_path, _msg = analyzer.analyze_json(manifest_path)
            except Exception as exc:  # analyzer's own contract: this is a genuine, unexpected analyzer failure
                error_record = {"code": "ANALYZER_INVOCATION_FAILED", "run_id": run_id,
                                "manifest_path": str(manifest_path), "type": type(exc).__name__, "message": str(exc)}
                try:
                    planner.atomic_write(paths["analysis_root"] / run_id / "analyzer_error.json", error_record)
                except OSError:
                    pass
                result["errors"].append(error_record)
                analyzer_failed = True
                break
            result["analyses_created"] += 1
            analyzed = _load_analyzed(analyzed_path)
        if not dry_run:
            _mirror_analysis(analyzed_path, paths["analysis_root"], run_id, result["warnings"])
        before_records = set(working_state["ingested_analysis_records"])
        conflicts_before = len(working_state["conflicts"])
        planner.ingest_analysis(campaign, working_state, [analyzed])
        result["analyses_ingested"] += len(set(working_state["ingested_analysis_records"]) - before_records)
        if not dry_run:
            planner.atomic_write(paths["state_path"], working_state)
        if len(working_state["conflicts"]) > conflicts_before:
            result["action"] = "CONFLICT"
            result["conflicts"] = list(working_state["conflicts"])
            return result

    if analyzer_failed:
        result["action"] = "ERROR"
        return result

    # --- Phase 6: decide next action -----------------------------------
    code, payload = _decide(campaign, working_state)

    if code == "RUN_DYNAMO_PENDING":
        batched_jobs = payload["jobs"]
        batch_id = batched_jobs[0]["batch_ids"][-1]
        if any(j["batch_ids"][-1] != batch_id for j in batched_jobs):
            result["action"] = "CONFLICT"
            result["errors"].append({"code": "INCONSISTENT_PENDING_BATCH",
                                     "message": "BATCHED jobs disagree on the current pending batch_id"})
            return result
        snapshot_path = paths["batches_root"] / "{0}.json".format(batch_id)
        if not snapshot_path.exists():
            if dry_run:
                snapshot = _rebuild_batch_snapshot(campaign, working_state, batch_id)
            else:
                snapshot = _rebuild_batch_snapshot(campaign, working_state, batch_id)
                if snapshot is not None:
                    planner.atomic_write(snapshot_path, snapshot)
        else:
            snapshot = planner.load_json(snapshot_path)
        if snapshot is None:
            result["action"] = "ERROR"
            result["errors"].append({"code": "PENDING_BATCH_SNAPSHOT_MISSING",
                                     "message": "batch {0} is pending but no snapshot could be found or rebuilt".format(batch_id)})
            return result
        if paths["batch_path"].exists():
            published = planner.load_json(paths["batch_path"])
            if not _batch_matches(published, snapshot):
                result["action"] = "ERROR"
                result["errors"].append({"code": "NEXT_BATCH_MISMATCH",
                                         "message": "{0} does not match the pending batch {1}; investigate before continuing".format(
                                             paths["batch_path"], batch_id)})
                return result
        elif not dry_run:
            planner.atomic_write(paths["batch_path"], snapshot)
        result["action"] = "RUN_DYNAMO"
        result["batch_id"] = batch_id
        result["job_ids"] = [j["job_id"] for j in snapshot["jobs"]]
        result["fallback_context"] = _fallback_annotations(working_state, result["job_ids"])
        return result

    if code == "GENERATE_NEXT_BATCH":
        if dry_run:
            eligible = sorted((j for j in working_state["jobs"].values() if j["status"] == "ELIGIBLE"),
                              key=lambda j: (j["stage_order"], j["job_order"]))
            selected = eligible[:campaign["execution_defaults"]["batch_size"]]
            result["action"] = "RUN_DYNAMO"
            result["job_ids"] = [j["job_id"] for j in selected]
            result["warnings"].append("dry-run: next batch would contain {0} job(s); nothing was written".format(len(selected)))
            return result
        batch = planner.generate_next_batch(campaign, working_state)
        planner.atomic_write(paths["state_path"], working_state)
        batch_id = batch["batch_id"]
        planner.atomic_write(paths["batches_root"] / "{0}.json".format(batch_id), batch)
        planner.atomic_write(paths["batch_path"], batch)
        result["action"] = "RUN_DYNAMO"
        result["batch_id"] = batch_id
        result["job_ids"] = [j["job_id"] for j in batch["jobs"]]
        result["fallback_context"] = _fallback_annotations(working_state, result["job_ids"])
        return result

    if code == "CAMPAIGN_COMPLETE":
        result["action"] = "CAMPAIGN_COMPLETE"
        result["host_color_id_feasibility"] = planner.host_color_id_feasibility(campaign, working_state)
        return result

    if code == "MANUAL_REVIEW_REQUIRED":
        pending = sorted(jid for jid, review in working_state["manual_review_requirements"].items()
                         if review["status"] == "PENDING")
        result["action"] = "MANUAL_REVIEW_REQUIRED"
        result["job_ids"] = pending
        # Per-job operator-facing detail: the operator must never have to
        # search the campaign tree manually for what to look at or why - each
        # pending review names its job, the view it renders, the concrete
        # raster artifact(s) already on record for it, and the specific
        # review question configured for that job (falling back to the
        # gate-scoped reason code only when no campaign-authored question was
        # ever set).
        reviews = []
        for job_id in pending:
            job = working_state["jobs"].get(job_id, {})
            view = campaign.get("view_registry", {}).get(job.get("view_key"), {})
            requirement = working_state["manual_review_requirements"][job_id]
            reviews.append({
                "job_id": job_id,
                "job_key": job.get("job_key"),
                "view_key": job.get("view_key"),
                "view_name": view.get("expected_name"),
                "view_type": view.get("expected_view_type"),
                "artifact_references": list(requirement.get("artifact_references") or []),
                "review_question": requirement.get("reason"),
            })
        result["reviews"] = reviews
        result["reason"] = "gate-scoped or campaign-authored manual review is pending"
        return result

    if code == "DIAGNOSTIC_AUTHORIZATION_REQUIRED":
        result["action"] = "DIAGNOSTIC_AUTHORIZATION_REQUIRED"
        result["job_ids"] = payload.get("job_ids", [])
        result["reason"] = ("job(s) are FAILED with no automatic path forward; authorize a diagnostic with "
                            "'python tools/campaign_planner.py diagnostic <campaign> <state> request JOB_ID --rule RULE_ID' "
                            "or investigate manually")
        return result

    if code == "CONFLICT":
        result["action"] = "CONFLICT"
        result["conflicts"] = list(working_state.get("conflicts", []))
        return result

    if code == "BLOCKED":
        result["action"] = "BLOCKED"
        result["job_ids"] = sorted(working_state.get("blocked_jobs", {}))
        result["reason"] = payload.get("reason") or "one or more jobs are blocked with no eligible work remaining"
        return result

    # AWAITING_REVIT_EXECUTION / AWAITING_EXTERNAL_ANALYSIS: only reachable
    # in --dry-run where analysis was intentionally skipped.
    result["action"] = code
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_console(result: dict[str, Any]) -> None:
    action = result["action"]
    print("ACTION: {0}".format(action))
    print("CAMPAIGN: {0}".format(result.get("campaign_id")))
    if action == "RUN_DYNAMO":
        print("BATCH: {0}".format(result.get("batch_id")))
        print("JOBS: {0}".format(len(result.get("job_ids", []))))
        print("NEXT_BATCH: {0}".format(result.get("batch_path")))
        for note in result.get("fallback_context") or []:
            print("NEXT_JOB: {0}".format(note.get("variant") or note.get("job_key")))
            print("REASON: {0}".format(note.get("reason")))
    elif action == "MANUAL_REVIEW_REQUIRED":
        for review in result.get("reviews") or [{"job_id": jid} for jid in result.get("job_ids", [])]:
            print("JOB: {0}".format(review.get("job_id")))
            if review.get("view_name") or review.get("view_type"):
                print("  VIEW: {0} ({1})".format(review.get("view_name"), review.get("view_type")))
            artifacts = review.get("artifact_references") or []
            if artifacts:
                for artifact in artifacts:
                    print("  ARTIFACT: {0}".format(artifact))
            else:
                print("  ARTIFACT: none recorded")
            if review.get("review_question"):
                print("  REVIEW_QUESTION: {0}".format(review["review_question"]))
        if result.get("reason"):
            print("REASON: {0}".format(result["reason"]))
    elif action == "DIAGNOSTIC_AUTHORIZATION_REQUIRED":
        for job_id in result.get("job_ids", []):
            print("JOB: {0}".format(job_id))
        if result.get("reason"):
            print("REASON: {0}".format(result["reason"]))
    elif action in ("BLOCKED", "CONFLICT"):
        for job_id in result.get("job_ids", []):
            print("JOB: {0}".format(job_id))
        if result.get("reason"):
            print("REASON: {0}".format(result["reason"]))
        for c in result.get("conflicts", []):
            print("CONFLICT: {0}".format(c))
    elif action == "ERROR":
        for e in result.get("errors", []):
            print("ERROR: {0}: {1}".format(e.get("code"), e.get("message")))
    if action == "CAMPAIGN_COMPLETE" and result.get("host_color_id_feasibility"):
        print("HOST_COLOR_ID_FEASIBILITY: {0}".format(result["host_color_id_feasibility"]["conclusion"]))
    if result.get("dry_run"):
        print("DRY_RUN: true (no state, analysis, or batch files were written)")
    print("VALIDATION_MANIFESTS_IGNORED: {0}".format(result.get("validation_manifests_ignored", 0)))
    print("RUNS_INGESTED: {0}".format(result.get("manifests_ingested", 0)))
    print("ANALYSES_CREATED: {0}".format(result.get("analyses_created", 0)))
    print("ANALYSES_INGESTED: {0}".format(result.get("analyses_ingested", 0)))
    for warning in result.get("warnings", []):
        print("WARNING: {0}".format(warning))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advance the Stage A campaign one idempotent cycle between manual Dynamo executions.")
    sub = parser.add_subparsers(dest="command", required=True)
    advance_parser = sub.add_parser("advance", help="Ingest evidence, analyze, and prepare the next Dynamo batch")
    advance_parser.add_argument("campaign")
    advance_parser.add_argument("state")
    advance_parser.add_argument("--batch", help="Override next_batch.json publish path (default: <state-dir>/next_batch.json)")
    advance_parser.add_argument("--manifest-root", help="Override manifest discovery root (default: <state-dir>/manifests)")
    advance_parser.add_argument("--analysis-root", help="Override analysis output root (default: <state-dir>/analysis)")
    advance_parser.add_argument("--artifact-root", help="Override raw artifact root, reported only (default: <state-dir>/raw)")
    advance_parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing anything")
    advance_parser.add_argument("--json", action="store_true", help="Print the machine-readable JSON summary instead of console text")
    args = parser.parse_args(argv)

    result = advance(args.campaign, args.state, batch=args.batch, manifest_root=args.manifest_root,
                     analysis_root=args.analysis_root, artifact_root=args.artifact_root, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_console(result)
    return EXIT_CODES.get(result["action"], 1)


if __name__ == "__main__":
    sys.exit(main())
