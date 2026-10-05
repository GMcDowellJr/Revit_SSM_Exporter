"""Capture-state probe round 3 (Q7): what can be checked without Revit.

The Revit half -- whether removing split regions leaves a plain view of one
segment, and whether painting the ElevationMarker element colours its text --
is the question itself and is answered by running the probe.
"""
import pytest

from tests.dynamo import probe_capture_state as probe


def test_the_default_is_round_3_and_round_2_is_still_selectable():
    assert probe.select_questions(None) == ["q7"]
    assert probe.select_questions("") == ["q7"]
    assert probe.select_questions("round2") == list(probe.ROUND2_QUESTIONS)
    assert "q7" in probe.select_questions("all")


def test_q7_views_default_and_take_in9_keys():
    params = probe.parse_inputs(["/tmp/out"])
    assert params["q7_split_views"] == [3300684]
    assert params["q7_marker_views"] == [17732958]
    params = probe.parse_inputs(["/tmp/out", None, None, None, None, None, None, None,
                                 "q7", '{"q7_split_views": [11, 12], '
                                       '"q7_marker_views": "13"}'])
    assert params["q7_split_views"] == [11, 12] and params["q7_marker_views"] == [13]


class _Manager(object):
    def __init__(self, offsets):
        self.offsets = list(offsets)
        self.removed = []

    @property
    def NumberOfSplitRegions(self):
        return len(self.offsets)

    def RemoveSplitRegion(self, index):
        self.removed.append(index)
        del self.offsets[index]

    def GetSplitRegionOffset(self, index):
        x = self.offsets[index]
        return type("P", (object,), {"X": x, "Y": 0.0, "Z": 0.0})()


class _View(object):
    def __init__(self, manager):
        self._m = manager

    def GetCropRegionShapeManager(self):
        return self._m


@pytest.mark.parametrize("keep", [0, 1, 2])
def test_removing_the_other_regions_keeps_the_one_asked_for(monkeypatch, keep):
    """Highest index first, so an index still to be removed never shifts."""
    def _tx_write(doc, name, fn, attempted, read_back, matches=None):
        fn()
        got = read_back()
        return {"read_back": got, "took_effect": matches(attempted, got)}
    monkeypatch.setattr(probe, "tx_write", _tx_write)
    manager = _Manager([10.0, 20.0, 30.0])
    rec = probe.remove_split_regions_except(
        type("Ctx", (object,), {"doc": None})(), _View(manager), keep, "t")
    assert manager.offsets == [[10.0, 20.0, 30.0][keep]]
    assert rec["read_back"]["offsets"] == [[[10.0, 20.0, 30.0][keep], 0.0, 0.0]]
    assert manager.removed == sorted(manager.removed, reverse=True)
    assert rec["took_effect"] is True and rec["read_back"]["count"] == 1


# --- 2026-10-05.1: which production, and which category names -----------------

def test_the_checkout_commit_is_read_from_a_ref_a_packed_ref_or_a_detached_head(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/topic\n")
    (git / "refs" / "heads" / "topic").write_text("abc123\n")
    assert probe.checkout_commit(str(tmp_path)) == {"head": "refs/heads/topic",
                                                    "commit": "abc123"}
    (git / "refs" / "heads" / "topic").unlink()
    (git / "packed-refs").write_text("# pack\ndef456 refs/heads/topic\n")
    assert probe.checkout_commit(str(tmp_path))["commit"] == "def456"
    (git / "HEAD").write_text("0123abcd\n")
    assert probe.checkout_commit(str(tmp_path)) == {"head": "detached", "commit": "0123abcd"}


def test_every_bic_name_with_the_categorys_value_is_listed(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("Autodesk.Revit.DB")
    fake.BuiltInCategory = types.SimpleNamespace(
        OST_Elevations=-2000535, OST_ElevationMarks=-2000200, OST_Viewers=-2000278,
        OST_Alias=-2000535, NotACategory=-2000535)
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake)
    assert sorted(probe.bic_names_for(-2000535)) == ["OST_Alias", "OST_Elevations"]
    assert probe.bic_names_for(-1) == []
