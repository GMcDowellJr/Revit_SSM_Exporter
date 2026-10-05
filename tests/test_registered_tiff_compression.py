"""The registered annotation TIFF is written LOSSLESSLY compressed.

Observed 2026-10-05: ``Image.fromarray(out).save(..., format="TIFF")`` with
no compression argument wrote a real view's registered TIFF at ~241 MB raw,
against a 1.5 MB unregistered capture. The decoder depends on exact palette
colours, so the only acceptable codecs are lossless ones, and a missing codec
must fail rather than fall back to raw.

The gates compare the compressed output with what the RAW write produced, by
running the real tool twice -- once with the old writer swapped back in -- on
several registration fixtures, and comparing decoded pixels (not bytes), the
grid's arrays and the roll-up's rows.
"""
import csv
import hashlib
import json
import shutil
import statistics
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools import register_stage_a_annotation as reg
from tools import registration_marks as rm
from tools import stage_a_grid as grid
from tools import stage_a_grid_rollup as rollup

from tests.test_stage_a_grid import _write_pair

VARIANTS = {
    "plain": {},
    "shared_tick_colour": {"shared_anno": True},
    "blended_ticks": {"blended_anno_h": True},
    "model_crop_offset": {"model_crop_offset": (2.0, 1.0, -3.0, -2.0)},
}


def _raw_writer(pixels, path, codecs=None):
    """The writer this change replaced, verbatim."""
    Image.fromarray(pixels).save(str(path), format="TIFF")
    return "raw"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_meta(run, view_ids):
    (run / "run_meta.json").write_text(json.dumps(
        {"schema": "vop.run_meta.v1", "run_id": "RUN_T", "finalized": True,
         "views_requested": list(view_ids),
         "views": [{"view_id": v, "capture_status": "success",
                    "capture_failure_reason": ""} for v in view_ids],
         "config": {"cell_size_paper_in": 0.125}}))


def _one_run(base, writer, monkeypatch):
    """Every variant as a view of one run: register -> grid -> roll-up with
    ``writer`` writing the registered TIFF."""
    monkeypatch.setattr(reg, "write_lossless_tiff", writer)
    run = base / "run"
    cap = run / "color_id_buffer"
    cap.mkdir(parents=True)
    view_ids = []
    for n, (name, kw) in enumerate(sorted(VARIANTS.items()), start=1):
        stage = base / ("stage_" + name)
        stage.mkdir()
        anno_src, model_src, _c = _write_pair(stage, **kw)
        stem = cap / "V_{0}".format(n)
        shutil.copy(str(stage / "V_1.tiff"), str(stem) + ".tiff")
        shutil.copy(str(stage / "V_1_anno.tiff"), str(stem) + "_anno.tiff")
        model = json.loads(model_src.read_text())
        model.update(view_id=n, tiff_path=str(stem) + ".tiff")
        anno = json.loads(anno_src.read_text())
        anno.update(view_id=n, tiff_path=str(stem) + "_anno.tiff",
                    model_pass_tiff_path=str(stem) + ".tiff")
        Path(str(stem) + ".json").write_text(json.dumps(model))
        Path(str(stem) + "_anno.json").write_text(json.dumps(anno))
        view_ids.append(n)
    _run_meta(run, view_ids)
    records = dict((p.name, reg.register(p)) for p in sorted(cap.glob("*_anno.json")))
    assert grid.main([str(run)]) == 0
    assert rollup.main([str(run)]) in (0, 1)
    return run, records


@pytest.fixture
def both(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        raw = _one_run(tmp_path / "raw", _raw_writer, m)
    packed = _one_run(tmp_path / "packed", reg.write_lossless_tiff, monkeypatch)
    return raw, packed


def _registered(run):
    return sorted((run / "color_id_buffer").glob("*.registered.tiff"))


# --- B-G1 / B-2 -------------------------------------------------------------------

def test_every_registered_tiff_is_lossless_compressed_and_hashed_as_written(both):
    _raw, (run, records) = both
    tiffs = _registered(run)
    assert len(tiffs) == len(VARIANTS)
    tags = []
    for path in tiffs:
        with Image.open(str(path)) as image:
            tags.append(image.tag_v2.get(reg.TIFF_COMPRESSION_TAG))
        rec = records[path.name.replace(".registered.tiff", ".json")]
        assert rec["status"] == "registered"
        assert rec["registered_tiff_sha256"] == _sha(path)
        on_disk = json.loads(path.with_suffix(".json").read_text())
        assert on_disk["registered_tiff_sha256"] == _sha(path)
    counts = dict((t, tags.count(t)) for t in set(tags))
    print("B-G1: compression tags {0} (8 deflate, 5 lzw, 1 raw)".format(counts))
    accepted = set(v for values in reg.LOSSLESS_TAG_VALUES.values() for v in values)
    assert set(tags) <= accepted and 1 not in tags


# --- B-G2 ---------------------------------------------------------------------------

def test_compressed_pixels_equal_the_raw_writes_pixels(both):
    (raw_run, _r), (run, _p) = both
    raw_tiffs, tiffs = _registered(raw_run), _registered(run)
    assert [p.name for p in raw_tiffs] == [p.name for p in tiffs]
    mismatches = 0
    for a, b in zip(raw_tiffs, tiffs):
        with Image.open(str(a)) as image:
            assert image.tag_v2.get(reg.TIFF_COMPRESSION_TAG) == 1   # the control is raw
        pa, pb = rm.load_rgb(a), rm.load_rgb(b)
        same = pa.shape == pb.shape and pa.dtype == pb.dtype and np.array_equal(pa, pb)
        mismatches += 0 if same else 1
    print("B-G2: {0} views, {1} mismatches".format(len(tiffs), mismatches))
    assert mismatches == 0


# --- B-G3 ---------------------------------------------------------------------------

# Columns that name a file or hash its bytes, which differ between the two
# runs by construction (different folders, different TIFF bytes).
_PATHLIKE = ("path", "sha256", "dir")


def _rollup_rows(run):
    with open(str(run / "analysis_grid" / rollup.CSV_NAME), newline="",
              encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [dict((k, v) for k, v in r.items()
                 if not any(s in k for s in _PATHLIKE)) for r in rows]


def test_the_grid_from_compressed_inputs_equals_the_grid_from_raw(both):
    (raw_run, _r), (run, _p) = both
    raw_npz = sorted((raw_run / "analysis_grid").glob("*.grid.npz"))
    npz = sorted((run / "analysis_grid").glob("*.grid.npz"))
    assert [p.name for p in raw_npz] == [p.name for p in npz] and len(npz) == len(VARIANTS)
    differences = 0
    for a, b in zip(raw_npz, npz):
        da, db = dict(np.load(str(a))), dict(np.load(str(b)))
        if sorted(da) != sorted(db):
            differences += 1
            continue
        for key in da:
            if not (da[key].dtype == db[key].dtype and np.array_equal(da[key], db[key])):
                differences += 1
                break
    raw_rows, rows = _rollup_rows(raw_run), _rollup_rows(run)
    assert len(rows) == len(VARIANTS) and rows == raw_rows
    print("B-G3: {0} views, {1} npz differences, {2} roll-up rows equal".format(
        len(npz), differences, len(rows)))
    assert differences == 0
    assert {r["registration_state"] for r in rows} == {"registered"}


# --- B-G4 ---------------------------------------------------------------------------

def test_sizes_raw_vs_compressed(both):
    (raw_run, _r), (run, _p) = both
    raw = [p.stat().st_size for p in _registered(raw_run)]
    packed = [p.stat().st_size for p in _registered(run)]
    not_smaller = sum(1 for a, b in zip(raw, packed) if b >= a)
    print("B-G4: raw median {0} max {1} B; compressed median {2} max {3} B; "
          "compressed >= raw: {4}".format(statistics.median(raw), max(raw),
                                          statistics.median(packed), max(packed),
                                          not_smaller))
    assert not_smaller == 0


# --- the writer's refusals ------------------------------------------------------------

def _pixels():
    return np.array([[[255, 255, 255], [12, 34, 56]], [[1, 2, 3], [255, 255, 255]]],
                    dtype=np.uint8)


def test_lzw_is_used_when_deflate_cannot_write(tmp_path, monkeypatch):
    real_save = Image.Image.save

    def _save(self, fp, format=None, **params):
        if params.get("compression") == "tiff_deflate":
            raise OSError("encoder libtiff not available")
        return real_save(self, fp, format=format, **params)
    monkeypatch.setattr(Image.Image, "save", _save)
    path = tmp_path / "x.registered.tiff"
    assert reg.write_lossless_tiff(_pixels(), path) == "tiff_lzw"
    with Image.open(str(path)) as image:
        assert image.tag_v2.get(reg.TIFF_COMPRESSION_TAG) == 5
    assert np.array_equal(rm.load_rgb(path), _pixels())


def test_no_lossless_codec_fails_loudly_and_writes_nothing(tmp_path, monkeypatch):
    def _save(self, fp, format=None, **params):
        Path(fp).write_bytes(b"partial")      # a half-written file is removed too
        raise OSError("encoder libtiff not available")
    monkeypatch.setattr(Image.Image, "save", _save)
    path = tmp_path / "x.registered.tiff"
    with pytest.raises(reg.NoLosslessTiffCodec) as err:
        reg.write_lossless_tiff(_pixels(), path)
    assert "tiff_deflate" in str(err.value) and "tiff_lzw" in str(err.value)
    assert not path.exists()


def test_a_writer_that_ignores_the_codec_is_refused(tmp_path, monkeypatch):
    real_save = Image.Image.save

    def _save(self, fp, format=None, **params):
        params.pop("compression", None)       # silently raw
        return real_save(self, fp, format=format, **params)
    monkeypatch.setattr(Image.Image, "save", _save)
    path = tmp_path / "x.registered.tiff"
    with pytest.raises(reg.NoLosslessTiffCodec):
        reg.write_lossless_tiff(_pixels(), path)
    assert not path.exists()


def test_a_failed_write_leaves_no_registered_record(tmp_path, monkeypatch):
    """register() raises before the record is written (the record is LAST)."""
    from tests.test_register_stage_a_annotation import _write_pair as bare_pair
    anno_path, _m, _c = bare_pair(tmp_path)

    def _fail(pixels, path, codecs=None):
        raise reg.NoLosslessTiffCodec("no codec")
    monkeypatch.setattr(reg, "write_lossless_tiff", _fail)
    with pytest.raises(reg.NoLosslessTiffCodec):
        reg.register(anno_path)
    tiff_out, json_out = reg.output_paths(anno_path)
    assert not tiff_out.exists() and not json_out.exists()


def test_a_failed_rewrite_keeps_the_earlier_registered_tiff(tmp_path, monkeypatch):
    """Codex, PR #230: the write went straight to the destination, so a failed
    re-registration destroyed the earlier TIFF while its record still hashed
    it. The earlier file survives byte for byte, and no temporary is left."""
    path = tmp_path / "x.registered.tiff"
    reg.write_lossless_tiff(_pixels(), path)
    before = path.read_bytes()

    def _save(self, fp, format=None, **params):
        Path(fp).write_bytes(b"partial")
        raise OSError("encoder libtiff not available")
    monkeypatch.setattr(Image.Image, "save", _save)
    with pytest.raises(reg.NoLosslessTiffCodec):
        reg.write_lossless_tiff(_pixels(), path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.registered.tiff"]


def test_a_rejected_deflate_tag_falls_back_to_lzw(tmp_path, monkeypatch):
    """Codex, PR #230: a deflate attempt that wrote the wrong tag raised at
    once, so the documented fallback to LZW was never tried."""
    real_save = Image.Image.save

    def _save(self, fp, format=None, **params):
        if params.get("compression") == "tiff_deflate":
            params.pop("compression")          # deflate accepted, raw written
        return real_save(self, fp, format=format, **params)
    monkeypatch.setattr(Image.Image, "save", _save)
    path = tmp_path / "x.registered.tiff"
    assert reg.write_lossless_tiff(_pixels(), path) == "tiff_lzw"
    with Image.open(str(path)) as image:
        assert image.tag_v2.get(reg.TIFF_COMPRESSION_TAG) == 5
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.registered.tiff"]


def test_the_gate_accepts_every_tag_the_tool_accepts_and_no_other():
    """Codex, PR #230: the gate kept its own {5, 8} and would fail a legacy
    deflate (32946) TIFF the tool deliberately writes as valid. Composed:
    the gate's verdict over each tag the tool accepts, and over raw."""
    from tools import gate_registered_tiff_compression as gate
    clean = {"B-G1_raw_count": 0, "B-G2_pixel_mismatches": [],
             "B-G3_npz_differences": [], "B-G3_rollup_row_differences": 0}
    for values in reg.LOSSLESS_TAG_VALUES.values():
        for tag in values:
            assert gate.passes(dict(clean, **{"B-G1_compression_tags": {tag: 1}})), tag
    assert not gate.passes(dict(clean, **{"B-G1_compression_tags": {1: 1},
                                          "B-G1_raw_count": 1}))
    assert not gate.passes(dict(clean, **{"B-G1_compression_tags": {7: 1}}))   # JPEG
