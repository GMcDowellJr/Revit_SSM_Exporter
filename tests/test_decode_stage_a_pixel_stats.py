"""S1: the decoder's pixel stats split what the legacy counts hid.

Link pixels were counted as off-palette (link_category_color_map was never
read) and pure black as BACKGROUND, so black view-symbol text vanished from
ink. pixel_stats counts every pixel once; background is white only there.
Asserted on the decoded FILE. id_array and the two legacy counts are
unchanged (the controls), so existing readers see the same numbers.
"""
import json
import os

import numpy as np
from PIL import Image

from tools import decode_stage_a_color_id as dsc

from tests.test_register_stage_a_annotation import _write_pair

LINK = (6, 246, 42)


def _capture(tmp_path):
    w, h = 40, 30
    arr = np.full((h, w, 3), 255, dtype=np.uint8)
    arr[2:6, 2:12] = (10, 20, 30)       # element 101: 40 px
    arr[10:14, 2:7] = LINK              # link Floors: 20 px
    arr[20:22, 2:5] = (0, 0, 0)         # black text: 6 px
    arr[25, 30] = (123, 45, 67)         # residual off-palette: 1 px
    tiff = os.path.join(str(tmp_path), "v_1.tiff")
    Image.fromarray(arr, mode="RGB").save(tiff)
    sidecar = {"view_id": 1, "tiff_path": tiff,
               "color_assignment_map": {"101": [10, 20, 30]},
               "link_category_color_map": {"Floors": list(LINK), "Walls": [1, 2, 3]},
               "applied_display_style": "FlatColors", "applied_smooth_edges": False}
    path = os.path.join(str(tmp_path), "v_1.json")
    with open(path, "w") as handle:
        json.dump(sidecar, handle)
    return path, w * h


def test_s1_every_pixel_counted_once_in_the_FILE(tmp_path):
    path, total = _capture(tmp_path)
    doc = json.loads(dsc.decode_one(__import__("pathlib").Path(path)).read_text())
    stats = doc["pixel_stats"]
    assert stats["element_px"] == 40
    assert stats["link_category_px"] == {"Floors": 20, "Walls": 0}
    assert stats["link_category_px_total"] == 20
    assert stats["black_px"] == 6
    assert stats["off_palette_px"] == 1
    assert stats["registration_mark_px"] == 0
    assert stats["background_white_px"] == total - 40 - 20 - 6 - 1
    parts = (stats["element_px"] + stats["registration_mark_px"]
             + stats["background_white_px"] + stats["black_px"]
             + stats["link_category_px_total"] + stats["off_palette_px"])
    assert parts == stats["total_px"] == total


def test_s1_control_the_legacy_counts_and_ids_are_unchanged(tmp_path):
    path, total = _capture(tmp_path)
    doc = json.loads(dsc.decode_one(__import__("pathlib").Path(path)).read_text())
    # Legacy semantics, deliberately kept: link + residual are "off-palette",
    # black + white + link + residual decode as background.
    assert doc["off_palette_foreground_pixel_count"] == 21
    assert doc["background_pixel_count"] == total - 40
    assert set(doc["elements"]) == {"101"}


def test_s1_registration_mark_pixels_are_their_own_count(tmp_path):
    _anno, model_path, _c = _write_pair(tmp_path)
    doc = json.loads(dsc.decode_one(model_path).read_text())
    stats = doc["pixel_stats"]
    assert stats["registration_mark_px"] == doc["registration_marks"]["mark_pixels_subtracted"]
    assert stats["registration_mark_px"] > 0
    assert stats["off_palette_px"] == 0
