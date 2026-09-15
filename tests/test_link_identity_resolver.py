"""Unit tests for tools/link_identity_resolver.py (Phase 1b: LINK
element-identity resolution from a Stage A sidecar + TIFF).

Pure-Python / NumPy: builds synthetic RGB arrays and near_face_w_map
candidate dicts directly rather than round-tripping through real TIFF
files, except for the two file-driven integration tests which write a
minimal real sidecar + TIFF pair to a tmp_path.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import link_identity_resolver as lir  # noqa: E402


# --- connected_components_with_pixels ---------------------------------------

def test_connected_components_with_pixels_splits_disjoint_blobs():
    mask = np.zeros((10, 10), dtype=bool)
    mask[1:3, 1:3] = True    # blob A: 4 px
    mask[6:9, 6:9] = True    # blob B: 9 px
    blobs = lir.connected_components_with_pixels(mask)
    assert blobs is not None
    assert len(blobs) == 2
    # Largest first.
    assert blobs[0]["pixel_count"] == 9
    assert blobs[1]["pixel_count"] == 4
    assert blobs[0]["bbox_px"] == [6, 6, 8, 8]
    assert blobs[1]["bbox_px"] == [1, 1, 2, 2]


def test_connected_components_with_pixels_skips_oversized_mask(monkeypatch):
    monkeypatch.setattr(lir, "MAX_MASK_PIXELS_FOR_COMPONENTS", 4)
    mask = np.ones((10, 10), dtype=bool)
    assert lir.connected_components_with_pixels(mask) is None


def test_connected_components_with_pixels_crops_to_foreground_before_size_check():
    """A large image (raw h*w far exceeds MAX_MASK_PIXELS_FOR_COMPONENTS,
    matching an ordinary sheet-sized Stage A capture) with a small, sparse
    foreground region must still be processed -- the size cap applies to
    the foreground's own extent, not the full image/mask dimensions. Gating
    on raw image size would mark nearly every real capture "skipped"
    regardless of how sparse the actual colored region is."""
    large_h, large_w = 3000, 3000  # 9,000,000 px total, well over the default 4,000,000 cap
    mask = np.zeros((large_h, large_w), dtype=bool)
    mask[100:103, 100:103] = True  # tiny 3x3 foreground far from the image's own size
    blobs = lir.connected_components_with_pixels(mask)
    assert blobs is not None
    assert len(blobs) == 1
    assert blobs[0]["pixel_count"] == 9
    assert blobs[0]["bbox_px"] == [100, 100, 102, 102]


def test_connected_components_with_pixels_returns_empty_list_for_blank_mask():
    mask = np.zeros((10, 10), dtype=bool)
    assert lir.connected_components_with_pixels(mask) == []


# --- _uv_rect_to_pixel_bbox ---------------------------------------------------

def test_uv_rect_to_pixel_bbox_maps_into_pixel_space():
    # bounds_uv spans [0, 100] x [0, 100] over a 100x100 image -- 1:1 mapping.
    bounds_uv = (0.0, 0.0, 100.0, 100.0)
    rect = [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0]]
    px = lir._uv_rect_to_pixel_bbox(rect, bounds_uv, image_w=100, image_h=100)
    # v increases upward in UV but y increases downward in pixel space, so
    # v=[10,20] maps to y=[80,90].
    assert px == (10, 80, 19, 89)


def test_uv_rect_to_pixel_bbox_returns_none_without_bounds_or_corners():
    assert lir._uv_rect_to_pixel_bbox(None, (0, 0, 10, 10), 10, 10) is None
    assert lir._uv_rect_to_pixel_bbox([[0, 0]], None, 10, 10) is None


# --- resolve_category: scoring, distinct confidence, nearest-W tie-break ----

def _rgb_array_with_two_squares(size=40):
    """Two disjoint 5x5 foreground blocks of the same color on a background."""
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[:, :] = (255, 255, 255)
    arr[5:10, 5:10] = (10, 20, 30)   # blob A
    arr[25:30, 25:30] = (10, 20, 30)  # blob B
    return arr


def _candidate(key, link_inst_id, link_elem_id, near_face_w, bbox_corners_uv):
    return {
        "key": key,
        "link_inst_id": link_inst_id,
        "link_elem_id": link_elem_id,
        "near_face_w": near_face_w,
        "bbox_corners_uv": bbox_corners_uv,
    }


def test_resolve_category_single_candidate_per_blob_is_high_confidence():
    arr = _rgb_array_with_two_squares()
    bounds_uv = (0.0, 0.0, float(arr.shape[1]), float(arr.shape[0]))
    # Candidate A's bbox exactly covers blob A's pixel footprint; candidate
    # B's exactly covers blob B's.
    cand_a = _candidate("1:101", 1, 101, near_face_w=5.0,
                         bbox_corners_uv=[[5, 30], [10, 30], [10, 35], [5, 35]])
    cand_b = _candidate("1:102", 1, 102, near_face_w=5.0,
                         bbox_corners_uv=[[25, 10], [30, 10], [30, 15], [25, 15]])

    result = lir.resolve_category(
        [10, 20, 30], [cand_a, cand_b], arr, bounds_uv, image_w=arr.shape[1], image_h=arr.shape[0],
    )

    assert result["skipped"] is False
    assert result["pixel_count"] == 50  # two 5x5 blobs
    blobs = result["blobs"]
    assert len(blobs) == 2
    for blob in blobs.values():
        assignment = blob["assignment"]
        assert assignment is not None
        assert assignment["confidence_label"] == "HIGH"
        assert assignment["confidence_score"] >= lir.CONFIDENCE_HIGH_THRESHOLD
        assert blob["candidates_considered"] == 1


def test_resolve_category_overlapping_candidates_get_distinct_scores_and_nearest_w_wins():
    """Two same-category candidates whose bbox footprints are IDENTICAL
    (so raw bbox-coverage is tied) must still resolve to distinct
    confidence_score values, with the nearer (smaller near_face_w)
    candidate selected -- the validation gate's explicit requirement."""
    size = 20
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[:, :] = (255, 255, 255)
    arr[5:10, 5:10] = (7, 8, 9)
    bounds_uv = (0.0, 0.0, float(size), float(size))

    same_rect = [[5, 10], [10, 10], [10, 15], [5, 15]]
    cand_near = _candidate("1:201", 1, 201, near_face_w=2.0, bbox_corners_uv=same_rect)
    cand_far = _candidate("1:202", 1, 202, near_face_w=9.0, bbox_corners_uv=same_rect)

    result = lir.resolve_category(
        [7, 8, 9], [cand_near, cand_far], arr, bounds_uv, image_w=size, image_h=size,
    )

    assert result["skipped"] is False
    blob = next(iter(result["blobs"].values()))
    scores = blob["candidate_scores"]
    assert len(scores) == 2
    assert scores[0]["coverage_fraction"] == pytest.approx(scores[1]["coverage_fraction"])
    assert scores[0]["confidence_score"] != scores[1]["confidence_score"], (
        "identical raw coverage must still be broken into distinct confidence_score values"
    )
    assert scores[0]["confidence_score"] > scores[1]["confidence_score"]
    # The nearer candidate (smaller near_face_w) wins the tie and is selected.
    assert blob["assignment"]["elem_id"] == "1:201"
    assert blob["assignment"]["near_face_w"] == 2.0
    # Both candidates fully explain the blob (identical footprints) -- this
    # is itself a genuine multi-candidate-plausible region, so it must never
    # be reported HIGH confidence despite the winning candidate's raw score.
    assert blob["assignment"]["multi_candidate_region"] is True
    assert blob["assignment"]["confidence_label"] == "LOW"
    assert blob["assignment"]["other_plausible_elem_ids"] == ["1:202"]


def test_resolve_category_adjoining_elements_merged_into_one_blob_are_flagged_ambiguous():
    """Two visible same-category LINK elements that touch/overlap in the
    raster (e.g. adjoining walls) form ONE connected-component blob. Picking
    only the top-ranked candidate must not silently claim sole, high-
    confidence ownership of the whole region -- the blob must be flagged as
    a plausible multi-element merger and list the other candidate too."""
    size = 30
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[:, :] = (255, 255, 255)
    # One connected blob spanning columns 5-14, rows 5-9 -- two elements'
    # footprints each cover roughly half of it (columns 5-9 and 10-14).
    arr[5:10, 5:15] = (1, 2, 3)
    bounds_uv = (0.0, 0.0, float(size), float(size))

    cand_left = _candidate("1:401", 1, 401, near_face_w=4.0,
                            bbox_corners_uv=[[5, 20], [10, 20], [10, 25], [5, 25]])
    cand_right = _candidate("1:402", 1, 402, near_face_w=4.0,
                             bbox_corners_uv=[[10, 20], [15, 20], [15, 25], [10, 25]])

    result = lir.resolve_category(
        [1, 2, 3], [cand_left, cand_right], arr, bounds_uv, image_w=size, image_h=size,
    )

    assert result["skipped"] is False
    blob = next(iter(result["blobs"].values()))
    assignment = blob["assignment"]
    assert assignment["multi_candidate_region"] is True
    assert assignment["confidence_label"] == "LOW"
    winner = assignment["elem_id"]
    other = assignment["other_plausible_elem_ids"]
    assert set([winner] + other) == {"1:401", "1:402"}
    assert len(other) == 1


def test_resolve_category_no_candidates_intersecting_blob_reports_null_assignment():
    arr = _rgb_array_with_two_squares()
    bounds_uv = (0.0, 0.0, float(arr.shape[1]), float(arr.shape[0]))
    # Candidate's footprint is far from both blobs.
    cand = _candidate("1:301", 1, 301, near_face_w=1.0,
                       bbox_corners_uv=[[35, 35], [38, 35], [38, 38], [35, 38]])

    result = lir.resolve_category(
        [10, 20, 30], [cand], arr, bounds_uv, image_w=arr.shape[1], image_h=arr.shape[0],
    )

    for blob in result["blobs"].values():
        assert blob["assignment"] is None
        assert blob["reason"] == "no_candidate_footprint_intersects_blob_bbox"


def test_resolve_category_zero_pixel_count_is_not_an_error():
    arr = np.full((10, 10, 3), 255, dtype=np.uint8)  # category never rendered
    bounds_uv = (0.0, 0.0, 10.0, 10.0)
    result = lir.resolve_category([1, 2, 3], [], arr, bounds_uv, image_w=10, image_h=10)
    assert result == {
        "rgb": [1, 2, 3], "pixel_count": 0, "blobs": {},
        "candidate_count": 0, "skipped": False, "skip_reason": None,
    }


# --- resolve_sidecar: end-to-end file-driven contract checks ----------------

def _write_sidecar_and_tiff(tmp_path, sidecar_extra):
    arr = np.full((20, 20, 3), 255, dtype=np.uint8)
    arr[2:6, 2:6] = (10, 20, 30)
    tiff_path = tmp_path / "view.tiff"
    Image.fromarray(arr, mode="RGB").save(tiff_path)

    sidecar = {
        "view_id": 999,
        "tiff_path": str(tiff_path),
        "bounds_xy": [0.0, 0.0, 20.0, 20.0],
        "link_category_color_map": {"Walls": [10, 20, 30]},
        "near_face_w_map": {"host": {}, "link": {}},
    }
    sidecar.update(sidecar_extra)
    sidecar_path = tmp_path / "view.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    return sidecar_path


def test_resolve_sidecar_raises_clear_error_when_near_face_w_map_missing(tmp_path):
    sidecar_path = tmp_path / "old.json"
    sidecar_path.write_text(json.dumps({"view_id": 1, "tiff_path": "x.tiff"}), encoding="utf-8")
    with pytest.raises(ValueError, match="MISSING_NEAR_FACE_W_MAP"):
        lir.resolve_sidecar(sidecar_path)


def test_resolve_sidecar_end_to_end_writes_advisory_output(tmp_path):
    cand = {
        "9:501": {
            "link_inst_id": 9, "link_elem_id": 501, "category": "Walls", "near_face_w": 3.0,
            "bbox_corners_uv": [[2, 14], [6, 14], [6, 18], [2, 18]],
        },
    }
    sidecar_path = _write_sidecar_and_tiff(tmp_path, {"near_face_w_map": {"host": {}, "link": cand}})

    doc = lir.resolve_sidecar(sidecar_path)

    assert doc["advisory_only"] is True
    assert doc["view_id"] == 999
    assert doc["confidence_high_threshold"] == lir.CONFIDENCE_HIGH_THRESHOLD
    walls = doc["categories"]["Walls"]
    assert walls["skipped"] is False
    assert len(walls["blobs"]) == 1
    blob = next(iter(walls["blobs"].values()))
    assert blob["assignment"]["elem_id"] == "9:501"

    out_path = lir.resolve_one(sidecar_path)
    assert out_path.name == "view.link_identity.json"
    assert out_path.exists()
    reloaded = json.loads(out_path.read_text())
    assert reloaded["categories"]["Walls"]["blobs"]


def test_resolve_sidecar_skips_category_when_bounds_xy_missing(tmp_path):
    cand = {
        "9:501": {
            "link_inst_id": 9, "link_elem_id": 501, "category": "Walls", "near_face_w": 3.0,
            "bbox_corners_uv": [[2, 14], [6, 14], [6, 18], [2, 18]],
        },
    }
    sidecar_path = _write_sidecar_and_tiff(
        tmp_path, {"bounds_xy": None, "near_face_w_map": {"host": {}, "link": cand}}
    )

    doc = lir.resolve_sidecar(sidecar_path)
    walls = doc["categories"]["Walls"]
    assert walls["skipped"] is True
    assert "bounds_xy" in walls["skip_reason"]
