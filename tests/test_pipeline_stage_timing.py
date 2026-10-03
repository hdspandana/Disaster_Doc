"""Regression tests for separately timed decode/preprocess and OCR stages."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import pipeline
from src.damage import DamageMap
from src.ocr import OcrResult


def test_pipeline_reports_image_and_ocr_stage_durations_separately(monkeypatch):
    image = np.zeros((630, 1000, 3), dtype=np.uint8)
    damage_map = DamageMap(np.zeros((630, 1000), dtype=np.uint8), paper_luma=255.0)

    def fake_extract(image_bytes, on_preprocessed=None):
        assert image_bytes == b"synthetic"
        if on_preprocessed is not None:
            on_preprocessed(image.shape[:2], ["test preprocessing"])
        return OcrResult(processing_seconds=2.25, preprocess_steps=["test preprocessing"]), image, image

    monkeypatch.setattr(pipeline.ocr, "extract_evidence", fake_extract)
    monkeypatch.setattr(pipeline.damage, "build_damage_map", lambda _: damage_map)
    monkeypatch.setattr(pipeline.field_mapping, "map_fields", lambda *_: {})
    monkeypatch.setattr(pipeline.classifier, "classify_all", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        pipeline.evidence,
        "summarize",
        lambda _: {"recovered": 0, "partial": 0, "unrecoverable": 0, "needs_verification": 0, "total": 0},
    )
    monkeypatch.setattr(pipeline.evidence, "render_annotation", lambda *args, **kwargs: image)
    monkeypatch.setattr(pipeline.evidence, "new_document", lambda **kwargs: object())

    emitted = []
    result = pipeline.run_pipeline(b"synthetic", on_stage=emitted.append)

    assert result.ok
    stages = {stage.key: stage for stage in result.stages}
    assert stages["image"].label == "Image decoded and preprocessed"
    assert stages["image"].seconds < 1.0
    assert stages["ocr"].seconds == pytest.approx(2.25, abs=0.05)
    assert [stage.key for stage in emitted][:3] == ["hash", "image", "ocr"]
