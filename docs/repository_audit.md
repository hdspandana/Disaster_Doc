# DisasterDoc repository audit

- **Audit date:** 2026-10-02 (Asia/Kolkata)
- **Repository:** `https://github.com/hdspandana/Disaster_Doc`
- **Audited revision:** `ea0d7e9` (`main`, “Fix OpenCV deployment dependency”)
- **Scope:** Full tracked repository, application/source modules, tests, demo tools/assets, configuration, dependencies, documentation, and available security boundaries.

This is an inspection artifact, not an implementation pass. No production code or tests were intentionally changed during the audit. Test execution regenerated demo images; those artifacts were restored to the audited commit afterward.

**Status note:** Sections 2, 5, 12, and the initial conclusion record the pre-hardening state at `ea0d7e9`; their gap labels are historical. Sections 14–15 record the original recommended order. Approved follow-up changes and current verification status are recorded in §16.

## 1. Current architecture

DisasterDoc is a single-process Python/Streamlit application. The deterministic processing logic is in `src/`; the Streamlit app orchestrates display and export. Configuration and the only supported template are currently defined in Python in `src/config.py`.

```text
Browser
  └── app.py (Streamlit; upload/demo selection, session state, review, exports)
        ├── src.pipeline.run_pipeline(bytes, use_ai)
        │     ├── src.hashing                 exact-upload SHA-256
        │     ├── src.ocr                     decode + preprocess + one EasyOCR pass
        │     │     ├── src.preprocessing      OpenCV decode / resize / CLAHE / denoise
        │     │     └── Observation[]           text + bbox/polygon + OCR confidence
        │     ├── src.damage                   pixel-heuristic DamageMap
        │     ├── src.fields                   fixed-coordinate template mapping
        │     ├── src.classifier               deterministic 3-state FieldResult[]
        │     ├── src.ai_commentary (optional)  commentary after classification
        │     └── src.evidence                  EvidenceDocument / annotation / task list
        └── src.report                         JSON and optional PDF, called by app.py
```

There is no separately deployed API, database, authentication layer, task queue, or persistent case store. Uploaded bytes, images, and results are held in Streamlit session state during the session. The app's displayed “case” is one selected document, not a persistent case-management object.

### File responsibilities

| Area | Existing files | Current responsibility |
|---|---|---|
| UI | `app.py`, `.streamlit/config.toml` | Streamlit upload/demo flow, status display, evidence selection, checkbox checklist, JSON/PDF download |
| Configuration/template | `src/config.py` | thresholds, preprocessing knobs, status strings, one hard-coded `synthetic_id_v1` template, environment settings |
| Image/OCR | `src/preprocessing.py`, `src/ocr.py` | OpenCV image decode, single preprocessing path, lazy EasyOCR reader, raw OCR observations and coordinate mapping |
| Damage | `src/damage.py` | luminance/local-contrast obscuration mask and adjacent-damage probe |
| Mapping/classification | `src/fields.py`, `src/classifier.py` | fixed row-band/value-zone mapping and deterministic status/value decision |
| Evidence/audit | `src/evidence.py`, `src/hashing.py` | in-memory evidence document, SHA-256 document ID, annotated image and checklist tasks |
| Optional AI | `src/ai_commentary.py` | optional Gemini commentary after deterministic classification; no image is sent |
| Reports | `src/report.py` | JSON serialization and ReportLab PDF generation |
| Synthetic demos | `tools/make_demo_docs.py`, `tools/make_figures.py`, `tools/tune_demo_docs.py`, `demo/`, `assets/` | three synthetic damaged samples, occlusion masks, visual figures, OCR tuning helper |
| Tests | `tests/test_pipeline.py`, `tests/test_app.py` | OCR-backed acceptance tests and Streamlit `AppTest` UI flows |

## 2. Current end-to-end pipeline

The actual code path is:

1. `app.py` reads bytes from a demo image or `st.file_uploader`, stores the bytes in session state, and caches a result by `(SHA-256, use_ai)` in an unbounded session dictionary.
2. `run_pipeline` computes `sha256:<digest>` over the exact input bytes before validating/decoding the image.
3. `ocr.extract_evidence` calls `preprocessing.load_image`, then `preprocess` (optional width downscale, grayscale, CLAHE, bilateral denoise), loads a process-global EasyOCR reader lazily under a lock, and runs **one** `readtext` pass.
4. OCR text is retained as returned. OCR boxes/polygons are mapped back to original-image coordinates. The OCR confidence is an engine score, not a calibrated correctness probability.
5. `damage.build_damage_map` analyzes the original decoded image, not the OCR-preprocessed image.
6. `fields.map_fields` maps OCR observations to the one fixed template using normalized row bands, one value x-range, label anchors, fuzzy label matching, and spatial line merging.
7. `classifier.classify_all` applies usable-observation thresholds, minimum character/token counts, one regex per field, truncation-marker rules, adjacent-damage/field-obscuration checks, and an OCR-confidence floor.
8. Optional AI commentary is requested **after** field status and value have been decided. `attach_ai_commentary` only writes the `ai_commentary` slot; deterministic status/value do not depend on it.
9. `evidence.new_document` assembles fields, raw observations, audit metadata, summary counts, verification tasks, and a timestamp. `render_annotation` creates the review image.
10. Back in `app.py`, JSON and PDF bytes are generated for a successful result and offered for download.

**Progress nuance:** `ocr.extract_evidence` performs image decoding, preprocessing, and OCR before returning. The pipeline then emits “Image processed” and “OCR completed” stages. Consequently, the callback does not expose a live preprocessing stage before OCR finishes, and the separately recorded OCR-stage duration is not a meaningful OCR duration. This is an observability accuracy issue, not a classification issue.

Invalid image bytes are rejected by the decoder and do not reach the EasyOCR reader. However, there is no explicit upload validation stage for byte size, magic bytes/container consistency, or decoded pixel/dimension budgets before image decode.

## 3. Existing features

Verified in source and/or tests:

- A real deterministic pipeline exists; the system is not an LLM-driven recovery tool.
- Three states exist: `RECOVERED`, `PARTIAL`, `UNRECOVERABLE`.
- The classifier separates `raw_ocr_text` from `value`; normal classification sets `value=None` for PARTIAL and UNRECOVERABLE.
- OCR observations are typed dataclasses and retain text, bounding geometry, and confidence.
- Preprocessing is deliberately non-generative: grayscale, CLAHE, mild denoise, and optional downscale; no inpainting or text completion.
- Damage analysis is a transparent pixel heuristic and feeds field downgrade logic.
- Deterministic reason strings and a heuristic confidence bucket are exposed.
- SHA-256 of exact uploaded bytes appears as the document ID and is included in JSON/PDF metadata. The README correctly disclaims authenticity/legal verification.
- EasyOCR initialization is lazy and protected by a threading lock.
- Gemini is optional; the UI can run with no key. The AI request includes field text/context but no image. It is called after classification, and the value/status layer is not overwritten by the AI merge.
- JSON and PDF evidence exports are implemented; PDF is treated as optional if ReportLab cannot be imported.
- The Streamlit UI offers demo cards, upload, processing-stage display, annotated OCR/evidence boxes, selected-field details, verification checkboxes, raw OCR observations, and report downloads.
- Synthetic demo cards are fictional/specimen-marked. `demo/gt/` contains pixel-level occlusion masks.
- The demo generator uses fixed NumPy seeds. Two executions within the audit environment produced identical generated image/mask hashes; the checked-in demo artifacts did not match the first regeneration in this environment (see §4).

## 4. Existing tests and baseline results

Test files:

- `tests/test_pipeline.py`: OCR-backed demo status tables; PARTIAL/UNRECOVERABLE null-value invariants; raw OCR preservation; bounding-box bounds; AI-enabled/disabled classification parity; AI output/merge guardrails; hash; verification task coverage; JSON/PDF output; invalid image handling; demo regeneration check.
- `tests/test_app.py`: Streamlit `AppTest` load; demo flow; selected-field details; UI value invariant; download controls; checklist rendering; severe-demo fragment behavior.

The tests cover useful end-to-end behavior, but most pipeline expectations run live EasyOCR rather than a deterministic OCR fixture. This means OCR engine/model/runtime variation affects exact expected statuses, duration, first-run network/model downloads, and test portability.

### Baseline commands and results

Environment used for the baseline: Python 3.13.14; project requirements installed in `.venv` with CPU-only PyTorch; Ruff 0.16.10 installed as an audit tool because Ruff is not declared by the project.

| Check | Result |
|---|---|
| `python -m compileall -q app.py src tests tools` | PASS |
| `pytest -q` | **31 passed, 2 failed, 16 warnings** (33 collected; ~110 s) |
| Streamlit `AppTest` flows | All UI tests passed within the full suite |
| Live `streamlit run app.py` startup | PASS; bound on `0.0.0.0:8501` |
| `ruff check .` (Ruff 0.16.10 defaults; no project config) | **47 findings** across 15 files; Ruff is not currently part of project CI or requirements |
| `git diff --check` | PASS; generated demo changes were restored after the test |
| Existing CI workflow | None found (`.github/` is absent) |

### Two failures requiring stabilization

1. `tests/test_pipeline.py::test_expected_statuses[mild_damage]`: ground-truth/demo expectation says DOB is RECOVERED, but the installed OCR run observed `1408 1991`. The date regex requires separators between day/month/year, so the current classifier returns PARTIAL and no value. This is conservative behavior; changing the classifier merely to force the expected RECOVERED status would be unsafe. The test expectation is coupled to an OCR reading that is not stable in this run.
2. `tests/test_pipeline.py::test_demo_documents_are_synthetic_and_repeatable`: one regeneration did not byte-match the checked-in demo PNGs/masks. Two consecutive regenerations under the same audit environment **did** match each other. The likely issue is stale generated artifacts or an unrecorded generator dependency/rendering-version difference; it is not evidence that same-seed generation is nondeterministic within one fixed environment.

The 16 warnings are predominantly EasyOCR/PyTorch quantization deprecations and CPU `pin_memory` warnings. They did not cause test failures.

Ruff's 47 default findings include broad exception handling, unused values/imports/directives, import ordering, `__all__` sorting, and formatting/simplification rules. Because no Ruff configuration exists, its current default rule set is not a project-defined quality gate; findings should be triaged rather than fixed mechanically (some broad catches are deliberate fail-safe boundaries).

## 5. Existing security controls and gaps

| Control | Audit classification | Finding |
|---|---|---|
| SHA-256 provenance | WORKING | Hashes exact input bytes; code and README say it is not authenticity. |
| Upload extension filter | PARTIAL | Streamlit lists supported extensions, but this is not a server-side content validation boundary. |
| Magic-byte/container validation | MISSING | No explicit signature/container check. `cv2.imdecode` is the only decode gate. |
| Compressed byte-size cap | PARTIAL | Streamlit config sets `maxUploadSize = 25` MB; direct calls to `run_pipeline` have no corresponding byte cap. |
| Decoded dimensions/pixel budget | MISSING | Decoder checks only a 40-pixel minimum; there is no maximum width/height/pixel count before full decode. Width downscaling happens after decode. |
| Decompression-bomb protection | MISSING | No explicit decoded-pixel budget or Pillow-style bomb guard on the upload path. |
| Invalid upload reaches OCR | WORKING for decode failures | `load_image` raises before `_get_reader`/`readtext`; current test covers arbitrary non-image bytes, not malformed/polyglot containers or oversized images. |
| Uploaded file persistence | MOSTLY WORKING | App holds bytes in memory/session state rather than writing the upload to disk. The per-session result cache is unbounded and retains image/result objects. |
| Temporary report files | RISKY | `src/report.py::_png_path` uses `NamedTemporaryFile(delete=False)` for PDF images and never unlinks the paths. |
| Path traversal | LOW EXPOSURE / NOT TESTED | App does not join the user filename into an output path; demo/tool paths are fixed. No explicit traversal regression test exists. |
| HTML output escaping | RISKY | OCR/AI/user-derived strings are interpolated into `st.markdown(..., unsafe_allow_html=True)` blocks without a consistently applied HTML-escape boundary. Treat as an HTML-injection/XSS risk until fixed/tested. |
| PDF/XML escaping | RISKY | OCR text, reasons, and AI commentary are passed into ReportLab `Paragraph` markup without consistent XML escaping; malformed markup may alter output or break PDF generation. |
| User-facing errors | PARTIAL | Top-level UI fallback shows exception type, but processing-stage details can include exception messages. `.streamlit/config.toml` has `showErrorDetails = true`. |
| AI separation/output policy | PARTIAL / CORE SEPARATION WORKING | AI runs after classification and factual fields are not merged. Guardrails and tests exist. Commentary and descriptive-field “possible interpretations” are only partly constrained; they can still anchor a reviewer or imply missing values. `recovered_value`/human fields are not a first-class policy/schema concept. |
| Prompt-injection resistance | PARTIAL | Prompt instructs the model not to follow/contradict evidence and outputs are filtered, but the OCR text is embedded directly in a prompt and no adversarial prompt-injection suite exists. The deterministic boundary substantially limits factual-write impact. |
| AI opt-in/timeout | PARTIAL | AI has no key by default, but the UI toggle defaults to `ai_available()` (so it defaults on if a key is configured). `DD_AI_TIMEOUT` is read into config but is not passed to the Gemini client/request. |
| Secret handling | PARTIAL | No hard-coded API key found; `.env` and Streamlit secrets are git-ignored. `python-dotenv` is listed but never loaded; copying `.env.example` to `.env` alone does not populate `os.environ` in this code. |
| CORS/XSRF | RISKY for exposed deployments | `.streamlit/config.toml` disables both CORS and XSRF protection. Its comment says session state has nothing worth protecting, but session state contains uploaded documents and analysis results. This should be reconsidered before network/public deployment. |
| Report integrity | PARTIAL | Document SHA-256 is recorded; JSON/evidence/report hashes, schema version, tamper-evident event chain, and signatures are absent. Correctly no authenticity claim is made. |

Privacy note: with AI enabled, the code sends text/context to Gemini but does not send image bytes. The prompt context also contains the document hash and field reasons, so “only text fragments” is not the complete metadata disclosure description. AI is external and optional.

## 6. Existing evidence structures

The project already has useful typed foundations; a typed model should extend these rather than replace them wholesale:

- `src.ocr.Observation`: dataclass with observation ID, verbatim text, bbox, polygon, and OCR confidence.
- `src.ocr.OcrResult`: observations, engine label, error, elapsed time, preprocessing-step labels.
- `src.fields.MappedField`: template field key/label, raw text, mapped observations, union bbox, confidence, row band, value zone.
- `src.damage.DamageMap`: obscuration mask and estimated paper luminance; exposes region ratio and longest run.
- `src.classifier.FieldResult`: status, `value`, `raw_ocr_text`, confidence bucket, OCR confidence, observation evidence, reasons, verification need/instruction, geometric/damage summary, optional AI commentary.
- `src.evidence.EvidenceDocument`: document hash ID, processing timestamp, template name, field list, raw observations, audit, summary, checklist tasks, processing metadata, disclaimer.

Strengths:

- OCR observation vs reported `value` is already separated in data flow.
- The normal classifier path sets `value=None` for PARTIAL and UNRECOVERABLE.
- AI commentary occupies a separate optional slot.
- Template/config thresholds are centralized rather than scattered across the pipeline.

Gaps relative to a reusable evidence contract:

- These are dataclasses/dicts without a versioned schema or validation at serialization boundaries; no Pydantic models or schema migration rules exist.
- `FieldResult` calls the claim `value` rather than an explicit `claimed_value`; system observation is called `raw_ocr_text`. The distinction is conceptually present but not self-describing enough for downstream consumers.
- Reasons are human-readable strings, not stable machine-readable reason codes.
- There is no first-class per-field object combining OCR tokens/engine version, preprocessing variant/parameters, damage dimensions/mask reference, agreement, coordinate space/registration, validator results, availability/visibility evidence, and classifier/config/template versions.
- One `Observation` has no engine/model version or token-level data beyond text and one box. Only one preprocessing/OCR output exists, so no agreement data exists.
- Damage evidence is a heuristic obscuration mask/ratio; no separately measured blur, contrast, visible-character count, or occlusion/truncation event record is serialized.
- Validation evidence is not structured as PASS/FAIL/NOT_APPLICABLE/UNKNOWN; regex/minimum-length rules are embedded in classifier control flow.
- Some UI-only geometry (`evidence_bbox`, `damage_bbox`) is not included in `FieldResult.to_dict()`; reports include individual OCR boxes and field region but do not fully serialize the drawn damage probe/union geometry.
- No separate human assertion/event model exists.
- `EvidenceDocument`/report JSON has no `schema_version`.

## 7. Existing classifier logic

`src/classifier.py` applies the following deterministic gates:

1. An OCR observation is “usable” if confidence is at least `0.25` and it has at least two alphanumeric characters.
2. If no usable observation exists, classify UNRECOVERABLE and keep `value=None`.
3. For usable observations, join observed text and check the template's regex, minimum alphanumeric count, minimum token count, truncation-marker suffix, adjacent damage, fraction of obscured field zone, and OCR confidence against `0.50`.
4. If every check has no issue, classify RECOVERED and copy observed text verbatim to `value`.
5. Any issue with some usable text produces PARTIAL and `value=None`.

The thresholds are centralized in `src/config.py`. `confidence_bucket` is explicitly described as heuristic; there is no claimed calibrated probability.

**Central false-recovery weakness:** a high-confidence single-character OCR substitution can still pass a permissive format/regex check in a low-damage region. For example, a valid-looking project identifier with one digit changed still matches `^[A-Z]{2}-[0-9]{5}$`; the classifier has no independent recognition pass, known ground truth, checksum, or other signal to detect the substitution. The requested `892314`/`892814` scenario is not tested. This does not mean the current classifier is nondeterministic; it means format validity and damage absence are not truth checks.

**Field validation limitations:** validation is mostly pattern/length/token checks. DOB is regex-only, not calendar-validity/range-validated; identifiers have no documented checksum; district is not checked against a justified vocabulary; address/name patterns may be narrow. A regex PASS must not be interpreted as truth.

## 8. Existing UI

The existing Streamlit UI is a functioning single-document review experience, not a blank scaffold:

- upload plus three selectable synthetic demos;
- sidebar controls for optional AI, raw OCR boxes, and preprocessed image;
- real pipeline results (not a fake animation), status summary, annotated image and expandable original image;
- selectable field cards with observed text, reported value, OCR confidence, heuristic bucket, bounding box and deterministic reasons;
- AI commentary visibly separated and labelled advisory/possibilities;
- status-derived human verification checklist, processing log, raw OCR table, SHA-256 and downloadable JSON/PDF.

It is currently a long single-page workflow with one document/session. There is no persistent cases dashboard, case ID/date/history, explicit preflight upload/hash review, zoom/pan/crop viewer, independently toggled damage mask/field-region overlay, separate human-verification screen/event capture, report/audit workspace, or evaluation dashboard. Checklist checkboxes are transient acknowledgements in Streamlit state; they do not create reviewer-attributed events and are not serialized into the report. The current image and detail panes are useful but are not the requested three-panel evidence-review workspace.

## 9. Existing report/audit model

### JSON

`EvidenceDocument.to_dict()` and `report.to_json_bytes()` export document ID/hash, processing timestamp, template name, summary, fields, verification tasks, raw OCR observations, audit metadata, processing metadata/threshold snapshot, and disclaimer. This is a useful trace record but it is not schema-versioned or cryptographically sealed.

### PDF

`report.to_pdf_bytes()` builds a human-readable report with original/annotated image, field findings/reasons, raw OCR table, human checklist, AI section when present, code/template/OCR labels, and disclaimer. PDF output is optional when ReportLab is missing. PDF text escaping and temp-file cleanup need security/correctness attention.

### Provenance limits

The only integrity identifier is SHA-256 of the uploaded bytes. No evidence JSON hash, report hash, processing-run ID, audit-event chain, or human-event reference exists. The README's distinction between a content hash and document authenticity is correct and should be preserved.

`outputs/*.json` and `outputs/*.pdf` are ignored by `.gitignore`; the README/tool refer to sample generated reports, but there is no tracked `outputs/` directory in a fresh checkout until `tools/make_figures.py` is run.

## 10. Current limitations

- One hard-coded template and fixed normalized field geometry; unsupported documents are not explicitly rejected as `UNSUPPORTED_TEMPLATE`.
- One grayscale/CLAHE/denoise preprocessing variant and one EasyOCR pass; no recognition agreement or character/token disagreement.
- No rotation, perspective correction, registration, or confidence-aware fallback for layout mismatch.
- Pixel-heuristic damage detection can confuse shadows/texture with damage and has no labeled calibration corpus.
- No field-level ground-truth benchmark, independent train/test split, OCR CER/exact-match reporting, false-recovery metric, risk-coverage curve, baseline comparison, or ablation.
- Three demo images are illustrative samples from a common synthetic card source, not 20–30 independent clean documents or real-world validation.
- No human verification event log, reviewer identity/timestamp, immutable system-vs-human history, or anti-anchoring experiment.
- The optional AI layer can generate “possible interpretations,” which risks anchoring even though output is labelled and cannot set facts. The prompt also asks for verification wording, not solely deterministic explanation. AI usefulness has not been evaluated.
- Thresholds are heuristics; OCR confidence is not calibrated.
- No schema/config/classifier/template version family beyond a static `CODE_VERSION` and template name.
- Performance timing is incomplete/misleading around combined OCR extraction; no benchmark latency distribution or controlled evaluation exists.
- There is no `.github/`, `pyproject.toml`, Ruff config, lockfile, `Dockerfile`, or deployment configuration.

## 11. Redundant, unused, and risky areas

Confirmed or apparent cleanup candidates (do not remove without checking downstream references):

- `src/config.py::ensure_output_dir`, `FIELD_DISPLAY`, and `AI_TIMEOUT_SECONDS` are not used by current call paths; the timeout setting is especially misleading because the AI request does not consume it.
- `src/fields.py` imports NumPy only to support a trailing `_unused` helper; this is unnecessary code/dependency surface.
- `src/report.py::to_pdf_bytes(annotation_for=...)` does not use the argument; a local `banner` style is created but not used.
- `tools/tune_demo_docs.py` has unused local/imports according to Ruff and creates temporary directories without explicit cleanup.
- `config.FIELD_ORDER` separately repeats the order implied by `config.TEMPLATE["fields"]`; field labels are also repeated in the unused `FIELD_DISPLAY` map.
- README describes generated sample report paths that are absent from a clean checkout due to ignored outputs.

No evidence was found of a second production classifier or a second OCR engine. Avoid introducing a replacement architecture; extend current modules/contracts and preserve their working boundaries.

## 12. Missing P0 capabilities

1. Stabilized, reproducible baseline tests that separate deterministic classifier behavior from live OCR variability; retain the actual conservative DOB abstention observed in the baseline.
2. Regression coverage for the false-recovery example (high-confidence, pattern-valid one-character OCR substitution) and explicit PARTIAL/UNRECOVERABLE null-value invariants at serialization/UI boundaries.
3. Versioned, validated first-class field-evidence contract with stable reason codes while reusing existing dataclasses and preserving current consumers.
4. Field-specific validator results (including calendar-valid DOB; legitimate, documented ID constraints; deliberately weak address checks) with PASS/FAIL/NOT_APPLICABLE/UNKNOWN semantics.
5. Secure upload resource/content validation before expensive decode/OCR, plus HTML/PDF output escaping, safe error handling, temp cleanup, bounded session caching, and deployment-appropriate CORS/XSRF settings.
6. A seeded, metadata-preserving synthetic corruption benchmark with multiple source documents/seeds/severities; current demo generator alone is not this benchmark.
7. Actual baselines, field-level false-recovery/precision/coverage/CER/exact match, risk-coverage, latency, and a small ablation study. No results should be claimed before running it.
8. Reproducible install/test/lint CI (including an explicit CPU-vs-GPU PyTorch installation path) and a clean existing-behavior test gate.

## 13. Missing P1 capabilities

- Controlled multi-pass preprocessing/OCR agreement using the current OCR engine first; characterize correlated errors.
- Human `VerificationEvent` objects separate from system fields and persisted in reports; preserve historical system output.
- Versioned template registry and explicit safe unsupported-template outcome.
- Rotation/perspective registration only with measured confidence and safe failure behavior.
- Evidence-review workspace improvements within Streamlit (not a React rewrite): safe crop/overlay/why-status review and distinct human actions.
- Evidence/report/schema/config/template/classifier hashes/version metadata and optionally a local tamper-evident event chain, explicitly not authentication.
- Evaluation dashboard in research/developer mode.

## 14. Recommended implementation order and first implementation

### Recommended order

1. **Baseline stabilization first.** Keep the current conservative classifier result; make tests deterministic where the test is about rules, isolate live OCR smoke/evaluation tests, and make generator reproducibility test compare same-environment generations. Do not “fix” the DOB failure by relaxing a validator or forcing a RECOVERED result.
2. **Security boundary hardening before external/public use.** Add byte/signature/container checks, decompressed pixel/dimension limits, safe error surfaces, output escaping, temp cleanup, bounded session cache, and explicitly opt-in AI. Preserve the no-image-to-AI policy and status/value separation.
3. **Evidence model and reason codes.** Build on `Observation`, `MappedField`, `FieldResult`, `DamageMap`, and `EvidenceDocument`; introduce schema/config/classifier/template versions and validation evidence without an unnecessary framework rewrite.
4. **Field validation + invariants.** Add structured validator results and tests; keep validators as evidence, never truth.
5. **Benchmark/evaluation.** Build a seeded corruption dataset/generator, ground-truth metadata, baselines, false-recovery and coverage metrics, risk-coverage curve, ablations, and honest result docs.
6. **P1 measured recognition consistency, human events, template registry, registration, report integrity, and Streamlit review upgrades.** Sequence these after the evidence/evaluation contracts are stable.
7. **P2 AI effectiveness study / independent OCR / ethically approved real-world validation only if evidence shows value.**

### First small implementation after approval: baseline stabilization only

Keep this as a reviewable patch separate from the later evidence-model refactor:

- Add deterministic classifier unit tests that directly exercise valid vs malformed/ambiguous date observations, the high-confidence one-character substitution risk, and the `PARTIAL`/`UNRECOVERABLE` null-value invariant.
- Retain real EasyOCR demo/UI smoke coverage, but do not make a fixed expected status depend on an unpinned model's exact punctuation output unless the engine/model/runtime is pinned and documented.
- Correct the generator reproducibility test so it checks two same-seed generations in isolated outputs; separately report a mismatch between committed artifacts and regenerated outputs rather than mutating repository demo files during the test.
- Record the baseline environment and keep existing AI-separation, report, and security-relevant tests intact.

This patch should not change classifier thresholds or convert the observed `1408 1991` into a claimed date. The safe expected behavior is PARTIAL unless a justified, tested field-specific rule can establish a verbatim supported value.

## 15. Exact candidate files and modification risks

### First baseline-stabilization patch (recommended for approval)

| File | Planned scope | Main risk / safeguard |
|---|---|---|
| `tests/test_pipeline.py` | Separate stable rule assertions from environment-sensitive OCR output assertions; keep end-to-end real OCR smoke and all null-value/AI/report invariants. | Risk: accidentally weakening acceptance tests. Safeguard: add stronger direct tests and never accept a claimed value for malformed OCR. |
| `tests/test_classifier.py` *(new)* | Direct deterministic cases for date pattern handling, high-confidence format-valid typo risk, and status/value invariants. | Risk: overfitting to one synthetic ID format. Keep field rules explicit and label the test as a risk demonstration, not an accuracy benchmark. |
| `tests/test_demo_generation.py` *(new, or a focused replacement in `test_pipeline.py`)* | Generate to isolated temporary outputs twice with fixed seeds and compare bytes/masks. | Risk: generator tests may be slow or import platform fonts. Verify temp cleanup and do not modify checked-in demos as a side effect. |
| `README.md` *(only if behavior/test contract changes)* | Correct claims about test determinism/results and install setup after a verified test design is chosen. | Risk: documenting unverified performance/accuracy. No accuracy claims should be added. |
| `requirements.txt` *(only if pinning is selected after environment review)* | Pin the minimum generator/OCR runtime needed for a reproducible supported environment; document CPU-only Torch route. | Risk: large platform-specific dependency changes; preserve supported CPU/GPU installation options. |

No production classifier change is required to preserve the current safe DOB abstention. The next evidence-model phase will likely touch `src/ocr.py`, `src/fields.py`, `src/classifier.py`, `src/evidence.py`, `src/pipeline.py`, `src/config.py`, and `src/report.py`, plus focused tests and possibly `app.py`. Those modules are central call-path contracts; change incrementally, keep compatibility adapters where useful, and test status/value/report/UI consumers at every step.

### Higher-priority security files for the subsequent security pass

Likely targets are `src/preprocessing.py`, `src/config.py`, `app.py`, `src/report.py`, `.streamlit/config.toml`, `.env.example`, and new security tests. Risks include rejecting legitimate image encodings, changing reverse-proxy behavior, changing the PDF output layout, and invalidating existing Streamlit `AppTest` selectors. Keep upload validation before OCR and avoid disabling security protections just to simplify local preview.

## Approved phase 1 follow-up: baseline stabilization

The user approved a narrowly scoped test-stabilization pass after this audit. This is recorded separately from the pre-change baseline above.

### Changes

- `tests/test_pipeline.py`
  - The mild-demo DOB is no longer required to have one exact status from a live OCR punctuation result. A separate assertion now ensures a DOB can be RECOVERED only when the observed string matches the configured pattern and the claimed text is verbatim; every non-RECOVERED outcome still has `value=None`.
  - The seeded-generation test now writes two full generator runs into pytest temporary directories and compares all generated demo images/masks. It no longer regenerates files in the repository or assumes unpinned renderer versions reproduce the exact committed PNG bytes.
- `tests/test_classifier.py` *(new)*
  - Added deterministic checks for a well-formed observed date, malformed date formatting, and no-observation abstention.
  - Added a strict expected-failure regression for the known high-confidence, format-valid one-digit identifier substitution. It is **not fixed** by this phase; it keeps the P0 false-recovery gap visible until the classifier has independent consistency evidence.
- `README.md`
  - Replaced the stale “33 tests” claim with the measured 37-passed/1-xfailed result and described the known false-recovery XFAIL and isolated same-seed generator test.

No production logic, dependencies, demo assets, or UI/report code changed in this phase.

### Post-change verification

- `pytest -q`: **37 passed, 1 xfailed, 16 warnings** (~131 s). The XFAIL is the explicit unresolved false-recovery regression, not a hidden pass.
- `python -m compileall -q app.py src tests tools`: PASS.
- `pip check`: PASS.
- `ruff check tests/test_classifier.py tests/test_pipeline.py`: PASS. The repository-wide default Ruff run still reports **44 existing findings**; no Ruff configuration/CI is present, and the touched test files introduce no lint findings.
- Full Streamlit `AppTest` and demo pipeline tests pass as part of pytest; the live app remains available on port 8501.
- Demo-generation regression runs in temporary outputs; the tracked demo artifacts remain unchanged.
- `git diff --check`: PASS.

This phase improves test determinism and exposes a known safety limitation without claiming to solve it. The next implementation phase remains pending user approval.

---

**Initial audit conclusion (pre-hardening):** DisasterDoc already has a useful deterministic core, explicit abstention semantics, synthetic demo workflow, and optional post-classification AI separation. The largest research weakness is the absence of measured false-recovery evaluation and recognition consistency; the largest immediate engineering risks were unvalidated image resource/content limits, unescaped user/model text in HTML/PDF rendering, unbounded session caching, and uncleaned PDF temp files. The original test baseline had two failures; after the approved stabilization pass, deterministic/regression tests passed and one known false-recovery test remained explicitly XFAIL.

## 16. Approved phase 2 follow-up: security hardening

The user approved a security-hardening phase after baseline stabilization. Implementation is complete and validated; no evidence-model, classifier, benchmark, or framework rewrite was started.

### Changes

- `src/config.py`: defined a 25 MiB upload cap, 25 million-pixel image cap, 10,000 px per-dimension cap, and one-result session-cache limit. Local `.env` loading now uses the already-declared `python-dotenv` dependency and `override=False` so explicit process environment variables take precedence.
- `src/preprocessing.py`: accepts only PNG, JPEG, WebP, BMP, and TIFF signatures; confirms Pillow's detected container matches the signature; rejects oversized, too-small, over-dimension, over-pixel, malformed, or decompression-bomb-warning inputs before OpenCV decoding. OpenCV decode failures have generic user-facing messages.
- `app.py`: added HTML escaping for dynamic OCR, AI, filename, and report metadata before unsafe HTML rendering; displays OCR-derived checklist tasks as plain text rather than Markdown; generic processing/PDF failures no longer expose exception details; uses content SHA-256 plus AI flag as the session-cache key and evicts old full-resolution results (one retained); AI commentary is off by default; upload and outbound-AI privacy notes now state supported formats/limits and the request context accurately.
- `src/ocr.py` and `src/pipeline.py`: hide engine/decoder exception details from the browser; the direct `run_pipeline` default is now `use_ai=False` as well as the UI default.
- `src/report.py`: escapes dynamic ReportLab paragraph content and embeds image bytes from memory instead of writing undeleted named PNG files.
- `.streamlit/config.toml`: explicitly enables CORS and XSRF protection, sets the 25 MB upload limit, and disables detailed client error output.
- `.env.example`, `README.md`: clarified opt-in AI, local secret loading, upload bounds, outbound AI fields/metadata, and the latest measured test outcome without adding accuracy claims.
- `tools/tune_demo_docs.py`: wraps generated OCR-tuning files in `TemporaryDirectory` for cleanup.
- `tests/test_security.py` *(new)*: covers all five supported formats, upload-byte cap, unknown/mismatched signatures, pixel/dimension/decompression-bomb rejection before OCR/decode, generic invalid-upload path, HTML text escaping, PDF escaping, and temporary-file cleanup.
- `tests/test_app.py` and `tests/test_pipeline.py`: cover AI-off defaults, bounded result cache, plain-text verification display, pipeline default, and disable external AI during tests even when a developer has a local `.env` key.

The deterministic 3-state classifier and its `PARTIAL`/`UNRECOVERABLE` null-value behavior were not changed. No accuracy, benchmark, or real-world performance results are claimed.

### Post-change verification

- `pytest -q`: **54 passed, 1 xfailed, 20 warnings** (~146 s). The strict XFAIL remains the known high-confidence, format-valid identifier-substitution limitation; security hardening did not resolve it.
- Focused security/UI regression run: **16 passed, 6 warnings**.
- `python -m compileall -q app.py src tests tools`: PASS.
- `pip check`: PASS.
- `git diff --check`: PASS.
- `ruff check app.py src tests tools` (Ruff 0.16.10 defaults): **23 findings remain**, primarily broad exception boundaries, sorting/simplification rules, and existing unused unpacked values. Ruff is not configured as a repository gate; do not treat this as a clean lint pass. New security tests and the modified tuning helper introduce no remaining findings.
- Streamlit AppTest/demo flows pass within pytest. Live Streamlit is running on port 8501; HTTP smoke check returned 200. The CORS/XSRF-enabled preview started without a platform preview warning.

### Remaining risks and follow-up

- A 25 MP image can still consume substantial memory during decoding, annotation, and PDF generation; one-result caching bounds retained sessions but does not cap concurrent sessions or request rate. Public deployment still needs hosting-level concurrency/rate controls and resource monitoring.
- `DD_AI_TIMEOUT` is still defined in config but is not passed into the Gemini SDK request; timeout enforcement remains a separate security follow-up. AI is opt-in, but any enabled request is external.
- CORS/XSRF were smoke-tested through this preview, not through every reverse-proxy deployment. Preserve the protections and configure proxy origin/forwarding correctly.
- Generic client errors improve disclosure safety but reduce immediate diagnostics; future operational logging should be server-side and avoid document contents, secrets, or paths.
- No authentication, durable case storage, benchmark corpus, or real-world accuracy validation was added.
- The false-recovery XFAIL remains open. The next major phase is pending explicit user approval.
