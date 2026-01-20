from vop_interwoven.pipeline import export_view_raster


class _StubId:
    def __init__(self, v: int):
        self.IntegerValue = v


class _StubView:
    def __init__(self, view_id=1, name="StubView"):
        self.Id = _StubId(view_id)
        self.Name = name


class _StubTile:
    def __init__(self, tile_size=8):
        self.tile_size = tile_size


class _StubCfg:
    def to_dict(self):
        return {}


class _StubRaster:
    def __init__(self):
        self.W = 10
        self.H = 10
        self.cell_size_ft = 1.0
        self.tile = _StubTile(tile_size=8)

        self.view_mode = "MODEL"
        self.view_mode_reason = {"why": "test"}

        self.model_mask = [False] * (self.W * self.H)
        self.model_edge_key = [-1] * (self.W * self.H)
        self.model_proxy_key = [-1] * (self.W * self.H)

        self.element_meta = []
        self.anno_meta = []
        self.bounds_meta = None

        # What this PR must surface explicitly
        self.skipped_outside_view_volume = 7

    def to_dict(self):
        return {"width": self.W, "height": self.H}


def test_export_includes_skipped_outside_view_volume_metric():
    view = _StubView(view_id=123, name="V")
    raster = _StubRaster()
    cfg = _StubCfg()

    out = export_view_raster(view, raster, cfg, diag=None, timings=None)

    assert "diagnostics" in out
    assert "skipped_outside_view_volume" in out["diagnostics"]
    assert out["diagnostics"]["skipped_outside_view_volume"] == 7


def test_export_defaults_skipped_outside_view_volume_to_zero_when_missing():
    view = _StubView(view_id=123, name="V")
    raster = _StubRaster()
    delattr(raster, "skipped_outside_view_volume")  # simulate older raster objects
    cfg = _StubCfg()

    out = export_view_raster(view, raster, cfg, diag=None, timings=None)

    assert out["diagnostics"]["skipped_outside_view_volume"] == 0

def test_should_skip_outside_view_volume_predicate_non_overlap_and_overlap_cases():
    from vop_interwoven.pipeline import _should_skip_outside_view_volume

    W0, Wmax = 0.0, 10.0

    # Entirely in front of volume
    assert _should_skip_outside_view_volume((-5.0, -1.0), W0, Wmax) is True

    # Entirely behind volume
    assert _should_skip_outside_view_volume((11.0, 20.0), W0, Wmax) is True

    # Overlaps at front boundary
    assert _should_skip_outside_view_volume((-1.0, 0.0), W0, Wmax) is False
    assert _should_skip_outside_view_volume((-1.0, 1.0), W0, Wmax) is False

    # Overlaps at far boundary
    assert _should_skip_outside_view_volume((10.0, 12.0), W0, Wmax) is False

    # Fully inside
    assert _should_skip_outside_view_volume((2.0, 3.0), W0, Wmax) is False

    # Reversed ranges should normalize
    assert _should_skip_outside_view_volume((3.0, 2.0), W0, Wmax) is False

    # Reversed volume should normalize
    assert _should_skip_outside_view_volume((11.0, 20.0), 10.0, 0.0) is True


def test_skip_counter_increments_when_predicate_true():
    from vop_interwoven.pipeline import _should_skip_outside_view_volume

    W0, Wmax = 0.0, 10.0
    wrappers = [
        {"depth_range": (-5.0, -1.0)},  # skip
        {"depth_range": (2.0, 3.0)},    # keep
        {"depth_range": (11.0, 20.0)},  # skip
        {"depth_range": (-1.0, 1.0)},   # keep
    ]

    skipped = 0
    for w in wrappers:
        if _should_skip_outside_view_volume(w.get("depth_range"), W0, Wmax):
            skipped += 1

    assert skipped == 2
