"""
Deterministic surface-damage detection.

Purpose: decide *where on the page no legible evidence exists*. This matters because
"the OCR read something" and "the OCR read everything that was printed there" are
different claims. If a value's text ends and a damaged, structureless zone begins
immediately after it, the value may be truncated - so it cannot be reported as
RECOVERED.

This is a transparent pixel heuristic (luminance + local contrast), not a trained
model:

    obscured  =  (luminance well below paper level  AND  no local structure)
                 OR  (luminance far below paper level - charred / ink-flooded)

No OCR text is created, modified or interpreted here.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import config


@dataclass
class DamageMap:
    """Per-pixel obscuration mask plus the reference paper luminance."""

    obscured: np.ndarray  # uint8 0/255, original image size
    paper_luma: float

    def ratio(self, bbox: list[int] | tuple[int, int, int, int]) -> float:
        """Fraction of the given box that is obscured (0.0 - 1.0)."""
        x0, y0, x1, y1 = [int(v) for v in bbox]
        h, w = self.obscured.shape
        x0, x1 = max(0, min(w - 1, x0)), max(0, min(w, x1))
        y0, y1 = max(0, min(h - 1, y0)), max(0, min(h, y1))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        patch = self.obscured[y0:y1, x0:x1]
        return float((patch > 0).mean())

    def longest_obscured_run(self, x0: int, x1: int, y0: int, y1: int) -> int:
        """Longest unbroken obscured run (px) inside a horizontal window of the row band.

        Used to distinguish "damage happens to sit near the value" from "the printed line
        ran into an unreadable area and was cut off".
        """
        h, w = self.obscured.shape
        x0, x1 = max(0, min(w, x0)), max(0, min(w, x1))
        y0, y1 = max(0, min(h, y0)), max(0, min(h, y1))
        if x1 <= x0 or y1 <= y0:
            return 0
        column_obscured = self.obscured[y0:y1, x0:x1].mean(axis=0) > 0.5
        best = run = 0
        for flag in column_obscured:
            run = run + 1 if flag else 0
            best = max(best, run)
        return int(best)


def build_damage_map(bgr: np.ndarray) -> DamageMap:
    """Build the obscuration mask for a BGR image."""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    paper = float(np.percentile(gray, 90))

    k = max(9, (min(h, w) // 60) | 1)  # local statistics window
    mean = cv2.boxFilter(gray, -1, (k, k))
    sq_mean = cv2.boxFilter(gray * gray, -1, (k, k))
    local_std = np.sqrt(np.maximum(sq_mean - mean * mean, 0))

    flat_damage = (gray < config.OBSCURATION_PAPER_FRAC * paper) & (local_std < config.OBSCURATION_LOCAL_STD)
    heavy_damage = gray < 0.45 * paper
    obscured = (flat_damage | heavy_damage).astype(np.uint8) * 255

    obscured = cv2.medianBlur(obscured, 5)
    kernel = np.ones((3, 3), np.uint8)
    obscured = cv2.morphologyEx(obscured, cv2.MORPH_OPEN, kernel)
    obscured = cv2.morphologyEx(obscured, cv2.MORPH_CLOSE, kernel)
    return DamageMap(obscured=obscured, paper_luma=paper)


def adjacent_obscuration(
    damage: DamageMap,
    value_bbox: list[int],
    image_shape: tuple[int, int],
    value_x_range: tuple[float, float],
) -> tuple[float, list[int], int]:
    """Measure damage in the gap immediately to the right of a value, inside its row.

    Returns (ratio, probe_bbox, continuous_run_px). A high ratio means the printed line
    probably continued into a damaged zone and was cut off.
    """
    h, w = image_shape
    x0, y0, x1, y1 = [int(v) for v in value_bbox]
    band_h = max(4, y1 - y0)
    band_y0 = max(0, y0 - int(band_h * 0.15))
    band_y1 = min(h, y1 + int(band_h * 0.15))

    gap = max(12, int(config.ADJACENT_GAP_FRAC * w))
    px1 = min(w, int(value_x_range[1] * w))
    probe_x0, probe_x1 = min(x1, px1), min(px1, x1 + gap)
    if probe_x1 <= probe_x0:
        return 0.0, [x1, band_y0, x1, band_y1], 0

    ratio = damage.ratio([probe_x0, band_y0, probe_x1, band_y1])
    run = damage.longest_obscured_run(probe_x0, probe_x1, band_y0, band_y1)
    return ratio, [probe_x0, band_y0, probe_x1, band_y1], run
