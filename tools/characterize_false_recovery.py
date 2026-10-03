"""Run the controlled classifier-level false-recovery characterization.

Usage (from the repository root):
    python tools/characterize_false_recovery.py
    python tools/characterize_false_recovery.py --output /tmp/characterization.json

The runner injects only observed text, controlled confidence, and a synthetic damage
map into the evaluation-only classifier harness. Ground truth is consulted only after
classification to label the outcome. No OCR engine, image pipeline, production state,
or production evidence is used or modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from src import config

MANIFEST_PATH = ROOT / "evaluation" / "false_recovery_cases.json"
OUTPUT_PATH = ROOT / "evaluation" / "false_recovery_results.json"

VALIDATOR_CODES = {
    "minimum_alnum_chars",
    "minimum_tokens",
    "field_pattern",
    "truncation_marker",
    "invalid_prefix",
    "invalid_length",
    "invalid_character",
    "dob_calendar",
    "validation_failed",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_state() -> dict[str, Any]:
    """Capture the source revision while making dirty-worktree status explicit."""
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


def _reason_code(reason: str) -> str:
    """Map current human-readable classifier diagnostics for evaluation summaries."""
    if reason.startswith("Observed text is shorter than the template expects"):
        return "minimum_alnum_chars"
    if reason.startswith("Observed text has fewer parts than the template expects"):
        return "minimum_tokens"
    if reason.startswith("Observed text does not satisfy the expected pattern"):
        return "field_pattern"
    if reason.startswith("Observed text ends with the truncation marker"):
        return "truncation_marker"
    if reason.startswith("ID prefix must use"):
        return "invalid_prefix"
    if reason.startswith(("ID text must be exactly", "ID serial component must contain exactly")):
        return "invalid_length"
    if reason.startswith(("ID serial component must contain ASCII digits", "ID separator does not match")):
        return "invalid_character"
    if reason.startswith("Observed DOB is not a valid calendar date"):
        return "dob_calendar"
    if reason.startswith("One or more deterministic field validation checks failed"):
        return "validation_failed"
    if reason.startswith("Damage begins immediately after the observed text"):
        return "adjacent_damage"
    if "of this field's value region is obscured" in reason:
        return "field_region_damage"
    if reason.startswith("OCR confidence for the surviving text is low"):
        return "low_confidence"
    if reason.startswith("Usable OCR evidence satisfies this field's expected pattern"):
        return "quality_gates_passed"
    if reason.startswith("The complete value cannot be established from this document"):
        return "abstention_summary"
    if reason.startswith("Only observations below the usable-evidence threshold"):
        return "usable_evidence_threshold"
    if reason.startswith("No OCR observation overlaps this field's value region"):
        return "no_observation"
    if reason.startswith("Unable to obtain sufficient OCR evidence"):
        return "ocr_unavailable"
    if reason.startswith("Overlapping OCR boxes contain different usable readings"):
        return "conflicting_ocr_observations"
    return "unclassified_reason"


def _evaluation_outcome(ground_truth: str, observed_text: str, status: str, claimed_value: str | None) -> str:
    """Compare the classifier's completed result with evaluation-only truth."""
    if ground_truth == observed_text and status == config.STATUS_RECOVERED and claimed_value == ground_truth:
        return "CORRECT_RECOVERY"
    if ground_truth != observed_text and status == config.STATUS_RECOVERED and claimed_value == observed_text:
        return "FALSE_RECOVERY"
    if ground_truth != observed_text and status != config.STATUS_RECOVERED and claimed_value is None:
        return "CORRECT_ABSTENTION"
    if ground_truth == observed_text and status != config.STATUS_RECOVERED and claimed_value is None:
        return "UNNECESSARY_ABSTENTION"
    return "OTHER_AMBIGUOUS"


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Classify first; consult ground truth only after the production FieldResult exists."""
    field_name = case["field_name"]
    observed_text = case["observed_text"]
    spec = config.TEMPLATE["fields"][field_name]

    # Deliberately do not pass case, ground_truth, or any truth-derived value to the harness.
    result = classify_controlled(
        field_name=field_name,
        observed_text=observed_text,
        confidence=float(case["ocr_confidence"]),
        damage_profile=case["damage_profile"],
    )

    reason_codes = [_reason_code(reason) for reason in result.reasons]
    validator_findings = [code for code in reason_codes if code in VALIDATOR_CODES]
    pattern_valid = re.fullmatch(spec["pattern"], observed_text.strip().upper()) is not None
    alnum_count = sum(char.isalnum() for char in observed_text)
    token_count = len([token for token in re.split(r"\s+", observed_text.strip()) if token])
    ground_truth = case["ground_truth"]
    outcome = _evaluation_outcome(ground_truth, observed_text, result.status, result.value)

    return {
        "case_id": case["case_id"],
        "field_name": field_name,
        "error_type": case["error_type"],
        "confusion_pair": case.get("confusion_pair"),
        "sweep": case.get("sweep"),
        "ground_truth": ground_truth,
        "observed_text": observed_text,
        "ocr_confidence_input": round(float(case["ocr_confidence"]), 4),
        "confidence_bucket": result.confidence_bucket,
        "damage_profile": case["damage_profile"],
        "outcome": outcome,
        "is_false_recovery": outcome == "FALSE_RECOVERY",
        "status": result.status,
        "claimed_value": result.value,
        "validation": {
            "pattern": spec["pattern"],
            "pattern_valid": pattern_valid,
            "alnum_characters": alnum_count,
            "minimum_alnum_characters": spec["min_chars"],
            "tokens": token_count,
            "minimum_tokens": spec["min_tokens"],
            "validator_findings": validator_findings,
            "validator_result": "REJECTED" if validator_findings else "PASSED",
            "note": "Pattern/length/token/truncation checks are format gates, not evidence of truth.",
        },
        "damage_evidence": {
            "obscuration_in_zone": round(float(result.obscuration_in_zone), 6),
            "adjacent_obscuration": round(float(result.adjacent_obscuration), 6),
            "field_region_bbox": result.field_region_bbox,
            "evidence_bbox": result.evidence_bbox,
            "damage_probe_bbox": result.damage_bbox,
        },
        "needs_verification": result.needs_verification,
        "reasons": result.reasons,
        "reason_codes": reason_codes,
        "evidence": result.evidence,
        "null_value_invariant_holds": (
            (result.status == config.STATUS_RECOVERED and result.value is not None)
            or (
                result.status in (config.STATUS_PARTIAL, config.STATUS_UNRECOVERABLE)
                and result.value is None
            )
        ),
    }


def characterize(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic, machine-readable result document."""
    if manifest is None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    cases = manifest["cases"]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id values must be unique")

    results = [_run_case(case) for case in cases]
    by_outcome = dict(sorted(Counter(row["outcome"] for row in results).items()))
    by_status = dict(sorted(Counter(row["status"] for row in results).items()))
    by_validation = dict(sorted(Counter(row["validation"]["validator_result"] for row in results).items()))
    reason_counts: Counter[str] = Counter()
    damage_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in results:
        reason_counts.update(row["reason_codes"])
        damage_outcomes[row["damage_profile"]][row["outcome"]] += 1

    repo = _repository_state()
    summary = {
        "case_count": len(results),
        "outcomes": by_outcome,
        "statuses": by_status,
        "validator_results": by_validation,
        "reason_code_occurrences": dict(sorted(reason_counts.items())),
        "false_recovery_case_ids": [row["case_id"] for row in results if row["is_false_recovery"]],
        "outcomes_by_damage_profile": {
            profile: dict(sorted(counts.items())) for profile, counts in sorted(damage_outcomes.items())
        },
    }
    return {
        "schema_version": 1,
        "title": manifest["title"],
        "method": "controlled_classifier_inputs",
        "scope": (
            "Evaluation-only direct calls to classify_field through a synthetic harness; "
            "no OCR engine or full image pipeline was run."
        ),
        "ground_truth_policy": manifest["ground_truth_policy"],
        "confidence_note": manifest["confidence_note"],
        "damage_note": manifest["damage_note"],
        "versions": {
            "code_version": config.CODE_VERSION,
            "classifier_version": config.CODE_VERSION,
            "pipeline_version": config.CODE_VERSION,
            "template": config.TEMPLATE_NAME,
            "repository_revision": repo["revision"],
            "repository_worktree_dirty": repo["worktree_dirty"],
            "source_sha256": {
                "src/classifier.py": _sha256(ROOT / "src" / "classifier.py"),
                "src/config.py": _sha256(ROOT / "src" / "config.py"),
            },
        },
        "summary": summary,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="where to write the JSON results")
    args = parser.parse_args()

    output = characterize()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(output['cases'])} controlled cases to {args.output}")
    print(json.dumps(output["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
