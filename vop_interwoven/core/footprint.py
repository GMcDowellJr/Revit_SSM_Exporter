# vop_interwoven/core/footprint.py

class CellRectFootprint:
    """Footprint wrapper for a CellRect, future-proofing for hull footprints."""
    def __init__(self, rect):
        self.rect = rect

    def tiles(self, tile_map):
        r = self.rect
        return tile_map.get_tiles_for_rect(r.i_min, r.j_min, r.i_max, r.j_max)

    def cells(self):
        return self.rect.cells()