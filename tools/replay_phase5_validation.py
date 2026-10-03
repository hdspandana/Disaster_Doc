"""Replay frozen Phase 2/3 observations through Phase 5 validators, evaluation-only.

Run from the repository root:
    .venv/bin/python tools/replay_phase5_validation.py

This reads the existing Phase 2 case/results and Phase 3 case/results artifacts, applies
only ``src.validation.validate_field`` to stored OCR observations, and writes a separate
Phase 5 analysis. It does not run OCR, call ``run_pipeline``, or rewrite Phase 2/3 files.
Ground truth is read only after each validator returns, for outcome labeling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config
from src.validation import validate_field

PHASE2_CASES = ROOT / "evaluation" / "false_recovery_cases.json"
PHASE2_RESULTS = ROOT / "evaluation" / "false_recovery_results.json"
PHASE3_CASES = ROOT / "evaluation" / "image_level_cases.json"
PHASE3_RESULTS = ROOT / "evaluation" / "image_level_results.json"
PHASE3_ASSETS = ROOT / "evaluation" / "image_level_assets"
OUTPUT_PATH = ROOT / "evaluation" / "phase_5_validation_replay.json"
OUTCOME_NAMES = (
    "CORRECT_ABSTENTION",
    "CORRECT_RECOVERY",
    "FALSE_RECOVERY",
    "INCORRECT_REJECTION",
)
VALIDATION_NAMES = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _asset_tree_hash(root: Path) -> tuple[int, str]:
    rows = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return len(rows), _sha256_bytes(payload)


def _repository_state() -> dict[str, Any]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "worktree_dirty": None}
    return {"revision": revision, "worktree_dirty": dirty}


def _normalize_old_validation(value: str | None) -> str:
    if value in ("PASS", "PASSED"):
        return "PASS"
    if value in ("FAIL", "REJECTED"):
        return "FAIL"
    if value == "UNKNOWN":
        return "UNKNOWN"
    if value == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    return "UNKNOWN"


def _outcome(status: str, claim: str | None, observed: str | None, ground_truth: str) -> str:
    if status == config.STATUS_RECOVERED:
        return "CORRECT_RECOVERY" if claim == ground_truth else "FALSE_RECOVERY"
    return "INCORRECT_REJECTION" if observed == ground_truth else "CORRECT_ABSTENTION"


def _case_after_validation(
    *,
    case_id: str,
    field_name: str,
    observed_value: str | None,
    old_status: str,
    old_claim: str | None,
    old_validation: str | None,
    ground_truth_supplier: Callable[[], str],
    old_outcome: str,
    provenance: str,
    rendered_target_value: str | None = None,
) -> dict[str, Any]:
    # The production validator receives only field name, observation, and template spec.
    validation = validate_field(field_name, observed_value, config.TEMPLATE["fields"][field_name])

    # Defer reading the evaluation label until validation has returned. It never influences
    # validation or the proposed deterministic status/claim.
    ground_truth = ground_truth_supplier()
    status_after = old_status
    claim_after = old_claim
    changed_reason = None
    calendar_invalid = (
        field_name == "dob"
        and any(
            item.validator == "dob_calendar" and item.result == "FAIL"
            for item in validation.evidence
        )
    )
    if old_status == config.STATUS_RECOVERED and calendar_invalid:
        status_after = config.STATUS_PARTIAL
        claim_after = None
        changed_reason = "CALENDAR_DATE_INVALID"

    outcome_after = _outcome(status_after, claim_after, observed_value, ground_truth)
    evidence = [item.to_dict() for item in validation.evidence]
    return {
        "case_id": case_id,
        "provenance": provenance,
        "field_name": field_name,
        "observed_value": observed_value,
        "ground_truth": ground_truth,
        "rendered_target_value": rendered_target_value,
        "validation_before": _normalize_old_validation(old_validation),
        "validation_after": validation.result,
        "validation_evidence": evidence,
        "status_before": old_status,
        "claimed_value_before": old_claim,
        "outcome_before": old_outcome,
        "status_after": status_after,
        "claimed_value_after": claim_after,
        "outcome_after": outcome_after,
        "classification_change_reason": changed_reason,
        "validation_pass_is_correctness_proof": False,
    }


def _phase_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    validation_before = Counter(row["validation_before"] for row in rows)
    validation_after = Counter(row["validation_after"] for row in rows)
    outcome_before = Counter(row["outcome_before"] for row in rows)
    outcome_after = Counter(row["outcome_after"] for row in rows)
    evidence_results = Counter(
        evidence["result"] for row in rows for evidence in row["validation_evidence"]
    )
    changes = [row for row in rows if row["status_before"] != row["status_after"]]
    return {
        "case_count": len(rows),
        "validation_by_case_before": {name: validation_before.get(name, 0) for name in VALIDATION_NAMES},
        "validation_by_case_after": {name: validation_after.get(name, 0) for name in VALIDATION_NAMES},
        "validator_evidence_result_rows_after": {
            name: evidence_results.get(name, 0) for name in VALIDATION_NAMES
        },
        "outcomes_before": {name: outcome_before.get(name, 0) for name in OUTCOME_NAMES},
        "outcomes_after": {name: outcome_after.get(name, 0) for name in OUTCOME_NAMES},
        "deltas": {
            name: outcome_after.get(name, 0) - outcome_before.get(name, 0) for name in OUTCOME_NAMES
        },
        "newly_rejected_case_ids": [row["case_id"] for row in changes],
        "classification_changed_count": len(changes),
        "false_recoveries_remaining": [
            row["case_id"] for row in rows if row["outcome_after"] == "FALSE_RECOVERY"
        ],
        "incorrect_rejections_after": [
            row["case_id"] for row in rows if row["outcome_after"] == "INCORRECT_REJECTION"
        ],
    }


def build_replay() -> dict[str, Any]:
    phase2_cases = json.loads(PHASE2_CASES.read_text(encoding="utf-8"))["cases"]
    phase2_results = json.loads(PHASE2_RESULTS.read_text(encoding="utf-8"))["cases"]
    phase2_result_map = {row["case_id"]: row for row in phase2_results}
    if set(phase2_result_map) != {case["case_id"] for case in phase2_cases}:
        raise ValueError("Phase 2 historical cases and results do not contain the same case IDs")

    phase2_rows: list[dict[str, Any]] = []
    for case in phase2_cases:
        old = phase2_result_map[case["case_id"]]
        phase2_rows.append(
            _case_after_validation(
                case_id=case["case_id"],
                field_name=case["field_name"],
                observed_value=case.get("observed_text"),
                old_status=old["status"],
                old_claim=old.get("claimed_value"),
                old_validation=old.get("validation", {}).get("validator_result"),
                ground_truth_supplier=lambda case=case: case["ground_truth"],
                old_outcome=old["outcome"],
                provenance="phase_2_controlled_classifier_case",
            )
        )

    phase3_cases = json.loads(PHASE3_CASES.read_text(encoding="utf-8"))["cases"]
    phase3_results = json.loads(PHASE3_RESULTS.read_text(encoding="utf-8"))["cases"]
    phase3_result_map = {row["case_id"]: row for row in phase3_results}
    if set(phase3_result_map) != {case["case_id"] for case in phase3_cases}:
        raise ValueError("Phase 3 case manifest and saved results do not contain the same case IDs")

    phase3_rows: list[dict[str, Any]] = []
    for case in phase3_cases:
        old = phase3_result_map[case["case_id"]]
        old_field = old["field_result"]
        old_eval = old["evaluation"]
        phase3_rows.append(
            _case_after_validation(
                case_id=case["case_id"],
                field_name=case["field_name"],
                observed_value=old_field.get("observed_value"),
                old_status=old_field["status"],
                old_claim=old_field.get("claimed_value"),
                old_validation=old_field.get("validation", {}).get("result"),
                ground_truth_supplier=lambda old_eval=old_eval: old_eval["ground_truth"],
                old_outcome=old_eval["outcome"],
                provenance="phase_3_saved_pipeline_ocr_observation",
                rendered_target_value=old_eval.get("rendered_target_value"),
            )
        )

    all_rows = phase2_rows + phase3_rows
    phase2_summary = _phase_summary(phase2_rows)
    phase3_summary = _phase_summary(phase3_rows)
    combined_summary = _phase_summary(all_rows)
    all_evidence_results = Counter(
        evidence["result"] for row in all_rows for evidence in row["validation_evidence"]
    )
    asset_count, asset_tree_hash = _asset_tree_hash(PHASE3_ASSETS)
    repository_state = _repository_state()
    input_hashes = {
        path.relative_to(ROOT).as_posix(): _sha256_file(path)
        for path in (PHASE2_CASES, PHASE2_RESULTS, PHASE3_CASES, PHASE3_RESULTS)
    }
    input_hashes["evaluation/image_level_assets/tree_sha256"] = asset_tree_hash
    versions = {
        "code_version": config.CODE_VERSION,
        "evidence_schema_version": config.EVIDENCE_SCHEMA_VERSION,
        "config_version": config.CONFIG_VERSION,
        "template_version": config.TEMPLATE_VERSION,
        "template": config.TEMPLATE_NAME,
        "repository_revision": repository_state["revision"],
        "repository_worktree_dirty": repository_state["worktree_dirty"],
        "source_sha256": {
            path: _sha256_file(ROOT / path)
            for path in (
                "src/validation.py",
                "src/classifier.py",
                "src/config.py",
                "tools/replay_phase5_validation.py",
            )
        },
    }
    return {
        "schema_version": 1,
        "title": "DisasterDoc Phase 5 field-validation replay",
        "method": "offline_replay_of_saved_ocr_observations",
        "scope": (
            "Evaluation-only replay of 33 saved Phase 2 controlled observations and 40 saved Phase 3 OCR outputs. "
            "No OCR, image pipeline, or Phase 2/3 artifact regeneration was performed."
        ),
        "ground_truth_policy": (
            "Ground truth is deferred until after each production validation call and used only for evaluation "
            "outcome labeling. It is never passed to src.validation, src.classifier, or production evidence."
        ),
        "classification_policy": (
            "All prior statuses/claims are retained except a formerly RECOVERED DOB whose new deterministic "
            "calendar validator returns FAIL; that case is projected to PARTIAL with no claim. No other "
            "classifier gate or threshold is changed by this replay."
        ),
        "input_artifact_sha256": input_hashes,
        "phase3_asset_file_count": asset_count,
        "versions": versions,
        "summary": {
            "case_count": len(all_rows),
            "validation_by_case_before": combined_summary["validation_by_case_before"],
            "validation_by_case_after": combined_summary["validation_by_case_after"],
            "validator_evidence_result_rows_after": {
                name: all_evidence_results.get(name, 0) for name in VALIDATION_NAMES
            },
            "phase2": phase2_summary,
            "phase3": phase3_summary,
        },
        "phase2_cases": phase2_rows,
        "phase3_cases": phase3_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="separate Phase 5 replay JSON output")
    args = parser.parse_args()
    result = build_replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote separate Phase 5 replay for {result['summary']['case_count']} saved observations to {args.output}")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
