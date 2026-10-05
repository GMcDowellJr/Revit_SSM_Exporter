"""C: the view template is detached on the PRIMARY for a dependent view, and
whether it took is READ BACK -- in both passes, into the sidecar FILES.

Capture-state probe (runs 20261001T145758 / 153207): 13663964 and 11999340
are dependent views. Clearing ViewTemplateId ON THE DEPENDENT commits and
changes nothing; DisplayStyle then raises "cannot be modified". Clearing it
ON THE PRIMARY takes effect at once -- the dependent reads -1. The shipped
detach ignored Commit()'s status, never read the template back, and wrote
view_template_detached: true regardless.

The fake dependent view below behaves as the probe measured: its template IS
its primary's, and a write to its own ViewTemplateId is accepted and ignored.

Mutations this is wired to:
  * detach on the view instead of the primary -> the dependent tests go red
    (read-back 777, fault view_template_not_detached);
  * record detached=True without the read-back -> the stubborn-view tests go
    red (no fault in the FILE);
  * drop the model-pass fault -> test_..._is_a_failed_model_capture goes red.
"""
import json

from vop_interwoven import color_id_buffer
from vop_interwoven.color_id_buffer import _detach_view_template, _reattach_view_template

from tests.stage_a_capture_fakes import (
    FakeDiag, FakeElementId, FakeViewPlan, INVALID_ELEMENT_ID, install_fake_revit_db,
)
from tests.test_stage_a_annotation_pass import VIEW_ID, _run_both_passes

TEMPLATE = FakeElementId(777)
PRIMARY_ID = 4242


class _Primary(FakeViewPlan):
    def __init__(self):
        FakeViewPlan.__init__(self, PRIMARY_ID, name="Primary")
        self.ViewTemplateId = TEMPLATE

    def GetPrimaryViewId(self):
        return INVALID_ELEMENT_ID


class _Dependent(FakeViewPlan):
    """A dependent view: its template is its primary's, and a write to its
    own ViewTemplateId commits and changes nothing (the probe's finding)."""

    def __init__(self, view_id, primary):
        self._primary = primary
        FakeViewPlan.__init__(self, view_id, name="Dependent")
        self.ignored_template_writes = 0  # FakeView.__init__ writes one

    @property
    def ViewTemplateId(self):
        return self._primary.ViewTemplateId

    @ViewTemplateId.setter
    def ViewTemplateId(self, value):
        self.ignored_template_writes = getattr(self, "ignored_template_writes", 0) + 1

    def GetPrimaryViewId(self):
        return self._primary.Id


class _Stubborn(FakeViewPlan):
    """Not dependent, but a template write does not take: the detach must
    be READ BACK to know that."""

    def __init__(self, view_id):
        FakeViewPlan.__init__(self, view_id, name="Stubborn")
        object.__setattr__(self, "_tid", TEMPLATE)

    @property
    def ViewTemplateId(self):
        return self._tid

    @ViewTemplateId.setter
    def ViewTemplateId(self, value):
        pass

    def GetPrimaryViewId(self):
        return INVALID_ELEMENT_ID


class _Doc(object):
    def __init__(self, *views):
        self._v = dict((int(v.Id.IntegerValue), v) for v in views)

    def GetElement(self, eid):
        return self._v.get(int(eid.IntegerValue))


def _detach(doc, view):
    with install_fake_revit_db():
        return _detach_view_template(doc, view, "t", diag=FakeDiag(), view_id=1)


# --- the helper ------------------------------------------------------------

def test_a_dependent_views_template_is_detached_on_its_primary_and_put_back():
    primary = _Primary()
    dep = _Dependent(1, primary)
    record, handle = _detach(_Doc(primary, dep), dep)
    assert record["detached_on"] == "primary" and record["target_view_id"] == PRIMARY_ID
    assert record["primary_view_id"] == PRIMARY_ID
    assert record["orig_view_template_id"] == 777
    assert record["read_back_view_template_id"] == -1
    assert record["detached"] is True and record["fault"] is None
    assert dep.ignored_template_writes == 0
    _reattach_view_template(handle)
    assert int(dep.ViewTemplateId.IntegerValue) == 777
    assert int(primary.ViewTemplateId.IntegerValue) == 777


def test_control_an_ordinary_view_is_detached_on_itself():
    view = FakeViewPlan(1)
    view.ViewTemplateId = TEMPLATE
    record, handle = _detach(_Doc(view), view)
    assert record["detached_on"] == "view" and record["detached"] is True
    assert record["primary_view_id"]["state"] == "unavailable"  # fake has no API
    _reattach_view_template(handle)
    assert int(view.ViewTemplateId.IntegerValue) == 777


def test_a_detach_that_does_not_take_is_a_fault_not_a_claim():
    view = _Stubborn(1)
    record, handle = _detach(_Doc(view), view)
    assert record["detached"] is False
    assert record["read_back_view_template_id"] == 777
    assert record["fault"] == "view_template_not_detached"
    assert handle is not None  # it was written, so the restore still runs


def test_no_template_writes_nothing():
    view = FakeViewPlan(1)
    record, handle = _detach(_Doc(view), view)
    assert handle is None and record["detached_on"] is None
    assert record["detached"] is False and record["fault"] is None


# --- both passes, the FILES ------------------------------------------------

def _sidecar(path):
    with open(path) as handle:
        return json.load(handle)


def _faults(path):
    return [f["fault"] for f in _sidecar(path)["capture_integrity"]["capture_faults"]]


def test_both_passes_detach_a_dependent_view_on_its_primary_in_the_FILES(tmp_path, monkeypatch):
    primary = _Primary()
    dep = _Dependent(VIEW_ID, primary)
    def _run():
        return _run_both_passes(tmp_path, view=dep)
    # The harness builds its own doc; let it resolve the primary too.
    from tests import test_stage_a_annotation_pass as harness
    real_doc = harness._SizedDoc

    class _DocWithPrimary(real_doc):
        def GetElement(self, eid):
            if int(eid.IntegerValue) == PRIMARY_ID:
                return primary
            return real_doc.GetElement(self, eid)
    monkeypatch.setattr(harness, "_SizedDoc", _DocWithPrimary)
    model, anno, _geom, _doc, _view, _diag = _run()
    for result in (model, anno):
        side = _sidecar(result["sidecar_path"])
        rec = side["view_template_detach"]
        assert rec["detached_on"] == "primary", result["sidecar_path"]
        assert rec["read_back_view_template_id"] == -1
        assert side["view_template_detached"] is True
        assert "view_template_not_detached" not in _faults(result["sidecar_path"])
    # And each pass put the PRIMARY's template back.
    assert int(primary.ViewTemplateId.IntegerValue) == 777
    assert dep.ignored_template_writes == 0


def test_a_template_that_will_not_detach_is_a_failed_model_capture_in_the_FILE(tmp_path):
    model, anno, _geom, _doc, _view, _diag = _run_both_passes(
        tmp_path, view=_Stubborn(VIEW_ID))
    side = _sidecar(model["sidecar_path"])
    assert side["view_template_detached"] is False
    assert side["view_template_detach"]["read_back_view_template_id"] == 777
    assert "view_template_not_detached" in _faults(model["sidecar_path"])
    assert model["success"] is False and side["failure_reason"] == "view_template_not_detached"
    # The annotation pass: the same fault, most upstream, so it is THE reason.
    assert _faults(anno["sidecar_path"])[0] == "view_template_not_detached"
    assert anno["success"] is False


def test_control_an_ordinary_templated_view_is_clean_in_the_FILES(tmp_path):
    view = FakeViewPlan(VIEW_ID)
    view.ViewTemplateId = TEMPLATE
    model, anno, _geom, _doc, _view, _diag = _run_both_passes(tmp_path, view=view)
    for result in (model, anno):
        side = _sidecar(result["sidecar_path"])
        assert side["view_template_detach"]["detached_on"] == "view"
        assert side["view_template_detached"] is True
        assert "view_template_not_detached" not in _faults(result["sidecar_path"])
    assert int(view.ViewTemplateId.IntegerValue) == 777


# --- the registered capture reads the PRIMARY's template back too ----------

def test_the_registered_capture_reads_back_a_dependent_views_primary():
    from vop_interwoven.stage_a_registered_capture import (
        primary_view, view_state, view_state_verdict,
    )
    primary = _Primary()
    dep = _Dependent(1, primary)
    found, reason = primary_view(_Doc(primary, dep), dep)
    assert found is primary and reason is None
    assert primary_view(_Doc(primary), primary) == (None, "not a dependent view")
    before = view_state(dep, found)
    assert before["primary_view_template_id"] == {"state": "value", "value": 777}
    primary.ViewTemplateId = INVALID_ELEMENT_ID  # a restore that never happened
    verdict = view_state_verdict(before, view_state(dep, found))
    assert verdict["primary_view_template_id"]["status"] == "not_restored"
    # Control: an ordinary view carries no primary key to be "unverified".
    assert "primary_view_template_id" not in view_state(FakeViewPlan(2))


# --- Codex, PR #226: a pass that fails after the detach reattaches -----------
#
# The detach COMMITS (on the PRIMARY for a dependent view); a raise before the
# restore used to leave every view of that primary detached in the frame-B
# fallback, which has no TransactionGroup rollback behind it. Mutations:
# deleting the pre-suppress guard -> the GetFilters test red; deleting the
# suppress-handler reattach -> the SetCategoryHidden test red; deleting the
# annotation guard -> the last test red.

import pytest  # noqa: E402


class _DependentFailing(_Dependent):
    def __init__(self, view_id, primary, fail):
        _Dependent.__init__(self, view_id, primary)
        self._fail = fail

    def GetFilters(self):
        if self._fail == "filters":
            raise RuntimeError("GetFilters refused (fake)")
        return _Dependent.GetFilters(self)

    def SetCategoryHidden(self, cat_id, value):
        if self._fail == "hide" and value:
            raise RuntimeError("SetCategoryHidden refused (fake)")
        return _Dependent.SetCategoryHidden(self, cat_id, value)


def _primary_doc(monkeypatch, primary):
    from tests import test_stage_a_annotation_pass as harness
    real_doc = harness._SizedDoc

    class _DocWithPrimary(real_doc):
        def GetElement(self, eid):
            if int(eid.IntegerValue) == PRIMARY_ID:
                return primary
            return real_doc.GetElement(self, eid)
    monkeypatch.setattr(harness, "_SizedDoc", _DocWithPrimary)


@pytest.mark.parametrize("fail", ["filters", "hide"])
def test_a_model_pass_that_fails_after_the_detach_reattaches_the_primary(
        tmp_path, monkeypatch, fail):
    primary = _Primary()
    view = _DependentFailing(VIEW_ID, primary, fail)
    _primary_doc(monkeypatch, primary)
    with pytest.raises(RuntimeError, match="refused"):
        _run_both_passes(tmp_path, view=view)
    assert int(primary.ViewTemplateId.IntegerValue) == 777


def test_an_annotation_pass_that_fails_after_the_detach_reattaches(tmp_path, monkeypatch):
    primary = _Primary()
    view = _Dependent(VIEW_ID, primary)
    _primary_doc(monkeypatch, primary)

    def _boom(*a, **k):
        raise RuntimeError("category state refused (fake)")
    monkeypatch.setattr(color_id_buffer, "_model_category_hidden_state", _boom)
    with pytest.raises(RuntimeError, match="refused"):
        _run_both_passes(tmp_path, view=view)
    assert int(primary.ViewTemplateId.IntegerValue) == 777


def test_a_primary_that_will_not_read_is_warned_and_an_ordinary_view_is_not():
    from vop_interwoven.stage_a_registered_capture import primary_view
    from tests.stage_a_capture_fakes import FakeDiag

    class _Broken(FakeViewPlan):
        def GetPrimaryViewId(self):
            raise RuntimeError("no primary (fake)")
    diag = FakeDiag()
    found, reason = primary_view(_Doc(), _Broken(1), diag=diag, view_id=1)
    assert found is None and "no primary" in reason
    assert [w["callsite"] for w in diag.warnings] == ["registered_primary_view"]
    quiet = FakeDiag()
    primary = _Primary()
    assert primary_view(_Doc(primary), primary, diag=quiet) == (None, "not a dependent view")
    assert quiet.warnings == []


# --- Codex, PR #226 (round 2): the WHOLE stretch from detach to Start --------
#
# The first guard stopped at the solid-pattern lookup; the DisplayStyle read
# and suppress_tx.Start() came after it, in both passes. And the reattach
# itself could raise out of its own handler (an unguarded Start) or claim
# success on a Commit that returned RolledBack. Mutations: narrowing either
# pass's guard back to where it was turns the Start / DisplayStyle cases red;
# taking Start out of the helper's try, or dropping the Committed check or
# the read-back, turns the helper cases red.

from tests import stage_a_capture_fakes as fakes  # noqa: E402


class _DependentDisplayFails(_Dependent):
    """DisplayStyle raises once the template is detached -- i.e. only on the
    read the capture makes AFTER the detach."""

    @property
    def DisplayStyle(self):
        if int(self._primary.ViewTemplateId.IntegerValue) == -1:
            raise RuntimeError("DisplayStyle refused (fake)")
        return getattr(self, "_display_style", None)

    @DisplayStyle.setter
    def DisplayStyle(self, value):
        object.__setattr__(self, "_display_style", value)


def test_a_model_pass_whose_display_style_read_fails_reattaches(tmp_path, monkeypatch):
    primary = _Primary()
    view = _DependentDisplayFails(VIEW_ID, primary)
    _primary_doc(monkeypatch, primary)
    with pytest.raises(RuntimeError, match="DisplayStyle refused"):
        _run_both_passes(tmp_path, view=view)
    assert int(primary.ViewTemplateId.IntegerValue) == 777


def _start_refused_for(monkeypatch, name_part):
    real_start = fakes.FakeTransaction.Start

    def _start(self):
        if name_part in self.name:
            raise RuntimeError("Start refused (fake): {0}".format(self.name))
        return real_start(self)
    monkeypatch.setattr(fakes.FakeTransaction, "Start", _start)


@pytest.mark.parametrize("which", ["VOP Stage A SUPPRESS", "VOP Stage A ANNO SUPPRESS"])
def test_a_suppress_transaction_that_will_not_start_reattaches(tmp_path, monkeypatch, which):
    primary = _Primary()
    view = _Dependent(VIEW_ID, primary)
    _primary_doc(monkeypatch, primary)
    _start_refused_for(monkeypatch, which)
    with pytest.raises(RuntimeError, match="Start refused"):
        _run_both_passes(tmp_path, view=view)
    assert int(primary.ViewTemplateId.IntegerValue) == 777


# --- the helper itself ---------------------------------------------------------

def _reattach(view, monkeypatch=None, **patch):
    for name, fn in patch.items():
        monkeypatch.setattr(fakes.FakeTransaction, name, fn)
    diag = FakeDiag()
    with install_fake_revit_db():
        ok = color_id_buffer._reattach_after_failure(
            None, {"target": view, "orig": TEMPLATE}, diag=diag, view_id=1)
    return ok, diag


def _detached_primary():
    primary = _Primary()
    primary.ViewTemplateId = INVALID_ELEMENT_ID
    return primary


def test_control_a_reattach_that_commits_and_reads_back_is_true():
    primary = _detached_primary()
    ok, diag = _reattach(primary)
    assert ok is True and diag.errors == []
    assert int(primary.ViewTemplateId.IntegerValue) == 777


def test_a_reattach_whose_start_raises_returns_false_and_does_not_raise(monkeypatch):
    def _start(self):
        raise RuntimeError("document is in failure handling (fake)")
    ok, diag = _reattach(_detached_primary(), monkeypatch, Start=_start)
    assert ok is False
    assert len(diag.errors) == 1 and "start the Transaction" in diag.errors[0]["message"]


def test_a_reattach_whose_commit_rolls_back_is_not_a_success(monkeypatch):
    def _commit(self):
        self._ended = True
        return fakes.FakeTransactionStatus.RolledBack
    ok, diag = _reattach(_detached_primary(), monkeypatch, Commit=_commit)
    assert ok is False
    assert len(diag.errors) == 1 and "commit" in diag.errors[0]["message"]


def test_a_reattach_that_does_not_read_back_is_not_a_success():
    view = _Stubborn(5)
    object.__setattr__(view, "_tid", INVALID_ELEMENT_ID)   # detached; writes ignored
    ok, diag = _reattach(view)
    assert ok is False
    assert len(diag.errors) == 1 and "read ViewTemplateId back" in diag.errors[0]["message"]
