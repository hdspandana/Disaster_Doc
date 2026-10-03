"""Regression tests for the Phase 3 image-level evaluation harness."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.controlled_classifier import classify_controlled
from tools import run_image_level_evaluation as image_eval

MANIFEST_PATH = ROOT / "evaluation" / "image_level_cases.json"
RESULTS_PATH = ROOT / "evaluation" / "image_level_results.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_image_case_manifest_has_ten_sources_and_four_variants_each():
    manifest = _manifest()
    sources = {source["source_id"]: source for source in manifest["sources"]}
    cases = manifest["cases"]

    assert len(sources) == 10
    assert len(cases) == 40
    assert len({case["case_id"] for case in cases}) == 40
    assert len({case["seed"] for case in cases}) == 40
    assert {severity: sum(case["severity"] == severity for case in cases) for severity in ("clean", "mild", "medium", "severe")} == {
        "clean": 10,
        "mild": 10,
        "medium": 10,
        "severe": 10,
    }
    for case in cases:
        source = sources[case["source_id"]]
        assert case["field_name"] == source["focus_field"]
        assert case["ground_truth"] == source["values"][case["field_name"]]
        assert case["image_path"].startswith("evaluation/image_level_assets/")
        assert case["damage_mask_path"].startswith("evaluation/image_level_assets/")


def test_seeded_image_generation_is_repeatable_and_restores_demo_globals():
    manifest = _manifest()
    sources = {source["source_id"]: source for source in manifest["sources"]}
    case = next(case for case in manifest["cases"] if case["case_id"] == "source_09_medium")
    original_values = dict(image_eval.make_demo_docs.VALUES)
    original_tuning = dict(image_eval.make_demo_docs.TUNING)

    first_bytes, first_mask = image_eval._render_and_encode(case, sources[case["source_id"]])
    second_bytes, second_mask = image_eval._render_and_encode(case, sources[case["source_id"]])

    assert first_bytes == second_bytes
    assert np.array_equal(first_mask, second_mask)
    assert image_eval.make_demo_docs.VALUES == original_values
    assert image_eval.make_demo_docs.TUNING == original_tuning


def test_runner_sends_image_bytes_only_and_keeps_truth_outside_field_result(monkeypatch, tmp_path):
    manifest = _manifest()
    source = next(source for source in manifest["sources"] if source["source_id"] == "source_01")
    case = next(case for case in manifest["cases"] if case["case_id"] == "source_01_clean")
    subset = copy.deepcopy(manifest)
    subset["sources"] = [source]
    subset["cases"] = [case]
    received: dict[str, object] = {}

    def fake_pipeline(image_bytes: bytes, use_ai: bool = False):
        received["image_bytes_type"] = type(image_bytes)
        received["use_ai"] = use_ai
        field = classify_controlled("id_number", "DX-48291", 0.94, "clean")
        document = SimpleNamespace(
            fields=[field],
            field=lambda field_name: field if field_name == "id_number" else None,
            processing={},
            audit={},
        )
        return SimpleNamespace(
            ok=True,
            error=None,
            document=document,
            stages=[],
            observations=[],
        )

    monkeypatch.setattr(image_eval, "run_pipeline", fake_pipeline)
    result = image_eval.run_evaluation(subset, asset_root=tmp_path, progress=False)
    row = result["cases"][0]

    assert received == {"image_bytes_type": bytes, "use_ai": False}
    assert row["evaluation"]["ground_truth"] == "DX-48291"
    assert "ground_truth" not in row["field_result"]
    assert row["field_result"]["observed_value"] == "DX-48291"
    assert row["field_result"]["claimed_value"] == "DX-48291"
    assert row["evaluation"]["outcome"] == "CORRECT_RECOVERY"


def test_outcome_labels_separate_recovery_and_abstention():
    assert image_eval._outcome(
        status="RECOVERED", claimed_value="AB-12345", observed_value="AB-12345", ground_truth="AB-12345"
    ) == "CORRECT_RECOVERY"
    assert image_eval._outcome(
        status="RECOVERED", claimed_value="AB-12345", observed_value="AB-12345", ground_truth="AB-12346"
    ) == "FALSE_RECOVERY"
    assert image_eval._outcome(
        status="PARTIAL", claimed_value=None, observed_value="AB-1234", ground_truth="AB-12345"
    ) == "CORRECT_ABSTENTION"
    assert image_eval._outcome(
        status="PARTIAL", claimed_value=None, observed_value="AB-12345", ground_truth="AB-12345"
    ) == "INCORRECT_REJECTION"


def test_saved_results_cover_the_manifest_and_hashes_separate_evaluation_truth():
    manifest = _manifest()
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    case_ids = {case["case_id"] for case in manifest["cases"]}

    assert results["summary"]["source_count"] == 10
    assert results["summary"]["case_count"] == 40
    assert {case["case_id"] for case in results["cases"]} == case_ids
    for case in results["cases"]:
        field_result = case["field_result"]
        assert "ground_truth" not in field_result
        assert case["evaluation"]["ground_truth"]
        if field_result["status"] in ("PARTIAL", "UNRECOVERABLE"):
            assert field_result["claimed_value"] is None
        for key, hash_key in (("path", "sha256"), ("damage_mask_path", "damage_mask_sha256")):
            path = ROOT / case["image"][key]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == case["image"][hash_key]
