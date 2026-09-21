"""Stage A step 3 -- the seven defects PR #211's SECOND review round found.

Sourced to https://github.com/GMcDowellJr/Revit_SSM_Exporter/pull/211
(chatgpt-codex-connector, 2026-09-21). All seven were mine, and four of
them were one defect wearing four hats:

    failure_reason was an ALLOWLIST OF REMEMBERED FAILURES rather than a
    statement of what the capture promises.

So the fix is not four more branches. It is `capture_faults` -- one list,
one row per promise, ordered causally -- and the four cases below are its
rows rather than its motivation. A promise with no row is now visible as an
absence instead of as a silent success.

The other three are structural and independent:
  - the annotation call was unguarded AFTER the model result was published
  - annotation failures never reached the run's failure accounting
  - batch relocation rewrote the model paths and not the annotation ones

CONTROL: ``test_a_clean_capture_reports_no_faults`` pins that the fault
list is reachable only by a real fault. Without it every assertion here
would also pass against an implementation that faulted unconditionally.
"""
import os
import types

import pytest

import vop_interwoven.color_id_buffer as color_id_buffer
from vop_interwoven import pipeline, streaming
from vop_interwoven.config import Config

from tests.stage_a_capture_fakes import (
    FakeDiag,
    FakeViewPlan,
    install_fake_revit_db,
)
from tests.test_stage_a_annotation_pass import (
    ANNO_CAT,
    GRID_CAT,
    GRID_HEAD_CAT,
    LINES_CAT,
    MODEL_CAT,
    VIEW_ID,
    _SizedDoc,
    _elements,
    _raster,
    _run_both_passes,
)
from tests.test_stage_a_annotation_pass_wiring import GEOM


def _capture(tmp_path, view=None, geom=None, doc=None, elements=None):
    elements = _elements() if elements is None else elements
    view = FakeViewPlan(view_id=VIEW_ID) if view is None else view
    if doc is None:
        doc = _SizedDoc(elements=elements, link_instances=[],
                        categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT,
                                    GRID_HEAD_CAT])
    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()
    with install_fake_revit_db():
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, view, cfg, dict(GEOM if geom is None else geom), diag=diag,
            raster=_raster(), elements=elements,
        )
    return result, diag


def _faults(result):
    return [f["fault"] for f in result["metadata"]["capture_faults"]]


# --- CONTROL ---------------------------------------------------------------

def test_a_clean_capture_reports_no_faults(tmp_path):
    """Without this, an unconditionally-faulting implementation passes
    every test below."""
    result, _diag = _capture(tmp_path)
    assert result["metadata"]["capture_faults"] == []
    assert result["failure_reason"] is None
    assert result["success"] is True


# --- the four rows -------------------------------------------------------

def test_a_failed_collection_fails_the_capture(tmp_path):
    """An empty TIFF from a collector that threw is not a view with no
    annotations, and downstream cannot tell them apart."""
    class _NoCollect(_SizedDoc):
        pass

    doc = _NoCollect(elements=[], link_instances=[],
                     categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT,
                                 GRID_HEAD_CAT])
    cfg = Config()
    cfg.debug_dump_path = str(tmp_path)
    diag = FakeDiag()

    from tests.stage_a_capture_fakes import FakeCollector

    class _Boom(FakeCollector):
        """Refuses ONLY the view-scoped form.

        _get_solid_pattern_id builds a document-wide collector in the same
        capture and is not inside the guarded block, so a fake that refuses
        every construction tests the wrong failure -- the function raises
        before it ever reaches the collection this is about.
        """

        def __init__(self, source, _view_id=None):
            if _view_id is not None:
                raise RuntimeError("collector refused")
            FakeCollector.__init__(self, source, _view_id)

    with install_fake_revit_db() as fake_db:
        fake_db.FilteredElementCollector = _Boom
        result = color_id_buffer.export_annotation_color_id_buffer_view(
            doc, FakeViewPlan(view_id=VIEW_ID), cfg, dict(GEOM), diag=diag,
            raster=_raster(), elements=None,
        )

    assert "annotation_collection_failed" in _faults(result)
    assert result["success"] is False
    assert result["failure_reason"] == "annotation_collection_failed"
    # And the zero counts are marked unavailable, not read as "no annotations".
    assert result["metadata"]["membership"]["status"] == "unavailable"


def test_an_unapplied_frame_b_crop_fails_the_capture(tmp_path):
    """FitToPage's extent is not frame B; the diagnostic already says so.

    Returning it as a successful annotation buffer hands downstream an image
    whose scale and origin are unknown while the sidecar's registration
    block describes a rectangle that was never rendered.
    """
    # The real trigger is crop_box_from_uv_bounds returning None -- a view
    # with no crop box -- not the CropBox attribute being unset, which the
    # capture reads for RESTORE and is a different thing entirely.
    import vop_interwoven.revit.view_basis as view_basis
    original = view_basis.crop_box_from_uv_bounds
    view_basis.crop_box_from_uv_bounds = lambda *a, **k: None
    try:
        result, _diag = _capture(tmp_path)
    finally:
        view_basis.crop_box_from_uv_bounds = original

    assert "annotation_frame_not_applied" in _faults(result)
    assert result["success"] is False
    assert result["metadata"]["registration"]["rendered_uv"] is None


def test_an_off_lattice_annotation_export_fails_the_capture(tmp_path):
    """The post-export dimension check compares the file against what Revit
    ACCEPTED, not against what the lattice requires -- so it can pass on a
    size that breaks registration outright."""
    class _Halving(_SizedDoc):
        def ExportImage(self, opts):
            # Revit accepts half the request; the file matches THAT.
            opts.PixelSize = int(opts.PixelSize) // 2
            _SizedDoc.ExportImage(self, opts)

    elements = _elements()
    doc = _Halving(elements=elements, link_instances=[],
                   categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT,
                               GRID_HEAD_CAT])
    result, _diag = _capture(tmp_path, doc=doc, elements=elements)

    assert "annotation_lattice_mismatch" in _faults(result)
    assert result["success"] is False


def test_an_off_lattice_MODEL_export_fails_the_annotation_capture(tmp_path):
    """Registration is a claim about TWO images.

    This pass cannot see the model export except through what that pass
    published, and this is the point where both halves are finally known.
    """
    geom = dict(GEOM)
    geom["model_accepted_px"] = int(GEOM["crop_px"][0]) // 2
    geom["requested_px"] = int(GEOM["crop_px"][0])

    result, _diag = _capture(tmp_path, geom=geom)

    faults = _faults(result)
    assert "annotation_lattice_mismatch" in faults
    detail = [f["detail"] for f in result["metadata"]["capture_faults"]
              if f["fault"] == "annotation_lattice_mismatch"][0]
    assert "MODEL export" in detail
    assert result["success"] is False


def test_an_unverifiable_read_back_fails_the_capture(tmp_path):
    """"Could not certify" is not "certified clean".

    An unreadable override may still be painted, and nothing later looks
    again -- so this is the only place the consequence can land.
    """
    class _Blind(FakeViewPlan):
        def __init__(self, view_id):
            FakeViewPlan.__init__(self, view_id)
            self._painted = False

        def GetElementOverrides(self, eid):
            if self._painted and int(eid.IntegerValue) == 2001:
                class _Boom(object):
                    @property
                    def ProjectionLineColor(self):
                        raise RuntimeError("cannot read")

                    def __getattr__(self, name):
                        return None
                return _Boom()
            return FakeViewPlan.GetElementOverrides(self, eid)

        def SetElementOverrides(self, eid, ogs):
            self._painted = True
            FakeViewPlan.SetElementOverrides(self, eid, ogs)

    result, _diag = _capture(tmp_path, view=_Blind(view_id=VIEW_ID))

    check = result["metadata"]["override_restore_check"]
    assert check["unreadable_count"] >= 1
    assert check["still_set_count"] == 0          # the OLD condition is clean
    assert "annotation_overrides_unverified" in _faults(result)
    assert result["success"] is False


def test_all_faults_are_recorded_not_only_the_reported_one(tmp_path):
    """failure_reason keeps its single-string contract, so the list is what
    stops one symptom hiding the others."""
    class _Halving(_SizedDoc):
        def ExportImage(self, opts):
            opts.PixelSize = int(opts.PixelSize) // 2
            _SizedDoc.ExportImage(self, opts)

    elements = _elements()
    doc = _Halving(elements=elements, link_instances=[],
                   categories=[MODEL_CAT, ANNO_CAT, LINES_CAT, GRID_CAT,
                               GRID_HEAD_CAT])
    import vop_interwoven.revit.view_basis as view_basis
    original = view_basis.crop_box_from_uv_bounds
    view_basis.crop_box_from_uv_bounds = lambda *a, **k: None
    try:
        result, _diag = _capture(tmp_path, doc=doc, elements=elements)
    finally:
        view_basis.crop_box_from_uv_bounds = original

    faults = _faults(result)
    assert "annotation_frame_not_applied" in faults
    assert "annotation_lattice_mismatch" in faults
    # Causal order: the earliest thing that went wrong is what is reported.
    assert result["failure_reason"] == "annotation_frame_not_applied"


# --- structural: the unguarded call ---------------------------------------

def _run_pipeline(monkeypatch, tmp_path, anno):
    from tests.test_stage_a_skips_the_model_pass import (
        VIEW_ID as PIPE_VIEW_ID, _fake_doc, _fake_revit_db, _fake_view,
    )

    view = _fake_view()
    doc = _fake_doc(view)

    def _model(doc_, view_, elements_, cfg_, **kwargs):
        out = kwargs.get("geometry_out")
        if out is not None:
            out.update(GEOM)
        return {"view_id": PIPE_VIEW_ID, "view_name": "L1 Plan", "success": True,
                "stage": "color_id_buffer_stage_a"}

    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_color_id_buffer_view", _model)
    monkeypatch.setattr(
        "vop_interwoven.color_id_buffer.export_annotation_color_id_buffer_view", anno)
    monkeypatch.setattr(pipeline, "render_model_front_to_back",
                        lambda *a, **k: {"timings": {}})
    monkeypatch.setattr(
        pipeline, "init_view_raster",
        lambda doc_, view_, cfg_, diag=None: types.SimpleNamespace(
            W=8, H=8, cell_size_ft=1.0, bounds_xy=None, view_basis=None))
    monkeypatch.setattr(pipeline, "collect_view_elements", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rasterize_annotations", lambda *a, **k: {})

    cfg = Config(enable_color_id_buffer_stage_a=True,
                 color_id_buffer_annotation_pass=True,
                 view_cache_enabled=False, perf_collect_timings=False)
    cfg.output_dir = str(tmp_path)
    with _fake_revit_db():
        return pipeline.process_document_views(doc, [PIPE_VIEW_ID], cfg)


def test_an_annotation_exception_is_nested_not_discarded(monkeypatch, tmp_path):
    """The model result is ALREADY published when the call is made.

    An unwind here reached the outer per-view handler, which appends its own
    stub as a SECOND entry -- and consumers take results[0], so the failure
    was discarded entirely while the model capture read clean.
    """
    def _raises(doc_, view_, cfg_, geom_, **kwargs):
        raise RuntimeError("ExportImage blew up")

    results = _run_pipeline(monkeypatch, tmp_path, _raises)

    assert len(results) == 1
    entry = results[0]
    assert entry["success"] is True                       # the model capture stands
    assert entry["annotation_pass_success"] is False      # and this is not lost
    assert entry["annotation_pass_failure_reason"] == "annotation_pass_raised"
    assert "ExportImage blew up" in entry["annotation_pass"]["error"]


def test_a_returning_annotation_pass_is_unaffected(monkeypatch, tmp_path):
    """CONTROL for the guard: the normal path must not change."""
    def _ok(doc_, view_, cfg_, geom_, **kwargs):
        return {"view_id": VIEW_ID, "success": True, "failure_reason": None,
                "stage": "color_id_buffer_stage_a_annotation",
                "tiff_path": "/a.tiff", "sidecar_path": "/a.json"}

    results = _run_pipeline(monkeypatch, tmp_path, _ok)
    assert len(results) == 1
    assert results[0]["annotation_pass_success"] is True


# --- structural: run-level accounting -------------------------------------

class _Recorder(streaming.StreamingExporter):
    def __init__(self):
        self.view_summaries = []
        self.full_results = []
        self.views_processed = 0
        self.views_failed = 0
        self.annotation_passes_failed = 0


def _stage_a_entry(anno_success):
    return {
        "view_id": VIEW_ID, "view_name": "L1", "success": True,
        "stage": "color_id_buffer_stage_a",
        "tiff_path": "/m.tiff", "sidecar_path": "/m.json",
        "annotation_pass": {"success": anno_success},
        "annotation_pass_success": anno_success,
        "annotation_pass_failure_reason": None if anno_success else "boom",
        "annotation_tiff_path": "/a.tiff",
        "annotation_sidecar_path": "/a.json",
    }


def test_a_failed_annotation_pass_makes_the_run_unclean():
    exporter = _Recorder()
    exporter.on_view_complete(_stage_a_entry(anno_success=False))

    # views_failed keeps meaning "the model capture failed" -- it did not.
    assert exporter.views_failed == 0
    assert exporter.annotation_passes_failed == 1


def test_a_successful_annotation_pass_leaves_the_run_clean():
    """CONTROL. Without it, an implementation that always counted a failure
    would pass the test above."""
    exporter = _Recorder()
    exporter.on_view_complete(_stage_a_entry(anno_success=True))
    assert exporter.annotation_passes_failed == 0


def test_a_pass_that_did_not_run_is_not_counted_as_failed():
    exporter = _Recorder()
    entry = _stage_a_entry(anno_success=True)
    for k in ("annotation_pass", "annotation_pass_success",
              "annotation_pass_failure_reason"):
        entry.pop(k)
    exporter.on_view_complete(entry)
    assert exporter.annotation_passes_failed == 0


# --- structural: batch relocation -----------------------------------------

def test_batch_relocation_rewrites_the_annotation_paths(tmp_path):
    """The annotation files move with everything else, so their recorded
    paths must move with everything else."""
    # thinrunner_streaming.py is a Dynamo Python Script node body and is NOT
    # importable outside Dynamo. tests/test_thinrunner_streaming_summary.py
    # already established the technique: exec the source up to the
    # "# RUN PIPELINE" marker and call the real function. Reused rather than
    # re-grepped, so this exercises production code rather than asserting on
    # its text.
    from tests.test_thinrunner_streaming_summary import _load_thinrunner_helpers
    tr = _load_thinrunner_helpers()

    batch = tmp_path / "_batch_tmp_0"
    final = tmp_path / "out"
    (batch / "color_id_buffer").mkdir(parents=True)
    (final).mkdir()
    for name in ("v_7.tiff", "v_7.json", "v_7_anno.tiff", "v_7_anno.json"):
        (batch / "color_id_buffer" / name).write_bytes(b"x")

    summaries = [{
        "stage": "color_id_buffer_stage_a",
        "tiff_path": str(batch / "color_id_buffer" / "v_7.tiff"),
        "sidecar_path": str(batch / "color_id_buffer" / "v_7.json"),
        "annotation_tiff_path": str(batch / "color_id_buffer" / "v_7_anno.tiff"),
        "annotation_sidecar_path": str(batch / "color_id_buffer" / "v_7_anno.json"),
    }]

    tr["_relocate_batch_stage_a_outputs"](str(batch), str(final), summaries)

    for key in ("tiff_path", "sidecar_path",
                "annotation_tiff_path", "annotation_sidecar_path"):
        moved = summaries[0][key]
        assert os.path.dirname(moved) == os.path.join(str(final), "color_id_buffer")
        # Present on disk where the summary says it is -- the whole point.
        assert os.path.exists(moved), key
