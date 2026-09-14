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


def _diagonal_contact_trace_worker(mask, queue):
    """Module-level (picklable under multiprocessing's "spawn" start method,
    required on Windows) worker for test_diagonal_pixel_contact_does_not_hang."""
    from tools import decode_stage_a_color_id as _dsc
    queue.put(_dsc._trace_loops_for_mask(mask))


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
            # The clean/nominal case: Stage A confirmed a flat-color,
            # anti-aliasing-off capture (color_id_buffer.py:560-624) --
            # see test_capture_reliability_downgrades_confidence_when_not_confirmed
            # for the degraded case.
            "applied_display_style": "FlatColors",
            "applied_smooth_edges": False,
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

    def test_diagonal_pixel_contact_does_not_hang_and_traces_separate_loops(self):
        # Regression test: two same-element pixels touching only at a shared
        # corner (a checkerboard 2x2 neighborhood) used to make the vertex ->
        # vertex edge dict silently drop one of the vertex's two outgoing
        # edges, so the traversal never returned to its start and hung
        # forever. This must terminate and must NOT merge the two pixels'
        # boundaries into one (wrong) loop.
        #
        # Uses multiprocessing rather than signal.SIGALRM/alarm: those are
        # POSIX-only and don't exist on Windows, which is this repo's actual
        # Revit/Dynamo development target (CLAUDE.md "Environment Notes") even
        # though this particular tool runs outside Dynamo -- a Windows-hosted
        # run of this test suite must not error out before even exercising
        # the tracer. multiprocessing.Process + join(timeout) + terminate()
        # is portable across POSIX and Windows ("spawn" is required on
        # Windows anyway, so the worker below is a module-level function to
        # stay picklable for it).
        import multiprocessing

        mask = np.array([[True, False], [False, True]], dtype=bool)
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        proc = ctx.Process(target=_diagonal_contact_trace_worker, args=(mask, queue))
        proc.start()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join()
            self.fail("diagonal-contact tracing hung (did not complete within 10s)")
        self.assertEqual(proc.exitcode, 0, "tracer worker process exited abnormally")
        loops = queue.get()

        self.assertEqual(len(loops), 2, "diagonal-touching pixels must trace as two separate loops")
        for loop in loops:
            self.assertAlmostEqual(abs(dsc._shoelace_area(loop)), 1.0)

    def test_feet_per_pixel_accounts_for_pixel_size_backoff(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            # Simulate Revit backing off from the requested width to half of it
            # (_set_pixel_size_with_backoff, color_id_buffer.py:285-316): the
            # TIFF is still `arr`'s actual 40px wide in this fixture, but the
            # sidecar must still report truthfully what was requested vs. accepted.
            sidecar["resolution"]["requested_pixel_size"] = 80
            sidecar["resolution"]["pixel_size"] = 40  # accepted (backed off) width
            from pathlib import Path
            doc = dsc.build_decoded_document(Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=None)
            # model_width_ft intended = (80/150)*96/12 = 4.2667 ft; actual image is
            # 40px wide, so feet_per_pixel must be 4.2667/40, NOT the pre-backoff
            # (80/150*96/12)/80 value the cancelling bug would have produced.
            expected = ((80.0 / 150.0) * 96.0 / 12.0) / 40.0
            self.assertAlmostEqual(doc["feet_per_pixel"], expected, places=9)
            # The bug's value (actual_px cancels out): view_scale/(export_dpi*12).
            buggy_value = 96.0 / (150.0 * 12.0)
            self.assertNotAlmostEqual(doc["feet_per_pixel"], buggy_value, places=6)

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

    def test_bounds_xy_read_automatically_from_sidecar(self):
        # color_id_buffer.py's crop_box_set step now crops the export to
        # raster.bounds_xy and persists that same rectangle as the sidecar's
        # "bounds_xy" field. Confirms the two pieces connect end-to-end: with
        # no --bounds CLI argument, the sidecar's own field alone is enough
        # to produce coordinate_space="view_uv" output.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["bounds_xy"] = [0.0, 0.0, 40.0, 30.0]
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            rc = dsc.main([sidecar_path])
            self.assertEqual(rc, 0)

            from pathlib import Path
            out_path = Path(sidecar_path).with_name(Path(sidecar_path).stem + ".decoded.json")
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "view_uv")
            self.assertEqual(doc["view_bounds_uv"], [0.0, 0.0, 40.0, 30.0])

    def test_explicit_bounds_overrides_sidecar_bounds_xy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["bounds_xy"] = [0.0, 0.0, 40.0, 30.0]
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            rc = dsc.main([sidecar_path, "--bounds", "100,200,140,230"])
            self.assertEqual(rc, 0)

            from pathlib import Path
            out_path = Path(sidecar_path).with_name(Path(sidecar_path).stem + ".decoded.json")
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "view_uv")
            self.assertEqual(doc["view_bounds_uv"], [100.0, 200.0, 140.0, 230.0])

    def test_null_bounds_xy_in_sidecar_falls_back_to_pixel_space(self):
        # color_id_buffer.py writes "bounds_xy": null when the crop could not
        # be applied (e.g. no raster provided, or the view has no CropBox).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["bounds_xy"] = None
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            rc = dsc.main([sidecar_path])
            self.assertEqual(rc, 0)

            from pathlib import Path
            out_path = Path(sidecar_path).with_name(Path(sidecar_path).stem + ".decoded.json")
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "pixel")
            self.assertIsNone(doc["view_bounds_uv"])

    def test_feet_per_pixel_unreliable_when_requested_size_was_clamped(self):
        # Regression test: color_id_buffer.py:398-399 clamps its own
        # pixel_size to MAX_STAGE_A_PIXEL_SIZE *before* writing it as
        # "requested_pixel_size" (color_id_buffer.py:930-933), so the true
        # pre-clamp desired width is never in the sidecar once clamping
        # occurs. feet_per_pixel must not silently report a wrong value
        # derived from the clamp value in that case.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["resolution"]["requested_pixel_size"] = dsc.MAX_STAGE_A_PIXEL_SIZE
            sidecar["resolution"]["pixel_size"] = dsc.MAX_STAGE_A_PIXEL_SIZE
            from pathlib import Path
            doc = dsc.build_decoded_document(Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=None)
            self.assertIsNone(doc["feet_per_pixel"])
            self.assertIsNotNone(doc["feet_per_pixel_unreliable_reason"])
            self.assertIn("MAX_STAGE_A_PIXEL_SIZE", doc["feet_per_pixel_unreliable_reason"])

    def test_capture_reliability_downgrades_confidence_when_not_confirmed(self):
        # Regression test: a view where Stage A fell back to Shading, failed
        # to change display style, or could not disable smooth edges
        # (color_id_buffer.py:560-624) is not a guaranteed exact-match,
        # anti-aliasing-off render. Emitting HIGH confidence regardless would
        # hand pipeline.py's AREAL+HIGH occlusion-authority gate
        # (pipeline.py:2443-2444) a decode that never earned it.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            doc = dsc.build_decoded_document(Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=None)
            self.assertTrue(doc["capture_reliable"])
            self.assertIsNone(doc["capture_unreliable_reason"])
            loops, confidence, strategy = dsc.reconstruct_areal_tuple(doc, 101)
            self.assertEqual(confidence, "HIGH")

        degraded_cases = [
            {"applied_display_style": "Shading", "applied_smooth_edges": False},
            {"applied_display_style": "FlatColors", "applied_smooth_edges": "unchanged (failed)"},
            {"applied_display_style": "unchanged", "applied_smooth_edges": "unchanged"},
        ]
        for overrides in degraded_cases:
            with tempfile.TemporaryDirectory() as tmp_dir:
                sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
                sidecar = json.load(open(sidecar_path))
                sidecar.update(overrides)
                doc = dsc.build_decoded_document(Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=None)
                self.assertFalse(doc["capture_reliable"], overrides)
                self.assertIsNotNone(doc["capture_unreliable_reason"], overrides)
                loops, confidence, strategy = dsc.reconstruct_areal_tuple(doc, 101)
                self.assertEqual(confidence, "MEDIUM", overrides)


    def test_grid_bounds_uv_reconstructs_raster_bounds_xy_from_offset(self):
        # color_id_buffer.py's compute_model_crop() promises: ADDING
        # model_crop_offset_uv to raster.bounds_xy's own corners recovers
        # the sidecar's "bounds_xy" (the rectangle actually rendered).
        # Equivalently, grid_bounds_uv (this tool's reconstruction of
        # raster.bounds_xy) = bounds_xy - offset, componentwise.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            # Simulate a narrowed capture: this fixture's TIFF/bounds_xy
            # (0,0,40,30) stands in for the narrower model-only crop that
            # was actually rendered; raster.bounds_xy (wider, annotation-
            # expanded) was (-5, -5, 45, 35).
            offset = (5.0, 5.0, -5.0, -5.0)  # bounds_xy=(0,0,40,30) = raster.bounds_xy + offset
            doc = dsc.build_decoded_document(
                Path(tiff_path), sidecar, Path(sidecar_path),
                bounds_uv=(0.0, 0.0, 40.0, 30.0),
                model_crop_offset_uv=offset,
            )
            self.assertEqual(doc["model_crop_offset_uv"], list(offset))
            self.assertEqual(doc["grid_bounds_uv"], [-5.0, -5.0, 45.0, 35.0])

    def test_grid_bounds_uv_absent_without_offset(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            doc = dsc.build_decoded_document(
                Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=(0.0, 0.0, 40.0, 30.0)
            )
            self.assertIsNone(doc["model_crop_offset_uv"])
            self.assertIsNone(doc["grid_bounds_uv"])

    def test_feet_per_pixel_uses_actual_crop_width_when_bounds_known(self):
        # Regression guard for the narrowed-crop case: feet_per_pixel must
        # be derived from the ACTUAL rendered rectangle's width (bounds_uv),
        # not from raster.W/cell_size_ft/scale (which, after color_id_
        # buffer.py's compute_model_crop() change, can now describe a wider
        # rectangle than what this particular TIFF was actually cropped to).
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            # resolution block still describes the WIDE raster (as color_id_
            # buffer.py's pixel_size computation is unchanged), but this
            # capture's TIFF (40px wide, per the fixture) was actually
            # cropped to a narrower 20ft-wide rectangle, not the 96ft-wide
            # estimate the resolution block alone would produce
            # ((150/150)*96/12 = 96.0 ft).
            sidecar["resolution"]["requested_pixel_size"] = 150
            sidecar["resolution"]["pixel_size"] = 40
            doc = dsc.build_decoded_document(
                Path(tiff_path), sidecar, Path(sidecar_path), bounds_uv=(0.0, 0.0, 20.0, 30.0)
            )
            # Correct: 20ft crop width / 40px actual width.
            self.assertAlmostEqual(doc["feet_per_pixel"], 20.0 / 40.0, places=9)
            # The stale, estimate-based value this would have been before
            # the fix ((150/150)*96/12/40 = 2.4) must NOT be what's reported.
            self.assertNotAlmostEqual(doc["feet_per_pixel"], 2.4, places=6)

    def test_round_trip_narrow_crop_decodes_to_same_uv_as_wide_crop(self):
        # The correctness proof for the whole crop-narrowing change: a
        # synthetic element at a fixed physical view-local UV position,
        # captured (a) the old way -- rendered across the full, wide
        # bounds_xy -- and (b) the new way -- rendered only across a
        # narrower model-only crop entirely inside bounds_xy -- must decode
        # to the IDENTICAL final UV position either way. This is a genuine
        # rectangle-to-rectangle relationship (a fixed offset PLUS a
        # different scale, since narrowing means the same element occupies
        # more pixels within a smaller crop), not a simple additive point
        # shift on top of a naive wide-crop decode -- see this tool's own
        # module docstring (MODEL_CROP_OFFSET_UV section) for why.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Element 501 occupies view-local UV rect u:[12,16], v:[8,11]
            # (4ft x 3ft), comfortably inside both crops below.
            color = (7, 8, 9)

            # (a) OLD: rendered across the full wide bounds_xy=(0,0,40,30)
            # at 1 ft/px (40x30 image) -- same convention as _make_fixture.
            wide_w, wide_h = 40, 30
            arr_wide = np.full((wide_h, wide_w, 3), 255, dtype=np.uint8)
            arr_wide[19:22, 12:16] = color  # v:[8,11]->rows[19,22), u:[12,16]->cols[12,16)
            wide_tiff = os.path.join(tmp_dir, "wide.tiff")
            Image.fromarray(arr_wide, mode="RGB").save(wide_tiff)
            wide_sidecar = {
                "view_id": 1,
                "resolution": {"pixel_size": wide_w, "requested_pixel_size": wide_w,
                                "export_dpi": 150.0, "view_scale": 96.0},
                "color_assignment_map": {"501": list(color)},
                "applied_display_style": "FlatColors",
                "applied_smooth_edges": False,
            }
            wide_sidecar_path = os.path.join(tmp_dir, "wide.json")
            with open(wide_sidecar_path, "w") as f:
                json.dump(wide_sidecar, f)

            # (b) NEW: rendered across a narrower model-only crop entirely
            # inside the wide bounds_xy, at the same 1 ft/px scale.
            # render_bounds=(10,5,20,15); offset = render_bounds - bounds_xy
            # = (10-0, 5-0, 20-40, 15-30) = (10, 5, -20, -15).
            narrow_w, narrow_h = 10, 10
            arr_narrow = np.full((narrow_h, narrow_w, 3), 255, dtype=np.uint8)
            # u:[12,16]-10 -> cols[2,6); v:[8,11], ymax=15 -> rows[15-11,15-8)=[4,7)
            arr_narrow[4:7, 2:6] = color
            narrow_tiff = os.path.join(tmp_dir, "narrow.tiff")
            Image.fromarray(arr_narrow, mode="RGB").save(narrow_tiff)
            narrow_sidecar = {
                "view_id": 1,
                "resolution": {"pixel_size": narrow_w, "requested_pixel_size": narrow_w,
                                "export_dpi": 150.0, "view_scale": 96.0},
                "color_assignment_map": {"501": list(color)},
                "applied_display_style": "FlatColors",
                "applied_smooth_edges": False,
                "bounds_xy": [10.0, 5.0, 20.0, 15.0],
                "model_crop_offset_uv": [10.0, 5.0, -20.0, -15.0],
            }
            narrow_sidecar_path = os.path.join(tmp_dir, "narrow.json")
            with open(narrow_sidecar_path, "w") as f:
                json.dump(narrow_sidecar, f)

            doc_wide = dsc.build_decoded_document(
                Path(wide_tiff), wide_sidecar, Path(wide_sidecar_path), bounds_uv=(0.0, 0.0, 40.0, 30.0)
            )
            # decode_one() end-to-end for the narrow capture, exactly as a
            # real caller would invoke it (reads "bounds_xy" and
            # "model_crop_offset_uv" from the sidecar automatically).
            out_path = dsc.decode_one(Path(narrow_sidecar_path))
            doc_narrow = json.loads(out_path.read_text())

            self.assertEqual(doc_narrow["grid_bounds_uv"], [0.0, 0.0, 40.0, 30.0])

            def _bbox(doc):
                pts = [p for loop in doc["elements"]["501"]["loops"] for p in loop["points"]]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                return (min(xs), min(ys), max(xs), max(ys))

            bbox_wide = _bbox(doc_wide)
            bbox_narrow = _bbox(doc_narrow)
            for a, b in zip(bbox_wide, bbox_narrow):
                self.assertAlmostEqual(a, b, places=6)
            # Sanity: the shared position really is the (12,8)-(16,11) rect
            # this test set out to place, on both sides.
            for a, expected in zip(bbox_wide, (12.0, 8.0, 16.0, 11.0)):
                self.assertAlmostEqual(a, expected, places=6)

    def test_decode_one_ignores_sidecar_offset_when_bounds_overridden(self):
        # An explicit --bounds override means the caller is asserting a
        # crop rectangle other than what color_id_buffer.py recorded; the
        # sidecar's own offset (computed against ITS recorded bounds_xy)
        # must not be applied against a different, caller-supplied rectangle.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["bounds_xy"] = [0.0, 0.0, 40.0, 30.0]
            sidecar["model_crop_offset_uv"] = [5.0, 5.0, -5.0, -5.0]
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            out_path = dsc.decode_one(Path(sidecar_path), bounds_uv=(100.0, 200.0, 140.0, 230.0))
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["view_bounds_uv"], [100.0, 200.0, 140.0, 230.0])
            self.assertIsNone(doc["model_crop_offset_uv"])
            self.assertIsNone(doc["grid_bounds_uv"])

    def test_older_sidecar_without_offset_field_still_decodes(self):
        # Backward compatibility: a sidecar written before this field
        # existed has no "model_crop_offset_uv" key at all.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            sidecar_path, tiff_path, arr = self._make_fixture(tmp_dir)
            sidecar = json.load(open(sidecar_path))
            sidecar["bounds_xy"] = [0.0, 0.0, 40.0, 30.0]
            self.assertNotIn("model_crop_offset_uv", sidecar)
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            out_path = dsc.decode_one(Path(sidecar_path))
            doc = json.loads(out_path.read_text())
            self.assertEqual(doc["coordinate_space"], "view_uv")
            self.assertIsNone(doc["model_crop_offset_uv"])
            self.assertIsNone(doc["grid_bounds_uv"])


if __name__ == "__main__":
    unittest.main()
