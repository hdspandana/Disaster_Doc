"""
Deterministic field classification.

    OCR observations -> field mapping -> evidence quality checks -> status

Statuses:
    RECOVERED      usable evidence exists, the value satisfies the template pattern,
                   it is not truncated and the surrounding region is readable.
    PARTIAL        some usable evidence exists, but the complete value cannot safely
                   be established (fragment, truncation, adjacent damage, low confidence).
    UNRECOVERABLE  there is no usable evidence for the field.

Hard invariant enforced here and asserted in tests: when the status is PARTIAL or
UNRECOVERABLE, `value` is None. No layer - including the AI layer - may set it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .damage import DamageMap, adjacent_obscuration
from .fields import MappedField
from .ocr import Observation


@dataclass
class FieldResult:
    """The deterministic verdict for one field, with the evidence behind it."""

    field_name: str
    label: str
    expected_type: str
    status: str
    value: str | None
    raw_ocr_text: str
    confidence_bucket: str
    ocr_confidence: float | None
    evidence: list[dict] = field(default_factory=list)
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
    ai_commentary: dict | None = None

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "label": self.label,
            "expected_type": self.expected_type,
            "value": self.value,
            "raw_ocr_text": self.raw_ocr_text,
            "status": self.status,
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


def _pattern_match(text: str, pattern: str) -> bool:
    return re.fullmatch(pattern, text.strip().upper()) is not None


def _ends_with_truncation_marker(text: str) -> str | None:
    stripped = text.strip()
    if stripped and stripped[-1] in config.TRUNCATION_MARKERS:
        return stripped[-1]
    return None


def _bucket(status: str, confidence: float, has_evidence: bool) -> str:
    """Heuristic evidence-confidence bucket (NOT a probability, NOT statistically validated)."""
    if not has_evidence:
        return "none"
    if status == config.STATUS_RECOVERED:
        return "high" if confidence >= config.HIGH_CONF else "medium"
    return "medium" if confidence >= config.LOW_CONF else "low"


def classify_field(
    mapped: MappedField,
    spec: dict,
    damage: DamageMap,
    shape: tuple[int, int],
    ocr_available: bool = True,
) -> FieldResult:
    """Apply the transparent rules to one mapped field."""
    result = FieldResult(
        field_name=mapped.key,
        label=spec["label"],
        expected_type=spec["expected_type"],
        status=config.STATUS_UNRECOVERABLE,
        value=None,
        raw_ocr_text=mapped.raw_text,
        confidence_bucket="none",
        ocr_confidence=None,
        verification_instruction=spec["verification_instruction"],
    )

    result.field_region_bbox = [mapped.value_zone[0], mapped.row_band[0], mapped.value_zone[1], mapped.row_band[1]]

    usable = usable_observations(mapped.observations)
    result.evidence = [
        {
            "observation_id": o.observation_id,
            "bbox": o.bbox,
            "ocr_confidence": round(float(o.confidence), 4),
            "text": o.text,
            "usable": o in usable,
        }
        for o in mapped.observations
    ]

    if mapped.observations:
        result.ocr_confidence = max(o.confidence for o in mapped.observations)
        result.evidence_bbox = mapped.bbox

    # ---- Step 1: is there any usable evidence at all? ---------------------------------
    if not usable:
        result.status = config.STATUS_UNRECOVERABLE
        result.needs_verification = True
        if not ocr_available:
            result.reasons.append("Unable to obtain sufficient OCR evidence from this document.")
        elif not mapped.observations:
            result.reasons.append(
                "No OCR observation overlaps this field's value region: the area is unreadable "
                "or the column could not be located."
            )
        else:
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

    alnum = sum(ch.isalnum() for ch in raw)
    tokens = [t for t in re.split(r"\s+", raw) if t]
    pattern_ok = _pattern_match(raw, spec["pattern"])
    marker = _ends_with_truncation_marker(raw)

    problems: list[str] = []
    if alnum < spec["min_chars"]:
        problems.append(
            f"Observed text is shorter than the template expects for this field "
            f"({alnum} of at least {spec['min_chars']} characters)."
        )
    if len(tokens) < spec["min_tokens"]:
        problems.append(
            f"Observed text has fewer parts than the template expects "
            f"({len(tokens)} of at least {spec['min_tokens']})."
        )
    if not pattern_ok:
        problems.append(
            f"Observed text does not satisfy the expected pattern for this field ({spec['pattern']})."
        )
    if marker:
        problems.append(
            f"Observed text ends with the truncation marker '{marker}', so the printed value "
            "probably continued beyond what is legible."
        )
    if adj_ratio >= config.ADJACENT_OBSCURATION_LIMIT and adj_run >= 6:
        problems.append(
            f"Damage begins immediately after the observed text (obscured {adj_ratio * 100:.0f}% of the "
            f"next {int(probe_bbox[2] - probe_bbox[0])} px, unbroken run {adj_run} px), so the printed "
            "value may continue into an unreadable area."
        )
    if result.obscuration_in_zone > config.OBSCURED_RATIO_LIMIT:
        problems.append(
            f"{result.obscuration_in_zone * 100:.0f}% of this field's value region is obscured "
            f"(limit {config.OBSCURED_RATIO_LIMIT * 100:.0f}%)."
        )
    if confidence < config.LOW_CONF:
        problems.append(
            f"OCR confidence for the surviving text is low ({confidence:.2f}); the glyphs themselves "
            "are not reliable evidence."
        )

    # ---- Step 3: verdict ---------------------------------------------------------------
    if not problems:
        result.status = config.STATUS_RECOVERED
        result.value = raw.strip()  # evidence itself, never a guess
        result.needs_verification = False
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


__all__ = ["FieldResult", "classify_field", "classify_all", "usable_observations"]
