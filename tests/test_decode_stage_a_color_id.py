"""
Unit tests for tools/decode_stage_a_color_id.py (VOP Stage A HOST color-ID
decode tool).

Requires numpy and Pillow (the tool's own stated dependencies -- this test
skips cleanly if either is unavailable rather than failing the whole suite,
since this tool is explicitly meant to run outside the Dynamo/Revit
environment the rest of this repo's tests target).

No real Stage A TIFF/sidecar capture exists in this repo (color_id_buffer.py
requires a live Revit document to produce one -- see tests/test_color_id_
buffer.py's own docstring). This test builds a synthetic TIFF + sidecar pair
that matches export_color_id_buffer_view()'s documented output shape
(color_id_buffer.py:927-954) and decodes that instead.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import numpy as np
    from PIL import Image
    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

if _DEPS_AVAILABLE:
    from tools import decode_stage_a_color_id as dsc


@unittest.skipUnless(_DEPS_AVAILABLE, "numpy and Pillow required for this tool's own tests")
class TestDecodeStageAColorId(unittest.TestCase):
    def _make_fixture(self, tmp_dir):
        w, h = 40, 30
        arr = np.full((h, w, 3), 255, dtype=np.uint8)  # white background

        # elem 101: solid rectangle, rows 5..14, cols 5..19.
        arr[5:15, 5:20] = (10, 20, 30)

        # elem 102: donut -- outer rows 5..19 cols 22..34, hole rows 9..14 cols 26..30.
        arr[5:20, 22:35] = (40, 50, 60)
        arr[9:15, 26:31] = 255

        tiff_path = os.path.join(tmp_dir, "test_view_1001.tiff")
        Image.fromarray(arr, mode="RGB").save(tiff_path)

        sidecar = {
            "view_id": 1001,
            "resolution": {
                "pixel_size": w,
                "requested_pixel_size": w,
                "export_dpi": 150.0,
                "view_scale": 96.0,
            },
            "color_assignment_map": {"101": [10, 20, 30], "102": [40, 50, 60]},
            "link_color_assignment_map": {},
            "tiff_path": tiff_path,
        }
        sidecar_path = os.path.join(tmp_dir, "test_view_1001.json")
        with open(sidecar_path, "w") as f:
            json.dump(sidecar, f)
        return sidecar_path, tiff_path, arr

    def test_decoded_ids_match_color_assignment_map_exactly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            rgb = dsc._load_rgb_array(tiff_path)
            id_array, stats = dsc.decode_ids(rgb, sidecar["color_assignment_map"])

            mask_101 = np.all(arr == (10, 20, 30), axis=2)
            mask_102 = np.all(arr == (40, 50, 60), axis=2)
            bg_mask = np.all(arr == (255, 255, 255), axis=2)

            self.assertTrue(np.all(id_array[mask_101] == 101))
            self.assertTrue(np.all(id_array[mask_102] == 102))
            self.assertTrue(np.all(id_array[bg_mask] == dsc.BACKGROUND_ELEMENT_ID))
            self.assertEqual(stats["off_palette_foreground_pixel_count"], 0)

    def test_loop_tracing_produces_correct_areas_and_hole_flags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            rgb = dsc._load_rgb_array(tiff_path)
            id_array, _ = dsc.decode_ids(rgb, sidecar["color_assignment_map"])

            count_101 = int(np.count_nonzero(np.all(arr == (10, 20, 30), axis=2)))
            count_102 = int(np.count_nonzero(np.all(arr == (40, 50, 60), axis=2)))

            loops_101 = dsc._loops_for_element(id_array, 101)
            self.assertEqual(len(loops_101), 1)
            self.assertFalse(loops_101[0]["is_hole"])
            self.assertAlmostEqual(loops_101[0]["area_px"], count_101, places=6)

            loops_102 = dsc._loops_for_element(id_array, 102)
            self.assertEqual(len(loops_102), 2, "donut shape must produce an outer loop and a hole loop")
            outer = [l for l in loops_102 if not l["is_hole"]]
            holes = [l for l in loops_102 if l["is_hole"]]
            self.assertEqual(len(outer), 1)
            self.assertEqual(len(holes), 1)
            net_area = outer[0]["area_px"] - holes[0]["area_px"]
            self.assertAlmostEqual(net_area, count_102, places=6)

    def test_reconstruct_areal_tuple_matches_extract_areal_geometry_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            from pathlib import Path
            doc = dsc.build_decoded_document(
                Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=(0.0, 0.0, 40.0, 30.0)
            )

            for elem_id in (101, 102):
                loops, confidence, strategy = dsc.reconstruct_areal_tuple(doc, elem_id)
                self.assertIsInstance(loops, list)
                self.assertGreaterEqual(len(loops), 1)
                self.assertIn(confidence, ("HIGH", "MEDIUM", "LOW", None))
                self.assertIsInstance(strategy, str)
                for loop in loops:
                    self.assertTrue({"points", "is_hole", "open", "strategy"}.issubset(loop.keys()))
                    self.assertIsInstance(loop["is_hole"], bool)
                    self.assertIsInstance(loop["open"], bool)
                    for pt in loop["points"]:
                        self.assertEqual(len(pt), 2)

            # areal_extraction.py:157-163: "Returns (None, None, 'failed') if
            # all strategies fail." -- an element absent from the decode must
            # reconstruct to exactly that documented failure triple.
            loops, confidence, strategy = dsc.reconstruct_areal_tuple(doc, 999999)
            self.assertEqual((loops, confidence, strategy), (None, None, "failed"))

    def test_uv_conversion_matches_established_probe_corner_convention(self):
        # tools/analyze_stage_a_probe.py's own tested pixel-CENTER mapping is
        # u = u_min + (x+0.5)*UV_width/image_width; v = v_max - (y+0.5)*UV_height/image_height.
        # This tool traces edges at pixel CORNERS (not centers), so the +0.5 is
        # dropped -- verified here at the two extreme corners.
        bounds = (100.0, 200.0, 140.0, 230.0)
        w, h = 40, 30
        u, v = dsc._pixel_corner_to_uv(0, 0, bounds, w, h)
        self.assertAlmostEqual(u, 100.0)
        self.assertAlmostEqual(v, 230.0)
        u, v = dsc._pixel_corner_to_uv(w, h, bounds, w, h)
        self.assertAlmostEqual(u, 140.0)
        self.assertAlmostEqual(v, 200.0)

    def test_cli_writes_sibling_decoded_json_without_overwriting_sidecar(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            rc = dsc.main([sidecar_path, "--bounds", "0,0,40,30"])
            self.assertEqual(rc, 0)

            from pathlib import Path
            out_path = Path(sidecar_path).with_name(Path(sidecar_path).stem + ".decoded.json")
            self.assertTrue(out_path.exists())
            self.assertTrue(os.path.exists(sidecar_path), "input sidecar must not be overwritten")

            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "view_uv")
            self.assertEqual(doc["element_count_with_geometry"], 2)
            self.assertEqual(doc["schema_version"], dsc.SCHEMA_VERSION)

    def test_pixel_space_output_when_bounds_omitted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            rc = dsc.main([sidecar_path])
            self.assertEqual(rc, 0)
            from pathlib import Path
            out_path = Path(sidecar_path).with_name(Path(sidecar_path).stem + ".decoded.json")
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "pixel")
            self.assertIsNone(doc["view_bounds_uv"])


if __name__ == "__main__":
    unittest.main()
