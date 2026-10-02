"""The offline gate, runnable: an archive sidecar re-encoded into the current
writer shape (C1-C6) decodes and registers IDENTICALLY to the original.

Two bindings:
  * the re-encoder composed with the WRITER: re-encoding what the baseline
    writer produced gives what the current writer produces (checked against
    the current writer's own output on the same fixture);
  * the re-encoded file composed with the READERS: decode_stage_a_color_id
    and register_stage_a_annotation give the same element pixel counts,
    transforms and registered image on both files.
"""
import hashlib
import json
import shutil

import pytest

from tests import test_register_stage_a_annotation as pair
from tests.test_frame_export_geometry_call_site import _export, _raster_at
from tools import decode_stage_a_color_id as dsc
from tools import register_stage_a_annotation as reg
from tools.reencode_stage_a_sidecar import reencode
from tools.stage_a_sidecar_shapes import legacy_view
from vop_interwoven.core.math_utils import Bounds2D

_VOLATILE = ("decode_ms", "generated_at_unix", "source_sidecar", "source_sidecar_sha256")


def _decoded(path):
    doc = json.loads(dsc.decode_one(path).read_text())
    for key in _VOLATILE:
        doc.pop(key, None)
    return doc


def _reencoded_copy(src_dir, dst_dir):
    dst_dir.mkdir()
    for f in src_dir.iterdir():
        if f.suffix == ".tiff":
            shutil.copy(str(f), str(dst_dir / f.name))
    for name in ("V_1.json", "V_1_anno.json"):
        old = json.loads((src_dir / name).read_text())
        for k in ("tiff_path", "model_pass_tiff_path"):
            if k in old:
                old[k] = str(dst_dir / (old[k].rsplit("/", 1)[-1]))
        (dst_dir / name).write_text(json.dumps(reencode(old)))
    return dst_dir / "V_1_anno.json", dst_dir / "V_1.json"


def test_decode_and_register_are_identical_on_old_and_reencoded_shapes(tmp_path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_anno, old_model, _c = pair._write_pair(old_dir)
    # A pre-P1 sidecar carries the probe/restore blocks; put them in, so the
    # P1 gate below has something to remove.
    for path, extra in ((old_model, {"palette_step": 7, "categories_hidden": {"-2000011": {}},
                                     "filter_state": {}, "paint_failures": 0}),
                        (old_anno, {"palette_step": 5, "capture_faults": [],
                                    "restore_failures": [], "override_restore_check": {},
                                    "unconfirmed_api_claims": ["x"], "paint_failures": 0})):
        side = json.loads(path.read_text())
        side.update(extra)
        path.write_text(json.dumps(side))
    new_anno, new_model = _reencoded_copy(old_dir, tmp_path / "new")

    # The re-encoded files really are the new shape (else this proves nothing).
    new_side = json.loads(new_model.read_text())
    assert "frame" in new_side and "resolution" not in new_side and "bounds_xy" not in new_side
    # P1 gate: capture_integrity present, none of the removed keys.
    from vop_interwoven.color_id_buffer import SIDECAR_PROBE_ONLY_KEYS
    for path in (new_model, new_anno):
        side = json.loads(path.read_text())
        assert side["capture_integrity"]["status"] == "rebuilt_from_pre_p1_keys"
        assert not (set(SIDECAR_PROBE_ONLY_KEYS) & set(side)), path

    for old, new in ((old_model, new_model), (old_anno, new_anno)):
        a, b = _decoded(old), _decoded(new)
        a.pop("source_tiff"), b.pop("source_tiff")
        assert a == b
        assert a["elements"], "nothing decoded; the comparison is vacuous"

    reg.register(old_anno)
    reg.register(new_anno)
    rec_old, rec_new = pair._persisted(old_anno), pair._persisted(new_anno)
    assert rec_old["status"] == rec_new["status"] == "registered"
    assert rec_old["annotation_to_model_px"] == rec_new["annotation_to_model_px"]

    def _sha(rec):
        return hashlib.sha256(open(rec["registered_tiff"], "rb").read()).hexdigest()
    assert _sha(rec_old) == _sha(rec_new)


def test_reencoding_what_the_old_writer_wrote_gives_what_the_new_writer_writes(tmp_path):
    """Composed with the real writer: fold the current output back to the
    pre-C5 shape the baseline writer wrote, re-encode, and compare."""
    md = json.loads(json.dumps(_export(tmp_path, _raster_at(
        1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))["metadata"]))
    old = dict(legacy_view(md))
    del old["frame"]
    frame = dict(reencode(old)["frame"])
    # D added a fact the old writer never recorded -- whether the crop was
    # written, and the crop read back -- so it is rebuilt and MARKED, never
    # equal to a measured one. Everything else is the new writer's, exactly.
    rebuilt = frame.pop("crop_write")
    assert rebuilt["status"] == "rebuilt_from_pre_d_frame"
    assert rebuilt["read_back"]["state"] == "unavailable"
    new = dict(md["frame"])
    assert new.pop("crop_write")["read_back"]["matches_request"] is True
    assert frame == new


def test_an_old_near_face_w_entry_reencodes_to_the_c1_c4_shape():
    old = {"bbox_corners_uv": [[1.23456789, 2.0], [3.0, 2.0], [3.0, 4.0], [1.23456789, 4.0]],
           "near_face_w": -1.0000004, "category": "Walls",
           "bbox_3d": {"state": "value", "value": {"min": [0, 0, 0], "max": [1, 1, 1.00000049]}},
           "source": {"state": "value", "value": "HOST"},
           "category_state": {"state": "value", "value": "Walls"},
           "import_symbol_state": {"state": "not_applicable", "reason": "x"},
           "view_specific_state": {"state": "not_applicable", "reason": "x"}}
    new = reencode({"near_face_w_map": {"host": {"1": old}, "link": {}}})["near_face_w_map"]["host"]["1"]
    assert new == {"uv_rect": [1.234568, 2.0, 3.0, 4.0], "near_face_w": -1.0,
                   "category": "Walls", "source": "HOST",
                   "bbox_3d": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}}


def test_the_cli_takes_a_tiff_a_folder_and_refuses_anything_else(tmp_path, capsys):
    """Greg ran it on a capture's .tiff and got a UnicodeDecodeError: the
    image was read as JSON text. A .tiff now resolves to its sidecar, a folder
    re-encodes every capture sidecar in it, and anything else is refused."""
    from tools import reencode_stage_a_sidecar as tool
    old_anno, old_model, _c = pair._write_pair(tmp_path)
    assert tool.main([str(old_model.with_suffix(".tiff"))]) == 0
    assert (tmp_path / "V_1.reencoded.json").exists()

    assert tool.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "TOTAL 2 sidecars" in out     # V_1.json, V_1_anno.json; not the .reencoded one

    stray = tmp_path / "notes.txt"
    stray.write_text("x")
    assert tool.main([str(stray)]) == 2
    assert tool.main([str(tmp_path / "missing.tiff")]) == 2


def test_a_crop_less_sidecar_decodes_at_the_runs_dpi_from_run_meta(tmp_path):
    """pipeline_0928_0953, Elevation annotation sidecar: with no crop the
    decoder derives feet-per-pixel from the requested px AND dpi. C6 moved
    the dpi to run_meta.json, so a re-encoded sidecar decoded at the 150 dpi
    DEFAULT -- identical only because that run used 150. With run_meta
    present the decoder must use the run's dpi."""
    # No registration ticks: this is the model-lattice formula's path (a
    # registered capture takes its scale from the ticks instead -- below).
    old_anno, _m, _c = pair._write_pair(tmp_path, with_marks=False)
    old = json.loads(old_anno.read_text())
    old["resolution"]["requested_export_dpi"] = 300.0
    old_anno.write_text(json.dumps(old))
    before = _decoded(old_anno)
    assert before["feet_per_pixel_basis"]["numerator"].endswith("sidecar_requested_export_dpi")

    new = reencode(old)
    assert "requested_export_dpi" not in new["frame"]
    old_anno.write_text(json.dumps(new))
    # Without run_meta: the default, and the basis SAYS so.
    no_meta = _decoded(old_anno)
    assert no_meta["feet_per_pixel_basis"]["numerator"].endswith("default_export_dpi")
    # With run_meta beside it: the run's dpi, and the same answer as before.
    (tmp_path / "run_meta.json").write_text(json.dumps(
        {"config": {"color_id_buffer_export_dpi": 300.0}}))
    with_meta = _decoded(old_anno)
    assert with_meta["feet_per_pixel"] == before["feet_per_pixel"]
    assert with_meta["feet_per_pixel_basis"] == before["feet_per_pixel_basis"]
    assert no_meta["feet_per_pixel"] != before["feet_per_pixel"]


def test_a_crop_less_capped_capture_decodes_on_the_achieved_dpi(tmp_path):
    """Codex, PR #221: with no crop and no pre_cap_px, the pixel count is
    requested_pixel_size -- the lattice AFTER the axis cap / floor, so on the
    ACHIEVED dpi. Divided by the requested dpi (restored from run_meta since
    C6, and carried in the sidecar before it) a capped view's extent came out
    requested/achieved too large. Here the cap halved 300 dpi to 150."""
    # No registration ticks: this is the model-lattice formula's path (a
    # registered capture takes its scale from the ticks instead -- below).
    old_anno, _m, _c = pair._write_pair(tmp_path, with_marks=False)
    old = json.loads(old_anno.read_text())
    px = old["resolution"]["requested_pixel_size"]
    old["resolution"].update({"requested_export_dpi": 300.0, "achieved_export_dpi": 150.0})
    old_anno.write_text(json.dumps(old))
    before = _decoded(old_anno)
    assert before["feet_per_pixel_basis"]["numerator"].endswith("sidecar_achieved_export_dpi")
    # The fixture's annotation image is exactly px wide, so the measured fit
    # axis is px: fpp == (px / achieved_dpi) * scale / 12 / px.
    assert before["feet_per_pixel"] == pytest.approx(px / 150.0 * 96.0 / 12.0 / px)

    # The re-encoded shape, with the run's REQUESTED 300 dpi in run_meta,
    # still decodes on the achieved dpi the sidecar keeps.
    old_anno.write_text(json.dumps(reencode(old)))
    (tmp_path / "run_meta.json").write_text(json.dumps(
        {"config": {"color_id_buffer_export_dpi": 300.0}}))
    after = _decoded(old_anno)
    assert after["feet_per_pixel"] == before["feet_per_pixel"]
    assert after["feet_per_pixel_basis"] == before["feet_per_pixel_basis"]


def test_a_registered_annotation_capture_takes_its_scale_from_its_ticks(tmp_path):
    """Greg, 2026-09-30: a registered capture's annotation image renders the
    AUTHORED crop, so it is not on the model lattice, and the model-extent
    formula understated its feet-per-pixel (0.63x on pipeline_0930_0919's
    ModelCallout). The fixture draws it at a KNOWN 12 px/ft against the
    model's 18, so the truth is 1/12 ft/px; the old formula gave the model's
    extent over this image's pixels instead."""
    old_anno, _m, _c = pair._write_pair(tmp_path)
    doc = _decoded(old_anno)
    assert doc["feet_per_pixel_basis"] == {"numerator": "registration_tick_fit",
                                           "denominator": "registration_tick_fit"}
    assert doc["feet_per_pixel"] == pytest.approx(1.0 / pair.A_U, rel=1e-3)
    assert doc["registration_marks"]["px_per_ft_u"] == pytest.approx(pair.A_U, rel=1e-3)
    # The formula it replaces is visibly different on this fixture, so the
    # assertion above discriminates.
    no_ticks = tmp_path / "plain"
    no_ticks.mkdir()
    plain_anno, _m2, _c2 = pair._write_pair(no_ticks, with_marks=False)
    assert _decoded(plain_anno)["feet_per_pixel"] != pytest.approx(1.0 / pair.A_U, rel=1e-2)


def test_a_registered_capture_whose_tick_fit_fails_reports_no_scale(tmp_path):
    """No fallback to the model-lattice formula, which is known to be wrong
    for this image: no scale, and the reason says why."""
    horizontal = [m["key"] for m in pair._marks() if m["orientation"] == "horizontal"]
    old_anno, _m, _c = pair._write_pair(tmp_path, drop_anno=tuple(horizontal))
    doc = _decoded(old_anno)
    assert doc["registration_marks"]["fit_status"] != "value"
    assert doc["feet_per_pixel"] is None
    assert "registration ticks" in doc["feet_per_pixel_unreliable_reason"]
