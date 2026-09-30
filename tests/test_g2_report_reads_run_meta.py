"""Codex, PR #221: since C6 a sidecar records only ACHIEVED values and the
requested dpi lives in the run's run_meta.json. The G2 excursion report read
the sidecar alone, so it printed requested_export_dpi n/a for every current
capture. Driven on a sidecar the CURRENT writer produced, with the control
that without run_meta it really is absent (else the test proves nothing)."""
import importlib.util
import json
import os

from tests.test_frame_export_geometry_call_site import _export, _raster_at
from vop_interwoven.core.math_utils import Bounds2D

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _g2():
    spec = importlib.util.spec_from_file_location(
        "g2_bbox_excursion_report",
        os.path.join(_ROOT, "tools", "notes", "g2_bbox_excursion_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_report_restores_the_requested_dpi_from_run_meta(tmp_path):
    from pathlib import Path
    g2 = _g2()
    result = _export(tmp_path, _raster_at(1.0, Bounds2D(12.0, 9.0, 40.0, 30.0)))
    sidecar = Path(result["sidecar_path"])
    # The fake export writes no real image; a blank one of the recorded size
    # is enough, since this asserts on the capture fields only.
    import numpy as np
    from PIL import Image
    frame = result["metadata"]["frame"]
    Image.fromarray(np.full((int(frame["actual_h"] or 32), int(frame["actual_w"] or 32), 3),
                            255, np.uint8)).save(str(sidecar.with_suffix(".tiff")), format="TIFF")
    assert "requested_export_dpi" not in json.loads(sidecar.read_text())["frame"]
    mappings = {"baseline": g2.uv_baseline, "patched": g2.uv_patched}

    no_meta = g2.measure_view(sidecar, mappings)
    assert "skip" not in no_meta, no_meta
    assert no_meta["capture"]["requested_export_dpi"] is None      # the control

    (sidecar.parent / "run_meta.json").write_text(json.dumps(
        {"config": {"color_id_buffer_export_dpi": 175.0}}))
    with_meta = g2.measure_view(sidecar, mappings)
    assert with_meta["capture"]["requested_export_dpi"] == 175.0
