"""Deterministic, field-specific validation for the configured synthetic template.

Validation describes whether an observed string satisfies implemented structural or
calendar rules. It is not source truth, identity verification, or document authentication.
This module has no access to evaluation manifests or ground truth.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from . import config

ValidationOutcome = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
# Kept as a compatibility alias for callers that used the Phase 4 type name.
ValidatorResult = ValidationOutcome


@dataclass(frozen=True)
class ValidationEvidence:
    """One deterministic validator result with a stable reason and supporting details."""

    validator: str
    result: ValidationOutcome
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator": self.validator,
            "result": self.result,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ValidationFinding:
    """A deterministic classifier-facing validation reason; contains no truth lookup."""

    reason_code: str
    message: str


@dataclass(frozen=True)
class FieldValidation:
    """Complete validation evidence for one observed field string."""

    evidence: tuple[ValidationEvidence, ...]
    findings: tuple[ValidationFinding, ...]

    @property
    def result(self) -> ValidationOutcome:
        return aggregate_validation_results(item.result for item in self.evidence)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.reason_code for item in self.findings))

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(item.message for item in self.findings)


def aggregate_validation_results(results: Iterable[ValidationOutcome]) -> ValidationOutcome:
    """Combine validator results conservatively; NOT_APPLICABLE never counts as PASS."""
    values = list(results)
    applicable = [result for result in values if result != "NOT_APPLICABLE"]
    if any(result == "FAIL" for result in applicable):
        return "FAIL"
    if not applicable:
        return "NOT_APPLICABLE"
    if any(result == "UNKNOWN" for result in applicable):
        return "UNKNOWN"
    return "PASS"


def _evidence(
    validator: str,
    result: ValidationOutcome,
    reason: str,
    details: dict[str, Any] | None = None,
) -> ValidationEvidence:
    return ValidationEvidence(validator, result, reason, details or {})


def _base_checks(
    observed_value: str | None, spec: dict[str, Any]
) -> tuple[list[ValidationEvidence], list[ValidationFinding]]:
    """The Phase 4 character/token/pattern/truncation checks, represented once."""
    if observed_value is None:
        unknown = {
            "reason": "no_usable_ocr_observation",
        }
        return [
            _evidence(name, "UNKNOWN", "INSUFFICIENT_OBSERVATION", unknown)
            for name in (
                "minimum_character_count",
                "minimum_token_count",
                "field_pattern",
                "truncation_marker",
            )
        ], []

    text = observed_value.strip()
    alnum_count = sum(character.isalnum() for character in text)
    tokens = [token for token in re.split(r"\s+", text) if token]
    pattern_valid = re.fullmatch(spec["pattern"], text.upper()) is not None
    marker = text[-1] if text and text[-1] in config.TRUNCATION_MARKERS else None

    evidence: list[ValidationEvidence] = []
    findings: list[ValidationFinding] = []

    char_valid = alnum_count >= spec["min_chars"]
    evidence.append(
        _evidence(
            "minimum_character_count",
            "PASS" if char_valid else "FAIL",
            "MINIMUM_CHARACTER_COUNT_MET" if char_valid else "MINIMUM_CHARACTER_COUNT_FAILED",
            {"observed": alnum_count, "minimum": spec["min_chars"]},
        )
    )
    if not char_valid:
        findings.append(
            ValidationFinding(
                "MINIMUM_CHARACTER_COUNT_FAILED",
                "Observed text is shorter than the template expects for this field "
                f"({alnum_count} of at least {spec['min_chars']} characters).",
            )
        )

    token_valid = len(tokens) >= spec["min_tokens"]
    evidence.append(
        _evidence(
            "minimum_token_count",
            "PASS" if token_valid else "FAIL",
            "MINIMUM_TOKEN_COUNT_MET" if token_valid else "MINIMUM_TOKEN_COUNT_FAILED",
            {"observed": len(tokens), "minimum": spec["min_tokens"]},
        )
    )
    if not token_valid:
        findings.append(
            ValidationFinding(
                "MINIMUM_TOKEN_COUNT_FAILED",
                "Observed text has fewer parts than the template expects "
                f"({len(tokens)} of at least {spec['min_tokens']}).",
            )
        )

    evidence.append(
        _evidence(
            "field_pattern",
            "PASS" if pattern_valid else "FAIL",
            "TEMPLATE_PATTERN_MATCHED" if pattern_valid else "FORMAT_INVALID",
            {"pattern": spec["pattern"], "matched": pattern_valid, "case_insensitive": True},
        )
    )
    if not pattern_valid:
        findings.append(
            ValidationFinding(
                "FORMAT_INVALID",
                f"Observed text does not satisfy the expected pattern for this field ({spec['pattern']}).",
            )
        )

    evidence.append(
        _evidence(
            "truncation_marker",
            "FAIL" if marker else "PASS",
            "TRUNCATED_OBSERVATION" if marker else "NO_TRUNCATION_MARKER",
            {"detected": marker is not None, "marker": marker},
        )
    )
    if marker:
        findings.append(
            ValidationFinding(
                "TRUNCATED_OBSERVATION",
                f"Observed text ends with the truncation marker '{marker}', so the printed value "
                "probably continued beyond what is legible.",
            )
        )

    return evidence, findings


def _id_checks(
    observed_value: str | None, spec: dict[str, Any]
) -> tuple[list[ValidationEvidence], list[ValidationFinding]]:
    """Expose the already-configured ID structure without requiring a particular ID value."""
    components = spec.get("id_components")
    if not isinstance(components, dict):
        raise TypeError("id_number template must define id_components")

    prefix_length = int(components["prefix_length"])
    separator = str(components["separator"])
    serial_length = int(components["serial_length"])
    expected_length = prefix_length + len(separator) + serial_length
    if observed_value is None:
        return [
            _evidence(name, "UNKNOWN", "INSUFFICIENT_OBSERVATION", {"reason": "no_usable_ocr_observation"})
            for name in ("id_prefix", "id_length", "id_separator", "id_serial")
        ], []

    raw_text = observed_value.strip()
    text = raw_text.upper()
    prefix = text[:prefix_length]
    observed_prefix = raw_text[:prefix_length]
    separator_start = prefix_length
    separator_end = separator_start + len(separator)
    observed_separator = text[separator_start:separator_end]
    serial = text[separator_end:]
    prefix_pattern = rf"[A-Z]{{{prefix_length}}}"
    serial_pattern = rf"[0-9]{{{serial_length}}}"

    prefix_valid = re.fullmatch(prefix_pattern, prefix) is not None if len(text) >= prefix_length else None
    prefix_result: ValidationOutcome = (
        "UNKNOWN" if prefix_valid is None else "PASS" if prefix_valid else "FAIL"
    )
    evidence = [
        _evidence(
            "id_prefix",
            prefix_result,
            "INSUFFICIENT_OBSERVATION" if prefix_valid is None else "PREFIX_CLASS_VALID" if prefix_valid else "INVALID_PREFIX",
            {
                "observed_prefix": observed_prefix,
                "normalized_prefix": prefix,
                "required_character_class": "uppercase ASCII letters after existing case-insensitive normalization",
                "required_length": prefix_length,
                "fixed_prefix_required": bool(components.get("fixed_prefix_required", False)),
            },
        )
    ]
    findings: list[ValidationFinding] = []
    if prefix_valid is False:
        findings.append(
            ValidationFinding(
                "INVALID_PREFIX",
                "ID prefix must use the configured number of uppercase ASCII letters; no specific letter pair is authoritative.",
            )
        )

    length_valid = len(text) == expected_length
    evidence.append(
        _evidence(
            "id_length",
            "PASS" if length_valid else "FAIL",
            "EXPECTED_LENGTH" if length_valid else "INVALID_LENGTH",
            {"observed": len(text), "expected": expected_length},
        )
    )
    if not length_valid:
        findings.append(
            ValidationFinding(
                "INVALID_LENGTH",
                f"ID text must be exactly {expected_length} characters under the configured template format.",
            )
        )

    separator_available = len(text) >= separator_end
    separator_valid = observed_separator == separator if separator_available else None
    separator_result: ValidationOutcome = (
        "UNKNOWN" if separator_valid is None else "PASS" if separator_valid else "FAIL"
    )
    evidence.append(
        _evidence(
            "id_separator",
            separator_result,
            "INSUFFICIENT_OBSERVATION" if separator_valid is None else "SEPARATOR_VALID" if separator_valid else "INVALID_SEPARATOR",
            {"observed": observed_separator, "expected": separator},
        )
    )
    if separator_valid is False:
        findings.append(
            ValidationFinding(
                "INVALID_CHARACTER",
                "ID separator does not match the configured template separator.",
            )
        )

    serial_characters_valid = re.fullmatch(r"[0-9]*", serial) is not None
    serial_length_valid = len(serial) == serial_length
    serial_valid = re.fullmatch(serial_pattern, serial) is not None
    if not serial:
        serial_result: ValidationOutcome = "UNKNOWN"
        serial_reason = "INSUFFICIENT_OBSERVATION"
    elif not serial_valid:
        serial_result = "FAIL"
        serial_reason = "INVALID_CHARACTER" if not serial_characters_valid else "INVALID_LENGTH"
    else:
        serial_result = "PASS"
        serial_reason = "SERIAL_DIGITS_VALID"
    evidence.append(
        _evidence(
            "id_serial",
            serial_result,
            serial_reason,
            {
                "observed": serial,
                "required_character_class": "ASCII digits",
                "required_pattern": serial_pattern,
                "required_length": serial_length,
                "character_class_valid": serial_characters_valid,
                "length_valid": serial_length_valid,
            },
        )
    )
    if not serial_characters_valid:
        findings.append(
            ValidationFinding("INVALID_CHARACTER", "ID serial component must contain ASCII digits only.")
        )
    elif serial and not serial_length_valid:
        findings.append(
            ValidationFinding(
                "INVALID_LENGTH",
                f"ID serial component must contain exactly {serial_length} digits under the configured template format.",
            )
        )
    return evidence, findings


def _dob_checks(
    field_name: str, observed_value: str | None, spec: dict[str, Any]
) -> tuple[list[ValidationEvidence], list[ValidationFinding]]:
    """Validate the configured day-month-year representation and actual calendar date."""
    if field_name != "dob":
        return [
            _evidence("dob_year_representation", "NOT_APPLICABLE", "NOT_A_DOB_FIELD"),
            _evidence("dob_calendar", "NOT_APPLICABLE", "NOT_A_DOB_FIELD"),
        ], []

    if observed_value is None:
        return [
            _evidence("dob_year_representation", "UNKNOWN", "INSUFFICIENT_OBSERVATION"),
            _evidence("dob_calendar", "UNKNOWN", "INSUFFICIENT_OBSERVATION"),
        ], []

    components = spec.get("dob_components") or config.TEMPLATE["fields"]["dob"]["dob_components"]
    component_pattern = re.compile(
        rf"^(?P<day>{components['day_pattern']})(?P<separator_1>{components['separator_pattern']})"
        rf"(?P<month>{components['month_pattern']})(?P<separator_2>{components['separator_pattern']})"
        r"(?P<year>[0-9]{4})$"
    )
    year_pattern = re.compile(rf"^(?:{components['year_representation_pattern']})$")
    text = observed_value.strip()
    match = component_pattern.fullmatch(text)
    if match is None:
        return [
            _evidence(
                "dob_year_representation",
                "UNKNOWN",
                "DATE_COMPONENTS_INCOMPLETE",
                {"expected_order": "day, month, year", "expected_separator_characters": "space, slash, or hyphen"},
            ),
            _evidence(
                "dob_calendar",
                "UNKNOWN",
                "DATE_COMPONENTS_INCOMPLETE",
                {"expected_order": "day, month, year"},
            ),
        ], []

    day_text = match.group("day")
    month_text = match.group("month")
    year_text = match.group("year")
    year_representation_valid = year_pattern.fullmatch(year_text) is not None
    year_record = _evidence(
        "dob_year_representation",
        "PASS" if year_representation_valid else "FAIL",
        "YEAR_REPRESENTATION_VALID" if year_representation_valid else "YEAR_NOT_ALLOWED_BY_TEMPLATE",
        {
            "observed_year": int(year_text),
            "template_year_pattern": components["year_representation_description"],
        },
    )

    date_parts = {"day": int(day_text), "month": int(month_text), "year": int(year_text)}
    try:
        date(date_parts["year"], date_parts["month"], date_parts["day"])
    except ValueError as exc:
        calendar_record = _evidence(
            "dob_calendar",
            "FAIL",
            "INVALID_CALENDAR_DATE",
            {**date_parts, "calendar_error": type(exc).__name__},
        )
        finding = ValidationFinding(
            "CALENDAR_DATE_INVALID",
            "Observed DOB is not a valid calendar date "
            f"(day {date_parts['day']}, month {date_parts['month']}, year {date_parts['year']}).",
        )
        return [year_record, calendar_record], [finding]

    calendar_record = _evidence(
        "dob_calendar",
        "PASS",
        "CALENDAR_DATE_VALID",
        date_parts,
    )
    return [year_record, calendar_record], []


def _field_structure_record(
    field_name: str, records: Sequence[ValidationEvidence]
) -> ValidationEvidence:
    result = aggregate_validation_results(item.result for item in records)
    reason = {
        "PASS": "CURRENT_STRUCTURAL_CHECKS_PASSED",
        "FAIL": "STRUCTURAL_CHECK_FAILED",
        "UNKNOWN": "INSUFFICIENT_OBSERVATION",
        "NOT_APPLICABLE": "NO_APPLICABLE_STRUCTURAL_CHECKS",
    }[result]
    return _evidence(
        f"{field_name}_structure",
        result,
        reason,
        {
            "checks": [{"validator": item.validator, "result": item.result} for item in records],
            "not_proof_of_correctness": True,
        },
    )


def validate_field(field_name: str, observed_value: str | None, spec: dict[str, Any] | None = None) -> FieldValidation:
    """Return deterministic validation evidence using only an observation and template.

    ``observed_value`` is never compared with a case value, expected identity, or ground
    truth. ``None`` means no usable observation was available; an observed empty string is
    evaluated as empty and fails the existing minimum/pattern checks.
    """
    if field_name not in config.TEMPLATE["fields"]:
        raise ValueError(f"unsupported template field: {field_name!r}")
    field_spec = spec or config.TEMPLATE["fields"][field_name]
    records, findings = _base_checks(observed_value, field_spec)
    structure_records = list(records)

    if field_name == "id_number":
        component_records, component_findings = _id_checks(observed_value, field_spec)
        records.extend(component_records)
        structure_records.extend(component_records)
        findings.extend(component_findings)
    else:
        # ID-specific checks are explicitly out of scope for other field types.
        for validator_name in ("id_prefix", "id_length", "id_separator", "id_serial"):
            records.append(_evidence(validator_name, "NOT_APPLICABLE", "NOT_AN_ID_FIELD"))

    dob_records, dob_findings = _dob_checks(field_name, observed_value, field_spec)
    records.extend(dob_records)
    if field_name == "dob":
        structure_records.extend(dob_records)
        findings.extend(dob_findings)

    if field_name == "district":
        records.append(
            _evidence(
                "district_registry",
                "NOT_APPLICABLE",
                "NO_CONTROLLED_DISTRICT_REGISTRY",
                {"scope": "synthetic_id_v1 template structure only"},
            )
        )

    structure_record = _field_structure_record(field_name, structure_records)
    records.append(structure_record)

    # The pre-existing classifier already rejects failures of its generic shape checks.
    # The only new semantic veto is an impossible DOB calendar date.
    has_validation_failure = any(
        item.result == "FAIL"
        for item in structure_records
        if not item.validator.endswith("_structure")
    )
    if has_validation_failure and not any(item.reason_code == "VALIDATION_FAILED" for item in findings):
        findings.append(
            ValidationFinding("VALIDATION_FAILED", "One or more deterministic field validation checks failed.")
        )

    return FieldValidation(tuple(records), tuple(findings))


__all__ = [
    "FieldValidation",
    "ValidationEvidence",
    "ValidationFinding",
    "ValidationOutcome",
    "ValidatorResult",
    "aggregate_validation_results",
    "validate_field",
]
