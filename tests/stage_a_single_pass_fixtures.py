"""Fixtures for the single-pass Stage A capture, and the S0 baseline they are held to.

Three registered captures on the fake DB, each the repo's existing fixture:

  * ``drafting``   -- tests/test_stage_a_annotation_only_views._run_registered_no_crop
    ("DraftingView"): a view with no crop region whose every element is owned
    by the view (membership model 0, annotation 3);
  * ``model``      -- tests/test_stage_a_registered_capture._run: a cropped plan
    with model AND annotation members (model 3, annotation 2);
  * ``model_only`` -- the same plan with its two view-owned annotations removed
    (model 3, annotation 0).

The S0 baseline (``tests/fixtures/stage_a_single_pass_s0/``) is each capture's
two sidecars as written at 8e1c1fb, before the single-pass change, NORMALISED:
wall-clock timings, process memory and file paths removed (``normalise``).
Regenerate only on a tree that has no single-pass code:

    python -m tests.stage_a_single_pass_fixtures --write-s0
"""
import contextlib
import json
import os
import re
import sys
from pathlib import Path

S0_DIR = Path(__file__).resolve().parent / "fixtures" / "stage_a_single_pass_s0"
FIXTURES = ("drafting", "model", "model_only")

# A key whose value is a wall-clock or memory measurement, or a path: neither
# is a property of the capture, so neither can be compared across two runs.
_VOLATILE_KEY = re.compile(r"(^|_)(ms|elapsed_ms|timings_ms|timings|process_memory|"
                           r"rollback_ms|tiff_path|model_pass_tiff_path|path)$")
_VOLATILE_EXACT = {"slowest", "elapsed_ms", "read_ms", "cost_ms", "timings_ms"}


def normalise(value):
    """``value`` with every volatile key removed, recursively."""
    if isinstance(value, dict):
        return dict((k, normalise(v)) for k, v in value.items()
                    if not (_VOLATILE_KEY.search(str(k)) or k in _VOLATILE_EXACT))
    if isinstance(value, list):
        return [normalise(v) for v in value]
    return value


# The view-marker categories the membership rule resolves
# (revit.annotation.STAGE_A_VIEW_REFERENCE_BIC_NAMES) that the shared fake
# BuiltInCategory lacks. Without them the rule reports itself incomplete and
# the single-pass predicate -- correctly -- runs both passes. Set only while a
# fixture runs, so no other test's fake surface changes. Real values.
MARKER_CATEGORIES = {"OST_Elev": -2000535, "OST_ElevationMarks": -2006045,
                     "OST_SectionHeads": -2000200, "OST_CalloutHeads": -2000196,
                     "OST_ReferenceViewer": -2000197}


@contextlib.contextmanager
def marker_categories():
    from tests.stage_a_capture_fakes import FakeBuiltInCategory
    added = [k for k in MARKER_CATEGORIES if not hasattr(FakeBuiltInCategory, k)]
    for k in added:
        setattr(FakeBuiltInCategory, k, MARKER_CATEGORIES[k])
    try:
        yield
    finally:
        for k in added:
            delattr(FakeBuiltInCategory, k)


def run_fixture(name, tmp_path, monkeypatch=None):
    """``(out, view, doc, exports, diag)`` of one fixture's registered capture,
    with the membership rule complete (``marker_categories``)."""
    with marker_categories():
        return _run_fixture(name, tmp_path)


def _run_fixture(name, tmp_path):
    from tests import test_stage_a_annotation_only_views as dv
    from tests import test_stage_a_registered_capture as mv
    if name == "drafting":
        out, view, _frame, exports = dv._run_registered_no_crop(tmp_path, "DraftingView")
        return out, view, None, exports, None
    if name == "model":
        return mv._run(tmp_path)
    if name == "datum_only":
        # model_only plus one GRID: ownerless, placed in the annotation pass
        # by category -- the view's only annotation ink.
        from tests.stage_a_capture_fakes import FakeBuiltInCategory, FakeCategory, FakeElement
        grids = FakeCategory("Grids", FakeBuiltInCategory.OST_Grids, cat_type="Annotation")
        return _model_only(mv, tmp_path, extra=[FakeElement(4001, grids)])
    if name == "unresolved_only":
        # model_only plus one element whose OwnerViewId will not read.
        from tests.stage_a_capture_fakes import FakeElement

        class _Unreadable(FakeElement):
            def __getattribute__(self, attr):
                if attr == "OwnerViewId":
                    raise RuntimeError("OwnerViewId unreadable (fake)")
                return FakeElement.__getattribute__(self, attr)
        return _model_only(mv, tmp_path, extra=[_Unreadable(4002)])
    if name == "model_only":
        real = mv._elements

        def _no_annotation():
            return [e for e in real() if int(e.OwnerViewId.IntegerValue) == -1]
        mv._elements = _no_annotation
        try:
            return mv._run(tmp_path)
        finally:
            mv._elements = real
    raise ValueError(name)


def _model_only(mv, tmp_path, extra):
    real = mv._elements

    def _no_annotation():
        return [e for e in real() if int(e.OwnerViewId.IntegerValue) == -1]
    mv._elements = _no_annotation
    try:
        return mv._run(tmp_path, extra_elements=extra)
    finally:
        mv._elements = real


def sidecars(out):
    """``{"model": dict or None, "annotation": dict or None}`` as written."""
    found = {}
    for which, key in (("model", "sidecar_path"), ("annotation", "annotation_sidecar_path")):
        path = out.get(key)
        found[which] = None
        if path and os.path.exists(path):
            with open(path) as handle:
                found[which] = json.load(handle)
    return found


def s0(name):
    """The S0 baseline of one fixture: ``{"model": ..., "annotation": ...}``."""
    with open(str(S0_DIR / (name + ".json"))) as handle:
        return json.load(handle)


def _write_s0():
    import tempfile
    S0_DIR.mkdir(parents=True, exist_ok=True)
    for name in FIXTURES:
        with tempfile.TemporaryDirectory() as tmp:
            out = run_fixture(name, tmp)[0]
            record = dict((k, normalise(v)) for k, v in sidecars(out).items())
            record["_membership"] = out["registration"]["membership"]
        with open(str(S0_DIR / (name + ".json")), "w") as handle:
            json.dump(record, handle, indent=1, sort_keys=True)
        print("wrote", name, record["_membership"])


if __name__ == "__main__":
    if "--write-s0" in sys.argv:
        _write_s0()
