# Phase 5 report — deterministic field validation

**Decision:** Phase 5 implementation and evaluation complete; stop for review. Do not commit or begin Phase 6 before review.

## 1. Objective and scope

Strengthen field-specific deterministic checks for ID, date of birth (DOB), name, district, and address. Expose validation results as `PASS`, `FAIL`, `UNKNOWN`, or `NOT_APPLICABLE`, with typed validator/reason/details records and an aggregate field result. Distinguish an invalid structure or impossible calendar date from an observation that merely looks plausible but has not been verified.

Validation is evidence about observed OCR text under the current synthetic template. It is not source truth, identity verification, authenticity, or proof that a value is correct. A `PASS` is not sufficient to recover a field on its own. Existing `RECOVERED`, `PARTIAL`, and `UNRECOVERABLE` statuses and the null-claim invariant for non-recovered fields remain unchanged.

No new OCR pass, external service, identity database, geocoder, web search, semantic similarity, LLM validation, threshold tuning, or benchmark was added. Phase 2/3 inputs and outputs remain historical; their saved observations were replayed offline into a separate Phase 5 analysis file.

## 2. Implementation

### Typed results and aggregate semantics

`src/validation.py` now owns the shared existing checks and field-specific validators. Every applicable result is one of:

- `PASS`: the named deterministic check passed for the observation;
- `FAIL`: the observation violates that check;
- `UNKNOWN`: there is not enough usable information to evaluate the check;
- `NOT_APPLICABLE`: the check does not apply, or the template supplies no basis for it.

Aggregate result precedence is: any applicable `FAIL` → `FAIL`; otherwise any applicable `UNKNOWN` → `UNKNOWN`; otherwise `PASS`; if every row is `NOT_APPLICABLE`, aggregate `NOT_APPLICABLE`. Not-applicable rows never count as passes. `None` means no usable observation (`UNKNOWN`); an observed empty string is evaluated and fails the applicable existing shape checks.

Each `validation_evidence` row contains the validator name, four-state result, stable reason, and typed details. A field-level `validation_result` is serialized into JSON and summarized in the PDF. AI commentary receives already-computed deterministic evidence but cannot create or modify validator outcomes, details, validation reason codes, claims, or status.

The evidence schema is version 2. The configuration version is `disasterdoc-config-v2`; the template version is `2` because field-specific validation definitions are now versioned. Existing template patterns, geometry, and labels were not changed.

### Field rules and limits

| Field | Deterministic checks | What `PASS` does not establish |
|---|---|---|
| **ID** | Reuses the configured pattern and checks its components: two letters, hyphen, five ASCII digits. It preserves the existing case-insensitive behavior and records uppercase normalization explicitly. The prefix class and length are template-configurable; there is no fixed `DX` prefix rule. | Does not detect a format-valid wrong ID, substitution, transposition, or mismatch to a person/document. No checksum, registry, or ground-truth comparison. |
| **DOB** | Reuses the existing template representation (day/month/year; supported separator; year representation 1900–2099) and checks the parsed date with Gregorian calendar rules, including leap years. The existing year representation is now explicit in template configuration; no age, future-date, minimum-year, or maximum-age rule was added. | A real calendar date is not evidence that it is the person's actual DOB. A valid-calendar but wrong DOB can still pass. |
| **Name** | Reuses the existing full-pattern, minimum alphanumeric count, minimum token count, and truncation-marker checks. No punctuation heuristic or external identity matching. | Does not establish spelling, identity, or truth; a plausible but wrong name may pass. |
| **District** | Reuses existing field pattern, character/token counts, and truncation check. `district_registry` is `NOT_APPLICABLE` (`NO_CONTROLLED_DISTRICT_REGISTRY`) because this template has no controlled vocabulary or registry. | Does not prove that a district exists or is correct; no gazetteer or external lookup is used. |
| **Address** | Reuses existing field pattern, character/token counts, and truncation check. No new address plausibility rule. | Does not establish deliverability, existence, or correctness; no geocoding or external verification is used. |

The common minimum-character, minimum-token, full-pattern, and truncation checks are still run once and reused by the field summaries. Phase 5 does not add unsupported punctuation, confidence, or plausibility thresholds.

### Deterministic reason codes and classifier integration

New reason codes are limited to checks implemented in Phase 5:

- `INVALID_PREFIX`
- `INVALID_LENGTH`
- `INVALID_CHARACTER`
- `CALENDAR_DATE_INVALID`

Existing Phase 4 reason codes remain. A format-valid two-letter prefix other than `DX` is not invalid. `CALENDAR_DATE_INVALID` means the parsed components do not form a Gregorian date; it is not a source-truth judgment.

The existing classifier already consumed deterministic format gates. Phase 5 minimally makes the newly explicit DOB calendar failure a recovery-gate failure: a formerly recovered impossible DOB becomes `PARTIAL`, has no claimed value, and carries `CALENDAR_DATE_INVALID`. ID component failures are already caught by the same configured full-pattern gate; the component records explain the structural failure without an independent, stricter classifier rule. No validation `PASS` recovers a field by itself, and no new status or threshold was introduced.

## 3. Separate Phase 2/3 replay

The replay in `tools/replay_phase5_validation.py` processed **73 saved observations** (33 Phase 2, 40 Phase 3) through `validate_field`. It did not run OCR or rewrite historical case/results/asset files. Evaluation ground truth was used only by the offline replay to label outcomes after validation/classification; production validation accepts observed text and the permitted template specification only.

`evaluation/phase_5_validation_replay.json` is the new, separate analysis artifact. It records input hashes, per-case validation counts, validator-row counts, before/after outcomes, changed case IDs, and remaining false recoveries.

### Aggregate validation counts by case

| Cohort | Cases | Before: PASS / FAIL / UNKNOWN / N/A | After: PASS / FAIL / UNKNOWN / N/A |
|---|---:|---:|---:|
| Phase 2 | 33 | 24 / 9 / 0 / 0 | 23 / 10 / 0 / 0 |
| Phase 3 | 40 | 24 / 7 / 9 / 0 | 23 / 8 / 9 / 0 |
| **Combined** | **73** | **48 / 16 / 9 / 0** | **46 / 18 / 9 / 0** |

These are per-case aggregate results, not the number of validator rows. In the Phase 5 replay, the **810** individual validator-evidence rows total **397 PASS / 57 FAIL / 71 UNKNOWN / 285 NOT_APPLICABLE**; rows include multiple checks per field, so their totals are not comparable to the 73 case aggregates.

### Before/after classification outcomes

| Cohort | Outcome | Before | After | Change |
|---|---|---:|---:|---:|
| Phase 2 | Correct abstention | 12 | 13 | +1 |
|  | Correct recovery | 5 | 5 | 0 |
|  | False recovery | 16 | 15 | −1 |
|  | Incorrect rejection | 0 | 0 | 0 |
| Phase 3 | Correct abstention | 18 | 19 | +1 |
|  | Correct recovery | 10 | 10 | 0 |
|  | False recovery | 11 | 10 | −1 |
|  | Incorrect rejection | 1 | 1 | 0 |
| **Combined** | **Correct abstention** | **30** | **32** | **+2** |
|  | **Correct recovery** | **15** | **15** | **0** |
|  | **False recovery** | **27** | **25** | **−2** |
|  | **Incorrect rejection** | **1** | **1** | **0** |

Only one case in each cohort changes classification:

- Phase 2: `dob_impossible_calendar_date` is no longer recovered.
- Phase 3: `source_05_mild` is no longer recovered.

No new incorrect rejection was introduced. The existing Phase 3 incorrect rejection, `source_08_mild`, remains. Correct recoveries are unchanged. The replay is a bounded deterministic re-analysis of saved observations, not a new accuracy estimate or larger benchmark.

### False recoveries that remain

Phase 2 still has **15** false recoveries: `id_single_digit_substitution_conf_060_clean`, `id_single_digit_substitution_conf_080_clean`, `id_single_digit_substitution_conf_094_clean`, `id_single_digit_substitution_conf_099_clean`, `id_single_digit_substitution_damage_moderate`, `id_multiple_digit_substitution`, `id_transposition`, `id_valid_format_wrong_value`, `dob_valid_format_wrong_value`, `name_ocr_confusion_O_to_Q`, `name_plausible_wrong_value`, `district_plausible_wrong_value`, `address_ocr_confusion_S_to_5`, `address_ocr_confusion_I_to_1`, and `address_plausible_wrong_value`.

Phase 3 still has **10** false recoveries: `source_01_mild`, `source_01_medium`, `source_02_mild`, `source_02_medium`, `source_03_mild`, `source_03_medium`, `source_04_mild`, `source_06_mild`, `source_06_medium`, and `source_07_mild`.

These are expected limits of structural validation: format-valid wrong IDs, plausible wrong names/districts/addresses, and a possible but wrong DOB remain capable of passing. Phase 5 does not claim to solve those errors.

### Historical-artifact integrity

Tests and the replay verified these Phase 2/3 input hashes without modifying the files:

- `evaluation/false_recovery_cases.json`: `e1f1d2196342ab4427205d23f7a26afd08acd29a0401370960bfb5261dd30681`
- `evaluation/false_recovery_results.json`: `6fdd0caf13b768da21bd77cdb02eaff89842edaff8b7d59b0314d152d7d0a4ea`
- `evaluation/image_level_cases.json`: `990f3a7cce65220ccf402098fb2f34bdc4741926fbdb5d136fa6f79138fd7f77`
- `evaluation/image_level_results.json`: `211a0b0666927c879f26652807f408ffa73efac43ae7f6b4c2e26413b536bc2f`
- Phase 3 image asset tree: **80 files**, tree SHA-256 `34d66fa9cf81165fc7a6be4ac4389e832969f919667735d66d2998999161764a`

## 4. Validation, security, and compatibility checks

- Full test suite: **98 passed, 1 strict XFAIL, 20 warnings** (`pytest -q`, 205.37 s). The false-recovery XFAIL is retained; the warnings are existing CPU/PyTorch/EasyOCR deprecation or accelerator notices.
- Security suite: **14 passed** (`pytest -q tests/test_security.py`).
- Focused validation/evidence/replay tests: **29 passed**.
- AppTest and pipeline flows passed as part of the full suite.
- Focused Ruff on Phase 5-touched production, tool, and test files: **PASS**.
- Repository-wide Ruff: **16 findings remain** in modules/tools untouched by Phase 5; no unrelated lint cleanup was performed.
- `compileall`, `pip check` (“No broken requirements found”), `git diff --check`, and JSON parsing for historical and Phase 5 artifacts: **PASS**.
- Existing upload/image/decompression limits, PDF/XML escaping, prompt-injection defenses, AI output guardrails, temp cleanup, CORS/XSRF, and bounded-state controls remain covered by the security/full suites. No UI redesign was made.
- No production validation path reads evaluation manifests, expected values, or source truth. AI remains commentary-only and cannot author or alter validation/status evidence. The production PDF/JSON output continues to separate observed values from claims.

## 5. Limitations and disposition

A structural `PASS` means only that the current deterministic checks accepted the observed string. `UNKNOWN` and `NOT_APPLICABLE` remain distinct from both pass and fail. The district registry check is not available; no external source was introduced to fill that gap. Calendar-valid dates, format-valid IDs, and plausible names, districts, and addresses may still be wrong.

Phase 5 improves the detection of impossible DOB dates while preserving abstention semantics and existing security boundaries. It does **not** establish document authenticity, identity, field truth, or calibrated correctness. The strict false-recovery XFAIL and one existing Phase 3 incorrect rejection remain intentionally visible. Stop here for review; no Phase 6 work or commit was performed.
