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
        
class HullFootprint:
    """
    Footprint defined by a convex hull in UV space.
    Rasterized conservatively via scanline fill.
    """
    def __init__(self, hull_uv, raster):
        self.hull = hull_uv
        self.raster = raster

        us = [p[0] for p in hull_uv]
        vs = [p[1] for p in hull_uv]
        self.u_min = int(min(us))
        self.u_max = int(max(us))
        self.v_min = int(min(vs))
        self.v_max = int(max(vs))

    def tiles(self, tile_map):
        return tile_map.get_tiles_for_rect(
            self.u_min, self.v_min,
            self.u_max, self.v_max
        )

    def cells(self):
        # simple even-odd scanline fill
        hull = self.hull
        n = len(hull)
        if n < 3:
            return []

        for j in range(self.v_min, self.v_max + 1):
            xs = []
            y = j + 0.5
            for i in range(n):
                (x1, y1) = hull[i]
                (x2, y2) = hull[(i + 1) % n]
                if (y1 <= y < y2) or (y2 <= y < y1):
                    t = (y - y1) / (y2 - y1)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs), 2):
                x_start = int(xs[k])
                x_end = int(xs[k + 1])
                for i in range(x_start, x_end + 1):
                    yield (i, j)