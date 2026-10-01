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
