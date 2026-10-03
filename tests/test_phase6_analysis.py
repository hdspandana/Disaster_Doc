"""Phase 6 offline analysis integrity and metric-definition checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.analyze_phase6 import OUTPUT_PATH, _render, build_analysis


def test_phase6_analysis_covers_exactly_the_25_frozen_residuals():
    analysis = build_analysis()
    rows = analysis["residual_cases"]
    phase2 = [row for row in rows if row["phase"] == "phase_2_controlled_classifier"]
    phase3 = [row for row in rows if row["phase"] == "phase_3_synthetic_image_pipeline"]

    assert len(rows) == 25
    assert len(phase2) == 15
    assert len(phase3) == 10
    assert all(row["classification"]["status_after_phase_5"] == "RECOVERED" for row in rows)
    assert all(row["validation"]["aggregate_after_phase_5"] == "PASS" for row in rows)
    assert all(row["evidence_justified_desired_status"] == "UNRECOVERABLE" for row in phase2)
    assert all(row["harness_status_after_phase_5"] == "RECOVERED" for row in phase2)
    assert all(row["evidence_justified_desired_status"] == "RECOVERED" for row in phase3)
    assert all(row["claim_equals_observation"] for row in rows)
    assert all(row["reference_values"]["observation_equals_source"] is False for row in rows)
    assert all(row["reference_values"]["observation_equals_rendered_target"] is True for row in phase3)
    assert all(row["reference_values"]["observation_equals_rendered_target"] is None for row in phase2)
    assert all("not OCR output" in row["evidence_origin"] for row in phase2)
    assert all("EasyOCR" in row["evidence_origin"] for row in phase3)


def test_phase6_reports_distinct_source_truth_and_visible_text_metrics():
    metrics = build_analysis()["metrics"]
    phase2 = metrics["phase_2_original_source_value"]
    phase3 = metrics["phase_3_original_source_value"]
    visible = metrics["phase_3_rendered_visible_text"]

    assert phase2["matrix"] == {"TP": 5, "FP": 15, "FN": 0, "TN": 13}
    assert phase2["precision"] == 0.25
    assert phase2["recall"] == 1.0
    assert phase2["exact_correct_recovery_rate_over_all_cases"] == 0.151515
    assert phase3["matrix"] == {"TP": 10, "FP": 10, "FN": 1, "TN": 19}
    assert phase3["precision"] == 0.5
    assert phase3["recall"] == 0.909091
    assert phase3["exact_correct_recovery_rate_over_all_cases"] == 0.25
    assert visible["matrix"] == {"TP": 20, "FP": 0, "FN": 3, "TN": 17}
    assert visible["precision"] == 1.0
    assert visible["recall"] == 0.869565
    assert visible["raw_saved_field_observation_exactly_matches_rendered_target_count"] == 23


def test_phase6_artifact_is_deterministic_and_phase2_phase3_phase5_inputs_are_frozen():
    assert OUTPUT_PATH.exists()
    artifact = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    assert artifact == build_analysis()
    assert artifact["phase_5_baseline"]["artifact_sha256"] == (
        "72ae74031e4a6124a0aa45010ee49cc9f1dcf345a353aba282d10ab04b9bd823"
    )
    assert artifact["label_circularity_audit"]["production_validation_or_classification_received_evaluation_labels"] is False
    assert _render(artifact).endswith(b"\n")
