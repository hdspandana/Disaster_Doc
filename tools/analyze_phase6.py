"""Build the Phase 6 offline root-cause analysis from frozen Phase 2/3/5 artifacts.

Usage (from the repository root):
    python tools/analyze_phase6.py
    python tools/analyze_phase6.py --check

This tool reads saved manifests/results and the frozen Phase 5 replay. It does not open
images, run OCR, call the production pipeline, alter classifier inputs, or write historical
artifacts. Its default output is the new versioned Phase 6 artifact only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PHASE2_CASES = ROOT / "evaluation" / "false_recovery_cases.json"
PHASE2_RESULTS = ROOT / "evaluation" / "false_recovery_results.json"
PHASE3_CASES = ROOT / "evaluation" / "image_level_cases.json"
PHASE3_RESULTS = ROOT / "evaluation" / "image_level_results.json"
PHASE5_REPLAY = ROOT / "evaluation" / "phase_5_validation_replay.json"
PHASE3_ASSETS = ROOT / "evaluation" / "image_level_assets"
OUTPUT_PATH = ROOT / "evaluation" / "phase_6_root_cause_v1.json"

ANALYSIS_VERSION = "phase-6-root-cause-v1"
SOURCE_FILES = (
    "src/config.py",
    "src/fields.py",
    "src/classifier.py",
    "src/validation.py",
    "src/ocr.py",
    "src/pipeline.py",
    "src/evidence.py",
)

P2_CAUSE = {
    "single_digit_substitution": (
        "FORMAT_VALID_ID_SUBSTITUTION",
        "A digit was replaced in the injected ID string; the two-letter/hyphen/five-digit shape remains valid.",
    ),
    "multiple_digit_substitution": (
        "FORMAT_VALID_ID_SUBSTITUTION",
        "Multiple serial digits were changed, but the fixed-length digit pattern still passes.",
    ),
    "transposition": (
        "FORMAT_VALID_ID_TRANSPOSITION",
        "Serial digits were transposed without violating the template's character or length rules.",
    ),
    "valid_format_incorrect_value": (
        "FORMAT_VALID_OR_PLAUSIBLE_VALUE_MISMATCH",
        "The injected value is structurally plausible; available validators do not establish identity, date-of-birth truth, place existence, or address truth.",
    ),
    "character_substitution": (
        "INJECTED_CHARACTER_CONFUSION",
        "A character was substituted in a controlled string. No OCR engine or pixels produced this reading; the permitted field pattern accepts the resulting characters.",
    ),
}

EXPECTED_RESIDUAL_IDS = {
    "phase_2_controlled_classifier": {
        "id_single_digit_substitution_conf_060_clean",
        "id_single_digit_substitution_conf_080_clean",
        "id_single_digit_substitution_conf_094_clean",
        "id_single_digit_substitution_conf_099_clean",
        "id_single_digit_substitution_damage_moderate",
        "id_multiple_digit_substitution",
        "id_transposition",
        "id_valid_format_wrong_value",
        "dob_valid_format_wrong_value",
        "name_ocr_confusion_O_to_Q",
        "name_plausible_wrong_value",
        "district_plausible_wrong_value",
        "address_ocr_confusion_S_to_5",
        "address_ocr_confusion_I_to_1",
        "address_plausible_wrong_value",
    },
    "phase_3_synthetic_image_pipeline": {
        "source_01_mild",
        "source_01_medium",
        "source_02_mild",
        "source_02_medium",
        "source_03_mild",
        "source_03_medium",
        "source_04_mild",
        "source_06_mild",
        "source_06_medium",
        "source_07_mild",
    },
}

P2_FIELD_CAUSE = {
    "valid_format_incorrect_value": {
        "id_number": (
            "FORMAT_VALID_ID_NO_CHECKSUM_OR_REGISTRY",
            "The alternative ID satisfies the template's two-letter/five-digit structure. There is no checksum, fixed authoritative prefix, or registry to compare it with the source record.",
        ),
        "dob": (
            "CALENDAR_VALID_DOB_NO_SOURCE_COMPARISON",
            "The alternative DOB is a possible calendar date under the template. Calendar validity cannot establish which date belongs to the source record.",
        ),
        "name": (
            "PLAUSIBLE_NAME_NO_IDENTITY_REFERENCE",
            "The alternative name satisfies the field pattern; no independent identity source or name registry is used.",
        ),
        "district": (
            "PLAUSIBLE_DISTRICT_NO_REGISTRY",
            "The district-like string satisfies the field pattern; there is no controlled district vocabulary or external registry.",
        ),
        "address": (
            "PLAUSIBLE_ADDRESS_NO_EXISTENCE_CHECK",
            "The address-like string satisfies the field pattern; no address registry, geocoder, or existence check is used.",
        ),
    },
    "character_substitution": {
        "name": (
            "INJECTED_NAME_CHARACTER_SUBSTITUTION",
            "The controlled string substitutes one alphabetic character. This was not OCR output; the resulting name still satisfies the configured pattern and token checks.",
        ),
        "address": (
            "INJECTED_ADDRESS_CHARACTER_SUBSTITUTION",
            "The controlled string substitutes an `S/5` or `I/1` character. This was not OCR output; the address pattern permits letters and digits.",
        ),
    },
}

P3_CAUSE = {
    "single_digit_substitution_9_to_8": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "The generator deliberately changed one printed ID digit. Saved OCR text exactly matches the altered rendered target, not the original source value.",
    ),
    "digit_transposition_63_to_36": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "The generator deliberately transposed printed ID digits. Saved OCR text exactly matches the altered rendered target, not the original source value.",
    ),
    "valid_format_wrong_identifier": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "A different format-valid synthetic ID was deliberately printed. OCR matches that visible target; no source-ID registry or checksum exists.",
    ),
    "plausible_wrong_name": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "A different plausible synthetic name was deliberately printed. OCR matches the rendered target; no name identity source is consulted.",
    ),
    "plausible_wrong_district": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "A different synthetic district string was deliberately printed. OCR matches the rendered target; no district registry is available or added.",
    ),
    "valid_date_digit_substitution": (
        "VISIBLE_TARGET_INTENTIONALLY_CHANGED",
        "A different calendar-valid date was deliberately printed. Calendar validation can reject impossible dates but cannot prove the source DOB.",
    ),
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _asset_tree_hash(root: Path) -> tuple[int, str]:
    rows = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return len(rows), _sha256_bytes(payload)


def _index(rows: list[dict[str, Any]], key: str = "case_id") -> dict[str, dict[str, Any]]:
    indexed = {row[key]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"duplicate {key} in evaluation artifact")
    return indexed


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _confusion(rows: list[dict[str, Any]], label_matches: Any) -> dict[str, Any]:
    """Classify whether each saved OCR candidate exactly matches the named truth label."""
    tp = fp = fn = tn = 0
    recovered_count = 0
    for row in rows:
        label_positive = bool(label_matches(row))
        prediction_positive = row["classification"]["status_after_phase_5"] == "RECOVERED"
        recovered_count += int(prediction_positive)
        if label_positive and prediction_positive:
            tp += 1
        elif not label_positive and prediction_positive:
            fp += 1
        elif label_positive and not prediction_positive:
            fn += 1
        else:
            tn += 1
    total = len(rows)
    return {
        "positive_label_definition": "saved field observation exactly equals the named reference value",
        "positive_prediction_definition": "status_after_phase_5 == RECOVERED",
        "matrix_semantics": {
            "TP": "candidate equals the named reference and status is RECOVERED",
            "FP": "candidate differs from the named reference but status is RECOVERED",
            "FN": "candidate equals the named reference but status is not RECOVERED",
            "TN": "candidate differs from the named reference and status is not RECOVERED",
        },
        "matrix": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
        "recovered_claim_coverage": _ratio(recovered_count, total),
        "exact_correct_recovery_rate_over_all_cases": _ratio(tp, total),
        "case_count": total,
        "limitations": (
            "Curated synthetic cases only. This is not a real-world accuracy estimate. "
            "For Phase 2, the candidate is injected classifier input, not OCR output."
        ),
    }


def _case_row(
    phase: str,
    replay_row: dict[str, Any],
    source_case: dict[str, Any],
    source_result: dict[str, Any],
) -> dict[str, Any]:
    field_name = replay_row["field_name"]
    observed = replay_row["observed_value"]
    claim = replay_row["claimed_value_after"]
    if phase == "phase_2_controlled_classifier":
        error_type = source_result["error_type"]
        cause_code, cause_text = P2_FIELD_CAUSE.get(error_type, {}).get(
            field_name,
            P2_CAUSE.get(
                error_type,
                ("CONTROLLED_INPUT_NOT_OCR", "The candidate was injected into a classifier harness; no image or OCR engine produced it."),
            ),
        )
        evidence_rows = source_result.get("evidence", [])
        source_info = {
            "source_id": None,
            "case_id": source_case["case_id"],
            "image_path": None,
            "image_sha256": None,
            "error_or_edit_type": error_type,
            "confusion_pair": source_result.get("confusion_pair"),
            "damage_profile": source_result.get("damage_profile"),
            "sweep": source_result.get("sweep"),
            "severity": None,
            "preprocessing_variant": None,
        }
        evidence_origin = "controlled classifier fixture; injected observation placeholder, not OCR output"
        confidence = source_result.get("ocr_confidence_input")
        confidence_bucket = source_result.get("confidence_bucket")
        damage = source_result.get("damage_evidence")
        rendered_target = None
        root_cause_category = "PHASE2_CONTROLLED_CANDIDATE_VS_SOURCE_LABEL"
        scope_note = (
            "This is a synthetic classifier input with a test bbox/confidence/damage profile. "
            "There is no rendered image, raw OCR engine output, or production OCR uncertainty to inspect."
        )
        desired_scope = (
            "For a production document assessment, UNRECOVERABLE is the evidence-justified status because Phase 2 supplies no rendered document or OCR-engine output. The saved RECOVERED result is only a harness outcome under its injected-observation assumption; it is not a document-level claim and is not a source-label-driven production rule."
        )
        desired_status = "UNRECOVERABLE"
    else:
        error_type = source_case["corruption_type"]
        cause_code, cause_text = P3_CAUSE.get(
            error_type,
            ("RENDERED_TARGET_DIFFERS_FROM_SOURCE_TRUTH", "The saved OCR observation matches the deliberately rendered target, which differs from the original source label."),
        )
        field_result = source_result["field_result"]
        evidence_rows = field_result.get("evidence", [])
        source_info = {
            "source_id": source_case["source_id"],
            "case_id": source_case["case_id"],
            "image_path": source_result["image"]["path"],
            "image_sha256": source_result["image"]["sha256"],
            "damage_mask_path": source_result["image"].get("damage_mask_path"),
            "damage_mask_sha256": source_result["image"].get("damage_mask_sha256"),
            "error_or_edit_type": error_type,
            "severity": source_case["severity"],
            "seed": source_case.get("seed"),
            "preprocessing_variant": source_case.get("preprocessing_variant"),
            "saved_pipeline_summary": {
                "ocr_engine": source_result["pipeline"].get("ocr_engine"),
                "preprocess_steps": source_result["pipeline"].get("preprocess_steps"),
                "runtime_seconds": source_result["pipeline"].get("runtime_seconds"),
                "page_obscured_fraction_detected": source_result["pipeline"].get("page_obscured_fraction_detected"),
            },
        }
        evidence_origin = "saved Phase 3 EasyOCR field evidence; image/OCR not rerun for Phase 6"
        confidence = field_result.get("ocr_confidence")
        confidence_bucket = field_result.get("confidence_bucket")
        damage = field_result.get("damage_evidence")
        rendered_target = replay_row.get("rendered_target_value")
        root_cause_category = "PHASE3_RENDERED_TARGET_VS_ORIGINAL_SOURCE_LABEL"
        scope_note = (
            "The saved OCR field reading equals the deliberately edited text in the rendered image. "
            "The Phase 3 outcome label compares that text with the pre-edit synthetic source value."
        )
        desired_scope = "RECOVERED as a transcription of the visible rendered target; not verified against the original source record."
        desired_status = "RECOVERED"

    if not evidence_rows:
        raise ValueError(f"residual case {replay_row['case_id']} has no saved field evidence")
    if replay_row["outcome_after"] != "FALSE_RECOVERY":
        raise ValueError(f"case {replay_row['case_id']} is not a Phase 5 residual false recovery")

    return {
        "case_id": replay_row["case_id"],
        "phase": phase,
        "source": source_info,
        "field_name": field_name,
        "evidence_origin": evidence_origin,
        "raw_observation_or_injected_candidate": observed,
        "field_evidence": evidence_rows,
        "ocr_confidence_or_injected_score": confidence,
        "confidence_bucket": confidence_bucket,
        "damage_evidence_or_profile": damage,
        "extracted_value": observed,
        "normalized_value": None,
        "normalization_note": (
            "No distinct normalized value is represented in the frozen result. The Phase 5 validation receives the saved observation; "
            "the recorded claim is copied from that observation for these cases."
        ),
        "claimed_value_before": replay_row["claimed_value_before"],
        "claimed_value_after_phase_5": claim,
        "claim_equals_observation": claim == observed,
        "reference_values": {
            "original_synthetic_source_value": replay_row["ground_truth"],
            "rendered_target_value": rendered_target,
            "observation_equals_source": observed == replay_row["ground_truth"],
            "observation_equals_rendered_target": (
                observed == rendered_target if rendered_target is not None else None
            ),
        },
        "validation": {
            "aggregate_before_phase_5": replay_row["validation_before"],
            "aggregate_after_phase_5": replay_row["validation_after"],
            "evidence_rows_after_phase_5": replay_row["validation_evidence"],
            "pass_is_source_truth": False,
        },
        "classification": {
            "status_before_phase_5": replay_row["status_before"],
            "status_after_phase_5": replay_row["status_after"],
            "outcome_after_phase_5_against_original_source": replay_row["outcome_after"],
        },
        "root_cause_category": root_cause_category,
        "root_cause_code": cause_code,
        "root_cause_diagnosis": cause_text,
        "overconfidence_point": (
            "The classifier copies one usable candidate to RECOVERED after deterministic format, truncation, confidence, and damage gates. "
            "Those gates contain no independent observation of the original source value."
        ),
        "evidence_justified_desired_status": desired_status,
        "harness_status_after_phase_5": replay_row["status_after"] if phase == "phase_2_controlled_classifier" else None,
        "desired_status_scope_and_caveat": desired_scope,
        "evaluation_label_audit": {
            "ground_truth_supplied_to_production": False,
            "label_circularity_found": False,
            "label_scope_note": scope_note,
        },
    }


def _field_metrics(rows: list[dict[str, Any]], reference_key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["field_name"]].append(row)
    output: dict[str, Any] = {}
    for field_name, group in sorted(grouped.items()):
        outcomes = Counter(item["classification"]["outcome_after_phase_5_against_original_source"] for item in group)
        total = len(group)
        recovered = [item for item in group if item["classification"]["status_after_phase_5"] == "RECOVERED"]
        correct = sum(item["raw_observation_or_injected_candidate"] == item["reference_values"][reference_key] for item in recovered)
        output[field_name] = {
            "case_count": total,
            "outcomes_after_phase_5_against_original_source": dict(sorted(outcomes.items())),
            "recovered_count": len(recovered),
            "correct_recovery_precision_against_original_source": _ratio(correct, len(recovered)),
            "exact_source_value_recovery_rate_over_cases": _ratio(correct, total),
        }
    return output


def build_analysis() -> dict[str, Any]:
    phase2_cases = _load(PHASE2_CASES)["cases"]
    phase2_results = _load(PHASE2_RESULTS)["cases"]
    phase3_cases = _load(PHASE3_CASES)["cases"]
    phase3_results = _load(PHASE3_RESULTS)["cases"]
    phase5 = _load(PHASE5_REPLAY)

    # Assert the historical inputs are the same ones referenced by the Phase 5 record.
    input_hashes = {
        "evaluation/false_recovery_cases.json": _sha256_file(PHASE2_CASES),
        "evaluation/false_recovery_results.json": _sha256_file(PHASE2_RESULTS),
        "evaluation/image_level_cases.json": _sha256_file(PHASE3_CASES),
        "evaluation/image_level_results.json": _sha256_file(PHASE3_RESULTS),
        "evaluation/image_level_assets/tree_sha256": _asset_tree_hash(PHASE3_ASSETS)[1],
    }
    if input_hashes != phase5["input_artifact_sha256"]:
        raise ValueError("Phase 2/3 input hashes differ from the frozen Phase 5 replay")

    phase2_case_by_id = _index(phase2_cases)
    phase2_result_by_id = _index(phase2_results)
    phase3_case_by_id = _index(phase3_cases)
    phase3_result_by_id = _index(phase3_results)
    rows: list[dict[str, Any]] = []
    for replay_row in phase5["phase2_cases"]:
        if replay_row["outcome_after"] != "FALSE_RECOVERY":
            continue
        case_id = replay_row["case_id"]
        rows.append(
            _case_row(
                "phase_2_controlled_classifier",
                replay_row,
                phase2_case_by_id[case_id],
                phase2_result_by_id[case_id],
            )
        )
    for replay_row in phase5["phase3_cases"]:
        if replay_row["outcome_after"] != "FALSE_RECOVERY":
            continue
        case_id = replay_row["case_id"]
        rows.append(
            _case_row(
                "phase_3_synthetic_image_pipeline",
                replay_row,
                phase3_case_by_id[case_id],
                phase3_result_by_id[case_id],
            )
        )

    phase2_rows = [row for row in rows if row["phase"] == "phase_2_controlled_classifier"]
    phase3_rows = [row for row in rows if row["phase"] == "phase_3_synthetic_image_pipeline"]
    for phase, phase_rows in (("phase_2_controlled_classifier", phase2_rows), ("phase_3_synthetic_image_pipeline", phase3_rows)):
        found_ids = {row["case_id"] for row in phase_rows}
        if found_ids != EXPECTED_RESIDUAL_IDS[phase]:
            raise ValueError(f"{phase} residual set differs from the reviewed 25-case baseline")
        if any(row["validation"]["aggregate_after_phase_5"] != "PASS" for row in phase_rows):
            raise ValueError(f"{phase} residual contains a non-PASS validation result")
        if any(row["classification"]["status_after_phase_5"] != "RECOVERED" for row in phase_rows):
            raise ValueError(f"{phase} residual contains a non-RECOVERED status")
    if any(row["reference_values"]["observation_equals_rendered_target"] is not True for row in phase3_rows):
        raise ValueError("a Phase 3 residual no longer matches its stored rendered target")

    phase2_full_rows = [
        _full_outcome_row(phase5_row, phase2_case_by_id[phase5_row["case_id"]], phase2_result_by_id[phase5_row["case_id"]])
        for phase5_row in phase5["phase2_cases"]
    ]
    phase3_full_rows = [
        _full_outcome_row(phase5_row, phase3_case_by_id[phase5_row["case_id"]], phase3_result_by_id[phase5_row["case_id"]])
        for phase5_row in phase5["phase3_cases"]
    ]

    p2_counts = phase5["summary"]["phase2"]["outcomes_after"]
    p3_counts = phase5["summary"]["phase3"]["outcomes_after"]
    p3_target_match_count = sum(
        item["raw_observation_or_injected_candidate"] == item["reference_values"]["rendered_target_value"]
        for item in phase3_full_rows
    )

    return {
        "analysis_version": ANALYSIS_VERSION,
        "title": "DisasterDoc Phase 6 residual false-recovery root-cause analysis",
        "method": "offline_join_of_frozen_phase_2_phase_3_and_phase_5_artifacts",
        "scope": {
            "residual_false_recoveries_case_count": len(rows),
            "phase_2_residual_count": len(phase2_rows),
            "phase_3_residual_count": len(phase3_rows),
            "images_decoded_or_viewed": False,
            "asset_bytes_read_for_hashes": True,
            "ocr_rerun": False,
            "production_pipeline_run": False,
            "historical_artifacts_written": False,
        },
        "phase_5_baseline": {
            "artifact": "evaluation/phase_5_validation_replay.json",
            "artifact_sha256": _sha256_file(PHASE5_REPLAY),
            "phase_2_outcomes_before": phase5["summary"]["phase2"]["outcomes_before"],
            "phase_2_outcomes_after": p2_counts,
            "phase_3_outcomes_before": phase5["summary"]["phase3"]["outcomes_before"],
            "phase_3_outcomes_after": p3_counts,
            "source_artifact_hashes": input_hashes,
        },
        "metrics": {
            "phase_2_original_source_value": _confusion(
                phase2_full_rows,
                lambda row: row["raw_observation_or_injected_candidate"] == row["reference_values"]["original_synthetic_source_value"],
            ),
            "phase_3_original_source_value": _confusion(
                phase3_full_rows,
                lambda row: row["raw_observation_or_injected_candidate"] == row["reference_values"]["original_synthetic_source_value"],
            ),
            "phase_3_rendered_visible_text": {
                **_confusion(
                    phase3_full_rows,
                    lambda row: row["raw_observation_or_injected_candidate"] == row["reference_values"]["rendered_target_value"],
                ),
                "raw_saved_field_observation_exactly_matches_rendered_target_count": p3_target_match_count,
                "raw_saved_field_observation_exactly_matches_rendered_target_rate": _ratio(p3_target_match_count, len(phase3_full_rows)),
                "interpretation": "OCR/text extraction from the altered synthetic image, distinct from agreement with the original source record.",
            },
            "phase_2_by_field": _field_metrics(phase2_full_rows, "original_synthetic_source_value"),
            "phase_3_by_field": _field_metrics(phase3_full_rows, "original_synthetic_source_value"),
        },
        "label_circularity_audit": {
            "production_validation_or_classification_received_evaluation_labels": False,
            "phase_2_ground_truth_use": "Outcome comparison occurs after the classifier returns; the harness receives only the injected text, confidence, and damage profile.",
            "phase_3_ground_truth_use": "The pipeline receives image bytes only. Original source values and rendered targets are compared after pipeline output is saved.",
            "phase_5_ground_truth_use": "The frozen replay calls validate_field before its evaluation-only ground-truth supplier; production validator has no expected-value input.",
            "finding": "No label leakage/circularity was found. Phase 3 has a label-scope mismatch for OCR accuracy: the historical false-recovery label compares intentionally edited visible text to the original source value.",
        },
        "root_cause_categories": {
            "PHASE2_CONTROLLED_CANDIDATE_VS_SOURCE_LABEL": "Injected synthetic classifier observation is not an OCR transcript. Its mismatch with source value tests source consistency/semantic limits, not OCR error.",
            "PHASE3_RENDERED_TARGET_VS_ORIGINAL_SOURCE_LABEL": "Saved OCR matches intentionally altered visible text; the evaluator labels against the pre-edit synthetic source value.",
            "SEPARATE_ROBUSTNESS_FINDING": "A newly reproduced overlapping-alternative case is independent of all 25 historical residuals; see the Phase 6 report and regression test.",
        },
        "residual_cases": rows,
        "reviewed_source_sha256": {
            relative: _sha256_file(ROOT / relative)
            for relative in (*SOURCE_FILES, "tools/analyze_phase6.py")
        },
    }


def _full_outcome_row(replay_row: dict[str, Any], source_case: dict[str, Any], source_result: dict[str, Any]) -> dict[str, Any]:
    """Compact all-case view used only for explicitly defined descriptive metrics."""
    if replay_row["provenance"] == "phase_2_controlled_classifier_case":
        observed = source_result["observed_text"]
        target = None
    else:
        observed = source_result["field_result"]["observed_value"]
        target = source_result["evaluation"]["rendered_target_value"]
    return {
        "case_id": replay_row["case_id"],
        "field_name": replay_row["field_name"],
        "raw_observation_or_injected_candidate": observed,
        "reference_values": {
            "original_synthetic_source_value": replay_row["ground_truth"],
            "rendered_target_value": target,
        },
        "classification": {
            "status_after_phase_5": replay_row["status_after"],
            "outcome_after_phase_5_against_original_source": replay_row["outcome_after"],
        },
    }


def _render(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="new Phase 6 artifact path")
    parser.add_argument("--check", action="store_true", help="compare with the existing Phase 6 artifact without writing")
    parser.add_argument("--force", action="store_true", help="explicitly replace the Phase 6 output only; historical Phase 2/3/5 files are always protected")
    args = parser.parse_args()

    if args.check and args.force:
        parser.error("--check and --force are mutually exclusive")
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    protected = {PHASE2_CASES.resolve(), PHASE2_RESULTS.resolve(), PHASE3_CASES.resolve(), PHASE3_RESULTS.resolve(), PHASE5_REPLAY.resolve()}
    if destination.resolve() in protected:
        parser.error("refusing to overwrite a frozen Phase 2/3/5 artifact")

    rendered = _render(build_analysis())
    if args.check:
        if not destination.exists():
            print(f"missing Phase 6 artifact: {destination}", file=sys.stderr)
            return 1
        existing = destination.read_bytes()
        if existing != rendered:
            print(f"Phase 6 artifact differs from deterministic analysis: {destination}", file=sys.stderr)
            return 1
        print(f"Phase 6 artifact matches: {destination.relative_to(ROOT) if destination.is_relative_to(ROOT) else destination}")
        return 0

    if destination.exists() and not args.force:
        parser.error(f"refusing to overwrite an existing versioned artifact: {destination}; use --check or explicit --force")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    print(f"Wrote {destination.relative_to(ROOT) if destination.is_relative_to(ROOT) else destination}")
    print(f"SHA-256 { _sha256_bytes(rendered) }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
