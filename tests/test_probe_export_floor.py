"""The export-floor A/B probe's pure parts: the plan, the verdict on each of
the three readings, and the net-watch log (tests/dynamo/probe_export_floor.py).
"""
import pytest

from tests.dynamo import probe_export_floor as probe


def test_the_plan_runs_every_gap_per_view_per_arm():
    plan = probe.build_plan([1, 2], gaps_s=(0, 30), staging="both")
    assert len(plan) == 2 * 2 * 3
    assert [(p["arm"], p["view_id"], p["repeat"], p["gap_s"]) for p in plan[:3]] == [
        ("temp", 1, 0, 0.0), ("temp", 1, 1, 0.0), ("temp", 1, 2, 30.0)]
    assert {p["arm"] for p in plan} == set(probe.STAGING_ARMS)
    assert {p["arm"] for p in probe.build_plan([1], staging="temp")} == {"temp"}
    with pytest.raises(ValueError):
        probe.build_plan([1], staging="network")
    with pytest.raises(ValueError):
        probe.build_plan([])


def _simulate(rule, gaps=probe.DEFAULT_GAPS_S, views=(1, 2), arms=("temp", "run_folder")):
    """Run the plan against a stall ``rule(state, step) -> slow``."""
    t, exports, state = 0.0, [], {"last_stall_end": -1e9, "seen": set()}
    for step in probe.build_plan(list(views), gaps, "both" if len(arms) == 2 else arms[0]):
        t += step["gap_s"]
        slow = rule(state, step, t)
        ms = 21500.0 if slow else 400.0
        parts = {"export_image": ms - 50.0, "move": 20.0, "read_dims": 1.0}
        exports.append(dict(step, t_start=t, ms=ms, parts_ms=parts))
        state["seen"].add((step["arm"], step["view_id"]))
        t += ms / 1000.0
        if slow:
            state["last_stall_end"] = t
    return probe.verdict(probe.annotate(exports))


def test_a_clock_reads_as_c():
    v = _simulate(lambda s, step, t: t - s["last_stall_end"] > 28.0)
    assert v["reading"] == "c_clock", v
    assert v["stall_part"] == "export_image"


def test_first_export_of_each_view_reads_as_a():
    v = _simulate(lambda s, step, t: (step["arm"], step["view_id"]) not in s["seen"])
    assert v["reading"] == "a_first_export_of_view", v
    assert v["first_export_slow"] == 1.0


def test_every_export_reads_as_b():
    v = _simulate(lambda s, step, t: True)
    assert v["reading"] == "b_every_export", v


def test_one_arm_only_shows_in_slow_by_arm():
    v = _simulate(lambda s, step, t: step["arm"] == "temp")
    assert v["slow_by_arm"] == {"run_folder": 0.0, "temp": 1.0}
    assert v["reading"] == "mixed"


def test_no_long_gaps_is_inconclusive():
    v = _simulate(lambda s, step, t: False, gaps=(0, 0, 5))
    assert v["reading"].startswith("inconclusive")


def test_net_watch_lines_are_parsed_and_counted():
    lines = ["2026-11-19T10:00:00.0\t4321\tRevit\t10.0.0.9\t445\n",
             "2026-11-19T10:00:00.5\t4321\tRevit\t10.0.0.9\t445\n",
             "2026-11-19T10:00:00.5\t888\tspoolsv\t10.0.0.7\t135\n",
             "garbage\n", ""]
    entries = probe.parse_net_watch(lines)
    assert len(entries) == 3
    assert probe.summarize_net_watch(entries)[0] == {
        "process": "Revit", "remote": "10.0.0.9", "port": "445", "samples": 2}


def test_options_accept_a_dict_or_json():
    assert probe.parse_options('{"staging": "temp"}') == {"staging": "temp"}
    assert probe.parse_options({"a": 1}) == {"a": 1}
    assert probe.parse_ids("1, 2;3") == [1, 2, 3]
