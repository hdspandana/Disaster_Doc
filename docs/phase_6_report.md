# Phase 6 report — evidence fidelity and residual root causes

- **Date:** 2026-10-02
- **Baseline:** Phase 5 replay, evidence schema 2, config v2
- **Phase 6 config:** `disasterdoc-config-v3`; evidence schema remains `2`; template remains `2`
- **Decision:** Do not tune the classifier to the 25 historical residuals. Clarify that `RECOVERED` is OCR transcription, add conservative handling for competing OCR boxes, and correct misleading stage timings.

## Executive summary

All **25** false recoveries remaining after Phase 5 have been reviewed individually. They do not represent 25 OCR errors:

- **Phase 2 (15 cases):** controlled synthetic inputs to the classifier harness. The injected text, confidence, and damage profile are not OCR engine output; there is no document image to inspect. The evaluation calls these false recoveries because the candidate differs from an offline source-value label.
- **Phase 3 (10 cases):** each saved EasyOCR reading exactly matches the deliberately edited value visible in its rendered synthetic image and differs from the original pre-edit source value. They are source-record mismatches, not demonstrated OCR misreads.

All 25 had structural validation `PASS`, remained `RECOVERED` in their Phase 5 records, and had claims copied from the observation. For the intended status meaning—transcription of visible text—`RECOVERED` is defensible for the ten Phase 3 cases. For Phase 2, the evidence-justified **production document** status is `UNRECOVERABLE`: the saved cases contain no rendered document or OCR-engine output. Their `RECOVERED` values are harness outcomes only, conditional on treating injected strings as complete OCR observations. In all 25, source-record correctness is **not established**. `PASS` is not truth, and the classifier has no independent source-value check.

A separate synthetic probe reproduced a real evidence-handling defect: two different usable OCR readings over the same field pixels were reduced to the higher-confidence reading, the alternative was omitted from field evidence, and the field became `RECOVERED`. Phase 6 now preserves both alternatives and returns `PARTIAL` with no claim. This does not explain or re-score any of the 25 historical cases. Stage reporting was also corrected so OCR time is no longer attributed to image preprocessing while the OCR stage reports approximately zero.

## 1. Scope, repository state, and Phase 5 baseline

The repository was already dirty at the start of Phase 6: the recorded baseline was **15 modified tracked files and 13 untracked files**, including prior Phase 5 work. Those files were preserved; no reset, commit, or push was performed. The Phase 5 replay records revision `ea0d7e95cb36`, a dirty worktree, evidence schema 2, config v2, and template v2.

Phase 5’s recorded outcomes are preserved below. These are source-label evaluation outcomes, not population accuracy estimates.

| Phase | Cases | Before Phase 5 | After Phase 5 |
|---|---:|---|---|
| Phase 2 controlled classifier inputs | 33 | 5 correct recoveries; 16 false recoveries; 12 correct abstentions; 0 incorrect rejections | 5 correct recoveries; 15 false recoveries; 13 correct abstentions; 0 incorrect rejections |
| Phase 3 saved image/OCR pilot | 40 | 10 correct recoveries; 11 false recoveries; 18 correct abstentions; 1 incorrect rejection | 10 correct recoveries; 10 false recoveries; 19 correct abstentions; 1 incorrect rejection |

Phase 5 changed only the impossible-calendar-date case in each phase: one false recovery became a correct abstention in Phase 2, and one in Phase 3. Correct recoveries and the existing Phase 3 incorrect rejection were unchanged. The strict false-recovery expected failure remains intentional: a single high-confidence, format-valid ID substitution is not detectable from structure alone.

The Phase 5 replay remains byte-identical (SHA-256 `72ae74031e4a6124a0aa45010ee49cc9f1dcf345a353aba282d10ab04b9bd823`). Phase 2/3 inputs and results were also left unchanged; their hashes and the Phase 3 asset-tree hash are recorded in `evaluation/phase_6_root_cause_v1.json`.

## 2. Pipeline and evaluation-label audit

The production path is: exact upload hash → validated image decode and preprocessing → one OCR pass producing raw text/box/confidence observations → damage map → fixed template field mapping → deterministic validation and classification → evidence/report output. Optional Gemini commentary remains downstream, advisory-only, and off by default.

`src.validation.validate_field()` receives only a field name, observed string, and template spec. It has no ground-truth argument and does not import evaluation code. The classifier likewise does not receive expected values. In Phase 2, the evaluation runner injects text/confidence/damage, lets classification finish, and only then compares the result with the source label. In Phase 3, the pipeline receives image bytes; source labels and rendered targets are compared after pipeline output is saved. Phase 5 also calls the validator before its evaluation-only ground-truth supplier.

**No label leakage or circularity was found.** The Phase 3 issue is label scope, not circular computation: some images intentionally display values different from the original source labels, while the historical “false recovery” label compares against those original labels. That is a valid source-consistency measurement, but it is not OCR transcription accuracy. The Phase 3 rendered target is separately available for the transcription view.

## 3. Case-by-case analysis of all 25 Phase 5 residuals

### Phase 2 — controlled classifier inputs (15)

Each `obs_test` row below is an injected fixture with a synthetic bbox and confidence, **not** an OCR reading. The bbox is included to show what the harness supplied; it is not image provenance. `clean` and `moderate 20%` refer to the harness damage profiles.

| Case / field | Injected candidate; score; bbox; profile | Source label → extracted/claimed candidate | Phase 5 validation / status; root-cause code |
|---|---|---|---|
| `id_single_digit_substitution_conf_060_clean` / ID | `obs_test` `DX-48281`; 0.60; `[60,211,220,231]`; clean | `DX-48291` → `DX-48281` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_single_digit_substitution_conf_080_clean` / ID | `obs_test` `DX-48281`; 0.80; `[60,211,220,231]`; clean | `DX-48291` → `DX-48281` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_single_digit_substitution_conf_094_clean` / ID | `obs_test` `DX-48281`; 0.94; `[60,211,220,231]`; clean | `DX-48291` → `DX-48281` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_single_digit_substitution_conf_099_clean` / ID | `obs_test` `DX-48281`; 0.99; `[60,211,220,231]`; clean | `DX-48291` → `DX-48281` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_single_digit_substitution_damage_moderate` / ID | `obs_test` `DX-48281`; 0.94; `[60,211,220,231]`; moderate 20% | `DX-48291` → `DX-48281` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_multiple_digit_substitution` / ID | `obs_test` `DX-48071`; 0.94; `[60,211,220,231]`; clean | `DX-48291` → `DX-48071` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_SUBSTITUTION` |
| `id_transposition` / ID | `obs_test` `DX-42891`; 0.94; `[60,211,220,231]`; clean | `DX-48291` → `DX-42891` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_TRANSPOSITION` |
| `id_valid_format_wrong_value` / ID | `obs_test` `QY-73156`; 0.94; `[60,211,220,231]`; clean | `DX-48291` → `QY-73156` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `FORMAT_VALID_ID_NO_CHECKSUM_OR_REGISTRY` |
| `dob_valid_format_wrong_value` / DOB | `obs_test` `14 08 1981`; 0.94; `[60,276,220,296]`; clean | `14 08 1991` → `14 08 1981` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `CALENDAR_VALID_DOB_NO_SOURCE_COMPARISON` |
| `name_ocr_confusion_O_to_Q` / name | `obs_test` `ANANYA RAQ`; 0.94; `[60,146,220,166]`; clean | `ANANYA RAO` → `ANANYA RAQ` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `INJECTED_NAME_CHARACTER_SUBSTITUTION` |
| `name_plausible_wrong_value` / name | `obs_test` `ANANYA RAJ`; 0.94; `[60,146,220,166]`; clean | `ANANYA RAO` → `ANANYA RAJ` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `PLAUSIBLE_NAME_NO_IDENTITY_REFERENCE` |
| `district_plausible_wrong_value` / district | `obs_test` `MYSURU`; 0.94; `[60,341,220,361]`; clean | `BENGALURU` → `MYSURU` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `PLAUSIBLE_DISTRICT_NO_REGISTRY` |
| `address_ocr_confusion_S_to_5` / address | `obs_test` `42 LAKEVIEW 5TREET, WARD 7`; 0.94; `[60,416,220,436]`; clean | `42 LAKEVIEW STREET, WARD 7` → `42 LAKEVIEW 5TREET, WARD 7` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `INJECTED_ADDRESS_CHARACTER_SUBSTITUTION` |
| `address_ocr_confusion_I_to_1` / address | `obs_test` `42 LAKEV1EW STREET, WARD 7`; 0.94; `[60,416,220,436]`; clean | `42 LAKEVIEW STREET, WARD 7` → `42 LAKEV1EW STREET, WARD 7` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `INJECTED_ADDRESS_CHARACTER_SUBSTITUTION` |
| `address_plausible_wrong_value` / address | `obs_test` `42 LAKEVIEW STREET, WARD 1`; 0.94; `[60,416,220,436]`; clean | `42 LAKEVIEW STREET, WARD 7` → `42 LAKEVIEW STREET, WARD 1` | PASS / harness RECOVERED; desired document status UNRECOVERABLE; `PLAUSIBLE_ADDRESS_NO_EXISTENCE_CHECK` |

**Diagnosis:** These strings were supplied to the classifier, so terms such as “single-digit OCR substitution” in the Phase 2 case names describe how a synthetic input was constructed; they do not establish OCR behavior. The format-valid ID edits pass because the template has no checksum or fixed authoritative prefix. The DOB is calendar-valid but cannot be source-verified. Name, district, and address patterns establish only structure; there is no identity registry, district registry, geocoder, or address lookup. `RECOVERED` is appropriate only as the harness’s candidate-transcription result. For a production document assessment, `UNRECOVERABLE` is the only evidence-justified status because no document/OCR evidence is present. Actual document text and OCR quality are not assessable from Phase 2.

### Phase 3 — saved synthetic image/OCR cases (10)

The table records the saved field observation’s OCR ID, verbatim text, engine score, and original-image bbox. In every row the saved OCR text equals the deliberate rendered target; the target differs from the original source label.

| Case / field | Saved OCR evidence (ID; score; bbox) | Original source → printed target → OCR/claim | Phase 5 validation / status; root cause |
|---|---|---|---|
| `source_01_mild` / ID | `obs_009` `DX-48281`; 0.9659; `[59,205,225,241]` | `DX-48291` → `DX-48281` → `DX-48281` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_01_medium` / ID | `obs_009` `DX-48281`; 0.9926; `[60,206,224,238]` | `DX-48291` → `DX-48281` → `DX-48281` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_02_mild` / ID | `obs_009` `QA-36714`; 0.9238; `[59,205,231,241]` | `QA-63714` → `QA-36714` → `QA-36714` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_02_medium` / ID | `obs_009` `QA-36714`; 0.9998; `[57,205,230,241]` | `QA-63714` → `QA-36714` → `QA-36714` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_03_mild` / ID | `obs_009` `QR-57139`; 0.5293; `[59,205,229,241]` | `LM-29084` → `QR-57139` → `QR-57139` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_03_medium` / ID | `obs_009` `QR-57139`; 0.7986; `[57,205,231,241]` | `LM-29084` → `QR-57139` → `QR-57139` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_04_mild` / DOB | `obs_011` `28 02 2000`; 0.9752; `[57,269,253,305]` | `29 02 2000` → `28 02 2000` → `28 02 2000` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_06_mild` / name | `obs_006` `SANA IYAR`; 0.9991; `[60,141,244,174]` | `SANA IYER` → `SANA IYAR` → `SANA IYAR` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_06_medium` / name | `obs_006` `SANA IYAR`; 0.8706; `[58,142,244,174]` | `SANA IYER` → `SANA IYAR` → `SANA IYAR` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |
| `source_07_mild` / district | `obs_013` `NORTHVALE`; 0.9999; `[59,335,267,371]` | `WESTHAVEN` → `NORTHVALE` → `NORTHVALE` | PASS / RECOVERED → RECOVERED; `VISIBLE_TARGET_INTENTIONALLY_CHANGED` |

**Diagnosis:** The OCR pipeline transcribed what the edited image showed. The overconfidence point is not a wrong glyph decision in these ten saved cases; it is the interpretation of a structurally valid OCR transcription as if it independently confirmed the pre-edit source value. DOB calendar validation correctly cannot distinguish two possible dates. The evidence supports `RECOVERED` as visible-text transcription; source-record consistency remains unverified.

The normalized value is not separately represented in the saved case contract. For all 25, the recorded claim equals the mapped observation exactly; Phase 5 validation does not rewrite the candidate. Full per-case validation rows, damage measurements/profiles, image hashes, and evidence records are in `evaluation/phase_6_root_cause_v1.json`.

## 4. Metrics and interpretation

For the confusion matrices below, a **positive label** means the saved candidate exactly equals the stated reference value; a **positive prediction** means status is `RECOVERED`. Thus TP/FP/FN/TN describe candidate-reference match versus recovery, not a generic multi-class classifier and not the validator’s `PASS`/`FAIL` states.

| Evaluation target | TP / FP / FN / TN | Precision | Recall | Specificity | Recovered coverage | Exact correct recovery / all cases |
|---|---:|---:|---:|---:|---:|---:|
| Phase 2 original source value (33 controlled inputs) | 5 / 15 / 0 / 13 | 25.0% | 100.0% | 46.4% | 20/33 = 60.6% | 5/33 = 15.2% |
| Phase 3 original source value (40 image cases) | 10 / 10 / 1 / 19 | 50.0% | 90.9% | 65.5% | 20/40 = 50.0% | 10/40 = 25.0% |
| Phase 3 rendered visible target (40 image cases) | 20 / 0 / 3 / 17 | 100.0% | 87.0% | 100.0% | 20/40 = 50.0% | 20/40 = 50.0% |

The Phase 3 raw field observation exactly matches rendered text in **23/40 (57.5%)** cases. All **20/20** Phase 5 recovered claims match that rendered target; the remaining three target-matching observations are not recovered because of structural/calendar gates (one impossible calendar date and two incomplete ID readings). The Phase 3 source-truth incorrect rejection is `source_08_mild` and remains unchanged.

These are descriptive measurements on curated synthetic cases, not representative estimates. Phase 2 has no OCR, so its matrix is classifier-harness behavior only. Phase 3 intentionally edits rendered values, so source-record metrics and visible-transcription metrics answer different questions. The phases are **not pooled**. A validator confusion matrix would be misleading: a structural `PASS` is not an independent source-truth label.

## 5. Phase 6 changes and decisions

### Conflicting overlapping OCR observations

The pre-change probe supplied two usable ID observations with the same bbox: `DX-48291` at 0.99 and `DX-48281` at 0.98. Mapping retained only the higher-confidence reading as field evidence, omitted the alternative, and classified `RECOVERED`.

`src/fields.py` now identifies pairs that (a) both meet existing OCR usability gates, (b) have different text after whitespace trimming and case-folding, and (c) have positive-area overlap between original-image boxes. The lower-priority alternative is excluded from the compatibility joined-text view, but **both observations** are carried into field evidence. `src/classifier.py` records an `ocr_observation_consistency=UNKNOWN` validation row with both IDs/texts/boxes/scores, emits `CONFLICTING_OCR_OBSERVATIONS`, returns `PARTIAL`, and leaves the claim null. OCR confidence is not treated as independent confirmation.

Targeted tests also show that same-text overlapping duplicates do not trigger the rule, a below-threshold alternative does not veto a usable reading, and adjacent non-overlapping name boxes still combine into a transcription. This rule was not retroactively replayed against Phase 3: the frozen residual records contain one field-mapped reading each, not the complete competing raw-observation set. It is a separate robustness fix, not an explanation for the 25.

This is intentionally conservative, but not yet calibrated on a real OCR corpus. Overlapping boxes that actually represent adjacent glyph fragments could increase abstentions; the test suite covers a basic adjacent-box control, not all OCR geometries. No false-recovery-reduction metric is claimed for this change.

### Status wording and timing

The UI now says directly that `RECOVERED` is a complete OCR transcription that passed current checks—not independent verification of source-record correctness, identity, or authenticity. Competing readings are shown escaped in the evidence detail. Existing `RECOVERED`/`PARTIAL`/`UNRECOVERABLE` semantics and the null-claim invariant remain unchanged.

OCR extraction now notifies the pipeline immediately after safe decode/preprocessing. The pipeline records that stage then, and records OCR duration from `OcrResult.processing_seconds`; it no longer attributes OCR runtime to “Image processed” and then reports OCR as approximately zero. A deterministic mocked-stage test checks ordering and a 2.25-second OCR duration. This is observability correction, not an OCR speed optimization.

The evidence record shape did not change, so schema version remains 2. The new typed validation row uses the existing `validator/result/reason/details` structure. The decision rule change increments config from v2 to v3; template version remains 2.

### Deliberate non-changes

- No Phase 2/3 source, case, result, image, or Phase 5 replay artifact was modified or regenerated. No historical OCR was rerun.
- No new ID checksum/prefix truth rule, DOB identity check, name/district/address registry, geocoder, arbitrary regex, similarity scorer, extra OCR pass, external service, or LLM accuracy feature was added.
- No classifier threshold was tuned. No status was changed for the 25 residuals. Structural validation remains limited to what the existing template rules can establish.
- No package dependency was added or modified. Existing optional Gemini use remains downstream and off by default.

## 6. Security, privacy, robustness, and performance review

The Phase 6 implementation adds no file upload path, filesystem persistence, external request, or user-controlled path handling. Existing protections were reviewed and the security suite passed: supported-container signature/format matching; 25 MiB upload, 25-million-pixel, and 10,000-pixel dimension bounds; Pillow decompression-bomb handling; invalid-image exclusion from OCR; escaped Streamlit HTML and PDF/XML output; in-memory PDF images with no temporary image files; bounded one-document session cache; and CORS/XSRF enabled in Streamlit configuration.

Privacy limits remain important: the app processes the upload in memory, but JSON/PDF exports intentionally contain raw OCR observations and may therefore include sensitive or unrelated page text. Protect exported reports. If the user explicitly enables Gemini commentary, incomplete OCR text, field/status/reasons, deterministic validation evidence (including competing OCR text/boxes/scores when present), the expected pattern, document hash, and obscured-area fraction are sent; the image is not sent. AI remains off by default and cannot change deterministic evidence. `DD_AI_TIMEOUT` is documented but not enforced by the SDK request. These are existing boundaries/limitations; Phase 6 makes the existing payload contents more explicit in the UI disclosure and does not add a new service or data flow.

Performance review found no OCR algorithm or preprocessing changes. Stage timing now measures the intended boundaries; on a cold run, OCR duration includes model initialization. The full OCR-backed suite completed in 187.43 seconds in the environment below. No broader throughput or latency benchmark was performed.

## 7. Verification and reproduction

### Test environment

A fresh `.venv` was restored because none existed at the start of Phase 6. Tested versions: Python 3.13.14, Streamlit 1.64.0, EasyOCR 1.7.2, CPU PyTorch 2.14.1, torchvision 0.29.1, OpenCV 4.10.0, NumPy 1.26.4, SciPy 1.16.3, tifffile 2025.6.11, ReportLab 5.0.1, pytest 9.1.1. `pip check` reported no broken requirements. NumPy/SciPy/tifffile were pinned in this local test environment for compatibility with the repository’s OpenCV 4.10 pin on Python 3.13; `requirements.txt` was not changed. EasyOCR weights/cache live outside the repository.

### Results

- Phase 5 baseline: **98 passed, 1 strict XFAIL, 20 warnings**; security **14 passed**; focused validation/evidence/replay **29 passed**.
- Phase 6 full suite: **106 passed, 1 strict XFAIL, 20 warnings** in 187.43 s.
- Security suite: **14 passed**.
- Focused validation/evidence/Phase 5 replay set: **29 passed**.
- Added Phase 6 analysis/conflict/timing tests passed in the full suite. The strict XFAIL remains strict and was not weakened.
- Offline analysis artifact reproducibility check passed; Phase 2/3/5 hashes still match the recorded inputs.

### Exact commands

From the repository root, the tested environment was constructed with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install 'torch==2.14.1+cpu' 'torchvision==0.29.1+cpu' \
  --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install 'numpy==1.26.4' 'scipy==1.16.3' 'tifffile==2025.6.11'
.venv/bin/python -m pip check
```

Verification and offline-artifact checks:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/test_security.py
.venv/bin/python -m pytest -q tests/test_validation.py tests/test_evidence_contract.py tests/test_phase5_validation_replay.py
.venv/bin/python -m pytest -q tests/test_phase6_analysis.py tests/test_conflicting_ocr_observations.py tests/test_pipeline_stage_timing.py
python3 tools/analyze_phase6.py --check
python3 -m compileall -q src app.py tests tools/analyze_phase6.py
git diff --check
```

To create the new analysis file before it exists, run `python3 tools/analyze_phase6.py`. To verify it later without writing, run `python3 tools/analyze_phase6.py --check`. The explicit `--force` option is only for replacing this Phase 6 versioned output; the script refuses to overwrite Phase 2/3/5 artifacts.

## 8. Deliverables and final state

- `docs/phase_6_report.md` — this report.
- `evaluation/phase_6_root_cause_v1.json` — deterministic 25-case analysis, all-case metric definitions, source hashes, and label audit. SHA-256: `ff23d79f86bab4632d9d925b9e27335e4b47a94b979efe30966d4f52289c1771`.
- `tools/analyze_phase6.py` — offline generator/checker for that new artifact.

No commit or push was made. The final repository status, diff-stat, and `git diff --check` result are reported separately with the completion summary.
