"""Security-boundary regression tests for uploads and rendered evidence."""

from __future__ import annotations

import sys
import tempfile
from html import escape as html_escape
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, ocr, report
from src.classifier import FieldResult
from src.evidence import EvidenceDocument
from src.pipeline import StageStatus, run_pipeline
from src.preprocessing import InvalidImageError, load_image


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (60, 50)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color="white").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "WEBP", "BMP", "TIFF"])
def test_supported_container_signatures_are_accepted(image_format):
    image = load_image(_image_bytes(image_format))

    assert image.shape == (50, 60, 3)


def test_oversized_upload_is_rejected_before_format_parsing(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(InvalidImageError, match="too large"):
        load_image(b"x" * 9)


def test_unknown_signature_is_rejected_before_opencv_decode(monkeypatch):
    def forbidden_decode(*_args, **_kwargs):
        pytest.fail("unsupported bytes reached OpenCV decode")

    monkeypatch.setattr("src.preprocessing.cv2.imdecode", forbidden_decode)
    with pytest.raises(InvalidImageError, match="could not be read as an image"):
        load_image(b"not an image container")


def test_signature_container_mismatch_is_rejected_before_opencv_decode(monkeypatch):
    spoofed = b"\x89PNG\r\n\x1a\n" + _image_bytes("JPEG")

    def forbidden_decode(*_args, **_kwargs):
        pytest.fail("signature/container mismatch reached OpenCV decode")

    monkeypatch.setattr("src.preprocessing.cv2.imdecode", forbidden_decode)
    with pytest.raises(InvalidImageError):
        load_image(spoofed)


def test_pixel_budget_is_checked_before_opencv_decode(monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 2_499)
    monkeypatch.setattr(config, "MAX_IMAGE_DIMENSION", 100)

    def forbidden_decode(*_args, **_kwargs):
        pytest.fail("over-budget image reached OpenCV decode")

    monkeypatch.setattr("src.preprocessing.cv2.imdecode", forbidden_decode)
    with pytest.raises(InvalidImageError, match="pixel limit"):
        load_image(_image_bytes("PNG", size=(50, 50)))


def test_dimension_budget_is_checked_before_opencv_decode(monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 1_000_000)
    monkeypatch.setattr(config, "MAX_IMAGE_DIMENSION", 100)

    def forbidden_decode(*_args, **_kwargs):
        pytest.fail("over-dimension image reached OpenCV decode")

    monkeypatch.setattr("src.preprocessing.cv2.imdecode", forbidden_decode)
    with pytest.raises(InvalidImageError, match="dimensions exceed"):
        load_image(_image_bytes("PNG", size=(101, 50)))


def test_pillow_decompression_bomb_warning_is_rejected(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_500)
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 3_000)
    monkeypatch.setattr(config, "MAX_IMAGE_DIMENSION", 100)

    with pytest.raises(InvalidImageError, match="valid supported image container"):
        load_image(_image_bytes("PNG", size=(50, 50)))


def test_invalid_upload_never_initializes_or_calls_ocr(monkeypatch):
    def forbidden_reader():
        pytest.fail("invalid upload reached the OCR reader")

    monkeypatch.setattr(ocr, "_get_reader", forbidden_reader)
    result = run_pipeline(b"not an image", use_ai=False)

    assert not result.ok
    assert result.document.fields == []


def test_streamlit_html_helpers_escape_ocr_and_stage_text():
    import app as disasterdoc_app

    payload = '<img src=x onerror="alert(1)"> &'
    field = FieldResult(
        field_name="district",
        label=payload,
        expected_type="string",
        status=config.STATUS_PARTIAL,
        value=None,
        raw_ocr_text=payload,
        confidence_bucket="low",
        ocr_confidence=0.4,
    )
    rendered_field = disasterdoc_app.field_card(field, selected=False)
    rendered_stage = disasterdoc_app.stage_html(
        StageStatus(key="ocr", label=payload, ok=False, detail=payload)
    )

    assert payload not in rendered_field
    assert payload not in rendered_stage
    assert html_escape(payload, quote=True) in rendered_field
    assert html_escape(payload, quote=True) in rendered_stage


def _malicious_report_document(payload: str) -> EvidenceDocument:
    field = FieldResult(
        field_name="district",
        label=payload,
        expected_type="string",
        status=config.STATUS_PARTIAL,
        value=None,
        raw_ocr_text=payload,
        confidence_bucket="low",
        ocr_confidence=0.94,
        evidence=[
            {
                "observation_id": payload,
                "bbox": [1, 2, 30, 12],
                "ocr_confidence": 0.94,
                "text": payload,
            }
        ],
        reasons=[payload],
        needs_verification=True,
        verification_instruction=payload,
    )
    field.ai_commentary = {
        "possible_interpretations": [payload],
        "verification_instruction": payload,
        "commentary": payload,
        "advisory_only": True,
    }
    return EvidenceDocument(
        document_id="sha256:" + "a" * 64,
        processed_at="2026-10-02T00:00:00+00:00",
        template="synthetic_id_v1",
        fields=[field],
        ocr_observations_raw=[
            {
                "observation_id": payload,
                "bbox": [1, 2, 30, 12],
                "ocr_confidence": 0.94,
                "text": payload,
            }
        ],
        audit={"ocr_engine": payload, "ai_guardrail_notes": [payload]},
        summary={"total": 1, "recovered": 0, "partial": 1, "unrecoverable": 0, "needs_verification": 1},
        verification_tasks=[{"task_id": "t1", "text": payload}],
        disclaimer=payload,
    )


def test_pdf_escapes_untrusted_text_and_does_not_leave_temp_images(tmp_path, monkeypatch):
    if not report.PDF_AVAILABLE:
        pytest.skip("reportlab not installed")

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = set(Path(tmp_path).iterdir())
    payload = '<script>alert("x")</script> & <b>not markup</b>'
    image = np.full((80, 120, 3), 255, dtype=np.uint8)

    pdf = report.to_pdf_bytes(_malicious_report_document(payload), image, image)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1_000
    assert set(Path(tmp_path).iterdir()) == before
