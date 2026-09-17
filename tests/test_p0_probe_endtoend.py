"""Run the P0 probe end to end without Revit.

This test exists because it did not. Two Revit runs were spent discovering
that run_probe returned a bare report instead of an execution envelope, and
that its captures went through production with raster=None -- both of which
are visible from the probe's return value alone and needed no Revit at all.
The offline suite had 45 tests for the COMPARATORS, which grade fabricated
records, and none for the PRODUCER that has to emit them. Testing the grader
against records you wrote by hand only proves the grader agrees with your
idea of the producer.

The Revit surface here is faked exactly as far as run_probe's own control
flow reaches -- transaction group, view, document -- and the capture step
is stubbed, because what is under test is the contract run_probe must
satisfy for the batch executor, not what production does inside a capture
(probe_stage_a_drift_onset and the color_id_buffer suites cover that).
"""
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "tests" / "dynamo") not in sys.path:
    sys.path.insert(0, str(REPO / "tests" / "dynamo"))

import probe_stage_a_p0_export_correctness as probe  # noqa: E402
from tests.dynamo import stage_a_probe_contract as contract  # noqa: E402


class _FakeId:
    def __init__(self, value):
        self.IntegerValue = int(value)


class _FakeView:
    def __init__(self, view_id=528698, name="MOB 1 - LEVEL 2"):
        self.Id = _FakeId(view_id)
        self.Name = name
        self.ViewType = "FloorPlan"
        self.CropBoxActive = False
        self.IsTemplate = False
        self.Document = object()

    @property
    def UniqueId(self):
        return "fake-unique-id"


class _FakeTransactionStatus:
    Started = "Started"
    RolledBack = "RolledBack"


class _FakeTransactionGroup:
    instances = []

    def __init__(self, doc, name):
        self.doc, self.name = doc, name
        self.started = False
        self.rolled_back = False
        _FakeTransactionGroup.instances.append(self)

    def Start(self):
        self.started = True
        return _FakeTransactionStatus.Started

    def RollBack(self):
        self.rolled_back = True
        return _FakeTransactionStatus.RolledBack


@pytest.fixture
def fake_revit(monkeypatch):
    _FakeTransactionGroup.instances = []
    db = types.ModuleType("Autodesk.Revit.DB")
    db.TransactionGroup = _FakeTransactionGroup
    db.TransactionStatus = _FakeTransactionStatus
    for name in ("Autodesk", "Autodesk.Revit", "Autodesk.Revit.DB"):
        monkeypatch.setitem(sys.modules, name, db if name.endswith("DB")
                            else types.ModuleType(name))
    monkeypatch.setattr(probe, "_get_current_document", lambda view=None: object())
    yield db


def _stub_capture(record_overrides=None, raise_with=None):
    def _capture(doc, view, case, output_dir, export_dpi=None, cap_axis_px=None,
                 disable_underlay=True, diag=None):
        if raise_with is not None:
            raise raise_with
        record = {
            "case": case, "view_id": 528698, "view_name": "MOB 1 - LEVEL 2",
            "resolution": {"dim_check": "pass", "actual_w": 8584, "actual_h": 9900},
            "probe_overrides": [{"override": "underlay_disabled", "value": True,
                                 "applied": True, "restored": False,
                                 "underlay_off_for_capture": True}],
            "metrics": {}, "success": True, "failure_reason": None,
        }
        record.update(record_overrides or {})
        return {"record_seed": record, "ledger": None,
                "tiff_path": str(Path(output_dir) / "c.tiff"),
                "sidecar_path": str(Path(output_dir) / "c.json"), "sidecar": {}}
    return _capture


def test_capture_run_returns_a_valid_completed_envelope(fake_revit, monkeypatch, tmp_path):
    """The defect that failed three jobs in Revit: no envelope, so the
    executor's `envelope.get("execution_status", "failed")` defaulted."""
    monkeypatch.setattr(probe, "run_capture", _stub_capture())
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_default")

    contract.validate_execution_envelope(env)
    assert env["execution_status"] == "completed"
    assert env["probe_id"] == probe.PROBE_NAME
    assert env["rollback_status"] == "succeeded"
    assert env["state_restoration_status"] == "restored"
    assert env["errors"] == []
    assert env["native_report"]["records"][0]["case"] == "p0_default"
    # The report is an artifact, and so is each capture.
    assert any(a.endswith("stage_a_p0_export_correctness.json") for a in env["artifact_paths"])
    assert any(a.endswith("c.tiff") for a in env["artifact_paths"])


def test_the_group_is_rolled_back_even_though_nothing_raised(fake_revit, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "run_capture", _stub_capture())
    probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path), selection="p0_default")
    groups = _FakeTransactionGroup.instances
    assert len(groups) == 1 and groups[0].started and groups[0].rolled_back


def test_an_applied_override_is_marked_restored_by_the_rollback(fake_revit, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "run_capture", _stub_capture())
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_default")
    override = env["native_report"]["records"][0]["probe_overrides"][0]
    assert override["restored"] is True


def test_an_unapplied_override_is_not_touched_by_the_rollback(fake_revit, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "run_capture", _stub_capture({
        "probe_overrides": [{"override": "underlay_disabled", "value": True,
                             "applied": False, "reason": "no_underlay_configured",
                             "restored": True, "underlay_off_for_capture": True}]}))
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_default")
    override = env["native_report"]["records"][0]["probe_overrides"][0]
    assert override["applied"] is False and override["restored"] is True


def test_a_raising_capture_fails_the_envelope_and_is_recorded(fake_revit, monkeypatch, tmp_path):
    """A case that could not run is not a case that passed."""
    monkeypatch.setattr(probe, "run_capture",
                        _stub_capture(raise_with=RuntimeError("boom")))
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_default")
    contract.validate_execution_envelope(env)
    assert env["execution_status"] == "failed"
    assert env["errors"] and "boom" in env["errors"][0]["error"]
    # envelope_status reads report["exceptions"]; it must be in step with errors.
    assert env["native_report"]["exceptions"]


def test_select_is_read_only_and_opens_no_transaction_group(fake_revit, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "run_select",
                        lambda doc, dpi, fit, cap_axis_px=None, diag=None: {
                            "case": "p0_select", "candidates": [],
                            "derived_job_candidates": [],
                            "enumeration_census": {"collector_returned": 0},
                            "probe_overrides": []})
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_select")
    contract.validate_execution_envelope(env)
    assert _FakeTransactionGroup.instances == []
    assert env["rollback_status"] == "not_started"
    assert env["state_restoration_status"] == "not_checked"
    # Read-only, so an empty candidate list is still a completed run -- but
    # the census has to be there to say why it is empty.
    assert env["execution_status"] == "completed"
    assert "enumeration_census" in env["native_report"]["records"][0]


def test_report_states_which_build_produced_it(fake_revit, monkeypatch, tmp_path):
    """Two runs produced byte-identical captures across a fix; telling them
    apart meant fingerprinting which keys existed."""
    monkeypatch.setattr(probe, "run_capture", _stub_capture())
    env = probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                          selection="p0_default")
    info = env["native_report"]["probe"]
    assert info["version"] == probe.PROBE_VERSION != "2026-09-17.1"
    assert set(info["features"]) == set(probe.REPORT_FEATURES)


def test_unknown_selection_is_refused_before_anything_runs(fake_revit, tmp_path):
    with pytest.raises(ValueError):
        probe.run_probe(raw_view=_FakeView(), output_dir=str(tmp_path),
                        selection="p0_defualt")


def test_output_dir_is_required(fake_revit):
    with pytest.raises(ValueError):
        probe.run_probe(raw_view=_FakeView(), output_dir=None, selection="p0_default")
