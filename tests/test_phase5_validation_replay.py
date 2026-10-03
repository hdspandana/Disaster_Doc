"""Integrity and result checks for the separate Phase 5 replay artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import replay_phase5_validation
from tools.replay_phase5_validation import OUTPUT_PATH, build_replay

EXPECTED_PHASE5_REPLAY_SHA256 = "72ae74031e4a6124a0aa45010ee49cc9f1dcf345a353aba282d10ab04b9bd823"
EXPECTED_INPUT_HASHES = {
    "evaluation/false_recovery_cases.json": "e1f1d2196342ab4427205d23f7a26afd08acd29a0401370960bfb5261dd30681",
    "evaluation/false_recovery_results.json": "6fdd0caf13b768da21bd77cdb02eaff89842edaff8b7d59b0314d152d7d0a4ea",
    "evaluation/image_level_cases.json": "990f3a7cce65220ccf402098fb2f34bdc4741926fbdb5d136fa6f79138fd7f77",
    "evaluation/image_level_results.json": "211a0b0666927c879f26652807f408ffa73efac43ae7f6b4c2e26413b536bc2f",
    "evaluation/image_level_assets/tree_sha256": "34d66fa9cf81165fc7a6be4ac4389e832969f919667735d66d2998999161764a",
}


def test_phase2_phase3_and_phase5_artifacts_remain_frozen():
    """The historical Phase 5 record stays byte-identical while current code evolves."""
    import hashlib

    historical = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    digest = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    assert digest == EXPECTED_PHASE5_REPLAY_SHA256
    assert historical["input_artifact_sha256"] == EXPECTED_INPUT_HASHES
    assert historical["phase3_asset_file_count"] == 80
    assert historical["versions"]["config_version"] == "disasterdoc-config-v2"

    # Recompute the historical validator results in memory without writing the Phase 5
    # artifact. Ignore version/source metadata, which correctly belongs to Phase 5.
    current = build_replay()
    for key in (
        "schema_version",
        "title",
        "method",
        "scope",
        "ground_truth_policy",
        "classification_policy",
        "input_artifact_sha256",
        "phase3_asset_file_count",
        "summary",
        "phase2_cases",
        "phase3_cases",
    ):
        assert current[key] == historical[key], key


def test_phase2_replay_rejects_impossible_dob_only_and_keeps_format_valid_errors():
    replay = build_replay()
    summary = replay["summary"]["phase2"]
    cases = {case["case_id"]: case for case in replay["phase2_cases"]}

    assert summary["validation_by_case_before"] == {
        "PASS": 24,
        "FAIL": 9,
        "UNKNOWN": 0,
        "NOT_APPLICABLE": 0,
    }
    assert summary["validation_by_case_after"] == {
        "PASS": 23,
        "FAIL": 10,
        "UNKNOWN": 0,
        "NOT_APPLICABLE": 0,
    }
    assert summary["outcomes_before"] == {
        "CORRECT_ABSTENTION": 12,
        "CORRECT_RECOVERY": 5,
        "FALSE_RECOVERY": 16,
        "INCORRECT_REJECTION": 0,
    }
    assert summary["outcomes_after"] == {
        "CORRECT_ABSTENTION": 13,
        "CORRECT_RECOVERY": 5,
        "FALSE_RECOVERY": 15,
        "INCORRECT_REJECTION": 0,
    }
    assert summary["newly_rejected_case_ids"] == ["dob_impossible_calendar_date"]

    impossible = cases["dob_impossible_calendar_date"]
    assert impossible["validation_after"] == "FAIL"
    assert impossible["status_after"] == "PARTIAL"
    assert impossible["claimed_value_after"] is None
    assert impossible["outcome_after"] == "CORRECT_ABSTENTION"

    for case_id in (
        "id_single_digit_substitution_conf_094_clean",
        "id_transposition",
        "id_valid_format_wrong_value",
        "dob_valid_format_wrong_value",
        "name_plausible_wrong_value",
        "district_plausible_wrong_value",
        "address_plausible_wrong_value",
    ):
        assert cases[case_id]["validation_after"] == "PASS"
        assert cases[case_id]["outcome_after"] == "FALSE_RECOVERY"


def test_phase3_replay_uses_saved_ocr_and_only_changes_impossible_dob_case():
    replay = build_replay()
    summary = replay["summary"]["phase3"]
    cases = {case["case_id"]: case for case in replay["phase3_cases"]}

    assert summary["validation_by_case_before"] == {
        "PASS": 24,
        "FAIL": 7,
        "UNKNOWN": 9,
        "NOT_APPLICABLE": 0,
    }
    assert summary["validation_by_case_after"] == {
        "PASS": 23,
        "FAIL": 8,
        "UNKNOWN": 9,
        "NOT_APPLICABLE": 0,
    }
    assert summary["outcomes_before"] == {
        "CORRECT_ABSTENTION": 18,
        "CORRECT_RECOVERY": 10,
        "FALSE_RECOVERY": 11,
        "INCORRECT_REJECTION": 1,
    }
    assert summary["outcomes_after"] == {
        "CORRECT_ABSTENTION": 19,
        "CORRECT_RECOVERY": 10,
        "FALSE_RECOVERY": 10,
        "INCORRECT_REJECTION": 1,
    }
    assert summary["newly_rejected_case_ids"] == ["source_05_mild"]
    assert summary["incorrect_rejections_after"] == ["source_08_mild"]

    rejected = cases["source_05_mild"]
    assert rejected["observed_value"] == "31 02 1994"
    assert rejected["validation_after"] == "FAIL"
    assert rejected["status_after"] == "PARTIAL"
    assert rejected["claimed_value_after"] is None
    assert rejected["outcome_after"] == "CORRECT_ABSTENTION"

    for case_id in ("source_01_mild", "source_02_mild", "source_06_mild", "source_07_mild"):
        assert cases[case_id]["validation_after"] == "PASS"
        assert cases[case_id]["outcome_after"] == "FALSE_RECOVERY"


def test_replay_counts_all_four_validator_states_without_claiming_calibration():
    replay = build_replay()
    assert replay["summary"]["validation_by_case_before"] == {
        "PASS": 48,
        "FAIL": 16,
        "UNKNOWN": 9,
        "NOT_APPLICABLE": 0,
    }
    assert replay["summary"]["validation_by_case_after"] == {
        "PASS": 46,
        "FAIL": 18,
        "UNKNOWN": 9,
        "NOT_APPLICABLE": 0,
    }
    assert replay["summary"]["validator_evidence_result_rows_after"] == {
        "PASS": 397,
        "FAIL": 57,
        "UNKNOWN": 71,
        "NOT_APPLICABLE": 285,
    }
    assert replay["versions"]["evidence_schema_version"] == 2


def test_replay_defers_evaluation_truth_until_after_validation_returns(monkeypatch):
    calls = []

    def validate(_field_name, _observed_value, _spec):
        calls.append("validator")
        return SimpleNamespace(result="PASS", evidence=(), reason_codes=(), messages=())

    monkeypatch.setattr(replay_phase5_validation, "validate_field", validate)
    row = replay_phase5_validation._case_after_validation(
        case_id="boundary-test",
        field_name="name",
        observed_value="ANANYA RAO",
        old_status="RECOVERED",
        old_claim="ANANYA RAO",
        old_validation="PASS",
        ground_truth_supplier=lambda: calls.append("ground_truth") or "ANANYA RAO",
        old_outcome="CORRECT_RECOVERY",
        provenance="unit_test",
    )

    assert calls == ["validator", "ground_truth"]
    assert row["outcome_after"] == "CORRECT_RECOVERY"
