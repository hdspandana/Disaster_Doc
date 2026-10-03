"""Focused regression tests for Phase 5 deterministic field validation."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from src import ai_commentary, config, evidence
from src.classifier import ReasonCode
from src.validation import aggregate_validation_results, validate_field


def _rows(field_name: str, observed: str | None):
    return {item.validator: item for item in validate_field(field_name, observed).evidence}


def test_validator_contract_uses_only_the_four_declared_results():
    outputs = {
        validate_field("name", "ANANYA RAO").result,
        validate_field("dob", "31 02 1991").result,
        validate_field("dob", None).result,
        _rows("district", "BENGALURU")["district_registry"].result,
    }
    assert outputs == {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
    assert aggregate_validation_results(["PASS", "NOT_APPLICABLE"]) == "PASS"
    assert aggregate_validation_results(["UNKNOWN", "NOT_APPLICABLE"]) == "UNKNOWN"
    assert aggregate_validation_results(["FAIL", "UNKNOWN"]) == "FAIL"
    assert aggregate_validation_results(["NOT_APPLICABLE"]) == "NOT_APPLICABLE"


def test_id_valid_malformed_length_character_and_prefix_components():
    valid = validate_field("id_number", "DX-48291")
    assert valid.result == "PASS"
    assert {name: _rows("id_number", "DX-48291")[name].result for name in (
        "id_prefix", "id_length", "id_separator", "id_serial"
    )} == {"id_prefix": "PASS", "id_length": "PASS", "id_separator": "PASS", "id_serial": "PASS"}

    malformed_separator = validate_field("id_number", "DX/48291")
    assert malformed_separator.result == "FAIL"
    assert "INVALID_CHARACTER" in malformed_separator.reason_codes

    wrong_length = validate_field("id_number", "DX-482910")
    assert wrong_length.result == "FAIL"
    assert "INVALID_LENGTH" in wrong_length.reason_codes

    invalid_character = validate_field("id_number", "DX-48B91")
    assert invalid_character.result == "FAIL"
    assert "INVALID_CHARACTER" in invalid_character.reason_codes

    invalid_prefix_class = validate_field("id_number", "D8-48291")
    assert invalid_prefix_class.result == "FAIL"
    assert "INVALID_PREFIX" in invalid_prefix_class.reason_codes


def test_id_uses_template_prefix_class_not_ground_truth_or_a_fixed_dx_prefix():
    # The configured format allows any two uppercase ASCII letters; only the evaluation
    # test knows this observation differs from its control value.
    observed = "DX-48281"
    evaluation_only_ground_truth = "DX-48291"
    result = validate_field("id_number", observed, config.TEMPLATE["fields"]["id_number"])

    assert result.result == "PASS"
    assert observed != evaluation_only_ground_truth
    assert "ground_truth" not in inspect.signature(validate_field).parameters
    payload = json.dumps([item.to_dict() for item in result.evidence])
    assert "ground_truth" not in payload
    assert "DX-48291" not in payload

    other_two_letter_prefix = validate_field("id_number", "QY-73156")
    assert other_two_letter_prefix.result == "PASS"
    assert _rows("id_number", "QY-73156")["id_prefix"].details["fixed_prefix_required"] is False

    # Preserve the existing case-insensitive template match; evidence makes the
    # normalization visible without changing the observed OCR value.
    lowercase = validate_field("id_number", "qy-73156")
    assert lowercase.result == "PASS"
    prefix_details = _rows("id_number", "qy-73156")["id_prefix"].details
    assert prefix_details["observed_prefix"] == "qy"
    assert prefix_details["normalized_prefix"] == "QY"


def test_dob_calendar_validation_handles_day_month_year_and_leap_years():
    for text in ("01 01 1991", "29 02 2020", "31-12-2099", "01/01/1900"):
        result = validate_field("dob", text)
        assert result.result == "PASS", text
        assert _rows("dob", text)["dob_year_representation"].result == "PASS"
        assert _rows("dob", text)["dob_calendar"].result == "PASS"

    for text in ("32 01 1991", "01 13 1991", "31 02 1991", "31 04 1991", "29 02 2021"):
        result = validate_field("dob", text)
        assert result.result == "FAIL", text
        assert _rows("dob", text)["dob_calendar"].result == "FAIL"
        assert "CALENDAR_DATE_INVALID" in result.reason_codes
        calendar = _rows("dob", text)["dob_calendar"].to_dict()
        assert calendar["reason"] == "INVALID_CALENDAR_DATE"
        assert "ground_truth" not in json.dumps(calendar)


def test_dob_incomplete_input_is_unknown_for_calendar_and_existing_format_checks_reject_it():
    result = validate_field("dob", "31 02")
    rows = _rows("dob", "31 02")

    assert rows["dob_calendar"].result == "UNKNOWN"
    assert rows["dob_year_representation"].result == "UNKNOWN"
    assert rows["field_pattern"].result == "FAIL"
    assert result.result == "FAIL"


def test_year_representation_uses_existing_template_not_an_arbitrary_age_bound():
    unsupported = validate_field("dob", "01 01 1899")
    rows = _rows("dob", "01 01 1899")

    assert rows["dob_year_representation"].result == "FAIL"
    # The date itself is a valid calendar date; it is the existing template's
    # 1900–2099 representation rule, not a new age policy, that rejects it.
    assert rows["dob_calendar"].result == "PASS"
    assert unsupported.result == "FAIL"


def test_name_checks_are_structural_and_plausible_wrong_names_still_pass():
    assert validate_field("name", "ANANYA RAO").result == "PASS"
    assert validate_field("name", "ANANYA RAJ").result == "PASS"
    assert validate_field("name", "").result == "FAIL"
    assert validate_field("name", "ANANYA!!! RAO").result == "FAIL"

    truncated = validate_field("name", "ANANYA RAO...")
    assert truncated.result == "FAIL"
    assert "TRUNCATED_OBSERVATION" in truncated.reason_codes
    assert validate_field("name", None).result == "UNKNOWN"


def test_district_uses_template_shape_without_inventing_a_registry():
    known_synthetic_value = validate_field("district", "BENGALURU")
    plausible_unlisted_value = validate_field("district", "MYSURU")
    assert known_synthetic_value.result == "PASS"
    assert plausible_unlisted_value.result == "PASS"
    assert _rows("district", "BENGALURU")["district_registry"].result == "NOT_APPLICABLE"
    assert _rows("district", "MYSURU")["district_registry"].reason == "NO_CONTROLLED_DISTRICT_REGISTRY"

    malformed = validate_field("district", "8ENGALURU")
    assert malformed.result == "FAIL"
    truncated = validate_field("district", "NORTH...")
    assert truncated.result == "FAIL"
    assert "TRUNCATED_OBSERVATION" in truncated.reason_codes
    assert validate_field("district", None).result == "UNKNOWN"


def test_address_checks_are_conservative_and_do_not_assert_address_existence():
    assert validate_field("address", "42 LAKEVIEW STREET, WARD 7").result == "PASS"
    assert validate_field("address", "42 LAKEVIEW STREET, WARD 1").result == "PASS"
    assert validate_field("address", "").result == "FAIL"
    assert validate_field("address", "42 OAK STREET # 3").result == "FAIL"

    truncated = validate_field("address", "42 LAKEVIEW STREET, WARD 7...")
    assert truncated.result == "FAIL"
    assert "TRUNCATED_OBSERVATION" in truncated.reason_codes
    insufficient = validate_field("address", "42 LAKE")
    assert insufficient.result == "FAIL"
    assert validate_field("address", None).result == "UNKNOWN"


def test_pass_is_not_correctness_and_only_invalid_calendar_dob_changes_classification():
    wrong_id = classify_controlled("id_number", "DX-48281", 0.94)
    assert wrong_id.validation_result == "PASS"
    assert wrong_id.status == config.STATUS_RECOVERED
    assert wrong_id.claimed_value == "DX-48281"

    invalid_date = classify_controlled("dob", "31 02 1991", 0.94)
    assert invalid_date.validation_result == "FAIL"
    assert invalid_date.status == config.STATUS_PARTIAL
    assert invalid_date.claimed_value is None
    assert ReasonCode.CALENDAR_DATE_INVALID in invalid_date.reason_codes

    valid_but_unverified_date = classify_controlled("dob", "14 08 1981", 0.94)
    assert valid_but_unverified_date.validation_result == "PASS"
    assert valid_but_unverified_date.status == config.STATUS_RECOVERED


def test_ai_cannot_create_or_change_validation_evidence_or_authority():
    field = classify_controlled("dob", "31 02 1991", 0.94)
    before = field.to_dict()
    raw_entry = {
        "possible_interpretations": ["31 02 1991"],
        "commentary": "The deterministic calendar validator failed.",
        "validation_result": "PASS",
        "validation_evidence": [{"validator": "dob_calendar", "result": "PASS"}],
        "validation_reason_codes": [],
        "dob_calendar": "PASS",
        "status": config.STATUS_RECOVERED,
        "claimed_value": "31 02 1991",
        "reason_codes": [],
    }
    cleaned, guardrail_notes = ai_commentary.enforce_guardrails(
        raw_entry, field, config.TEMPLATE["fields"]["dob"]["pattern"]
    )
    _events, notes = evidence.attach_ai_commentary([field], {"dob": cleaned}, guardrail_notes)
    after = field.to_dict()

    assert after["validation_result"] == before["validation_result"] == "FAIL"
    assert after["validation_evidence"] == before["validation_evidence"]
    assert after["status"] == before["status"] == config.STATUS_PARTIAL
    assert after["claimed_value"] == before["claimed_value"] is None
    assert after["reason_codes"] == before["reason_codes"]
    assert field.ai_commentary is not None
    assert any("validation_result" in note for note in notes)


def test_ai_prompt_can_only_summarize_system_validation():
    field = classify_controlled("dob", "31 02 1991", 0.94)
    prompt = ai_commentary._prompt(
        field,
        config.TEMPLATE["fields"]["dob"]["pattern"],
        {"template": config.TEMPLATE_NAME},
    )

    assert 'Deterministic validation result: FAIL' in prompt
    assert '"validator": "dob_calendar"' in prompt
    assert "If validation is mentioned, summarize only the deterministic result" in prompt
