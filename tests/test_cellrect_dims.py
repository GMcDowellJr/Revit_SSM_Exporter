# tests/test_cellrect_dims.py

import pytest

from vop_interwoven.core.math_utils import CellRect, cellrect_dims


def test_cellrect_dims_core_cellrect_inclusive_indices():
    r = CellRect(2, 3, 6, 8)  # inclusive => (5, 6)
    assert cellrect_dims(r) == (5, 6)


def test_cellrect_dims_supports_x0_x1_half_open_form():
    class R(object):
        def __init__(self):
            self.x0 = 10
            self.x1 = 15
            self.y0 = 7
            self.y1 = 9

    r = R()
    assert cellrect_dims(r) == (5, 2)


def test_cellrect_dims_rejects_unknown_shape():
    class R(object):
        pass

    with pytest.raises(ValueError):
        cellrect_dims(R())
