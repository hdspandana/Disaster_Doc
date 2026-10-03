"""Deterministic classifier tests independent of EasyOCR model/runtime output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from src import config


def _classify(field_name: str, observed_text: str | None, confidence: float = 0.94):
    """Reuse the evaluation-only controlled observation and clean damage-map harness."""
    return classify_controlled(field_name, observed_text, confidence, damage_profile="clean")


def test_well_formed_observed_date_can_be_reported_verbatim():
    result = _classify("dob", "14 08 1991")

    assert result.status == config.STATUS_RECOVERED
    assert result.value == "14 08 1991"


def test_malformed_observed_date_is_partial_and_has_no_claimed_value():
    result = _classify("dob", "1408 1991")

    assert result.status == config.STATUS_PARTIAL
    assert result.value is None


def test_no_observation_is_unrecoverable_and_has_no_claimed_value():
    result = _classify("dob", None)

    assert result.status == config.STATUS_UNRECOVERABLE
    assert result.value is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known P0 limitation: a single high-confidence, format-valid OCR substitution has no "
        "independent recognition evidence to trigger abstention yet."
    ),
)
def test_high_confidence_format_valid_identifier_typo_should_not_be_claimed():
    """A regex-valid digit substitution is a known false-recovery risk, not verified truth."""
    ground_truth = "DX-48291"
    observed_text = "DX-48281"
    assert observed_text != ground_truth  # controlled synthetic error

    result = _classify("id_number", observed_text, confidence=0.94)

    assert result.status != config.STATUS_RECOVERED
    assert result.value is None
