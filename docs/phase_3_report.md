# Phase 3 Report — Image-Level End-to-End Evaluation

## Objective

Test whether the Phase 2 false-recovery pattern can pass through a synthetic image, preprocessing, EasyOCR, damage analysis, field mapping, and the unchanged deterministic classifier. Keep all ground truth evaluation-only and stop before Phase 4.

## Starting state

- Repository: `/home/user/Disaster_Doc`, branch `main`, HEAD `ea0d7e95cb36`; the worktree was already dirty from earlier approved phases. I did not reset or commit those changes.
- Phase 2 artifacts were present: the 33-case classifier corpus, executed results, characterization runner, and report.
- Existing demo infrastructure had one synthetic card layout, three seeded damage builders, and three ground-truth pixel masks. `run_pipeline(image_bytes, use_ai=False)` provides the end-to-end entry point.
- Baseline before Phase 3 changes: `pytest -q` **64 passed, 1 strict XFAIL, 20 warnings**; security suite **14 passed**. Repository-wide Ruff had 23 existing findings.

## Changes made

- Added a 10-source, 40-case manifest with clean, mild, medium, and severe variants.
- Reused `tools/make_demo_docs.py` rendering and damage builders; added evaluation-only image-space blur, contrast/fade, scratch, JPEG, and occlusion effects where specified.
- Added a runner that sends only generated image bytes to the unchanged full pipeline with AI disabled. It records evaluation truth only after the pipeline returns.
- Saved all 40 images and 40 masks, and per-case executed results including observed/claimed text, confidence, geometry, validation, damage, reasons, stages, runtime, errors, and hashes.
- Added five evaluation-harness regression tests and updated current-state/README documentation.

## Files changed

**Added:**

- `tools/run_image_level_evaluation.py`
- `evaluation/image_level_cases.json`
- `evaluation/image_level_results.json`
- `evaluation/image_level_assets/` — 40 generated images and 40 damage masks
- `tests/test_image_level_evaluation.py`
- `docs/image_level_evaluation.md`
- `docs/phase_3_report.md`

**Updated:**

- `README.md`
- `docs/current_state_gap_analysis.md`

`src/` production modules, classifier thresholds/status rules, security code, Phase 2 artifacts, and the strict XFAIL were not changed. No commit was made.

## Production behavior changed?

**NO.** The full pipeline and classifier were exercised unchanged. The runner calls `run_pipeline` with encoded images, `use_ai=False`; it does not supply text or ground-truth metadata to the classifier or pipeline.

## Evaluation changes

- 10 distinct fictional field-value records, all on the existing single template.
- 40 images total: 10 clean controls plus 10 each at mild, medium, and severe generator profiles.
- Case families: single-digit substitution, transposition, valid-format wrong ID, wrong valid and impossible DOB, plausible wrong name/district/address, address glyph confusion, deletion, insertion, truncation, blur, fade/contrast loss, scratch, JPEG compression, and occlusion.
- Full-page cropping was excluded because the current mapper uses fixed normalized geometry and there is no registration stage.
- Each case has a deterministic seed and saved image/mask. The image SHA-256 values in the results matched all 40 saved images; mask hashes matched all 40 masks.

## Tests

- Phase 3 image-evaluation tests: **5 passed**.
- Full suite after Phase 3: **69 passed, 1 strict XFAIL, 20 warnings** in 200.24 seconds.
- Security suite: **14 passed**.
- Focused Ruff on Phase 3 and characterization artifacts: passed.
- Repository-wide Ruff: 23 existing findings remain and were not altered.
- Repository `compileall`, `pip check`, `git diff --check`, and JSON parsing: passed.

## Results

| Measure | Cases |
|---|---:|
| Sources / images | 10 / 40 |
| Pipeline errors | 0 (AI stage disabled by design) |
| OCR-stage failures | 0 |
| Correct recovery | 10 |
| False recovery | 11 |
| Correct abstention | 18 |
| Incorrect rejection | 1 |
| Statuses: RECOVERED / PARTIAL / UNRECOVERABLE | 21 / 10 / 9 |

Descriptive ratios for this selected synthetic set: recovery coverage **21/40 = 0.525**; exact match **10/40 = 0.250**; false recovery among recovered **11/21 = 0.52381**. They are not estimates of real-world performance.

All 10 clean controls were recovered exactly. False recoveries included `DX-48291`→`DX-48281`, `QA-63714`→`QA-36714`, a valid-format wrong ID, `29 02 2000`→`28 02 2000`, impossible `31 02 1994`, a plausible wrong name, and a plausible wrong district. The detailed cases and reasons are in `evaluation/image_level_results.json`.

Critically, **all 11 false-recovery OCR observations matched the deliberately rendered wrong target value**. Thus, an end-to-end false recovery relative to the original source truth was observed, but this pilot did **not** demonstrate EasyOCR misreading unchanged source glyphs and then causing a false recovery. Image-visible edits and OCR errors are separately recorded in the results.

Structural edits were abstained on; severe cases were partial or unrecoverable. One address case was an incorrect rejection: OCR returned the original correct value but the existing adjacent-damage rule abstained. `source_10_medium` produced an OCR mismatch (`PARR`, trailing `_`) and abstained with pattern, marker, damage, and confidence reasons.

Format/length/token/truncation checks were recorded as PASS for 24 rows, FAIL for 7, and UNKNOWN for 9 rows without OCR text. A shape-valid impossible date still passed the existing DOB regex. Confidence values are raw EasyOCR scores, not calibrated probabilities. The per-case pipeline wall-time median was about 6.79 seconds in this environment; it is not a performance claim.

## Findings

1. The clean synthetic controls behave sensibly under this pilot: all 10 target fields were exact recoveries.
2. The pipeline can report a wrong, format-valid value as `RECOVERED` when the altered value is visible in the rendered image and OCR observes it. Damage and format checks do not establish that it matches the pre-corruption source.
3. This experiment does not establish that the Phase 2 string-substitution false recovery is caused by EasyOCR misrecognition of an otherwise unchanged image. The OCR output matched the rendered target in every false-recovery case.
4. The image-level pilot produces reproducible, inspectable assets and per-case results, but remains a single-template synthetic evaluation with selected/correlated cases.

## Limitations

- 10 fictional value records share one layout; these are not 10 independent templates or real documents.
- Severity labels refer to existing named builder profiles, not a calibrated damage-dose scale; different profiles affect different fields.
- Many false-recovery variants intentionally render a wrong value into the image. They measure disagreement with original source truth, not a naturally occurring OCR error rate.
- No crop/registration, multi-pass recognition, new validator, classifier change, risk-coverage study, calibration, statistical significance, or real-world performance claim was included.
- OCR confidence is not a correctness probability. The strict Phase 2 XFAIL remains unchanged.

## Decision

**Phase 3 gate passes narrowly:** clean controls passed, all 40 pipeline runs completed without pipeline/OCR-stage errors, image and mask hashes matched their result records, truth remained evaluation-only, existing tests/security remained green, and the report distinguishes visible content edits from OCR mismatches. The principal research limitation is that no OCR-mismatch-driven false recovery was observed in this selected set.

## Next phase recommendation

Proceed to **Phase 4 only after review**: define a small, versioned evidence contract that explicitly separates `observed_value` from `claimed_value` and adds deterministic reason codes using the existing `Observation`, `FieldResult`, and `EvidenceDocument` foundations. Keep classifier thresholds/status semantics unchanged; preserve `PARTIAL`/`UNRECOVERABLE` → `value=None`. Do not implement field validation or multi-pass recognition in Phase 4; those belong to later gated phases.
