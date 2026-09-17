"""D1 gate tests: ExportImage aspect-clamp padding in the pixel->UV mapping.

Revit's ImageExportOptions/ExportImage clamps a capture's aspect ratio at
10:1 and pads the short axis to reach it. Section 1 of the 2026-09-17 byColor
run came back 9960x996 px -- exactly 10.000. The clamp is ExportImage's own
behaviour, cannot be disabled, and is recorded in no sidecar field.

tools/decode_stage_a_color_id.py used to stretch u and v independently across
the whole image, which implies non-square pixels and silently displaced every
decoded point on the padded axis. These tests pin the corrected model:

    fpp   = max(crop_u_ft / actual_w, crop_v_ft / actual_h)
    pad_x = (actual_w - crop_u_ft / fpp) / 2
    pad_y = (actual_h - crop_v_ft / fpp) / 2
    u     = xmin + (x - pad_x) * fpp
    v     = ymax - (y - pad_y) * fpp

EVIDENCE NOTE. The task's gates G2/G3/G7 are specified against two real runs
(byColor 20260917T104744, byGeom 20260917T112657). Neither run is present in
this repository or in the container these tests run in, and neither is a
Stage A capture reproducible without a live Revit document (see tests/test_
color_id_buffer.py). Every fixture below is therefore synthetic, built to the
same pixel geometry the runs exhibit (notably Section 1's 10.000 aspect), and
the gate numbers reported from them are labelled as such.
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


def _old_pixel_corner_to_uv(x, y, bounds_uv, image_w, image_h):
    """The pre-D1 mapping, kept here only to quantify what the fix moved.

    Verbatim from decode_stage_a_color_id.py @ a9051f2 -- u and v stretched
    independently across the full padded image.
    """
    xmin, ymin, xmax, ymax = bounds_uv
    u = xmin + (float(x) / float(image_w)) * (xmax - xmin)
    v = ymax - (float(y) / float(image_h)) * (ymax - ymin)
    return (u, v)


def _palette(n):
    """n distinct, non-black non-white RGB triples for a color_assignment_map."""
    out = {}
    for i in range(n):
        elem_id = 1000 + i
        out[str(elem_id)] = [(i % 200) + 20, ((i * 7) % 200) + 20, ((i * 13) % 200) + 20]
    return out


class _Capture:
    """A synthetic padded capture: an image, a crop rectangle, and the exact
    pixel band each element was painted into (so "its own bbox" in UV is a
    computed fact here, not a measurement)."""

    def __init__(self, tmp_dir, image_w, image_h, crop, bands, requested_axis="width",
                 name="cap", sidecar_dims=None):
        self.crop = crop
        self.image_w = image_w
        self.image_h = image_h
        self.bands = bands  # {elem_id_str: (c0, c1, r0, r1)} pixel half-open
        arr = np.full((image_h, image_w, 3), 255, dtype=np.uint8)
        self.color_map = _palette(len(bands))
        self.band_by_id = {}
        for (elem_id_str, rgb), band in zip(sorted(self.color_map.items()), bands):
            c0, c1, r0, r1 = band
            arr[r0:r1, c0:c1] = tuple(rgb)
            self.band_by_id[elem_id_str] = band
        self.tiff_path = os.path.join(tmp_dir, name + ".tiff")
        Image.fromarray(arr, mode="RGB").save(self.tiff_path)

        resolution = {
            "pixel_size": image_w if requested_axis == "width" else image_h,
            "requested_pixel_size": image_w if requested_axis == "width" else image_h,
            "export_dpi": 150.0,
            "view_scale": 96.0,
            "requested_axis": requested_axis,
            "dim_check": "pass",
            "pre_cap_px": image_w if requested_axis == "width" else image_h,
        }
        aw, ah = sidecar_dims if sidecar_dims else (image_w, image_h)
        resolution["actual_w"], resolution["actual_h"] = aw, ah
        self.sidecar = {
            "view_id": 4242,
            "resolution": resolution,
            "color_assignment_map": self.color_map,
            "applied_display_style": "FlatColors",
            "applied_smooth_edges": False,
            "bounds_xy": list(crop),
        }
        self.sidecar_path = os.path.join(tmp_dir, name + ".json")
        with open(self.sidecar_path, "w") as f:
            json.dump(self.sidecar, f)

    def expected_uv_bbox(self, elem_id_str, fpp, pad_x, pad_y):
        c0, c1, r0, r1 = self.band_by_id[elem_id_str]
        xmin, ymin, xmax, ymax = self.crop
        return (
            xmin + (c0 - pad_x) * fpp,
            ymax - (r1 - pad_y) * fpp,
            xmin + (c1 - pad_x) * fpp,
            ymax - (r0 - pad_y) * fpp,
        )

    def decode(self):
        from pathlib import Path
        return dsc.build_decoded_document(
            Path(self.tiff_path), self.sidecar, Path(self.sidecar_path),
            bounds_uv=tuple(float(c) for c in self.crop))


def _decoded_v_range(doc, elem_id_str):
    pts = [p for loop in doc["elements"][elem_id_str]["loops"] for p in loop["points"]]
    vs = [p[1] for p in pts]
    return (min(vs), max(vs))


@unittest.skipUnless(_DEPS_AVAILABLE, "numpy and Pillow required for this tool's own tests")
class TestClampPadGeometry(unittest.TestCase):

    # ---------------------------------------------------------------- G1
    def test_g1_known_pad_is_recovered(self):
        """G1: a deliberately known pad comes back to within 0.5 px."""
        # 80 x 4 ft crop into a 400 x 40 px image. Square pixels put the
        # content in 400 x 20 px, so the clamp padded 10 px above and below.
        fpp, pad_x, pad_y = dsc._clamp_pad_geometry((0.0, 0.0, 80.0, 4.0), 400, 40)
        self.assertAlmostEqual(fpp, 0.2, places=12)
        self.assertLessEqual(abs(pad_x - 0.0), 0.5, "pad_x {0}".format(pad_x))
        self.assertLessEqual(abs(pad_y - 10.0), 0.5, "pad_y {0}".format(pad_y))

        # And the same pad on the other axis: 4 x 80 ft into 40 x 400 px.
        fpp, pad_x, pad_y = dsc._clamp_pad_geometry((0.0, 0.0, 4.0, 80.0), 40, 400)
        self.assertAlmostEqual(fpp, 0.2, places=12)
        self.assertLessEqual(abs(pad_x - 10.0), 0.5, "pad_x {0}".format(pad_x))
        self.assertLessEqual(abs(pad_y - 0.0), 0.5, "pad_y {0}".format(pad_y))

    def test_g1_round_trip_through_the_public_mapping(self):
        """The pad the helper recovers is the pad the mapping applies."""
        crop = (10.0, 5.0, 90.0, 9.0)  # 80 x 4 ft
        geom = dsc._clamp_pad_geometry(crop, 400, 40)
        fpp, pad_x, pad_y = geom
        # The content's top-left corner in pixels is (pad_x, pad_y); it must
        # map to the crop's own (xmin, ymax).
        u, v = dsc._pixel_corner_to_uv(pad_x, pad_y, crop, 400, 40, geometry=geom)
        self.assertAlmostEqual(u, 10.0, places=9)
        self.assertAlmostEqual(v, 9.0, places=9)
        # ...and the bottom-right content corner to (xmax, ymin).
        u, v = dsc._pixel_corner_to_uv(400 - pad_x, 40 - pad_y, crop, 400, 40, geometry=geom)
        self.assertAlmostEqual(u, 90.0, places=9)
        self.assertAlmostEqual(v, 5.0, places=9)

    # ---------------------------------------------------------------- G2
    def test_g2_padded_capture_decodes_every_element_inside_its_own_bbox(self):
        """G2 (synthetic stand-in): elements decoding with V outside their own
        bbox goes to 0, and the worst-case V error is <= 0.10 ft.

        Section 1's real capture is unavailable here (see module docstring),
        so this reproduces its geometry: a 10.000 aspect image whose short
        axis is padded, carrying 81 elements -- the same element count the
        run reported -- in distinct row bands across the crop.
        """
        import tempfile
        # Section 1's own measured export size, 9960 x 996 px -- exactly
        # 10.000, which is the clamp firing.
        image_w, image_h = 9960, 996
        fpp_true = 0.02
        crop = (0.0, 0.0, image_w * fpp_true, 8.0)  # 199.20 x 8.00 ft
        content_h = int(round(8.0 / fpp_true))      # 400 px
        pad_y_true = (image_h - content_h) / 2.0    # 298 px

        n = 81
        bands = []
        for i in range(n):
            c0 = 40 + i * 120
            # Spread the bands across the full height of the content strip so
            # the pre-fix error is sampled at both ends of it, not just near
            # the top where the two mappings still nearly agree.
            r0 = int(pad_y_true) + int(i * (content_h - 8) / float(n - 1))
            bands.append((c0, c0 + 80, r0, r0 + 8))

        with tempfile.TemporaryDirectory() as tmp_dir:
            cap = _Capture(tmp_dir, image_w, image_h, crop, bands, name="section1_like")
            doc = cap.decode()
            fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, image_w, image_h)
            self.assertAlmostEqual(fpp, fpp_true, places=12)
            self.assertAlmostEqual(pad_y, pad_y_true, places=9)

            self.assertEqual(doc["element_count_with_geometry"], n)

            out_of_bbox_new = 0
            worst_new = 0.0
            out_of_bbox_old = 0
            worst_old = 0.0
            for elem_id_str, band in cap.band_by_id.items():
                _, exp_vmin, _, exp_vmax = cap.expected_uv_bbox(elem_id_str, fpp, pad_x, pad_y)
                got_vmin, got_vmax = _decoded_v_range(doc, elem_id_str)
                err = max(abs(got_vmin - exp_vmin), abs(got_vmax - exp_vmax))
                worst_new = max(worst_new, err)
                if got_vmin < exp_vmin - 1e-9 or got_vmax > exp_vmax + 1e-9:
                    out_of_bbox_new += 1

                # What the pre-D1 mapping would have produced for the same pixels.
                c0, c1, r0, r1 = band
                _, old_vmax = _old_pixel_corner_to_uv(c0, r0, crop, image_w, image_h)
                _, old_vmin = _old_pixel_corner_to_uv(c0, r1, crop, image_w, image_h)
                old_err = max(abs(old_vmin - exp_vmin), abs(old_vmax - exp_vmax))
                worst_old = max(worst_old, old_err)
                if old_vmin < exp_vmin - 1e-9 or old_vmax > exp_vmax + 1e-9:
                    out_of_bbox_old += 1

            print("\n[G2] pre-fix  out-of-bbox {0}/{1}, worst V error {2:.4f} ft".format(
                out_of_bbox_old, n, worst_old))
            print("[G2] post-fix out-of-bbox {0}/{1}, worst V error {2:.4f} ft".format(
                out_of_bbox_new, n, worst_new))

            self.assertEqual(out_of_bbox_new, 0)
            self.assertLessEqual(worst_new, 0.10)
            # The fixture has to actually exercise the defect, or the gate
            # proves nothing.
            self.assertGreater(out_of_bbox_old, 0)

    # ---------------------------------------------------------------- G3
    def test_g3_unpadded_captures_compute_a_near_zero_pad(self):
        """G3: where the clamp did not fire, both pads land in [-1.0, +2.0] px.

        Unpadded means the crop's aspect equals the image's, up to the
        integer rounding of the pixel counts -- which is the only thing that
        can move the pad off zero at all.
        """
        cases = [
            # (crop_u_ft, crop_v_ft, fpp) -> image dims rounded from them
            (96.38, 71.20, 0.0125),
            (240.0, 180.0, 0.05),
            (19.92, 14.55, 0.002),
            (301.7, 47.9, 0.04),
            (8.0, 8.0, 0.01),
            (1234.5, 678.9, 0.25),
        ]
        worst_low, worst_high = 0.0, 0.0
        for crop_u, crop_v, fpp_true in cases:
            image_w = max(1, int(round(crop_u / fpp_true)))
            image_h = max(1, int(round(crop_v / fpp_true)))
            fpp, pad_x, pad_y = dsc._clamp_pad_geometry(
                (0.0, 0.0, crop_u, crop_v), image_w, image_h)
            for name, pad in (("pad_x", pad_x), ("pad_y", pad_y)):
                self.assertGreaterEqual(
                    pad, -1.0, "{0}={1} on {2}x{3} px".format(name, pad, image_w, image_h))
                self.assertLessEqual(
                    pad, 2.0, "{0}={1} on {2}x{3} px".format(name, pad, image_w, image_h))
            worst_low = min(worst_low, pad_x, pad_y)
            worst_high = max(worst_high, pad_x, pad_y)
        print("\n[G3] unpadded pad range over {0} synthetic views: "
              "[{1:.4f}, {2:.4f}] px".format(len(cases), worst_low, worst_high))

    # ---------------------------------------------------------------- G4
    def test_g4_vertical_fit_decodes_within_the_same_error_bound(self):
        """G4: a capture whose requested_axis is the vertical axis, with the
        clamp firing on the WIDTH, decodes to the same bound as G2.

        No such view exists in the byColor run available here, so it is
        constructed -- which the task explicitly permits.
        """
        import tempfile
        image_w, image_h = 100, 996
        fpp_true = 0.02
        crop = (0.0, 0.0, 0.8, image_h * fpp_true)  # 0.80 x 19.92 ft
        content_w = int(round(0.8 / fpp_true))      # 40 px
        pad_x_true = (image_w - content_w) / 2.0    # 30 px

        n = 40
        bands = []
        for i in range(n):
            r0 = 4 + i * 24
            c0 = int(pad_x_true) + (i % 18) * 2
            bands.append((c0, c0 + 2, r0, r0 + 8))

        with tempfile.TemporaryDirectory() as tmp_dir:
            cap = _Capture(tmp_dir, image_w, image_h, crop, bands,
                           requested_axis="height", name="vertical_fit")
            doc = cap.decode()
            fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, image_w, image_h)
            self.assertAlmostEqual(fpp, fpp_true, places=12)
            self.assertAlmostEqual(pad_x, pad_x_true, places=9)
            self.assertAlmostEqual(doc["feet_per_pixel"], fpp_true, places=12)

            worst = 0.0
            out_of_bbox = 0
            for elem_id_str in cap.band_by_id:
                _, exp_vmin, _, exp_vmax = cap.expected_uv_bbox(elem_id_str, fpp, pad_x, pad_y)
                got_vmin, got_vmax = _decoded_v_range(doc, elem_id_str)
                worst = max(worst, abs(got_vmin - exp_vmin), abs(got_vmax - exp_vmax))
                if got_vmin < exp_vmin - 1e-9 or got_vmax > exp_vmax + 1e-9:
                    out_of_bbox += 1
            print("\n[G4] vertical fit: out-of-bbox {0}/{1}, worst V error "
                  "{2:.4f} ft".format(out_of_bbox, n, worst))
            self.assertEqual(out_of_bbox, 0)
            self.assertLessEqual(worst, 0.10)

    def test_g4_crop_numerator_follows_requested_axis(self):
        """The crop path no longer divides crop WIDTH by measured_w
        regardless: under vertical fit with the clamp on the width, the
        number comes from the height."""
        crop = (0.0, 0.0, 0.8, 19.92)
        fpp, _, _ = dsc._clamp_pad_geometry(crop, 100, 996)
        self.assertAlmostEqual(fpp, 19.92 / 996, places=12)   # height axis
        self.assertNotAlmostEqual(fpp, 0.8 / 100, places=6)   # not the width

    # ---------------------------------------------------------------- G5
    def test_g5_square_capture_has_zero_pad_on_both_axes(self):
        """G5: actual_w == actual_h decodes with pad_x == pad_y == 0 within
        0.5 px, and feet_per_pixel is identical on both axes."""
        import tempfile
        image_w = image_h = 300
        crop = (0.0, 0.0, 60.0, 60.0)
        fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, image_w, image_h)
        self.assertLessEqual(abs(pad_x), 0.5)
        self.assertLessEqual(abs(pad_y), 0.5)
        self.assertAlmostEqual(pad_x, pad_y, places=12)
        # Identical on both axes, so neither axis is the "unpadded" one.
        self.assertAlmostEqual(60.0 / image_w, 60.0 / image_h, places=12)
        self.assertAlmostEqual(fpp, 60.0 / image_w, places=12)

        with tempfile.TemporaryDirectory() as tmp_dir:
            bands = [(20, 60, 30, 70), (120, 200, 140, 260)]
            cap = _Capture(tmp_dir, image_w, image_h, crop, bands, name="square")
            doc = cap.decode()
            self.assertAlmostEqual(doc["feet_per_pixel"], 0.2, places=12)
            for elem_id_str in cap.band_by_id:
                exp = cap.expected_uv_bbox(elem_id_str, fpp, pad_x, pad_y)
                got_vmin, got_vmax = _decoded_v_range(doc, elem_id_str)
                self.assertAlmostEqual(got_vmin, exp[1], places=9)
                self.assertAlmostEqual(got_vmax, exp[3], places=9)

    # ---------------------------------------------------------------- G6
    def test_g6_negative_pad_guard_fires_and_withholds_reliability(self):
        """G6: a pad below -1.0 px sets feet_per_pixel_unreliable_reason and
        does NOT report capture_reliable = True.

        A pad is >= 0 by construction whenever feet_per_pixel and the pads
        are measured against the same image, so the only way to drive one
        negative is a sidecar whose recorded export size is not this file's
        size -- a truncated/edited sidecar, or a TIFF matched to the wrong
        one. Here the sidecar claims 60x45 px for a file that is 40x30.
        """
        import tempfile
        crop = (0.0, 0.0, 20.0, 15.0)
        with tempfile.TemporaryDirectory() as tmp_dir:
            bands = [(5, 20, 5, 15)]
            cap = _Capture(tmp_dir, 40, 30, crop, bands,
                           name="bad_dims", sidecar_dims=(60, 45))
            _, pad_x, pad_y = dsc._clamp_pad_geometry(
                crop, 40, 30, measured_w=60, measured_h=45)
            self.assertLess(min(pad_x, pad_y), dsc.MIN_PAD_PX)
            print("\n[G6] recovered pads on a mismatched sidecar: "
                  "pad_x={0:.3f} px, pad_y={1:.3f} px (floor {2} px)".format(
                      pad_x, pad_y, dsc.MIN_PAD_PX))

            doc = cap.decode()
            self.assertIsNotNone(doc["feet_per_pixel_unreliable_reason"])
            # The DIMENSION-AGREEMENT guard now catches this first; the pad
            # sign is no longer the mechanism. Same defect, caught earlier
            # and for a reason that also covers the inverse direction.
            self.assertIn("not the same size", doc["feet_per_pixel_unreliable_reason"])
            self.assertIsNot(doc["capture_reliable"], True)
            self.assertFalse(doc["capture_reliable"])
            self.assertIsNotNone(doc["capture_unreliable_reason"])
            # ...and the elements it did decode must not carry AREAL+HIGH
            # occlusion authority on geometry the tool cannot place.
            for entry in doc["elements"].values():
                self.assertEqual(entry["confidence"], "MEDIUM")

    def test_g6_dimension_guard_fires_when_the_file_is_LARGER(self):
        """The inverse of the case above, and the one the pad-sign check
        could never see: when the file is BIGGER than the sidecar records,
        both pads come out POSITIVE and the mismatch reads as a legitimate
        centred clamp pad.

        Sidecar 40x30 against a 60x45 file on a 20x15 ft crop gives fpp 0.5
        and pads (+10.0, +7.5) -- nothing negative anywhere, so the old guard
        stayed silent and the capture could keep HIGH confidence.
        """
        import tempfile
        crop = (0.0, 0.0, 20.0, 15.0)
        with tempfile.TemporaryDirectory() as tmp_dir:
            bands = [(5, 30, 5, 20)]
            cap = _Capture(tmp_dir, 60, 45, crop, bands,
                           name="file_larger", sidecar_dims=(40, 30))
            fpp, pad_x, pad_y = dsc._clamp_pad_geometry(
                crop, 60, 45, measured_w=40, measured_h=30)
            self.assertAlmostEqual(fpp, 0.5, places=9)
            self.assertGreater(min(pad_x, pad_y), 0.0, "both pads positive: the blind spot")
            self.assertGreaterEqual(min(pad_x, pad_y), dsc.MIN_PAD_PX,
                                    "the pad-sign guard cannot see this case")
            print("\n[G6-inverse] file larger than sidecar: pad_x={0:.3f} px, "
                  "pad_y={1:.3f} px -- both positive".format(pad_x, pad_y))

            doc = cap.decode()
            self.assertIsNotNone(doc["feet_per_pixel_unreliable_reason"])
            self.assertIn("not the same size", doc["feet_per_pixel_unreliable_reason"])
            self.assertFalse(doc["capture_reliable"])
            for entry in doc["elements"].values():
                self.assertEqual(entry["confidence"], "MEDIUM")

    def test_g6_unexplained_pad_without_a_clamp_is_rejected(self):
        """A frame the clamp model cannot account for must not be silently
        reinterpreted as centred padding.

        100x80 ft into a 64x64 px image gives fpp 1.5625 and pad_y 6.4 px --
        but the crop's aspect is 1.25, nowhere near the limit, so ExportImage
        had nothing to pad. The producer's own dimension check validates only
        the fitted axis and the ceiling, so a derived-axis mismatch like this
        reaches the decoder unflagged and would otherwise earn HIGH
        confidence on shifted loops.
        """
        import tempfile
        crop = (0.0, 0.0, 100.0, 80.0)
        with tempfile.TemporaryDirectory() as tmp_dir:
            bands = [(10, 30, 10, 30)]
            cap = _Capture(tmp_dir, 64, 64, crop, bands, name="bad_frame")
            fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, 64, 64)
            self.assertAlmostEqual(fpp, 1.5625, places=9)
            self.assertAlmostEqual(pad_y, 6.4, places=6)
            self.assertGreater(pad_y, dsc.MAX_UNCLAMPED_PAD_PX)
            self.assertLess(100.0 / 80.0, dsc.MAX_STAGE_A_ASPECT,
                            "fixture must sit INSIDE the clamp limit")

            doc = cap.decode()
            self.assertIsNotNone(doc["feet_per_pixel_unreliable_reason"])
            self.assertIn("clamp never touched", doc["feet_per_pixel_unreliable_reason"])
            self.assertFalse(doc["capture_reliable"])

    def test_g6_rounding_pads_from_the_real_run_stay_clean(self):
        """The unclamped pads the byColor run actually exhibits -- 0.000,
        0.103, 0.340, 0.356 and 1.807 px, Revit's own rounding of the derived
        axis -- must NOT trip the guard above. A threshold that demoted four
        of six real views would be worse than no threshold.
        """
        import tempfile
        # Reproduces Elevation 5's shape: the worst real rounding pad, 1.807 px.
        image_w, image_h = 1000, 500
        crop = (0.0, 0.0, (image_w - 2 * 1.807) * 0.1, 50.0)
        fpp, pad_x, pad_y = dsc._clamp_pad_geometry(crop, image_w, image_h)
        self.assertAlmostEqual(pad_x, 1.807, places=6)
        self.assertLess(pad_x, dsc.MAX_UNCLAMPED_PAD_PX)
        with tempfile.TemporaryDirectory() as tmp_dir:
            cap = _Capture(tmp_dir, image_w, image_h, crop,
                           [(100, 300, 100, 300)], name="rounding_pad")
            doc = cap.decode()
            self.assertIsNone(doc["feet_per_pixel_unreliable_reason"])
            self.assertTrue(doc["capture_reliable"])

    # --------------------------------------------------- non-divergence
    def test_both_call_sites_share_one_derivation(self):
        """The reported feet_per_pixel and the per-point mapping must come
        from the same number. This is the whole point of the shared helper:
        if they drift, points land somewhere feet_per_pixel does not
        describe and nothing reports it."""
        import tempfile
        crop = (7.0, 3.0, 87.0, 7.0)  # 80 x 4 ft
        with tempfile.TemporaryDirectory() as tmp_dir:
            bands = [(40, 80, 12, 24)]
            cap = _Capture(tmp_dir, 400, 40, crop, bands, name="shared")
            doc = cap.decode()
            fpp = doc["feet_per_pixel"]
            pts = doc["elements"][sorted(cap.band_by_id)[0]]["loops"][0]["points"]
            us = sorted(set(round(p[0], 9) for p in pts))
            # The element is 40 px wide; its decoded u-extent must be exactly
            # 40 * the reported feet_per_pixel.
            self.assertAlmostEqual(max(us) - min(us), 40 * fpp, places=9)


if __name__ == "__main__":
    unittest.main()
