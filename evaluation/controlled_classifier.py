"""Deterministic classifier harness for controlled, synthetic observations.

This module is evaluation/test-layer only. It does not run OCR, create image text, or
accept ground truth. Ground truth is kept in the separate case manifest and is used
only by the evaluation runner after ``classify_field`` has returned.
"""

from __future__ import annotations

import numpy as np

from src import classifier, config
from src.damage import DamageMap
from src.fields import MappedField
from src.ocr import Observation

IMAGE_SHAPE = (630, 1000)
OBSERVATION_X_RANGE = (60, 220)
DAMAGE_PROFILES = (
    "clean",
    "zone_moderate_20pct",
    "zone_heavy_50pct",
    "adjacent_after_value",
)


def _geometry(field_name: str) -> tuple[dict, tuple[int, int], tuple[int, int], tuple[int, int, int, int]]:
    """Use the same fixed synthetic geometry as the existing classifier unit tests."""
    height, width = IMAGE_SHAPE
    spec = config.TEMPLATE["fields"][field_name]
    centre_y = int(spec["row_y"] * height)
    half_band = int(spec["row_height"] * height / 2)
    row_band = (max(0, centre_y - half_band), min(height, centre_y + half_band))
    value_zone = (
        int(config.TEMPLATE["value_x_range"][0] * width),
        int(config.TEMPLATE["value_x_range"][1] * width),
    )
    x0, x1 = OBSERVATION_X_RANGE
    evidence_bbox = (x0, centre_y - 10, x1, centre_y + 10)
    return spec, row_band, value_zone, evidence_bbox


def _damage_map(field_name: str, profile: str) -> DamageMap:
    """Make a repeatable obscuration mask that exercises current classifier gates.

    The profiles are direct synthetic ``DamageMap`` inputs, not rendered documents and
    not outputs from ``build_damage_map``. They isolate classifier behavior at 0%, 20%,
    and 50% field-zone obscuration, plus a fully obscured adjacent probe.
    """
    if profile not in DAMAGE_PROFILES:
        raise ValueError(f"unknown damage profile: {profile!r}")

    height, width = IMAGE_SHAPE
    _, row_band, value_zone, evidence_bbox = _geometry(field_name)
    obscured = np.zeros((height, width), dtype=np.uint8)
    zone_x0, zone_x1 = value_zone

    if profile == "zone_moderate_20pct":
        start = zone_x0 + int((zone_x1 - zone_x0) * 0.80)
        obscured[row_band[0] : row_band[1], start:zone_x1] = 255
    elif profile == "zone_heavy_50pct":
        start = zone_x0 + (zone_x1 - zone_x0) // 2
        obscured[row_band[0] : row_band[1], start:zone_x1] = 255
    elif profile == "adjacent_after_value":
        # Match the current adjacent_obscuration row expansion (15% of bbox height).
        _, y0, x1, y1 = evidence_bbox
        band_height = max(4, y1 - y0)
        probe_y0 = max(0, y0 - int(band_height * 0.15))
        probe_y1 = min(height, y1 + int(band_height * 0.15))
        gap = max(12, int(config.ADJACENT_GAP_FRAC * width))
        probe_x1 = min(width, int(config.TEMPLATE["value_x_range"][1] * width))
        probe_x0 = min(x1, probe_x1)
        probe_end = min(probe_x1, x1 + gap)
        obscured[probe_y0:probe_y1, probe_x0:probe_end] = 255

    return DamageMap(obscured=obscured, paper_luma=255.0)


def classify_controlled(
    field_name: str,
    observed_text: str | None,
    confidence: float = 0.94,
    damage_profile: str = "clean",
) -> classifier.FieldResult:
    """Classify one injected OCR observation without providing ground truth.

    ``confidence`` is an explicitly controlled input to the classifier; it is not
    calibrated and must not be interpreted as a probability of correctness.
    """
    spec, row_band, value_zone, evidence_bbox = _geometry(field_name)
    observations: list[Observation] = []
    bbox: list[int] | None = None
    if observed_text is not None:
        x0, y0, x1, y1 = evidence_bbox
        bbox = [x0, y0, x1, y1]
        observations.append(
            Observation(
                observation_id="obs_test",
                text=observed_text,
                bbox=bbox,
                polygon=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                confidence=confidence,
            )
        )

    mapped = MappedField(
        key=field_name,
        label=spec["label"],
        observations=observations,
        raw_text=observed_text or "",
        bbox=bbox,
        ocr_confidence=confidence if observations else 0.0,
        row_band=row_band,
        value_zone=value_zone,
    )
    damage = _damage_map(field_name, damage_profile)
    return classifier.classify_field(mapped, spec, damage, IMAGE_SHAPE)


__all__ = ["DAMAGE_PROFILES", "IMAGE_SHAPE", "classify_controlled"]
