"""The PROBE-ONLY annotation-extent switch of the registered capture, and the
lattice-overrun probe's own summary, DRIVEN through production's
``export_registered_stage_a_view`` on the fake DB.

Group A of RUN_2025-02-03_to_2025-11-01_SERIES_SMALL_VIEWS.md: a section
marker whose line runs past the crop makes the annotation export taller than
the model's; over the 10000 px ceiling it is halved and refused
(``annotation_lattice_mismatch``). The fake Revit here renders exactly that:
the ANNOTATION export's derived axis is the drawn extent -- crop A plus the
marker's overrun while the marker is visible and the annotation crop is off,
crop A plus the annotation-crop offsets while it is on -- and the MODEL
export's is crop A's. What is asserted is what each export SAW (the fake
doc's ExportImage) and what the FILES say.

``color_id_buffer_anno_extent_variant`` is read with getattr and absent from
Config, so nothing else in the suite sets it: every position is asserted
here, with a control pinning that unset is the shipped capture.
Mutations wired to these tests: applying the variant before the model pass
(the model export would see it); dropping the annotation-crop read-back
key from view_state (the restore of the write goes unverified); dropping the
variant record from the payload (the FILE test goes red).
"""
import json
import math
import os

import pytest

from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX

from tests import test_probe_anno_pass_run_variant as world
from tests import test_stage_a_annotation_pass_probe_switches as switches
from tests import test_stage_a_registered_capture as rc_tests
from tests.dynamo import probe_stage_a_lattice_overrun as probe
from tests.stage_a_capture_fakes import FakeElement

# The fake view's authored crop is 60 x 45 ft (tests/test_probe_anno_pass_
# run_variant._ProbeView), at 1/96: 7.5 px per dpi on the fitted axis. At
# 1300 dpi the model lattice is 9750 x 7313 px -- under the 10000 ceiling.
DPI = 1300.0
CROP_W, CROP_H = 60.0, 45.0
# A section line 21 ft below and 0 ft above the crop: the annotation's drawn
# extent is 66 ft tall, 10725 px at the model's fitted request -- over.
MARKER_ID = 3001
MARKER_OVERRUN_FT = 21.0
ANNO_CROP_OFFSET_FT = 0.25        # world._Manager's four offsets


def _marker():
    return FakeElement(MARKER_ID, world.VIEWERS_CAT, name="Section 7",
                       bbox=switches._BBox((40, 15 - MARKER_OVERRUN_FT, 0), (41, 50, 0)))


def install_overrun_exporter(monkeypatch, views, overrun_ft=MARKER_OVERRUN_FT, extra_h_px=0):
    """ExportImage renders FitToPage of the DRAWN extent: the model pass at
    crop A's aspect; the annotation pass taller while the marker is visible
    (not hidden in the view, annotation crop off), and at crop A plus the
    annotation-crop offsets while the annotation crop is on."""
    seen = []

    def _export(self, opts):
        self.export_image_calls.append(opts)
        if self.on_export_image is not None:
            self.on_export_image(opts)
        view = views[0]
        px = int(opts.PixelSize)
        anno = os.path.basename(opts.FilePath) == "t_anno"
        crop_on = bool(getattr(view, "_anno_crop", 0))
        marker_drawn = MARKER_ID not in view.hidden_elements and MARKER_ID in self._by_id
        if anno and crop_on:
            w_ft = CROP_W + 2 * ANNO_CROP_OFFSET_FT
            h_ft = CROP_H + 2 * ANNO_CROP_OFFSET_FT
        elif anno and marker_drawn:
            w_ft, h_ft = CROP_W, CROP_H + overrun_ft
        else:
            w_ft, h_ft = CROP_W, CROP_H
        h = int(math.floor(px * h_ft / w_ft))
        if anno and marker_drawn and not crop_on:
            # Drawn content that does not scale with PixelSize (a fixed text
            # height): what would defeat a proportional re-size.
            h += int(extra_h_px)
        switches._write_tiff_header(opts.FilePath + ".tiff", px, h)
        seen.append({"pass": "annotation" if anno else "model", "px": px, "h": h,
                     "annotation_crop_active": crop_on,
                     "marker_hidden": MARKER_ID in view.hidden_elements,
                     "marks_in_doc": len([i for i in self._by_id if i > world.MARK_ID_BASE])})
    monkeypatch.setattr(switches._SizedDoc, "ExportImage", _export)
    return seen


class _AnnoCropParam(object):
    """VIEWER_ANNOTATION_CROP_ACTIVE. Its value lives on the VIEW, so the
    group's rollback (which restores the view's attributes) restores it, as
    Revit's does. The fake view has NO AnnotationCropActive attribute: on
    Revit 2025 the ViewPlan wrapper has none either (probe run
    20261019T1340), and assigning one changed nothing in Revit."""

    def __init__(self, view):
        self._view = view

    def AsInteger(self):
        return int(self._view._anno_crop)

    def Set(self, value):
        object.__setattr__(self._view, "_anno_crop", int(value))
        return True


def run_capture(tmp_path, monkeypatch, variant=None, extra_cfg=None, marker=True,
                overrun_ft=MARKER_OVERRUN_FT, extra_h_px=0, anno_crop_param=True):
    views = []

    def _setup(view):
        object.__setattr__(view, "_anno_crop", 0)
        if anno_crop_param:
            view.params["VIEWER_ANNOTATION_CROP_ACTIVE"] = _AnnoCropParam(view)
        assert not hasattr(view, "AnnotationCropActive")
        views.append(view)

    def _cfg(cfg):
        cfg.color_id_buffer_export_dpi = DPI
        if variant is not None:
            cfg.color_id_buffer_anno_extent_variant = variant
            # Measured alone, as the probe runs it (run_variant).
            cfg.color_id_buffer_co_size = False
        for k, v in (extra_cfg or {}).items():
            setattr(cfg, k, v)
    seen = install_overrun_exporter(monkeypatch, views, overrun_ft, extra_h_px)
    out, view, doc, exports, diag = rc_tests._run(
        tmp_path, extra_elements=[_marker()] if marker else [], view_setup=_setup,
        cfg_setup=_cfg)
    return out, view, doc, seen


def _file(path):
    with open(path) as handle:
        return json.load(handle)


def _faults(path):
    return [f["fault"] for f in _file(path)["capture_integrity"]["capture_faults"]]


def test_as_is_the_marker_overruns_the_ceiling_and_the_FILE_says_lattice_mismatch(
        tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, "as_is")
    model, first, second = seen
    assert model == dict(model, **{"pass": "model", "px": 9750, "h": 7312})
    assert first["pass"] == "annotation" and first["h"] > MAX_STAGE_A_AXIS_PX
    assert second["px"] == 9750 // 2          # halved off the lattice
    assert "annotation_lattice_mismatch" in _faults(out["annotation_sidecar_path"])
    reg = _file(out["annotation_sidecar_path"])["registration_marks"]
    assert reg["anno_extent_variant"] == {"variant": "as_is", "applied": "nothing"}


def test_section_markers_hidden_for_the_annotation_pass_only_fits_and_is_put_back(
        tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, "section_markers_hidden")
    model, anno = seen
    assert model["marker_hidden"] is False         # the model pass is the shipped one
    assert anno["marker_hidden"] is True and anno["px"] == 9750
    assert anno["h"] == 7312
    assert "annotation_lattice_mismatch" not in _faults(out["annotation_sidecar_path"])
    rec = _file(out["sidecar_path"])["registration_marks"]["anno_extent_variant"]
    assert rec["hidden"] == 1 and rec["took_effect"] is True
    assert rec["section_markers"]["ids"] == [MARKER_ID]
    assert rec["section_markers"]["past_reference_ids"] == [MARKER_ID]
    # The rollback is the restore, and the read-back says so.
    assert MARKER_ID not in view.hidden_elements
    assert out["registration"]["faults"] == []
    assert out["registration"]["restore"]["view_membership"]["status"] != "changed"


def test_annotation_crop_on_for_the_annotation_pass_only_and_read_back(tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, "annotation_crop_on")
    model, anno = seen
    assert model["annotation_crop_active"] is False
    assert anno["annotation_crop_active"] is True and anno["px"] == 9750
    rec = _file(out["annotation_sidecar_path"])["registration_marks"]["anno_extent_variant"]
    assert rec["before"]["value"] is False and rec["read_back"]["value"] is True
    assert rec["offsets_ft"]["Top"]["value"] == ANNO_CROP_OFFSET_FT
    # The write is in the read-back, and the rollback undid it.
    vs = out["registration"]["restore"]["view_state"]
    assert vs["annotation_crop_active"] == {"status": "restored"}
    assert view._anno_crop == 0
    assert not hasattr(view, "AnnotationCropActive")       # nothing written beside Revit
    assert out["registration"]["faults"] == []


def test_annotation_crop_on_that_cannot_be_read_is_not_applied_and_says_so(
        tmp_path, monkeypatch):
    """Run 20261019T1340: the read of the crop state failed, the write went to
    a Python attribute and read back True -- took_effect true over a variant
    that changed nothing. With no parameter to write through, the variant now
    refuses: nothing written, took_effect false, a fault in the FILE."""
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, "annotation_crop_on",
                                       anno_crop_param=False)
    assert all(s["annotation_crop_active"] is False for s in seen)
    rec = _file(out["annotation_sidecar_path"])["registration_marks"]["anno_extent_variant"]
    assert rec["before"]["state"] == "unavailable" and "set_returned" not in rec
    assert rec["took_effect"] is False
    faults = [f["fault"] for f in _file(out["sidecar_path"])["registration_marks"]["faults"]]
    assert "anno_extent_variant_not_applied" in faults
    assert not hasattr(view, "AnnotationCropActive")


def test_control_unset_is_the_shipped_capture(tmp_path, monkeypatch):
    out, view, doc, seen = run_capture(tmp_path, monkeypatch, None, marker=False)
    assert [s["pass"] for s in seen] == ["model", "annotation"]
    reg = _file(out["sidecar_path"])["registration_marks"]
    assert reg["anno_extent_variant"] is None
    assert "annotation_crop_active" not in out["registration"]["restore"]["view_state"]
    assert out["registration"]["faults"] == [] and out["success"] is True


def test_an_unknown_variant_is_refused_not_defaulted(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="color_id_buffer_anno_extent_variant"):
        run_capture(tmp_path, monkeypatch, "markers_off")


def test_the_probes_summary_reads_the_FILES_production_wrote(tmp_path, monkeypatch):
    """Composed: the probe's pure summary over the sidecars the real capture
    wrote under each variant, not over a fixture shaped like them."""
    by_variant = {}
    for variant in ("as_is", "section_markers_hidden", "annotation_crop_on"):
        out, _view, _doc, _seen = run_capture(tmp_path / variant, monkeypatch, variant)
        summary = probe.summarize_view(_file(out["sidecar_path"]),
                                       _file(out["annotation_sidecar_path"]),
                                       {"view_id": rc_tests.VIEW_ID})
        by_variant[variant] = [summary]
    as_is = by_variant["as_is"][0]
    assert as_is["state"] == "overrun"
    assert as_is["anno_first_derived_px"] == 10725 and as_is["model_derived_px"] == 7312
    assert by_variant["section_markers_hidden"][0]["state"] == "fits"
    assert by_variant["section_markers_hidden"][0]["variant_record"]["hidden"] == 1
    v = probe.verdict(by_variant)
    assert v["as_is"]["overrun"] == [rc_tests.VIEW_ID] and v["as_is"]["fits"] == []
    assert v["section_markers_hidden"]["fits"] == [rc_tests.VIEW_ID]
    assert v["annotation_crop_on"]["fits"] == [rc_tests.VIEW_ID]
    # The marker is no longer painted: the content the variant gives up.
    assert v["section_markers_hidden"]["content_lost"] == [rc_tests.VIEW_ID]
    assert all(not r for r in (v[k]["restore_faults"] for k in v))


def test_probe_variant_selection_refuses_what_this_build_lacks():
    assert probe.select_variants("all", probe.VARIANTS[:3]) == list(probe.VARIANTS[:3])
    with pytest.raises(ValueError, match="not available"):
        probe.select_variants("co_sized", probe.VARIANTS[:3])
    with pytest.raises(ValueError, match="unknown"):
        probe.select_variants("as-is")
    with pytest.raises(ValueError, match="unknown key"):
        probe.parse_options({"repo": "x"})


def test_the_probe_offers_co_sized_on_a_build_that_has_it():
    assert probe._available_variants() == list(probe.VARIANTS)
