"""Executed, deterministic characterization of controlled OCR-like errors.

Ground truth is loaded only by the evaluation runner after classify_field returns;
these tests do not route it through production code or evidence objects.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from src import config
from tools.characterize_false_recovery import OUTPUT_PATH, characterize


@pytest.fixture(scope="module")
def characterization() -> dict:
    return characterize()


def _case_map(characterization: dict) -> dict[str, dict]:
    return {case["case_id"]: case for case in characterization["cases"]}


def test_phase2_snapshot_is_frozen_while_live_characterization_is_repeatable(characterization):
    """Keep Phase 2 immutable; Phase 5 behavior is recorded in a separate replay artifact."""
    phase2_manifest = ROOT / "evaluation" / "false_recovery_cases.json"
    phase2_snapshot = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    assert hashlib.sha256(phase2_manifest.read_bytes()).hexdigest() == (
        "e1f1d2196342ab4427205d23f7a26afd08acd29a0401370960bfb5261dd30681"
    )
    assert hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest() == (
        "6fdd0caf13b768da21bd77cdb02eaff89842edaff8b7d59b0314d152d7d0a4ea"
    )
    assert phase2_snapshot["summary"]["outcomes"] == {
        "CORRECT_ABSTENTION": 12,
        "CORRECT_RECOVERY": 5,
        "FALSE_RECOVERY": 16,
    }
    assert characterize() == characterization
    assert phase2_snapshot != characterization
    assert characterization["summary"]["case_count"] == 33
    assert len(_case_map(characterization)) == 33


def test_correct_baselines_cover_all_template_fields(characterization):
    cases = _case_map(characterization)
    expected_fields = set(config.FIELD_ORDER)
    baselines = [case for case in cases.values() if case["case_id"].startswith("baseline_")]

    assert {case["field_name"] for case in baselines} == expected_fields
    assert all(case["outcome"] == "CORRECT_RECOVERY" for case in baselines)
    assert all(case["claimed_value"] == case["ground_truth"] for case in baselines)


def test_high_confidence_valid_digit_substitution_is_a_recorded_false_recovery(characterization):
    case = _case_map(characterization)["id_single_digit_substitution_conf_094_clean"]

    assert case["ground_truth"] == "DX-48291"
    assert case["observed_text"] == "DX-48281"
    assert case["validation"]["pattern_valid"] is True
    assert case["outcome"] == "FALSE_RECOVERY"
    assert case["status"] == config.STATUS_RECOVERED
    assert case["claimed_value"] == case["observed_text"]
    assert case["confidence_bucket"] == "high"
    assert case["reasons"]
    assert case["evidence"][0]["text"] == case["observed_text"]


def test_confidence_sweep_shows_the_current_gate_not_a_truth_probability(characterization):
    sweep = [case for case in characterization["cases"] if case.get("sweep") in ("confidence", "confidence_and_damage")]
    by_confidence = {case["ocr_confidence_input"]: case for case in sweep}

    assert sorted(by_confidence) == [0.4, 0.6, 0.8, 0.94, 0.99]
    assert by_confidence[0.4]["status"] == config.STATUS_PARTIAL
    assert by_confidence[0.4]["claimed_value"] is None
    assert "low_confidence" in by_confidence[0.4]["reason_codes"]
    for confidence in (0.6, 0.8, 0.94, 0.99):
        assert by_confidence[confidence]["status"] == config.STATUS_RECOVERED
        assert by_confidence[confidence]["outcome"] == "FALSE_RECOVERY"
    assert by_confidence[0.6]["confidence_bucket"] == "medium"
    assert by_confidence[0.8]["confidence_bucket"] == "high"


def test_damage_profiles_trigger_the_current_zone_and_adjacent_gates(characterization):
    cases = _case_map(characterization)
    moderate = cases["id_single_digit_substitution_damage_moderate"]
    heavy = cases["id_single_digit_substitution_damage_heavy"]
    adjacent = cases["id_single_digit_substitution_damage_adjacent"]

    assert moderate["damage_evidence"]["obscuration_in_zone"] == pytest.approx(0.20)
    assert moderate["status"] == config.STATUS_RECOVERED
    assert moderate["outcome"] == "FALSE_RECOVERY"

    assert heavy["damage_evidence"]["obscuration_in_zone"] == pytest.approx(0.50)
    assert heavy["status"] == config.STATUS_PARTIAL
    assert heavy["claimed_value"] is None
    assert "field_region_damage" in heavy["reason_codes"]

    assert adjacent["damage_evidence"]["obscuration_in_zone"] < 0.35
    assert adjacent["damage_evidence"]["adjacent_obscuration"] == pytest.approx(1.0)
    assert adjacent["status"] == config.STATUS_PARTIAL
    assert adjacent["claimed_value"] is None
    assert "adjacent_damage" in adjacent["reason_codes"]


def test_format_gates_catch_structural_errors_but_not_valid_format_errors(characterization):
    cases = _case_map(characterization)
    for case_id in (
        "id_ocr_confusion_8_to_B",
        "id_ocr_confusion_1_to_I",
        "id_deletion",
        "id_insertion",
        "id_truncation_unmarked",
        "id_truncation_marker",
        "dob_ocr_confusion_1_to_I",
        "name_ocr_confusion_O_to_0",
        "district_ocr_confusion_B_to_8",
    ):
        case = cases[case_id]
        assert case["validation"]["pattern_valid"] is False
        assert case["status"] == config.STATUS_PARTIAL
        assert case["claimed_value"] is None
        assert case["outcome"] == "CORRECT_ABSTENTION"

    assert "truncation_marker" in cases["id_truncation_marker"]["validation"]["validator_findings"]

    for case_id in (
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
    ):
        case = cases[case_id]
        assert case["validation"]["pattern_valid"] is True
        assert case["status"] == config.STATUS_RECOVERED
        assert case["outcome"] == "FALSE_RECOVERY"


def test_impossible_calendar_date_is_rejected_without_changing_the_shape_regex(characterization):
    case = _case_map(characterization)["dob_impossible_calendar_date"]

    assert case["observed_text"] == "31 02 1991"
    assert case["validation"]["pattern_valid"] is True
    assert case["validation"]["validator_result"] == "REJECTED"
    assert "dob_calendar" in case["validation"]["validator_findings"]
    assert case["status"] == config.STATUS_PARTIAL
    assert case["claimed_value"] is None
    assert case["outcome"] == "CORRECT_ABSTENTION"


def test_partial_and_unrecoverable_results_never_carry_values(characterization):
    for case in characterization["cases"]:
        if case["status"] in (config.STATUS_PARTIAL, config.STATUS_UNRECOVERABLE):
            assert case["claimed_value"] is None
        assert case["null_value_invariant_holds"] is True
        assert "unclassified_reason" not in case["reason_codes"]


def test_ground_truth_is_not_an_input_to_the_classifier_or_production_evidence():
    """The harness API cannot accept truth, and its result contains only OCR evidence."""
    result = classify_controlled("id_number", "DX-48281", 0.94, "clean")
    serialized = result.to_dict()

    assert "ground_truth" not in inspect.signature(classify_controlled).parameters
    assert "ground_truth" not in serialized
    assert serialized["raw_ocr_text"] == "DX-48281"
    assert serialized["value"] == "DX-48281"
    assert serialized["evidence"][0]["text"] == "DX-48281"
    assert "DX-48291" not in json.dumps(serialized)


def test_live_phase5_classifier_counts_are_separate_from_phase2_snapshot(characterization):
    assert characterization["summary"]["outcomes"] == {
        "CORRECT_ABSTENTION": 13,
        "CORRECT_RECOVERY": 5,
        "FALSE_RECOVERY": 15,
    }
    assert characterization["summary"]["statuses"] == {
        "PARTIAL": 13,
        "RECOVERED": 20,
    }
    assert characterization["summary"]["validator_results"] == {
        "PASSED": 23,
        "REJECTED": 10,
    }
