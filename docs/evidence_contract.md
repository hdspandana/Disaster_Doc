# DisasterDoc evidence contract

**Evidence schema version:** `2`

**Status:** Phase 6 additive evidence-handling refinement. The classifier now preserves different usable OCR readings whose boxes overlap and abstains rather than selecting one by confidence alone. Phase 5 calendar validation remains unchanged; the three production statuses and their null-claim semantics remain unchanged.

This contract separates what the OCR system observed from what the classifier claimed, records each deterministic validator and its result, and makes uncertainty explicit. Validation is evidence about the observed string—not ground truth, identity verification, document authentication, or proof of correctness. The existing heuristic OCR confidence bucket is not a probability or an independently verified score.

## Field-result contract

Each serialized field result includes these canonical keys:

| Key | Meaning |
|---|---|
| `observed_value` | Field-level text view; `null` when there is no mapped text. It is an observation, not a claim. If overlapping readings conflict, this compatibility view may show the deterministic primary reading, while all alternatives remain separately recorded in `ocr_evidence` and conflict details; no value is claimed. |
| `claimed_value` | The value deterministic code elects to report. It is copied from usable OCR only when status is `RECOVERED`; otherwise it is `null`. |
| `status` | Exactly `RECOVERED`, `PARTIAL`, or `UNRECOVERABLE`. |
| `reason_codes` | Ordered stable identifiers for actual deterministic checks and decisions. |
| `reason_descriptions` | Fixed human-readable descriptions corresponding to `reason_codes`. |
| `validation_result` | Aggregate of applicable validation rows: `FAIL` if any applicable row fails; otherwise `UNKNOWN` if any applicable row is unknown; otherwise `PASS`; if every row is not applicable, `NOT_APPLICABLE`. Not-applicable rows never count as passes. |
| `validation_evidence` | Ordered rows with `validator`, `result`, stable `reason`, and supporting `details`. Each result is `PASS`, `FAIL`, `UNKNOWN`, or `NOT_APPLICABLE`. |
| `ocr_evidence` | Field-mapped OCR observations, including observation ID, observed text, confidence, original-image bbox/polygon, and whether the current usability gate accepted the observation. |
| `damage_evidence` | Typed local field/adjacent damage measurements and boxes. `evaluated=false` with `null` ratios means local ratios were not evaluated; it is not a measured zero. |

`None` passed to a validator means no usable observation was available and yields `UNKNOWN` where the check applies. A present but empty string is evaluated as empty and fails the existing minimum/pattern checks. A `PASS` means only that the named implemented checks passed.

The engine's raw score remains `ocr_confidence`; `confidence_bucket` remains a heuristic, not a correctness probability. No evidence score, recognition agreement, or semantic similarity score is added.

When two different, usable OCR observations have positive-area overlap between their original-image bounding boxes, the field records both in `ocr_evidence` and adds an `ocr_observation_consistency` row with result `UNKNOWN`. Its `details.conflicts` lists both observation IDs, verbatim text, boxes, and engine scores. The classifier may keep a deterministic primary reading in the compatibility `observed_value` view, but it reports no `claimed_value` and sets status `PARTIAL`; higher OCR confidence is not treated as independent corroboration. Case-only text differences are not conflicts. This is a geometric ambiguity rule, not a truth validator.

## Compatibility aliases

The schema v2 JSON is additive to the Phase 4 record:

- `value` remains the legacy alias for `claimed_value`.
- `raw_ocr_text` remains the legacy classifier text view; `observed_value` is the explicit field-level observation view.
- `evidence` remains the legacy list of mapped OCR summaries; `ocr_evidence` is the richer canonical record backed by `Observation`.
- `deterministic_reasons` retains the detailed human-readable strings; code-based reasons are primary for machine consumers.
- Legacy numeric `obscuration_in_zone` and `adjacent_obscuration` keys remain. If `damage_evidence.evaluated` is false, treat those legacy defaults as unavailable—not measured zero.
- Existing summary, verification-task, audit, processing, disclaimer, and raw-document OCR sections remain.

In Python, `FieldResult.value`, `raw_ocr_text`, and `validation_evidence` remain available. Legacy dataclass positional constructor fields were not reordered. New consumers should use `claimed_value`, `observed_value`, and `validation_result`.

## Existing shared validators

The existing template-specific checks are still run once and now live in `src/validation.py`:

1. minimum alphanumeric character count;
2. minimum whitespace-token count;
3. full match against the field's existing configured pattern (case-insensitive as before);
4. the existing trailing truncation-marker check.

These generic rows remain independently visible. Field structure summaries reuse their outcomes; they do not invent extra length, punctuation, confidence, or plausibility thresholds. Existing confidence and damage gates remain separate classifier evidence.

## Field-specific validation

### ID

The configured template pattern is `^[A-Z]{2}-[0-9]{5}$`: two letters, a hyphen, and five ASCII digits. The existing pattern check is case-insensitive; the component check makes its uppercase normalization explicit while preserving the original observed text. Phase 5 exposes prefix class, total length, separator, and serial checks. **There is no fixed `DX` prefix rule**: `QY-73156` (and its lowercase OCR form `qy-73156`) is structurally valid under the existing case-insensitive rule, and `DX-48281` passes structural validation even when an evaluation case says the source string was `DX-48291`. The validator cannot know which format-valid ID is the right one.

Malformed prefix class, length, separator, or serial character evidence may fail. `INVALID_PREFIX`, `INVALID_LENGTH`, and `INVALID_CHARACTER` describe those checks; no checksum, registry, or ground-truth comparison exists.

### DOB

The existing date representation is day, month, year with a space, slash, or hyphen separator and a year matching the configured `(19|20)[0-9]{2}` pattern. This is the existing template's 1900–2099 representation—not a newly imposed age rule. Phase 5 adds actual Gregorian calendar validation using the parsed components:

- `29 02 2020` → calendar `PASS`;
- `31 02 1991`, `31 04 1991`, and `29 02 2021` → calendar `FAIL`;
- incomplete/unparseable date components → calendar `UNKNOWN`.

A four-digit year outside the existing template representation can fail `dob_year_representation` and `field_pattern` while still being a real calendar year. No future-date, minimum-age, maximum-age, or other arbitrary bounds are imposed. Calendar validity does not mean the DOB is true.

A `dob_calendar=FAIL` is the only new validation failure that changes classification: a formerly `RECOVERED` field becomes `PARTIAL`, has `claimed_value=null`, and carries `CALENDAR_DATE_INVALID`. No status or threshold is added.

### Name

The current configured character pattern, minimum alphanumeric count, minimum token count, and truncation-marker check define `name_structure`. No punctuation-ratio heuristic, person database, external identity check, or truth inference is added. A plausible but wrong name such as `ANANYA RAJ` can pass; `PASS` means format-valid, not correct.

### District

The current template has no controlled district vocabulary or registry. `district_registry` is explicitly `NOT_APPLICABLE` with reason `NO_CONTROLLED_DISTRICT_REGISTRY`; no national or local gazetteer is introduced. Existing field pattern, character/token counts, and truncation checks define `district_structure`. Both `BENGALURU` and a structurally valid unlisted name such as `MYSURU` may pass.

### Address

The current configured address pattern, minimum alphanumeric count, minimum token count, and truncation-marker check define `address_structure`. No address-existence claim, geocoding, maps, government lookup, or external verification is performed. A structurally plausible wrong address may pass.

## Reason codes

Reason codes are stable strings emitted in deterministic check order. Phase 5 and Phase 6 additions are limited to implemented checks:

| Code | Meaning and trigger |
|---|---|
| `INVALID_PREFIX` | ID prefix does not contain the configured number of ASCII letters after the existing case-insensitive uppercase normalization. It does not mean a two-letter prefix other than `DX` is wrong. |
| `INVALID_LENGTH` | ID component/total length fails the configured structure. |
| `INVALID_CHARACTER` | ID separator or serial contains a character not allowed by the configured structure. |
| `CALENDAR_DATE_INVALID` | Parsed DOB components do not form a valid Gregorian calendar date. |
| `CONFLICTING_OCR_OBSERVATIONS` | Two usable OCR observations with different stripped/case-folded text overlap in original-image coordinates; both readings are preserved and neither is promoted to a claim. |

Phase 4 codes retained: `OCR_UNAVAILABLE`, `NO_FIELD_OBSERVATION`, `NO_USABLE_OBSERVATION`, `LOW_OCR_CONFIDENCE`, `INSUFFICIENT_TEXT`, `MINIMUM_CHARACTER_COUNT_FAILED`, `MINIMUM_TOKEN_COUNT_FAILED`, `FORMAT_INVALID`, `TRUNCATED_OBSERVATION`, `VALIDATION_FAILED`, `ADJACENT_OBSCURATION`, `HIGH_LOCAL_DAMAGE`, `INSUFFICIENT_EVIDENCE`, and `RECOVERY_GATES_PASSED`. Phase 6 adds `CONFLICTING_OCR_OBSERVATIONS` for unresolved overlapping alternatives.

`FORMAT_INVALID` remains the configured full-pattern failure. `VALIDATION_FAILED` indicates that one or more applicable deterministic validation rows failed. `CALENDAR_DATE_INVALID` is not a claim that the observation is false according to source truth; it says only that it is not a possible calendar date. No `RECOGNITION_DISAGREEMENT` code is emitted.

## Status and claim invariants

- `RECOVERED` may carry a non-null `claimed_value` copied from usable OCR after current classifier gates; it means OCR transcription only, not source-record correctness, identity verification, or authenticity.
- `PARTIAL` always has `claimed_value=null` and legacy `value=null`, even if `observed_value` contains text.
- `UNRECOVERABLE` always has `claimed_value=null` and legacy `value=null`.
- Serialization rejects a non-null claim for either non-recovered status.
- OCR observation and claim remain distinct keys even when the text is identical.
- Human verification is separate; it does not rewrite machine observations, claims, status, validation, damage evidence, or reason codes.

Example: a format-valid but possibly wrong ID remains a validation `PASS`; validation has no source truth:

```json
{
  "observed_value": "DX-48281",
  "claimed_value": "DX-48281",
  "status": "RECOVERED",
  "validation_result": "PASS",
  "validation_evidence": [
    {"validator": "field_pattern", "result": "PASS", "reason": "TEMPLATE_PATTERN_MATCHED", "details": {"pattern": "^[A-Z]{2}-[0-9]{5}$", "matched": true}},
    {"validator": "id_prefix", "result": "PASS", "reason": "PREFIX_CLASS_VALID", "details": {"observed_prefix": "DX", "normalized_prefix": "DX", "required_character_class": "uppercase ASCII letters after existing case-insensitive normalization", "required_length": 2, "fixed_prefix_required": false}}
  ]
}
```

Example: a calendar-invalid DOB fails deterministic validation and is not claimed:

```json
{
  "observed_value": "31 02 1991",
  "claimed_value": null,
  "status": "PARTIAL",
  "validation_result": "FAIL",
  "reason_codes": ["CALENDAR_DATE_INVALID", "VALIDATION_FAILED", "INSUFFICIENT_EVIDENCE"],
  "validation_evidence": [
    {"validator": "dob_year_representation", "result": "PASS", "reason": "YEAR_REPRESENTATION_VALID", "details": {"observed_year": 1991, "template_year_pattern": "1900 through 2099 (existing template pattern)"}},
    {"validator": "dob_calendar", "result": "FAIL", "reason": "INVALID_CALENDAR_DATE", "details": {"day": 31, "month": 2, "year": 1991}}
  ]
}
```

## Version metadata

Every production JSON evidence document includes:

- `evidence_schema_version`: `2` (Phase 5 added four-state validation evidence; Phase 6 uses the existing typed validation-row shape for OCR-conflict details, so no top-level schema shape changed);
- `metadata.code_version`: the existing application version (`disasterdoc-0.1.0`);
- `metadata.config_version`: `disasterdoc-config-v3` for the calendar validation and overlapping-reading abstention decision rules;
- `metadata.template_version`: `2`; Phase 5 field-specific validation metadata is now versioned, while field patterns, template geometry, and labels remain unchanged;
- `metadata.template`: current template name;
- `metadata.ocr_engine`: configured engine label when available.

Increment `config_version` for decision-relevant validation/config changes. Increment `template_version` when template geometry, labels, field definitions, or patterns change. No OCR package/model version is currently exposed by the production pipeline, so none is invented. The PDF displays the schema/config/template versions.

## Deterministic decision ownership and AI boundary

The classifier owns claims, status, validator results/evidence, and reason codes. Optional AI remains downstream. Its prompt includes the deterministic validation result/evidence and instructs it not to perform validation or invent results; if it discusses validation, it may only summarize supplied deterministic records. Output guardrails reject/log validation results, validators, validation reason codes, claims, status, reasons, and evidence by key. AI can populate only `ai_commentary`; it cannot change a validation result or classification.

## Ground-truth separation

`src.validation.validate_field` accepts only a field name, observed string, and template spec. It imports no evaluation harness and has no expected-value argument. Ground truth remains in `evaluation/` manifests/results and evaluation/reporting code; it is consulted only after validation/classification to label research outcomes. Production JSON/PDF decisions contain no ground-truth value.

## PDF and JSON presentation

JSON exposes `validation_result` and each validator's result, reason, and details. The PDF shows the aggregate result and each validator/reason, with existing XML escaping retained. PDF field rows continue to label **Observed**, **Claimed**, and **Status** separately. An abstention prints **Not claimed**, never the OCR fragment in the claim column. `RECOVERED` is described as an OCR transcription that passed current checks, not independent source-record verification.

## Phase 5 replay boundary

Phase 2's saved cases/results and Phase 3's case manifest/results/assets remain frozen historical artifacts. Phase 5 writes a separate `evaluation/phase_5_validation_replay.json`, replaying stored observations through the validator without OCR or pipeline reruns. Phase 6 reads those frozen artifacts and writes a separate versioned root-cause analysis; it does not rerun OCR, rewrite historical outputs, or feed evaluation labels into production validation.
