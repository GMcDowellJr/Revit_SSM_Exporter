"""P0 Stage A export-correctness gates: the cap, the dimension check, the AA read.

All offline. Nothing here touches Revit: the export is driven through a fake
document/view/options triple that records what PixelSize it was handed and
writes a TIFF header of whatever size the fake says Revit produced.
"""
import struct

import pytest

from vop_interwoven import color_id_buffer as cib
from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX, cap_axes


# --------------------------------------------------------------------------
# G1/G2 -- the cap
# --------------------------------------------------------------------------

def test_cap_is_the_measured_limit_and_production_holds_no_15000():
    assert MAX_STAGE_A_AXIS_PX == 10000
    assert cib.MAX_STAGE_A_PIXEL_SIZE == MAX_STAGE_A_AXIS_PX


@pytest.mark.parametrize("requested,derived,expect_px,expect_derived,expect_capped", [
    # A 20000x5000-aspect view: the fitted axis is what is over.
    (20000, 5000, 10000, 2500, True),
    # Its portrait equivalent: the DERIVED axis is what is over, which a
    # fitted-axis-only cap would wave straight through.
    (5000, 20000, 2500, 10000, True),
    # Measured clean, accepted unchanged -- the cap must not shave a view
    # that already fits.
    (10000, 9642, 10000, 9642, False),
    # Measured drifted at 8695 wide: under any width cap, over on height.
    (8695, 10028, 8671, 10000, True),
])
def test_two_axis_cap_bounds_the_longer_axis(requested, derived, expect_px,
                                             expect_derived, expect_capped):
    out = cap_axes(requested, derived)
    assert out["accepted_px"] == expect_px
    assert out["accepted_derived_px"] == expect_derived
    assert out["cap_applied"] is expect_capped
    assert max(out["accepted_px"], out["accepted_derived_px"]) <= MAX_STAGE_A_AXIS_PX
    assert out["pre_cap_px"] == requested


def test_cap_rejects_nonpositive_axes():
    for bad in ({"requested_px": 0, "derived_px": 10},
                {"requested_px": 10, "derived_px": 0}):
        with pytest.raises(ValueError):
            cap_axes(**bad)


def test_test_contract_uses_the_production_cap():
    """G1: one cap source. The probe contract must not re-implement it."""
    from tests.dynamo import resolution_contract as probe_contract
    assert probe_contract.cap_axes is cap_axes
    assert probe_contract.round_half_up_positive is not None
    report = probe_contract.calculate_paper_space_resolution(
        276, 100, 96, 150, "active_model_crop")
    capped = probe_contract.apply_resolution_cap(report, MAX_STAGE_A_AXIS_PX)
    assert capped["accepted_width_px"] <= MAX_STAGE_A_AXIS_PX
    assert capped["predicted_height_px"] <= MAX_STAGE_A_AXIS_PX


# --------------------------------------------------------------------------
# G3 -- dimension read + mismatch routing
# --------------------------------------------------------------------------

def write_tiff_header(path, width, height, endian="<"):
    """A minimal but real classic-TIFF header: two IFD entries, no pixels.

    read_image_dimensions never reads past the first IFD, so this is exactly
    as much file as the check under test consumes.
    """
    order = b"II" if endian == "<" else b"MM"
    entries = b""
    for tag, value in ((256, width), (257, height)):
        # LONG (type 4), count 1, value inline in the 4-byte field.
        entries += struct.pack(endian + "HHI", tag, 4, 1) + struct.pack(endian + "I", value)
    blob = (order + struct.pack(endian + "HI", 42, 8)
            + struct.pack(endian + "H", 2) + entries + struct.pack(endian + "I", 0))
    with open(str(path), "wb") as fh:
        fh.write(blob)
    return path


@pytest.mark.parametrize("endian", ["<", ">"])
def test_read_image_dimensions_reads_both_byte_orders(tmp_path, endian):
    p = write_tiff_header(tmp_path / "t.tiff", 10000, 9642, endian)
    assert cib.read_image_dimensions(str(p)) == (10000, 9642)


def test_read_image_dimensions_handles_short_typed_tags(tmp_path):
    entries = b""
    for tag, value in ((256, 800), (257, 600)):
        entries += struct.pack("<HHI", tag, 3, 1) + struct.pack("<H", value) + b"\x00\x00"
    blob = b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 2) + entries + struct.pack("<I", 0)
    p = tmp_path / "short.tiff"
    p.write_bytes(blob)
    assert cib.read_image_dimensions(str(p)) == (800, 600)


@pytest.mark.parametrize("payload", [
    b"",                       # empty
    b"not a tiff at all!!",    # no byte-order mark
    b"II" + struct.pack("<HI", 43, 8),   # BigTIFF
    b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 5),  # truncated IFD
])
def test_unreadable_files_raise_rather_than_guess(tmp_path, payload):
    p = tmp_path / "bad.tiff"
    p.write_bytes(payload)
    with pytest.raises(Exception):
        cib.read_image_dimensions(str(p))


class _FakeDiag:
    def __init__(self):
        self.warnings = []
        self.errors = []

    def warn(self, **kw):
        self.warnings.append(kw)

    def error(self, **kw):
        self.errors.append(kw)


class _FakeExporter:
    """Stands in for _export_one_tiff: records requests, writes a header.

    ``produce`` maps the accepted PixelSize to the (w, h) the fake Revit
    "rendered", so a test can make the export come back at half the request
    or over the per-axis limit.
    """

    def __init__(self, produce, corrupt=False):
        self.produce = produce
        self.corrupt = corrupt
        self.requests = []

    def __call__(self, doc, view, out_dir, output_path, pixel_size, diag=None,
                 view_id=None, fit_direction="horizontal"):
        self.requests.append(int(pixel_size))
        w, h = self.produce(int(pixel_size))
        if self.corrupt:
            with open(output_path, "wb") as fh:
                fh.write(b"\x00" * 4)
        else:
            write_tiff_header(output_path, w, h)
        return int(pixel_size)


def _run_export(monkeypatch, tmp_path, exporter, pixel_size, **kw):
    monkeypatch.setattr(cib, "_export_one_tiff", exporter)
    diag = _FakeDiag()
    out = tmp_path / "out" / "v.tiff"
    path, accepted, report = cib._export_tiff(
        None, None, str(out), pixel_size, diag=diag, view_id=7, **kw)
    return accepted, report, diag


def test_matching_export_passes(monkeypatch, tmp_path):
    exporter = _FakeExporter(lambda px: (px, 9642))
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, 10000)
    assert report["dim_check"] == "pass"
    assert (report["actual_w"], report["actual_h"]) == (10000, 9642)
    assert exporter.requests == [10000]
    assert accepted == 10000
    assert diag.errors == []


def test_half_size_export_is_a_mismatch_and_backs_off(monkeypatch, tmp_path):
    """G3: an image at half the requested dimensions -> dim_check = mismatch."""
    exporter = _FakeExporter(lambda px: (px // 2, px // 2))
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, 8000)
    assert report["dim_check"] == "mismatch"
    assert report.get("backoff_exhausted") is True
    # It halved all the way to the floor rather than accepting any of them.
    assert exporter.requests[0] == 8000
    assert exporter.requests[-1] == 16
    assert all(b == max(16, a // 2) for a, b in zip(exporter.requests, exporter.requests[1:]))
    assert diag.errors, "backoff exhaustion must be recorded as an error"


def test_over_limit_derived_axis_is_a_mismatch_then_recovers(monkeypatch, tmp_path):
    """The 8695x10028 shape: fitted axis exact, derived axis over the line."""
    exporter = _FakeExporter(lambda px: (px, int(round(px * 10028 / 8695.0))))
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, 8695)
    assert exporter.requests == [8695, 4347]
    assert report["dim_check"] == "pass"
    assert max(report["actual_w"], report["actual_h"]) <= MAX_STAGE_A_AXIS_PX
    assert [a["dim_check"] for a in report["attempts"]] == ["mismatch", "pass"]


def test_verification_ceiling_ignores_a_raised_sizing_cap(monkeypatch, tmp_path):
    """G5's second bullet, offline: an over-cap request must not pass."""
    exporter = _FakeExporter(lambda px: (px, int(round(px * 12356 / 15000.0))))
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, 15000)
    assert report["attempts"][0]["dim_check"] == "mismatch"
    assert exporter.requests[0] == 15000 and len(exporter.requests) > 1
    assert report["dim_check"] == "pass"
    assert max(report["actual_w"], report["actual_h"]) <= MAX_STAGE_A_AXIS_PX


def test_unreadable_export_is_read_failed_not_pass(monkeypatch, tmp_path):
    """G3: an unreadable file -> read_failed, and never counted as a pass."""
    exporter = _FakeExporter(lambda px: (px, px), corrupt=True)
    accepted, report, diag = _run_export(monkeypatch, tmp_path, exporter, 4000)
    assert report["dim_check"] == "read_failed"
    assert report["actual_w"] is None and report["actual_h"] is None
    assert report["dim_read_error"]
    # A failed read is not a mismatch either: there is nothing to back off from.
    assert exporter.requests == [4000]
    assert any(w.get("callsite") == "export_dim_check" for w in diag.warnings)


def test_vertical_fit_checks_the_height_axis(monkeypatch, tmp_path):
    exporter = _FakeExporter(lambda px: (5000, px))
    accepted, report, diag = _run_export(
        monkeypatch, tmp_path, exporter, 9000, fit_direction="vertical")
    assert report["requested_axis"] == "height"
    assert report["dim_check"] == "pass"


# --------------------------------------------------------------------------
# G4 / end-to-end -- through export_color_id_buffer_view with a fake Revit
#
# Reuses the fake Autodesk.Revit.DB surface already built for the shadow-
# suppression test rather than standing up a second one; only the pieces
# these gates need (an ExportImage that writes a real TIFF header, a view
# whose GetViewDisplayModel raises) are specialised here.
# --------------------------------------------------------------------------

from vop_interwoven.config import Config  # noqa: E402
from tests.test_color_id_buffer_shadow_suppression import (  # noqa: E402
    _FakeDiag as _HarnessDiag,
    _FakeDoc,
    _FakeView,
    _install_fake_revit_db,
)


class _SizedDoc(_FakeDoc):
    """A fake doc whose ExportImage writes a readable TIFF header.

    ``render`` maps the requested PixelSize to the (w, h) this fake Revit
    pretends it produced, which is the whole point: the real bug was that
    nothing downstream ever compared the two.
    """

    def __init__(self, render):
        _FakeDoc.__init__(self)
        self.render = render
        self.pixel_sizes = []

    def ExportImage(self, opts):
        self.export_image_calls.append(opts)
        if self.on_export_image is not None:
            self.on_export_image(opts)
        self.pixel_sizes.append(int(opts.PixelSize))
        import os as _os
        out_dir = _os.path.dirname(opts.FilePath)
        if out_dir and not _os.path.isdir(out_dir):
            _os.makedirs(out_dir)
        w, h = self.render(int(opts.PixelSize))
        write_tiff_header(opts.FilePath + ".tiff", w, h)


class _ViewDisplayModelRaises(_FakeView):
    """GetViewDisplayModel() raises, as it does on hosts/view types that
    don't expose one."""

    def GetViewDisplayModel(self):
        raise RuntimeError("no display model on this view")


def _run_view(tmp_path, doc, view, **cfg_kw):
    cfg = Config(**cfg_kw)
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    diag = _HarnessDiag()
    result = color_id_buffer_view(doc, view, cfg, diag)
    return result, diag


def color_id_buffer_view(doc, view, cfg, diag):
    return cib.export_color_id_buffer_view(
        doc, view, elements=[], cfg=cfg, diag=diag, raster=None, elem_cache=None)


def test_matching_export_records_pass_and_the_cap_fields(tmp_path):
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px, px))
        result, diag = _run_view(tmp_path, doc, _FakeView(view_id=101))

    res = result["metadata"]["resolution"]
    assert result["success"] is True
    assert result["failure_reason"] is None
    assert res["dim_check"] == "pass"
    assert res["actual_w"] == res["requested_px"] == res["pre_cap_px"]
    assert res["cap_applied"] is False
    assert res["requested_axis"] == "width"
    assert res["max_axis_px"] == MAX_STAGE_A_AXIS_PX
    assert res["dim_check_ceiling_px"] == MAX_STAGE_A_AXIS_PX


def test_half_size_export_fails_the_view_with_export_dim_mismatch(tmp_path):
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px // 2, px // 2))
        result, diag = _run_view(tmp_path, doc, _FakeView(view_id=102))

    res = result["metadata"]["resolution"]
    assert res["dim_check"] == "mismatch"
    assert result["success"] is False
    assert result["failure_reason"] == "export_dim_mismatch"
    # Backed off rather than accepting the first bad export, and never
    # reported success on the way down.
    assert len(doc.pixel_sizes) > 1
    assert doc.pixel_sizes[-1] == 16
    assert any(e.get("callsite") == "export_dim_check" for e in diag.errors)


def test_unreadable_export_is_flagged_but_does_not_fail_the_view(tmp_path):
    """A read failure is not a pass, and not a mismatch either: the capture
    proceeds, flagged, because there is nothing to back off from."""
    with _install_fake_revit_db():
        doc = _FakeDoc()  # writes b"FAKE_TIFF", which has no TIFF header
        result, diag = _run_view(tmp_path, doc, _FakeView(view_id=103))

    res = result["metadata"]["resolution"]
    assert res["dim_check"] == "read_failed"
    assert res["actual_w"] is None and res["actual_h"] is None
    assert res["dim_read_error"]
    assert result["success"] is True
    assert result["failure_reason"] is None


def test_aa_read_failure_is_explicit_not_unchanged(tmp_path):
    """G4: a raising GetViewDisplayModel -> applied_smooth_edges = read_failed."""
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px, px))
        result, diag = _run_view(tmp_path, doc, _ViewDisplayModelRaises(view_id=104))

    meta = result["metadata"]
    assert meta["applied_smooth_edges"] == "read_failed"
    assert meta["smooth_edges_read_error"] == "RuntimeError"
    assert any(w.get("callsite") == "smooth_edges_capture" for w in diag.warnings)
    # Declared behaviour: the capture proceeds, flagged.
    assert result["success"] is True


class _DisplayModelWithoutSmoothEdges(object):
    """A ViewDisplayModel that simply has no SmoothEdges member, as on a
    host/view type that does not expose the setting."""

    def __init__(self, show_shadows):
        self.ShowShadows = show_shadows
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class _ViewWithoutSmoothEdges(_FakeView):
    def GetViewDisplayModel(self):
        return _DisplayModelWithoutSmoothEdges(self._show_shadows)

    def SetViewDisplayModel(self, dm):
        self._show_shadows = dm.ShowShadows
        self.display_model_writes.append((None, dm.ShowShadows))


def test_missing_smooth_edges_attribute_is_read_failed_not_off(tmp_path):
    """G1: no SmoothEdges member at all must not read as 'already off'."""
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px, px))
        result, diag = _run_view(tmp_path, doc, _ViewWithoutSmoothEdges(view_id=106))

    meta = result["metadata"]
    assert meta["applied_smooth_edges"] == "read_failed"
    assert meta["smooth_edges_read_error"] == "AttributeError"
    assert any(w.get("callsite") == "smooth_edges_capture" for w in diag.warnings)
    # Shadow suppression is untouched by this: that capture has always had
    # its own sentinel and still reports on its own terms.
    assert meta["applied_show_shadows"] is False


def test_writer_never_emits_the_legacy_unchanged_value(tmp_path):
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px, px))
        for view in (_FakeView(view_id=107),
                     _ViewDisplayModelRaises(view_id=108),
                     _ViewWithoutSmoothEdges(view_id=109)):
            result, _ = _run_view(tmp_path, doc, view)
            assert result["metadata"]["applied_smooth_edges"] != "unchanged"


def test_normalizer_maps_legacy_unchanged_to_read_failed():
    assert cib.normalize_applied_smooth_edges("unchanged") == "read_failed"
    assert cib.normalize_applied_smooth_edges("read_failed") == "read_failed"
    assert cib.normalize_applied_smooth_edges(False) is False
    assert cib.normalize_applied_smooth_edges("unchanged (failed)") == "unchanged (failed)"
    assert cib.normalize_applied_smooth_edges(None) is None


def test_aa_normal_path_is_unchanged_by_this_patch(tmp_path):
    """G4: the successful path keeps its existing values."""
    with _install_fake_revit_db():
        doc = _SizedDoc(lambda px: (px, px))
        result, diag = _run_view(tmp_path, doc, _FakeView(view_id=105))

    meta = result["metadata"]
    assert meta["applied_smooth_edges"] is False
    assert meta["smooth_edges_read_error"] is None


def test_read_failed_denies_the_decoder_high_confidence(tmp_path):
    """"read_failed" must not read as a clean AA-off capture downstream."""
    # The decode tool states numpy as a dependency and is meant to run
    # outside Dynamo; skip rather than fail the suite where it is absent,
    # matching tests/test_decode_stage_a_color_id.py.
    pytest.importorskip("numpy")
    from tools.decode_stage_a_color_id import _capture_reliability
    ok, reason = _capture_reliability(
        {"applied_display_style": "FlatColors", "applied_smooth_edges": "read_failed"})
    assert ok is False and "read_failed" in reason
    ok, reason = _capture_reliability(
        {"applied_display_style": "FlatColors", "applied_smooth_edges": False})
    assert ok is True and reason is None
