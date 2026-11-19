"""tools/probe_colour_mode_attribution.py on synthetic masks and boxes.

Gate 3 of the colour-mode probe: a unique case, an overlapping same-colour
case, and a no-box case, each in the labelled and the unlabelled form, plus
the parts the report is built from.
"""
import numpy as np
import pytest

from tools import probe_colour_mode_attribution as pca

RED, BLUE = (200, 0, 0), (0, 0, 200)


def _ink(shape, *rects):
    ink = np.zeros(shape, dtype=bool)
    for x0, y0, x1, y1 in rects:
        ink[y0:y1, x0:x1] = True
    return ink


def test_one_box_owns_its_ink_uniquely():
    ink = _ink((10, 10), (2, 2, 5, 5))
    owner, count = pca.attribute(ink, [{"id": 7, "rect": (1, 1, 6, 6)}])
    assert (owner[ink] == 7).all() and (owner[~ink] == 0).all()
    assert count[ink].max() == 1


def test_overlapping_boxes_without_labels_are_ambiguous():
    ink = _ink((10, 10), (3, 3, 4, 4))
    owner, _ = pca.attribute(ink, [{"id": 1, "rect": (0, 0, 5, 5)},
                                   {"id": 2, "rect": (2, 2, 8, 8)}])
    assert owner[3, 3] == pca.AMBIGUOUS


def test_overlapping_same_colour_boxes_stay_ambiguous_and_colour_splits_different():
    ink = _ink((10, 10), (3, 3, 4, 4), (4, 4, 5, 5))
    labels = np.full((10, 10), pca.OFF_PALETTE, dtype=np.int64)
    labels[3, 3] = 100   # both boxes are label 100 -> still ambiguous
    labels[4, 4] = 200   # only box 3 is label 200 -> unique
    boxes = [{"id": 1, "rect": (0, 0, 6, 6), "label": 100},
             {"id": 2, "rect": (2, 2, 8, 8), "label": 100},
             {"id": 3, "rect": (2, 2, 8, 8), "label": 200}]
    owner, _ = pca.attribute(ink, boxes, labels)
    assert owner[3, 3] == pca.AMBIGUOUS
    assert owner[4, 4] == 3


def test_ink_with_no_box_or_no_label_is_unassigned():
    ink = _ink((10, 10), (8, 8, 10, 10), (1, 1, 2, 2))
    labels = np.full((10, 10), pca.OFF_PALETTE, dtype=np.int64)
    owner, _ = pca.attribute(ink, [{"id": 1, "rect": (0, 0, 4, 4), "label": 5}], labels)
    assert (owner[ink] == 0).all()   # (8,8): no box; (1,1): off-palette
    # A box whose element the probe could not label matches nothing by colour.
    labels[1, 1] = 5
    owner, _ = pca.attribute(ink, [{"id": 1, "rect": (0, 0, 4, 4), "label": None}], labels)
    assert owner[1, 1] == 0


def test_label_image_and_its_refusal():
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    rgb[0, 0] = RED
    rgb[1, 1] = BLUE
    labels = pca.label_image(pca.pack(rgb), {10: RED, 20: BLUE})
    assert labels.tolist() == [[10, pca.OFF_PALETTE], [pca.OFF_PALETTE, 20]]
    with pytest.raises(ValueError):
        pca.label_image(pca.pack(rgb), {10: RED, 11: RED})


def test_uv_rect_to_px_flips_v_and_clips():
    # 10 ft wide crop at 1 px/ft: u 0..10 -> x 0..10; v 0..10 -> y 10..0.
    mapping = {"a_u": 1.0, "b_u": 0.0, "a_v": -1.0, "b_v": 10.0}
    assert pca.uv_rect_to_px((2, 6, 4, 8), mapping, (10, 10)) == (2, 2, 4, 4)
    assert pca.uv_rect_to_px((-5, -5, 3, 3), mapping, (10, 10)) == (0, 7, 3, 10)
    assert pca.uv_rect_to_px((20, 20, 30, 30), mapping, (10, 10)) is None


def test_element_cells_counts_blocks_per_element():
    ids = np.zeros((8, 8), dtype=np.int64)
    ids[0, 0] = ids[0, 5] = 1     # two 4x4 cells
    ids[7, 7] = 2
    assert pca.element_cells(ids, 4) == {1: 2, 2: 1}
    assert pca.ink_cells(ids > 0, 4) == 3


def test_the_report_against_the_baseline():
    shape = (8, 8)
    truth = np.zeros(shape, dtype=np.int64)
    truth[1, 1:3] = 1      # a wall
    truth[5, 5:7] = 2      # a casework item, its box overlapping a door's
    truth[6, 6] = 3        # the door
    ink = truth > 0
    boxes = [{"id": 1, "rect": (0, 0, 4, 3)}, {"id": 2, "rect": (4, 4, 8, 8)},
             {"id": 3, "rect": (5, 5, 8, 8)}]
    owner, _ = pca.attribute(ink, boxes)
    rep = pca.view_arm_report(ink, owner, truth,
                              {1: "Walls", 2: "Casework", 3: "Doors"}, cell_px=4)
    assert rep["ink_px"] == 5
    assert rep["unique"] == 0.4 and rep["ambiguous"] == 0.6 and rep["unassigned"] == 0.0
    assert rep["unique_correct"] == 1.0
    assert rep["by_category_group"]["walls"]["unique"] == 1.0
    assert rep["by_category_group"]["casework"]["ambiguous"] == 1.0
    assert rep["by_category_group"]["doors"]["ambiguous"] == 1.0
    # Elements 2 and 3 lost their cells to ambiguity: |err| 1 each, 0 for 1.
    assert rep["element_cell_abs_error"] == {"median": 1.0, "p90": 1.0, "elements": 3}


def test_label_mismatch_counts_the_colour_another_setting_won():
    truth = np.array([[1, 1, 2, 0]], dtype=np.int64)
    ink = np.array([[True, True, True, False]])
    labels = np.array([[10, 99, pca.OFF_PALETTE, pca.OFF_PALETTE]], dtype=np.int64)
    owner, _ = pca.attribute(ink, [{"id": 1, "rect": (0, 0, 2, 1), "label": 10},
                                   {"id": 2, "rect": (2, 0, 3, 1), "label": 20}], labels)
    rep = pca.view_arm_report(ink, owner, truth, {}, cell_px=1, labels=labels,
                              truth_label={1: 10, 2: 20})
    assert rep["label_mismatch"] == round(2 / 3.0, 6)   # 99, and the off-palette one
    assert rep["off_palette"] == round(1 / 3.0, 6)
    assert rep["unique"] == round(1 / 3.0, 6)


def test_category_groups():
    assert pca.category_group("Walls") == "walls"
    assert pca.category_group("Furniture Systems") == "furniture"
    assert pca.category_group("Generic Models") == "other"


def test_a_missing_probe_record_is_refused(tmp_path):
    assert pca.main([str(tmp_path), str(tmp_path)]) == 2


# --- end to end over files: the probe record, the baseline run -----------

def _baseline_run(tmp_path):
    import json
    from PIL import Image
    run = tmp_path / "run"
    (run / "color_id").mkdir(parents=True)
    img = np.full((20, 40, 3), 255, np.uint8)       # crop 40 x 20 ft at 1 px/ft
    img[2:4, 2:10] = (10, 20, 30)                   # wall 101
    img[12:14, 20:30] = (40, 50, 60)                # casework 102
    img[12:14, 30:32] = (70, 80, 90)                # door 103, its box inside 102's
    Image.fromarray(img).save(str(run / "color_id" / "V_5.tiff"))
    side = {"view_id": 5, "frame": {"crop_uv": [0, 0, 40, 20], "pixel_size": 40},
            "color_assignment_map": {"101": [10, 20, 30], "102": [40, 50, 60],
                                     "103": [70, 80, 90]},
            "near_face_w_map": {"host": {
                "101": {"uv_rect": [1, 15, 11, 19], "category": "Walls", "source": "HOST"},
                "102": {"uv_rect": [19, 5, 33, 9], "category": "Casework", "source": "HOST"},
                "103": {"uv_rect": [29, 5, 33, 9], "category": "Doors", "source": "HOST"}}}}
    (run / "color_id" / "V_5.json").write_text(json.dumps(side))
    return run


def _probe(tmp_path, arms, shape=(20, 40)):
    import json
    from PIL import Image
    probe = tmp_path / "probe"
    probe.mkdir()
    recs = {}
    for arm, paint in arms.items():
        img = np.full(shape + (3,), 255, np.uint8)
        for (y0, y1, x0, x1), rgb in paint:
            img[y0:y1, x0:x1] = rgb
        path = probe / "5_{0}.tiff".format(arm)
        Image.fromarray(img).save(str(path))
        recs[arm] = {"tiff_path": str(path), "timings_ms": {"export": 1.0},
                     "label_colour": {"-2000011": [200, 0, 0], "-2001000": [0, 200, 0],
                                      "-2000023": [0, 0, 200]}}
    (probe / "probe_colour_mode_x.json").write_text(json.dumps({"views": [{
        "view_id": 5, "arms": recs, "element_labels": {
            "101": {"category": -2000011}, "102": {"category": -2001000},
            "103": {"category": -2000023}}}]}))
    return probe


def test_end_to_end_unlabelled_and_category_arms(tmp_path):
    run = _baseline_run(tmp_path)
    ink = [((2, 4, 2, 10), (0, 0, 0)), ((12, 14, 20, 32), (0, 0, 0))]
    by_cat = [((2, 4, 2, 10), (200, 0, 0)), ((12, 14, 20, 30), (0, 200, 0)),
              ((12, 14, 30, 32), (0, 0, 200))]
    probe = _probe(tmp_path, {"as_is": ink, "category": by_cat})
    result = pca.run(str(probe), str(run), cell_px=4)
    view = result["views"][0]
    assert view["census_boxes"] == 3
    as_is, cat = view["arms"]["as_is"], view["arms"]["category"]
    # Unlabelled: the boxes overlap on columns 29-31, 3 x 2 of the 40 ink px:
    # the door's own 4 px and the casework's last column.
    assert as_is["ambiguous"] == round(6 / 40.0, 6)
    assert as_is["by_category_group"]["doors"]["ambiguous"] == 1.0
    # By category colour the overlap resolves: different categories.
    assert cat["unique"] == 1.0 and cat["unique_correct"] == 1.0
    assert cat["label_mismatch"] == 0.0
    assert view["arms"]["type"] == {"refused": "no exported image for this arm"}
    assert "analysis layer" in result["metrics_not_computed"]


def test_end_to_end_refuses_an_image_off_the_baseline_lattice(tmp_path):
    run = _baseline_run(tmp_path)
    probe = _probe(tmp_path, {"as_is": []}, shape=(21, 40))
    rep = pca.run(str(probe), str(run))["views"][0]["arms"]["as_is"]
    assert "not on one lattice" in rep["refused"]
