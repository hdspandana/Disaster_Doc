# Phase 3 — Image-Level End-to-End Evaluation

**Evaluation version:** `image-level-v1`  
**Template:** `synthetic_id_v1`  
**Scope:** small, synthetic, single-template pilot; no production rules changed

## Objective

Check whether the controlled false-recovery patterns from Phase 2 pass through the actual image pipeline:

```text
synthetic image → preprocessing → EasyOCR → damage analysis → field mapping → classifier
```

The evaluation calls `src.pipeline.run_pipeline(image_bytes, use_ai=False)` on generated PNG/JPEG bytes. It does not pass OCR-like strings to the classifier. Ground truth is stored in evaluation files and compared only after the pipeline returns. The pipeline records the optional AI stage as disabled (`ok=false`) by design; this is not counted as a pipeline or OCR error.

## Starting point inspected

The repository already had a fictional card renderer in `tools/make_demo_docs.py`, three seeded damage builders (`build_mild`, `build_partial`, `build_severe`), three matching pixel-occlusion masks under `demo/gt/`, a single fixed template, and the `run_pipeline` entry point. There was no image-level case manifest or evaluation runner. The original source remains one synthetic layout; this pilot varies fictional field values, not layouts.

The Phase 2 artifacts were present. Before Phase 3, the full suite reported **64 passed, 1 strict XFAIL, 20 warnings**, and the security suite reported **14 passed**. The repository-wide Ruff check still had the same 23 out-of-scope findings; they were not modified.

## Dataset and image construction

The manifest defines **10 fictional source records**, each with a clean control and one mild, one medium, and one severe variant: **40 cases total**. Every image is 1000×630. The generated images and per-case damage masks are saved under `evaluation/image_level_assets/` (80 files, approximately 29 MB); their SHA-256 hashes are in the results JSON.

The case set covers:

- one-digit substitution and digit transposition in identifiers;
- a different, valid-format identifier;
- a wrong but pattern-valid date and an impossible calendar date;
- plausible wrong name and district;
- address glyph confusion and plausible wrong address;
- deletion, insertion, and truncation;
- blur, fading/contrast loss, scratches, JPEG compression, and occlusion.

The three existing demo damage builders are reused for the mild/medium/severe levels (`build_partial` supplies the medium profile). These labels name the generator profiles; they are not calibrated or necessarily monotonic damage doses for every target field, because each builder affects different rows. Source 10 adds the named image-space blur, fade/contrast/scratch, and JPEG/occlusion transforms. Full-page cropping was not included: the current field mapper uses fixed normalized geometry and there is no registration stage, so cropping would confound this pilot with an unsupported layout shift.

For semantic-edit cases, the altered field text is rendered into the image pixels using the existing card renderer. The original source value remains the evaluation ground truth; the changed value is recorded separately as `rendered_target_value`. This is an evaluation-only pixel edit, not a production input or classifier string injection. The unchanged pipeline receives only encoded image bytes. This distinction is essential to interpreting false recoveries below.

Seeds are deterministic and recorded per case. The runner resets and restores the demo generator's values, tuning, and relevant environment overrides. It saves both the generated image and synthetic damage mask. Running `source_01_mild` independently as a smoke case and again within the full run produced the same OCR observation and status.

## Definitions

Only the focused target field of each image is counted in the case metrics.

- **Correct recovery:** status is `RECOVERED` and `claimed_value` exactly equals the original source ground truth.
- **False recovery:** status is `RECOVERED` and the claim differs from that ground truth.
- **Correct abstention:** status is not `RECOVERED`, `claimed_value` is `None`, and the OCR observation does not exactly equal ground truth.
- **Incorrect rejection:** the OCR observation exactly equals ground truth, but the classifier abstains.
- **Recovery coverage:** recovered target-field cases divided by successful pipeline cases.
- **Exact match:** correct recoveries divided by successful pipeline cases.
- **False recovery rate among recovered:** false recoveries divided by recovered target-field cases.

A correct OCR reading of a visibly edited value is not treated as an OCR recognition error. The JSON records whether observed text matched the rendered target to make that distinction auditable.

## Executed results

| Outcome/status | Count |
|---|---:|
| Source records | 10 |
| Image cases | 40 |
| Pipeline errors | 0 |
| OCR-stage failures | 0 |
| Correct recovery | 10 |
| False recovery | 11 |
| Correct abstention | 18 |
| Incorrect rejection | 1 |
| `RECOVERED` | 21 |
| `PARTIAL` | 10 |
| `UNRECOVERABLE` | 9 |

Descriptive metrics for this constructed set:

| Metric | Numerator / denominator | Value |
|---|---:|---:|
| Recovery coverage | 21 / 40 | 0.525 |
| Exact match | 10 / 40 | 0.250 |
| False recovery among recovered | 11 / 21 | 0.52381 |

These are not estimates of real-world performance. The cases are selected, correlated variants from one template; the ratios describe only this pilot.

### Outcomes by severity

| Severity | Correct recovery | False recovery | Correct abstention | Incorrect rejection | Status counts |
|---|---:|---:|---:|---:|---|
| Clean | 10 | 0 | 0 | 0 | 10 RECOVERED |
| Mild | 0 | 7 | 2 | 1 | 7 RECOVERED, 3 PARTIAL |
| Medium | 0 | 4 | 6 | 0 | 4 RECOVERED, 4 PARTIAL, 2 UNRECOVERABLE |
| Severe | 0 | 0 | 10 | 0 | 3 PARTIAL, 7 UNRECOVERABLE |

All 10 clean controls recovered their target field exactly. All severe variants abstained.

### False-recovery cases

The 11 false recoveries were `source_01_mild`, `source_01_medium`, `source_02_mild`, `source_02_medium`, `source_03_mild`, `source_03_medium`, `source_04_mild`, `source_05_mild`, `source_06_mild`, `source_06_medium`, and `source_07_mild`:

- `DX-48291` → rendered/OCR-observed/claimed `DX-48281` (single-digit substitution), mild and medium;
- `QA-63714` → `QA-36714` (transposition), mild and medium;
- `LM-29084` → `QR-57139` (valid-format wrong identifier), mild and medium;
- `29 02 2000` → `28 02 2000` (wrong but valid date);
- `17 09 1994` → `31 02 1994` (impossible calendar date accepted by the shape pattern);
- `SANA IYER` → `SANA IYAR` (plausible wrong name), mild and medium;
- `WESTHAVEN` → `NORTHVALE` (plausible wrong district).

Every false-recovery case had OCR text equal to the deliberately rendered, wrong target value. **None of the 11 false recoveries involved EasyOCR disagreeing with the rendered target.** Thus this run shows that the complete pipeline will claim a value when the image visibly contains a synthetically altered, format-valid value; it does **not** demonstrate an OCR misread of unchanged source text that became a false recovery.

The wrong recovered values had measured EasyOCR confidence scores from approximately **0.529 to 0.99982**. These are OCR-engine outputs, not calibrated correctness probabilities. A valid-format wrong ID at 0.529 was recovered; higher-confidence cases were also recovered. Most false-recovery field-zone obscuration readings were around 0.05–0.07, below the classifier's 0.35 field-zone limit.

### Abstentions and the incorrect rejection

- The deletion, insertion, and truncation observations were rejected by the existing ID pattern gate and carried no claim.
- Medium/severe damage removed usable observations in several fields; those results were `UNRECOVERABLE` or `PARTIAL` with `claimed_value=None`.
- On `source_10_medium`, OCR returned `88 PARR STREET BLOC_` instead of the rendered `88 PARK STREET, BLOCK 4`; the pattern, truncation marker, adjacent damage, and low confidence reasons kept the result `PARTIAL`.
- On `source_08_mild`, OCR returned the original ground-truth address despite an O→0 edit in the rendered image. The classifier abstained due to its adjacent-damage finding. Under the strict definition above, this is one incorrect rejection (a coverage loss), not a false recovery.

### Validation and damage evidence

The results file records current pattern, minimum-character, token, and truncation checks for each observed target field. Across 40 rows these were: **24 PASS, 7 FAIL, 9 UNKNOWN** (no mapped OCR text). Pattern validation alone was true for 28 rows, false for 3, and unavailable for 9. PASS is a shape result only; it is not semantic truth. In particular, the DOB pattern accepts `31 02 1994`.

Each result includes the classifier's field/adjacent damage values and boxes, page obscuration, the generator's separate synthetic damage-mask coverage, OCR boxes/polygons, confidence, exact reasons, and pipeline stages. For example, the `source_10_medium` address was `PARTIAL` with 0.137 detected field-zone obscuration and 0.679 adjacent-probe obscuration; the severe variant had 0.565 field-zone and 0.641 adjacent-probe obscuration. The evaluation mask and production damage map are separately recorded and are not assumed to be equivalent.

Per-case pipeline wall time in this run ranged from about **6.39 to 11.00 seconds** (median about **6.79 seconds**); it excludes image rendering/asset writes and is environment-specific. The full run took about 10.4 minutes including image creation and OCR. These timings are instrumentation for reproduction, not a performance benchmark.

## Reproduction

From the repository root:

```bash
.venv/bin/python tools/run_image_level_evaluation.py
.venv/bin/python -m pytest -q tests/test_image_level_evaluation.py
```

Use `--limit 1` for a quick smoke run and give it a temporary `--results`/`--assets-dir` path so it does not replace the full recorded results. Default execution regenerates the saved images/masks and overwrites `evaluation/image_level_results.json`.

## Limitations and interpretation

- This is a synthetic pilot with 10 fictional source records, one template, a single EasyOCR engine, one preprocessing path, and 40 selected target-field cases. It is not a representative dataset or a statistically powered benchmark.
- The semantic-edit variants render alternate values into the image. The 11 false recoveries are relative to the pre-edit source truth and show the system's inability to authenticate a plausible visible value. They are not evidence that OCR misread intact glyphs.
- The physical damage variants are generated overlays, not scans or photographs of real damaged documents. The damage masks are synthetic references, not independent labels for OCR correctness.
- No real-world OCR error rate, real-document performance, calibration, significance, risk-coverage curve, or generalization claim is made.
- Cropping/registration, multiple OCR passes, new validators, classifier changes, and UI changes were not included.

**Phase 3 conclusion:** end-to-end false recovery was observed for visibly altered synthetic field content; OCR faithfully read those altered values and the unchanged classifier recovered them. This supports the Phase 2 observation that format, confidence, and local damage checks do not establish source truth. It does not establish an EasyOCR misrecognition-driven false recovery on an otherwise unchanged source image. Production behavior remains unchanged.
