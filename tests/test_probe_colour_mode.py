"""The colour-mode probe (tests/dynamo/probe_colour_mode.py), offline.

Gate 2: the probe never saves the document, every probe transaction is
rolled back, and the Object Styles state after the probe equals the state
before it. The probe's own Object Styles write (``apply_object_styles``) is
driven against fake categories inside the probe's own ``rolled_back_group``,
over a fake TransactionGroup whose rollback restores what changed under it,
and the probe's own comparison (``object_styles_diff``) must come back
empty. The CONTROL is a group that does not restore: the comparison must then
name every category, or a comparison that always says "restored" would pass.
"""
import ast
import os

import pytest

from tests.dynamo import probe_colour_mode as probe

PROBE_FILE = os.path.join(os.path.dirname(probe.__file__), "probe_colour_mode.py")


class _Id(object):
    def __init__(self, i):
        self.IntegerValue = i


class _Color(object):
    def __init__(self, r, g, b, valid=True):
        self.Red, self.Green, self.Blue, self.IsValid = r, g, b, valid


class _Cat(object):
    def __init__(self, i, name, rgb=None, subs=()):
        self.Id = _Id(i)
        self.Name = name
        self.LineColor = _Color(*rgb) if rgb else _Color(0, 0, 0, valid=False)
        self.SubCategories = list(subs)


class _Status(object):
    Started, Committed, RolledBack, Failed = "Started", "Committed", "RolledBack", "Failed"


def _api(categories, restores=True):
    """A fake Revit namespace whose TransactionGroup snapshots every
    category's LineColor on Start and puts it back on RollBack."""
    log = []

    def _walk():
        for c in categories:
            yield c
            for s in c.SubCategories:
                yield s

    class _Group(object):
        def __init__(self, doc, name):
            self.name = name

        def Start(self):
            self.saved = [(c, c.LineColor) for c in _walk()]
            log.append(("group_start", self.name))
            return _Status.Started

        def RollBack(self):
            log.append(("group_rollback", self.name))
            if restores:
                for c, colour in self.saved:
                    c.LineColor = colour
            return _Status.RolledBack

    class _Tx(object):
        def __init__(self, doc, name):
            self.name = name

        def Start(self):
            log.append(("tx_start", self.name))

        def Commit(self):
            log.append(("tx_commit", self.name))
            return _Status.Committed

    api = type("Api", (), {"TransactionGroup": _Group, "Transaction": _Tx,
                           "TransactionStatus": _Status, "Color": _Color})
    return api, log


def _categories():
    return [_Cat(-2000011, "Walls", (0, 0, 0), subs=[_Cat(-1, "Common Edges", (10, 10, 10))]),
            _Cat(-2000080, "Furniture", None),
            _Cat(-2001000, "Casework", (128, 128, 128))]


def test_object_styles_are_restored_by_the_outer_rollback():
    cats = _categories()
    api, log = _api(cats)
    before = probe.read_object_styles(cats)
    colours = probe.label_colours([c.Id.IntegerValue for c in cats],
                                  [(200, 0, 0), (0, 200, 0), (0, 0, 200)])
    with probe.rolled_back_group(None, "outer", api):
        probe.apply_object_styles(None, cats, colours, api)
        during = probe.read_object_styles(cats)
    assert probe.object_styles_diff(before, during)          # it did change them
    assert during["-1"]["rgb"] == colours[-2000011]   # subcategory follows its parent
    assert probe.object_styles_diff(before, probe.read_object_styles(cats)) == []
    assert log[-1] == ("group_rollback", "outer")


def test_control_a_group_that_does_not_restore_is_caught():
    cats = _categories()
    api, _log = _api(cats, restores=False)
    before = probe.read_object_styles(cats)
    colours = probe.label_colours([c.Id.IntegerValue for c in cats],
                                  [(200, 0, 0), (0, 200, 0), (0, 0, 200)])
    with probe.rolled_back_group(None, "outer", api):
        probe.apply_object_styles(None, cats, colours, api)
    diffs = probe.object_styles_diff(before, probe.read_object_styles(cats))
    assert sorted(d["category_id"] for d in diffs) == sorted(
        ["-2000011", "-1", "-2000080", "-2001000"])


def test_the_group_rolls_back_when_the_body_raises_and_refuses_a_failed_rollback():
    api, log = _api([])
    with pytest.raises(ValueError):
        with probe.rolled_back_group(None, "g", api):
            raise ValueError("inside")
    assert log[-1] == ("group_rollback", "g")

    class _Bad(api.TransactionGroup):
        def RollBack(self):
            return _Status.Failed
    bad = type("Api", (), {"TransactionGroup": _Bad, "TransactionStatus": _Status})
    with pytest.raises(RuntimeError):
        with probe.rolled_back_group(None, "g", bad):
            pass


def test_the_probe_file_never_saves_and_never_commits_a_group():
    tree = ast.parse(open(PROBE_FILE).read())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert not called & {"Save", "SaveAs", "Close", "Assimilate", "SaveToProjectAsImage"}
    # A TransactionGroup is only ever made through rolled_back_group.
    groups = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "TransactionGroup"]
    assert len(groups) == 1


def test_labels_palette_and_type_plan():
    assert probe.label_colours([3, 1, 3], [(1, 2, 3), (4, 5, 6)]) == {1: [1, 2, 3], 3: [4, 5, 6]}
    with pytest.raises(ValueError):
        probe.label_colours([1, 2], [(1, 2, 3)])
    labels = {10: {"category": -2000011, "type": 500}, 11: {"category": -2000011, "type": 500},
              12: {"category": -2001000, "type": 600}, 13: {"category": None, "type": 7},
              14: {"category": -2000080, "type": None}}
    assert probe.type_filter_plan(labels) == [(500, -2000011), (600, -2001000)]


def test_select_arms_and_baseline_ids():
    assert probe.select_arms("all") == list(probe.ARMS)
    assert probe.select_arms("type, as_is") == ["as_is", "type"]
    with pytest.raises(ValueError):
        probe.select_arms("greyscale")
    assert probe.baseline_host_ids({"near_face_w_map": {"host": {"7": {}, "3": {}, "x": None}}}) \
        == [3, 7]


def test_the_probe_record_feeds_the_attribution_tool():
    """The keys the probe writes are the keys the tool reads."""
    from tools import probe_colour_mode_attribution as pca
    src = open(PROBE_FILE).read()
    for key in ("element_labels", "label_colour", "tiff_path", "timings_ms", "precedence"):
        assert ('"{0}"'.format(key) in src or "{0}=".format(key) in src), key
    assert set(pca.ARMS) == set(probe.ARMS)
    assert set(pca.LABELLED_ARMS) == {"category", "type"}
