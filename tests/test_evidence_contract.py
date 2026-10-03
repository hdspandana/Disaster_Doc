"""Regression tests for the versioned deterministic evidence contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from src import ai_commentary, config, evidence
from src.classifier import FieldResult, ReasonCode
from src.report import to_json_bytes


def _code_values(result) -> list[str]:
    return [code.value for code in result.reason_codes]


def test_observed_value_can_exist_without_a_claimed_value():
    result = classify_controlled("id_number", "DX-48", 0.94)

    assert result.status == config.STATUS_PARTIAL
    assert result.observed_value == "DX-48"
    assert result.claimed_value is None
    serialized = result.to_dict()
    assert serialized["observed_value"] == "DX-48"
    assert serialized["claimed_value"] is None


def test_recovered_result_exposes_distinct_observed_and_claimed_values():
    result = classify_controlled("id_number", "DX-48291", 0.94)

    assert result.status == config.STATUS_RECOVERED
    assert result.observed_value == "DX-48291"
    assert result.claimed_value == "DX-48291"
    serialized = result.to_dict()
    assert serialized["observed_value"] == serialized["claimed_value"] == "DX-48291"
    assert "observed_value" in serialized and "claimed_value" in serialized
    assert _code_values(result) == ["RECOVERY_GATES_PASSED"]


@pytest.mark.parametrize(
    ("field_name", "observed", "confidence", "expected_status"),
    [
        ("id_number", "DX-48", 0.94, config.STATUS_PARTIAL),
        ("id_number", None, 0.94, config.STATUS_UNRECOVERABLE),
    ],
)
def test_non_recovered_statuses_never_claim_a_value(field_name, observed, confidence, expected_status):
    result = classify_controlled(field_name, observed, confidence)

    assert result.status == expected_status
    assert result.claimed_value is None
    assert result.value is None
    assert result.to_dict()["claimed_value"] is None


def test_classification_refuses_an_invalid_non_null_claim():
    with pytest.raises(ValueError, match="cannot carry a claimed value"):
        FieldResult(
            field_name="id_number",
            label="ID NUMBER",
            expected_type="string",
            status=config.STATUS_PARTIAL,
            value="DX-48291",
            raw_ocr_text="DX-48",
            confidence_bucket="low",
            ocr_confidence=0.9,
        )


def test_production_evidence_has_no_ground_truth_and_is_versioned():
    result = classify_controlled("id_number", "DX-48281", 0.94)
    document = evidence.new_document(
        document_id="sha256:" + "a" * 64,
        fields=[result],
        observations=[observation.to_dict() for observation in result.ocr_observations],
        audit={"ocr_engine": "EasyOCR", "code_version": config.CODE_VERSION},
    )
    payload = json.loads(to_json_bytes(document))
    field = payload["fields"][0]
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["evidence_schema_version"] == config.EVIDENCE_SCHEMA_VERSION
    assert payload["metadata"] == {
        "code_version": config.CODE_VERSION,
        "config_version": config.CONFIG_VERSION,
        "template_version": config.TEMPLATE_VERSION,
        "template": config.TEMPLATE_NAME,
        "ocr_engine": "EasyOCR",
    }
    assert field["observed_value"] == "DX-48281"
    assert field["claimed_value"] == "DX-48281"
    assert field["value"] == field["claimed_value"]  # legacy alias
    assert field["raw_ocr_text"] == result.raw_ocr_text  # legacy alias
    assert field["ocr_evidence"][0]["observed_text"] == "DX-48281"
    assert field["ocr_evidence"][0]["polygon"]
    assert "ground_truth" not in serialized
    assert "DX-48291" not in serialized
    assert str(ROOT) not in serialized
    assert "api_key" not in serialized.lower()


def test_shape_validation_evidence_is_typed_and_unknown_when_not_run():
    malformed = classify_controlled("id_number", "DX-48", 0.94)
    missing = classify_controlled("id_number", None, 0.94)
    malformed_rows = {row.validator: row.result for row in malformed.validation_evidence}
    missing_rows = {row.validator: row.result for row in missing.validation_evidence}

    assert [
        malformed_rows[name]
        for name in ("minimum_character_count", "minimum_token_count", "field_pattern", "truncation_marker")
    ] == ["FAIL", "PASS", "FAIL", "PASS"]
    assert malformed_rows["id_length"] == "FAIL"
    assert malformed.validation_result == "FAIL"
    assert _code_values(malformed) == [
        "MINIMUM_CHARACTER_COUNT_FAILED",
        "FORMAT_INVALID",
        "INVALID_LENGTH",
        "VALIDATION_FAILED",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert {missing_rows[name] for name in (
        "minimum_character_count", "minimum_token_count", "field_pattern", "truncation_marker"
    )} == {"UNKNOWN"}
    assert missing.validation_result == "UNKNOWN"
    assert missing.damage_evidence.evaluated is False
    assert missing.damage_evidence.obscuration_ratio is None


def test_malformed_format_has_stable_machine_readable_reason_code():
    result = classify_controlled("id_number", "DX-48", 0.94)

    assert ReasonCode.FORMAT_INVALID in result.reason_codes
    assert result.to_dict()["reason_codes"] == _code_values(result)
    assert "Observed text did not match the configured field pattern." in result.to_dict()["reason_descriptions"]


def test_low_confidence_has_reason_code_without_claiming_a_probability():
    result = classify_controlled("id_number", "DX-48291", 0.4)

    assert result.status == config.STATUS_PARTIAL
    assert ReasonCode.LOW_OCR_CONFIDENCE in result.reason_codes
    assert result.claimed_value is None
    assert result.ocr_confidence == 0.4
    assert "probability" in result.to_dict()["reason_descriptions"][0]


def test_damage_rejections_have_codes_for_the_existing_damage_gates():
    high_local = classify_controlled("id_number", "DX-48291", 0.94, "zone_heavy_50pct")
    adjacent = classify_controlled("id_number", "DX-48291", 0.94, "adjacent_after_value")

    assert high_local.status == config.STATUS_PARTIAL
    assert ReasonCode.HIGH_LOCAL_DAMAGE in high_local.reason_codes
    assert high_local.damage_evidence.evaluated is True
    assert high_local.damage_evidence.obscuration_ratio == pytest.approx(0.5)
    assert adjacent.status == config.STATUS_PARTIAL
    assert ReasonCode.ADJACENT_OBSCURATION in adjacent.reason_codes
    assert adjacent.damage_evidence.adjacent_obscuration_ratio == pytest.approx(1.0)


def test_reason_code_order_is_repeatable_and_no_future_recognition_code_is_emitted():
    first = classify_controlled("id_number", "DX-48_", 0.4, "adjacent_after_value")
    second = classify_controlled("id_number", "DX-48_", 0.4, "adjacent_after_value")
    codes = _code_values(first)

    assert codes == _code_values(second)
    assert codes == [
        "MINIMUM_CHARACTER_COUNT_FAILED",
        "FORMAT_INVALID",
        "TRUNCATED_OBSERVATION",
        "INVALID_LENGTH",
        "INVALID_CHARACTER",
        "VALIDATION_FAILED",
        "ADJACENT_OBSCURATION",
        "LOW_OCR_CONFIDENCE",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert "RECOGNITION_DISAGREEMENT" not in {code.value for code in ReasonCode}


def test_ai_output_cannot_mutate_deterministic_evidence_or_inject_reason_codes():
    field = classify_controlled("district", "NORT", 0.94)
    before = field.to_dict()
    pattern = config.TEMPLATE["fields"]["district"]["pattern"]
    raw_entry = {
        "possible_interpretations": ["NORTHVALE"],
        "verification_instruction": "Confirm with the applicant.",
        "commentary": "Only a fragment is visible.",
        "observed_value": "ATTACKER-OBSERVATION",
        "claimed_value": "NORTHVALE",
        "value": "NORTHVALE",
        "status": config.STATUS_RECOVERED,
        "reason_codes": ["RECOGNITION_DISAGREEMENT"],
        "evidence": [{"text": "fabricated"}],
        "ocr_evidence": [{"observed_text": "fabricated"}],
        "validation_evidence": [{"validator": "injected", "result": "PASS"}],
        "validation_result": "PASS",
        "validation_reason_codes": ["CALENDAR_DATE_INVALID"],
        "dob_calendar": "PASS",
        "damage_evidence": {"evaluated": True},
        "ground_truth": "NORTHVALE",
    }
    cleaned, guardrail_notes = ai_commentary.enforce_guardrails(raw_entry, field, pattern)
    events, notes = evidence.attach_ai_commentary(
        [field], {"district": cleaned}, guardrail_notes
    )
    after = field.to_dict()

    protected = (
        "observed_value",
        "claimed_value",
        "value",
        "raw_ocr_text",
        "status",
        "reason_codes",
        "reason_descriptions",
        "evidence",
        "ocr_evidence",
        "validation_result",
        "validation_evidence",
        "damage_evidence",
    )
    assert all(after[key] == before[key] for key in protected)
    assert field.ai_commentary is not None
    assert "reason_codes" not in field.ai_commentary
    assert "ground_truth" not in field.ai_commentary
    assert events and events[0]["event"] == "ai_commentary_attached"
    assert all(
        any(key in note for note in notes)
        for key in ("observed_value", "claimed_value", "status", "reason_codes", "validation_result")
    )
