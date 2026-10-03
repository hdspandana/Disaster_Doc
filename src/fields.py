"""
Deterministic field mapping.

Maps raw OCR observations onto the five template fields using fixed template regions,
label matching and simple spatial rules. Nothing here is learned, and nothing here
invents text: a field only ever holds observations the OCR engine actually returned.

Rules (deliberately boring and auditable):
  1. A field's LABEL observation must sit inside that field's label anchor box and
     fuzzy-match the expected label (OCR routinely reads NAME as "MAME").
  2. VALUE observations sit inside the field's row band and value x-range.
  3. If several observations form one visual line (small vertical offset, small
     horizontal gap) they are merged left-to-right into one evidence group. The
     individual observations are always kept verbatim alongside the joined text.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import numpy as np

from . import config
from .ocr import Observation


# OCR confusions that are safe to fold when comparing labels (never applied to values).
_CONFUSION_MAP = str.maketrans(
    {
        "0": "O", "1": "I", "5": "S", "2": "Z", "8": "B", "6": "G",
        "M": "N", "V": "U", "Q": "O", "L": "I", "C": "G", "K": "X",
    }
)


def normalise_label(text: str) -> str:
    """Uppercase, keep letters/digits/spaces, fold common OCR confusion pairs."""
    kept = "".join(ch for ch in text.upper() if ch.isalnum() or ch == " ")
    return " ".join(kept.split()).translate(_CONFUSION_MAP)


def label_similarity(a: str, b: str) -> float:
    """Similarity of two labels after normalisation (0..1)."""
    na, nb = normalise_label(a), normalise_label(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


@dataclass
class MappedField:
    """One template field with the OCR evidence mapped onto it."""

    key: str
    label: str
    label_observation: Observation | None = None
    observations: list[Observation] = field(default_factory=list)
    raw_text: str = ""
    bbox: list[int] | None = None  # union of evidence boxes, original coords
    ocr_confidence: float = 0.0  # best single-observation OCR confidence
    row_band: tuple[int, int] = (0, 0)
    value_zone: tuple[int, int] = (0, 0)
    # Different usable OCR readings over overlapping pixels cannot be resolved by
    # confidence ordering alone. Keep the alternatives for classification/reporting.
    conflicting_observation_pairs: list[tuple[Observation, Observation]] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.observations)

    def to_dict(self) -> dict:
        return {
            "field_name": self.key,
            "label": self.label,
            "label_observation_id": self.label_observation.observation_id if self.label_observation else None,
            "raw_ocr_text": self.raw_text,
            "bbox": self.bbox,
            "ocr_confidence": round(float(self.ocr_confidence), 4),
            "observation_ids": [o.observation_id for o in self.observations],
        }


def _row_band(spec: dict, shape: tuple[int, int]) -> tuple[int, int]:
    h, _ = shape
    cy = spec["row_y"] * h
    half = spec["row_height"] * h / 2.0
    return int(max(0, cy - half)), int(min(h, cy + half))


def _value_zone(shape: tuple[int, int]) -> tuple[int, int]:
    _, w = shape
    return int(config.TEMPLATE["value_x_range"][0] * w), int(config.TEMPLATE["value_x_range"][1] * w)


def _label_anchor(spec: dict, shape: tuple[int, int]) -> list[int]:
    h, w = shape
    anchor = config.TEMPLATE["label_anchor"]
    x0 = anchor["x"] * w
    y0 = (spec["row_y"] + anchor["y_offset"]) * h
    return [
        int(x0),
        int(y0),
        int(x0 + anchor["width"] * w),
        int(y0 + anchor["height"] * h),
    ]


def _centre(box: list[int]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _inside(box: list[int], region: list[int] | tuple[int, int, int, int], tol: int = 6) -> bool:
    cx, cy = _centre(box)
    x0, y0, x1, y1 = region
    return (x0 - tol) <= cx <= (x1 + tol) and (y0 - tol) <= cy <= (y1 + tol)


def find_label(spec: dict, observations: list[Observation], shape: tuple[int, int]) -> Observation | None:
    """Find the observation that is this field's label."""
    anchor = _label_anchor(spec, shape)
    best: tuple[float, Observation] | None = None
    for obs in observations:
        if not _inside(obs.bbox, anchor):
            continue
        score = label_similarity(obs.text, spec["label"])
        if best is None or score > best[0]:
            best = (score, obs)
    if best is None or best[0] < 0.72:
        return None
    return best[1]


def _value_candidates(
    spec: dict,
    observations: list[Observation],
    shape: tuple[int, int],
    excluded_ids: set[str],
) -> list[Observation]:
    """Return observations in the field value region before choosing a line."""
    band = _row_band(spec, shape)
    zone = _value_zone(shape)
    band_height = max(1, band[1] - band[0])
    max_box_height = 1.6 * band_height  # a value line cannot be taller than its row band
    return [
        o
        for o in observations
        if o.observation_id not in excluded_ids
        and _inside(o.bbox, (zone[0] - 40, band[0], zone[1], band[1]), tol=2)
        and o.alnum_count >= 1
        # Rotated watermarks, logos and multi-line blocks produce tall boxes that overlap
        # several fields. They are not single-line values, so they are not evidence.
        and (o.bbox[3] - o.bbox[1]) <= max_box_height
    ]


def _overlap(a: list[int], b: list[int]) -> bool:
    """True when two OCR boxes share positive area in both dimensions."""
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _conflicting_observation_pairs(candidates: list[Observation]) -> list[tuple[Observation, Observation]]:
    """Keep usable, materially different readings of the same overlapping pixels."""
    usable = [
        o for o in candidates
        if o.confidence >= config.MIN_USABLE_OCR_CONF and o.alnum_count >= config.MIN_USABLE_CHARS
    ]
    pairs: list[tuple[Observation, Observation]] = []
    for index, first in enumerate(usable):
        for second in usable[index + 1:]:
            if first.observation_id == second.observation_id:
                continue
            if first.text.strip().casefold() == second.text.strip().casefold():
                continue
            if _overlap(first.bbox, second.bbox):
                pairs.append((first, second))
    return pairs


def select_value_observations(
    spec: dict,
    observations: list[Observation],
    shape: tuple[int, int],
    excluded_ids: set[str],
) -> list[Observation]:
    """Collect the observations that belong to this field's value, merged into one line."""
    band = _row_band(spec, shape)
    candidates = _value_candidates(spec, observations, shape, excluded_ids)
    if not candidates:
        return []

    height = max(1, band[1] - band[0])
    gap_limit = 0.06 * shape[1]

    # Build a left-to-right chain around the most confident observation.
    primary = max(candidates, key=lambda o: (o.confidence, o.alnum_count))
    line = [primary]
    for obs in sorted(candidates, key=lambda o: o.bbox[0]):
        if obs is primary:
            continue
        cy_primary = _centre(primary.bbox)[1]
        cy_obs = _centre(obs.bbox)[1]
        if abs(cy_obs - cy_primary) > 0.55 * height:
            continue
        h_obs = obs.bbox[3] - obs.bbox[1]
        h_primary = max(1, primary.bbox[3] - primary.bbox[1])
        if not (0.4 * h_primary <= h_obs <= 2.2 * h_primary):
            continue
        near = any(
            min(abs(obs.bbox[0] - other.bbox[2]), abs(other.bbox[0] - obs.bbox[2])) <= gap_limit
            for other in line
        )
        if near:
            line.append(obs)
    return sorted(line, key=lambda o: o.bbox[0])


def map_fields(observations: list[Observation], shape: tuple[int, int]) -> dict[str, MappedField]:
    """Map observations onto every field in the template."""
    labels: dict[str, Observation | None] = {}
    for key, spec in config.TEMPLATE["fields"].items():
        labels[key] = find_label(spec, observations, shape)

    label_ids = {o.observation_id for o in labels.values() if o is not None}

    mapped: dict[str, MappedField] = {}
    for key, spec in config.TEMPLATE["fields"].items():
        candidates = _value_candidates(spec, observations, shape, label_ids)
        conflicts = _conflicting_observation_pairs(candidates)
        value_obs = select_value_observations(spec, observations, shape, label_ids)
        # Keep one deterministic reading in the compatibility raw_text field; the
        # classifier receives every competing observation separately and must abstain.
        losing_ids: set[str] = set()
        for first, second in conflicts:
            winner = max((first, second), key=lambda o: (o.confidence, o.alnum_count))
            loser = second if winner is first else first
            losing_ids.add(loser.observation_id)
        value_obs = [o for o in value_obs if o.observation_id not in losing_ids]
        band = _row_band(spec, shape)
        zone = _value_zone(shape)

        raw_text = " ".join(o.text for o in value_obs).strip()
        if value_obs:
            x0 = min(o.bbox[0] for o in value_obs)
            y0 = min(o.bbox[1] for o in value_obs)
            x1 = max(o.bbox[2] for o in value_obs)
            y1 = max(o.bbox[3] for o in value_obs)
            bbox: list[int] | None = [int(x0), int(y0), int(x1), int(y1)]
            confidence = max(o.confidence for o in value_obs)
        else:
            bbox, confidence = None, 0.0

        mapped[key] = MappedField(
            key=key,
            label=spec["label"],
            label_observation=labels[key],
            observations=value_obs,
            raw_text=raw_text,
            bbox=bbox,
            ocr_confidence=confidence,
            row_band=band,
            value_zone=zone,
            conflicting_observation_pairs=conflicts,
        )
    return mapped


__all__ = ["MappedField", "map_fields", "find_label", "label_similarity", "normalise_label"]


def _unused(_: np.ndarray) -> None:  # pragma: no cover - keeps numpy import meaningful
    return None
