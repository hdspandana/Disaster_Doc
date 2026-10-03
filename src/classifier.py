"""
Deterministic field classification.

    OCR observations -> field mapping -> evidence quality checks -> status

Statuses:
    RECOVERED      usable evidence exists and passes the configured deterministic
                   structure/semantic checks, is not truncated, and the surrounding region
                   is readable. This is not independent verification or proof of correctness.
    PARTIAL        some usable evidence exists, but the complete value cannot safely
                   be reported (invalid structure/calendar date, fragment, truncation,
                   adjacent damage, or low confidence).
    UNRECOVERABLE  there is no usable evidence for the field.

Hard invariant enforced here and asserted in tests: when the status is PARTIAL or
UNRECOVERABLE, `claimed_value` (legacy alias `value`) is None. `observed_value` remains
separate and may contain OCR text. No AI layer may set either field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import config
from .damage import DamageMap, adjacent_obscuration
from .fields import MappedField
from .ocr import Observation
from .validation import (
    ValidationEvidence,
    ValidatorResult,
    aggregate_validation_results,
    validate_field,
)


class ReasonCode(str, Enum):
    """Stable deterministic explanations for the current classifier checks."""

    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    NO_FIELD_OBSERVATION = "NO_FIELD_OBSERVATION"
    NO_USABLE_OBSERVATION = "NO_USABLE_OBSERVATION"
    LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
    INSUFFICIENT_TEXT = "INSUFFICIENT_TEXT"
    MINIMUM_CHARACTER_COUNT_FAILED = "MINIMUM_CHARACTER_COUNT_FAILED"
    MINIMUM_TOKEN_COUNT_FAILED = "MINIMUM_TOKEN_COUNT_FAILED"
    FORMAT_INVALID = "FORMAT_INVALID"
    INVALID_PREFIX = "INVALID_PREFIX"
    INVALID_LENGTH = "INVALID_LENGTH"
    INVALID_CHARACTER = "INVALID_CHARACTER"
    CALENDAR_DATE_INVALID = "CALENDAR_DATE_INVALID"
    TRUNCATED_OBSERVATION = "TRUNCATED_OBSERVATION"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ADJACENT_OBSCURATION = "ADJACENT_OBSCURATION"
    HIGH_LOCAL_DAMAGE = "HIGH_LOCAL_DAMAGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_OCR_OBSERVATIONS = "CONFLICTING_OCR_OBSERVATIONS"
    RECOVERY_GATES_PASSED = "RECOVERY_GATES_PASSED"


REASON_CODE_DESCRIPTIONS: dict[ReasonCode, str] = {
    ReasonCode.OCR_UNAVAILABLE: "OCR did not produce usable evidence for this field.",
    ReasonCode.NO_FIELD_OBSERVATION: "No OCR observation was mapped to the field value region.",
    ReasonCode.NO_USABLE_OBSERVATION: "No single OCR observation met both configured usability checks.",
    ReasonCode.LOW_OCR_CONFIDENCE: "The OCR engine score was below a configured heuristic threshold; it is not a correctness probability.",
    ReasonCode.INSUFFICIENT_TEXT: "The OCR observations did not contain enough text to qualify as usable evidence.",
    ReasonCode.MINIMUM_CHARACTER_COUNT_FAILED: "Observed text did not meet the template's minimum character count.",
    ReasonCode.MINIMUM_TOKEN_COUNT_FAILED: "Observed text did not meet the template's minimum token count.",
    ReasonCode.FORMAT_INVALID: "Observed text did not match the configured field pattern.",
    ReasonCode.INVALID_PREFIX: "The ID prefix does not satisfy the template's two-letter structure after case-insensitive normalization; no specific prefix is treated as truth.",
    ReasonCode.INVALID_LENGTH: "The ID does not satisfy the template's fixed component length.",
    ReasonCode.INVALID_CHARACTER: "The ID contains a character not allowed by the template's component structure.",
    ReasonCode.CALENDAR_DATE_INVALID: "The observed DOB components do not form a valid calendar date.",
    ReasonCode.TRUNCATED_OBSERVATION: "Observed text ended with a configured truncation marker.",
    ReasonCode.VALIDATION_FAILED: "One or more deterministic field validation checks failed.",
    ReasonCode.ADJACENT_OBSCURATION: "The adjacent damage check found obscuration immediately after the observation.",
    ReasonCode.HIGH_LOCAL_DAMAGE: "The measured field-region damage exceeded its configured heuristic limit.",
    ReasonCode.INSUFFICIENT_EVIDENCE: "The current deterministic evidence does not support reporting a complete value.",
    ReasonCode.CONFLICTING_OCR_OBSERVATIONS: "Different usable OCR readings overlap the same image region; confidence ordering alone cannot establish which reading is correct.",
    ReasonCode.RECOVERY_GATES_PASSED: "Current deterministic recovery checks passed; this is not independent verification or proof of correctness.",
}


@dataclass(frozen=True)
class DamageEvidence:
    """Typed view of the classifier's existing local damage measurements."""

    evaluated: bool
    field_region_bbox: list[int] | None
    observation_bbox: list[int] | None
    adjacent_probe_bbox: list[int] | None
    obscuration_ratio: float | None
    adjacent_obscuration_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "field_region_bbox": self.field_region_bbox,
            "observation_bbox": self.observation_bbox,
            "adjacent_probe_bbox": self.adjacent_probe_bbox,
            "obscuration_ratio": (
                round(float(self.obscuration_ratio), 4) if self.obscuration_ratio is not None else None
            ),
            "adjacent_obscuration_ratio": (
                round(float(self.adjacent_obscuration_ratio), 4)
                if self.adjacent_obscuration_ratio is not None
                else None
            ),
        }


@dataclass
class FieldResult:
    """Deterministic field decision with distinct observation and claim contracts.

    ``value`` and ``raw_ocr_text`` remain as compatibility attributes. New consumers
    should use ``claimed_value`` and ``observed_value`` respectively.
    """

    field_name: str
    label: str
    expected_type: str
    status: str
    value: str | None
    raw_ocr_text: str
    confidence_bucket: str
    ocr_confidence: float | None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    needs_verification: bool = False
    verification_instruction: str = ""
    evidence_bbox: list[int] | None = None
    field_region_bbox: list[int] | None = None  # template region that was examined (not evidence)
    damage_bbox: list[int] | None = None  # probe area that showed continuing damage
    obscuration_in_zone: float = 0.0
    adjacent_obscuration: float = 0.0
    # Filled only by the AI commentary layer, and only for PARTIAL/UNRECOVERABLE fields.
    # It is never read when computing status or value.
    ai_commentary: dict[str, Any] | None = None
    # New typed-contract fields follow the legacy positional constructor fields.
    observed_value: str | None = None
    ocr_observations: list[Observation] = field(default_factory=list)
    validation_evidence: list[ValidationEvidence] = field(default_factory=list)
    reason_codes: list[ReasonCode] = field(default_factory=list)
    damage_assessed: bool = False

    def __post_init__(self) -> None:
        if self.observed_value is None and self.raw_ocr_text:
            # Backward-compatible construction from callers that still pass raw_ocr_text.
            self.observed_value = self.raw_ocr_text
        self._check_claim_invariant()

    @property
    def claimed_value(self) -> str | None:
        """The value the deterministic classifier elects to report (legacy alias: value)."""
        return self.value

    @claimed_value.setter
    def claimed_value(self, value: str | None) -> None:
        self.value = value

    @property
    def damage_evidence(self) -> DamageEvidence:
        """Return measured damage only when the classifier actually evaluated it."""
        evaluated = self.damage_assessed
        return DamageEvidence(
            evaluated=evaluated,
            field_region_bbox=self.field_region_bbox,
            observation_bbox=self.evidence_bbox,
            adjacent_probe_bbox=self.damage_bbox,
            obscuration_ratio=self.obscuration_in_zone if evaluated else None,
            adjacent_obscuration_ratio=self.adjacent_obscuration if evaluated else None,
        )

    @property
    def validation_result(self) -> ValidatorResult:
        """Aggregate current field validation; PASS is not evidence of correctness."""
        return aggregate_validation_results([item.result for item in self.validation_evidence])

    def _check_claim_invariant(self) -> None:
        if self.status in (config.STATUS_PARTIAL, config.STATUS_UNRECOVERABLE) and self.claimed_value is not None:
            raise ValueError(f"{self.status} field {self.field_name!r} cannot carry a claimed value")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, additive contract while retaining legacy keys."""
        self._check_claim_invariant()
        usable_by_id = {item.get("observation_id"): bool(item.get("usable", True)) for item in self.evidence}
        reason_codes = [code.value if isinstance(code, ReasonCode) else str(code) for code in self.reason_codes]
        reason_descriptions = [
            REASON_CODE_DESCRIPTIONS.get(code, "") if isinstance(code, ReasonCode) else ""
            for code in self.reason_codes
        ]
        return {
            "field_name": self.field_name,
            "label": self.label,
            "expected_type": self.expected_type,
            "observed_value": self.observed_value,
            "claimed_value": self.claimed_value,
            "status": self.status,
            "reason_codes": reason_codes,
            "reason_descriptions": reason_descriptions,
            "ocr_evidence": [
                {
                    **observation.to_dict(),
                    "observed_text": observation.text,
                    "usable": usable_by_id.get(observation.observation_id, True),
                }
                for observation in self.ocr_observations
            ],
            "damage_evidence": self.damage_evidence.to_dict(),
            "validation_result": self.validation_result,
            "validation_evidence": [item.to_dict() for item in self.validation_evidence],
            # Legacy aliases retained for JSON/report/evaluation consumers through schema v1.
            "value": self.value,
            "raw_ocr_text": self.raw_ocr_text,
            "confidence_bucket": self.confidence_bucket,
            "ocr_confidence": None if self.ocr_confidence is None else round(float(self.ocr_confidence), 4),
            "evidence": self.evidence,
            "deterministic_reasons": self.reasons,
            "field_region_bbox": self.field_region_bbox,
            "obscuration_in_zone": round(float(self.obscuration_in_zone), 4),
            "adjacent_obscuration": round(float(self.adjacent_obscuration), 4),
            "needs_verification": self.needs_verification,
            "verification_instruction": self.verification_instruction,
            "ai_commentary": self.ai_commentary,
        }


def usable_observations(observations: list[Observation]) -> list[Observation]:
    """Observations good enough to count as evidence at all."""
    return [
        o
        for o in observations
        if o.confidence >= config.MIN_USABLE_OCR_CONF and o.alnum_count >= config.MIN_USABLE_CHARS
    ]


def _bucket(status: str, confidence: float, has_evidence: bool) -> str:
    """Heuristic evidence-confidence bucket (NOT a probability, NOT statistically validated)."""
    if not has_evidence:
        return "none"
    if status == config.STATUS_RECOVERED:
        return "high" if confidence >= config.HIGH_CONF else "medium"
    return "medium" if confidence >= config.LOW_CONF else "low"


def _record_conflicting_ocr_evidence(result: FieldResult, mapped: MappedField) -> str | None:
    """Record overlapping alternative readings as unresolved, never as corroboration."""
    if not mapped.conflicting_observation_pairs:
        return None

    conflicts = [
        {
            "observation_ids": [first.observation_id, second.observation_id],
            "observed_texts": [first.text, second.text],
            "bboxes": [first.bbox, second.bbox],
            "ocr_confidences": [round(float(first.confidence), 4), round(float(second.confidence), 4)],
        }
        for first, second in mapped.conflicting_observation_pairs
    ]
    result.validation_evidence.append(
        ValidationEvidence(
            validator="ocr_observation_consistency",
            result="UNKNOWN",
            reason=ReasonCode.CONFLICTING_OCR_OBSERVATIONS.value,
            details={
                "policy": (
                    "Different stripped, case-folded text from two usable observations with positive-area "
                    "bbox overlap is unresolved; confidence ordering does not select a claim."
                ),
                "conflicts": conflicts,
            },
        )
    )
    result.reason_codes.append(ReasonCode.CONFLICTING_OCR_OBSERVATIONS)
    return (
        "Overlapping OCR boxes contain different usable readings. Both observations are retained, "
        "but this evidence cannot establish which reading is correct."
    )


def classify_field(
    mapped: MappedField,
    spec: dict,
    damage: DamageMap,
    shape: tuple[int, int],
    ocr_available: bool = True,
) -> FieldResult:
    """Apply the transparent rules to one mapped field."""
    conflict_observations = [
        observation
        for pair in mapped.conflicting_observation_pairs
        for observation in pair
    ]
    field_observations: list[Observation] = []
    seen_ids: set[str] = set()
    for observation in [*mapped.observations, *conflict_observations]:
        if observation.observation_id not in seen_ids:
            field_observations.append(observation)
            seen_ids.add(observation.observation_id)

    result = FieldResult(
        field_name=mapped.key,
        label=spec["label"],
        expected_type=spec["expected_type"],
        status=config.STATUS_UNRECOVERABLE,
        value=None,
        raw_ocr_text=mapped.raw_text,
        confidence_bucket="none",
        ocr_confidence=None,
        observed_value=mapped.raw_text or None,
        ocr_observations=field_observations,
        verification_instruction=spec["verification_instruction"],
    )

    result.field_region_bbox = [mapped.value_zone[0], mapped.row_band[0], mapped.value_zone[1], mapped.row_band[1]]

    usable = usable_observations(mapped.observations)
    usable_ids = {o.observation_id for o in usable_observations(field_observations)}
    result.evidence = [
        {
            "observation_id": o.observation_id,
            "bbox": o.bbox,
            "ocr_confidence": round(float(o.confidence), 4),
            "text": o.text,
            "usable": o.observation_id in usable_ids,
        }
        for o in field_observations
    ]

    if mapped.observations:
        result.ocr_confidence = max(o.confidence for o in field_observations)
        result.evidence_bbox = mapped.bbox

    # ---- Step 1: is there any usable evidence at all? ---------------------------------
    if not usable:
        result.status = config.STATUS_UNRECOVERABLE
        result.needs_verification = True
        validation = validate_field(mapped.key, None, spec)
        result.validation_evidence = list(validation.evidence)
        conflict_message = _record_conflicting_ocr_evidence(result, mapped)
        if conflict_message:
            result.reasons.append(conflict_message)
        if not ocr_available:
            result.reason_codes.extend((ReasonCode.OCR_UNAVAILABLE, ReasonCode.INSUFFICIENT_EVIDENCE))
            result.reasons.append("Unable to obtain sufficient OCR evidence from this document.")
        elif not mapped.observations:
            result.reason_codes.extend((ReasonCode.NO_FIELD_OBSERVATION, ReasonCode.INSUFFICIENT_EVIDENCE))
            result.reasons.append(
                "No OCR observation overlaps this field's value region: the area is unreadable "
                "or the column could not be located."
            )
        else:
            has_confident_observation = any(o.confidence >= config.MIN_USABLE_OCR_CONF for o in mapped.observations)
            has_sufficient_text = any(o.alnum_count >= config.MIN_USABLE_CHARS for o in mapped.observations)
            if not has_confident_observation:
                result.reason_codes.append(ReasonCode.LOW_OCR_CONFIDENCE)
            if not has_sufficient_text:
                result.reason_codes.append(ReasonCode.INSUFFICIENT_TEXT)
            if has_confident_observation and has_sufficient_text:
                result.reason_codes.append(ReasonCode.NO_USABLE_OBSERVATION)
            result.reason_codes.append(ReasonCode.INSUFFICIENT_EVIDENCE)
            result.reasons.append(
                "Only observations below the usable-evidence threshold were found "
                f"(best OCR confidence {mapped.ocr_confidence:.2f}, minimum {config.MIN_USABLE_OCR_CONF:.2f})."
            )
        result.confidence_bucket = _bucket(result.status, mapped.ocr_confidence, bool(mapped.observations))
        return result

    # ---- Step 2: evidence quality checks ---------------------------------------------
    raw = " ".join(o.text for o in usable).strip()
    result.raw_ocr_text = raw
    confidence = max(o.confidence for o in usable)
    result.ocr_confidence = confidence
    if mapped.bbox:
        x0 = min(o.bbox[0] for o in usable)
        y0 = min(o.bbox[1] for o in usable)
        x1 = max(o.bbox[2] for o in usable)
        y1 = max(o.bbox[3] for o in usable)
        result.evidence_bbox = [int(x0), int(y0), int(x1), int(y1)]

    band_y0, band_y1 = mapped.row_band
    zone_x0, zone_x1 = mapped.value_zone
    result.obscuration_in_zone = damage.ratio([zone_x0, band_y0, zone_x1, band_y1])
    adj_ratio, probe_bbox, adj_run = adjacent_obscuration(
        damage, result.evidence_bbox or [zone_x0, band_y0, zone_x1, band_y1], shape, mapped.value_zone
    )
    result.adjacent_obscuration = adj_ratio
    result.damage_bbox = probe_bbox
    result.damage_assessed = True

    validation = validate_field(mapped.key, raw, spec)
    result.validation_evidence = list(validation.evidence)
    result.reason_codes.extend(ReasonCode(code) for code in validation.reason_codes)
    problems = list(validation.messages)
    conflict_message = _record_conflicting_ocr_evidence(result, mapped)
    if conflict_message:
        problems.append(conflict_message)

    if adj_ratio >= config.ADJACENT_OBSCURATION_LIMIT and adj_run >= 6:
        result.reason_codes.append(ReasonCode.ADJACENT_OBSCURATION)
        problems.append(
            f"Damage begins immediately after the observed text (obscured {adj_ratio * 100:.0f}% of the "
            f"next {int(probe_bbox[2] - probe_bbox[0])} px, unbroken run {adj_run} px), so the printed "
            "value may continue into an unreadable area."
        )
    if result.obscuration_in_zone > config.OBSCURED_RATIO_LIMIT:
        result.reason_codes.append(ReasonCode.HIGH_LOCAL_DAMAGE)
        problems.append(
            f"{result.obscuration_in_zone * 100:.0f}% of this field's value region is obscured "
            f"(limit {config.OBSCURED_RATIO_LIMIT * 100:.0f}%)."
        )
    if confidence < config.LOW_CONF:
        result.reason_codes.append(ReasonCode.LOW_OCR_CONFIDENCE)
        problems.append(
            f"OCR confidence for the surviving text is low ({confidence:.2f}); the glyphs themselves "
            "are not reliable evidence."
        )

    # ---- Step 3: verdict ---------------------------------------------------------------
    if not problems:
        result.status = config.STATUS_RECOVERED
        result.value = raw.strip()  # evidence itself, never a guess
        result.needs_verification = False
        result.reason_codes.append(ReasonCode.RECOVERY_GATES_PASSED)
        result.reasons.append(
            "Usable OCR evidence satisfies this field's expected pattern, is not truncated, and the "
            "surrounding region is readable."
        )
    else:
        result.status = config.STATUS_PARTIAL
        result.value = None  # a partial reading is never promoted to a recovered value
        result.needs_verification = True
        result.reasons.extend(problems)
        result.reasons.append(
            "The complete value cannot be established from this document, so no value is reported."
        )
        result.reason_codes.append(ReasonCode.INSUFFICIENT_EVIDENCE)

    result.confidence_bucket = _bucket(result.status, confidence, True)
    return result


def classify_all(
    mapped_fields: dict[str, MappedField],
    damage: DamageMap,
    shape: tuple[int, int],
    ocr_available: bool = True,
) -> list[FieldResult]:
    """Classify every field in template order."""
    results: list[FieldResult] = []
    for key in config.FIELD_ORDER:
        spec = config.TEMPLATE["fields"][key]
        results.append(classify_field(mapped_fields[key], spec, damage, shape, ocr_available))
    return results


__all__ = [
    "REASON_CODE_DESCRIPTIONS",
    "DamageEvidence",
    "FieldResult",
    "ReasonCode",
    "ValidationEvidence",
    "classify_all",
    "classify_field",
    "usable_observations",
]
