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
    assert reencode(old)["frame"] == md["frame"]


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
