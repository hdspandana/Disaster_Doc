"""Conservative handling for incompatible OCR readings of the same image region."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config
from src.classifier import ReasonCode, classify_field
from src.damage import DamageMap
from src.fields import map_fields
from src.ocr import Observation

SHAPE = (630, 1000)


def _observation(observation_id: str, text: str, bbox: list[int], confidence: float) -> Observation:
    x0, y0, x1, y1 = bbox
    return Observation(
        observation_id=observation_id,
        text=text,
        bbox=bbox,
        polygon=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        confidence=confidence,
    )


def _classify(field_name: str, observations: list[Observation]):
    mapped = map_fields(observations, SHAPE)[field_name]
    damage = DamageMap(np.zeros(SHAPE, dtype=np.uint8), paper_luma=255.0)
    result = classify_field(mapped, config.TEMPLATE["fields"][field_name], damage, SHAPE)
    return mapped, result


def test_different_usable_readings_over_same_pixels_are_preserved_and_not_claimed():
    bbox = [60, 211, 225, 241]
    first = _observation("obs_001", "DX-48291", bbox, 0.99)
    second = _observation("obs_002", "DX-48281", bbox, 0.98)

    mapped, result = _classify("id_number", [first, second])

    assert len(mapped.conflicting_observation_pairs) == 1
    assert result.status == config.STATUS_PARTIAL
    assert result.claimed_value is None
    assert result.observed_value == "DX-48291"  # compatibility view; not promoted to a claim
    assert ReasonCode.CONFLICTING_OCR_OBSERVATIONS in result.reason_codes
    assert result.validation_result == "UNKNOWN"
    assert {row.result for row in result.validation_evidence if row.validator == "id_number_structure"} == {"PASS"}

    serialized = result.to_dict()
    assert {item["observed_text"] for item in serialized["ocr_evidence"]} == {"DX-48291", "DX-48281"}
    assert all(item["usable"] for item in serialized["ocr_evidence"])
    conflict = next(row for row in serialized["validation_evidence"] if row["validator"] == "ocr_observation_consistency")
    assert conflict["result"] == "UNKNOWN"
    assert conflict["reason"] == "CONFLICTING_OCR_OBSERVATIONS"
    assert conflict["details"]["conflicts"][0]["observation_ids"] == ["obs_001", "obs_002"]
    assert conflict["details"]["conflicts"][0]["observed_texts"] == ["DX-48291", "DX-48281"]


def test_same_text_overlapping_duplicate_does_not_create_a_conflict():
    bbox = [60, 211, 225, 241]
    _, result = _classify(
        "id_number",
        [
            _observation("obs_001", "DX-48291", bbox, 0.99),
            _observation("obs_002", "DX-48291", bbox, 0.98),
        ],
    )

    assert result.status == config.STATUS_RECOVERED
    assert result.claimed_value == "DX-48291"
    assert ReasonCode.CONFLICTING_OCR_OBSERVATIONS not in result.reason_codes


def test_subthreshold_alternative_does_not_veto_a_usable_reading():
    bbox = [60, 211, 225, 241]
    _, result = _classify(
        "id_number",
        [
            _observation("obs_001", "DX-48291", bbox, 0.99),
            _observation("obs_002", "DX-48281", bbox, 0.20),
        ],
    )

    assert result.status == config.STATUS_RECOVERED
    assert result.claimed_value == "DX-48291"
    assert ReasonCode.CONFLICTING_OCR_OBSERVATIONS not in result.reason_codes


def test_nonoverlapping_adjacent_name_boxes_still_form_one_transcription():
    row_y = int(config.TEMPLATE["fields"]["name"]["row_y"] * SHAPE[0])
    y0, y1 = row_y - 10, row_y + 10
    _, result = _classify(
        "name",
        [
            _observation("obs_001", "ANANYA", [60, y0, 125, y1], 0.96),
            _observation("obs_002", "RAO", [130, y0, 170, y1], 0.95),
        ],
    )

    assert result.status == config.STATUS_RECOVERED
    assert result.claimed_value == "ANANYA RAO"
    assert ReasonCode.CONFLICTING_OCR_OBSERVATIONS not in result.reason_codes
