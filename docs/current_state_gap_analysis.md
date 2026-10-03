# DisasterDoc current-state gap analysis

- **Audit date:** 2026-10-02 (Asia/Kolkata)
- **Repository:** `/home/user/Disaster_Doc`
- **Branch / HEAD:** `main` at `ea0d7e9` (`Fix OpenCV deployment dependency`)
- **Audit basis:** actual working tree, including the uncommitted baseline-stabilization and security-hardening changes. This is not a claim that `HEAD` contains those changes.
- **Scope:** README, app, all `src/` modules, tests, tools, demo images/masks, Streamlit config, requirements, Git state, reports, existing audit documentation, and searches for security/evaluation/benchmark code.

## Executive assessment

DisasterDoc already has a small, functioning evidence-first Streamlit application and a deterministic three-state classifier. The core safety invariant is implemented in normal classification: `PARTIAL` and `UNRECOVERABLE` carry `claimed_value=None` (legacy `value=None`); `RECOVERED` copies the observed OCR string rather than completing it. The security pass has added server-side image-container/resource checks, escaped rendering, bounded session results, opt-in AI, safe user-facing errors, and browser-origin protections.

The principal technical/research weakness remains **false recovery**: format/length/pattern checks, OCR confidence, and local readability do not establish source truth. Phase 2 characterized 33 direct classifier inputs (16 false recoveries, 12 abstentions, 5 correct controls). Phase 3 then ran 40 seeded synthetic images through the full pipeline and observed 11 false recoveries relative to the uncorrupted source values. All 11 OCR observations matched the deliberately rendered wrong image text; this pilot did **not** demonstrate an OCR misread of unchanged source glyphs becoming a false recovery. The measurements are selected synthetic cases, not real-world rates.

Phase 4 made observation, claim, deterministic evidence, and reason-code representations explicit. Phase 5 added typed field-specific validation and calendar-valid DOB gating without changing the three status meanings or classifier thresholds. The replay rejects impossible dates but leaves most format-valid wrong values unresolved. No need to replace Streamlit, EasyOCR, or the deterministic pipeline has been demonstrated. **Stop at the Phase 5 gate; do not begin Phase 6 without review.**

## Repository and project hygiene

- The current working tree is on `main`, at `ea0d7e9`, with uncommitted changes from earlier approved phases plus Phases 4–5. Phase 2/3 historical artifacts are retained byte-for-byte; Phase 5 adds only a separate evaluation replay. No unrelated UI redesign, new OCR path, or external service was added. No commit was made.
- Present: `README.md`, `requirements.txt`, `.env.example`, `.streamlit/config.toml`, `src/`, `tests/`, `tools/`, three original demo images/masks, README figures, Phase 2 controlled classifier cases, and Phase 3 image-level cases/results/assets.
- Still absent: `pyproject.toml`, `setup.cfg`, lockfile, `.github/` CI, Docker/deployment files, and an `outputs/` directory in this checkout. The new evaluation runner is a small synthetic pilot, not a general benchmark framework.
- `docs/repository_audit.md` records the original audit and prior phases; its early sections are explicitly historical. This file records the present assessment.
- `requirements.txt` pins Streamlit, OpenCV, and EasyOCR, but leaves Torch, NumPy, Pillow, ReportLab, Google GenAI, dotenv, and pytest ranges/versions comparatively open. There is no supported Python/dependency lock or clean-install matrix.
- README links to `outputs/sample_partial_damage_evidence.json` and `.pdf`, but `outputs/` is absent until `tools/make_figures.py` is run. README also says checked-in demos reproduce byte-for-byte, while the current regression verifies two same-environment generator runs against each other, not the committed artifacts.

## End-to-end path and stage assessment

| Stage | Status | What exists / gap |
|---|---|---|
| Intake | **WORKING / PARTIAL** | Streamlit demo selection and file upload; extension filter; 25 MiB server cap. The uploaded bytes/name live in session state. There is no multi-document case object or upload-review state. |
| Validation | **WORKING / PARTIAL** | `src.preprocessing` recognizes PNG/JPEG/WebP/BMP/TIFF signatures, checks Pillow's container, size, dimensions, pixels, and Pillow decompression-bomb warnings before OpenCV decode. The pipeline hashes bytes before invoking that validation; direct API calls also depend on a bytes-like input contract. No authentication, concurrency cap, or request-rate limit exists. |
| Decode | **WORKING** | Pillow verification plus OpenCV decode; failures are returned with safe user-facing messages. An image is decoded before width downscaling, so the decoded-pixel cap—not `MAX_OCR_WIDTH`—is the relevant memory bound. |
| Preprocessing | **WORKING / PARTIAL** | One deterministic path: optional width downscale, grayscale, CLAHE, bilateral denoise. It does not restore/inpaint. No controlled threshold/sharpen variants or preprocessing comparison are recorded. |
| OCR | **WORKING / PARTIAL** | Lazy, lock-protected EasyOCR CPU reader; one `readtext` pass; observations preserve OCR text, polygon/bbox, and engine confidence. OCR confidence is not calibrated. Model/weight version and an independently reproducible runtime identity are not recorded in each observation/report. |
| Damage analysis | **WORKING / PARTIAL** | Pixel heuristic using percentile luminance and local contrast, then morphology; computes field-zone obscuration and an immediately-right probe. It is not trained/calibrated; adjacent damage only represents one geometric cue; complete mask/probe geometry is not fully serialized. |
| Field mapping | **WORKING for one template / WEAK generalization** | Normalized fixed row bands, value zone, label anchors, fuzzy label matching, and nearby line merging. No explicit unsupported-template outcome; off-template, rotated, perspective, multi-page, or unfamiliar layouts can be mis-mapped. |
| Classification | **WORKING invariant / RESEARCH-CRITICAL weakness** | Deterministic `RECOVERED` / `PARTIAL` / `UNRECOVERABLE`; null-claim invariant for non-recovered fields. Existing usable OCR, shape, truncation, damage, and confidence gates remain. Phase 5 adds calendar-invalid DOB abstention without changing thresholds. No independent recognition signal, checksum/registry, field truth, or calibrated correctness model exists; format-valid wrong IDs, dates, names, districts, and addresses may still pass. |
| Evidence assembly | **PHASE 5 VALIDATION CONTRACT ADDED** | `FieldResult` exports observed vs claimed values, stable reason codes, typed OCR/damage/validation records, per-validator `PASS`/`FAIL`/`UNKNOWN`/`NOT_APPLICABLE`, aggregate validation result, and schema v2/config/template metadata. Legacy aliases remain additive. Recognition agreement remains intentionally absent. |
| Optional AI | **WORKING boundary / PARTIAL safety** | Off by default; called after deterministic status/value; no image is sent; AI writes only to `ai_commentary`. OCR/reason/context is placed directly in the prompt. Output is length-filtered/escaped, but AI commentary can still anchor a reviewer; no prompt-injection suite exists. `DD_AI_TIMEOUT` is read by config but is not applied to the Gemini client. |
| Human verification | **PARTIAL** | Status-derived checklist and Streamlit acknowledgements exist. Acknowledgements are transient session state and are not serialized as reviewer-attributed events. There is no human decision/corrected-value model, timestamped history, or explicit separation of human assertion from machine evidence in reports. |
| JSON/PDF reports | **WORKING / PARTIAL** | JSON exports observations, field results, summary, audit/processing data, verification tasks, disclaimer, Phase 5 schema/config/template metadata, separate observed/claimed values, stable reason codes, and four-state validator records with reasons/details. PDF presents observed, claimed, status, and validator results separately; escaping and in-memory image handling remain. No report hash/signature, serialized human events, or full document-level mask. PDF includes a current generation timestamp, so byte-for-byte PDF reproducibility is not a goal/current guarantee. |
| UI/review | **WORKING / PARTIAL** | Streamlit provides demos/upload, annotated/original/preprocessed images, field selection, reasons, checklist, raw OCR table, and downloads. It is a single-document page, not a persistent case/evidence-review workspace. Checklist state is not a durable verification record. |
| Evaluation | **PARTIAL / SYNTHETIC PILOT** | Phases 2–3 now provide 33 controlled classifier inputs and 40 image-level cases (10 fictional source records × clean/mild/medium/severe). Per-case results and basic exact-match/coverage/false-recovery metrics are recorded. This is one template and a selected synthetic pilot; there is no representative or real-world benchmark, threshold sweep, risk-coverage analysis, calibration, or ablation. |
| Export/operations | **PARTIAL** | `.streamlit/config.toml` enables CORS/XSRF and disables detailed browser errors. Local preview was previously validated. Reverse-proxy behavior needs deployment-specific verification; server-side safe diagnostic logging, rate/concurrency controls, and an install/CI gate are absent. |

## What already exists and is actually useful

- Deterministic status/value code is the authority; optional AI is downstream and cannot write the factual fields through `attach_ai_commentary`.
- Raw OCR observations are retained as a distinct list and mapped boxes are tested against image bounds.
- Every normal `PARTIAL` and `UNRECOVERABLE` classifier result has a null value, with regression coverage in unit, integration, and UI tests.
- The original demo assets remain three 1000×630 damage examples from one card design. Phase 3 adds 10 fictional source records and 40 generated image cases using that same template; it does not add 10 layouts or real documents.
- The generator stores source field values in `tools/make_demo_docs.py`; its masks describe generated pixel damage. Phase 3 keeps source truth and rendered edits in `evaluation/image_level_cases.json`, outside production pipeline state/evidence.
- `tools/tune_demo_docs.py` remains a damage-cut inspection utility; `tools/make_figures.py` creates README/sample exports. The Phase 3 runner is `tools/run_image_level_evaluation.py` and its generated images/masks are stored under `evaluation/image_level_assets/`.
- Tests cover controlled classifier cases, the strict expected-failure false-recovery case, live OCR demos, the new image-evaluation harness, AppTest flows, upload security, HTML/PDF escaping, bounded caching, and seeded generator reproducibility.

## Duplicated, unused, or misleading areas to manage

- **Not duplicated:** there is one production OCR path and one production classifier. Do not add a second classifier/evidence store without showing a specific gap.
- `config.FIELD_ORDER` repeats the order already represented in the template field mapping; labels/display values also have repeated sources. This is low priority and should not be casually cleaned during research work.
- `src/fields.py` contains a trailing `_unused` NumPy helper solely to justify an import; it is cleanup, not a research priority.
- `src/report.py::to_pdf_bytes(annotation_for=...)` retains an unused parameter. A previous unused style was removed during security work; avoid unrelated report refactors.
- `src/config.py::AI_TIMEOUT_SECONDS` is currently misleading because it is not passed to the SDK. The README now discloses this; either wire it with a tested SDK option in its own small change or remove/deprecate it after approval.
- `StageStatus` progress is emitted only after `ocr.extract_evidence` returns, even though that function does decode, preprocess, and OCR. Thus the “image processed” callback arrives after OCR and the immediately measured “OCR completed” stage duration is near zero; timing labels should not be used as measured per-stage performance.
- The classifier test's XFAIL is a known failure demonstration, not a successful safety guarantee. Do not remove or convert it to XPASS/normal pass without independent evidence that the case is now rejected.

## Research-critical gaps

1. **False-recovery evidence:** Phase 2 now covers substitutions, transpositions, edits, truncation, and valid-format wrong values at classifier level. Phase 3 observes end-to-end false recovery only when the rendered pixels contain an intentionally altered target value; it did not find a false recovery caused by EasyOCR disagreeing with the rendered target. A targeted image-level study of ambiguous/damaged glyphs remains open.
2. **Observation vs claim semantics:** **Phase 4 complete.** Production JSON now names `observed_value` and `claimed_value`; the existing `raw_ocr_text` and `value` interfaces remain compatibility aliases. Evidence schema, code, configuration, and template metadata are explicit.
3. **Evidence completeness:** **Phase 5 complete for current validators.** Deterministic reason codes and PASS/FAIL/UNKNOWN/NOT_APPLICABLE records cover existing shape checks, ID components, DOB year representation/calendar validity, and scoped field structure. There is no district registry, source-truth check, or recognition-agreement evidence.
4. **Ground-truth independence:** Phase 2/3 truth is in evaluation-only manifests and is compared after classification. The image-level set uses one fixed template and synthetic source values; it is not a held-out or real-world dataset.
5. **Measured outcomes are pilot-only:** Phase 3 defines denominators for exact match, coverage, and false recovery among recovered cases. Those counts are descriptive for 40 selected rows; there is no CER study, independent dataset, risk-coverage analysis, calibration, or statistically supported claim.
6. **Generalization:** one synthetic template, fixed normalized regions, one OCR engine and one preprocessing pass. The current system cannot support claims about other layouts, real damaged identity documents, or field populations.
7. **Human auditability:** current acknowledgements are not an auditable human assertion and must never overwrite OCR/system evidence if later upgraded.

## Cosmetic/product-only gaps (lower priority)

The current page could eventually be reorganized into an evidence-review workspace with clearer observed/claimed/status/reason/provenance separation and durable review actions. The UI is already functional and visually styled; more charts, menus, frameworks, or decorative AI do not reduce false recovery. Keep UX changes tied to reviewer comprehension or measured usability.

## Priority recommendation

| Priority | Recommendation / current status | Gate before moving on |
|---|---|---|
| **P0.1** | **Complete through Phase 5.** CPU test environment, full suite, security suite, and UI/AppTest flows were verified. | Keep the strict false-recovery XFAIL and existing security controls unchanged. |
| **P0.2** | **Complete (Phase 2).** Controlled evaluation-only classifier cases cover the identified string error families; classifier thresholds were not changed. | Results are characterization, not a real-world rate. |
| **P0.3** | **Complete (Phase 4).** A versioned observed/claimed evidence contract, typed existing evidence, deterministic reason codes, AI separation, and compatible JSON/PDF exports are implemented. | Preserve the three status semantics and `PARTIAL`/`UNRECOVERABLE` → null claim invariant. |
| **P0.4** | **Complete (Phase 5).** Calendar-invalid DOBs now abstain; ID/name/district/address checks remain structural and do not claim source truth. | Phase 6 should only investigate residual format-valid substitutions after review; do not infer correctness from validation PASS. |
| **P0.5** | **Complete narrowly (Phase 3).** The seeded generator produced a 10-source, 40-image, one-template end-to-end pilot. | All false recoveries matched deliberately rendered wrong target text; OCR-induced false recovery of unchanged glyphs remains unshown. |
| **P0.6** | **Partial.** The pilot defines exact-match, coverage, and false-recovery denominators; risk-coverage, CER, calibration, and ablations remain future work. | Expand only after an image-level study that isolates actual OCR misrecognition. |
| **P1** | Compare controlled preprocessing variants using current EasyOCR; consider recognition consistency only after ambiguous-image cases establish value. Keep human verification and report provenance separate. | Justify added passes/complexity with measured results and keep human assertions separate. |
| **P2** | Evidence-review UX, evaluation dashboard, registration, template registry, and performance instrumentation. | Backend contracts and evaluation must be stable first. |
| **P3** | Independent OCR engine, statistical calibration, more templates, or a labelled real-world sample. | Only if controlled data shows value and ethics/security review supports it. |

## Phase 1 baseline verification (historical; before Phases 2–3)

The ignored `.venv/` was absent in the restored workspace, and the global interpreter lacked Streamlit, EasyOCR, Torch, ReportLab, Google GenAI, and python-dotenv. A new local `.venv/` was created; CPU-only Torch/torchvision wheels were installed first, followed by `requirements.txt`. This avoided CUDA package resolution. The OCR weights downloaded on the first full test run into the configured external EasyOCR cache; no demo assets were regenerated or changed by the test run.

| Check | Result |
|---|---|
| `pytest -q` | **54 passed, 1 strict XFAIL, 20 warnings** in 196.92 s |
| AppTest/UI flows | Included in the full suite; all passed |
| Live Streamlit app / HTTP smoke | PASS; running on `0.0.0.0:8501`; HTTP 200 under CORS/XSRF-enabled config |
| Security suite (`pytest -q tests/test_security.py`) | **14 passed** in 0.87 s |
| `python -m compileall -q -x '(^|/)(\.venv|\.git)(/|$)' .` | PASS (entire repository, excluding the virtual environment and Git internals) |
| `python -m pip check` | PASS; no broken requirements |
| `git diff --check` | PASS |
| Focused Ruff on classifier/security/AppTest/pipeline tests and tuning helper | PASS |
| `ruff check app.py src tests tools` (Ruff 0.16.10, default rules) | **23 findings remain**; this is not a clean repository-wide lint pass |

Environment observed: Python 3.13.14; Streamlit 1.64.0; OpenCV 4.10.0; EasyOCR 1.7.2; CPU Torch 2.14.1+cpu / torchvision 0.29.1+cpu; NumPy 2.5.2; Pillow 12.3.0; ReportLab 5.0.1; Google GenAI 2.27.0; python-dotenv 1.2.4; pytest 9.1.1; Ruff 0.16.10. Requirements leave several of these versions unpinned, so this records the observed environment, not a lockfile or universal reproducibility guarantee.

The XFAIL is the explicit known case: OCR returns `DX-48281` at high confidence for controlled truth `DX-48291`, and the current format/damage rules still permit recovery. It remains visible and unchanged. No classifier threshold, evidence result, or production behavior was altered during Phases 0–1.

## Latest verification after Phase 5

| Check | Result |
|---|---|
| `pytest -q` | **98 passed, 1 strict XFAIL, 20 warnings** in 205.37 s |
| Security suite (`pytest -q tests/test_security.py`) | **14 passed** |
| Focused validation/evidence/replay tests | **29 passed**; the strict false-recovery XFAIL also remains in the full suite |
| AppTest/UI and pipeline tests | Included in the full suite; passed. No UI redesign or AppTest-affecting UI changes were made. |
| Separate Phase 5 replay | 73 saved observations; Phase 2/3 JSON and image-tree hashes match historical inputs; no OCR rerun and no historical artifact rewrite. Replay results are in `evaluation/phase_5_validation_replay.json`. |
| Focused Ruff on Phase 5-touched source, tests, and replay tools | PASS |
| Repository-wide Ruff | 16 findings remain in files untouched by Phase 5; intentionally out of scope |
| Repository `compileall`, `pip check`, `git diff --check`, Phase 2/3 and Phase 5 JSON parsing | PASS |
| Security / ground-truth boundary | Security suite passes; production validation accepts observations/template specs only. Evaluation truth is used only by the offline replay for outcome labeling after validation. No new secret or ground-truth value was added to production evidence. |

## Stop condition

Phases 0–5 are complete. The four-state validation contract and AI boundary passed verification, the three status semantics and strict false-recovery XFAIL remain, Phase 2/3 historical artifacts are unchanged, and Phase 5 wrote only its separate replay analysis. **Stop for review; do not start Phase 6 or commit.**
