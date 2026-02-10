"""Lightweight per-view occlusion diagnostics tracking."""


class OcclusionTracker:
    """Aggregates low-cost occlusion diagnostics for a single view render."""

    def __init__(self, extraction_est_ms=5.0, raster_tile_est_ms=0.1):
        self.elements_processed = 0
        self.elements_fully_occluded = 0
        self.elements_partially_occluded = 0
        self.tiles_rejected_total = 0

        self.total_tiles_written = 0
        self.total_tiles_available = 0
        self.coverage_pct = 0.0
        self.saturated_tiles = 0
        self.saturation_pct = 0.0

        self.first_saturated_at_idx = None
        self.first_saturated_at_pct = 0.0

        self.time_sorting_ms = 0.0
        self.time_occlusion_tests_ms = 0.0

        self.time_saved_extraction_est_ms = 0.0
        self.time_saved_raster_est_ms = 0.0
        self.net_occlusion_benefit_ms = 0.0
        self.occlusion_roi = 0.0

        self._extraction_est_ms = float(extraction_est_ms)
        self._raster_tile_est_ms = float(raster_tile_est_ms)

    def record_element(self):
        self.elements_processed += 1

    def record_bbox_rejection(self):
        self.elements_fully_occluded += 1

    def record_partial_occlusion(self, tile_count):
        try:
            tc = int(tile_count)
        except Exception:
            tc = 0

        if tc <= 0:
            return

        self.tiles_rejected_total += tc
        self.elements_partially_occluded += 1

    def record_occlusion_test_ms(self, elapsed_ms):
        try:
            self.time_occlusion_tests_ms += max(0.0, float(elapsed_ms))
        except Exception:
            pass

    def check_saturation(self, tile_map, element_idx):
        if self.first_saturated_at_idx is not None:
            return
        try:
            num_tiles = len(getattr(tile_map, "filled_count", []) or [])
        except Exception:
            num_tiles = 0

        for tile_idx in range(num_tiles):
            try:
                if tile_map.is_tile_full(tile_idx):
                    self.first_saturated_at_idx = int(element_idx)
                    break
            except Exception:
                continue

    def finalize(self, tile_map):
        filled = getattr(tile_map, "filled_count", []) or []
        total_tiles = len(filled)
        self.total_tiles_available = int(total_tiles)

        written = 0
        saturated = 0
        for tile_idx, cell_count in enumerate(filled):
            if cell_count > 0:
                written += 1
            try:
                if tile_map.is_tile_full(tile_idx):
                    saturated += 1
            except Exception:
                continue

        self.total_tiles_written = int(written)
        self.saturated_tiles = int(saturated)

        if self.total_tiles_available > 0:
            self.coverage_pct = (float(self.total_tiles_written) / float(self.total_tiles_available)) * 100.0
            self.saturation_pct = (float(self.saturated_tiles) / float(self.total_tiles_available)) * 100.0
        else:
            self.coverage_pct = 0.0
            self.saturation_pct = 0.0

        if self.first_saturated_at_idx is not None and self.elements_processed > 0:
            self.first_saturated_at_pct = min(
                1.0,
                max(0.0, float(self.first_saturated_at_idx) / float(self.elements_processed))
            )
        else:
            self.first_saturated_at_pct = 0.0

        self.time_saved_extraction_est_ms = float(self.elements_fully_occluded) * self._extraction_est_ms
        self.time_saved_raster_est_ms = float(self.tiles_rejected_total) * self._raster_tile_est_ms
        total_saved = self.time_saved_extraction_est_ms + self.time_saved_raster_est_ms

        overhead = float(self.time_sorting_ms) + float(self.time_occlusion_tests_ms)
        self.net_occlusion_benefit_ms = total_saved - overhead
        self.occlusion_roi = (total_saved / overhead) if overhead > 0.0 else 0.0

    def as_dict(self):
        rejection_rate = 0.0
        if self.elements_processed > 0:
            rejection_rate = (
                float(self.elements_fully_occluded + self.elements_partially_occluded)
                / float(self.elements_processed)
            ) * 100.0

        return {
            "total_tiles_written": int(self.total_tiles_written),
            "total_tiles_available": int(self.total_tiles_available),
            "coverage_pct": float(self.coverage_pct),
            "saturated_tiles": int(self.saturated_tiles),
            "saturation_pct": float(self.saturation_pct),
            "elements_processed": int(self.elements_processed),
            "elements_fully_occluded": int(self.elements_fully_occluded),
            "elements_partially_occluded": int(self.elements_partially_occluded),
            "tiles_rejected_total": int(self.tiles_rejected_total),
            "occlusion_rejection_rate": float(rejection_rate),
            "time_sorting_ms": float(self.time_sorting_ms),
            "time_occlusion_tests_ms": float(self.time_occlusion_tests_ms),
            "time_saved_extraction_est_ms": float(self.time_saved_extraction_est_ms),
            "time_saved_raster_est_ms": float(self.time_saved_raster_est_ms),
            "time_saved_est_ms": float(self.time_saved_extraction_est_ms + self.time_saved_raster_est_ms),
            "net_occlusion_benefit_ms": float(self.net_occlusion_benefit_ms),
            "occlusion_roi": float(self.occlusion_roi),
            "first_saturated_tile_at_element_idx": self.first_saturated_at_idx,
            "first_saturated_tile_at_pct": float(self.first_saturated_at_pct),
        }
