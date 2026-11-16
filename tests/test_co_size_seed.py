"""S1 (2026-11-13): the first attempt is seeded from the previous run's
co-sized fit (vop_interwoven/co_size_seed.py), driven through production's
``export_registered_stage_a_view`` on the fake DB whose annotation export
renders the drawn extent (tests/test_stage_a_lattice_overrun_probe.py: crop A
plus a section marker 21 ft past it, 10725 px tall at the model's 9750 px
request).

Run 1 is captured unseeded; its run_meta record (``out["co_sizing"]``) seeds
run 2. Asserted on what each EXPORT saw and on the FILES:

* gate 4 (fake half): a correctly seeded view is captured in ONE attempt,
  and both exports are exactly run 1's co-sized exports;
* gate 5: a seed that does not fit costs exactly one more attempt, at the
  unseeded fit_px on this fixture, and never a third -- with the measured
  bound over generated inputs (the retry can sit 1-2 px BELOW the unseeded
  fit, never above it, never over the ceiling);
* gate 6: a changed size key is not seeded;
* every view's record carries ``seed``.

Mutations wired here (gate 7): seeding regardless of the key turns the
key-mismatch test red (7b); allowing a third attempt turns the still-overruns
test red (7c); confirming a seed without the measurement check turns the
changed-annotation test red.
"""
import copy
import json
import math

import pytest
from hypothesis import given, settings, strategies as st

from vop_interwoven import co_size_seed as cs
from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX, co_sized_request
from vop_interwoven.run_meta import view_outcomes

from tests.test_stage_a_annotation_pass_probe_switches import VIEW_ID
from tests.test_stage_a_lattice_overrun_probe import run_capture

R = 9750
FIT = 9090                       # co_sized_request(9750, ..., 10725) -- the unseeded fit


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def _seeds(compact, run_id="RUN_1"):
    return {"schema": cs.SCHEMA, "from_run_id": run_id, "from_run_dir": "x",
            "config_hash": None, "views": {str(VIEW_ID): compact}, "reason": None}


def _run1(tmp_path, monkeypatch):
    out, _view, _doc, seen = run_capture(tmp_path / "run1", monkeypatch)
    monkeypatch.undo()
    return out, seen


def _seeded(tmp_path, monkeypatch, compact, name="run2", **kwargs):
    extra = dict(kwargs.pop("extra_cfg", {}), _stage_a_co_size_seeds=_seeds(compact))
    out, _view, _doc, seen = run_capture(tmp_path / name, monkeypatch, extra_cfg=extra,
                                         **kwargs)
    monkeypatch.undo()
    return out, seen


def _passes(seen):
    return [(s["pass"], s["px"], s["h"]) for s in seen]


# --- run 1: what the next run seeds from ------------------------------------

def test_an_unseeded_co_sized_view_records_what_the_next_run_needs(tmp_path, monkeypatch):
    out, seen = _run1(tmp_path, monkeypatch)
    co = out["co_sizing"]
    assert co["final_request"] == "co_sized" and co["fit_px"] == FIT and co["attempts"] == 2
    assert co["final_failure_reason"] is None
    assert co["size_key"]["requested_px"] == R
    assert co["final_measure"]["pixel_size"] == FIT
    assert co["seed"]["state"] == cs.NO_PREVIOUS
    # run_meta carries it per view (the next run reads one file, not sidecars).
    (entry,) = view_outcomes([{"view_id": VIEW_ID, "co_sizing": co,
                               "stage_a_capture_status": "success"}])
    assert entry["co_sizing"] == co
    # And the sidecar's record says the same, seed included.
    side = _file(out["sidecar_path"])["registration_marks"]["co_sizing"]
    assert side["seed"]["state"] == cs.NO_PREVIOUS and side["size_key"] == co["size_key"]


# --- gate 4 (fake half): a correct seed, one attempt, the same exports -------

def test_a_correct_seed_captures_in_one_attempt_with_run_1s_exports(tmp_path, monkeypatch):
    out1, seen1 = _run1(tmp_path, monkeypatch)
    out2, seen2 = _seeded(tmp_path, monkeypatch, out1["co_sizing"])
    # Run 1: attempt 1 at R (the overrun and its halving), attempt 2 at FIT.
    # Run 2: run 1's attempt 2, and nothing else.
    assert _passes(seen2) == _passes(seen1)[3:]
    assert _passes(seen2) == [("model", FIT, int(math.floor(FIT * 45.0 / 60.0))),
                              ("annotation", FIT, int(math.floor(FIT * 66.0 / 60.0)))]
    reg = out2["registration"]
    assert reg["faults"] == [] and out2["success"] is True
    co = reg["co_sizing"]
    assert co["attempts"] == 1 and co["fit_px"] == FIT and co["final_request"] == "co_sized"
    assert co["seed"]["state"] == cs.SEEDED and co["seed"]["outcome"] == cs.CONFIRMED
    assert co["seed"]["from_run_id"] == "RUN_1"
    assert reg["timings_ms"]["co_sizing_first_attempt"] == 0.0
    # The FILES: the frames are run 1's.
    for key in ("sidecar_path", "annotation_sidecar_path"):
        f1, f2 = _file(out1[key])["frame"], _file(out2[key])["frame"]
        assert f1 == f2, key
    # And run 2 seeds run 3 in turn: a confirmed seed is a co-sized capture.
    assert out2["co_sizing"]["final_request"] == "co_sized"
    assert out2["co_sizing"]["final_measure"] == out1["co_sizing"]["final_measure"]
    assert cs.seed_for_view(out2["co_sizing"], out2["co_sizing"]["size_key"],
                            "RUN_2")["state"] == cs.SEEDED


# --- gate 5: a wrong seed, exactly one more attempt -------------------------

@pytest.mark.parametrize("wrong_fit,expected_fit", [
    # A seed whose retry lands on the unseeded fit exactly ...
    (9098, FIT),
    # ... and one a pixel below it: the seed's measurement bounds the
    # annotation at R to (D_s + 1) * R / s, which can exceed the D_R + 1 the
    # unseeded co_sized_request would measure by one (10727 vs 10726 here).
    (R - 10, FIT - 1),
])
def test_a_seed_that_does_not_fit_runs_one_more_attempt(tmp_path, monkeypatch,
                                                        wrong_fit, expected_fit):
    out1, _seen1 = _run1(tmp_path, monkeypatch)
    out2, seen2 = _seeded(tmp_path, monkeypatch, dict(out1["co_sizing"], fit_px=wrong_fit))
    assert [s["pass"] for s in seen2].count("model") == 2
    assert seen2[0]["px"] == wrong_fit and seen2[1]["h"] > MAX_STAGE_A_AXIS_PX
    co = out2["registration"]["co_sizing"]
    assert co["seed"]["outcome"] == cs.REFUTED_OVERRUN
    assert co["attempts"] == 2 and co["fit_px"] == expected_fit
    assert co["first_attempt"]["seeded"] is True
    assert out2["success"] is True and out2["registration"]["faults"] == []
    assert max(s["h"] for s in seen2[-2:]) <= MAX_STAGE_A_AXIS_PX
    if expected_fit == FIT:
        # Where the retry is the unseeded fit, the capture IS the unseeded one.
        assert _file(out2["sidecar_path"])["frame"] == _file(out1["sidecar_path"])["frame"]


def test_a_refuted_seed_that_still_overruns_stops_at_two_attempts(tmp_path, monkeypatch):
    """Gate 7c's discriminating fixture: 500 px of the drawn extent do not
    scale, so the retry overruns too. Two attempts, the fault kept."""
    out1, _ = _run1(tmp_path, monkeypatch)
    out2, seen2 = _seeded(tmp_path, monkeypatch, dict(out1["co_sizing"], fit_px=R - 10),
                          extra_h_px=500)
    assert [s["pass"] for s in seen2].count("model") == 2
    assert out2["registration"]["co_sizing"]["attempts"] == 2
    assert "annotation_lattice_mismatch" in [
        f["fault"] for f in _file(out2["annotation_sidecar_path"])["capture_integrity"][
            "capture_faults"]]


def test_a_seed_whose_annotation_changed_is_refuted_by_measurement(tmp_path, monkeypatch):
    """The size key cannot see the annotation: the marker now overruns by
    less (15 ft, not 21), so last run's fit is too SMALL. The seeded attempt
    fits but measures differently, and one more attempt runs: the unseeded
    path would capture at the model's own request (or a fit near it)."""
    out1, _ = _run1(tmp_path, monkeypatch)
    out2, seen2 = _seeded(tmp_path, monkeypatch, out1["co_sizing"], overrun_ft=15.0)
    unseeded, _v, _d, seen_u = run_capture(tmp_path / "unseeded", monkeypatch,
                                           overrun_ft=15.0)
    co = out2["registration"]["co_sizing"]
    assert co["seed"]["outcome"] == cs.REFUTED_CHANGED
    assert [s["pass"] for s in seen2].count("model") == 2
    final_u = unseeded["registration"]["co_sizing"].get("fit_px") or R
    final_2 = co.get("fit_px") or R
    assert 0 <= final_u - final_2 <= 2, (final_u, final_2)
    assert max(s["h"] for s in seen2[-2:]) <= MAX_STAGE_A_AXIS_PX


@settings(max_examples=400, deadline=None)
@given(R_=st.integers(2000, 10000), k=st.floats(1.0005, 3.0),
       seed_frac=st.floats(0.8, 0.9999))
def test_the_retry_is_the_unseeded_fit_or_up_to_two_px_below_and_always_fits(
        R_, k, seed_frac):
    """The measured bound of refuted_request against co_sized_request on an
    extent that scales with the request (112,776 cases: 78 % equal, the rest
    1-2 px below; never above, never over the ceiling)."""
    D_R = int(math.floor(R_ * k))
    co = co_sized_request(R_, R_, [{"requested_px": R_, "accepted_px": R_, "actual_w": R_,
                                    "actual_h": D_R, "dim_check": "mismatch"}])
    if co["state"] != "applies":
        return
    s = max(64, min(R_ - 1, int(co["fit_px"] / seed_frac)))
    retry = cs.refuted_request(R_, s, int(math.floor(s * k)), MAX_STAGE_A_AXIS_PX)
    assert 0 <= co["fit_px"] - retry["request_px"] <= 2
    assert math.floor(retry["request_px"] * k) <= MAX_STAGE_A_AXIS_PX


# --- gate 6: a changed size key ---------------------------------------------

def test_a_changed_size_key_is_not_seeded(tmp_path, monkeypatch):
    out1, seen1 = _run1(tmp_path, monkeypatch)
    prev = copy.deepcopy(out1["co_sizing"])
    prev["size_key"]["crop_snapped_uv"][0] += 1.0          # the crop moved
    out2, seen2 = _seeded(tmp_path, monkeypatch, prev)
    co = out2["registration"]["co_sizing"]
    assert co["seed"]["state"] == cs.KEY_MISMATCH
    assert "crop_snapped_uv" in co["seed"]["reason"]
    assert _passes(seen2) == _passes(seen1)                # unseeded behaviour
    assert co["attempts"] == 2


def test_the_seed_switch_off_captures_unseeded(tmp_path, monkeypatch):
    out1, seen1 = _run1(tmp_path, monkeypatch)
    out2, seen2 = _seeded(tmp_path, monkeypatch, out1["co_sizing"],
                          extra_cfg={"color_id_buffer_co_size_seed": False})
    assert out2["registration"]["co_sizing"]["seed"]["state"] == cs.DISABLED
    assert _passes(seen2) == _passes(seen1)


def test_a_view_that_fits_is_never_seeded_and_says_why(tmp_path, monkeypatch):
    out, _v, _d, seen = run_capture(tmp_path, monkeypatch, marker=False)
    out2, seen2 = _seeded(tmp_path, monkeypatch, out["co_sizing"], name="again",
                          marker=False)
    assert out2["registration"]["co_sizing"]["seed"]["state"] == cs.PREVIOUS_SINGLE_ATTEMPT
    assert _passes(seen2) == _passes(seen)


# --- the pure decision -------------------------------------------------------

KEY = {"view_id": 1, "requested_px": R}
PREV = {"final_request": "co_sized", "fit_px": FIT, "final_failure_reason": None,
        "size_key": KEY, "final_measure": {"pixel_size": FIT}}


@pytest.mark.parametrize("previous,key,enabled,state", [
    (PREV, KEY, True, cs.SEEDED),
    (None, KEY, True, cs.NO_PREVIOUS),
    (dict(PREV, final_request="model_request"), KEY, True, cs.PREVIOUS_SINGLE_ATTEMPT),
    (dict(PREV, final_failure_reason="annotation_lattice_mismatch"), KEY, True,
     cs.PREVIOUS_FAILED),
    (PREV, dict(KEY, requested_px=R + 1), True, cs.KEY_MISMATCH),
    (PREV, "the size key could not be read: ValueError", True, cs.KEY_UNREADABLE),
    (PREV, KEY, False, cs.DISABLED),
])
def test_seed_for_view_states(previous, key, enabled, state):
    seed = cs.seed_for_view(previous, key, "RUN_1", enabled=enabled)
    assert seed["state"] == state and seed["state"] in cs.SEED_STATES
    assert (seed["fit_px"] == FIT) is (state == cs.SEEDED)


def test_verify_seed_needs_the_exact_measurement():
    seed = {"expect": {"pixel_size": FIT, "model": [FIT, 6817], "annotation": [FIT, 9999]}}
    assert cs.verify_seed(seed, copy.deepcopy(seed["expect"]))[0] is True
    changed = dict(seed["expect"], annotation=[FIT, 9998])
    assert cs.verify_seed(seed, changed)[0] is False
    assert cs.verify_seed(seed, None)[0] is False


# --- which run seeds this one -------------------------------------------------

def _row(run_id, date="2026-11-13", title="FVOT_ BLDG 1_detached", finalized=True):
    return {"run_dir": "/r/" + run_id, "run_id": run_id, "date": date,
            "doc_title": title, "finalized": finalized}


def test_the_previous_run_is_the_same_documents_latest_earlier_one():
    index = [_row("20261113T1034"), _row("20261113T1100"),
             _row("20261113T1200"),                               # this run
             _row("20261113T1300"),                               # later: not previous
             _row("20261113T1150", finalized=False),
             _row("20261113T1140", title="Other model_detached"),
             _row("20261114T0900", date="2026-11-14")]
    prev = cs.previous_capture_run(index, "FVOT_ BLDG 1_detached", "2026-11-13",
                                   "20261113T1200")
    assert prev["run_id"] == "20261113T1100"
    # Same day, by the clock: change_skip's baseline rule (an EARLIER as-of
    # date) would find none here.
    from vop_interwoven.change_skip import series_runs
    assert series_runs(index, cs._doc_key("FVOT_ BLDG 1_detached")[1],
                       "2026-11-13") == []


def test_an_archive_series_matches_across_archive_dates():
    index = [_row("20261110T1151", date="2025-04-13",
                  title="KSRF_Hosp_Interior_AR_V57_2025-04-13_detached")]
    prev = cs.previous_capture_run(index, "KSRF_Hosp_Interior_AR_V58_2025-04-20_detached",
                                   "2025-04-20", "20261113T1200")
    assert prev["run_id"] == "20261110T1151"


def test_load_seeds_reads_the_previous_runs_run_meta(tmp_path):
    co = dict(PREV, schema=cs.SCHEMA)
    run = tmp_path / "2026-11-13__20261113T1034_R1"
    run.mkdir()
    (run / "run_meta_R1.json").write_text(json.dumps({
        "run_id": "20261113T1034_R1", "date": "2026-11-13", "finalized": True,
        "doc_title": {"state": "value", "value": "FVOT_ BLDG 1_detached"},
        "views": [{"view_id": 7, "capture_status": "success", "co_sizing": co},
                  {"view_id": 8, "capture_status": "failed", "co_sizing": co}]}))
    seeds = cs.load_seeds(str(tmp_path), "FVOT_ BLDG 1_detached",
                          {"as_of_date": "2026-11-13", "run_id": "20261113T1056_R2"}, "h")
    assert seeds["from_run_id"] == "20261113T1034_R1" and seeds["reason"] is None
    assert list(seeds["views"]) == ["7"]          # a failed view seeds nothing
    assert cs.run_summary(seeds)["views_offered"] == 1
    none = cs.load_seeds(str(tmp_path), "Another model",
                         {"as_of_date": "2026-11-13", "run_id": "20261113T1056_R2"}, "h")
    assert none["views"] == {} and "no earlier finalized run" in none["reason"]


# --- capture_run: read once per run, set on the config ------------------------

def test_capture_run_sets_the_seeds_only_under_stage_a(tmp_path, monkeypatch):
    import types
    from vop_interwoven import capture_run
    from vop_interwoven.config import Config
    calls = []
    monkeypatch.setattr(cs, "load_seeds", lambda *a: calls.append(a) or {"views": {}})
    identity = {"as_of_date": "2026-11-13", "run_id": "20261113T1200"}
    doc = types.SimpleNamespace(Title="FVOT_ BLDG 1_detached")
    off = object()                               # Stage A off: cfg untouched
    assert capture_run._co_size_seeds(doc, off, str(tmp_path), identity) is None
    cfg = Config(enable_color_id_buffer_stage_a=True)
    assert capture_run._co_size_seeds(doc, cfg, str(tmp_path), identity) == {"views": {}}
    assert cfg._stage_a_co_size_seeds == {"views": {}} and len(calls) == 1
    assert calls[0][1] == "FVOT_ BLDG 1_detached" and calls[0][3]      # a config hash
    cfg.color_id_buffer_co_size_seed = False
    assert capture_run._co_size_seeds(doc, cfg, str(tmp_path), identity) is None
    assert cfg._stage_a_co_size_seeds is None and len(calls) == 1
    # Private: never in the recorded config or its hash.
    assert "_stage_a_co_size_seeds" not in cfg.to_dict()
